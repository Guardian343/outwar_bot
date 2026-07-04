"""
god_monitor.py

- Polls Prime Gods page every 30 minutes (at :00 and :30)
- Polls boss spawns page every 2 minutes
- Posts spawn/death alerts in clean list format
- On startup: posts current live gods and bosses
"""

import asyncio
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone
from outwar import database as db
from outwar.scraper import (
    parse_gods, parse_envoys, parse_bosses,
    parse_god_stats_page, parse_prime_god_page,
    unscramble_loot, God, Envoy,
)
from cogs import embed_style as es

BASE_URL   = "https://sigil.outwar.com"
from yarl import URL as _URL
_SIGIL_URL = _URL("https://sigil.outwar.com")

import re as _re


def _aggregate_loot_display(entry: dict):
    """
    Build the display item list for one crew's loot, combining all points drops
    into a single "N points" line. Amulet chests are shown by their real item name
    (e.g. "Amulet Chest (100) x2") so different chest sizes stay distinct.
    Returns (display_items, drop_count).

    drop_count is the TRUE number of individual drops (every rolled item counts as
    its quantity, and an accumulated-points award counts as one), independent of how
    many display lines are shown after combining.
    """
    item_counts = entry.get("item_counts") or {}
    points      = entry.get("points", 0) or 0
    others      = []
    for name, count in item_counts.items():
        low = name.lower().strip()
        pm  = _re.match(r"^(\d+)\s*points$", low)   # item literally named "N points"
        if pm:
            points += int(pm.group(1)) * count
        else:
            others.append(f"{name} x{count}" if count > 1 else name)

    display = list(others)
    if points > 0:
        display.append(f"{points} points")

    # Prefer the parser's authoritative count; fall back to recomputing if absent.
    drop_count = entry.get("drop_count")
    if drop_count is None:
        drop_count = sum(item_counts.values()) + (1 if (entry.get("points", 0) or 0) > 0 else 0)
    return display, drop_count


def _format_focus_drops(rec: dict) -> str:
    """
    Format a day's accumulated focused-crew drops into one line, e.g.
    "Artifact of X x2, Soul Gem, Amulet Chest (100) x5, Points x280".
    Amulet chests keep their real names/sizes; all points are combined into one total.
    """
    item_counts = rec.get("item_counts", {}) or {}
    points      = rec.get("points", 0) or 0
    parts       = []
    for name, count in item_counts.items():
        low = name.lower().strip()
        pm  = _re.match(r"^(\d+)\s*points?$", low)
        if pm:
            points += int(pm.group(1)) * count
        else:
            parts.append(f"{name} x{count}" if count > 1 else name)
    if points > 0:
        parts.append(f"Points x{points}")
    return ", ".join(parts) if parts else "No focused drops yesterday"


class GodMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_gods: dict[str, bool] = {}
        self._last_envoys: dict[str, bool] = {}
        self._last_bosses: dict[str, bool] = {}
        self._gods_cache: list[God] = []
        self._envoys_cache: list = []
        self._monitor_running = False
        self._last_god_poll = None  # tracks last half-hour window polled

    @property
    def session(self):
        return self.bot.outwar

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._monitor_running:
            self._monitor_running = True
            self._last_gods = db.get_god_state()
            self._last_envoys = db.get_envoy_state()
            self._last_bosses = db.get_boss_state()
            self._boss_window_state = {}
            self.god_poll_loop.start()
            self.boss_poll_loop.start()
            self.daily_summary_loop.start()
            self.session_check_loop.start()
            print("God and boss monitors started.")
            await self._post_startup_state()

    def cog_unload(self):
        self.god_poll_loop.cancel()
        self.boss_poll_loop.cancel()
        self.daily_summary_loop.cancel()
        self.session_check_loop.cancel()

    # ------------------------------------------------------------------
    # Poll loops
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def god_poll_loop(self):
        now = datetime.now(timezone.utc)
        minute = now.minute
        # Poll at :00/:30, or at :01/:31 as a catch-up if the previous minute was missed
        if minute not in (0, 1, 30, 31):
            return
        # Don't poll twice for the same half-hour window
        half = now.replace(second=0, microsecond=0, minute=0 if minute < 30 else 30)
        if self._last_god_poll == half:
            return
        self._last_god_poll = half
        await self._poll_gods()

    @god_poll_loop.before_loop
    async def before_god_poll(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def boss_poll_loop(self):
        await self._poll_bosses()

    @boss_poll_loop.before_loop
    async def before_boss_poll(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=6)
    async def session_check_loop(self):
        """Check all stored sessions every 6 hours and DM owner if any have expired."""
        await self._check_sessions()

    @session_check_loop.before_loop
    async def before_session_check(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(300)  # Wait 5 mins after startup before first check

    async def _check_sessions(self):
        try:
            import aiohttp as _aio
            from cogs.auth import OWNER_ID
            settings = db.get_settings()
            all_sessions = settings.get("user_sessions", {})
            if not all_sessions:
                return

            expired = {}  # discord_user_id -> [rga_names]

            for discord_id, user_sessions in all_sessions.items():
                if not isinstance(user_sessions, dict):
                    continue
                user_expired = []
                for rga_name, session_id in user_sessions.items():
                    try:
                        async with _aio.ClientSession() as s:
                            s.cookie_jar.update_cookies(
                                {"rg_sess_id": session_id},
                                response_url=_URL("https://sigil.outwar.com")
                            )
                            async with s.get(
                                "https://sigil.outwar.com/home",
                                timeout=_aio.ClientTimeout(total=10)
                            ) as resp:
                                html = await resp.text()
                        if "login" in html.lower() and "rg_sess_id" not in html:
                            user_expired.append(rga_name)
                    except Exception:
                        pass
                if user_expired:
                    expired[discord_id] = user_expired

            if not expired:
                return

            # DM the owner about all expired sessions
            owner = self.bot.get_user(OWNER_ID)
            if owner:
                lines = []
                for discord_id, rgas in expired.items():
                    user = self.bot.get_user(int(discord_id))
                    user_str = user.mention if user else f"User {discord_id}"
                    lines.append(f"**{user_str}**: {', '.join(f'`{r}`' for r in rgas)}")
                await owner.send(
                    f"⚠️ **Expired session IDs detected:**\n" + "\n".join(lines) +
                    f"\nAsk them to DM the bot with `!session-set` to refresh."
                )

            # Also DM the affected users directly
            for discord_id, rgas in expired.items():
                try:
                    user = self.bot.get_user(int(discord_id))
                    if user:
                        await user.send(
                            f"⚠️ Your session ID(s) for **{', '.join(rgas)}** have expired.\n"
                            f"Please DM me with `!session-set` to refresh them."
                        )
                except Exception:
                    pass

        except Exception as e:
            print(f"Session check error: {e}")

    @tasks.loop(minutes=1)
    async def daily_summary_loop(self):
        # Fire at 09:00 UK time (UTC+0 winter / UTC+1 summer)
        import pytz
        uk_tz  = pytz.timezone("Europe/London")
        now_uk = datetime.now(uk_tz)
        if now_uk.hour == 9 and now_uk.minute == 0:
            await self._post_daily_summary()

    @daily_summary_loop.before_loop
    async def before_daily_summary(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Startup state
    # ------------------------------------------------------------------

    async def _post_startup_state(self):
        """Post currently spawned gods and bosses to alert channels on startup."""
        await asyncio.sleep(3)

        # --- Gods ---
        try:
            god_channel = await self._get_alert_channel("gods")
            html = await self.session.get("primegods")
            gods = parse_gods(html)
            self._gods_cache = gods

            if god_channel:
                spawned = [g for g in gods if g.spawned]
                if spawned:
                    names = "\n".join(f"{es.ICON_STAR} **{g.name}**" if self._is_focus_crew(g.name)
                                      else f"• **{g.name}**" for g in spawned)
                    embed = es.spawn_embed(
                        f"{es.ICON_GOD} Prime Gods Currently Spawned",
                        description=f"{names}\n\n[View Prime Gods »](http://sigil.outwar.com/primegods)"
                    )
                    await god_channel.send(embed=embed)
        except Exception as e:
            print(f"Startup god state error: {e}")

        # --- Bosses ---
        try:
            boss_channel = await self._get_alert_channel("bosses")
            html = await self.session.get("crew_bossspawns")
            bosses = parse_bosses(html)

            if boss_channel:
                spawned = [b for b in bosses if b.spawned]
                if spawned:
                    names = "\n".join(f"• **{b.full_name}**" for b in spawned)
                    embed = es.spawn_embed(
                        f"{es.ICON_BOSS} Bosses Currently Spawned",
                        description=names[:4000]
                    )
                    await boss_channel.send(embed=embed)
        except Exception as e:
            print(f"Startup boss state error: {e}")

    # ------------------------------------------------------------------
    # Poll logic
    # ------------------------------------------------------------------

    async def _poll_gods(self):
        try:
            html = await self.session.get("primegods")
            gods = parse_gods(html)
            envoys = parse_envoys(html)
            self._gods_cache = gods
            self._envoys_cache = envoys
            await self._process_god_changes(gods)
            await self._process_envoy_changes(envoys)
        except Exception as e:
            print(f"God monitor poll error: {e}")

    async def _poll_bosses(self):
        try:
            html = await self.session.get("crew_bossspawns")
            bosses = parse_bosses(html)
            await self._process_boss_changes(bosses)
        except Exception as e:
            print(f"Boss monitor poll error: {e}")

    # ------------------------------------------------------------------
    # Change processing
    # ------------------------------------------------------------------

    async def _process_god_changes(self, gods: list[God]):
        channel = await self._get_alert_channel("gods")
        new_state = {}
        just_spawned = []
        just_died = []

        for god in gods:
            new_state[god.name] = god.spawned
            was_spawned = self._last_gods.get(god.name)
            if was_spawned is None:
                continue
            if not was_spawned and god.spawned:
                just_spawned.append(god)
            elif was_spawned and not god.spawned:
                just_died.append(god)

        if channel:
            # Post spawned gods as one embed
            if just_spawned:
                names = "\n".join(f"{es.ICON_STAR} **{g.name}**" if self._is_focus_crew(g.name)
                                  else f"• **{g.name}**" for g in just_spawned)
                embed = es.spawn_embed(
                    f"{es.ICON_SPAWN} Prime God{'s' if len(just_spawned) > 1 else ''} Spawned",
                    description=f"{names}\n\n[View Prime Gods »](http://sigil.outwar.com/primegods)"
                )
                await channel.send(embed=embed)

            # Post died gods as one embed with loot links
            if just_died:
                # The loot page needs the REAL spawnid, which is different from god_id.
                # It only exists after the god dies and must be read from the god's page.
                import re as _re_loot

                async def _resolve_loot_link(g):
                    try:
                        god_html = await self.session.get(f"primegods?mobid={g.god_id}")
                        data     = parse_prime_god_page(god_html)
                        loot_url = data.get("loot_url")
                        if loot_url:
                            sm = _re_loot.search(r"spawnid=(\d+)", loot_url)
                            if sm:
                                spawnid = sm.group(1)
                                return f"• [**{g.name}**](http://sigil.outwar.com/primegod_loot?spawnid={spawnid})"
                    except Exception as e:
                        print(f"[GODS] Could not resolve loot link for {g.name}: {e}")
                    # Fallback: name with no link rather than a broken link
                    return f"• **{g.name}**"

                lines = await asyncio.gather(*[_resolve_loot_link(g) for g in just_died])
                embed = es.death_embed(
                    f"{es.ICON_DEATH} Prime God{'s' if len(just_died) > 1 else ''} Defeated",
                    description="\n".join(lines) + "\n\n*Drop summary to follow…*"
                )
                await channel.send(embed=embed)

                # Fire drops as independent background tasks
                for god in just_died:
                    asyncio.create_task(self._post_god_drops(channel, god))

        self._last_gods = new_state
        db.save_god_state(new_state)

    async def _process_envoy_changes(self, envoys):
        channel = await self._get_alert_channel("envoys") or await self._get_alert_channel("gods")
        new_state = {}

        for envoy in envoys:
            new_state[envoy.name] = envoy.spawned
            was_spawned = self._last_envoys.get(envoy.name)
            if was_spawned is None:
                continue

            if not was_spawned and envoy.spawned:
                if channel:
                    embed = es.spawn_embed(
                        f"{es.ICON_ENVOY} Envoy Spawned",
                        description=f"**{envoy.name}**"
                    )
                    await channel.send(embed=embed)

            elif was_spawned and not envoy.spawned:
                if channel:
                    embed = es.death_embed(
                        f"{es.ICON_DEATH} Envoy Defeated",
                        description=f"**{envoy.name}**\n\n*Drop summary to follow…*"
                    )
                    await channel.send(embed=embed)
                # Fire drops fetch independently — don't let one slow envoy block the others
                asyncio.create_task(self._post_envoy_drops(envoy))

        self._last_envoys = new_state
        db.save_envoy_state(new_state)

    async def _process_boss_changes(self, bosses):
        channel = await self._get_alert_channel("bosses")
        new_state = {}
        just_spawned = []
        just_died    = []
        entered_window = []

        for boss in bosses:
            new_state[boss.full_name] = boss.spawned
            was_spawned = self._last_bosses.get(boss.full_name)

            # Check window entry (only for non-spawned bosses with timing data)
            if not boss.spawned and boss.spawn_days and (boss.last_killed or db.get_boss_death_dt(boss.full_name)):
                try:
                    import re as _re3
                    # Prefer our own precise UTC death record; fall back to parsing
                    # the page's (CST-assumed) kill string only if we've never seen
                    # this boss die.
                    killed_dt = db.get_boss_death_dt(boss.full_name)
                    if not killed_dt and boss.last_killed:
                        clean = _re3.sub(r'<[^>]+>', '', boss.last_killed).strip()
                        for fmt in ("%a, %d %b %Y %I:%M%p", "%a, %d %b %Y %I:%M %p",
                                    "%m-%d-%y %I:%M%p", "%Y-%m-%d %H:%M"):
                            try:
                                from datetime import timezone as _tz3, timedelta as _td3
                                _CST = _tz3(_td3(hours=-6))
                                killed_dt = datetime.strptime(clean, fmt).replace(tzinfo=_CST)
                                break
                            except ValueError:
                                continue
                    if killed_dt:
                        from datetime import timedelta as _td2
                        base   = _td2(days=boss.spawn_days)
                        min_dt = killed_dt + base * 0.75
                        now    = datetime.now(timezone.utc)
                        was_in_window  = self._boss_window_state.get(boss.full_name, False)
                        is_in_window   = now >= min_dt
                        if is_in_window and not was_in_window:
                            entered_window.append(boss)
                        self._boss_window_state[boss.full_name] = is_in_window
                except Exception:
                    pass

            if was_spawned is None:
                continue
            if not was_spawned and boss.spawned:
                just_spawned.append(boss)
            elif was_spawned and not boss.spawned:
                just_died.append(boss)
                # Record the precise UTC moment we observed the despawn. This is the
                # authoritative, timezone-unambiguous reference for the spawn window —
                # far more reliable than parsing the page's CST-assumed kill string.
                try:
                    db.record_boss_death(boss.full_name)
                except Exception as e:
                    print(f"[BOSS] could not record death time for {boss.full_name}: {e}")

        if channel:
            if entered_window:
                for boss in entered_window:
                    await channel.send(f"🪟 **{boss.full_name}** has entered its window.")

            if just_spawned:
                names = "\n".join(f"• **{b.full_name}**" for b in just_spawned)
                embed = es.spawn_embed(
                    f"{es.ICON_BOSS} Boss{'es' if len(just_spawned) > 1 else ''} Spawned",
                    description=names[:4000]
                )
                await channel.send(embed=embed)

            if just_died:
                names = "\n".join(f"• **{b.full_name}**" for b in just_died)
                embed = es.death_embed(
                    f"{es.ICON_DEATH} Boss{'es' if len(just_died) > 1 else ''} Defeated",
                    description=names[:4000]
                )
                await channel.send(embed=embed)

                # Post detailed drop summary per boss to drops channel
                drops_channel = await self._get_alert_channel("drops") or channel

                for boss in just_died:
                    print(f"[DROPS] boss={boss.full_name} stats_url={boss.stats_url}")
                    if boss.stats_url:
                        try:
                            await asyncio.sleep(15)  # let stats page settle after death
                            from bs4 import BeautifulSoup
                            stats_html = await self.session.get(boss.stats_url)
                            soup = BeautifulSoup(stats_html, "lxml")
                            rows = soup.select("#content-header-row div table tbody tr")
                            print(f"[DROPS] {boss.full_name}: html_len={len(stats_html)} rows_found={len(rows)}")
                            if not rows:
                                print(f"[DROPS] {boss.full_name} preview: {stats_html[:300]!r}")

                            drop_embed = es.drops_embed(
                                f"{es.ICON_DROPS} {boss.full_name} — Drop Summary"
                            )
                            crews_with_drops = []   # (crew, dmg, is_focus, loot)
                            no_drops         = []   # (crew, dmg, is_focus)

                            for row in rows:
                                name_cell = row.select_one("td:nth-of-type(1)")
                                dmg_cell  = row.select_one("td:nth-of-type(2)")
                                loot_cell = row.select_one("td:nth-of-type(3)")
                                if not name_cell:
                                    continue
                                raw = loot_cell.get("onmouseover", "") if loot_cell else ""
                                scrambled = (raw
                                    .replace("popup(event,'", "")
                                    .replace("<br>','808080')", "")
                                    .replace("','808080')", "")
                                    .replace("<br>", "|")
                                    .replace("\\", ""))
                                loot      = unscramble_loot(scrambled)
                                crew      = name_cell.get_text(strip=True).replace("_", "\\_")
                                dmg       = dmg_cell.get_text(strip=True) if dmg_cell else ""
                                is_focus  = self._is_focus_crew(crew)

                                if not loot or loot == "No Items":
                                    no_drops.append((crew, dmg, is_focus))
                                    continue
                                crews_with_drops.append((crew, dmg, is_focus, loot))

                            # Focus crews first
                            crews_with_drops.sort(key=lambda c: not c[2])
                            no_drops.sort(key=lambda c: not c[2])

                            total_drops = len(crews_with_drops)
                            drop_embed.description = (
                                f"**{total_drops}** crew(s) looted · "
                                f"**{len(no_drops)}** with no drops"
                            )

                            for crew, dmg, is_focus, loot in crews_with_drops:
                                star   = f"{es.ICON_STAR} " if is_focus else ""
                                header = f"{star}{crew} — {dmg} dmg" if dmg else f"{star}{crew}"
                                items  = [l for l in loot.split("\n") if l.strip()]
                                body   = es.bullet_list(items) if len(items) > 1 else loot
                                drop_embed.add_field(name=header[:256], value=body[:1024] or "—", inline=False)
                                if len(drop_embed.fields) == 25:
                                    await drops_channel.send(embed=drop_embed)
                                    drop_embed = es.drops_embed(
                                        f"{es.ICON_DROPS} {boss.full_name} — Drop Summary (continued)"
                                    )

                            if no_drops:
                                if len(drop_embed.fields) >= 24:
                                    await drops_channel.send(embed=drop_embed)
                                    drop_embed = es.drops_embed(
                                        f"{es.ICON_DROPS} {boss.full_name} — Drop Summary (continued)"
                                    )
                                nd_lines = []
                                for crew, dmg, is_focus in no_drops:
                                    star = f"{es.ICON_STAR} " if is_focus else ""
                                    nd_lines.append(f"{star}{crew} ({dmg} dmg)" if dmg else f"{star}{crew}")
                                drop_embed.add_field(
                                    name=f"{es.ICON_NODROP} No Drops ({len(no_drops)})",
                                    value=" · ".join(nd_lines)[:1024],
                                    inline=False
                                )

                            if drop_embed.fields:
                                await drops_channel.send(embed=drop_embed)
                            else:
                                await drops_channel.send(
                                    f"{es.ICON_DROPS} **{boss.full_name}** died but no drop data was found on the stats page."
                                )
                        except Exception as e:
                            print(f"Error fetching boss drops: {e}")

        self._last_bosses = new_state
        db.save_boss_state(new_state)

    # ------------------------------------------------------------------
    # Drop helpers
    # ------------------------------------------------------------------

    async def _post_daily_summary(self):
        """Post the daily 9am summary to the summary channel."""
        try:
            channel_id = db.get_alert_channel("summary")
            if not channel_id:
                return
            channel = self.bot.get_channel(channel_id)
            if not channel:
                return

            summary_crews = db.get_summary_crews()
            if not summary_crews:
                await channel.send("⚠️ No crews set for daily summary. Use `!summary-set <crew>`.")
                return

            import re as _re
            from datetime import timezone as _tz, timedelta as _td

            today_str = datetime.now().strftime("%d %b %Y")

            # --- Boss section (same for all crews) ---
            html   = await self.session.get("crew_bossspawns")
            bosses = parse_bosses(html)
            CST    = _tz(_td(hours=-6))

            async def _spawn_info(boss):
                days_ago = None
                if boss.last_killed:
                    clean = _re.sub(r'<[^>]+>', '', boss.last_killed).strip()
                    for fmt in ("%a, %d %b %Y %I:%M%p", "%a, %d %b %Y %I:%M %p",
                                "%m-%d-%y %I:%M%p", "%Y-%m-%d %H:%M"):
                        try:
                            dt = datetime.strptime(clean, fmt).replace(tzinfo=CST)
                            days_ago = (datetime.now(_tz.utc) - dt).days
                            break
                        except ValueError:
                            continue
                killed_str = f" · Killed {days_ago}d ago" if days_ago is not None else ""

                if boss.spawned:
                    hp_str = ""
                    if boss.stats_url:
                        try:
                            from outwar.scraper import parse_boss_damage
                            stats_html   = await self.session.get(boss.stats_url)
                            _, total_dmg = parse_boss_damage(stats_html)
                            if boss.hp > 0:
                                hp_pct = max(0.0, 100.0 - (total_dmg / boss.hp * 100.0))
                                hp_str = f" — **{hp_pct:.0f}%** HP left"
                        except Exception:
                            pass
                    return f"🔴 **{boss.full_name}** — Spawned{hp_str}{killed_str}"
                if not boss.spawn_days or not boss.last_killed:
                    return f"💤 **{boss.full_name}** — No data"
                try:
                    clean = _re.sub(r'<[^>]+>', '', boss.last_killed).strip()
                    killed_dt = None
                    for fmt in ("%a, %d %b %Y %I:%M%p", "%a, %d %b %Y %I:%M %p",
                                "%m-%d-%y %I:%M%p", "%Y-%m-%d %H:%M"):
                        try:
                            killed_dt = datetime.strptime(clean, fmt).replace(tzinfo=CST)
                            break
                        except ValueError:
                            continue
                    if not killed_dt:
                        return f"💤 **{boss.full_name}**{killed_str}"
                    base   = _td(days=boss.spawn_days)
                    min_dt = killed_dt + base * 0.75
                    max_dt = killed_dt + base * 1.25
                    now    = datetime.now(tz=_tz.utc)
                    if now < min_dt:
                        diff = min_dt - now
                        return f"💤 **{boss.full_name}** — Window opens in {diff.days}d {diff.seconds//3600}h{killed_str}"
                    elif min_dt <= now <= max_dt:
                        diff = max_dt - now
                        return f"⏳ **{boss.full_name}** — In window · {diff.days}d {diff.seconds//3600}h left{killed_str}"
                    else:
                        return f"⚠️ **{boss.full_name}** — Window passed{killed_str}"
                except Exception:
                    return f"💤 **{boss.full_name}**{killed_str}"

            boss_lines = [await _spawn_info(b) for b in bosses]
            boss_text  = "\n".join(boss_lines) or "No data"

            # --- Yesterday's consolidated focused-crew drops (same for all crews) ---
            from datetime import date as _date, timedelta as _td2
            yesterday      = (_date.today() - _td2(days=1)).isoformat()
            focus_rec      = db.get_focus_drops(yesterday)
            focus_drops_text = _format_focus_drops(focus_rec)

            # --- Per-crew summary ---
            autoboss_cog = self.bot.cogs.get("BossRaidCommands")
            import re as _re2

            for crew_full in summary_crews:
                trustees = db.get_trustees_by_crew(crew_full)
                if not trustees:
                    continue

                # AutoBoss section
                ab_lines   = []
                if autoboss_cog and autoboss_cog._running:
                    s      = autoboss_cog._status
                    source = s.get("source", "").lower()
                    # Check if this autoboss session is for this crew
                    crew_obj = next((c for c in db.get_crews()
                                    if c["full_name"].lower() == crew_full.lower()), None)
                    crew_short = crew_obj.get("name", "").lower() if crew_obj else ""
                    is_this_crew = (source == crew_short or
                                   crew_full.lower() in source or
                                   source in crew_full.lower())

                    if is_this_crew:
                        phase = s.get("phase", "raiding")
                        if phase == "waiting_boss":
                            ab_lines.append("⏳ Waiting for a boss to spawn")
                        elif phase == "waiting_md":
                            ab_lines.append("🔄 Waiting for MD to recharge")
                            # MD check using skills_info.php with cooldown threshold
                            ready_active = 0
                            cooldown_n   = 0
                            not_trained  = 0
                            for t in trustees:
                                suid = t.get("suid")
                                if not suid:
                                    not_trained += 1
                                    continue
                                try:
                                    self.session._session.cookie_jar.update_cookies(
                                        {"ow_userid": str(suid)}, response_url=_SIGIL_URL
                                    )
                                    html_md = await self.session.get("skills_info.php?id=3014")
                                    self.session._session.cookie_jar.update_cookies(
                                        {"ow_userid": str(self.session.user_id)}, response_url=_SIGIL_URL
                                    )
                                    lvl_m = _re2.search(r"Markdown Level (\d+)", html_md)
                                    level = int(lvl_m.group(1)) if lvl_m else 0
                                    if level <= 1:
                                        not_trained += 1
                                        continue
                                    if "recharging" in html_md.lower():
                                        cd_m = _re2.search(r"(\d+)\s*minutes?\s*remaining", html_md, _re2.I)
                                        remaining = int(cd_m.group(1)) if cd_m else 0
                                        if remaining > 384:
                                            ready_active += 1  # still active
                                        else:
                                            cooldown_n += 1
                                    else:
                                        ready_active += 1  # ready to cast
                                except Exception:
                                    not_trained += 1
                            ab_lines.append(
                                f"MD: **{ready_active}** ready/active · **{cooldown_n}** cooldown · **{not_trained}** not trained"
                            )
                        else:
                            md_end  = s.get("md_end_max", 0)
                            md_secs = max(0, int(md_end - datetime.now().timestamp()))
                            md_str  = f"{md_secs//3600}h {(md_secs%3600)//60}m" if md_secs > 0 else "expired"
                            ab_lines.append(f"⚔️ Raiding · Boss: **{s.get('boss','—')}**")
                            ab_lines.append(
                                f"{s.get('raids',0)} raids · {s.get('damage',0):,} damage · MD expires in {md_str}"
                            )
                    else:
                        ab_lines.append("⏹️ AutoBoss not running for this crew")
                else:
                    ab_lines.append("⏹️ AutoBoss not running")

                embed = discord.Embed(
                    title=f"☀️ Good morning — Daily Summary · {today_str}",
                    colour=es.COLOR_INFO
                )
                embed.add_field(name=f"{es.ICON_BOSS} Server Bosses", value=boss_text, inline=False)
                embed.add_field(
                    name=f"{es.ICON_DROPS} Yesterday's Focused Drops",
                    value=focus_drops_text[:1024],
                    inline=False,
                )
                embed.add_field(
                    name=f"🤖 AutoBoss — {crew_full}",
                    value="\n".join(ab_lines),
                    inline=False
                )

                # Bot health — uses HealthMonitor's existing tracked stats
                health_cog = self.bot.cogs.get("HealthMonitor")
                if health_cog:
                    from datetime import timezone as _tz2
                    from cogs.health import BOT_START_TIME as _bot_start
                    now_health = datetime.now(_tz2.utc)
                    uptime     = now_health - _bot_start

                    health_lines = []
                    hrs, rem = divmod(int(uptime.total_seconds()), 3600)
                    mins = rem // 60
                    health_lines.append(f"⏱️ Uptime: **{hrs}h {mins}m**")
                    health_lines.append(f"🔄 Re-logins: **{health_cog._relogins}**")
                    health_lines.append(f"⚠️ Errors logged: **{len(health_cog._errors)}**")
                    if health_cog._last_raid:
                        ago_mins = int((now_health - health_cog._last_raid).total_seconds() // 60)
                        last_raid_str = f"{ago_mins}m ago" if ago_mins < 60 else f"{ago_mins//60}h {ago_mins%60}m ago"
                        health_lines.append(f"⚔️ Last raid: **{last_raid_str}**")
                    else:
                        health_lines.append("⚔️ Last raid: **None this session**")
                    if health_cog._raid_fails > 0:
                        health_lines.append(f"🔴 Consecutive raid failures: **{health_cog._raid_fails}**")

                    embed.add_field(name="🏥 Bot Health", value="\n".join(health_lines), inline=False)

                embed.set_footer(text="Next summary tomorrow at 9:00 AM")
                embed.timestamp = datetime.now(_tz.utc)
                await channel.send(embed=embed)

        except Exception as e:
            print(f"Daily summary error: {e}")

    async def _post_envoy_drops(self, envoy):
        from outwar.scraper import parse_prime_god_loot, get_latest_envoy_pool
        import re as _re
        try:
            drops_channel = await self._get_alert_channel("envoys") or await self._get_alert_channel("drops") or await self._get_alert_channel("gods")
            if not drops_channel:
                print(f"[DROPS] No drops channel for envoy {envoy.name}")
                return
            target_id = envoy.envoy_id if envoy.envoy_id > 0 else None
            if not target_id:
                print(f"[DROPS] No target_id for {envoy.name}")
                return
            settings = db.get_settings()
            pool_number = settings.get("envoy_loot_pool", 49)
            try:
                envoy_page = await self.session.get(f"envoy?target={target_id}")
                sse_m = _re.search(r"spawnid=(\d+)&envoyid=\d+", envoy_page)
                if sse_m:
                    pool_number = int(sse_m.group(1))
                    print(f"[DROPS] {envoy.name}: pool={pool_number} from page SSE URL")
                else:
                    detected = get_latest_envoy_pool(envoy_page)
                    if detected:
                        pool_number = detected + 1
                        print(f"[DROPS] {envoy.name}: history max={detected}, trying pool={pool_number}")
            except Exception as e:
                print(f"[DROPS] Pool detect failed for {envoy.name}: {e}, using {pool_number}")
            sse_url = f"ajax/timedgod_loot_sse.php?spawnid={pool_number}&envoyid={target_id}"
            print(f"[DROPS] Envoy SSE: {sse_url}")
            loot_by_crew = []
            sse_data = None
            # Envoy loot pools are large — rolling can take 30+ minutes.
            # Use a 60-minute timeout and retry up to 3 times on failure.
            for attempt in range(3):
                try:
                    print(f"[DROPS] {envoy.name} SSE attempt {attempt+1} (up to 60 min wait)...")
                    sse_data = await self.session.get_sse(sse_url, timeout_secs=3600)
                    if sse_data and len(sse_data) > 100:
                        loot_by_crew = parse_prime_god_loot(sse_data)
                        if loot_by_crew:
                            print(f"[DROPS] {envoy.name} got {len(loot_by_crew)} winners")
                            break
                        print(f"[DROPS] {envoy.name} SSE len={len(sse_data)} no loot parsed — retrying")
                    else:
                        print(f"[DROPS] {envoy.name} SSE too short: {len(sse_data) if sse_data else 0}")
                except Exception as e:
                    print(f"[DROPS] {envoy.name} attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(30)
            else:
                await drops_channel.send(f"⚠️ **{envoy.name}** drops timed out after 3 attempts.")
                return
            if not loot_by_crew:
                preview = (sse_data or "")[:300]
                await drops_channel.send(f"\u26a0\ufe0f **{envoy.name}** no loot parsed. SSE preview: ```{preview}```")
                return
            total_items = sum(len(e.get("items", [])) for e in loot_by_crew)
            # Build trustee name set for highlighting
            trustee_names = {t["name"].lower() for t in db.get_trustees()}

            embed = es.drops_embed(
                f"{es.ICON_DROPS} {envoy.name} — Drop Summary (Pool {pool_number})",
                description=f"**{total_items}** item(s) → **{len(loot_by_crew)}** winner(s)"
            )
            part = 1
            for entry in sorted(loot_by_crew, key=lambda x: len(x.get("items", [])), reverse=True):
                winner = entry["crew"]
                items  = entry.get("items", [])
                if not items:
                    continue
                is_trustee = winner.lower() in trustee_names
                star   = f"{es.ICON_STAR} " if is_trustee else ""
                header = f"{star}{winner} — {len(items)} item{'s' if len(items) != 1 else ''}"
                embed.add_field(name=header[:256], value=es.bullet_list(items)[:1024], inline=False)
                if len(embed.fields) == 25:
                    part += 1
                    await drops_channel.send(embed=embed)
                    embed = es.drops_embed(
                        f"{es.ICON_DROPS} {envoy.name} — Drop Summary (Pool {pool_number}, part {part})"
                    )
            if embed.fields or embed.description:
                await drops_channel.send(embed=embed)
        except Exception as e:
            import traceback
            print(f"[DROPS] Error posting envoy drops for {envoy.name}: {e}")
            print(traceback.format_exc())

    async def _post_god_drops(self, channel, god: God):
        try:
            god_html = await self.session.get(f"primegods?mobid={god.god_id}")
            data     = parse_prime_god_page(god_html)
            loot_url = data.get("loot_url")
            if not loot_url:
                print(f"[DROPS] No loot_url found for {god.name}")
                return

            import re as _re
            spawn_m = _re.search(r"spawnid=(\d+)", loot_url)
            if not spawn_m:
                print(f"[DROPS] No spawnid in {loot_url}")
                return
            spawnid = spawn_m.group(1)

            # Fetch SSE stream directly — this blocks until loot is complete
            sse_url  = f"ajax/timedgod_loot_sse.php?spawnid={spawnid}&envoyid=0"
            print(f"[DROPS] Fetching SSE: {sse_url}")
            sse_data = None
            for attempt in range(5):
                try:
                    sse_data = await self.session.get_sse(sse_url)
                    if sse_data and len(sse_data) > 50:
                        break
                    print(f"[DROPS] SSE attempt {attempt+1} too short: {len(sse_data) if sse_data else 0}")
                except Exception as e:
                    print(f"[DROPS] SSE fetch attempt {attempt+1} failed: {e}")
                if attempt < 4:
                    await asyncio.sleep(10)

            if not sse_data:
                print(f"[DROPS] All SSE fetch attempts failed for {god.name}")
                return


            from outwar.scraper import parse_prime_god_loot
            loot_by_crew = parse_prime_god_loot(sse_data)
            print(f"[DROPS] loot_by_crew count={len(loot_by_crew)}")

            # Persist focused-crew drops for the daily summary
            self._record_focus_drops(loot_by_crew)

            # stats = every crew that killed this spawn (authoritative kill list)
            stats     = data.get("stats") or []
            kills_map = {s["crew"].lower(): s for s in stats}

            drops_channel = await self._get_alert_channel("drops") or channel
            if not drops_channel:
                print(f"[DROPS] No drops channel configured")
                return

            # ── Build the with-drops list, enriched with kills from stats ──
            # display items: points combined into one line, amulets combined into
            # one line, everything else kept. drop_count is the TRUE number of
            # individual drops (counts quantities, not display lines).
            crews_with_drops = []   # (crew_name, kills, pct, is_focus, items, drop_count)
            drop_crew_keys   = set()
            for entry in loot_by_crew:
                crew_name = entry["crew"]
                display_items, drop_count = _aggregate_loot_display(entry)
                if not display_items:
                    continue
                drop_crew_keys.add(crew_name.lower())
                kd   = kills_map.get(crew_name.lower(), {})
                crews_with_drops.append((
                    crew_name,
                    kd.get("kills", 0),
                    kd.get("pct", 0.0),
                    self._is_focus_crew(crew_name),
                    display_items,
                    drop_count,
                ))

            # Sort: focus crews first, then by kills descending
            crews_with_drops.sort(key=lambda c: (not c[3], -c[1]))

            # ── No-drops crews = killed the spawn (in stats) but got nothing ──
            # Pull each one's kill count too, and sort by kills descending.
            no_drops = []   # (crew_name, kills, pct, is_focus)
            for s in stats:
                if s["crew"].lower() not in drop_crew_keys:
                    no_drops.append((
                        s["crew"],
                        s.get("kills", 0),
                        s.get("pct", 0.0),
                        self._is_focus_crew(s["crew"]),
                    ))
            no_drops.sort(key=lambda c: (not c[3], -c[1]))

            total_drops = sum(c[5] for c in crews_with_drops)
            total_kills = sum(s.get("kills", 0) for s in stats)

            embed = es.drops_embed(
                f"{es.ICON_DROPS} {god.name} — Drop Summary",
                description=(
                    f"**{len(crews_with_drops)}** crew(s) looted · "
                    f"**{total_drops}** drop(s) · "
                    f"**{total_kills}** total kills this spawn"
                ),
            )

            # ── One field per looting crew: kills in the header, items below ──
            for crew_name, kills, pct, is_focus, items, drop_count in crews_with_drops:
                header = es.crew_header(crew_name, is_focus, kills, pct)
                body   = es.bullet_list(items)
                embed.add_field(name=header[:256], value=body[:1024] or "—", inline=False)

                # Discord hard-caps embeds at 25 fields — flush and continue
                if len(embed.fields) == 25:
                    await drops_channel.send(embed=embed)
                    embed = es.drops_embed(f"{es.ICON_DROPS} {god.name} — Drop Summary (continued)")

            # ── No Drops block, placed AFTER all the looting crews ──
            if no_drops:
                if len(embed.fields) >= 24:
                    await drops_channel.send(embed=embed)
                    embed = es.drops_embed(f"{es.ICON_DROPS} {god.name} — Drop Summary (continued)")
                nd_lines = []
                for crew_name, kills, pct, is_focus in no_drops:
                    star = f"{es.ICON_STAR} " if is_focus else ""
                    kl   = es.kills_label(kills)
                    nd_lines.append(f"{star}{crew_name} ({kl})" if kl else f"{star}{crew_name}")
                embed.add_field(
                    name=f"{es.ICON_NODROP} No Drops ({len(no_drops)})",
                    value=" · ".join(nd_lines)[:1024],
                    inline=False,
                )

            if embed.fields:
                await drops_channel.send(embed=embed)
            elif not crews_with_drops and not no_drops:
                print(f"[DROPS] Nothing to post for {god.name}")

        except Exception as e:
            import traceback
            print(f"Error posting god drops: {e}")
            print(traceback.format_exc())

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(name="set-alert-channel")
    async def set_alert_channel(self, ctx, alert_type: str, channel: discord.TextChannel = None):
        """Set the alert channel for gods, bosses or envoys."""
        valid_types = ("gods", "bosses", "envoys", "drops", "summary", "log")
        alert_type = alert_type.lower()
        if alert_type not in valid_types:
            await ctx.send(f"Unknown type `{alert_type}`. Valid: {', '.join(valid_types)}")
            return
        target = channel or ctx.channel
        db.set_alert_channel(alert_type, target.id)
        await ctx.send(f"✅ **{alert_type.title()}** alerts will now post to {target.mention}")

    @commands.command(name="alert-channels")
    async def alert_channels(self, ctx):
        """Show all configured alert channels."""
        embed = es.info_embed("⚙️ Alert Channel Configuration")
        for alert_type in ("gods", "bosses", "envoys", "drops", "summary", "log"):
            channel_id = db.get_alert_channel(alert_type)
            if channel_id:
                ch = self.bot.get_channel(channel_id)
                value = ch.mention if ch else f"Unknown channel ({channel_id})"
            else:
                value = f"Not set — use `!set-alert-channel {alert_type}`"
            embed.add_field(name=alert_type.title(), value=value, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="envoys")
    async def envoy_status(self, ctx):
        """Show current envoy spawn status."""
        await ctx.send("🔍 Checking Envoys...")
        html = await self.session.get("primegods")
        envoys = parse_envoys(html)
        self._envoys_cache = envoys

        if not envoys:
            await ctx.send("No envoys found on the page.")
            return

        spawned = [e for e in envoys if e.spawned]
        dead = [e for e in envoys if not e.spawned]

        embed = es.info_embed(f"{es.ICON_ENVOY} Envoy Status")
        embed.add_field(
            name=f"🌟 Spawned ({len(spawned)})",
            value="\n".join(f"**{e.name}**" for e in spawned) or "None",
            inline=True
        )
        if dead:
            embed.add_field(
                name=f"💀 Dead ({len(dead)})",
                value="\n".join(e.name for e in dead),
                inline=True
            )
        await ctx.send(embed=embed)

    @commands.command(name="envoy-pool")
    async def envoy_pool(self, ctx, number: int = None):
        """View or set the current envoy loot pool number. Usage: !envoy-pool 48"""
        settings = db.get_settings()
        if number is None:
            current = settings.get("envoy_loot_pool", 48)
            await ctx.send(f"Current envoy loot pool: **{current}**")
            return
        settings["envoy_loot_pool"] = number
        db.save_settings(settings)
        await ctx.send(f"✅ Envoy loot pool set to **{number}**")

    @commands.command(name="envoy-fetch")
    async def envoy_fetch(self, ctx, pool: int = None):
        """Manually fetch drops for all 8 envoys. Pool auto-detected from spawn history if not specified. Usage: !envoy-fetch [pool]"""
        from outwar.scraper import Envoy as _Envoy, get_latest_envoy_pool

        envoys = [
            _Envoy(envoy_id=1, name="Mob Envoy",       spawned=False, stats_url="envoy?target=1"),
            _Envoy(envoy_id=2, name="PVP Envoy",       spawned=False, stats_url="envoy?target=2"),
            _Envoy(envoy_id=3, name="Raid Envoy",      spawned=False, stats_url="envoy?target=3"),
            _Envoy(envoy_id=4, name="Alvar Envoy",     spawned=False, stats_url="envoy?target=4"),
            _Envoy(envoy_id=5, name="Delruk Envoy",    spawned=False, stats_url="envoy?target=5"),
            _Envoy(envoy_id=6, name="Vordyn Envoy",    spawned=False, stats_url="envoy?target=6"),
            _Envoy(envoy_id=7, name="PP Envoy (Hard)", spawned=False, stats_url="envoy?target=7"),
            _Envoy(envoy_id=8, name="PP Envoy (Easy)", spawned=False, stats_url="envoy?target=8"),
        ]

        if pool is not None:
            # Manual override — store it and use it directly
            settings = db.get_settings()
            settings["envoy_loot_pool"] = pool
            db.save_settings(settings)
            await ctx.send(f"📦 Fetching envoy drops for pool **{pool}** across all 8 envoys...")
        else:
            # Auto-detect from spawn history (raid envoy = target 3, representative)
            try:
                envoy_page = await self.session.get("envoy?target=3")
                detected   = get_latest_envoy_pool(envoy_page)
                pool       = detected + 1 if detected else db.get_settings().get("envoy_loot_pool", 49)
                await ctx.send(f"📦 Auto-detected pool **{pool}** (history max={detected}) — fetching all 8 envoys...")
            except Exception:
                pool = db.get_settings().get("envoy_loot_pool", 49)
                await ctx.send(f"📦 Fetching envoy drops for pool **{pool}** across all 8 envoys...")

        for envoy in envoys:
            await self._post_envoy_drops(envoy)

        await ctx.send(f"✅ All 8 envoy drops posted for pool **{pool}**.")

    @commands.command(name="envoy-shop")
    async def envoy_shop(self, ctx):
        """Display the Envoy Quartermaster shop — paginated by shop section."""
        SHOPS = [
            {
                "title": "🏪 Envoy Quartermaster — Treasury (Lesser)",
                "colour": discord.Colour.gold(),
                "currency": "Lesser Envoy Trophy",
                "items": [
                    ("Recharge the Fury",        1),
                    ("Standard Issue Neuralyzer", 2),
                    ("Advanced Neuralyzer",       2),
                    ("Recharge Totem",            1),
                    ("Power Potion Pack",         5),
                    ("Flask of Endurance",        1),
                    ("Quest Experience Potion",   3),
                    ("Faction Change",            5),
                    ("Character Class Change",    5),
                    ("25 Character Slots",        5),
                    ("Magic Gem",                 3),
                    ("Infinite Tower Spheroid",   3),
                    ("God Slayer Kill Confirmed", 1),
                    ("Transcended Extract",       7),
                    ("Tier 2 Booster Upgrade",   25),
                ],
            },
            {
                "title": "🏪 Envoy Quartermaster — Astral/Veldara (Lesser)",
                "colour": discord.Colour.purple(),
                "currency": "Lesser Envoy Trophy",
                "items": [
                    ("Vault Tear",           2),
                    ("Dimensional Bond",     2),
                    ("Runestone of Rillax",  3),
                    ("Runestone of Villax",  3),
                    ("Runestone of Holgor",  4),
                    ("Runestone of Arcon",   4),
                    ("Runestone of Firan",   4),
                    ("Runestone of Kinark",  4),
                    ("Runestone of Shayar",  4),
                    ("Runestone of Agnar",   5),
                    ("Runestone of Valzek",  5),
                    ("2x Interstellar Vessels", 1),
                    ("Exposed Rock",         1),
                    ("Pulsating Stone",      4),
                    ("Bottled Chaos",        8),
                    ("Boon of Nature",      20),
                ],
            },
            {
                "title": "🏪 Envoy Quartermaster — Badge (Lesser)",
                "colour": discord.Colour.blue(),
                "currency": "Lesser Envoy Trophy",
                "items": [
                    ("Yorrons Fragment",       1),
                    ("3x Blood Crystals",      1),
                    ("Vanishas Fragrance",     1),
                    ("4x Drolba Tonics",       1),
                    ("10x Elemental Fusers",   2),
                    ("3x Guardian Stamps",     5),
                    ("5x Talismans",           5),
                    ("Epic 1.0-3.0 Items",     5),
                    ("Nanomite Items",         5),
                    ("Valyrian Items",         5),
                    ("SoL-MSoL Items",        10),
                ],
            },
            {
                "title": "🏪 Envoy Quartermaster — Grand (Grand)",
                "colour": discord.Colour.dark_gold(),
                "currency": "Grand Envoy Trophy",
                "items": [
                    ("Vortex of the Elements", 5),
                    ("Vortex of Betrayal",     6),
                    ("Vortex of Death",        7),
                    ("Demonic Glyph",          7),
                    ("Unstable Mineral",       2),
                    ("Unstable Jewel",         2),
                    ("Boon of Unity",          6),
                    ("Boon of Power",          7),
                    ("Boon of Vision",         8),
                    ("Boon of Chaos",          8),
                    ("Prominent Medal",        3),
                    ("Eminent Medal",          4),
                ],
            },
        ]

        def _make_page(shop, idx, total):
            e = discord.Embed(title=shop["title"], colour=shop["colour"])
            e.description = f"Currency: **{shop['currency']}**"
            lines = [f"`{cost:>2}` — {name}" for name, cost in shop["items"]]
            e.add_field(name="Item", value="\n".join(lines), inline=False)
            e.set_footer(text=f"⏮ ◀ ▶ ⏭  •  Page {idx+1}/{total}")
            return e

        pages = [_make_page(s, i, len(SHOPS)) for i, s in enumerate(SHOPS)]
        current = [0]

        msg = await ctx.send(embed=pages[0])
        for emoji in ("⏮", "◀", "▶", "⏭"):
            await msg.add_reaction(emoji)

        import asyncio as _asyncio
        queue = _asyncio.Queue()
        EMOJIS = {"⏮", "◀", "▶", "⏭"}

        async def on_reaction(payload):
            if (payload.message_id == msg.id
                    and payload.user_id == ctx.author.id
                    and str(payload.emoji) in EMOJIS):
                queue.put_nowait(str(payload.emoji))

        self.bot.add_listener(on_reaction, "on_raw_reaction_add")
        self.bot.add_listener(on_reaction, "on_raw_reaction_remove")

        try:
            while True:
                try:
                    emoji = await _asyncio.wait_for(queue.get(), timeout=120.0)
                except _asyncio.TimeoutError:
                    break

                if emoji == "▶":
                    current[0] = (current[0] + 1) % len(pages)
                elif emoji == "◀":
                    current[0] = (current[0] - 1) % len(pages)
                elif emoji == "⏭":
                    current[0] = len(pages) - 1
                elif emoji == "⏮":
                    current[0] = 0

                await msg.edit(embed=pages[current[0]])
        finally:
            self.bot.remove_listener(on_reaction, "on_raw_reaction_add")
            self.bot.remove_listener(on_reaction, "on_raw_reaction_remove")
            try:
                await msg.clear_reactions()
            except Exception:
                pass

    async def envoy_drops(self, ctx, *, envoy_name: str):
        """Fetch and display drops for an envoy."""
        if not self._envoys_cache:
            html = await self.session.get("primegods")
            self._envoys_cache = parse_envoys(html)

        envoy = next(
            (e for e in self._envoys_cache if envoy_name.lower() in e.name.lower()),
            None
        )
        if not envoy:
            await ctx.send(f"Envoy `{envoy_name}` not found.")
            return

        await ctx.send(f"Fetching drops for **{envoy.name}**...")
        try:
            if envoy.stats_url:
                html = await self.session.get(envoy.stats_url)
                drops, _ = parse_god_stats_page(html)
                if drops:
                    embed = discord.Embed(
                        title=f"📦 {envoy.name} — Drops",
                        color=discord.Color.purple()
                    )
                    for drop in drops:
                        embed.add_field(
                            name=drop.crew_name[:256],
                            value=f"{drop.loot or 'No Items'}\n*{drop.damage}*"[:1024],
                            inline=True
                        )
                    await ctx.send(embed=embed)
                    return
        except Exception as e:
            print(f"Envoy drops error: {e}")
        await ctx.send(f"No drop data found for **{envoy.name}**.")

    @commands.command(name="poll-now")
    @commands.has_permissions(manage_channels=True)
    async def poll_now(self, ctx):
        """Manually trigger a full poll right now."""
        await ctx.send("🔄 Polling gods, envoys and bosses now...")
        await self._poll_gods()
        await self._poll_bosses()
        await ctx.send("✅ Poll complete.")

    @commands.command(name="alerting")
    async def alerting(self, ctx):
        """Show all configured alert channels."""
        alert_types = {
            "gods":    "Prime God spawns & deaths",
            "bosses":  "Server boss spawns & deaths",
            "envoys":  "Envoy spawns & deaths",
            "drops":   "Drop summaries (prime gods & bosses)",
            "summary": "Daily 9am summary",
        }
        embed = es.info_embed("⚙️ Alert Channel Configuration")
        for alert_type, label in alert_types.items():
            channel_id = db.get_alert_channel(alert_type)
            if channel_id:
                ch = self.bot.get_channel(channel_id)
                value = ch.mention if ch else f"Unknown channel ({channel_id})"
            else:
                value = f"Not set — use `!set-alert-channel {alert_type} #channel`"
            embed.add_field(name=label, value=value, inline=False)

        # Summary crews
        summary_crews = db.get_summary_crews()
        embed.add_field(
            name="Summary crews",
            value="\n".join(f"• {c}" for c in summary_crews) if summary_crews
                  else "None set — use `!summary-set <crew>`",
            inline=False
        )

        # Focus crews
        focus_crews = db.get_focus_crews()
        embed.add_field(
            name="Drop highlight crews",
            value="\n".join(f"• {c}" for c in focus_crews) if focus_crews
                  else "None set — use `!focusdrops <crew>`",
            inline=False
        )

        await ctx.send(embed=embed)
    @commands.command(name="summary-now")
    async def summary_now(self, ctx):
        """Manually trigger the daily summary right now."""
        channel_id = db.get_alert_channel("summary")
        if not channel_id:
            await ctx.send("\u274c No summary channel set. Use `!set-alert-channel summary #channel`")
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            await ctx.send(f"\u274c Summary channel ID `{channel_id}` not found.")
            return
        summary_crews = db.get_summary_crews()
        if not summary_crews:
            await ctx.send("\u274c No crews set. Use `!summary-set <crew>` first.")
            return
        await ctx.send(f"\u2705 Generating summary for **{', '.join(summary_crews)}**")
        try:
            await self._post_daily_summary()
        except Exception as e:
            await ctx.send(f"\u274c Error: `{e}`")
            print(f"summary-now error: {e}")

    @commands.command(name="summary-set")
    async def summary_set(self, ctx, *, crew_name: str):
        """Add a crew to the daily summary. Usage: !summary-set <crew>"""
        crew = db.get_crew(crew_name)
        full_name = crew["full_name"] if crew else crew_name
        if db.add_summary_crew(full_name):
            await ctx.send(f"✅ **{full_name}** added to daily summary.")
        else:
            await ctx.send(f"**{full_name}** is already in the summary.")

    @commands.command(name="summary-remove")
    async def summary_remove(self, ctx, *, crew_name: str):
        """Remove a crew from the daily summary. Usage: !summary-remove <crew>"""
        crew = db.get_crew(crew_name)
        full_name = crew["full_name"] if crew else crew_name
        if db.remove_summary_crew(full_name):
            await ctx.send(f"✅ **{full_name}** removed from daily summary.")
        else:
            await ctx.send(f"**{full_name}** wasn't in the summary.")

    @commands.command(name="summary-list")
    async def summary_list(self, ctx):
        """Show crews included in the daily summary."""
        crews = db.get_summary_crews()
        if not crews:
            await ctx.send("No crews set. Use `!summary-set <crew>` to add one.")
        else:
            await ctx.send("**Daily summary crews:**\n" + "\n".join(f"• {c}" for c in crews))

    @commands.command(name="focusdrops")
    async def focusdrops_add(self, ctx, *, crew_name: str):
        """Highlight a crew in drop summaries. Usage: !focusdrops <crew>"""
        crew = db.get_crew(crew_name)
        full_name = crew["full_name"] if crew else crew_name
        if db.add_focus_crew(full_name):
            await ctx.send(f"✅ **{full_name}** will now be highlighted in drop summaries.")
        else:
            await ctx.send(f"**{full_name}** is already in the focus list.")

    @commands.command(name="unfocusdrops")
    async def focusdrops_remove(self, ctx, *, crew_name: str):
        """Remove a crew from drop highlights. Usage: !unfocusdrops <crew>"""
        crew = db.get_crew(crew_name)
        full_name = crew["full_name"] if crew else crew_name
        if db.remove_focus_crew(full_name):
            await ctx.send(f"✅ **{full_name}** removed from drop highlights.")
        else:
            await ctx.send(f"**{full_name}** wasn't in the focus list.")

    @commands.command(name="focuslist")
    async def focusdrops_list(self, ctx):
        """Show all crews currently highlighted in drop summaries."""
        crews = db.get_focus_crews()
        if not crews:
            await ctx.send("No focus crews set. Use `!focusdrops <crew>` to add one.")
        else:
            await ctx.send(f"**Highlighted crews in drop summaries:**\n" + "\n".join(f"• {c}" for c in crews))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_focus_crew(self, crew_name: str) -> bool:
        """Check if a crew name matches any focus crew."""
        focus = [c.lower() for c in db.get_focus_crews()]
        name_lower = crew_name.lower()
        return any(f in name_lower or name_lower in f for f in focus)

    def _record_focus_drops(self, loot_by_crew):
        """Persist focused-crew drops under today's date for the daily summary."""
        try:
            from datetime import date
            today = date.today().isoformat()
            merged_items, merged_points, any_focus = {}, 0, False
            for entry in loot_by_crew or []:
                if not self._is_focus_crew(entry.get("crew", "")):
                    continue
                any_focus = True
                for name, cnt in (entry.get("item_counts") or {}).items():
                    merged_items[name] = merged_items.get(name, 0) + cnt
                merged_points += entry.get("points", 0) or 0
            if any_focus and (merged_items or merged_points):
                db.record_focus_drops(today, merged_items, merged_points)
                print(f"[DROPS] Recorded focused drops for {today}: "
                      f"{sum(merged_items.values())} items, {merged_points} points")
        except Exception as e:
            print(f"[DROPS] focus-drop record failed: {e}")

    async def _get_alert_channel(self, alert_type: str):
        channel_id = db.get_alert_channel(alert_type)
        if not channel_id:
            return None
        return self.bot.get_channel(channel_id)


async def setup(bot):
    await bot.add_cog(GodMonitor(bot))
