"""
utility_commands.py

Quality of life commands:
    !status          — Bot health, session info, monitor status
    !rage <group>    — Live rage check for a group
    !who <character> — Quick stats and cap lookup for one character
    !uncapped <god>  — Which groups have enough non-capped accounts
"""

import asyncio
from datetime import datetime, timezone
import discord
from discord.ext import commands
from yarl import URL
from outwar import database as db
from outwar.scraper import parse_god_cap, parse_rage, parse_character_stats_profile

SIGIL_URL = URL("https://sigil.outwar.com")


BOT_VERSION = "1.3.0"
BOT_UPDATED = "11 Jun 2026"


class UtilityCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._start_time = datetime.now(timezone.utc)

    @property
    def session(self):
        return self.bot.outwar

    # ------------------------------------------------------------------
    # !ping
    # ------------------------------------------------------------------

    @commands.command(name="ping")
    async def ping(self, ctx):
        """Show bot latency."""
        latency = round(self.bot.latency * 1000)
        emoji   = "🟢" if latency < 100 else "🟡" if latency < 250 else "🔴"
        await ctx.send(f"{emoji} Pong! Latency: **{latency}ms**")

    # ------------------------------------------------------------------
    # !status
    # ------------------------------------------------------------------


    @commands.command(name="status")
    async def deathbot_status(self, ctx):
        """Show bot health — uptime, session, monitor status."""
        from outwar.table_image import render_status_table

        uptime  = datetime.now(timezone.utc) - self._start_time
        hours, r = divmod(int(uptime.total_seconds()), 3600)
        mins, secs = divmod(r, 60)

        trustees = db.get_trustees()
        gods     = db.get_prime_gods()
        groups   = db.get_groups()
        settings = db.get_settings()
        spawned  = [g for g in gods if g.get("spawned")]

        god_ch  = settings.get("alert_channels", {}).get("gods")
        boss_ch = settings.get("alert_channels", {}).get("bosses")

        buf = render_status_table({
            "uptime":       f"{hours}h {mins}m {secs}s",
            "version":      f"v{BOT_VERSION} ({BOT_UPDATED})",
            "session_user": f"LoDRaid ({self.session.user_id})",
            "trustees":     len(trustees),
            "gods":         len(gods),
            "spawned":      len(spawned),
            "groups":       len(groups),
            "god_channel":  f"#{god_ch}" if god_ch else None,
            "boss_channel": f"#{boss_ch}" if boss_ch else None,
        })
        await ctx.send(file=discord.File(buf, filename="status.png"))

    # ------------------------------------------------------------------
    # !rage
    # ------------------------------------------------------------------

    @commands.command(name="rage")
    async def rage_check(self, ctx, *, group: str):
        """Show live rage for all characters in a group. Usage: !rage <group>"""
        from outwar.table_image import render_rage_table

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        msg = await ctx.send(f"⏳ Fetching rage for **{len(trustees)}** characters...")
        semaphore = asyncio.Semaphore(10)

        async def _fetch_rage(t):
            suid = t.get("suid")
            if not suid:
                return {"name": t["name"], "rage": 0}
            try:
                async with semaphore:
                    self.session._session.cookie_jar.update_cookies(
                        {"ow_userid": str(suid)}, response_url=SIGIL_URL
                    )
                    html = await self.session.get("home")
                return {"name": t["name"], "rage": parse_rage(html)}
            except Exception:
                return {"name": t["name"], "rage": 0}
            finally:
                self.session._session.cookie_jar.update_cookies(
                    {"ow_userid": str(self.session.user_id)}, response_url=SIGIL_URL
                )

        results = await asyncio.gather(*[_fetch_rage(t) for t in trustees])
        results.sort(key=lambda x: x["rage"], reverse=True)

        buf = render_rage_table(group, results)
        await msg.delete()
        await ctx.send(file=discord.File(buf, filename="rage.png"))

    # ------------------------------------------------------------------
    # !who
    # ------------------------------------------------------------------

    @commands.command(name="who")
    async def who(self, ctx, *, name: str):
        """Quick stats and cap lookup for a single character. Usage: !who <name>"""
        from outwar.table_image import render_who_table

        trustees = db.get_trustees()
        trustee  = next((t for t in trustees if t["name"].lower() == name.lower()), None)
        if not trustee:
            await ctx.send(f"Character `{name}` not found in trustees.")
            return

        suid = trustee.get("suid")
        if not suid:
            await ctx.send(f"No SUID found for `{name}`.")
            return

        msg = await ctx.send(f"⏳ Fetching stats for **{name}**...")

        try:
            self.session._session.cookie_jar.update_cookies(
                {"ow_userid": str(suid)}, response_url=SIGIL_URL
            )
            home_html = await self.session.get("home")
            self.session._session.cookie_jar.update_cookies(
                {"ow_userid": str(suid)}, response_url=SIGIL_URL
            )
            profile_html = await self.session.get("profile")

            _cap_used, cap_max = parse_god_cap(home_html)
            cap_cur            = (cap_max - _cap_used) if cap_max else 0
            live_rage        = parse_rage(home_html)
            stats            = parse_character_stats_profile(profile_html)

            buf = render_who_table(name, {
                "crew":           trustee.get("crew") or "—",
                "level":          trustee.get("level", 0),
                "rage":           live_rage,
                "power":          stats.get("power", 0),
                "elemental":      stats.get("elemental", 0),
                "chaos":          stats.get("chaos", 0),
                "faction":        stats.get("faction", "None"),
                "faction_level":  stats.get("faction_level", 0),
                "cap_cur":        cap_cur,
                "cap_max":        cap_max,
            })
        except Exception as e:
            await msg.delete()
            await ctx.send(f"Error fetching stats for `{name}`: {e}")
            return
        finally:
            self.session._session.cookie_jar.update_cookies(
                {"ow_userid": str(self.session.user_id)}, response_url=SIGIL_URL
            )

        await msg.delete()
        await ctx.send(file=discord.File(buf, filename="who.png"))

    # ------------------------------------------------------------------
    # !uncapped
    # ------------------------------------------------------------------

    @commands.command(name="uncapped")
    async def uncapped(self, ctx, *, god_name: str):
        """Show which groups have enough non-capped accounts to hit a god. Usage: !uncapped <god>"""
        god = db.get_prime_god(god_name)
        if not god:
            await ctx.send(f"God `{god_name}` not found.")
            return

        required = god.get("max_members") or god.get("recommended") or 10
        msg = await ctx.send(f"⏳ Checking caps for **{god['name']}** (needs {required})...")

        all_groups = db.get_groups()
        if not all_groups:
            await msg.delete()
            await ctx.send("No groups found. Run `!autorank` first.")
            return

        trustees  = db.get_trustees()
        semaphore = asyncio.Semaphore(10)

        async def _check_cap(t):
            suid = t.get("suid")
            if not suid:
                return t["name"], True
            try:
                async with semaphore:
                    self.session._session.cookie_jar.update_cookies(
                        {"ow_userid": str(suid)}, response_url=SIGIL_URL
                    )
                    html = await self.session.get("home")
                used, max_cap = parse_god_cap(html)
                avail = (max_cap - used) if max_cap else 0
                return t["name"], (max_cap > 0 and avail <= 0)
            except Exception:
                return t["name"], False
            finally:
                self.session._session.cookie_jar.update_cookies(
                    {"ow_userid": str(self.session.user_id)}, response_url=SIGIL_URL
                )

        all_cap_results = dict(await asyncio.gather(*[_check_cap(t) for t in trustees]))

        ready     = []
        not_ready = []
        for group_name, group_data in sorted(all_groups.items()):
            members   = db.group_to_list(group_data)
            available = [m for m in members if not all_cap_results.get(m, False)]
            capped_n  = len(members) - len(available)
            if len(available) >= required:
                ready.append((group_name, len(available), len(members), capped_n))
            else:
                not_ready.append((group_name, len(available), len(members), capped_n))

        from outwar.table_image import render_uncapped_table
        buf = render_uncapped_table(god["name"], required, ready, not_ready)
        await msg.delete()
        await ctx.send(file=discord.File(buf, filename="uncapped.png"))

    def _resolve_group(self, group: str) -> list:
        # Delegates to the single canonical impl in database.resolve_group
        return db.resolve_group(group)


async def setup(bot):
    await bot.add_cog(UtilityCommands(bot))
