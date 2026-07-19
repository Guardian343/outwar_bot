import asyncio
import discord
from discord.ext import commands
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from outwar import database as db
from cogs import embed_style as es
from outwar.scraper import (
    parse_bosses, parse_boss_damage,
    unscramble_loot
)

BASE_URL = "https://sigil.outwar.com"


@dataclass
class RaidStatus:
    status: str = ""
    target: str = ""
    damage: int = 0
    total_damage: int = 0
    nr_of_raids: int = 0
    avg_per_raid: int = 0
    avg_per_character: int = 0
    amount_of_characters: int = 0
    current_former: Optional[str] = None
    current_sinner: Optional[str] = None
    last_raid_stats: str = ""
    start_time: datetime = field(default_factory=datetime.now)


class BossCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.trustees = db.get_trustees()
        self.raid_status: Optional[RaidStatus] = None
        self._raid_task: Optional[asyncio.Task] = None
        self._raid_accounts: list = []

    @property
    def session(self):
        return self.bot.outwar

    # ------------------------------------------------------------------
    # !boss dmg / !boss drops
    # ------------------------------------------------------------------

    @commands.group(name="boss", invoke_without_command=True)
    async def boss(self, ctx):
        """Boss command hub. Use !boss <action> — e.g. !boss raid lod 100"""
        embed = es.info_embed(
            f"{es.ICON_BOSS} Boss Commands",
            description=(
                "**Raiding**\n"
                "`!boss auto <group> [boss]` — hands-free auto-raid (fire once)\n"
                "`!boss raid <group> [count] [boss]` — raid a crew boss, no skills/pots\n"
                "`!boss single <group> [boss]` — one round, no skills\n"
                "`!boss stop` — stop the session · `!boss status` — live stats\n"
                "`!boss group` — accounts in session · `!boss proceed` — confirm partial raid\n"
                "`!boss pots <crew>` — boss pots · `!boss reset-md` — reset Markdown state\n\n"
                "**Info**\n"
                "`!boss list` — all bosses and their status\n"
                "`!boss records` — all-time best damage\n"
                "`!boss dmg <name>` — live damage table for a spawned boss\n"
                "`!boss drops <name>` — drop table after a boss is killed\n"
                "`!boss window <boss> <desc>` — set a spawn window\n\n"
                "_Classic names (`!autoboss`, `!bosslist`, `!boss-stop`…) still work._"
            )
        )
        await ctx.send(embed=embed)

    # ── Raid actions: thin subcommands that re-run the classic command so its
    #    auth and argument parsing apply unchanged (logic stays untouched). ──
    async def _redispatch(self, ctx, target_name: str, rest: str = ""):
        ctx.message.content = f"{ctx.prefix}{target_name} {rest}".rstrip()
        await self.bot.process_commands(ctx.message)

    @boss.command(name="auto")
    async def boss_auto(self, ctx, *, rest: str = ""):
        """Start hands-free auto-raiding. Same as !autoboss."""
        await self._redispatch(ctx, "autoboss", rest)

    @boss.command(name="raid")
    async def boss_raid(self, ctx, *, rest: str = ""):
        """Raid a crew boss, no skills/pots. Same as !bossraid."""
        await self._redispatch(ctx, "bossraid", rest)

    @boss.command(name="single")
    async def boss_single(self, ctx, *, rest: str = ""):
        """One round of boss raids, no skills. Same as !raidboss."""
        await self._redispatch(ctx, "raidboss", rest)

    @boss.command(name="stop")
    async def boss_stop_sub(self, ctx, *, rest: str = ""):
        """Stop the running session. Same as !boss-stop."""
        await self._redispatch(ctx, "boss-stop", rest)

    @boss.command(name="status")
    async def boss_status_sub(self, ctx, *, rest: str = ""):
        """Live session stats. Same as !boss-status."""
        await self._redispatch(ctx, "boss-status", rest)

    @boss.command(name="group")
    async def boss_group_sub(self, ctx, *, rest: str = ""):
        """Accounts in the session. Same as !boss-group."""
        await self._redispatch(ctx, "boss-group", rest)

    @boss.command(name="records")
    async def boss_records_sub(self, ctx, *, rest: str = ""):
        """All-time best damage per boss. Same as !boss-records."""
        await self._redispatch(ctx, "boss-records", rest)

    @boss.command(name="pots")
    async def boss_pots_sub(self, ctx, *, rest: str = ""):
        """Use boss pots on a crew. Same as !boss-pots."""
        await self._redispatch(ctx, "boss-pots", rest)

    @boss.command(name="proceed")
    async def boss_proceed_sub(self, ctx, *, rest: str = ""):
        """Confirm a partial-readiness raid. Same as !boss-proceed."""
        await self._redispatch(ctx, "boss-proceed", rest)

    @boss.command(name="window")
    async def boss_window_sub(self, ctx, *, rest: str = ""):
        """Set a boss spawn window. Same as !boss-window."""
        await self._redispatch(ctx, "boss-window", rest)

    @boss.command(name="list", aliases=["all"])
    async def boss_list_sub(self, ctx, *, rest: str = ""):
        """All bosses and their status. Same as !bosslist."""
        await self._redispatch(ctx, "bosslist", rest)

    @boss.command(name="reset-md", aliases=["resetmd", "reset"])
    async def boss_reset_md_sub(self, ctx, *, rest: str = ""):
        """Reset Markdown skill state before a raid. Same as !reset-md."""
        await self._redispatch(ctx, "reset-md", rest)

    @boss.command(name="dmg")
    async def boss_dmg(self, ctx, *, name: str):
        """
        Show the live damage table for a spawned boss.
        Usage: !boss dmg <name>
        Example: !boss dmg cosmos
        """
        boss = await self._get_boss_by_name(name)
        if not boss:
            await ctx.send(f"Boss `{name}` not found. Use `!bosslist` to see current bosses.")
            return

        if not boss.spawned:
            await ctx.send(f"**{boss.full_name}** is currently dead. Use `!boss drops {name}` to see the drop table.")
            return

        await ctx.send(f"Fetching damage table for **{boss.full_name}**...")
        import time as _time
        stats_url_cb = f"{boss.stats_url}{'&' if '?' in boss.stats_url else '?'}_={int(_time.time())}"
        stats_html = await self.session.get(stats_url_cb)
        message, total_dmg = parse_boss_damage(stats_html)

        if total_dmg == 0 and not message:
            await ctx.send(
                f"⚠️ No damage data found for **{boss.full_name}** — "
                f"the damage table may not have loaded yet, or the boss was just spawned. "
                f"Try again in a moment."
            )
            return

        pct = max(0.0, 100.0 - (total_dmg / boss.hp * 100.0)) if boss.hp > 0 else 0

        embed = discord.Embed(
            title=f"📊 {boss.full_name} — {pct:.2f}% HP remaining",
            color=discord.Color.red() if pct < 25 else discord.Color.orange() if pct < 50 else discord.Color.green()
        )

        # Split into chunks if too long
        chunks = [message[i:i+1024] for i in range(0, len(message), 1024)]
        for i, chunk in enumerate(chunks[:5]):
            embed.add_field(
                name="Damage" if i == 0 else "​",
                value=chunk,
                inline=False
            )

        embed.set_footer(text=f"Total damage dealt: {total_dmg:,}")
        await ctx.send(embed=embed)

    @boss.command(name="drops")
    async def boss_drops(self, ctx, name: str, crew: str = None, style: str = None):
        """
        Show the drop table for a defeated boss.
        Default: compact table showing only crews with items.
        Add '2' for full detailed list.
        Usage:
            !boss drops <name>         — compact table (crews with items only)
            !boss drops <name> 2       — full detailed list
            !boss drops <name> <crew>  — single crew only
        Examples:
            !boss drops cosmos
            !boss drops cosmos 2
            !boss drops cosmos lod
        """
        # Handle "!boss drops cosmos 2" — style passed as crew arg
        if crew in ("2", "full", "detailed"):
            style = crew
            crew = None

        boss = await self._get_boss_by_name(name)
        if not boss:
            await ctx.send(f"Boss `{name}` not found. Use `!bosslist` to see current bosses.")
            return

        if boss.spawned:
            await ctx.send(f"**{boss.full_name}** is still alive. Use `!boss dmg {name}` to see live damage.")
            return

        await ctx.send(f"Fetching drop table for **{boss.full_name}**...")
        stats_html = await self.session.get(boss.stats_url)

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(stats_html, "lxml")
        rows = soup.select("#content-header-row div table tbody tr")

        if not rows:
            await ctx.send("No drop data found — the boss may not have been killed yet.")
            return

        # Parse all rows
        parsed = []
        for row in rows:
            name_cell = row.select_one("td:nth-of-type(1)")
            dmg_cell  = row.select_one("td:nth-of-type(2)")
            loot_cell = row.select_one("td:nth-of-type(3)")
            if not name_cell:
                continue
            crew_title = name_cell.get_text(strip=True).replace("_", "\\_")
            dmg = dmg_cell.get_text(strip=True) if dmg_cell else "—"
            raw = loot_cell.get("onmouseover", "") if loot_cell else ""
            scrambled = (raw
                .replace("popup(event,'", "")
                .replace("<br>','808080')", "")
                .replace("','808080')", "")
                .replace("<br>", "|")
                .replace("\\", ""))
            loot = unscramble_loot(scrambled)
            parsed.append({"crew": crew_title, "dmg": dmg, "loot": loot})

        # Single crew filter
        if crew:
            crew_norm = db.normalize_crew(crew)
            parsed = [p for p in parsed if crew_norm.lower() in p["crew"].lower()]
            if not parsed:
                await ctx.send(f"No entry found for crew `{crew}` on this boss.")
                return

        focus_crews = db.get_focus_crews()

        def _is_focus(crew_name):
            return any(f.lower() in crew_name.lower() or crew_name.lower() in f.lower()
                      for f in focus_crews)

        full_mode = style in ("2", "full", "detailed")

        if full_mode:
            embed = es.drops_embed(
                f"{es.ICON_DROPS} {boss.full_name} — Full Drop Table",
                description=f"**{len(parsed)}** crew(s) listed"
            )
            for i, p in enumerate(parsed, 1):
                star   = f"{es.ICON_STAR} " if _is_focus(p['crew']) else ""
                dmg    = p.get('dmg', '')
                header = f"{star}{p['crew']} — {dmg} dmg" if dmg else f"{star}{p['crew']}"
                loot   = p['loot'] or 'No Items'
                items  = [l for l in loot.replace(" | ", "\n").split("\n") if l.strip()]
                value  = es.bullet_list(items) if len(items) > 1 else loot
                if len(value) > 1024:
                    value = value[:1021] + "..."
                embed.add_field(name=header[:256], value=value, inline=False)
                if len(embed.fields) == 25:
                    await ctx.send(embed=embed)
                    embed = es.drops_embed(
                        f"{es.ICON_DROPS} {boss.full_name} — Full Drop Table (continued)"
                    )
            if embed.fields:
                await ctx.send(embed=embed)
        else:
            with_items = [p for p in parsed if p["loot"] and p["loot"] != "No Items"]
            no_items   = [p for p in parsed if not p["loot"] or p["loot"] == "No Items"]

            embed = es.drops_embed(
                f"{es.ICON_DROPS} {boss.full_name} — Drops",
                description=f"**{len(with_items)}** crew(s) looted · **{len(no_items)}** with no drops"
            )

            if not with_items:
                embed.description = "No crews received items from this boss."
            else:
                for p in with_items:
                    loot_lines = [l for l in p["loot"].replace(" | ", "\n").split("\n") if l.strip()]
                    body  = es.bullet_list(loot_lines) if len(loot_lines) > 1 else p["loot"]
                    dmg   = p.get('dmg', '')
                    star  = f"{es.ICON_STAR} " if _is_focus(p['crew']) else ""
                    header = f"{star}{p['crew']} — {dmg} dmg" if dmg else f"{star}{p['crew']}"
                    value = body
                    if len(value) > 1024:
                        value = value[:1021] + "..."
                    embed.add_field(name=header[:256], value=value or "—", inline=False)

                if no_items:
                    no_item_names = " · ".join(p["crew"] for p in no_items)
                    embed.add_field(
                        name=f"{es.ICON_NODROP} No Drops ({len(no_items)})",
                        value=no_item_names[:1024],
                        inline=False
                    )

            embed.set_footer(text=f"Use !boss drops {name} 2 for full detailed list  ·  {es.BRAND_FOOTER}")
            await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Boss listing / status
    # ------------------------------------------------------------------    # ------------------------------------------------------------------
    # Helper — get boss by short name or partial full name
    # ------------------------------------------------------------------

    async def _get_boss_by_name(self, name: str):
        html = await self.session.get(
            "crew_bossspawns"
        )
        bosses = parse_bosses(html)
        name_lower = name.lower()
        return next(
            (b for b in bosses
             if b.name.lower() == name_lower
             or name_lower in b.full_name.lower()),
            None
        )


    @commands.command(name="bosslist")
    async def boss_list(self, ctx):
        """Show all server bosses and their spawn status."""
        from outwar.table_image import render_boss_table
        import re as _re

        msg = await ctx.send("⏳ Fetching boss status...")
        html   = await self.session.get("crew_bossspawns")
        bosses = parse_bosses(html)

        boss_rows = []
        for boss in bosses:
            hp_pct = None
            if boss.spawned:
                try:
                    stats_html = await self.session.get(boss.stats_url)
                    _, total_dmg = parse_boss_damage(stats_html)
                    hp_pct = max(0.0, 100.0 - (total_dmg / boss.hp * 100.0)) if boss.hp > 0 else 100.0
                    status = "ALIVE"
                except Exception as e:
                    hp_pct = 100.0
                    status = "ALIVE"
            else:
                status = "DEAD"

            # Calculate spawn window from last_killed + spawn_days ±25%
            spawn_window = ""
            if boss.spawn_days > 0 and (boss.last_killed or db.get_boss_death_dt(boss.full_name)):
                try:
                    from datetime import timezone, timedelta as _td
                    CST = timezone(_td(hours=-6))

                    # Prefer our own precise UTC death record; only parse the page's
                    # CST-assumed kill string if we've never observed this boss die.
                    killed_dt = db.get_boss_death_dt(boss.full_name)
                    last_killed_clean = _re.sub(r'<[^>]+>', '', boss.last_killed or "").strip()

                    if not killed_dt and last_killed_clean:
                        for fmt in (
                            "%a, %d %b %Y %I:%M%p",   # Sun, 7 Jun 2026 5:23am
                            "%a, %d %b %Y %I:%M %p",  # Sun, 7 Jun 2026 5:23 am
                            "%m-%d-%y %I:%M%p",
                            "%m-%d-%y %I:%M %p",
                            "%Y-%m-%d %H:%M",
                        ):
                            try:
                                killed_dt = datetime.strptime(last_killed_clean, fmt)
                                killed_dt = killed_dt.replace(tzinfo=CST)
                                break
                            except ValueError:
                                continue

                    if killed_dt:
                        base   = timedelta(days=boss.spawn_days)
                        min_dt = killed_dt + base * 0.75
                        max_dt = killed_dt + base * 1.25
                        now    = datetime.now(tz=timezone.utc)

                        if now < min_dt:
                            diff = min_dt - now
                            days = diff.days
                            hrs  = diff.seconds // 3600
                            status = "NEAR" if days == 0 else "DEAD"
                            spawn_window = f"Opens in {days}d {hrs}h" if days > 0 else f"Opens in {hrs}h"
                        elif min_dt <= now <= max_dt:
                            diff = max_dt - now
                            days = diff.days
                            hrs  = diff.seconds // 3600
                            status = "NEAR"
                            spawn_window = f"In window · {days}d {hrs}h left"
                        else:
                            spawn_window = "Window passed"
                    else:
                        spawn_window = f"Killed: {boss.last_killed}"
                except Exception:
                    spawn_window = f"Killed: {boss.last_killed}" if boss.last_killed else ""
            elif boss.spawn_days > 0:
                spawn_window = f"±25% of {boss.spawn_days}d"

            boss_rows.append({
                "name":         boss.full_name,
                "spawned":      boss.spawned,
                "status":       status,
                "hp_pct":       hp_pct if hp_pct is not None else 0,
                "hp_str":       f"{hp_pct:.1f}%" if (hp_pct is not None and boss.spawned) else "—",
                "spawn_window": spawn_window,
            })

        buf = render_boss_table(boss_rows)
        await msg.delete()
        await ctx.send(file=discord.File(buf, filename="boss_status.png"))

    @commands.command(name="boss-window")
    async def boss_window(self, ctx, boss_name: str, *, window: str):
        """
        Set the spawn window description for a boss.
        Usage: !boss-window cosmos Spawns every 3 days
        Usage: !boss-window cosmos clear
        """
        from outwar import database as db
        settings = db.get_settings()
        windows  = settings.get("boss_windows", {})

        # Match boss name from known bosses
        from outwar.scraper import BOSS_ATTRIBUTES
        matched = None
        for key in BOSS_ATTRIBUTES:
            if boss_name.lower() in key.lower():
                matched = key
                break

        if not matched:
            await ctx.send(f"Unknown boss `{boss_name}`. Use the short name e.g. `cosmos`, `death`, `mae`.")
            return

        if window.lower() == "clear":
            windows.pop(matched, None)
            await ctx.send(f"✅ Cleared spawn window for **{matched}**.")
        else:
            windows[matched] = window
            await ctx.send(f"✅ **{matched}** spawn window set to: `{window}`")

        settings["boss_windows"] = windows
        db.save_settings(settings)

    @commands.command(name="boss-records")
    async def boss_records(self, ctx):
        """Show all-time best raid damage per boss."""
        from outwar.table_image import render_boss_records_table
        settings = db.get_settings()
        records  = settings.get("boss_records", {})
        if not records:
            await ctx.send("No boss raid records yet — records are set when autoboss runs.")
            return
        buf = render_boss_records_table(records)
        await ctx.send(file=discord.File(buf, filename="boss_records.png"))


async def setup(bot):
    await bot.add_cog(BossCommands(bot))
