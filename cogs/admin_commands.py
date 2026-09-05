"""
admin_commands.py

!scan-trustees  — Scrapes the bot account's trustee page on Outwar, fetches each
                  character's crew/level/rage, and saves everything to trustees.json.

!autorank <crew> <stat> <size>
                — Fetches all characters in a crew, ranks them by the chosen stat
                  (ele, chaos, power), splits them into groups of <size>, and saves
                  them as groups named <CREW>1, <CREW>2, etc.
                  Replaces any existing groups with the same prefix.

                  Example: !autorank to ele 10
                  Creates groups TO1 (ranks 1-10), TO2 (ranks 11-20), etc.
"""

import asyncio
import io
import discord
from discord.ext import commands
from outwar import database as db
from cogs import embed_style as es
from outwar.scraper import (
    parse_trustee_list,
    parse_character_crew_and_level,
    parse_character_profile,
)
from outwar import logger

BASE_URL = "https://sigil.outwar.com"

STAT_LABELS = {
    "ele":    "Elemental Damage",
    "chaos":  "Chaos Damage",
    "power":  "Power",
}


class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._guard_running   = False
        self._guard_trustees  = []

    @property
    def session(self):
        return self.bot.outwar

    # ------------------------------------------------------------------
    # !trustee group — unifies scan/update/check/remove/clear. The classic
    # commands live across two cogs, so subcommands redispatch through the
    # bot's router (rewrite message → process_commands), which keeps each
    # classic command's own auth + logic intact regardless of which cog it's in.
    # ------------------------------------------------------------------

    @commands.group(name="trustee", aliases=["trustees"], invoke_without_command=True)
    async def trustee(self, ctx):
        """Trustee hub. Use !trustee <action> — scan / update / check / remove / clear."""
        await ctx.send(embed=es.info_embed(
            "👥 Trustee Commands",
            description=(
                "`!trustee scan` — scan all crews for trustee access\n"
                "`!trustee update` — refresh trustee levels/rage\n"
                "`!trustee check` — check which trustees are reachable\n"
                "`!trustee remove [crew]` — remove trustees (optionally one crew)\n"
                "`!trustee clear` — clear the whole trustee database\n\n"
                "_Classic names (`!scan-trustees`, `!update-trustees`…) still work._"
            )))

    async def _trustee_redispatch(self, ctx, target: str, rest: str = ""):
        ctx.message.content = f"{ctx.prefix}{target} {rest}".rstrip()
        await self.bot.process_commands(ctx.message)

    @trustee.command(name="scan")
    async def trustee_scan(self, ctx):
        """Scan all crews for trustee access. Same as !scan-trustees."""
        await self._trustee_redispatch(ctx, "scan-trustees")

    @trustee.command(name="update")
    async def trustee_update(self, ctx):
        """Refresh trustee levels/rage. Same as !update-trustees."""
        await self._trustee_redispatch(ctx, "update-trustees")

    @trustee.command(name="check")
    async def trustee_check(self, ctx):
        """Check which trustees are reachable. Same as !check-trustees."""
        await self._trustee_redispatch(ctx, "check-trustees")

    @trustee.command(name="remove")
    async def trustee_remove(self, ctx, *, crew_name: str = ""):
        """Remove trustees (optionally one crew). Same as !remove-trustees."""
        await self._trustee_redispatch(ctx, "remove-trustees", crew_name)

    @trustee.command(name="clear")
    async def trustee_clear(self, ctx):
        """Clear the whole trustee database. Same as !clear-trustees."""
        await self._trustee_redispatch(ctx, "clear-trustees")


    @commands.command(name="guard-start")
    async def guard_start(self, ctx):
        """Start background task keeping On Guard and Street Smarts active for ALL trustees."""
        if self._guard_running:
            await ctx.send("⚠️ Guard task is already running. Use `!guard-stop` first.")
            return

        trustees = db.get_trustees()
        if not trustees:
            await ctx.send("⚠️ No trustees found.")
            return

        self._guard_running  = True
        self._guard_trustees = trustees
        await ctx.send(f"🛡️ Guarding Started for **{len(trustees)}** accounts")
        asyncio.create_task(self._guard_loop())

    @commands.command(name="guard-stop")
    async def guard_stop(self, ctx):
        """Stop the guard background task."""
        if not self._guard_running:
            await ctx.send("⚠️ Guard task is not running.")
            return
        self._guard_running = False
        await ctx.send("🛡️ Guarding Stopped")

    async def _guard_loop(self):
        """
        Background loop — casts On Guard and Street Smarts across all trustees.
        Each skill runs on its own independent precise-sleep timer, but they share
        a cast lock so they never write to the cookie jar simultaneously.
        On Guard: 270 min cooldown, Street Smarts: 1296 min cooldown.
        """
        from yarl import URL as _URL
        SIGIL_URL = _URL("https://sigil.outwar.com")
        cast_lock = asyncio.Lock()  # prevents On Guard and Street Smarts casting simultaneously

        async def _cast_all(skill_id: int, name: str):
            """Cast skill_id on all trustees concurrently using post_as — no shared cookie mutation."""
            sem = asyncio.Semaphore(10)
            cast_count = 0
            failed     = 0

            async def _cast_one(t):
                nonlocal cast_count, failed
                suid = t.get("suid")
                if not suid:
                    return
                async with sem:
                    try:
                        resp = await self.session.post_as("cast_skills.php", {
                            "castskillid": str(skill_id),
                            "cast":        "Cast Skill",
                        }, suid)
                        if "You just cast" in resp or "already cast" in resp.lower():
                            cast_count += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1

            async with cast_lock:
                await asyncio.gather(*[_cast_one(t) for t in self._guard_trustees])

            logger.info(
                "GUARD",
                f"{name} cast on {cast_count}/{len(self._guard_trustees)} accounts"
                + (f" ({failed} failed)" if failed else "")
            )

        async def _cast_and_recast(skill_id: int, cooldown_mins: int, name: str):
            while self._guard_running:
                await _cast_all(skill_id, name)
                # Sleep precisely until cooldown expires, chunked for !guard-stop responsiveness
                total   = cooldown_mins * 60
                elapsed = 0
                while elapsed < total and self._guard_running:
                    await asyncio.sleep(min(300, total - elapsed))
                    elapsed += 300

        await asyncio.gather(
            _cast_and_recast(7,  270,  "On Guard"),
            _cast_and_recast(25, 1296, "Street Smarts"),
        )

    # ------------------------------------------------------------------
    # !scan-trustees
    # ------------------------------------------------------------------

    async def _scan_one_server(self, ctx, server_id: int):
        """Scan + enrich + save trustees for ONE server. Returns the enriched count
        (or None if nothing found). Enrichment is server-aware: Sigil uses the proven
        cookie path; non-Sigil uses the cookieless per-request path (sess_get)."""
        from outwar.servers import name_for, host_for as _host_for
        server_name = name_for(server_id)
        await ctx.send(f"🔍 Scanning **{server_name}** trustee list from Outwar...")

        # Trustee list via the safe per-request method (serverid= + per-server suid),
        # NOT ac_serverid (which flips the account server-side). Proven for both
        # servers (Sigil ~751, Torax ~535).
        html = await self.session.get_server("myaccount", server_id)
        raw_trustees = parse_trustee_list(html)

        if not raw_trustees:
            await ctx.send(
                f"⚠️ No trustees found on the **{server_name}** myaccount page. "
                "Make sure characters are trusteed to the bot account on that server."
            )
            return None

        await ctx.send(
            f"Found **{len(raw_trustees)}** {server_name} trustees. "
            f"Fetching crew/level info... (this may take a moment)"
        )

        semaphore = asyncio.Semaphore(10)
        _host = _host_for(server_id)
        is_sigil = (int(server_id) == 1)
        ssid = getattr(self.session, "session_id", None)

        async def _enrich(trustee: dict) -> dict:
            async with semaphore:
                try:
                    if is_sigil:
                        # Sigil: proven cookie-session ow_userid path.
                        from yarl import URL as _URL
                        HOST_URL = _URL(_host)
                        self.session._session.cookie_jar.update_cookies(
                            {"ow_userid": str(trustee["suid"])}, response_url=HOST_URL
                        )
                        profile_html = await self.session.get("profile")
                        self.session._session.cookie_jar.update_cookies(
                            {"ow_userid": str(self.session.user_id)}, response_url=HOST_URL
                        )
                    else:
                        # Torax / non-Sigil: cookieless per-request path with the
                        # trustee's own suid on that server (proven 2026-08-13).
                        from outwar import ssid_store
                        profile_html = await ssid_store.sess_get(
                            "profile", ssid, int(trustee["suid"]), server_id
                        )
                    crew, level, rage, crew_id = parse_character_crew_and_level(profile_html)
                    trustee["crew"] = crew
                    trustee["rage"] = rage
                    if crew_id is not None:
                        trustee["crew_id"] = crew_id
                    if level > 0:
                        trustee["level"] = level
                except Exception as e:
                    logger.warning("ADMIN", f"Error enriching {trustee['name']} "
                                            f"(server {server_id}): {e}")
            return trustee

        enriched = await asyncio.gather(*[_enrich(t) for t in raw_trustees])
        enriched = [t for t in enriched if t]

        # Save ONLY this server's trustees — preserves other servers' lists.
        db.save_trustees_for_server(server_id, enriched)

        # Per-server summary embed
        crews: dict[str, int] = {}
        for t in enriched:
            crew = t.get("crew") or "Unknown"
            crews[crew] = crews.get(crew, 0) + 1

        embed = es.report_embed(
            f"{es.ICON_REPORT} {server_name} Trustees Scanned",
            description=f"**{len(enriched)}** {server_name} trustees saved "
                        f"(other servers' trustees preserved)."
        )
        crew_lines = "\n".join(
            f"{name}: **{count}**"
            for name, count in sorted(crews.items(), key=lambda x: -x[1])
        )
        if crew_lines:
            embed.add_field(name="Characters per Crew", value=crew_lines[:1024], inline=False)
        no_crew = [t["name"] for t in enriched if not t.get("crew")]
        if no_crew:
            embed.add_field(
                name=f"⚠️ No Crew Detected ({len(no_crew)})",
                value=" ".join(no_crew)[:1024],
                inline=False
            )
        await ctx.send(embed=embed)
        return len(enriched)

    @commands.command(name="scan-trustees")
    async def scan_trustees(self, ctx, server: str = None):
        """
        Scrape the bot account's trustee list and update trustees.json per server.
        Each server's trustees are enriched with crew/level/rage and saved separately.

          !scan-trustees          → scan ALL active servers (Sigil + Torax)
          !scan-trustees sigil    → scan just Sigil
          !scan-trustees torax    → scan just Torax
        (With no arg in a server-specific channel, still scans all active servers.)
        """
        from outwar.servers import name_for

        # Resolve which servers to scan.
        if server:
            key = server.strip().lower()
            if key in ("sigil", "1"):
                targets = [1]
            elif key in ("torax", "2"):
                targets = [2]
            elif key in ("all", "both"):
                targets = db.get_active_servers()
            else:
                await ctx.send("Usage: `!scan-trustees [sigil|torax|all]`")
                return
        else:
            # No arg → scan all active servers.
            targets = db.get_active_servers()

        if len(targets) > 1:
            names = ", ".join(name_for(s) for s in targets)
            await ctx.send(f"🔄 Scanning trustees for **{len(targets)}** servers: {names}")

        results = {}
        for sid in targets:
            count = await self._scan_one_server(ctx, sid)
            if count is not None:
                results[sid] = count

        # Combined summary when more than one server scanned.
        if len(targets) > 1 and results:
            total = sum(results.values())
            lines = "\n".join(f"• {name_for(s)}: **{c}** trustees" for s, c in results.items())
            embed = es.report_embed(
                f"{es.ICON_REPORT} All Servers Scanned",
                description=f"Total **{total}** trustees saved across "
                            f"{len(results)} server(s):\n{lines}"
            )
            await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # !remove-trustees  /  !clear-trustees   (destructive — need confirm)
    # ------------------------------------------------------------------

    @commands.command(name="remove-trustees")
    async def remove_trustees(self, ctx, *, crew_name: str = None):
        """
        Remove trustees the bot has assigned. Usage:
          !remove-trustees <crew>   → remove all trustees in that crew
          !remove-trustees          → (no crew) shows how to clear ALL
        Destructive: asks for confirmation before saving.
        """
        if not crew_name:
            await ctx.send(
                "Specify a crew to remove its trustees: `!remove-trustees <crew>`.\n"
                "To wipe **every** trustee, use `!clear-trustees`."
            )
            return

        all_trustees = db.get_trustees()
        if not all_trustees:
            await ctx.send("There are no trustees assigned to the bot.")
            return

        # Resolve the crew (accept short name or full name), same as autorank does.
        crew_full = db.normalize_crew(crew_name)
        crew = db.get_crew(crew_name)
        if crew:
            crew_full = crew["full_name"]

        to_remove = [t for t in all_trustees if t.get("crew") == crew_full]
        if not to_remove:
            await ctx.send(
                f"No trustees found in crew **{crew_full}**. "
                f"(Names are matched on the stored `crew` field — check `!scan-trustees` output.)"
            )
            return

        # Confirmation — destructive.
        await ctx.send(
            f"⚠️ This will remove **{len(to_remove)}** trustee(s) from crew "
            f"**{crew_full}**, leaving **{len(all_trustees) - len(to_remove)}**.\n"
            f"Reply **yes** within 30s to confirm."
        )

        def _check(m):
            return m.author == ctx.author and m.channel == ctx.channel and \
                m.content.strip().lower() in ("yes", "y", "no", "n")

        try:
            reply = await self.bot.wait_for("message", check=_check, timeout=30.0)
        except Exception:
            await ctx.send("Timed out — no trustees removed.")
            return
        if reply.content.strip().lower() in ("no", "n"):
            await ctx.send("Cancelled — no trustees removed.")
            return

        remaining = [t for t in all_trustees if t.get("crew") != crew_full]
        db.save_trustees(remaining)
        await ctx.send(
            f"✅ Removed **{len(to_remove)}** trustee(s) from **{crew_full}**. "
            f"**{len(remaining)}** trustee(s) remain."
        )

    @commands.command(name="clear-trustees")
    async def clear_trustees(self, ctx):
        """Remove ALL trustees assigned to the bot. Destructive — asks to confirm."""
        all_trustees = db.get_trustees()
        if not all_trustees:
            await ctx.send("There are no trustees assigned to the bot.")
            return

        await ctx.send(
            f"⚠️ This will remove **ALL {len(all_trustees)}** trustee(s) — the bot will "
            f"have no accounts assigned until you `!scan-trustees` again.\n"
            f"Reply **yes** within 30s to confirm."
        )

        def _check(m):
            return m.author == ctx.author and m.channel == ctx.channel and \
                m.content.strip().lower() in ("yes", "y", "no", "n")

        try:
            reply = await self.bot.wait_for("message", check=_check, timeout=30.0)
        except Exception:
            await ctx.send("Timed out — no trustees removed.")
            return
        if reply.content.strip().lower() in ("no", "n"):
            await ctx.send("Cancelled — no trustees removed.")
            return

        db.save_trustees([])
        await ctx.send(
            f"✅ Cleared all **{len(all_trustees)}** trustee(s). "
            f"Run `!scan-trustees` to reassign."
        )

    # ------------------------------------------------------------------
    # !autorank
    # ------------------------------------------------------------------

    @commands.command(name="autorank")
    async def autorank(self, ctx, crew_name: str, stat: str, size: int = 10):
        """
        Rank all characters in a crew by a stat and split into groups.

        Usage: !autorank <crew> <stat> <size>
        Stats: ele, chaos, power
        Example: !autorank to ele 10
        """
        stat = stat.lower()
        if stat not in STAT_LABELS:
            await ctx.send(
                f"Unknown stat `{stat}`. Use one of: {', '.join(STAT_LABELS.keys())}"
            )
            return

        if size < 1:
            await ctx.send("Group size must be at least 1.")
            return

        # Resolve crew
        crew_full = db.normalize_crew(crew_name)
        crew = db.get_crew(crew_name)
        if crew:
            crew_full = crew["full_name"]

        trustees = db.get_trustees_by_crew(crew_full)

        if not trustees:
            # Fall back: try treating crew_name as a group
            rga_group = db.get_group(crew_name)
            if rga_group:
                names = db.group_to_list(rga_group)
                all_trustees = db.get_trustees()
                trustees = [t for t in all_trustees if t["name"] in names]

        if not trustees:
            await ctx.send(
                f"No trustees found for `{crew_name}`. "
                f"Run `!scan-trustees` first, or check the crew name."
            )
            return

        stat_label = STAT_LABELS[stat]
        await ctx.send(
            f"📊 Fetching **{stat_label}** for **{len(trustees)}** characters in **{crew_full}**..."
        )

        # Fetch character stats concurrently
        semaphore = asyncio.Semaphore(10)

        async def _fetch_stat(trustee: dict):
            async with semaphore:
                try:
                    html = await self.session.get(
                        f"profile.php?transnick={trustee['name']}&server=1"
                    )
                    char = parse_character_profile(html, trustee["name"])
                    if char:
                        return {
                            "name": trustee["name"],
                            "value": getattr(char, stat if stat != "ele" else "elemental", 0)
                        }
                except Exception as e:
                    logger.warning("ADMIN", f"Error fetching {trustee['name']}: {e}")
                return None

        results = await asyncio.gather(*[_fetch_stat(t) for t in trustees])
        results = [r for r in results if r and r["value"] > 0]

        if not results:
            await ctx.send(f"No characters with {stat_label} data found.")
            return

        # Sort descending (rank 1 = highest)
        results.sort(key=lambda x: x["value"], reverse=True)

        # Delete existing autorank groups for this crew+stat
        # Clean up old format groups (LOD_ELE_1 etc) and new format (LOD1 etc)
        old_prefix = f"{crew_name.upper()}_{stat.upper()}_"
        new_prefix = f"{crew_name.upper()}"
        db.delete_groups_by_prefix(old_prefix)
        deleted = db.delete_groups_by_prefix(new_prefix)

        # Split into chunks of <size>
        chunks = [results[i:i + size] for i in range(0, len(results), size)]
        new_groups = []
        for i, chunk in enumerate(chunks, 1):
            group_name = f"{crew_name.upper()}{i}"
            character_names = " ".join(c["name"] for c in chunk)
            new_groups.append({"name": group_name, "character_names": character_names})

        added = db.bulk_add_groups(new_groups)

        # Build summary embed
        embed = es.report_embed(
            f"{es.ICON_REPORT} Autorank Complete — {crew_full}",
            description=(
                f"Ranked **{len(results)}** characters by **{stat_label}**\n"
                f"Split into **{len(chunks)}** groups of up to **{size}**\n"
                f"Replaced **{deleted}** old groups"
            )
        )

        # Show each group with rank range and stat range
        for i, (group, chunk) in enumerate(zip(new_groups, chunks), 1):
            top_val = chunk[0]["value"]
            bot_val = chunk[-1]["value"]
            rank_start = (i - 1) * size + 1
            rank_end = rank_start + len(chunk) - 1
            names_preview = ", ".join(c["name"] for c in chunk[:5])
            if len(chunk) > 5:
                names_preview += f" +{len(chunk)-5} more"
            embed.add_field(
                name=f"{group['name']} (Ranks {rank_start}–{rank_end})",
                value=f"{stat_label}: {bot_val:,} – {top_val:,}\n{names_preview}",
                inline=False
            )

            # Discord embeds max 25 fields
            if i == 25:
                embed.set_footer(text=f"... and {len(chunks)-25} more groups")
                break

        await ctx.send(embed=embed)
        await ctx.send(
            f"Groups saved. Use `!groups {new_groups[0]['name']}` to inspect one, "
            f"or `!cast-ss {new_groups[0]['name']}` to skill it."
        )

    @commands.command(name="standings", aliases=["rankings"])
    async def standings(self, ctx, *, crew: str = None):
        """Show our crew(s)' current global rankings (power / elemental / chaos).
        Usage: !standings [crew]"""
        from outwar.scraper import parse_crew_rankings

        cats = [("crew_power", "Power"),
                ("crew_elepower", "Elemental"),
                ("crew_chaos", "Chaos")]

        def _abbr(n):
            n = int(n)
            for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
                if n >= div:
                    return f"{n/div:.2f}{suf}"
            return str(n)

        status = await ctx.send("🏆 Fetching crew rankings…")
        ranks = {}
        for cat, _ in cats:
            try:
                raw = await self.session.get(f"ajax/rankings.php?type={cat}")
                ranks[cat] = parse_crew_rankings(raw)
            except Exception:
                ranks[cat] = []
        if not any(ranks.values()):
            await status.edit(content="Couldn't fetch rankings right now — try again shortly.")
            return

        # our crews = distinct crew names from trustees (raw names, symbols intact)
        our = {}
        for t in db.get_trustees():
            cn = (t.get("crew") or "").strip()
            if not cn:
                continue
            e = our.setdefault(cn.lower(), {"name": cn, "id": t.get("crew_id")})
            if t.get("crew_id") is not None:
                e["id"] = t["crew_id"]

        if crew:
            full = db.normalize_crew(crew)
            targets = [v for k, v in our.items()
                       if k in (full.lower(), crew.lower()) or full.lower() in k]
            if not targets:
                targets = [{"name": full, "id": None}]
        else:
            targets = list(our.values())
            if not targets:
                await status.edit(content="No crews found — run the trustee scan first.")
                return

        def _norm(s):
            import re as _re
            return _re.sub(r"[^a-z0-9]", "", (s or "").lower())

        def _find(lst, name, cid):
            # 1) crew-id match — symbol-proof, the real key
            if cid is not None:
                for e in lst:
                    if e["id"] == cid:
                        return e
            # 2) exact name
            for e in lst:
                if e["name"].lower() == name.lower():
                    return e
            # 3) normalized name — strips symbols / mojibake / spaces so a name like
            #    "★Legion of Death★" (mangled to "â Legion of Deathâ ") still matches
            nm = _norm(name)
            if nm:
                for e in lst:
                    if _norm(e["name"]) == nm:
                        return e
            return None

        embed = es.info_embed("🏆 Crew Standings")
        for tgt in sorted(targets, key=lambda x: x["name"].lower())[:20]:
            parts, cid = [], tgt.get("id")
            for cat, label in cats:
                e = _find(ranks.get(cat, []), tgt["name"], tgt.get("id"))
                if e:
                    cid = cid or e["id"]
                    parts.append(f"**{label}** #{e['rank']} ({_abbr(e['stat'])})")
                else:
                    parts.append(f"**{label}** —")
            title = tgt["name"] + (f"  ·  id {cid}" if cid else "")
            embed.add_field(name=title, value=" · ".join(parts), inline=False)
        embed.set_footer(text="Rank #position (stat). '—' = outside the top-100 ranked list.")
        await status.edit(content=None, embed=embed)

    @commands.command(name="scan-keys", aliases=["scankeys", "scan-teleporters"])
    async def scan_keys(self, ctx, *, account: str = None):
        """Discovery scan: read an account's Keys backpack tab, fetch each item's
        rollover, and report which are teleporters + where they go.
        Usage: !scan-keys [account]   (defaults to the first trustee with a suid)"""
        from outwar.scraper import parse_backpack_items, parse_teleport_destination

        trustees = db.get_trustees()
        if account:
            t = next((x for x in trustees
                      if x.get("name", "").lower() == account.lower()), None)
            if not t:
                await ctx.send(f"Account `{account}` not found in trustees.")
                return
        else:
            t = next((x for x in trustees if x.get("suid")), None)
            if not t:
                await ctx.send("No trustees with a suid — run `!scan-trustees` first.")
                return

        suid = t.get("suid")
        status = await ctx.send(f"🔑 Scanning keys for **{t['name']}**…")

        try:
            html = await self.session.get_as("ajax/backpackcontents.php?tab=key", suid)
        except Exception as e:
            await status.edit(content=f"Failed to fetch keys tab: {e}")
            return

        items = parse_backpack_items(html)
        if not items:
            await status.edit(content=f"No keys found for **{t['name']}** "
                                      f"(tab empty or account has no keys).")
            return

        sem = asyncio.Semaphore(5)
        async def _roll(item):
            async with sem:
                try:
                    roll = await self.session.get_as(
                        f"item_rollover.php?id={item['item_id']}&data=0", suid)
                except Exception:
                    roll = ""
                dest, kind = parse_teleport_destination(roll)
                return {**item, "destination": dest, "kind": kind}
        scanned = await asyncio.gather(*[_roll(i) for i in items])

        teleporters = [s for s in scanned if s["destination"]]
        reusable    = [s for s in teleporters if s["kind"] == "reusable"]
        consumable  = [s for s in teleporters if s["kind"] == "consumable"]
        others      = [s for s in scanned if not s["destination"]]

        # Console dump — paste this back to build the destination->room mapping.
        lines = [
            f"===== [KEY-SCAN] {t['name']} (suid={suid}) — {len(items)} keys, "
            f"{len(reusable)} reusable + {len(consumable)} consumable teleporters ====="
        ]

        for s in scanned:
            if s["destination"]:
                tag = f"TELEPORT[{s['kind']}] -> {s['destination']}"
            else:
                tag = "(not a teleporter)"

            lines.append(
                f"name={s['item_name']!r} id={s['item_id']} "
                f"qty={s['quantity']} {tag}"
            )

        lines.append("===== [KEY-SCAN] end =====")

        logger.info("KEY-SCAN", "\n" + "\n".join(lines))

        # Save to KB. Bot only auto-uses reusables; consumables flagged reserved.
        # Uses the merging helper so a rescan NEVER wipes a room we've already
        # mapped by hand (this used to reset "room" to None every scan).
        _total, _new = db.merge_teleporters(teleporters)

        _reuse = sum(1 for s in teleporters if s.get("kind") == "reusable")
        _consume = sum(1 for s in teleporters if s.get("kind") == "consumable")
        embed = es.info_embed(f"🔑 Key scan — {t['name']}")
        embed.add_field(
            name="Totals",
            value=(f"{len(items)} keys · **{len(teleporters)}** teleporters "
                   f"({_reuse} reusable / {_consume} consumable by rollover text) "
                   f"· {len(others)} other"),
            inline=False)
        if teleporters:
            lines = "\n".join(f"• **{s['item_name']}** → {s['destination']}"
                              for s in teleporters[:30])
            embed.add_field(name=f"🌀 Teleporters ({len(teleporters)})",
                            value=lines[:1020], inline=False)
            if len(teleporters) > 30:
                embed.add_field(name="…", value=f"+{len(teleporters)-30} more (see console)",
                                inline=False)
        embed.set_footer(text="Full list dumped to console. Saved to teleporters.json — "
                              "room mapping pending.")
        await status.edit(content=None, embed=embed)

    @commands.command(name="teleporters", aliases=["teles", "telelist"])
    async def teleporters_cmd(self, ctx, *, filter_arg: str = ""):
        """
        Inspect the teleporter knowledge base (read-only — nothing is fired or moved).
          !teleporters            → summary: how many known, mapped vs unmapped rooms
          !teleporters mapped     → only those with a destination room worked out
          !teleporters unmapped   → those still needing a room number (the gap to fill)
          !teleporters <text>     → filter by name/destination text
        This surfaces what we know for the future teleport-routing build and, crucially,
        shows exactly which teleporters still need their destination ROOM mapped — that
        hand-mapping is what gates proximity routing.
        """
        import discord
        kb = db.get_teleporters()
        if not kb:
            await ctx.send("No teleporters known yet. Run `!scan-keys <account>` on an "
                           "account that holds teleporter items first.")
            return

        # Normalise into a list of records.
        recs = []
        for item_id, t in kb.items():
            recs.append({
                "id": item_id,
                "name": t.get("name", "?"),
                "destination": t.get("destination"),   # text description of where it goes
                "kind": t.get("kind"),                  # reusable / consumable
                "room": t.get("room"),                  # mapped room number (or None)
            })

        f = filter_arg.strip().lower()
        mapped = [r for r in recs if r["room"]]
        unmapped = [r for r in recs if not r["room"]]
        reusable = [r for r in recs if r["kind"] == "reusable"]
        consumable = [r for r in recs if r["kind"] == "consumable"]

        # --- Filtered views ---
        if f in ("mapped", "done"):
            view, title = mapped, f"Mapped teleporters ({len(mapped)})"
        elif f in ("unmapped", "todo", "pending"):
            view, title = unmapped, f"Unmapped teleporters — need room numbers ({len(unmapped)})"
        elif f:
            view = [r for r in recs if f in r["name"].lower()
                    or (r["destination"] and f in r["destination"].lower())]
            title = f"Teleporters matching '{filter_arg}' ({len(view)})"
        else:
            # --- Summary embed ---
            embed = discord.Embed(title="🌀 Teleporter knowledge base",
                                  color=discord.Color.blurple())
            embed.add_field(name="Known", value=str(len(recs)), inline=True)
            embed.add_field(name="Room mapped", value=f"{len(mapped)} ✅", inline=True)
            embed.add_field(name="Room unmapped", value=f"{len(unmapped)} ⏳", inline=True)
            embed.add_field(name="Reusable", value=str(len(reusable)), inline=True)
            embed.add_field(name="Consumable", value=str(len(consumable)), inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.description = (
                "Read-only view of what the bot knows about teleporters. "
                "`!teleporters unmapped` shows the ones still needing a destination "
                "room number — that mapping is what future teleport-routing needs.")
            if unmapped:
                sample = ", ".join(r["name"] for r in unmapped[:8])
                embed.add_field(name="Next to map (sample)",
                                value=sample + ("…" if len(unmapped) > 8 else ""),
                                inline=False)
            embed.set_footer(text="Nothing is fired or moved — inspection only.")
            await ctx.send(embed=embed)
            return

        if not view:
            await ctx.send(f"No teleporters match `{filter_arg}`.")
            return

        # --- Detail list ---
        lines = []
        for r in sorted(view, key=lambda x: (x["room"] is None, x["name"].lower())):
            room = f"room {r['room']}" if r["room"] else "room **?**"
            kind = f" · {r['kind']}" if r["kind"] else ""
            dest = f" → {r['destination']}" if r["destination"] else ""
            lines.append(f"🌀 **{r['name']}**{dest} — {room}{kind}")
        # chunk to Discord limits
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        buf = ""
        for ln in lines:
            if len(buf) + len(ln) + 1 > 1024:
                embed.add_field(name="\u200b", value=buf, inline=False)
                buf = ""
            buf += ln + "\n"
        if buf:
            embed.add_field(name="\u200b", value=buf, inline=False)
        embed.set_footer(text="Inspection only — nothing fired or moved.")
        await ctx.send(embed=embed)


    @commands.command(name="tele-fire", aliases=["telefire"])
    async def tele_fire(self, ctx, account: str, *, tele_name: str):
        """
        LIVE TEST — fire ONE teleporter key on an account and show the raw result.
        This ACTIVATES a real item (POST /ajax/backpack_action.php action=activate) and
        will move/consume it. Use to prove the mechanism on a single key before any bulk
        mapping. Usage: !tele-fire <account> <teleporter name>
          - Reads the item id from the teleporter KB (from !scan-keys)
          - Fires the activate request, shows status / redirectTo / error verbatim
          - Reads the account's resulting room and (if sane) stores it as the arrival room
        """
        import json as _json
        tele_name = tele_name.strip().strip('"')

        # Resolve the teleporter → item id from the KB.
        kb = db.get_teleporters()
        # Allow explicit id selection: "#12345"
        if tele_name.startswith("#") and tele_name[1:].strip().isdigit():
            want = tele_name[1:].strip()
            matches = [(iid, t) for iid, t in kb.items() if str(iid) == want]
        else:
            matches = [(iid, t) for iid, t in kb.items()
                       if tele_name.lower() in (t.get("name", "").lower())]
        if not matches:
            await ctx.send(f"🔎 No teleporter matching `{tele_name}`. See `!teleporters`.")
            return
        # Duplicate-name entries (per-account item instances) all go to the SAME place,
        # so for firing/mapping we just use the first match. If it errors as "not owned",
        # the raw response will tell us and we try another id.
        item_id, trec = matches[0]
        dup_note = f" _(1 of {len(matches)} same-named entries)_" if len(matches) > 1 else ""

        # Resolve account → suid.
        t = next((x for x in db.get_trustees()
                  if x.get("name", "").lower() == account.lower()), None)
        if not t or not t.get("suid"):
            await ctx.send(f"Account `{account}` not found (or no suid).")
            return
        suid = t["suid"]

        await ctx.send(f"🧪 Firing **{trec.get('name')}** on **{account}**{dup_note}…\n"
                       f"_This activates a real item — watch what happens in-game._")

        # Try each same-named entry in turn — the KB may hold copies belonging to other
        # accounts (different item ids), and only the one THIS account owns will fire.
        # Stop at the first that doesn't come back with a "not owned"/error response.
        raw = None
        fired_id = None
        last_err = None
        for cand_id, _crec in matches:
            try:
                resp = await self.session.post_as(
                    "ajax/backpack_action.php",
                    {"action": "activate", "itemids[]": str(cand_id)},
                    suid,
                )
            except Exception as e:
                last_err = str(e)
                continue
            # crude ownership check: an error response means this id isn't usable here.
            low = (resp or "").lower()
            if '"error"' in low and ('not' in low and ('own' in low or 'found' in low or 'have' in low)):
                last_err = resp[:200]
                continue
            raw = resp
            fired_id = cand_id
            break

        if raw is None:
            await ctx.send(f"⚠️ None of the {len(matches)} `{trec.get('name')}` entries fired "
                           f"successfully for **{account}**. Last response/error:\n"
                           f"```\n{(last_err or 'unknown')[:400]}\n```\n"
                           f"(This account may not own this teleporter.)")
            return
        item_id = fired_id

        # Show the raw response verbatim — this is the whole point of the test.
        snippet = raw[:1500] if raw else "(empty response)"
        await ctx.send(f"**Raw response** (item {fired_id}):\n```\n{snippet}\n```")

        # Try to parse it as the JSON the JS expects (status / error / redirectTo).
        try:
            data = _json.loads(raw)
            bits = []
            if data.get("status"): bits.append(f"status: {data['status']}")
            if data.get("error"): bits.append(f"error: {data['error']}")
            if data.get("redirectTo"): bits.append(f"redirectTo: {data['redirectTo']}")
            if bits:
                await ctx.send("**Parsed:** " + " · ".join(bits))
        except Exception:
            await ctx.send("_(response wasn't clean JSON — see raw above)_")

        # Read the resulting room (no-op move reports curRoom).
        try:
            loc = await self.session.get_as("ajax_changeroomb.php?room=0&lastroom=0", suid)
            room = int(_json.loads(loc).get("curRoom", 0))
        except Exception:
            room = 0
        if room:
            kb[item_id]["room"] = room
            db.save_teleporters(kb)
            await ctx.send(f"📍 **{account}** is now in room **{room}** — saved as "
                           f"**{trec.get('name')}**'s arrival room. Confirm it matches in-game!")
        else:
            await ctx.send("📍 Couldn't read a resulting room — check in-game where the "
                           "account landed and use `!tele-map` to set it.")


    @commands.command(name="tele-map", aliases=["telemap", "tmap"])
    async def tele_map(self, ctx, account: str, *, rest: str):
        """
        Record a teleporter's ARRIVAL ROOM after you activate it in-game.
          !tele-map <account> <teleporter name>          → reads the account's CURRENT room
          !tele-map <account> <teleporter name> = <room> → set the room manually
        The bot does NOT activate anything here — you activate in-game, this captures where
        you landed. (To have the bot fire it for you, use !tele-fire.)
        """
        import json as _json
        rest = rest.strip()
        manual_room = None
        if "=" in rest:
            name_part, room_part = rest.rsplit("=", 1)
            rp = room_part.strip()
            if rp.isdigit():
                manual_room = int(rp); rest = name_part.strip()
        tele_name = rest.strip().strip('"')
        if not tele_name:
            await ctx.send("Usage: `!tele-map <account> <teleporter name>` or `… = <room>`.")
            return
        kb = db.get_teleporters()
        matches = [(iid, t) for iid, t in kb.items()
                   if tele_name.lower() in (t.get("name", "").lower())]
        if not matches:
            await ctx.send(f"🔎 No teleporter matching `{tele_name}`. See `!teleporters`.")
            return
        if len(matches) > 1:
            lines = "\n".join(f"`#{iid}` — {t.get('name')}" for iid, t in matches[:10])
            await ctx.send(f"🔎 `{tele_name}` matches {len(matches)}:\n{lines}\n"
                           f"These may be duplicates — try `!teleporters-clean`.")
            return
        item_id, trec = matches[0]
        if manual_room is not None:
            room, src = manual_room, "set manually"
        else:
            t = next((x for x in db.get_trustees()
                      if x.get("name", "").lower() == account.lower()), None)
            if not t or not t.get("suid"):
                await ctx.send(f"Account `{account}` not found (or no suid)."); return
            try:
                raw = await self.session.get_as("ajax_changeroomb.php?room=0&lastroom=0", t["suid"])
                room = int(_json.loads(raw).get("curRoom", 0))
            except Exception as e:
                await ctx.send(f"⚠️ Couldn't read current room: `{e}`"); return
            if not room:
                await ctx.send("⚠️ Read room 0 — set manually with `= <room>`."); return
            src = f"read from {account}'s current room"
        kb[item_id]["room"] = room
        db.save_teleporters(kb)
        await ctx.send(f"✅ **{trec.get('name')}** → arrival room **{room}** ({src}). "
                       f"`!teleporters mapped` to see progress.")

    @commands.command(name="count-keys", aliases=["countkeys", "keycount"])
    async def count_keys(self, ctx, *, account: str = None):
        """
        Count everything in an account's Keys backpack tab — distinct items AND total
        quantity — so you can run it BEFORE and AFTER firing teleporters to prove whether
        anything was consumed. Read-only. Usage: !count-keys [account]
        """
        from outwar.scraper import parse_backpack_items
        trustees = db.get_trustees()
        if account:
            t = next((x for x in trustees
                      if x.get("name", "").lower() == account.lower()), None)
            if not t:
                await ctx.send(f"Account `{account}` not found in trustees.")
                return
        else:
            t = next((x for x in trustees if x.get("suid")), None)
            if not t:
                await ctx.send("No trustees with a suid.")
                return
        suid = t.get("suid")
        if not suid:
            await ctx.send(f"`{t['name']}` has no suid.")
            return

        try:
            html = await self.session.get_as("ajax/backpackcontents.php?tab=key", suid)
        except Exception as e:
            await ctx.send(f"Failed to fetch keys tab: {e}")
            return

        items = parse_backpack_items(html)
        if not items:
            await ctx.send(f"No keys found for **{t['name']}**.")
            return

        distinct = len(items)
        total_qty = 0
        for it in items:
            try:
                total_qty += int(it.get("quantity", 1) or 1)
            except (ValueError, TypeError):
                total_qty += 1

        import discord
        embed = discord.Embed(title=f"🔑 Key count — {t['name']}",
                              color=discord.Color.teal())
        embed.add_field(name="Distinct items", value=str(distinct), inline=True)
        embed.add_field(name="Total quantity", value=str(total_qty), inline=True)
        embed.set_footer(text="Run before AND after firing teleporters — if these numbers "
                              "are unchanged, nothing was consumed.")
        # Per-item quantity list (so you can see exactly which one changed, if any).
        lines = []
        for it in sorted(items, key=lambda x: x.get("item_name", "")):
            q = it.get("quantity", 1) or 1
            qtag = f" ×{q}" if str(q) not in ("1", "None") else ""
            lines.append(f"{it.get('item_name','?')}{qtag}")
        buf = ""
        for ln in lines:
            if len(buf) + len(ln) + 2 > 1024:
                embed.add_field(name="Items", value=buf, inline=False); buf = ""
            buf += ln + "\n"
        if buf.strip():
            embed.add_field(name="Items" if not embed.fields[2:] else "Items (cont.)",
                            value=buf, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="tele-map-all", aliases=["telemapall", "tmapall"])
    async def tele_map_all(self, ctx, account: str, mode: str = "reusable"):
        """
        Bulk-map teleporter arrival rooms: fire each UNMAPPED teleporter on <account>,
        one at a time, capture where it lands, and save the room. Fires real items.
          !tele-map-all <account>              → only teleporters KNOWN reusable (safe default)
          !tele-map-all <account> all          → include consumables (WILL use them up)
          !tele-map-all <account> remap         → also re-fire already-mapped ones (verify)
        Skips any the account doesn't own. Paces between fires. Watch it in-game.
        """
        import json as _json, asyncio as _asyncio
        mode = (mode or "reusable").strip().lower()

        t = next((x for x in db.get_trustees()
                  if x.get("name", "").lower() == account.lower()), None)
        if not t or not t.get("suid"):
            await ctx.send(f"Account `{account}` not found (or no suid).")
            return
        suid = t["suid"]

        kb = db.get_teleporters()
        # Collapse to one target per NAME (same-named copies go to the same room, so we
        # only need to map each distinct teleporter once). Prefer an unmapped entry.
        by_name = {}
        for iid, rec in kb.items():
            nm = (rec.get("name") or "").strip()
            by_name.setdefault(nm, []).append((iid, rec))

        targets = []
        for nm, entries in by_name.items():
            # already mapped? skip unless remap
            already = next((e for e in entries if e[1].get("room")), None)
            if already and mode != "remap":
                continue
            # consumable filter
            kind = entries[0][1].get("kind")
            if mode not in ("all", "remap") and kind == "consumable":
                continue
            targets.append((nm, entries))

        if not targets:
            await ctx.send("Nothing to map with that mode. "
                           "(Try `all` to include consumables, or `remap` to redo mapped ones.)")
            return

        warn = ("⚠️ includes **consumables** (will be used up)" if mode == "all"
                else "re-firing already-mapped too" if mode == "remap"
                else "reusable-only (safe)")
        await ctx.send(f"🗺️ Bulk-mapping **{len(targets)}** teleporters on **{account}** "
                       f"— {warn}. Firing one at a time; watch in-game. `!boss-stop` won't "
                       f"halt this — it runs to completion.")

        mapped_now = 0
        results = []
        for nm, entries in targets:
            fired = False
            for cand_id, rec in entries:
                try:
                    resp = await self.session.post_as(
                        "ajax/backpack_action.php",
                        {"action": "activate", "itemids[]": str(cand_id)},
                        suid,
                    )
                except Exception as e:
                    results.append(f"⚠️ {nm}: request error"); continue
                low = (resp or "").lower()
                if '"error"' in low and 'not' in low and ('own' in low or 'found' in low or 'have' in low):
                    continue   # account doesn't own this copy — try next
                # success — read arrival room
                await _asyncio.sleep(0.4)
                try:
                    loc = await self.session.get_as("ajax_changeroomb.php?room=0&lastroom=0", suid)
                    room = int(_json.loads(loc).get("curRoom", 0))
                except Exception:
                    room = 0
                if room:
                    kb[cand_id]["room"] = room
                    db.save_teleporters(kb)
                    mapped_now += 1
                    results.append(f"✅ {nm} → room {room}")
                else:
                    results.append(f"❓ {nm}: fired but couldn't read room")
                fired = True
                break
            if not fired:
                results.append(f"⛔ {nm}: {account} doesn't own it")
            await _asyncio.sleep(0.8)   # pace between teleporters

        # Report in chunks (Discord 2000-char limit)
        header = f"🗺️ **Bulk map complete** — {mapped_now} newly mapped of {len(targets)} tried.\n"
        buf = header
        for line in results:
            if len(buf) + len(line) + 1 > 1900:
                await ctx.send(buf); buf = ""
            buf += line + "\n"
        if buf.strip():
            await ctx.send(buf)

    @commands.command(name="teleporters-clean", aliases=["teles-clean", "tele-dedupe"])
    async def teleporters_clean(self, ctx, apply: str = ""):
        """
        Inspect (and optionally fix) duplicate teleporter entries.
          !teleporters-clean          → REPORT duplicate-by-name groups with their raw ids
                                         (shows WHY there are dupes — compare the ids)
          !teleporters-clean apply    → de-dupe: keep one entry per name, preferring the
                                         one that already has a mapped room, merging fields
        Read-only unless you pass 'apply'. Nothing in-game is touched either way.
        """
        import discord
        kb = db.get_teleporters()
        if not kb:
            await ctx.send("Teleporter KB is empty.")
            return

        # Group entries by normalised name.
        by_name = {}
        for iid, t in kb.items():
            nm = (t.get("name") or "").strip().lower()
            by_name.setdefault(nm, []).append((iid, t))

        dupes = {nm: entries for nm, entries in by_name.items() if len(entries) > 1}
        if not dupes:
            await ctx.send("✅ No duplicate teleporter names — KB is clean.")
            return

        if apply.strip().lower() != "apply":
            # REPORT mode — show each dupe group with raw ids so we can see the cause.
            embed = discord.Embed(
                title=f"🔍 Duplicate teleporters ({len(dupes)} names affected)",
                color=discord.Color.orange())
            embed.description = ("Same name stored under multiple keys. Compare the ids to "
                                 "see the cause. Run `!teleporters-clean apply` to merge.")
            shown = 0
            for nm, entries in dupes.items():
                if shown >= 15:
                    break
                lines = []
                for iid, t in entries:
                    room = t.get("room")
                    lines.append(f"id=`{iid}` · room={room if room else '?'} · "
                                 f"{t.get('kind') or '?'}")
                embed.add_field(name=entries[0][1].get("name", nm),
                                value="\n".join(lines), inline=False)
                shown += 1
            await ctx.send(embed=embed)
            return

        # APPLY mode — merge each group to one entry.
        removed = 0
        for nm, entries in dupes.items():
            # Prefer the entry that has a mapped room; else the first.
            entries_sorted = sorted(entries, key=lambda e: (e[1].get("room") is None,))
            keep_id, keep_rec = entries_sorted[0]
            # Merge any non-null fields from the others into the kept one.
            for iid, t in entries_sorted[1:]:
                for f in ("destination", "kind", "room"):
                    if not keep_rec.get(f) and t.get(f):
                        keep_rec[f] = t[f]
                del kb[iid]
                removed += 1
            kb[keep_id] = keep_rec
        db.save_teleporters(kb)
        word = "entry" if removed == 1 else "entries"
        await ctx.send(f"✅ De-duped: removed **{removed}** duplicate {word}, "
                       f"KB now has **{len(kb)}** teleporters. Run `!teleporters` to verify.")


PROTECTED_ACCOUNTS = {"guardianliam", "brabbit2005", "chester2210", "3ncore"}

# Optimise ranking metrics. 'weighted' = percentile-blend score (0-100) of an
# account vs the pool (all trustees − excluded); weights live in settings.json.
OPT_METRICS = {"ele", "power", "chaos", "weighted"}


def _opt_weights():
    """Live-tunable blend weights (ele/power/chaos), default 60/30/10."""
    s = db.get_settings()
    return (float(s.get("ele_weight", 0.60)),
            float(s.get("power_weight", 0.30)),
            float(s.get("chaos_weight", 0.10)))


def _weighted_scores(accounts) -> dict:
    """Return {name: score 0-100}. Each stat -> percentile within this set, then
    blended by the live weights. Percentile ranks position in the pool, so no
    single account defines the scale and it's faction-fair."""
    import bisect
    n = len(accounts)
    if n == 0:
        return {}
    we, wp, wc = _opt_weights()
    eles   = sorted(a.get("elemental", 0) for a in accounts)
    powers = sorted(a.get("power", 0) for a in accounts)
    chaoss = sorted(a.get("chaos", 0) for a in accounts)
    def pct(vals, v):
        return 100.0 * bisect.bisect_right(vals, v) / n
    return {a["name"]: (we * pct(eles, a.get("elemental", 0))
                        + wp * pct(powers, a.get("power", 0))
                        + wc * pct(chaoss, a.get("chaos", 0)))
            for a in accounts}


class OptimumCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def session(self):
        return self.bot.outwar

    @commands.command(name="exclude")
    async def exclude(self, ctx, *accounts):
        """Exclude one or more accounts on THIS server (from the channel).
        The bot ignores them in raids, optimise, caps, stats — everywhere.
        Per-server: excluding on Sigil doesn't affect the same name on Torax.
        Live — takes effect immediately, no restart. Usage: !exclude Name1 Name2 …"""
        from outwar.servers import server_from_channel, name_for
        if not accounts:
            await ctx.send("Usage: `!exclude <account> [account2 …]`")
            return
        server_id = server_from_channel(ctx.channel)
        added = db.add_excluded(list(accounts), server_id=server_id)
        already = [a for a in accounts if a not in added]
        lines = []
        if added:
            lines.append(f"🚫 Excluded **{len(added)}** on **{name_for(server_id)}**: {', '.join(added)}")
        if already:
            lines.append(f"Already excluded: {', '.join(already)}")
        total = db.get_excluded(server_id)
        lines.append(f"Total excluded on {name_for(server_id)}: **{len(total)}**")
        await ctx.send("\n".join(lines))

    @commands.command(name="include", aliases=["unexclude"])
    async def include(self, ctx, *accounts):
        """Remove one or more accounts from THIS server's exclude list.
        Usage: !include Name1 Name2 …"""
        from outwar.servers import server_from_channel, name_for
        if not accounts:
            await ctx.send("Usage: `!include <account> [account2 …]`")
            return
        server_id = server_from_channel(ctx.channel)
        removed = db.remove_excluded(list(accounts), server_id=server_id)
        if removed:
            await ctx.send(f"✅ Un-excluded **{len(removed)}** on **{name_for(server_id)}**: "
                           f"{', '.join(removed)}\n"
                           f"Total excluded on {name_for(server_id)}: **{len(db.get_excluded(server_id))}**")
        else:
            await ctx.send(f"None of those were excluded on **{name_for(server_id)}**.")

    @commands.command(name="excluded")
    async def excluded(self, ctx):
        """Show THIS server's exclude list."""
        from outwar.servers import server_from_channel, name_for
        server_id = server_from_channel(ctx.channel)
        ex = db.get_excluded(server_id)
        if not ex:
            await ctx.send(f"No accounts are excluded on **{name_for(server_id)}**.")
            return
        await ctx.send(f"🚫 **Excluded accounts ({len(ex)})**\n" + ", ".join(sorted(ex)))

    @commands.command(name="optimise-all", aliases=["optimize-all", "optimiseall"])
    async def optimise_all(self, ctx, *crew_names):
        """Optimise several crews in priority order, without reusing accounts.
        Crew 1 gets first pick of the strongest accounts, crew 2 picks from the rest, etc.
        Usage: !optimise-all [ele|power|chaos|weighted] <crew1> <crew2> …"""
        crew_names = list(crew_names)
        metric = "ele"
        if crew_names and crew_names[0].lower() in OPT_METRICS:
            metric = crew_names.pop(0).lower()
        if len(crew_names) < 2:
            await ctx.send("Usage: `!optimise-all [ele|power|chaos|weighted] "
                           "<crew1> <crew2> …` — at least two crews.")
            return

        crews = []
        locked_skipped = []
        for cn in crew_names:
            crew = db.get_crew(cn)
            full = crew["full_name"] if crew else db.normalize_crew(cn)
            _in = db.get_trustees_by_crew(full)
            _lid = next((t.get("crew_id") for t in _in if t.get("crew_id") is not None), None)
            if db.is_crew_locked(_lid, full):
                locked_skipped.append(full)
                continue
            crews.append((cn, full))

        if locked_skipped:
            await ctx.send("🔒 Skipping locked crew(s) — members must remain: "
                           + ", ".join(locked_skipped))
        if len(crews) < 2:
            await ctx.send("Need at least two non-locked crews to optimise.")
            return

        all_t    = db.get_trustees()
        from outwar.servers import server_from_channel as _sfc
        excluded = {n.lower() for n in db.get_excluded(_sfc(ctx.channel))}
        msg = await ctx.send(
            f"🔍 Optimising **{len(crews)}** crews — fetching stats for {len(all_t)} trustees…")

        sem = asyncio.Semaphore(10)

        async def _fetch(t):
            async with sem:
                try:
                    html = await self.session.get(f"profile.php?transnick={t['name']}&server=1")
                    char = parse_character_profile(html, t["name"])
                    if char:
                        return {"name": t["name"], "crew": t.get("crew", ""),
                                "crew_id": t.get("crew_id"),
                                "power": char.power, "elemental": char.elemental,
                                "chaos": char.chaos}
                except Exception:
                    pass
            return None

        results = await asyncio.gather(*[_fetch(t) for t in all_t])
        pool = [r for r in results if r and r["name"].lower() not in PROTECTED_ACCOUNTS
                and r["name"].lower() not in excluded
                and not db.is_crew_locked(r.get("crew_id"), r.get("crew"))]
        await msg.delete()

        # Ranking key by metric. 'weighted' = percentile blend vs all trustees − excluded.
        wscores = {}
        if metric == "weighted":
            base = [r for r in results if r and r["name"].lower() not in excluded]
            wscores = _weighted_scores(base)
            def key(r):
                return (wscores.get(r["name"], 0.0),)
        elif metric == "power":
            def key(r):
                return (r.get("power", 0), r.get("elemental", 0))
        elif metric == "chaos":
            def key(r):
                return (r.get("chaos", 0), r.get("elemental", 0))
        else:  # ele (default)
            def key(r):
                return (r.get("elemental", 0), r.get("power", 0))

        pool.sort(key=key, reverse=True)

        claimed_names = set()
        _mlabel = {"ele": "elemental, then power", "power": "power, then elemental",
                   "chaos": "chaos, then elemental",
                   "weighted": "weighted score (60/30/10, live)"}[metric]
        await ctx.send(
            f"🧮 **Multi-crew optimise** — priority order: "
            f"{', '.join(f for _, f in crews)} · filling each crew to 200 "
            f"(rank: {_mlabel})")

        for cn, full in crews:
            current = [r for r in pool if r["crew"] == full]
            # claim the strongest 200 still available (priority order, no reuse)
            claimed = []
            for r in pool:
                if r["name"] in claimed_names:
                    continue
                claimed.append(r)
                if len(claimed) >= 200:
                    break
            claimed_set = {r["name"] for r in claimed}
            claimed_names |= claimed_set

            cur_names = {r["name"] for r in current}
            move_out = sorted([r for r in current if r["name"] not in claimed_set],
                              key=key, reverse=True)
            add_in   = sorted([r for r in claimed if r["name"] not in cur_names],
                              key=key, reverse=True)

            def _tot(rows, k):
                return sum(r.get(k, 0) for r in rows)
            cur_p, cur_e, cur_c = _tot(current, "power"), _tot(current, "elemental"), _tot(current, "chaos")
            new_p, new_e, new_c = _tot(claimed, "power"), _tot(claimed, "elemental"), _tot(claimed, "chaos")

            header = (
                f"**{full}** — {len(claimed)}/200 filled · {len(add_in)} in / {len(move_out)} out\n"
                f"current → Power {cur_p:,} · Ele {cur_e:,} · Chaos {cur_c:,}\n"
                f"new     → Power {new_p:,} · Ele {new_e:,} · Chaos {new_c:,}"
            )

            def _row(r):
                sc = f"  ⚖️{wscores[r['name']]:.1f}" if r['name'] in wscores else ""
                return (f"{r['name']}  —  Ele {r['elemental']:,} | Power {r['power']:,} "
                        f"| Chaos {r['chaos']:,}{sc}")

            lines = [
                f"{full} — optimise plan",
                f"Slots filled: {len(claimed)}/200",
                f"Current totals: Power {cur_p:,} | Ele {cur_e:,} | Chaos {cur_c:,}",
                f"New totals:     Power {new_p:,} | Ele {new_e:,} | Chaos {new_c:,}",
                "",
                f"=== MOVE OUT ({len(move_out)}) ===",
            ]
            lines += [_row(r) for r in move_out] or ["(none)"]
            lines += ["", f"=== ADD ({len(add_in)}) ==="]
            lines += [_row(r) for r in add_in] or ["(none)"]
            txt = "\n".join(lines)

            buf = io.BytesIO(txt.encode("utf-8"))
            fname = full.replace(" ", "_").replace("/", "-") + "_optimise.txt"
            await ctx.send(content=header, file=discord.File(buf, filename=fname))

    @commands.command(name="optimise")
    async def optimum(self, ctx, metric_or_crew: str, crew: str = None):
        """
        Suggest optimal crew composition. Rank by ele (default), power, chaos, or
        weighted (percentile blend 60/30/10, live-tunable in settings.json).
        Usage: !optimise [ele|power|chaos|weighted] <crew>
        """
        # Parse optional leading metric: "!optimise weighted LoD" vs "!optimise LoD"
        if crew is not None and metric_or_crew.lower() in OPT_METRICS:
            metric, crew_name = metric_or_crew.lower(), crew
        else:
            metric, crew_name = "ele", metric_or_crew

        # Resolve crew
        crew = db.get_crew(crew_name)
        crew_full = crew["full_name"] if crew else db.normalize_crew(crew_name)

        # Locked crews are fixed — never optimise (rearrange) them.
        _lock_id = None
        _in = db.get_trustees_by_crew(crew_full)
        if _in:
            _lock_id = next((t.get("crew_id") for t in _in if t.get("crew_id") is not None), None)
        if db.is_crew_locked(_lock_id, crew_full):
            await ctx.send(f"🔒 **{crew_full}** is a locked crew — its members must remain, "
                           f"so it won't be optimised.")
            return

        in_crew  = db.get_trustees_by_crew(crew_full)
        all_t    = db.get_trustees()

        if not in_crew:
            await ctx.send(f"No trustees found in `{crew_full}`. Run `!scan-trustees` first.")
            return

        msg = await ctx.send(
            f"🔍 Fetching stats for **{len(all_t)}** trustees — this may take a moment..."
        )

        sem = asyncio.Semaphore(10)

        async def _fetch(t):
            async with sem:
                try:
                    html = await self.session.get(
                        f"profile.php?transnick={t['name']}&server=1"
                    )
                    char = parse_character_profile(html, t["name"])
                    if char:
                        return {
                            "name":      t["name"],
                            "crew":      t.get("crew", ""),
                            "crew_id":   t.get("crew_id"),
                            "power":     char.power,
                            "elemental": char.elemental,
                            "chaos":     char.chaos,
                        }
                except Exception:
                    pass
            return None

        results = await asyncio.gather(*[_fetch(t) for t in all_t])
        from outwar.servers import server_from_channel as _sfc
        _excluded = {n.lower() for n in db.get_excluded(_sfc(ctx.channel))}
        # Weighted percentile base = all trustees − excluded (before protected/locked filter)
        _wbase = [r for r in results if r and r["name"].lower() not in _excluded]
        results = [r for r in results if r and r["name"].lower() not in PROTECTED_ACCOUNTS
                   and r["name"].lower() not in _excluded
                   and not db.is_crew_locked(r.get("crew_id"), r.get("crew"))]

        # Ranking key by metric (weighted = percentile blend vs all trustees − excluded).
        if metric == "weighted":
            _wscores = _weighted_scores(_wbase)
            def _sort_key(r):
                return (_wscores.get(r["name"], 0.0),)
        elif metric == "power":
            def _sort_key(r):
                return (r.get("power", 0), r.get("elemental", 0))
        elif metric == "chaos":
            def _sort_key(r):
                return (r.get("chaos", 0), r.get("elemental", 0))
        else:  # ele (default)
            def _sort_key(r):
                return (r.get("elemental", 0), r.get("power", 0))

        in_stats  = sorted(
            [r for r in results if r["crew"] == crew_full],
            key=_sort_key
        )
        out_stats = sorted(
            [r for r in results if r["crew"] != crew_full],
            key=_sort_key,
            reverse=True
        )

        # Find beneficial swaps
        swaps    = []
        used_out = set()
        for weak in in_stats:
            for strong in out_stats:
                if strong["name"] in used_out:
                    continue
                if _sort_key(strong) > _sort_key(weak):
                    swaps.append((weak, strong))
                    used_out.add(strong["name"])
                    break

        await msg.delete()

        if not swaps:
            await ctx.send(
                f"✅ **{crew_full}** already has the optimal elemental composition "
                f"from available trustees — no improvements found."
            )
            return

        # Calculate total gains
        gain_ele   = sum(s.get("elemental", 0) - w.get("elemental", 0) for w, s in swaps)
        gain_power = sum(s.get("power", 0)     - w.get("power", 0)     for w, s in swaps)
        gain_chaos = sum(s.get("chaos", 0)     - w.get("chaos", 0)     for w, s in swaps)

        lines = [
            f"**Optimum suggestions for {crew_full}** ({len(swaps)} swaps)\n",
            f"**Estimated gains:** "
            f"ELE +{gain_ele:,} · "
            f"PWR {'+' if gain_power >= 0 else ''}{gain_power:,} · "
            f"CHAOS {'+' if gain_chaos >= 0 else ''}{gain_chaos:,}\n",
        ]

        for weak, strong in swaps:
            w_ele, w_pwr = weak.get("elemental", 0),   weak.get("power", 0)
            s_ele, s_pwr = strong.get("elemental", 0), strong.get("power", 0)
            lines.append(
                f"REMOVE `{weak['name']}` (ELE: {w_ele:,} · PWR: {w_pwr:,}) "
                f"→ ADD `{strong['name']}` (ELE: {s_ele:,} · PWR: {s_pwr:,})"
            )

        await ctx.send(
            f"**Optimum suggestions for {crew_full}** ({len(swaps)} swaps)\n"
            f"**Estimated gains:** "
            f"ELE +{gain_ele:,} · "
            f"PWR {'+' if gain_power >= 0 else ''}{gain_power:,} · "
            f"CHAOS {'+' if gain_chaos >= 0 else ''}{gain_chaos:,}"
        )

        # Summary blocks for easy copy-paste
        remove_names  = " ".join(w["name"] for w, _ in swaps)
        add_names     = " ".join(s["name"] for _, s in swaps)
        remove_pwr    = sum(w.get("power", 0)     for w, _ in swaps)
        remove_ele    = sum(w.get("elemental", 0) for w, _ in swaps)
        remove_chaos  = sum(w.get("chaos", 0)     for w, _ in swaps)
        add_pwr       = sum(s.get("power", 0)     for _, s in swaps)
        add_ele       = sum(s.get("elemental", 0) for _, s in swaps)
        add_chaos     = sum(s.get("chaos", 0)     for _, s in swaps)

        await ctx.send(
            f"**Remove accounts:** {remove_names}\n"
            f"Total PWR: **{remove_pwr:,}** · Total ELE: **{remove_ele:,}** · Total CHAOS: **{remove_chaos:,}**"
        )
        await ctx.send(
            f"**Add accounts:** {add_names}\n"
            f"Total PWR: **{add_pwr:,}** · Total ELE: **{add_ele:,}** · Total CHAOS: **{add_chaos:,}**"
        )

    @commands.group(name="crew", invoke_without_command=True)
    async def crew(self, ctx):
        """Crew hub. Use !crew <action> — lock / unlock / locked / scores.
        (For adding/editing crews see !crews.)"""
        await ctx.send(embed=es.info_embed(
            "🏰 Crew Commands",
            description=(
                "`!crew lock <crew_id>` — lock a crew from raids\n"
                "`!crew unlock <crew_id>` — unlock it\n"
                "`!crew locked` — list locked crews\n"
                "`!crew scores [crew]` — crew score rankings\n\n"
                "_Add/edit crews with `!crews`. Classic names (`!crew-lock`…) still work._"
            )))

    async def _crew_redispatch(self, ctx, target, rest=""):
        ctx.message.content = f"{ctx.prefix}{target} {rest}".rstrip()
        await self.bot.process_commands(ctx.message)

    @crew.command(name="lock")
    async def crew_lock_sub(self, ctx, crew_id: int):
        """Lock a crew. Same as !crew-lock."""
        await self._crew_redispatch(ctx, "crew-lock", str(crew_id))

    @crew.command(name="unlock")
    async def crew_unlock_sub(self, ctx, crew_id: int):
        """Unlock a crew. Same as !crew-unlock."""
        await self._crew_redispatch(ctx, "crew-unlock", str(crew_id))

    @crew.command(name="locked")
    async def crew_locked_sub(self, ctx):
        """List locked crews. Same as !crews-locked."""
        await self._crew_redispatch(ctx, "crews-locked")

    @crew.command(name="scores")
    async def crew_scores_sub(self, ctx, *, arg: str = ""):
        """Crew score rankings. Same as !scores."""
        await self._crew_redispatch(ctx, "scores", arg)

    @commands.command(name="crews-locked", aliases=["locked-crews", "crewlocked"])
    async def crews_locked(self, ctx):
        """Show crews locked out of optimisation (members never moved)."""
        ids, names = db.get_locked_crews()
        if not ids and not names:
            await ctx.send("No crews are locked.")
            return
        # try to attach a readable name to each locked id from trustees
        id_names = {}
        for t in db.get_trustees():
            cid = t.get("crew_id")
            if cid is not None and int(cid) in ids and t.get("crew"):
                id_names[int(cid)] = t["crew"]
        lines = ["🔒 **Locked crews** (excluded from optimisation — members must remain):"]
        for i in sorted(ids):
            lines.append(f"• id `{i}`" + (f" — {id_names[i]}" if i in id_names else ""))
        if names:
            lines.append(f"• name fallbacks: {', '.join(sorted(names))}")
        await ctx.send("\n".join(lines))

    @commands.command(name="crew-lock", aliases=["crewlock"])
    async def crew_lock(self, ctx, crew_id: int):
        """Lock a crew by ID so its members are never moved by optimisation."""
        db.add_locked_crew(crew_id=crew_id)
        await ctx.send(f"🔒 Crew id `{crew_id}` locked — its members won't be optimised.")

    @commands.command(name="crew-unlock", aliases=["crewunlock"])
    async def crew_unlock(self, ctx, crew_id: int):
        """Unlock a previously locked crew by ID."""
        db.remove_locked_crew(crew_id=crew_id)
        await ctx.send(f"🔓 Crew id `{crew_id}` unlocked — it can now be optimised.")

    @commands.command(name="scores", aliases=["score"])
    async def scores(self, ctx, *, arg: str = None):
        """Weighted account scores — percentile blend (ele/power/chaos), live weights.
        `!score <account>` — one account's percentiles + blended score
        `!scores [crew]`   — roster ranked by weighted score (default: all trustees)"""
        import bisect
        all_t = db.get_trustees()
        from outwar.servers import server_from_channel as _sfc
        excluded = {n.lower() for n in db.get_excluded(_sfc(ctx.channel))}

        single = crew_filter = None
        if arg:
            if any(t.get("name", "").lower() == arg.lower() for t in all_t):
                single = arg
            else:
                crew_filter = arg

        msg = await ctx.send(f"🔍 Scoring — fetching stats for {len(all_t)} trustees…")
        sem = asyncio.Semaphore(10)

        async def _fetch(t):
            async with sem:
                try:
                    html = await self.session.get(f"profile.php?transnick={t['name']}&server=1")
                    char = parse_character_profile(html, t["name"])
                    if char:
                        return {"name": t["name"], "crew": t.get("crew", ""),
                                "power": char.power, "elemental": char.elemental,
                                "chaos": char.chaos}
                except Exception:
                    pass
            return None

        results = await asyncio.gather(*[_fetch(t) for t in all_t])
        base = [r for r in results if r and r["name"].lower() not in excluded]
        try:
            await msg.delete()
        except Exception:
            pass
        if not base:
            await ctx.send("No scored accounts (all excluded or stats unavailable).")
            return

        scores = _weighted_scores(base)          # {name: score}
        we, wp, wc = _opt_weights()
        n = len(base)
        eles   = sorted(x["elemental"] for x in base)
        powers = sorted(x["power"] for x in base)
        chaoss = sorted(x["chaos"] for x in base)

        if single:
            a = next((r for r in base if r["name"].lower() == single.lower()), None)
            if not a:
                await ctx.send(f"`{single}` isn't in the scoring pool (excluded or no stats).")
                return
            ep = 100 * bisect.bisect_right(eles, a["elemental"]) / n
            pp = 100 * bisect.bisect_right(powers, a["power"]) / n
            cp = 100 * bisect.bisect_right(chaoss, a["chaos"]) / n
            embed = es.info_embed(f"⚖️ Weighted score — {a['name']}")
            embed.add_field(name="Score", value=f"**{scores[a['name']]:.1f}** / 100", inline=False)
            embed.add_field(name="Percentile",
                            value=f"Ele **{ep:.0f}** · Power **{pp:.0f}** · Chaos **{cp:.0f}**",
                            inline=False)
            embed.add_field(name="Raw stats",
                            value=f"Ele {a['elemental']:,} · Power {a['power']:,} · Chaos {a['chaos']:,}",
                            inline=False)
            embed.set_footer(text=f"Pool: {n} trustees − excluded · "
                                  f"weights {we:.0%}/{wp:.0%}/{wc:.0%}")
            await ctx.send(embed=embed)
            return

        ranked = base
        label = "all trustees"
        if crew_filter:
            cf = db.get_crew(crew_filter)
            cf_full = cf["full_name"] if cf else db.normalize_crew(crew_filter)
            ranked = [r for r in base if r["crew"] == cf_full]
            label = cf_full
            if not ranked:
                await ctx.send(f"No scored accounts in `{cf_full}`.")
                return
        ranked = sorted(ranked, key=lambda r: -scores[r["name"]])
        avg = sum(scores[r["name"]] for r in ranked) / len(ranked)

        lines = [f"{i:>2}. {r['name']:<18} {scores[r['name']]:>5.1f}"
                 for i, r in enumerate(ranked, 1)]
        header = (f"⚖️ **Weighted scores — {label}** ({len(ranked)} accounts · "
                  f"avg {avg:.1f} · weights {we:.0%}/{wp:.0%}/{wc:.0%})")
        # Chunk into ≤1900-char code blocks so long rosters don't overflow.
        chunk = ""
        first = True
        for ln in lines:
            if len(chunk) + len(ln) + 1 > 1850:
                await ctx.send((header if first else "") + f"\n```\n{chunk}```")
                chunk = ""; first = False
            chunk += ln + "\n"
        if chunk:
            await ctx.send((header if first else "") + f"\n```\n{chunk}```")


async def setup(bot):
    await bot.add_cog(AdminCommands(bot))
    await bot.add_cog(OptimumCommands(bot))
