"""
misc_commands.py

Contains the remaining commands ported from the C# bot:
    !check-item <item> [group/crew]   — Check who has (or lacks with !) an item
    !eligible                         — Level 79s close to level 80
    !top <amount> <group>             — Top N by power/ele/chaos in a group
    !top-all <amount> <stat>          — Top N across ALL trustees
    !bottom <amount> <group>          — Bottom N in a group
    !rgastats <group>                 — Full stat summary for a named group
    !crewstats <crew>                 — Full stat summary for a crew
    !giveaway <prize> [exclude...]    — Random winner from the giveaway pool
"""

import asyncio
import random
import discord
from discord.ext import commands
from outwar import database as db, logger
from cogs import embed_style as es
from outwar.scraper import (
    parse_character_profile,
    parse_backpack_for_item,
    parse_equipment_page,
)
from outwar.constants import (
    ITEMS,
    TOP_ALL_EXCLUDED_NAMES,
    TOP_ALL_EXCLUDED_SUBSTRINGS,
    GIVEAWAY_USERS,
)

BASE_URL = "https://sigil.outwar.com"
SEMAPHORE_SIZE = 10  # max concurrent Outwar requests


class MiscCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.trustees = db.get_trustees()

    @property
    def session(self):
        return self.bot.outwar

    # ------------------------------------------------------------------
    # !check-item
    # ------------------------------------------------------------------

    @commands.command(name="check-item")
    async def check_item(self, ctx, item_key: str, group: str = None):
        """
        Check which characters have (or lack) an item.
        Prefix item with ! to reverse (show who DOESN'T have it).
        Examples:
            !check-item rems to
            !check-item !bubble mygroup
            !check-item crest
        """
        reverse = item_key.startswith("!")
        item_key = item_key.lstrip("!").lower()

        item = ITEMS.get(item_key)
        if not item:
            await ctx.send(
                f"Item `{item_key}` not recognised. "
                f"Known items: {', '.join(sorted(ITEMS.keys()))}"
            )
            return

        if group is None and item_key == "resist":
            await ctx.send("Only single character check possible for resist.")
            return

        # Resolve trustees
        trustees = self._resolve_trustees(group, item["level"])
        if not trustees:
            await ctx.send(f"No trustees found for `{group or 'all'}`.")
            return

        direction = "without" if reverse else "with"
        location = "equipment" if item["equipped"] else "backpacks"
        await ctx.send(
            f"Checking {location} for **{len(trustees)}** character(s) "
            f"{direction} **{item['name']}**..."
        )

        # Fetch in parallel
        semaphore = asyncio.Semaphore(SEMAPHORE_SIZE)
        results = []  # list of {"name", "item_name", "item_id", "quantity"}

        async def _check(t):
            async with semaphore:
                suid = t.get("suid") or _extract_suid(t.get("url", ""))
                if not suid:
                    return
                try:
                    if item["equipped"]:
                        html = await self.session.get(
                            f"equipment.php?uid={suid}&id={suid}&server=1"
                        )
                        found = parse_equipment_page(html, item["name"])
                    else:
                        tab = f"&tab={item['tab']}" if item["tab"] != "equipped" else ""
                        html = await self.session.get(
                            f"ajax/backpackcontents.php?suid={suid}&id={suid}&server=1{tab}"
                        )
                        found = parse_backpack_for_item(html, item["name"])

                    for f in found:
                        results.append({
                            "name": t["name"],
                            "item_name": f["item_name"],
                            "item_id": f.get("item_id"),
                            "quantity": f.get("quantity", 1),
                        })
                except Exception as e:
                    logger.warning("MISC", f"check-item error for {t['name']}: {e}")

        await asyncio.gather(*[_check(t) for t in trustees])

        # Reverse mode: show who DOESN'T have it
        if reverse:
            found_names = {r["name"] for r in results}
            missing = [t for t in trustees if t["name"] not in found_names]
            results = [{"name": t["name"], "item_name": "", "item_id": None, "quantity": 1}
                       for t in missing]

        if not results:
            embed = discord.Embed(
                description=f"**Characters {direction} {item['name']}**\n0"
            )
            await ctx.send(embed=embed)
            return

        # Sort
        if item["count"]:
            if item["name"] == "Badge Reputation":
                results = [r for r in results if r["quantity"] > 12]
                results.sort(key=lambda x: x["quantity"])
            else:
                results.sort(key=lambda x: x["quantity"])
        else:
            if item["name"] == "Chaos Ore":
                results.sort(key=lambda x: x["name"])
            else:
                results.sort(key=lambda x: (x["item_name"], x["name"]))

        # Build message
        await self._send_item_results(ctx, results, item, reverse)

    # ------------------------------------------------------------------
    # !eligible
    # ------------------------------------------------------------------

    @commands.command(name="eligible")
    async def eligible(self, ctx):
        """Show level 79 characters close to reaching level 80 (United Path eligible)."""
        EIGHTY_EXP = 2_000_000_000
        THRESHOLD_EXP = 1_800_000_000

        level79 = [t for t in self.trustees if t.get("level") == 79]
        await ctx.send(
            f"Checking **{len(level79)}** level 79 characters for United Path eligibility..."
        )

        semaphore = asyncio.Semaphore(SEMAPHORE_SIZE)

        async def _check(t):
            async with semaphore:
                try:
                    html = await self.session.get(
                        f"profile.php?transnick={t['name']}&server=1"
                    )
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, "lxml")
                    exp_node = soup.select_one(
                        "#divProfile div div div div div div div table tbody tr:nth-of-type(2) td:nth-of-type(2) b font"
                    )
                    name_node = soup.select_one("#divHeaderName font")
                    if exp_node and name_node:
                        exp = int(exp_node.get_text(strip=True).replace(",", ""))
                        if exp >= THRESHOLD_EXP:
                            return {"name": name_node.get_text(strip=True), "exp": exp}
                except Exception as e:
                    logger.warning("MISC", f"eligible error for {t['name']}: {e}")
                return None

        raw = await asyncio.gather(*[_check(t) for t in level79])
        chars = sorted([r for r in raw if r], key=lambda x: x["exp"])

        if not chars:
            await ctx.send("No level 79 characters close to level 80 found.")
            return

        embed = discord.Embed()
        chunk = ""
        for c in chars:
            needed = EIGHTY_EXP - c["exp"]
            line = f"{c['name']} (needs {needed:,} exp)\n"
            if len(chunk) + len(line) > 1000:
                embed.add_field(
                    name=f"Eligible for United Path raids ({len(chars)})",
                    value=chunk, inline=False
                )
                chunk = ""
            chunk += line
        if chunk:
            embed.add_field(
                name=f"Eligible for United Path raids ({len(chars)})",
                value=chunk, inline=False
            )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # !top / !bottom
    # ------------------------------------------------------------------

    @commands.command(name="top")
    async def top(self, ctx, amount: int, group: str, stat: str = None):
        """Show top N characters by stat in a group or crew.
        Stats: power, ele, chaos (optional — shows all if omitted)
        Usage: !top 30 lod ele"""
        if amount <= 0:
            await ctx.send("Amount must be greater than 0.")
            return
        await self._rank_command(ctx, amount, group, ascending=False, stat_filter=stat)

    @commands.command(name="bottom")
    async def bottom(self, ctx, amount: int, group: str, stat: str = None):
        """Show bottom N characters by stat in a group or crew.
        Usage: !bottom 10 lod power"""
        if amount <= 0:
            await ctx.send("Amount must be greater than 0.")
            return
        await self._rank_command(ctx, amount, group, ascending=True, stat_filter=stat)

    @commands.command(name="top-all")
    async def top_all(self, ctx, amount: int, stat: str):
        """
        Show top N across ALL trustees (level 80+) by a single stat.
        Stats: power, ele, chaos
        """
        if amount <= 0:
            await ctx.send("Your amount sucks balls.")
            return

        stat = stat.lower()
        stat_map = {"power": "power", "ele": "elemental", "chaos": "chaos"}
        if stat not in stat_map:
            await ctx.send(f"Unknown stat `{stat}`. Use: power, ele, chaos")
            return

        filtered = [
            t for t in self.trustees
            if t.get("level", 0) >= 80
            and t["name"].lower() not in TOP_ALL_EXCLUDED_NAMES
            and not any(s in t["name"].lower() for s in TOP_ALL_EXCLUDED_SUBSTRINGS)
        ]

        await ctx.send(
            f"Checking TOP **{amount}** by **{stat.upper()}** "
            f"for **{len(filtered)}** characters..."
        )

        characters = await self._fetch_characters_parallel(filtered)
        # Filter out low-ele alts (same threshold as C# — ele >= 10000)
        characters = [c for c in characters if c.elemental >= 10000]

        if not characters:
            await ctx.send("No characters found.")
            return

        attr = stat_map[stat]
        ranked = sorted(characters, key=lambda c: getattr(c, attr), reverse=True)[:amount]

        embed = es.info_embed(f"🏆 Top {amount} by {stat.upper()}")
        chunk = ""
        for i, c in enumerate(ranked, 1):
            val = getattr(c, attr)
            line = f"{i}. {c.name} - {val:,}\n"
            if len(chunk) + len(line) > 1000:
                embed.add_field(name=f"TOP {amount} by {stat.upper()}", value=chunk, inline=False)
                chunk = ""
            chunk += line
        if chunk:
            embed.add_field(name=f"TOP {amount} by {stat.upper()}", value=chunk, inline=False)

        totals = (
            f"**Power:** {sum(c.power for c in ranked):,}  "
            f"**Ele:** {sum(c.elemental for c in ranked):,}  "
            f"**Chaos:** {sum(c.chaos for c in ranked):,}"
        )
        await ctx.send(embed=embed)
        await ctx.send(totals)

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # !giveaway
    # ------------------------------------------------------------------

    @commands.command(name="giveaway")
    async def giveaway(self, ctx, prize: str, *exclude):
        """
        Pick a random winner from the giveaway pool.
        Exclude participants by name.
        Example: !giveaway "Rare Item" rabbit liam
        """
        pool = dict(GIVEAWAY_USERS)

        for name in exclude:
            pool.pop(name.lower(), None)

        if not pool:
            await ctx.send("No participants left after exclusions!")
            return

        winner_name, winner_id = random.choice(list(pool.items()))
        await ctx.send(f"Congratulations! <@{winner_id}> wins **{prize}**!")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_trustees(self, group: str, min_level: int = 1) -> list:
        """Resolve a group/crew name or None to a list of trustees."""
        all_trustees = db.get_trustees()

        if group is None:
            trustees = all_trustees
        else:
            rga_group = db.get_group(group)
            crew = db.get_crew(group)

            if rga_group:
                names = set(db.group_to_list(rga_group))
                trustees = [t for t in all_trustees if t["name"] in names]
            elif crew:
                trustees = db.get_trustees_by_crew(crew["full_name"])
            else:
                normalized = db.normalize_crew(group)
                trustees = db.get_trustees_by_crew(normalized)
                if not trustees:
                    group_lower = group.lower()
                    trustees = [t for t in all_trustees if group_lower in t.get("crew", "").lower()]
                if not trustees:
                    trustees = [t for t in all_trustees if t["name"].lower() == group.lower()]

        return [t for t in trustees if t.get("level", 0) >= min_level]

    async def _fetch_characters_parallel(self, trustees: list) -> list:
        semaphore = asyncio.Semaphore(SEMAPHORE_SIZE)

        async def _fetch(t):
            async with semaphore:
                try:
                    html = await self.session.get(
                        f"profile.php?transnick={t['name']}&server=1"
                    )
                    return parse_character_profile(html, t["name"])
                except Exception as e:
                    logger.warning("MISC", f"Error fetching {t['name']}: {e}")
                    return None

        results = await asyncio.gather(*[_fetch(t) for t in trustees])
        return [c for c in results if c]

    async def _rank_command(self, ctx, amount: int, group: str, ascending: bool, stat_filter: str = None):
        trustees = self._resolve_trustees(group)
        if not trustees:
            await ctx.send("No characters found.")
            return

        stat_map = {
            "power": ("Power",            "power",     "Power"),
            "ele":   ("Elemental Damage", "elemental", "Elemental Damage"),
            "chaos": ("Chaos Damage",     "chaos",     "Chaos Damage"),
        }

        # Resolve stat filter
        if stat_filter:
            stat_filter = stat_filter.lower()
            if stat_filter not in stat_map:
                await ctx.send(f"Unknown stat `{stat_filter}`. Use: power, ele, chaos")
                return
            stats_to_show = [stat_map[stat_filter]]
        else:
            stats_to_show = list(stat_map.values())

        direction = "BOTTOM" if ascending else "TOP"
        label = f"in {group.upper()}" if group else ""
        stat_label = stats_to_show[0][2] if stat_filter else "all stats"
        await ctx.send(
            f"Checking {direction} **{amount}** {label} by **{stat_label}** "
            f"for **{len(trustees)}** characters..."
        )

        characters = await self._fetch_characters_parallel(trustees)
        if not characters:
            await ctx.send("No characters found.")
            return

        for stat, attr, slabel in stats_to_show:
            ranked = sorted(
                [c for c in characters if getattr(c, attr, 0) > 0],
                key=lambda c: getattr(c, attr),
                reverse=not ascending
            )[:amount]

            if not ranked:
                continue

            from outwar.table_image import render_ranking_table
            label_str = f"in {group.upper()}" if group else "Overall"
            title = f"{direction} {amount} {label_str} — {slabel}"
            rows = [
                {"rank": i, "name": c.name, "value": getattr(c, attr)}
                for i, c in enumerate(ranked, 1)
            ]
            buf = render_ranking_table(title, rows, slabel)
            await ctx.send(file=discord.File(buf, filename=f"top_{attr}.png"))

            # Send names as copyable text
            names_str = " ".join(c.name for c in ranked)
            await ctx.send(f"```{names_str}```")

    async def _send_item_results(self, ctx, results: list, item: dict, reverse: bool):
        """Format and send item check results, splitting into multiple embeds if needed."""
        chunks = [results[i:i+80] for i in range(0, len(results), 80)]
        direction = "without" if reverse else "with"
        total = len(results)

        for chunk in chunks:
            embed = discord.Embed()
            message = ""

            if item["grouped"]:
                # Just list names together
                embed.title = f"Characters {direction} {item['name']} ({total})"
                message = f"**Characters {direction} {item['name']}**\n"
                message += " ".join(r["name"] for r in chunk)
            else:
                # Group by item tier
                if item["count"]:
                    last_qty = None
                    for i, r in enumerate(chunk):
                        if r["quantity"] != last_qty:
                            header = f"\n\n**{r['quantity']}x {r['item_name']}**\n" if i > 0 else f"**{r['quantity']}x {r['item_name']}**\n"
                            message += header
                            last_qty = r["quantity"]
                        message += r["name"] + " "
                else:
                    last_item = None
                    for i, r in enumerate(chunk):
                        item_label = r["item_name"] or item["name"]
                        if item_label != last_item:
                            header = f"\n\n**{item_label}**\n" if i > 0 else f"**{item_label}**\n"
                            message += header
                            last_item = item_label
                        message += r["name"] + " "

            if len(message) > 4096:
                message = message[:4093] + "..."
            embed.description = message
            await ctx.send(embed=embed)


def _extract_suid(url: str) -> int:
    import re
    m = re.search(r"suid=(\d+)", url)
    return int(m.group(1)) if m else 0


class GroupStatCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def session(self):
        return self.bot.outwar

    def _resolve_group(self, group: str) -> list:
        # Delegates to the single canonical impl in database.resolve_group
        return db.resolve_group(group)

    @commands.command(name="pcaps")
    async def prime_caps(self, ctx, *, group: str):
        """Show prime god cap status for all characters in a group. Usage: !pcaps <group>"""
        from outwar.scraper import parse_god_cap, parse_character_stats_profile, parse_rage
        from outwar.table_image import render_caps_table
        from yarl import URL
        import discord

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        msg = await ctx.send(f"⏳ Fetching cap status for **{len(trustees)}** characters...")

        semaphore = asyncio.Semaphore(8)
        SIGIL_URL = URL("https://sigil.outwar.com")

        async def _fetch_cap(t):
            suid = t.get("suid")
            if not suid:
                return {"name": t["name"], "cur": 0, "max": 0, "error": True,
                        "faction": "—", "crew": t.get("crew", "—"), "rage": 0}
            try:
                async with semaphore:
                    self.session._session.cookie_jar.update_cookies(
                        {"ow_userid": str(suid)}, response_url=SIGIL_URL
                    )
                    home_html    = await self.session.get("home")
                    self.session._session.cookie_jar.update_cookies(
                        {"ow_userid": str(suid)}, response_url=SIGIL_URL
                    )
                    profile_html = await self.session.get("profile")

                used, max_cap = parse_god_cap(home_html)
                cur          = (max_cap - used) if max_cap else 0
                live_rage    = parse_rage(home_html)
                profile      = parse_character_stats_profile(profile_html)
                faction      = profile.get("faction") or "None"
                flvl         = profile.get("faction_level", 0)
                return {
                    "name":    t["name"],
                    "cur":     cur,
                    "max":     max_cap,
                    "error":   False,
                    "faction": f"{faction} ({flvl})" if flvl else faction,
                    "crew":    t.get("crew", "—"),
                    "rage":    live_rage,
                }
            except Exception:
                return {"name": t["name"], "cur": 0, "max": 0, "error": True,
                        "faction": "—", "crew": t.get("crew", "—"), "rage": 0}
            finally:
                self.session._session.cookie_jar.update_cookies(
                    {"ow_userid": str(self.session.user_id)}, response_url=SIGIL_URL
                )

        results = await asyncio.gather(*[_fetch_cap(t) for t in trustees])
        results.sort(key=lambda x: (x["cur"] <= 0 if x["max"] else True, -x["cur"]))

        buf = render_caps_table(group, results)
        await msg.delete()
        await ctx.send(file=discord.File(buf, filename="cap_status.png"))

    @commands.command(name="group-stats")
    async def group_stats(self, ctx, *, group: str):
        """Show power, ele, chaos and faction for all characters in a group. Usage: !group-stats <group>"""
        from outwar.scraper import parse_character_stats_profile
        from outwar.table_image import render_stats_table
        from yarl import URL
        import discord

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        msg = await ctx.send(f"⏳ Fetching stats for **{len(trustees)}** characters...")

        semaphore = asyncio.Semaphore(8)
        SIGIL_URL = URL("https://sigil.outwar.com")

        async def _fetch_stats(t):
            suid = t.get("suid")
            if not suid:
                return None
            try:
                async with semaphore:
                    self.session._session.cookie_jar.update_cookies(
                        {"ow_userid": str(suid)}, response_url=SIGIL_URL
                    )
                    html = await self.session.get("profile")
                stats = parse_character_stats_profile(html)
                stats["name"] = t["name"]
                return stats
            except Exception:
                return None
            finally:
                self.session._session.cookie_jar.update_cookies(
                    {"ow_userid": str(self.session.user_id)}, response_url=SIGIL_URL
                )

        results = await asyncio.gather(*[_fetch_stats(t) for t in trustees])
        results = [r for r in results if r]

        if not results:
            await msg.delete()
            await ctx.send("No stats found.")
            return

        results.sort(key=lambda x: x.get("power", 0), reverse=True)
        buf = render_stats_table(group, results)
        await msg.delete()
        await ctx.send(file=discord.File(buf, filename="group_stats.png"))


async def setup(bot):
    await bot.add_cog(MiscCommands(bot))
    await bot.add_cog(GroupStatCommands(bot))
    await bot.add_cog(CompareCommands(bot))


class CompareCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def session(self):
        return self.bot.outwar

    @commands.command(name="compare")
    async def compare(self, ctx, char1: str, char2: str):
        """Compare two characters side by side. Usage: !compare <char1> <char2>"""
        from outwar.scraper import parse_character_stats_profile, parse_character_crew_and_level
        from outwar.table_image import render_compare_table

        msg = await ctx.send(f"📊 Fetching stats for **{char1}** and **{char2}**...")

        async def _fetch(name):
            html  = await self.session.get(f"profile.php?transnick={name}&server=1")
            stats = parse_character_stats_profile(html)
            crew, level, rage, _crew_id = parse_character_crew_and_level(html)
            stats.update({"name": name, "level": level, "rage": rage, "crew": crew})
            return stats

        try:
            s1 = await _fetch(char1)
            s2 = await _fetch(char2)
            buf = render_compare_table(s1, s2)
            await msg.delete()
            await ctx.send(file=discord.File(buf, filename="compare.png"))
        except Exception as e:
            await msg.delete()
            await ctx.send(f"Error fetching stats: {e}")
