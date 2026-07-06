import re
import aiohttp
import asyncio

from dataclasses import dataclass
from enum import Enum
from typing import Optional


BASE_URL = "https://sigil.outwar.com"
LOGIN_URL = "http://sigil.outwar.com/index.php"

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
        print(f"[SESSION] HTTP connector: limit_per_host={limit}")

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

        print(f"Got user_id from redirect: {self.user_id}")
        print("Got session ID from cookie.")

        from datetime import datetime, timezone
        self._last_login = datetime.now(timezone.utc)

    def _is_logged_out(self, html: str) -> bool:
        """Detect if the response is a login redirect / session expired."""
        if not html:
            return False

        return (
            "login_username" in html
            or "login_password" in html
            or ("Please login" in html and "rg_sess_id" not in html)
        )

    def _is_ad_frame(self, html: str) -> bool:
        """
        Detect Outwar ad-frame responses.

        These are not normal success pages, but they also don't always mean the
        underlying action failed. Callers should verify game state instead of
        blindly retrying the action.
        """
        if not html:
            return False

        lower = html.lower()

        return (
            ("outerdiv" in lower and "inneriframe" in lower)
            or "160x600" in lower
        )

    async def _relogin_if_needed(self, html: str) -> bool:
        """Re-login if session has expired. Returns True if re-login happened."""
        if not self._is_logged_out(html):
            return False

        async with self._relogin_lock:
            try:
                print("Session expired — re-logging in...")
                await self._do_login()
                print("Re-login successful.")

                if self.on_relogin:
                    await self.on_relogin(success=True)

                return True

            except Exception as e:
                print(f"Re-login failed: {e}")

                if self.on_relogin:
                    await self.on_relogin(success=False, error=str(e))

                return False
            
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
    ) -> RequestResult:
        """
        Send a request to Outwar and return a classified result.

        Read-only requests may retry because they only fetch data.

        Action requests should NOT be blindly retried. A timeout, rate limit,
        or ad-frame does not always mean the action failed server-side. The
        caller should verify game state instead.
        """
        method = method.upper()
        url = f"{BASE_URL}/{path.lstrip('/')}"

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
                    print(
                        f"[SESSION] Rate limited: {method} {url} "
                        f"attempt {attempt + 1}/{max_attempts}"
                    )

                    return RequestResult(
                        status=RequestStatus.RATE_LIMITED,
                        html=html,
                        attempts=attempt + 1,
                    )

                if self._is_ad_frame(html):
                    print(
                        f"[SESSION] Ad-frame response: {method} {url} "
                        f"attempt {attempt + 1}/{max_attempts}"
                    )

                    return RequestResult(
                        status=RequestStatus.AD_FRAME,
                        html=html,
                        attempts=attempt + 1,
                    )

                if self._is_logged_out(html):
                    relogged = await self._relogin_if_needed(html)

                    if relogged and not is_action:
                        continue

                    return RequestResult(
                        status=RequestStatus.LOGGED_OUT,
                        html=html,
                        attempts=attempt + 1,
                    )

                return RequestResult(
                    status=RequestStatus.SUCCESS,
                    html=html,
                    attempts=attempt + 1,
                )

            except asyncio.TimeoutError:
                last_error = "timeout"

                if is_action:
                    print(f"[SESSION] Action timeout, not retried: {method} {url}")

                    return RequestResult(
                        status=RequestStatus.TIMEOUT,
                        error=last_error,
                        attempts=attempt + 1,
                    )

            except aiohttp.ClientError as e:
                last_error = str(e)

                if is_action:
                    print(f"[SESSION] Action client error, not retried: {method} {url}: {e}")

                    return RequestResult(
                        status=RequestStatus.CLIENT_ERROR,
                        error=last_error,
                        attempts=attempt + 1,
                    )

            except Exception as e:
                last_error = str(e)

                if is_action:
                    print(f"[SESSION] Action error, not retried: {method} {url}: {e}")

                    return RequestResult(
                        status=RequestStatus.ERROR,
                        error=last_error,
                        attempts=attempt + 1,
                    )

            if not is_action and attempt < max_attempts - 1:
                wait = min(30.0, 2.0 ** attempt)

                print(
                    f"[SESSION] Request failed: {method} {url} "
                    f"attempt {attempt + 1}/{max_attempts}: {last_error}. "
                    f"Retrying in {wait:.0f}s..."
                )

                await asyncio.sleep(wait)

        print(
            f"[SESSION] All {max_attempts} attempts failed for "
            f"{method} {url}: {last_error}"
        )

        return RequestResult(
            status=RequestStatus.ERROR,
            error=last_error,
            attempts=max_attempts,
        )

    async def get(self, path: str) -> str:
        """Read-only GET request. Safe to retry."""
        result = await self.request_result(
            "GET",
            path,
            is_action=False,
        )

        return result.html if result.ok else ""

    async def get_as(self, path: str, suid: int) -> str:
        """Read-only GET as a specific trustee. Safe to retry."""
        result = await self.request_result(
            "GET",
            path,
            cookies={"ow_userid": str(suid)},
            is_action=False,
        )

        return result.html if result.ok else ""

    async def post(self, path: str, data: dict, *, is_action: bool = True) -> str:
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
        )

        return result.html if result.ok else ""

    async def post_as(
        self,
        path: str,
        data: dict,
        suid: int,
        *,
        is_action: bool = True,
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
        )

        return result.html if result.ok else ""

    async def get_sse(self, path: str, timeout_secs: int = 3600) -> str:
        """
        Fetch an SSE endpoint with an extended timeout and graceful handling
        of TransferEncodingError — the loot data is usually complete by the
        time the error fires.
        """
        url = f"{BASE_URL}/{path.lstrip('/')}"
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