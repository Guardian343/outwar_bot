import re
import aiohttp
import asyncio

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from outwar import logger
from outwar.servers import host_for, login_url_for, DEFAULT_SERVER


# Back-compat aliases (server 1 / Sigil). New code should prefer host_for(server_id).
BASE_URL = host_for(DEFAULT_SERVER)
LOGIN_URL = login_url_for(DEFAULT_SERVER)

# Default cap on simultaneous connections to sigil. Prevents bursting too many
# requests at once (the rate-limit trigger). Tunable live via settings.json
# "host_connection_limit". All account traffic shares one session, so this is a
# GLOBAL throttle across every code path (slayer nav, boss joins, monitors).
DEFAULT_HOST_LIMIT = 10


class RequestStatus(str, Enum):
    SUCCESS = "success"
    RATE_LIMITED = "rate_limited"
    AD_FRAME = "ad_frame"
    LOGGED_OUT = "logged_out"
    TIMEOUT = "timeout"
    CLIENT_ERROR = "client_error"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class RequestResult:
    status: RequestStatus
    html: str = ""
    error: Optional[str] = None
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status == RequestStatus.SUCCESS


def _build_session(quiet: bool = False) -> aiohttp.ClientSession:
    """aiohttp session with a per-host connection cap (Freak's rate-limit fix)."""
    limit = DEFAULT_HOST_LIMIT

    try:
        from outwar import database as db
        limit = int(db.get_settings().get("host_connection_limit", DEFAULT_HOST_LIMIT))
    except Exception:
        pass

    limit = max(1, limit)

    connector = aiohttp.TCPConnector(
        limit=limit,            # total simultaneous connections
        limit_per_host=limit,   # per-host cap — the throttle that actually matters
        ttl_dns_cache=300,      # cache DNS so we're not re-resolving every request
        enable_cleanup_closed=True,
    )

    if not quiet:
        logger.info("SESSION", f"HTTP connector: limit_per_host={limit}")

    return aiohttp.ClientSession(connector=connector)


class LoginError(Exception):
    """Raised when Outwar login fails."""
    pass


class OutwarSession:
    """Shared HTTP session for the main bot account."""

    def __init__(self):
        self._session: aiohttp.ClientSession = None
        self.session_id: str = None
        self.user_id: int = None
        self._username: str = None
        self._password: str = None
        self._relogin_lock = asyncio.Lock()
        self.on_relogin = None
        self._last_login = None
        # --- Re-login throttle + circuit breaker (prevents the thrash cascade
        #     where a brief network blip triggers dozens of re-logins that then
        #     kick each other's sessions and sustain the flood) ---
        self._last_relogin_attempt = None   # datetime of the most recent re-login attempt
        self._relogin_cooldown_secs = 60     # min seconds between re-login attempts
        self._relogin_times = []             # timestamps of recent successful-path re-logins
        self._relogin_window_secs = 600      # 10-min window for the circuit breaker
        self._relogin_max_in_window = 5      # >this many in the window → trip the breaker
        self._relogin_breaker_until = None   # datetime; while set + future, re-login is paused
        self._relogin_breaker_backoff_secs = 300  # how long to pause when the breaker trips
        # Direct logged-out signal: set True the moment a genuine logged-out page is
        # seen, cleared on a successful (re)login. is_healthy() reads this so it can
        # report unhealthy on the FIRST sign of trouble — WITHOUT waiting for the
        # circuit breaker to trip (the throttle prevents repeated re-logins, so the
        # breaker often never trips; relying on it alone left is_healthy blind).
        self._known_logged_out = False

    async def login(self, username: str, password: str):
        self._username = username
        self._password = password

        if self._session:
            await self._session.close()

        self._session = _build_session()
        await self._do_login()

    async def _do_login(self):
        data = {
            "login_username": self._username,
            "login_password": self._password,
        }

        try:
            async with self._session.post(LOGIN_URL, data=data) as resp:
                content = await resp.text()
                final_url = str(resp.url)
        except Exception as e:
            raise LoginError(f"Network error during login: {e}")

        if "Invalid username" in content or "login_username" in content:
            raise LoginError(
                "Outwar login failed — check OUTWAR_USERNAME and OUTWAR_PASSWORD in .env"
            )

        # Extract session ID from redirect URL or cookie
        try:
            self.session_id = self._extract(content, "rg_sess_id=", 32)
        except (ValueError, IndexError):
            # Try from cookie
            cookies = self._session.cookie_jar.filter_cookies("https://sigil.outwar.com")
            sess_cookie = cookies.get("rg_sess_id")

            if sess_cookie:
                self.session_id = sess_cookie.value[:32]
            else:
                raise LoginError("Could not extract session ID — login may have failed.")

        # Extract user_id from redirect URL
        try:
            m = re.search(r"suid=(\d+)", final_url)

            if m:
                self.user_id = int(m.group(1))
            else:
                user_id_str = self._extract_until(content, "owchar=", "&")
                self.user_id = int(user_id_str)
        except (ValueError, TypeError):
            self.user_id = 0

        logger.info("SESSION", f"Got user_id from redirect: {self.user_id}")
        logger.info("SESSION", "Got session ID from cookie.")

        # FORCE the account to Sigil (server 1). TWO steps are required (Liam):
        # (1) switch the server via ac_serverid=1, then (2) SELECT an account on that
        # server — switching alone drifts back to wherever an account was last
        # selected. Step 2 = fetch the bot's Sigil account link (suid 1157932). The
        # account's server state persists on Outwar's side, so a fresh login can land
        # on Torax (933209); without this the bot acts as its Torax identity on the
        # Sigil host and everything breaks.
        if self.user_id != 1157932:  # 1157932 = the bot's Sigil suid
            try:
                logger.info("SESSION", f"Login landed on suid {self.user_id} — forcing Sigil…")
                # Step 1: switch server to Sigil
                async with self._session.get(
                    f"{BASE_URL}/myaccount.php?ac_serverid=1", allow_redirects=True
                ) as sresp:
                    await sresp.text()
                # Step 2: SELECT the bot's Sigil account (this is what makes it stick)
                sel_url = (f"{BASE_URL}/world.php?rg_sess_id={self.session_id}"
                           f"&suid=1157932&serverid=1")
                async with self._session.get(sel_url, allow_redirects=True) as selresp:
                    selcontent = await selresp.text()
                    selurl = str(selresp.url)
                # Confirm by re-reading the suid
                new_suid = None
                m2 = re.search(r"suid=(\d+)", selurl)
                if m2:
                    new_suid = int(m2.group(1))
                else:
                    m3 = re.search(r"owchar=(\d+)", selcontent)
                    if m3:
                        new_suid = int(m3.group(1))
                if new_suid == 1157932:
                    logger.info("SESSION", "Forced Sigil + selected account 1157932 ✓")
                    self.user_id = 1157932
                elif new_suid:
                    logger.warning("SESSION", f"After Sigil switch, suid is {new_suid} (expected 1157932)")
                    self.user_id = new_suid
                else:
                    logger.warning("SESSION", "Could not confirm Sigil suid — forcing 1157932")
                    self.user_id = 1157932
            except Exception as e:
                logger.warning("SESSION", f"Forced Sigil switch failed: {e} — assuming 1157932")
                self.user_id = 1157932

        logger.info("SESSION", f"Final bot suid: {self.user_id}")
        self._known_logged_out = False  # fresh login → session healthy

        from datetime import datetime, timezone
        self._last_login = datetime.now(timezone.utc)

    def _is_logged_out(self, html: str) -> bool:
        """Detect if the response is a login redirect / session expired."""
        return self._is_logged_out_page(html)
    
    def _is_ajax_or_partial_success(self, html: str, path: str = "") -> bool:
        """
        Detect valid AJAX / partial responses.

        These responses often do not contain full page markers like toolbar_rage
        or charselectdropdown, but they can still be valid successful responses.
        """
        if not html:
            return False

        lower = html.lower()
        path_lower = path.lower()

        # Most AJAX endpoints are valid partial responses and should not require
        # full logged-in page markers. skills_info.php is a skill-detail fragment
        # (cooldown/active text) that likewise lacks full-page chrome — treat it as
        # a valid partial too, otherwise every MD/Last-Stand cooldown check flags
        # "unknown" and burns 5 retries.
        # item_rollover.php is the same shape: a small item tooltip fragment with
        # no page chrome. A key scan fires one per key, so without this every
        # `!bp scan keys` logs a warning for every key it owns.
        if ("ajax" in path_lower
                or "skills_info" in path_lower
                or "item_rollover" in path_lower):
            return True

        # JSON response.
        stripped = html.strip()
        if (
            (stripped.startswith("{") and stripped.endswith("}"))
            or (stripped.startswith("[") and stripped.endswith("]"))
        ):
            return True

        # Common partial response markers used by Outwar endpoints.
        partial_markers = (
            "item_id",
            "item_name",
            "backpack",
            "potion",
            "success",
            "already drank",
            "already consumed",
            "invalid item",
        )

        return any(marker in lower for marker in partial_markers)

    def _is_logged_in_page(self, html: str) -> bool:
        """
        Detect a valid logged-in Outwar page.

        Normal logged-in Outwar pages also contain public marketing meta tags
        and layout/ad-related CSS such as #outerdiv and #inneriframe. Those are
        NOT reliable signals for logout or ad-frame detection.

        This detector uses positive authenticated-page markers instead.
        """
        if not html:
            return False

        lower = html.lower()

        logged_in_markers = (
            "toolbar_rage",
            "charselectdropdown",
            "god cap",
            "sidebar-wrapper",
            "main content starts here",
            "crew_home",
            "crew_bossspawns",
            "primegods",
            "logout",
        )

        # Require more than one marker so one random string does not classify
        # a page as authenticated by accident.
        score = sum(marker in lower for marker in logged_in_markers)
        return score >= 2

    def _is_logged_out_page(self, html: str) -> bool:
        """
        Detect the public/login/unauthenticated Outwar page.

        This should only return True when the page looks unauthenticated AND
        does not contain normal logged-in game markers.
        """
        if not html:
            return False

        lower = html.lower()

        if self._is_logged_in_page(html):
            return False

        logged_out_markers = (
            "login_username",
            "login_password",
            "please login",
            "browser based mmorpg",
            "free online mmorpg",
            "no download required",
            "outwar is a free online",
        )

        return any(marker in lower for marker in logged_out_markers)

    async def _relogin_if_needed(self, html: str) -> bool:
        """Re-login if the session has genuinely expired. Returns True if a
        re-login actually happened.

        THROTTLED + CIRCUIT-BROKEN so a transient network blip can't cause a
        re-login cascade:
          - At most one re-login attempt per `_relogin_cooldown_secs` (default 60s).
            If many requests see a logged-out page at once, only the first
            re-logs in; the rest are told "no" and simply retry/back off.
          - If re-logins pile up (> `_relogin_max_in_window` within
            `_relogin_window_secs`), the breaker trips and pauses ALL re-login
            attempts for `_relogin_breaker_backoff_secs`, logging loudly + alerting
            once, rather than hammering Outwar (which is what caused the hour-long
            flood from a few seconds of network trouble).
        """
        if not self._is_logged_out(html):
            return False

        # A genuine logged-out page was seen — mark the session unhealthy IMMEDIATELY
        # so is_healthy() reports it on the first sign, even if the throttle/breaker
        # decide not to actually re-login this instant.
        self._known_logged_out = True

        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)

        async with self._relogin_lock:
            # 1) Circuit breaker: if tripped and still within the back-off, refuse.
            if self._relogin_breaker_until and now < self._relogin_breaker_until:
                return False

            # 2) Throttle: refuse if we attempted a re-login very recently.
            if (self._last_relogin_attempt is not None
                    and (now - self._last_relogin_attempt).total_seconds()
                        < self._relogin_cooldown_secs):
                return False

            # 3) Circuit-breaker accounting: prune old timestamps, then check count.
            cutoff = now - timedelta(seconds=self._relogin_window_secs)
            self._relogin_times = [t for t in self._relogin_times if t > cutoff]
            if len(self._relogin_times) >= self._relogin_max_in_window:
                self._relogin_breaker_until = now + timedelta(
                    seconds=self._relogin_breaker_backoff_secs)
                logger.error(
                    "SESSION",
                    f"Re-login circuit breaker TRIPPED: "
                    f"{len(self._relogin_times)} re-logins in "
                    f"{self._relogin_window_secs // 60} min. Pausing re-login for "
                    f"{self._relogin_breaker_backoff_secs // 60} min — this usually "
                    f"means a network problem, NOT a real logout. Keeping the "
                    f"existing session and backing off."
                )
                if self.on_relogin:
                    try:
                        await self.on_relogin(
                            success=False,
                            error=(f"circuit breaker tripped — too many re-logins; "
                                   f"paused {self._relogin_breaker_backoff_secs // 60} min "
                                   f"(likely a network issue)"))
                    except Exception:
                        pass
                return False

            # Passed all guards — record the attempt and try the re-login.
            self._last_relogin_attempt = now
            try:
                logger.warning("SESSION", "Session expired — re-logging in...")
                await self._do_login()
                logger.info("SESSION", "Re-login successful.")
                self._known_logged_out = False  # recovered — session healthy again
                self._relogin_times.append(datetime.now(timezone.utc))
                if self.on_relogin:
                    await self.on_relogin(success=True)
                return True
            except Exception as e:
                logger.error("SESSION", f"Re-login failed: {e}")
                # Count failed attempts toward the breaker too — repeated failures
                # (e.g. site unreachable) should trip it and stop the hammering.
                self._relogin_times.append(datetime.now(timezone.utc))
                if self.on_relogin:
                    await self.on_relogin(success=False, error=str(e))
                return False

    def is_healthy(self) -> bool:
        """Cheap, synchronous check of whether the session looks usable RIGHT NOW.
        Fan-out background loops (Primewatcher pot cast, boss-raid pot recast) call
        this before iterating over many accounts, so a network blip / logged-out
        session makes them SKIP the cycle instead of firing a doomed request per
        account (which is what turned a brief blip into a flood).

        Unhealthy when ANY of:
          - not logged in (no session / no user_id), OR
          - a genuine logged-out page was seen and we haven't re-logged in since
            (`_known_logged_out`) — this is the DIRECT signal, so we bail on the
            FIRST sign of trouble without waiting for the circuit breaker, OR
          - the re-login circuit breaker is currently tripped (sustained trouble).
        """
        if not self._session or not self.user_id:
            return False
        if self._known_logged_out:
            return False
        from datetime import datetime, timezone
        if self._relogin_breaker_until and datetime.now(timezone.utc) < self._relogin_breaker_until:
            return False
        return True

    # ── Internal retry helper ────────────────────────────────────────────────
    async def request_result(
        self,
        method: str,
        path: str,
        *,
        data: dict = None,
        cookies: dict = None,
        is_action: bool = False,
        max_attempts: int = None,
        timeout_secs: float = 60.0,
        server_id: int = DEFAULT_SERVER,
    ) -> RequestResult:
        """
        Send a request to Outwar and return a classified result.

        ``server_id`` selects which game server's host to hit (1=Sigil default,
        2=Torax). Defaults to Sigil so existing single-server callers are
        unchanged; dual-server callers pass server_id explicitly.

        Read-only requests may retry because they only fetch data.

        Action requests should NOT be blindly retried. A timeout, rate limit,
        or ad-frame does not always mean the action failed server-side. The
        caller should verify game state instead.
        """
        method = method.upper()
        url = f"{host_for(server_id)}/{path.lstrip('/')}"

        if max_attempts is None:
            max_attempts = 1 if is_action else 5

        timeout = aiohttp.ClientTimeout(total=timeout_secs)
        last_error = None

        for attempt in range(max_attempts):
            try:
                kwargs = {"timeout": timeout}

                if cookies:
                    kwargs["cookies"] = cookies

                if method == "POST":
                    kwargs["data"] = data or {}
                    cm = self._session.post(url, **kwargs)
                else:
                    cm = self._session.get(url, **kwargs)

                async with cm as resp:
                    html = await resp.text()

                html_lower = html.lower()

                if any(
                    marker in html_lower
                    for marker in ("too many requests", "rate limit", "slow down")
                ):
                    logger.warning(
                        "SESSION",
                        f"Rate limited: {method} {url} "
                        f"attempt {attempt + 1}/{max_attempts}"
                    )

                    return RequestResult(
                        status=RequestStatus.RATE_LIMITED,
                        html=html,
                        attempts=attempt + 1,
                    )
                
                # If the response looks unauthenticated, re-login for read-only
                # requests and retry. For action requests, do not automatically
                # repeat the action after re-login because it may have landed.
                if self._is_logged_out(html):
                    logger.warning(
                        "SESSION",
                        f"Logged-out response: {method} {url} "
                        f"attempt {attempt + 1}/{max_attempts}"
                    )

                    relogged = await self._relogin_if_needed(html)

                    if relogged and not is_action:
                        continue

                    return RequestResult(
                        status=RequestStatus.LOGGED_OUT,
                        html=html,
                        attempts=attempt + 1,
                    )

                # Got an authenticated (not logged-out) response — the session is
                # working, so clear any stale logged-out flag. This lets is_healthy()
                # recover on its own if a logged-out reading was transient.
                if self._known_logged_out:
                    self._known_logged_out = False

                if self._is_ajax_or_partial_success(html, path):
                    return RequestResult(
                        status=RequestStatus.SUCCESS,
                        html=html,
                        attempts=attempt + 1,
                    )
                
                # Classify authenticated game pages first. This avoids false
                # positives from normal Outwar pages that contain marketing
                # meta tags or layout/ad CSS in the header.
                if self._is_logged_in_page(html):
                    return RequestResult(
                        status=RequestStatus.SUCCESS,
                        html=html,
                        attempts=attempt + 1,
                    )

                # Unknown page shape. Keep the HTML for legacy callers and
                # debugging, but classify it separately from logged-out.
                logger.warning(
                    "SESSION",
                    f"Unknown response shape: {method} {url} "
                    f"attempt {attempt + 1}/{max_attempts}"
                )

                return RequestResult(
                    status=RequestStatus.UNKNOWN,
                    html=html,
                    error="unknown_response_shape",
                    attempts=attempt + 1,
                )

            except asyncio.TimeoutError:
                last_error = "timeout"

                if is_action:
                    logger.warning("SESSION", f"Action timeout, not retried: {method} {url}")

                    return RequestResult(
                        status=RequestStatus.TIMEOUT,
                        error=last_error,
                        attempts=attempt + 1,
                    )

            except aiohttp.ClientError as e:
                last_error = str(e)

                if is_action:
                    logger.warning("SESSION", f"Action client error, not retried: {method} {url}: {e}")

                    return RequestResult(
                        status=RequestStatus.CLIENT_ERROR,
                        error=last_error,
                        attempts=attempt + 1,
                    )

            except Exception as e:
                last_error = str(e)

                if is_action:
                    logger.error("SESSION", f"Action error, not retried: {method} {url}: {e}")

                    return RequestResult(
                        status=RequestStatus.ERROR,
                        error=last_error,
                        attempts=attempt + 1,
                    )

            if not is_action and attempt < max_attempts - 1:
                wait = min(30.0, 2.0 ** attempt)

                logger.warning(
                    "SESSION",
                    f"Request failed: {method} {url} "
                    f"attempt {attempt + 1}/{max_attempts}: {last_error}. "
                    f"Retrying in {wait:.0f}s..."
                )

                await asyncio.sleep(wait)

        logger.error(
            "SESSION",
            f"All {max_attempts} attempts failed for "
            f"{method} {url}: {last_error}"
        )

        return RequestResult(
            status=RequestStatus.ERROR,
            error=last_error,
            attempts=max_attempts,
        )

    async def get(self, path: str, *, server_id: int = DEFAULT_SERVER) -> str:
        """Read-only GET request. Safe to retry. server_id selects the game
        server (1=Sigil default, 2=Torax)."""
        result = await self.request_result(
            "GET",
            path,
            is_action=False,
            server_id=server_id,
        )

        return result.html

    def _suid_for_server(self, server_id: int) -> int:
        """The bot account's suid ON A GIVEN SERVER. The same Outwar account has a
        DIFFERENT suid per server (e.g. LoDRaid = 1157932 on Sigil, 933209 on Torax).
        Using the Sigil suid on Torax makes Torax serve the not-logged-in fallback
        page — which is why Torax reads failed until now.

        server 1 → self.user_id (auto-detected at login).
        server N → a configured override from settings `bot_suid_by_server` {"2":933209}.
        Falls back to self.user_id if no override (so nothing breaks; it just won't
        authenticate on that server until the suid is set)."""
        if int(server_id) == DEFAULT_SERVER:
            return self.user_id
        try:
            from outwar import database as db
            mapping = db.get_settings().get("bot_suid_by_server", {}) or {}
            val = mapping.get(str(int(server_id)))
            if val:
                return int(val)
        except Exception:
            pass
        return self.user_id

    async def get_server(self, path: str, server_id: int = DEFAULT_SERVER) -> str:
        """Read-only GET for a specific server using the bot's OWN rg_sess_id + the
        bot's suid FOR THAT SERVER as per-request URL params (cookieless).

        Concurrency-safe multi-server fetch: server is a URL param, so Sigil and
        Torax fetches are independent and run concurrently (proven by the live
        Outwar client running 1284 accounts across both servers at once). The
        per-server suid is essential — the same account has a different suid on each
        server, and using the wrong one yields the not-logged-in fallback page.

        Falls back to the cookie get() if the bot's ssid isn't available yet.
        """
        ssid = getattr(self, "session_id", None)
        # Server 1 (Sigil) uses the PROVEN cookie session — that's the bot's logged-in
        # home server and the cookie path is rock-solid. Only NON-default servers need
        # the cookieless per-request path (different suid, can't use the Sigil cookie).
        if int(server_id) == DEFAULT_SERVER:
            return await self.get(path, server_id=server_id)
        suid = self._suid_for_server(server_id)
        if not ssid or not suid:
            # No ssid yet (pre-login) — fall back to the cookie path.
            return await self.get(path, server_id=server_id)
        from outwar import ssid_store
        return await ssid_store.sess_get(path, ssid, suid, server_id)

    async def get_as(self, path: str, suid: int, *, server_id: int = DEFAULT_SERVER) -> str:
        """Read-only GET as a specific trustee. Safe to retry."""
        result = await self.request_result(
            "GET",
            path,
            cookies={"ow_userid": str(suid)},
            is_action=False,
            server_id=server_id,
        )

        return result.html

    async def post(self, path: str, data: dict, *, is_action: bool = True,
                   server_id: int = DEFAULT_SERVER) -> str:
        """
        POST request.

        Defaults to is_action=True because most Outwar POST endpoints mutate
        game state. If a POST endpoint is truly read-only, pass is_action=False.
        """
        result = await self.request_result(
            "POST",
            path,
            data=data,
            is_action=is_action,
            server_id=server_id,
        )

        return result.html

    async def post_as(
        self,
        path: str,
        data: dict,
        suid: int,
        *,
        is_action: bool = True,
        server_id: int = DEFAULT_SERVER,
    ) -> str:
        """
        POST as a specific trustee.

        Defaults to is_action=True because most Outwar POST endpoints mutate
        game state: casting, potions, joining raids, attacking, etc.
        """
        result = await self.request_result(
            "POST",
            path,
            data=data,
            cookies={"ow_userid": str(suid)},
            is_action=is_action,
            server_id=server_id,
        )

        return result.html

    async def get_sse(self, path: str, timeout_secs: int = 3600,
                      *, server_id: int = DEFAULT_SERVER) -> str:
        """
        Fetch an SSE endpoint with an extended timeout and graceful handling
        of TransferEncodingError — the loot data is usually complete by the
        time the error fires. server_id selects the game server.

        For non-default servers we append the bot's own rg_sess_id + suid +
        serverid as per-request params (cookieless), so the stream authenticates
        correctly on Torax too (the cookie session is logged into Sigil only).
        """
        url = f"{host_for(server_id)}/{path.lstrip('/')}"
        if server_id != DEFAULT_SERVER and self.session_id:
            suid = self._suid_for_server(server_id)
            sep = "&" if "?" in url else "?"
            url = (f"{url}{sep}rg_sess_id={self.session_id}"
                   f"&suid={suid}&serverid={int(server_id)}")
        timeout = aiohttp.ClientTimeout(total=timeout_secs)

        try:
            async with self._session.get(url, timeout=timeout) as resp:
                try:
                    data = await resp.text()
                except aiohttp.TransferEncodingError:
                    data = (
                        resp.content._buffer.decode("utf-8", errors="replace")
                        if hasattr(resp.content, "_buffer")
                        else ""
                    )
                except Exception:
                    data = ""
        except aiohttp.TransferEncodingError:
            raise

        return data

    async def close(self):
        if self._session:
            await self._session.close()

    @staticmethod
    def _extract(content: str, search: str, length: int) -> str:
        idx = content.index(search) + len(search)
        return content[idx: idx + length]

    @staticmethod
    def _extract_until(content: str, search: str, end: str) -> str:
        idx = content.index(search) + len(search)
        end_idx = content.index(end, idx)
        return content[idx:end_idx]


class AccountSession:
    """Per-trustee HTTP session."""

    def __init__(self, name: str, suid: int, level: int, crew: str, rage: int, url: str):
        self.name = name
        self.suid = suid
        self.level = level
        self.crew = crew
        self.rage = rage
        self.url = url
        self.has_md = False
        self.is_active = False
        self.in_cooldown = False
        self._session: aiohttp.ClientSession = None
        self._logged_in = False

    async def login(self, username: str, password: str):
        self._session = _build_session(quiet=True)

        data = {
            "login_username": username,
            "login_password": password,
        }

        async with self._session.post(LOGIN_URL, data=data) as resp:
            content = await resp.text()

        self._logged_in = True
        return content

    async def get(self, path: str) -> str:
        url = f"{BASE_URL}/{path.lstrip('/')}"

        async with self._session.get(url) as resp:
            return await resp.text()

    async def post(self, path: str, data: dict) -> str:
        url = f"{BASE_URL}/{path.lstrip('/')}"

        async with self._session.post(url, data=data) as resp:
            return await resp.text()

    async def close(self):
        if self._session:
            await self._session.close()