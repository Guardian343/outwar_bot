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

    @commands.command(name="bot-suid", hidden=True)
    async def botsuid_cmd(self, ctx, server: str = None, suid: int = None):
        """OWNER: view or set the bot account's suid for a server. The same account
        has a DIFFERENT suid per server (LoDRaid: 1157932 Sigil / 933209 Torax).
        Torax reads need the Torax suid or they hit the not-logged-in fallback.
        Usage:
          !bot-suid                     → show current mapping
          !bot-suid torax 933209         → set the Torax suid
        After setting, use !server-probe primegods to confirm Torax returns real data."""
        from outwar.servers import resolve_server, name_for
        s = db.get_settings()
        mapping = s.get("bot_suid_by_server", {}) or {}
        if server is None:
            sigil = getattr(self.session, "user_id", "?")
            lines = [f"**Sigil (1):** {sigil} _(auto-detected at login)_"]
            for sid, val in sorted(mapping.items()):
                lines.append(f"**{name_for(int(sid))} ({sid}):** {val}")
            if not mapping:
                lines.append("_No per-server overrides set. Torax will use the Sigil "
                             "suid and hit the fallback page until set._")
            await ctx.send("🔑 Bot suid mapping:\n" + "\n".join(lines))
            return
        sid = resolve_server(server, None)
        if not sid or sid == 1:
            await ctx.send("Set a non-Sigil server, e.g. `!bot-suid torax 933209`. "
                           "(Sigil's suid is auto-detected.)")
            return
        if suid is None:
            await ctx.send(f"Usage: `!bot-suid {server} <suid>`")
            return
        mapping[str(sid)] = int(suid)
        s["bot_suid_by_server"] = mapping
        db.save_settings(s)
        await ctx.send(f"✅ Set **{name_for(sid)}** bot suid to **{suid}**. "
                       f"Now run `!server-probe primegods` — Torax should return REAL data "
                       f"(different size, not the ~41KB fallback).")

    @commands.command(name="server-probe", hidden=True)
    async def server_probe(self, ctx, path: str = "crew_bossspawns"):
        """OWNER PROBE: test whether the bot's OWN rg_sess_id works cookielessly with
        a per-request serverid, for BOTH servers. Fetches <path> for serverid=1 and
        serverid=2 using the bot's own ssid+suid, and reports whether they differ.
        Different content = the per-request serverid model works for game pages (the
        foundation for concurrent-safe dual-server monitoring). Identical = it
        doesn't, and we need another approach. Read-only, safe."""
        from outwar import ssid_store as store
        import hashlib
        ssid = getattr(self.session, "session_id", None)
        suid = getattr(self.session, "user_id", None)
        if not ssid or not suid:
            await ctx.send("❌ Bot has no session_id/user_id available — can't probe.")
            return
        await ctx.send(f"🔬 Probing `{path}` for both servers using the bot's ssid + "
                       f"per-server suid…")
        results = {}
        for sid in (1, 2):
            try:
                # Use the correct suid for each server (Sigil vs Torax differ).
                server_suid = self.session._suid_for_server(sid)
                html = await store.sess_get(path, ssid, server_suid, sid)
            except Exception as e:
                html = f"__ERR__ {e}"
            results[sid] = html or ""

        def _fp(h):
            if h.startswith("__ERR__"):
                return h, 0, "(error)"
            digest = hashlib.md5(h.encode("utf-8", "ignore")).hexdigest()[:8]
            # crude signal: count boss/god rows or table rows
            import re as _re
            rows = len(_re.findall(r"<tr", h, _re.I))
            title = _re.search(r"<title>([^<]*)</title>", h, _re.I)
            t = title.group(1).strip()[:40] if title else "(no title)"
            return digest, len(h), f"{rows} rows · `{t}`"

        s1_fp, s1_len, s1_info = _fp(results[1])
        s2_fp, s2_len, s2_info = _fp(results[2])
        differ = (s1_fp != s2_fp) and not results[1].startswith("__ERR__") \
                 and not results[2].startswith("__ERR__")
        verdict = ("✅ **DIFFERENT** — per-request serverid WORKS for game pages. "
                   "Safe to build concurrent dual-server monitoring on this."
                   if differ else
                   "⚠️ **IDENTICAL (or error)** — per-request serverid did NOT switch "
                   "servers for this page. The bot's own ssid may not support the "
                   "cookieless serverid model; we'd need another approach.")
        await ctx.send(
            f"**Server probe — `{path}`**\n"
            f"• Sigil (1): {s1_len:,}b · fp `{s1_fp}` · {s1_info}\n"
            f"• Torax (2): {s2_len:,}b · fp `{s2_fp}` · {s2_info}\n"
            f"\n{verdict}"
        )

    @commands.command(name="envoy-debug", hidden=True)
    async def envoy_debug(self, ctx, target: str = None):
        """TEMP: dump raw envoy pages to files on the Pi for inspection."""
        import os
        out_dir = os.path.expanduser("~")
        pages = {"envoy_overview": "envoy_overview"}
        if target:
            pages[f"envoy_target_{target}"] = f"envoy?target={target}"
        else:
            pages["envoy_page"] = "envoy"
        results = []
        for label, path in pages.items():
            try:
                html = await self.session.get(path)
                fp = os.path.join(out_dir, f"{label}.html")
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(html)
                results.append(f"✅ `{path}` → `~/{label}.html` ({len(html):,} bytes)")
            except Exception as e:
                results.append(f"❌ `{path}` failed: {e}")
        await ctx.send("**Envoy debug dump:**\n" + "\n".join(results))

    # ------------------------------------------------------------------
    # TEMPORARY DEBUG — dump the home page (where God Cap lives) to inspect
    # whether cap expiry/reset timings are present. Remove once cap-expiry
    # display is built into !pcaps.
    # ------------------------------------------------------------------
    @commands.command(name="caps-debug", hidden=True)
    async def caps_debug(self, ctx, account: str = None):
        """TEMP: dump crew_capstatus by acting as a TRUSTEE character via the bot's own
        session (get_as → ow_userid cookie = 'select this character'). crew_capstatus
        shows the crew of whatever character is active, so acting as any character in a
        crew reveals that crew's caps. NEVER touches a stored account's session — uses
        only the bot's own login + trusteed characters. Auto-picks a LoD trustee, or
        name one: !caps-debug SomeName"""
        import os
        try:
            # Match the crew flexibly: trustees store the PLAIN name ('Legion of Death'),
            # while CREW_ALIASES may carry decorative symbols (†...†). Compare the core
            # alphanumeric text so daggers/stars/encoding don't cause a miss.
            import re as _re0
            def _norm(s):
                return _re0.sub(r"[^a-z0-9]", "", (s or "").lower())
            lod_alias = db.CREW_ALIASES.get("lod", "Legion of Death")
            lod_key = _norm(lod_alias) or _norm("Legion of Death")
            trustees = db.get_trustees()
            in_crew = [t for t in trustees if _norm(t.get("crew", "")) == lod_key]
            LOD = "Legion of Death"
            chosen = None
            if account:
                chosen = next((t for t in trustees
                               if t.get("name", "").lower() == account.lower()), None)
            if not chosen:
                chosen = in_crew[0] if in_crew else None
            if not chosen:
                await ctx.send(f"❌ No trustee found in **{LOD}**. "
                               f"({len(trustees)} trustees, {len(in_crew)} in crew.)")
                return
            suid = str(chosen.get("suid") or "")
            name = chosen.get("name", suid)
            if not suid:
                await ctx.send(f"❌ Trustee **{name}** has no suid stored.")
                return

            # Act as this trustee character via the bot's OWN session (get_as sets the
            # ow_userid cookie = 'play as'). No stored SSID touched.
            html = await self.session.get_as("crew_capstatus", int(suid))
            fp = os.path.join(os.path.expanduser("~"), "crew_capstatus.html")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(html)

            import re as _re
            title = _re.search(r"<title>([^<]*)</title>", html, _re.I)
            title_txt = title.group(1).strip() if title else "(no title)"
            is_caps = bool(_re.search(r"cap\s*status|god\s*cap|caps?\s*(used|remaining|left)", html, _re.I))
            hints = []
            for pat in [r".{0,25}\d{1,2}:\d{2}(:\d{2})?.{0,25}",
                        r".{0,25}\d+\s*(day|hour|hr|min).{0,25}",
                        r".{0,35}(expire|reset|remaining|available|free|cap).{0,35}"]:
                hints += [m.group(0).strip() for m in _re.finditer(pat, html, _re.I)][:4]
            hint_txt = "\n".join(dict.fromkeys(hints))[:1400] if hints else "(no obvious expiry text)"
            await ctx.send(
                f"**caps-debug** as trustee **{name}** (suid {suid}):\n"
                f"{'✅ CAPS PAGE' if is_caps else '⚠️ not the caps page'} — "
                f"`crew_capstatus` → `~/crew_capstatus.html` ({len(html):,} bytes)\n"
                f"Title: `{title_txt}`\n"
                f"Hints:\n```\n{hint_txt}\n```"
            )
        except Exception as e:
            await ctx.send(f"❌ caps-debug failed: {e}")

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

        # Exclusions are now runtime-editable (db), not a hardcoded constant.
        excl = db.get_top_exclusions()
        excl_names, excl_subs = excl["names"], excl["substrings"]
        filtered = [
            t for t in self.trustees
            if t.get("level", 0) >= 80
            and t["name"].lower() not in excl_names
            and not any(s in t["name"].lower() for s in excl_subs)
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

    @commands.command(name="top-exclude")
    async def top_exclude(self, ctx, action: str = None, *, value: str = None):
        """Manage the top-all exclusion list (bot/alt accounts hidden from rankings).
        Usage:
          !top-exclude list                 — show all excluded names/substrings
          !top-exclude add <name>           — exclude an exact account name
          !top-exclude add-sub <substring>  — exclude any name containing <substring>
          !top-exclude remove <name/sub>    — remove an entry (checks both)
        Replaces the old hardcoded list — fully runtime-editable now."""
        action = (action or "list").lower()
        if action == "list":
            excl = db.get_top_exclusions()
            names = sorted(excl["names"])
            subs = sorted(excl["substrings"])
            embed = es.info_embed(
                "🚫 Top-All Exclusions",
                description=f"**{len(names)}** names · **{len(subs)}** substrings"
            )
            if names:
                # chunk to stay under field limits
                block, part = "", 1
                for n in names:
                    if len(block) + len(n) + 2 > 1000:
                        embed.add_field(name=f"Names ({part})", value=block, inline=False)
                        block, part = "", part + 1
                    block += f"{n}, "
                if block:
                    embed.add_field(name=f"Names ({part})" if part > 1 else "Names",
                                    value=block.rstrip(", "), inline=False)
            if subs:
                embed.add_field(name="Substrings", value=", ".join(subs), inline=False)
            await ctx.send(embed=embed)
            return
        if not value:
            await ctx.send("Give a value, e.g. `!top-exclude add SomeAlt`.")
            return
        if action == "add":
            ok = db.add_top_exclusion(value, is_substring=False)
            await ctx.send(f"✅ Excluded name **{value}**." if ok
                           else f"**{value}** is already excluded.")
        elif action in ("add-sub", "addsub", "add_substring"):
            ok = db.add_top_exclusion(value, is_substring=True)
            await ctx.send(f"✅ Excluded any name containing **{value}**." if ok
                           else f"Substring **{value}** is already excluded.")
        elif action in ("remove", "rm", "delete"):
            ok = db.remove_top_exclusion(value)
            await ctx.send(f"✅ Removed **{value}** from exclusions." if ok
                           else f"**{value}** wasn't in the exclusion list.")
        else:
            await ctx.send(f"Unknown action `{action}`. Use: list, add, add-sub, remove.")

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

        result = [t for t in trustees if t.get("level", 0) >= min_level]
        # Dedup by name: a character that exists on BOTH servers (Sigil + Torax) has
        # a trustee entry per server, so an unscoped name filter matches both copies
        # → the character was appearing twice in !top/!bottom rankings. Keep the first
        # occurrence per name. (Ranking is by character name/stats, not per-server, so
        # one entry per name is correct here.)
        seen = set()
        deduped = []
        for t in result:
            key = t["name"].lower()
            if key not in seen:
                seen.add(key)
                deduped.append(t)
        return deduped

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

    def _resolve_crew_name(self, name: str):
        """If `name` refers to a crew (by alias like 'gv'/'lod' or by matching a crew
        that trustees belong to), return (stored_crew_name, a_trustee_in_it). Else
        (None, None). Used to switch !pcaps into whole-crew mode."""
        import re
        def _norm(s):
            return re.sub(r"[^a-z0-9]", "", (s or "").lower())
        key = _norm(name)
        trustees = db.get_trustees()
        # Build the set of crews trustees are actually in (normalised → display name)
        crew_by_norm = {}
        for t in trustees:
            c = t.get("crew", "")
            if c:
                crew_by_norm.setdefault(_norm(c), c)
        # 1) alias match (gv → Gorilla Voltage), then normalise the alias target
        alias_target = db.CREW_ALIASES.get(name.lower())
        if alias_target and _norm(alias_target) in crew_by_norm:
            key = _norm(alias_target)
        # 2) direct crew-name match
        if key in crew_by_norm:
            stored = crew_by_norm[key]
            member = next((t for t in trustees if _norm(t.get("crew", "")) == key
                           and t.get("suid")), None)
            if member:
                return stored, member
        return None, None

    async def _pcaps_whole_crew(self, ctx, crew_name: str, member: dict):
        """Show Character / Caps / Next Cap for every member of a crew, from ONE
        crew_capstatus fetch (acting as a trustee in that crew). Handles big crews
        (up to ~200) — caps-focused columns only, no per-account fetching."""
        from outwar.scraper import parse_crew_cap_status
        from outwar.table_image import render_crew_caps_table
        import discord

        suid = member.get("suid")
        msg = await ctx.send(f"⏳ Fetching crew caps for **{crew_name}**…")
        try:
            html = await self.session.get_as("crew_capstatus", int(suid))
            caps = parse_crew_cap_status(html)
        except Exception as e:
            await msg.edit(content=f"❌ Couldn't fetch crew caps: {e}")
            return
        if not caps:
            await msg.edit(content=f"❌ No crew cap data found for **{crew_name}** "
                                   f"(is the account still in that crew?).")
            return

        # Build rows: name, used/max, next-cap (only when used > 0)
        rows = []
        for name, d in caps.items():
            used, mx = d.get("used", 0), d.get("max", 0)
            nxt = d.get("next_expiry") if (d.get("next_expiry") and used > 0) else "—"
            rows.append({"name": name, "used": used, "max": mx, "next_cap": nxt})
        # Sort: fully capped first (used==max), then by most-used
        rows.sort(key=lambda r: (-(r["used"] == r["max"] and r["max"] > 0), -r["used"]))

        buf = render_crew_caps_table(crew_name, rows)
        await msg.delete()
        await ctx.send(file=discord.File(buf, filename="crew_cap_status.png"))

    @commands.command(name="pcaps")
    async def prime_caps(self, ctx, *, group: str):
        """Cap status for a group, OR a whole crew. Usage: !pcaps <group> | !pcaps <crew>
        (crew shows Character/Caps/Next Cap for every member in one fetch)."""
        from outwar.scraper import parse_god_cap, parse_character_stats_profile, parse_rage
        from outwar.table_image import render_caps_table
        from yarl import URL
        import discord

        # Whole-crew mode: if the name resolves to a crew, show its full member caps
        # from a single crew_capstatus fetch (no per-account scraping).
        crew_name, crew_member = self._resolve_crew_name(group)
        if crew_name and crew_member:
            await self._pcaps_whole_crew(ctx, crew_name, crew_member)
            return

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
                    "faction_name":  faction,   # raw name for totals
                    "faction_level": flvl,       # raw level for totals
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

        # One extra fetch: the crew's cap-status page has everyone's NEXT-cap time in
        # the "Crew Member Status" table. Fetch it ONCE (as a trustee in the crew) and
        # cross-reference by account name — far cheaper than per-account, and gives the
        # "Next Cap" column. Uses the bot's own session (get_as), no stored SSID.
        try:
            from outwar.scraper import parse_crew_cap_status
            crew_suid = next((t.get("suid") for t in trustees if t.get("suid")), None)
            if crew_suid:
                cap_html = await self.session.get_as("crew_capstatus", int(crew_suid))
                crew_caps = parse_crew_cap_status(cap_html)
                for r in results:
                    entry = crew_caps.get(r["name"])
                    # Show the next-cap time whenever the crew table has one for this
                    # account. The "Next Expiry" is when their soonest used cap regens —
                    # meaningful whether they're fully capped or just partially used
                    # (e.g. 7/10 used still has a next-cap time). Only "—" when they have
                    # no used caps at all, or aren't in the table.
                    if entry and entry.get("next_expiry") and entry.get("used", 0) > 0:
                        r["next_cap"] = entry["next_expiry"]
                    else:
                        r["next_cap"] = "—"
        except Exception:
            for r in results:
                r.setdefault("next_cap", "—")

        results.sort(key=lambda x: (x["cur"] <= 0 if x["max"] else True, -x["cur"]))

        buf = render_caps_table(group, results)
        await msg.delete()
        await ctx.send(file=discord.File(buf, filename="cap_status.png"))

    @commands.command(name="profile")
    async def profile(self, ctx, *, name: str):
        """
        Show a character's full profile card (stats + equipment paperdoll + crests),
        faithful to Outwar's own profile layout. Usage: !profile <outwar name>
        """
        name = name.strip()
        if not name:
            await ctx.send("Usage: `!profile <outwar name>`")
            return

        msg = await ctx.send(f"🔎 Building profile for **{name}**…")
        try:
            import io as _io
            import asyncio as _asyncio
            import aiohttp as _aiohttp
            from PIL import Image as _PILImage
            from outwar.scraper import (
                parse_full_profile, parse_equipment_paperdoll, parse_skill_crests,
            )
            from outwar.table_image import render_profile_full, render_profile, pick_item_frame

            # 1. Fetch the profile page (items live on it — no separate endpoint).
            prof_html = await self.session.get(f"profile.php?transnick={name}&server=1")
            profile = parse_full_profile(prof_html)
            if not profile.get("stats") and not profile.get("name"):
                await msg.edit(content=f"❌ Couldn't find a profile for **{name}** "
                                       f"(check the spelling, or the character may not exist).")
                return
            if not profile.get("name"):
                profile["name"] = name

            paperdoll = parse_equipment_paperdoll(prof_html)
            crests    = parse_skill_crests(prof_html)

            # 2. Equipped augments: fetch each item's item_rollover.php to read its
            #    augment slots (confirmed via OWMod: augments live in the rollover, not
            #    the profile page). Only real equipped items (numeric id) have rollovers.
            from outwar.scraper import parse_item_augments
            SIGIL = "https://sigil.outwar.com"
            augments = []
            item_ids = [it["id"] for it in paperdoll["items"]
                        if it.get("id") and "_" not in it["id"]]
            if item_ids:
                rsem = _asyncio.Semaphore(6)  # bounded — one rollover per item

                async def _roll(http, iid):
                    async with rsem:
                        try:
                            url = f"{SIGIL}/item_rollover.php?id={iid}"
                            async with http.get(url, timeout=_aiohttp.ClientTimeout(total=10)) as r:
                                if r.status == 200:
                                    return iid, await r.text()
                        except Exception:
                            return iid, None
                    return iid, None

                async with _aiohttp.ClientSession() as http:
                    rolls = await _asyncio.gather(*[_roll(http, i) for i in item_ids])
                # Preserve equip order, keep only FILLED augments for the grid.
                roll_map = {iid: html for iid, html in rolls if html}
                for iid in item_ids:
                    if iid in roll_map:
                        for slot in parse_item_augments(roll_map[iid]):
                            if slot.get("filled") and slot.get("img"):
                                augments.append({"img": slot["img"], "filled": True})

            # 3. Collect every UNIQUE image URL (dedup — many gems share an image),
            #    then download them concurrently but BOUNDED (rate-limit-safe).
            urls = set()
            for it in paperdoll["items"]:
                if it.get("img"):
                    urls.add(it["img"])
            for cr in crests:
                if cr.get("img"):
                    urls.add(cr["img"])
            for aug in augments:
                if aug.get("img"):
                    urls.add(aug["img"])

            item_icons = {}
            if urls:
                sem = _asyncio.Semaphore(8)  # bounded concurrency (won't flood)

                async def _dl(http, url):
                    full = url
                    if full.startswith("/"):
                        full = SIGIL + full
                    elif not full.startswith("http"):
                        full = f"{SIGIL}/{full}"
                    async with sem:
                        try:
                            async with http.get(full, timeout=_aiohttp.ClientTimeout(total=10)) as r:
                                if r.status == 200:
                                    raw = _PILImage.open(_io.BytesIO(await r.read()))
                                    # Item/aug icons are often animated glow GIFs — pick
                                    # the fully-lit frame, not the dim frame 0.
                                    return url, pick_item_frame(raw)
                        except Exception:
                            return url, None
                    return url, None

                async with _aiohttp.ClientSession() as http:
                    results = await _asyncio.gather(*[_dl(http, u) for u in urls])
                for url, im in results:
                    if im is not None:
                        item_icons[url] = im

            # 3b. Preferred-Player crown — fetch Outwar's own ProPP.png so it matches
            #     the game exactly. Only when the profile is flagged PP; a failed fetch
            #     just means no crown (never blocks the card).
            crown_icon = None
            if profile.get("is_preferred"):
                try:
                    async with _aiohttp.ClientSession() as http:
                        cu = f"{SIGIL}/images/profile/ProPP.png"
                        async with http.get(cu, timeout=_aiohttp.ClientTimeout(total=10)) as r:
                            if r.status == 200:
                                crown_icon = _PILImage.open(_io.BytesIO(await r.read()))
                except Exception:
                    crown_icon = None

            # 4. Render — full paperdoll (+ augments) if we parsed items, else stat card.
            if paperdoll["items"]:
                buf = render_profile_full(profile, paperdoll, crests, item_icons,
                                          augments, crown_icon=crown_icon)
            else:
                buf = render_profile(profile)

            url = f"{SIGIL}/profile?transnick={name}&serverid=1"
            await msg.delete()
            # Clean Bloop-style presentation: the card image in an embed whose TITLE
            # is the clickable "Open in Browser" link — no separate URL preview unfurl.
            embed = discord.Embed(
                title="🔗 Open in Browser",
                url=url,
                colour=discord.Colour.dark_theme(),
            )
            embed.set_image(url="attachment://profile.png")
            await ctx.send(embed=embed,
                           file=discord.File(buf, filename="profile.png"))
        except Exception as e:
            logger.warning("MISC", f"[profile] error for {name}: {e}")
            await msg.edit(content=f"⚠️ Error building profile for **{name}**: `{e}`")

    @commands.command(name="profile-raw")
    async def profile_raw(self, ctx, *, name: str):
        """
        Diagnostic: dump the profile-header HTML region so we can see how the
        Preferred-Player crown and the profile picture are represented, then build
        parsers from the real structure. Usage: !profile-raw <outwar name>
        """
        name = name.strip()
        if not name:
            await ctx.send("Usage: `!profile-raw <outwar name>`")
            return
        await ctx.send(f"🔬 Dumping profile HTML for **{name}**…")
        try:
            import re as _re
            html = await self.session.get(f"profile.php?transnick={name}&server=1")

            # 1. Any <img> whose src/context suggests a profile picture or crown/badge.
            imgs = _re.findall(r"<img[^>]+>", html, _re.IGNORECASE)
            interesting = [t for t in imgs if _re.search(
                r"prefer|crown|premium|badge|avatar|profile|pic|myimages|upload|custom",
                t, _re.IGNORECASE)]

            # 2. The header block — grab a window around the character name so we can
            #    see the crown/preferred markup that sits next to it in Bloop's card.
            head = ""
            m = _re.search(_re.escape(name), html, _re.IGNORECASE)
            if m:
                s = max(0, m.start() - 400)
                head = html[s:m.end() + 400]

            def _chunk(label, body):
                body = body.strip() or "(none found)"
                for i in range(0, len(body), 1800):
                    part = body[i:i + 1800]
                    return f"**{label}:**\n```html\n{part}\n```"

            await ctx.send(_chunk(f"IMG tags matching profile/crown/avatar ({len(interesting)})",
                                  "\n".join(interesting[:15])))
            if head:
                await ctx.send(_chunk("HTML around the character name (header region)", head))
            else:
                await ctx.send("Couldn't locate the name in the HTML to window the header.")
        except Exception as e:
            await ctx.send(f"⚠️ profile-raw error: `{e}`")

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
