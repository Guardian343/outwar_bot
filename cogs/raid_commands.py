"""
raid_commands.py — Prime God database, status, drops and raiding.
"""

import asyncio
import re
import aiohttp
import discord
from discord.ext import commands
from datetime import datetime, timezone
from yarl import URL
from outwar import database as db, logger
from cogs import embed_style as es
from outwar.scraper import (
    parse_gods,
    parse_prime_god_page, parse_prime_god_loot, format_time_remaining,
    parse_rec_stats_block,
)

SIGIL_URL = URL("https://sigil.outwar.com")

DEFAULT_RECOMMENDED = {
    "anisupremacy": 20, "anichaos": 25, "anipower": 25, "aniele": 25, "anivers": 25,
    "rezun": 30, "banok": 30, "envar": 30,
    "valzek": 40, "agnar": 40,
    "shayar": 40, "kinark": 40, "firan": 40, "arcon": 40, "holgor": 40,
    "villax": 50, "rillax": 50, "thanox": 50, "murfax": 50, "gregov": 50,
    "dexor": 60, "balerion": 60, "viserion": 60, "dlanod": 60,
    "straya": 75, "skarthul": 75, "nafir": 100,
    "raiyar": 50, "esquin": 50, "crolvak": 50, "xynak": 50, "bolkor": 50,
    "yirkon": 40, "keeper": 40, "akkel": 40, "nayark": 40, "amalgamated": 40,
    "zikkir": 30, "volgan": 30, "jorun": 30, "tarkin": 30, "sarcrina": 30,
    "karvaz": 25, "felroc": 25, "kretok": 25, "qsec": 25, "ormsul": 25,
    "gorganus": 20, "anvilfist": 20, "lacuste": 20, "sylvanna": 20,
}

DEFAULT_HP = {
    "anichaos": 5_000_000_000, "anipower": 5_000_000_000,
    "aniele": 5_000_000_000, "anivers": 5_000_000_000,
    "rezun": 10_000_000_000, "banok": 10_000_000_000, "envar": 10_000_000_000,
    "valzek": 20_000_000_000, "agnar": 20_000_000_000,
    "shayar": 20_000_000_000, "kinark": 20_000_000_000, "firan": 20_000_000_000,
    "arcon": 20_000_000_000, "holgor": 20_000_000_000,
    "villax": 50_000_000_000, "rillax": 50_000_000_000,
    "thanox": 50_000_000_000, "murfax": 50_000_000_000, "gregov": 50_000_000_000,
    "dexor": 75_000_000_000, "balerion": 75_000_000_000, "viserion": 75_000_000_000,
    "straya": 100_000_000_000, "skarthul": 100_000_000_000, "nafir": 150_000_000_000,
}


def _default_hp(short_name: str) -> int:
    return DEFAULT_HP.get(short_name, 0)


# Room IDs where each Prime God is located — characters must navigate here before raiding
GOD_ROOMS = {
    "Skarthul the Avenged":         23954,
    "Rillax, Twin of Wisdom":       38891,
    "Villax, Twin of Strength":     38892,
    "Akkel the Enflamed Warrior":   26318,
    "Amalgamated Apparition":       26323,
    "Ancient Magus Tarkin":         26295,
    "Anvilfist":                    9251,
    "Archdevil Yirkon":             26321,
    "Bolkor, the Holy Master":      26339,
    "Crolvak, the Fire Master":     26341,
    "Esquin, the Kinetic Master":   26342,
    "Felroc, Overseer of Hellfire": 20602,
    "Gorganus of the Wood":         9113,
    "Jorun the Blazing Swordsman":  26302,
    "Karvaz, Lord of Alsayic":      23061,
    "Keeper of Nature":             26320,
    "Kretok, Descendant of Nature": 19436,
    "Lacuste of the Swarm":         9190,
    "Nayark the Mummified Sorcerer":26324,
    "Ormsul the Putrid":            9072,
    "Q-SEC Commander":              11844,
    "Raiyar, the Shadow Master":    26343,
    "Sarcrina the Astral Priestess":26294,
    "Sylvanna TorLai":              8864,
    "Volgan the Living Ironbark":   26306,
    "Xynak, the Arcane Master":     26340,
    "Zikkir the Dark Archer":       26298,
    "Murfax, Beast of the Caves":   32630,
    "Valzek, Harbinger of Death":   42546,
    "Animation of Versatility":     42631,
    "Agnar, Astral Betrayer":       38898,
    "Holgor, the Holy Deity":       39422,
    "Arcon, the Arcane Deity":      39820,
    "Straya, the Underworld Ruler": 24791,
    "Kinark, the Kinetic Deity":    40828,
    "Shayar, the Shadow Deity":     41546,
    "Balerion, Dragon of Dread":    27461,
    "Dlanod, the Crazed Chancellor":25497,
    "Envar, Demon of Lunacy":       26243,
    "Rezun, Demon of Madness":      26276,
    "Thanox, Balancer of Chaos":    37797,
    "Nafir, God of Desolation":     26338,
    "Banok, Demon of Insanity":     26256,
    "Animation of Chaos":           43137,
    "Animation of Power":           42974,
    "Animation of Elements":        43282,
    "Firan, the Fire Deity":        40313,
    "Gregov, Knight of the Woods":  31954,
    "Dexor, Victor of Veldara":     31475,
    "Viserion, the Necrodragon":    27023,
}


async def _fetch_loot_page(session, loot_url: str, god_id: int) -> str:
    """
    Fetch prime god loot via the SSE stream endpoint.
    The loot data is streamed via /ajax/timedgod_loot_sse.php?spawnid=X
    Returns all event data concatenated as a string.
    """
    # Extract spawnid from loot_url e.g. primegod_loot?spawnid=388493775
    m = re.search(r"spawnid=(\d+)", loot_url)
    if not m:
        return ""
    spawnid = m.group(1)

    all_cookies = {
        k: v.value
        for k, v in session._session.cookie_jar.filter_cookies(
            "https://sigil.outwar.com"
        ).items()
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
        "Referer": f"https://sigil.outwar.com/primegod_loot?spawnid={spawnid}",
    }

    sse_url = f"https://sigil.outwar.com/ajax/timedgod_loot_sse.php?spawnid={spawnid}&envoyid=0"
    events = []

    try:
        async with aiohttp.ClientSession(headers=headers, cookies=all_cookies) as fresh:
            async with fresh.get(sse_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                async for line in resp.content:
                    line = line.decode("utf-8", errors="replace").strip()
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "END-OF-STREAM":
                            break
                        events.append(data)
    except Exception as e:
        logger.warning("RAID", f"SSE fetch error: {e}")

    if events:
        pass

    return "\n".join(events)


def _coarse_time(secs) -> str:
    """Round remaining seconds to a clean 'X hours' / 'X minutes' label for the list."""
    if secs is None:
        return "—"
    if secs >= 3600:
        h = round(secs / 3600)
        return f"{h} hour{'s' if h != 1 else ''}"
    if secs >= 60:
        m = round(secs / 60)
        return f"{m} minute{'s' if m != 1 else ''}"
    return "<1 minute"


class _GodStatsSelect(discord.ui.Select):
    """Dropdown of spawned Prime Gods; selecting one posts that god's live stats
    privately (ephemeral) to whoever selected it."""

    def __init__(self, cog, gods: list[dict]):
        self.cog     = cog
        self.god_map = {str(g["god_id"]): g for g in gods}
        options = [
            discord.SelectOption(
                label=g["name"][:100],
                description=f"{g['coarse']} remaining"[:100],
                value=str(g["god_id"]),
            )
            for g in gods[:25]   # Discord caps selects at 25 options
        ]
        super().__init__(
            placeholder="Select a Prime God for live stats…",
            min_values=1, max_values=1, options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        god = self.god_map.get(self.values[0], {})
        try:
            embed = await self.cog._build_god_stats_embed(god)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Couldn't fetch stats: `{e}`", ephemeral=True)


class _GodUpView(discord.ui.View):
    def __init__(self, cog, gods: list[dict], timeout: float = 600):
        super().__init__(timeout=timeout)
        self.message = None
        self.add_item(_GodStatsSelect(cog, gods))

    async def on_timeout(self):
        # Grey out the dropdown once it expires so it's clear it's stale.
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class RaidCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._gods_cache = []

    @property
    def session(self):
        return self.bot.outwar

    # ------------------------------------------------------------------
    # !primeupdate
    # ------------------------------------------------------------------

    @commands.command(name="primeupdate")
    async def prime_update(self, ctx):
        """One-time base setup. Re-run only when new gods are added."""
        await ctx.send("🔍 Scraping Prime Gods page... this may take a minute.")

        try:
            html = await self.session.get("primegods")
        except Exception as e:
            await ctx.send(f"❌ Failed to fetch Prime Gods page: {e}")
            return

        gods = parse_gods(html)
        if not gods:
            await ctx.send("⚠️ No gods found. The page structure may have changed.")
            return

        await ctx.send(f"Found **{len(gods)}** gods. Fetching individual pages...")

        semaphore = asyncio.Semaphore(5)

        async def _fetch_god_page(god) -> dict:
            async with semaphore:
                existing = db.get_prime_god(god.name)
                entry = {
                    "god_id":             god.god_id,
                    "name":               god.name,
                    "short_name":         god.short_name,
                    "spawned":            god.spawned,
                    "stats_url":          getattr(god, "stats_url", ""),
                    "hp":                 existing.get("hp") if existing and existing.get("hp_custom") else _default_hp(god.short_name),
                    "recommended":        existing.get("recommended") if existing and existing.get("recommended_custom") else DEFAULT_RECOMMENDED.get(god.short_name, 50),
                    "max_members":        existing.get("max_members") if existing else None,
                    "recommended_custom": existing.get("recommended_custom", False) if existing else False,
                    "hp_custom":          existing.get("hp_custom", False) if existing else False,
                }
                try:
                    god_html = await self.session.get(f"primegods?mobid={god.god_id}")
                    page_data = parse_prime_god_page(god_html)
                    if page_data.get("max_members") and not entry["recommended_custom"]:
                        entry["max_members"] = page_data["max_members"]
                        entry["recommended"] = page_data["max_members"]
                    for key in ("atk", "ele_dmg", "spawn_chance"):
                        if page_data.get(key):
                            entry[key] = page_data[key]
                except Exception as e:
                    logger.warning("RAID", f"Could not fetch god page for {god.name}: {e}")
                return entry

        entries = await asyncio.gather(*[_fetch_god_page(g) for g in gods])

        added = 0
        updated = 0
        for entry in entries:
            existing = db.get_prime_god(entry["name"])
            db.upsert_prime_god(entry)
            if existing:
                updated += 1
            else:
                added += 1

        self._gods_cache = db.get_prime_gods()
        spawned = [g for g in self._gods_cache if g.get("spawned")]
        dead = [g for g in self._gods_cache if not g.get("spawned")]

        embed = es.report_embed(
            f"{es.ICON_REPORT} Prime Gods Database Built",
            description=(
                f"**{len(gods)}** gods found\n"
                f"**{added}** new gods added, **{updated}** updated\n"
                f"**{len(spawned)}** currently spawned, **{len(dead)}** dead\n\n"
                f"Use `!gods` to see spawn status or `!god-list` for full details."
            )
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # !gods
    # ------------------------------------------------------------------

    @commands.command(name="gods")
    async def list_gods(self, ctx):
        """Show currently spawned Prime Gods."""
        gods = db.get_prime_gods()
        spawned = [g for g in gods if g.get("spawned")]
        if not spawned:
            await ctx.send("No Prime Gods are currently spawned.")
            return
        from outwar.table_image import render_gods_table
        buf = render_gods_table(spawned, spawned_only=True)
        await ctx.send(file=discord.File(buf, filename="gods.png"))

    # ------------------------------------------------------------------
    # !up
    # ------------------------------------------------------------------

    @commands.command(name="up")
    async def gods_up(self, ctx):
        """Show all currently spawned Prime Gods with time remaining, plus a dropdown for live stats."""
        msg = await ctx.send("🔍 Checking Prime Gods...")

        html = await self.session.get("primegods")
        gods = parse_gods(html)
        spawned = [g for g in gods if g.spawned]

        if not spawned:
            await msg.delete()
            await ctx.send("No Prime Gods are currently spawned.")
            return

        semaphore = asyncio.Semaphore(5)

        async def _fetch_time(god):
            async with semaphore:
                try:
                    god_html = await self.session.get(f"primegods?mobid={god.god_id}")
                    data = parse_prime_god_page(god_html)
                    god_db = db.get_prime_god(god.name) or {}
                    return {
                        "name":        god.name,
                        "god_id":      god.god_id,
                        "remaining":   data.get("time_remaining_secs"),
                        "coarse":      _coarse_time(data.get("time_remaining_secs")),
                        "max_members": data.get("max_members") or god_db.get("max_members"),
                        "rec_power":   god_db.get("rec_power"),
                        "rec_ele":     god_db.get("rec_ele"),
                        "rec_chaos":   god_db.get("rec_chaos"),
                        "recommended": god_db.get("recommended"),
                    }
                except Exception:
                    return {
                        "name": god.name, "god_id": god.god_id, "remaining": None,
                        "coarse": "—", "max_members": None, "rec_power": None,
                        "rec_ele": None, "recommended": None,
                    }

        results = await asyncio.gather(*[_fetch_time(g) for g in spawned])
        results.sort(key=lambda x: x["remaining"] if x["remaining"] is not None else 999999)

        lines = "\n".join(f"{r['name']} · {r['coarse']}" for r in results)
        embed = es.info_embed(f"{es.ICON_STATS} Prime Gods Up ({len(results)})")
        embed.url = "https://sigil.outwar.com/primegods"
        embed.description = lines[:4096]
        embed.set_footer(text="Select a god below for live kills & rec stats (only you'll see it)")

        view = _GodUpView(self, results)
        await msg.delete()
        view.message = await ctx.send(embed=embed, view=view)

    async def _build_god_stats_embed(self, god: dict) -> discord.Embed:
        """Build the per-god live stats embed shown when a god is picked from the dropdown."""
        god_id = god.get("god_id")
        name   = god.get("name", f"God {god_id}")

        god_html = await self.session.get(f"primegods?mobid={god_id}")
        data = parse_prime_god_page(god_html)

        embed = es.info_embed(name)
        embed.url = f"https://sigil.outwar.com/primegods?mobid={god_id}"

        if data.get("time_remaining_secs") is not None:
            embed.description = f"Remaining time: {format_time_remaining(data['time_remaining_secs'])}"

        stats = data.get("stats", [])
        if stats:
            lines = ""
            for i, s in enumerate(stats, 1):
                kl = "Kills" if s["kills"] != 1 else "Kill"
                lines += f"{i}. {s['crew']} · {s['kills']} {kl} ({s['pct']}%)\n"
            embed.add_field(name="Crew Kills", value=lines[:1024], inline=False)
        else:
            embed.add_field(name="Crew Kills", value="No kills yet this spawn.", inline=False)

        # Max members + recommended stats footer block
        mm = data.get("max_members") or god.get("max_members")
        rp = god.get("rec_power")
        re_ = god.get("rec_ele")
        rc = god.get("rec_chaos")
        info = []
        if mm:
            info.append(f"Max members: {mm}")
        if rp or re_ or rc:
            rp_s = f"{rp:,}" if rp else "—"
            re_s = f"{re_:,}" if re_ else "—"
            rec_line = f"Rec Pwr: {rp_s}, Rec Ele: {re_s}"
            if rc:
                rec_line += f", Rec Chaos: {rc:,}"
            info.append(rec_line)
        if info:
            embed.add_field(name="\u200b", value="\n".join(info), inline=False)

        return embed

    # ------------------------------------------------------------------
    # !god <name>
    # ------------------------------------------------------------------

    @commands.command(name="god")
    async def god_info(self, ctx, *, name: str):
        """Show full details for a specific Prime God."""
        from outwar.table_image import render_table, TEXT_GREEN, TEXT_RED, TEXT_GOLD, TEXT_WHITE, TEXT_DIM, TEXT_BLUE
        god = db.get_prime_god(name)
        if not god:
            await ctx.send(f"God `{name}` not found. Try `!god-list`.")
            return

        win_stats = db.get_raid_win_stats(god.get("god_id"))

        def _fmt(n):
            if not n: return "—"
            return f"{n:,}"

        columns = [
            {"key": "label", "label": "Stat",  "align": "left",  "color_fn": lambda r: (186, 190, 240)},
            {"key": "value", "label": "Value", "align": "right", "color_fn": lambda r: r.get("color", TEXT_WHITE)},
        ]
        rows = [
            {"label": "Status",       "value": "SPAWNED" if god.get("spawned") else "DEAD",   "color": TEXT_GREEN if god.get("spawned") else TEXT_RED},
            {"label": "Alias",        "value": god.get("short_name") or "—",                  "color": TEXT_BLUE},
            {"label": "Recommended",  "value": f"{god.get('recommended', '—')} members",      "color": TEXT_WHITE},
            {"label": "Max Members",  "value": _fmt(god.get("max_members")),                  "color": TEXT_WHITE},
            {"label": "ATK",          "value": _fmt(god.get("atk")),                          "color": TEXT_WHITE},
            {"label": "Ele Dmg",      "value": _fmt(god.get("ele_dmg")),                      "color": TEXT_GREEN},
            {"label": "Rec. Power",   "value": _fmt(god.get("rec_power")),                    "color": TEXT_WHITE},
            {"label": "Rec. Ele",     "value": _fmt(god.get("rec_ele")),                      "color": TEXT_GREEN},
            {"label": "Rec. Chaos",   "value": _fmt(god.get("rec_chaos")),                    "color": TEXT_GOLD},
        ]
        if win_stats:
            rows += [
                {"label": "Wins Recorded",  "value": str(win_stats["wins"]),              "color": TEXT_GOLD},
                {"label": "Min Power (win)","value": _fmt(win_stats.get("min_power")),    "color": TEXT_WHITE},
                {"label": "Min Ele (win)",  "value": _fmt(win_stats.get("min_ele")),      "color": TEXT_GREEN},
            ]

        buf = render_table(god["name"], "SPAWNED" if god.get("spawned") else "DEAD", columns, rows, "")
        await ctx.send(file=discord.File(buf, filename="god_info.png"))

    # ------------------------------------------------------------------
    # !god-set
    # ------------------------------------------------------------------

    @commands.command(name="god-set")
    async def god_set(self, ctx, name: str, field: str, *, value: str):
        """Update a field on a god. Fields: recommended, hp, short_name, rec_power, rec_ele, rec_chaos"""
        valid_fields = ("recommended", "hp", "short_name", "rec_power", "rec_ele", "rec_chaos")
        field = field.lower()
        if field not in valid_fields:
            await ctx.send(f"Unknown field `{field}`. Valid: {', '.join(valid_fields)}")
            return

        god = db.get_prime_god(name)
        if not god:
            await ctx.send(f"God `{name}` not found.")
            return

        try:
            if field in ("recommended", "hp", "rec_power", "rec_ele", "rec_chaos"):
                value = int(value.replace(",", ""))
            updates = {field: value}
            if field == "recommended":
                updates["recommended_custom"] = True
            if field == "hp":
                updates["hp_custom"] = True
            if field == "rec_power":
                updates["rec_power_custom"] = True
            if field == "rec_ele":
                updates["rec_ele_custom"] = True
            if field == "rec_chaos":
                updates["rec_chaos_custom"] = True
        except ValueError:
            await ctx.send(f"`{field}` must be a number.")
            return

        db.update_prime_god(god["god_id"], updates)
        await ctx.send(f"✅ **{god['name']}** — `{field}` updated to `{value:,}`" if isinstance(value, int) else f"✅ **{god['name']}** — `{field}` updated to `{value}`")

    # ------------------------------------------------------------------
    # !god-rec-import — bulk set rec power/ele/chaos from a pasted block
    # ------------------------------------------------------------------

    @commands.command(name="god-rec-import")
    async def god_rec_import(self, ctx, *, content: str):
        """Bulk-set rec power/ele/chaos from a stats block. Previews first; add 'apply' to commit.
        Usage: !god-rec-import [apply] <stats block>  (first num=power, second=ele, third=chaos)"""
        apply = False
        text  = content.strip()
        if text.lower().startswith("apply"):
            apply = True
            text  = text[5:].strip()
        if not text:
            await ctx.send("Paste the stats block after the command. Add `apply` (before the block) to commit.")
            return

        entries, skipped = parse_rec_stats_block(text)

        matched    = []   # (input_name, resolved_name, god_id, power, ele, chaos)
        unresolved = []
        for e in entries:
            for nm in e["names"]:
                god = db.get_prime_god(nm)
                if god:
                    matched.append((nm, god["name"], god["god_id"], e["power"], e["ele"], e["chaos"]))
                else:
                    unresolved.append(nm)

        if apply:
            count = 0
            for nm, rname, gid, p, el, c in matched:
                updates = {
                    "rec_power": p, "rec_power_custom": True,
                    "rec_ele":   el, "rec_ele_custom":  True,
                }
                if c is not None:
                    updates["rec_chaos"]        = c
                    updates["rec_chaos_custom"] = True
                db.update_prime_god(gid, updates)
                count += 1
            msg = f"✅ Updated **{count}** god(s)."
            if unresolved:
                msg += "\n⚠️ Unresolved (set manually): " + ", ".join(f"`{u}`" for u in unresolved)
            if skipped:
                msg += "\n⏭️ Skipped:\n" + "\n".join(f"• `{r[:55]}` — {why}" for r, why in skipped)
            await ctx.send(msg[:1950])
            return

        # Preview (no changes made)
        embed = es.info_embed(
            "Rec Stats Import — Preview",
            f"**{len(matched)}** god(s) will update · "
            f"**{len(unresolved)}** unresolved · **{len(skipped)}** line(s) skipped",
        )
        lines = ""
        for nm, rname, gid, p, el, c in matched:
            cc   = f" / {c:,}c" if c is not None else ""
            line = f"{rname}: {p:,} / {el:,}{cc}\n"
            if len(lines) + len(line) > 1000:
                embed.add_field(name="Will update", value=lines, inline=False)
                lines = ""
            lines += line
        if lines:
            embed.add_field(name="Will update", value=lines, inline=False)
        if unresolved:
            embed.add_field(name="Unresolved names (set manually)",
                            value=", ".join(f"`{u}`" for u in unresolved)[:1024], inline=False)
        if skipped:
            embed.add_field(name="Skipped lines",
                            value="\n".join(f"`{r[:48]}` — {why}" for r, why in skipped)[:1024], inline=False)
        embed.set_footer(text="Verify, then re-run with: !god-rec-import apply <block>")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # !god-list
    # ------------------------------------------------------------------

    @commands.command(name="god-list")
    async def god_list(self, ctx):
        """Full Prime God reference — alias, recommended size, rec power/ele/chaos."""
        from outwar.table_image import render_table
        gods = db.get_prime_gods()
        if not gods:
            await ctx.send("No gods in database. Run `!primeupdate` first.")
            return

        def _fmt(n):
            if not n: return "—"
            if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
            if n >= 1_000: return f"{n/1_000:.0f}K"
            return str(n)

        def _fmt_plain(n):
            # Chaos is a small literal value — show it in full, not as K
            return f"{n:,}" if n else "—"

        TEXT_BLUE  = (100, 160, 255)
        TEXT_WHITE = (235, 237, 255)
        TEXT_GREEN = (52, 211, 153)
        TEXT_DIM   = (100, 105, 140)
        TEXT_GOLD  = (251, 191, 36)
        TEXT_PURPLE = (167, 139, 250)

        columns = [
            {"key": "name",   "label": "God",       "align": "left",   "color_fn": None},
            {"key": "alias",  "label": "Alias",     "align": "left",   "color_fn": lambda r: TEXT_BLUE},
            {"key": "rec",    "label": "Rec",       "align": "center", "color_fn": lambda r: TEXT_DIM},
            {"key": "power",  "label": "Rec Power", "align": "right",  "color_fn": lambda r: TEXT_WHITE},
            {"key": "ele",    "label": "Rec Ele",   "align": "right",  "color_fn": lambda r: TEXT_GREEN},
            {"key": "chaos",  "label": "Rec Chaos", "align": "right",  "color_fn": lambda r: TEXT_PURPLE},
        ]

        gods_sorted = sorted(gods, key=lambda x: x.get("name", ""))

        # Paginate so each image stays large and legible instead of one tall, tiny table
        PAGE = 22
        pages = [gods_sorted[i:i + PAGE] for i in range(0, len(gods_sorted), PAGE)]
        for idx, page in enumerate(pages):
            rows = [
                {
                    "name":  g.get("name", "—"),
                    "alias": g.get("short_name") or "—",
                    "rec":   str(g.get("recommended", "—")),
                    "power": _fmt(g.get("rec_power")),
                    "ele":   _fmt(g.get("rec_ele")),
                    "chaos": _fmt_plain(g.get("rec_chaos")),
                }
                for g in page
            ]
            subtitle = f"{len(gods)} gods" + (f" · page {idx + 1}/{len(pages)}" if len(pages) > 1 else "")
            buf = render_table(
                "PRIME GOD REFERENCE", subtitle, columns, rows,
                "Use !god <name> for full details",
            )
            await ctx.send(file=discord.File(buf, filename=f"god_list_{idx + 1}.png"))

    @commands.command(name="god-export")
    async def god_export(self, ctx):
        """Export every god's live recommended power/ele/chaos to a .txt file."""
        gods = db.get_prime_gods()
        if not gods:
            await ctx.send("No gods in database. Run `!primeupdate` first.")
            return

        rows = sorted(
            gods,
            key=lambda g: (g.get("rec_power") or 0, g.get("rec_ele") or 0, g.get("name", "")),
        )
        lines = [
            "DeathBot — Prime God Recommended Stats",
            "Power / Ele / Chaos   (chaos shown only where set)",
            "=" * 60, "",
        ]
        name_w = max((len(g.get("name", "")) for g in rows), default=10) + 2
        count = 0
        for g in rows:
            p, e, c = g.get("rec_power"), g.get("rec_ele"), g.get("rec_chaos")
            if not (p or e or c):
                continue  # skip gods with no recs set
            p_s = f"{p:,}" if p else "—"
            e_s = f"{e:,}" if e else "—"
            c_s = f"{c:,}" if c else "—"
            lines.append(f"{g.get('name', '—'):<{name_w}}{p_s:>9} / {e_s:>8} / {c_s:>6}")
            count += 1
        lines += ["", f"{count} gods · generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

        import io
        buf = io.BytesIO("\n".join(lines).encode("utf-8"))
        await ctx.send(
            content=f"📄 Recommended stats for **{count}** gods:",
            file=discord.File(buf, filename="god_recommended_stats.txt"),
        )

    # ------------------------------------------------------------------
    # !beatable — which gods a group's AVERAGE stats can beat
    # ------------------------------------------------------------------

    @commands.command(name="beatable", aliases=["can-beat", "canbeat"])
    async def beatable(self, ctx, *, group: str):
        """Which gods a group can beat. Default: group AVERAGE meets rec.
        Add 'strict' to require EVERY member to meet rec. Usage: !beatable <group> [strict]"""
        from outwar.scraper import parse_character_stats_profile
        from outwar.table_image import render_table, TEXT_GREEN, TEXT_RED, TEXT_WHITE, TEXT_GOLD, TEXT_DIM
        import io

        # Optional trailing 'strict' flag
        strict = False
        toks = group.split()
        if toks and toks[-1].lower().lstrip("-") == "strict":
            strict = True
            group = " ".join(toks[:-1]).strip()

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return
        raiders = sum(1 for t in trustees if t.get("suid"))  # accounts that can actually raid
        gods = db.get_prime_gods()
        if not gods:
            await ctx.send("No gods in database. Run `!primeupdate` first.")
            return

        mode = "strict — all members" if strict else "average"
        msg = await ctx.send(f"⏳ Reading stats for **{len(trustees)}** characters in **{group}** ({mode})…")

        sem = asyncio.Semaphore(8)

        async def _fetch(t):
            suid = t.get("suid")
            if not suid:
                return None
            try:
                async with sem:
                    html = await self.session.get_as("profile", suid)
                s = parse_character_stats_profile(html)
                return (s.get("power", 0) or 0, s.get("elemental", 0) or 0, s.get("chaos", 0) or 0)
            except Exception:
                return None

        stats = [r for r in await asyncio.gather(*[_fetch(t) for t in trustees]) if r]
        if not stats:
            await msg.delete()
            await ctx.send("Couldn't read stats for that group.")
            return

        n = len(stats)
        avg_p = sum(r[0] for r in stats) // n
        avg_e = sum(r[1] for r in stats) // n
        avg_c = sum(r[2] for r in stats) // n
        min_p, min_e, min_c = min(r[0] for r in stats), min(r[1] for r in stats), min(r[2] for r in stats)

        evaluable = []
        for g in gods:
            rp, re_, rc = g.get("rec_power"), g.get("rec_ele"), g.get("rec_chaos")
            if not (rp and re_):
                continue
            qualify = sum(1 for p, e, c in stats if p >= rp and e >= re_ and (not rc or c >= rc))
            stat_ok = (qualify == n) if strict else ((avg_p >= rp) and (avg_e >= re_) and (not rc or avg_c >= rc))
            need = g.get("max_members")
            members_ok = (need is None) or (raiders >= need)
            ok = stat_ok and members_ok
            evaluable.append((g, rp, re_, rc, ok, qualify, need))

        if not evaluable:
            await msg.delete()
            await ctx.send("No gods have recommended power/ele set yet. Run `!god-rec-import` first.")
            return

        evaluable.sort(key=lambda x: (x[1], x[2]))  # by rec power, then rec ele

        def _fmt(v):
            if not v:
                return "—"
            if v >= 1000:
                return f"{v/1000:.0f}k" if v % 1000 == 0 else f"{v/1000:.1f}k"
            return str(v)

        def _mem_color(r):
            nd = r["need"]
            if not nd:
                return TEXT_DIM
            if r["msz"] < nd:
                return TEXT_RED      # too few
            if r["msz"] > nd:
                return TEXT_GOLD     # too many (works, excess idle)
            return TEXT_GREEN        # exact

        columns = [
            {"key": "god",     "label": "God",       "align": "left",   "color_fn": lambda r: TEXT_GREEN if r["ok"] else TEXT_DIM},
            {"key": "rp",      "label": "Rec Power", "align": "right",  "color_fn": lambda r: TEXT_WHITE},
            {"key": "re",      "label": "Rec Ele",   "align": "right",  "color_fn": lambda r: TEXT_WHITE},
            {"key": "rc",      "label": "Rec Chaos", "align": "right",  "color_fn": lambda r: TEXT_GOLD},
            {"key": "mem",     "label": "Members",   "align": "center", "color_fn": _mem_color},
            {"key": "qual",    "label": "Qualify",   "align": "center", "color_fn": lambda r: TEXT_GREEN if r["q"] == n else (TEXT_GOLD if r["q"] else TEXT_RED)},
            {"key": "verdict", "label": "Beat?",     "align": "center", "color_fn": lambda r: TEXT_GREEN if r["ok"] else TEXT_RED},
        ]
        rows = [{
            "god": g.get("name", "—"),
            "rp": _fmt(rp), "re": _fmt(re_), "rc": _fmt(rc) if rc else "—",
            "mem": f"{raiders}/{need}" if need else f"{raiders}/?",
            "qual": f"{q}/{n}",
            "verdict": "YES" if ok else "NO",
            "ok": ok, "q": q, "need": need, "msz": raiders,
        } for g, rp, re_, rc, ok, q, need in evaluable]

        can = sum(1 for *_r, ok, _q, _need in evaluable if ok)
        mem_note = "Members shows raiders/needed (red=too few, gold=too many)"
        if strict:
            agg = f"Min Power {_fmt(min_p)} · Ele {_fmt(min_e)} · Chaos {_fmt(min_c)}"
            foot = f"STRICT: every member meets rec · {mem_note}"
            title = f"BEATABLE (STRICT) — {group.upper()}"
        else:
            agg = f"Avg Power {_fmt(avg_p)} · Ele {_fmt(avg_e)} · Chaos {_fmt(avg_c)}"
            foot = f"AVERAGE: group mean meets rec · {mem_note}"
            title = f"BEATABLE — {group.upper()}"
        subtitle = f"{agg}  ·  {raiders} raiders  ·  beats {can}/{len(evaluable)}"
        buf = render_table(title, subtitle, columns, rows, foot)
        await msg.delete()
        await ctx.send(file=discord.File(buf, filename="beatable.png"))

    # ------------------------------------------------------------------
    # !prime-stats / !ps
    # ------------------------------------------------------------------

    @commands.command(name="prime-stats", aliases=["ps"])
    async def prime_stats(self, ctx, *, god_name: str):
        """Show current spawn stats for a Prime God. Alias: !ps"""
        god = db.get_prime_god(god_name)
        if not god:
            await ctx.send(f"God `{god_name}` not found. Try `!god-list`.")
            return

        await ctx.send(f"Fetching stats for **{god['name']}**...")
        god_html = await self.session.get(f"primegods?mobid={god['god_id']}")
        data = parse_prime_god_page(god_html)

        embed = es.info_embed(f"{es.ICON_STATS} {god['name']} — Spawn Stats")
        embed.timestamp = datetime.now(timezone.utc)

        info_parts = []
        if data.get("atk"):
            info_parts.append(f"**ATK:** {data['atk']:,}")
        if data.get("ele_dmg"):
            info_parts.append(f"**Ele:** {data['ele_dmg']:,}")
        if data.get("max_members"):
            info_parts.append(f"**Max Members:** {data['max_members']}")
        if data.get("spawn_chance"):
            info_parts.append(f"**Spawn Chance:** {data['spawn_chance']}%")
        if info_parts:
            embed.add_field(name="God Info", value="\n".join(info_parts), inline=False)

        if data.get("spawned") and data.get("time_remaining_secs") is not None:
            embed.add_field(name="⏱️ Time Remaining", value=format_time_remaining(data["time_remaining_secs"]), inline=False)

        stats = data.get("stats", [])
        if stats:
            lines = ""
            for i, s in enumerate(stats, 1):
                lines += f"**{i}.** {s['crew']} — {s['kills']} kill{'s' if s['kills'] != 1 else ''} ({s['pct']}%)\n"
                if len(lines) > 900:
                    embed.add_field(name="Crew Stats", value=lines, inline=False)
                    lines = ""
            if lines:
                embed.add_field(name="Crew Stats", value=lines, inline=False)
        else:
            embed.add_field(name="Crew Stats", value="No kills yet this spawn.", inline=False)

        if data.get("loot_url"):
            embed.add_field(name="Drops", value=f"[View Loot](https://sigil.outwar.com/{data['loot_url']})", inline=False)

        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # !prime-drops / !pd
    # ------------------------------------------------------------------

    @commands.command(name="prime-drops", aliases=["pd"])
    async def prime_drops(self, ctx, *, god_name: str):
        """Show drop table for a Prime God. Alias: !pd"""
        god = db.get_prime_god(god_name)
        if not god:
            await ctx.send(f"God `{god_name}` not found. Try `!god-list`.")
            return

        await ctx.send(f"Fetching drops for **{god['name']}**...")
        god_html = await self.session.get(f"primegods?mobid={god['god_id']}")
        data = parse_prime_god_page(god_html)

        if not data.get("loot_url"):
            await ctx.send(f"No loot data found for **{god['name']}**. The god may not have been killed yet.")
            return

        try:
            loot_html = await _fetch_loot_page(self.session, data["loot_url"], god["god_id"])
        except Exception as e:
            await ctx.send(f"Error fetching loot page: {e}")
            return

        loot_by_crew = parse_prime_god_loot(loot_html)

        if not loot_by_crew:
            await ctx.send(f"No drop data found for **{god['name']}**.")
            return

        embed = es.drops_embed(f"{es.ICON_DROPS} {god['name']} — Drops")
        embed.timestamp = datetime.now(timezone.utc)

        for entry in loot_by_crew:
            if not entry["items"]:
                continue
            embed.add_field(
                name=entry["crew"],
                value="\n".join(entry["items"])[:1024],
                inline=True
            )
            if len(embed.fields) == 24:
                await ctx.send(embed=embed)
                embed = es.drops_embed(f"{es.ICON_DROPS} {god['name']} — Drops (continued)")

        if embed.fields:
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"No items dropped for **{god['name']}**.")

    # ------------------------------------------------------------------
    # !rm — hit a prime god once
    # ------------------------------------------------------------------

    async def _is_god_spawned_live(self, god: dict) -> bool:
        """
        Check a Prime God's LIVE spawn status from the game rather than trusting the
        stored `spawned` flag, which goes stale between !primeupdate runs / monitor
        polls (a god can spawn in-game while the DB still reads dead). Fetches the
        god's own page and reads the real status, and syncs the stored flag so other
        views agree. Falls back to the stored flag only if the live fetch fails.
        """
        god_id = god.get("god_id")
        if not god_id:
            return bool(god.get("spawned"))
        try:
            html    = await self.session.get(f"primegods?mobid={god_id}")
            data    = parse_prime_god_page(html)
            spawned = bool(data.get("spawned"))
            try:
                db.update_prime_god(god_id, {"spawned": spawned})
            except Exception:
                pass
            return spawned
        except Exception as e:
            logger.warning("RAID", f"Live spawn check failed for {god.get('name')}: {e}")
            return bool(god.get("spawned"))

    @commands.command(name="rm")
    async def raid_mob_once(self, ctx, group: str, god_name: str):
        """Hit a Prime God once with a group. Usage: !rm <group> <god>"""
        god = db.get_prime_god(god_name)
        if not god:
            await ctx.send(f"God `{god_name}` not found. Try `!god-list`.")
            return

        if not await self._is_god_spawned_live(god):
            await ctx.send(f"**{god['name']}** is not currently spawned.")
            return

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        won, damage, note = await self._do_god_raid(ctx, god, trustees)

        # Determine if raid was blocked vs actually attempted
        raid_blocked = not won and note and ("capped" in note or "available" in note or "low rage" in note or "Could not" in note)

        embed = discord.Embed(
            title=f"{'🏆 Win!' if won else ('⛔ Raid Not Formed' if raid_blocked else '⚔️ Attempt Complete')} — {god['name']}",
            color=discord.Color.gold() if won else (discord.Color.dark_grey() if raid_blocked else discord.Color.blue())
        )
        embed.add_field(name="Characters sent", value=str(len(trustees)), inline=True)
        embed.add_field(name="Result", value="Won!" if won else ("Not formed" if raid_blocked else "Did not win"), inline=True)
        if damage:
            embed.add_field(name="Damage", value=f"{damage:,}", inline=True)
        if note:
            embed.add_field(name="Notes", value=note, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="rmdebug")
    async def raid_mob_debug(self, ctx, group: str, god_name: str):
        """Like !rm but posts each internal raid step, to diagnose raids that don't fire."""
        god = db.get_prime_god(god_name)
        if not god:
            await ctx.send(f"God `{god_name}` not found.")
            return
        spawned = await self._is_god_spawned_live(god)
        trustees = self._resolve_group(group)
        await ctx.send(
            f"🔬 Debug raid on **{god['name']}** with **{group}** "
            f"({len(trustees)} chars, spawned={spawned})…"
        )
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return
        debug_log = []
        try:
            won, damage, note = await self._do_god_raid(ctx, god, trustees, debug_log=debug_log)
            debug_log.append(f"RETURNED: won={won} damage={damage} note={note!r}")
        except Exception as e:
            debug_log.append(f"EXCEPTION: {e!r}")
        out = "\n".join(debug_log) or "no steps logged"
        for i in range(0, len(out), 1900):
            await ctx.send(f"```\n{out[i:i+1900]}\n```")

        # Attach the raw attack page so the HP/win parser can be tuned to the real format.
        attack_html = getattr(self, "_last_attack_html", None)
        if attack_html:
            import io
            await ctx.send(
                "Raw raidattack page (send me this if the HP%/win looks wrong):",
                file=discord.File(io.BytesIO(attack_html.encode("utf-8")), filename="raidattack.html"),
            )
            self._last_attack_html = None

    # ------------------------------------------------------------------
    # !rg — hit a prime god multiple times
    # ------------------------------------------------------------------

    @commands.command(name="rg")
    async def raid_god_multi(self, ctx, group: str, god_name: str, tries: int, wins: int = 1):
        """
        Raid a Prime God multiple times, stopping after N wins.
        Usage: !rg <group> <god> <tries> [wins]
        """
        if tries < 1 or tries > 100:
            await ctx.send("Number of tries must be between 1 and 100.")
            return
        if wins < 1:
            await ctx.send("Number of wins must be at least 1.")
            return

        god = db.get_prime_god(god_name)
        if not god:
            await ctx.send(f"God `{god_name}` not found. Try `!god-list`.")
            return

        if not await self._is_god_spawned_live(god):
            await ctx.send(f"**{god['name']}** is not currently spawned.")
            return

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        win_str = f"{wins} win{'s' if wins > 1 else ''}"
        await ctx.send(
            f"Checking caps for **{len(trustees)}** characters..."
        )

        # Upfront cap check — ensure group has enough caps for requested wins
        available, capped, cap_warn = await self._check_group_caps(trustees, wins)
        if capped:
            await ctx.send(f"⚠️ {len(capped)} capped: {', '.join(t['name'] for t in capped)}")
        if cap_warn:
            await ctx.send(cap_warn)

        await ctx.send(
            f"Raiding **{god['name']}** — up to **{tries}** tries, "
            f"stopping after **{win_str}** with **{len(available)}** characters..."
        )

        total_wins = 0
        total_attempts = 0
        total_damage = 0
        start_time = datetime.now()
        cached_caps: dict[str, bool] = {}  # name -> is_capped, persists across attempts

        for attempt in range(1, tries + 1):
            # Only check spawn every 5 attempts to avoid extra fetches
            if attempt == 1 or attempt % 5 == 0:
                html = await self.session.get("primegods")
                live_gods = parse_gods(html)
                live_god = next((g for g in live_gods if g.god_id == god.get("god_id")), None)
                if not live_god or not live_god.spawned:
                    await ctx.send(f"**{god['name']}** is no longer spawned. Stopping after {total_attempts} attempts.")
                    break

            total_attempts += 1
            won, damage, note = await self._do_god_raid(ctx, god, trustees, cap_cache=cached_caps)
            if damage:
                total_damage += damage

            if won:
                total_wins += 1
                msg = f"🏆 **Win {total_wins}/{wins}** on attempt {attempt}!"
                if damage:
                    msg += f" Damage: {damage:,}"
                if note:
                    msg += f"\n_{note}_"
                await ctx.send(msg)
                if total_wins >= wins:
                    await ctx.send(f"✅ Reached **{wins}** win(s). Stopping.")
                    break
            else:
                msg = f"Attempt {attempt}/{tries} — no win."
                if damage:
                    msg += f" Damage: {damage:,}"
                if note:
                    msg += f"\n_{note}_"
                await ctx.send(msg)

            if attempt < tries:
                await asyncio.sleep(3)

        elapsed    = (datetime.now() - start_time).seconds
        win_rate   = f"{(total_wins/total_attempts*100):.0f}%" if total_attempts > 0 else "0%"
        mins, secs = divmod(elapsed, 60)
        elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"

        from outwar.table_image import render_raid_summary
        buf = render_raid_summary(god["name"], {
            "group":        group,
            "attempts":     total_attempts,
            "wins":         total_wins,
            "win_rate":     win_rate,
            "total_damage": total_damage,
            "elapsed":      elapsed_str,
            "target_wins":  wins,
        })
        await ctx.send(file=discord.File(buf, filename="raid_summary.png"))

    @commands.command(name="rq")
    async def raid_queue(self, ctx, group: str, tries: int, wins: int, *, gods_str: str):
        """
        Raid multiple gods in sequence with the same group.
        Usage: !rq <group> <tries> <wins> <god1,god2,god3>
        Example: !rq lod1 5 2 zikkir,firan,rezun
        """
        if tries < 1 or tries > 50:
            await ctx.send("Tries must be between 1 and 50.")
            return
        if wins < 1:
            await ctx.send("Wins must be at least 1.")
            return

        god_names = [g.strip() for g in gods_str.replace(" ", ",").split(",") if g.strip()]
        if not god_names:
            await ctx.send("No gods specified.")
            return

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        # Resolve all gods upfront
        gods = []
        for gname in god_names:
            god = db.get_prime_god(gname)
            if not god:
                await ctx.send(f"⚠️ God `{gname}` not found — skipping.")
                continue
            if not god.get("spawned"):
                await ctx.send(f"⚠️ **{god['name']}** is not spawned — skipping.")
                continue
            gods.append(god)

        if not gods:
            await ctx.send("No valid spawned gods in queue.")
            return

        await ctx.send(
            f"📋 Checking caps for **{len(trustees)}** characters across **{len(gods)}** gods..."
        )

        # Upfront cap check — each account needs enough caps for all gods × wins
        total_caps_needed = len(gods) * wins
        available, capped, cap_warn = await self._check_group_caps(trustees, total_caps_needed)
        if capped:
            await ctx.send(f"⚠️ {len(capped)} capped: {', '.join(t['name'] for t in capped)}")
        if cap_warn:
            await ctx.send(cap_warn)

        await ctx.send(
            f"📋 Queue: **{len(gods)}** gods · **{tries}** tries · **{wins}** win(s) each · **{len(available)}** characters\n"
            f"Order: {' → '.join(g['name'] for g in gods)}"
        )

        queue_start = datetime.now()
        queue_wins  = 0
        queue_total = 0

        for god in gods:
            await ctx.send(f"▶️ Starting **{god['name']}**...")
            total_wins = 0
            total_attempts = 0
            total_damage = 0
            cached_caps: dict[str, bool] = {}
            start_time = datetime.now()

            for attempt in range(1, tries + 1):
                if attempt == 1 or attempt % 5 == 0:
                    html = await self.session.get("primegods")
                    live_gods = parse_gods(html)
                    live_god = next((g for g in live_gods if g.god_id == god.get("god_id")), None)
                    if not live_god or not live_god.spawned:
                        await ctx.send(f"**{god['name']}** is no longer spawned.")
                        break

                total_attempts += 1
                won, damage, note = await self._do_god_raid(ctx, god, trustees, cap_cache=cached_caps)
                if damage:
                    total_damage += damage

                if won:
                    total_wins += 1
                    msg = f"🏆 **{god['name']}** Win {total_wins}/{wins} on attempt {attempt}!"
                    if damage:
                        msg += f" Damage: {damage:,}"
                    await ctx.send(msg)
                    if total_wins >= wins:
                        await ctx.send(f"✅ **{god['name']}** done.")
                        break
                else:
                    msg = f"**{god['name']}** attempt {attempt}/{tries} — no win."
                    if note:
                        msg += f" _{note}_"
                    await ctx.send(msg)

                if attempt < tries:
                    await asyncio.sleep(2)

            elapsed = (datetime.now() - start_time).seconds
            queue_wins  += total_wins
            queue_total += total_attempts

            embed = discord.Embed(
                title=f"{'✅' if total_wins >= wins else '❌'} {god['name']} Complete",
                color=discord.Color.gold() if total_wins >= wins else discord.Color.red()
            )
            embed.add_field(name="Attempts", value=str(total_attempts), inline=True)
            embed.add_field(name="Wins",     value=f"{total_wins}/{wins}", inline=True)
            embed.add_field(name="Damage",   value=f"{total_damage:,}", inline=True)
            embed.add_field(name="Time",     value=f"{elapsed}s", inline=True)
            await ctx.send(embed=embed)

        total_elapsed = (datetime.now() - queue_start).seconds
        embed = discord.Embed(
            title="📋 Queue Complete",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Gods",          value=str(len(gods)),    inline=True)
        embed.add_field(name="Total Attempts",value=str(queue_total),  inline=True)
        embed.add_field(name="Total Wins",    value=str(queue_wins),   inline=True)
        embed.add_field(name="Total Time",    value=f"{total_elapsed}s", inline=True)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # !rg summary win rate
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _do_god_raid(self, ctx, god: dict, trustees: list, cap_cache: dict = None, debug_log: list = None) -> tuple[bool, int, str]:
        """Form and launch a raid on a Prime God. Returns (won, damage, note)."""
        def dbg(m):
            logger.debug("RAID", m)
            if debug_log is not None:
                debug_log.append(m)

        god_id = god.get("god_id")
        if not god_id:
            dbg("no god_id on god dict")
            return False, 0, None

        # Sort by rage descending — try highest rage first as former
        sorted_trustees = sorted(trustees, key=lambda t: t.get("rage", 0), reverse=True)
        if not sorted_trustees:
            return False, 0, None

        session = self.session
        room_id = GOD_ROOMS.get(god["name"])

        try:
            # Pre-flight cap check — use cache if available, only fetch uncached accounts
            from outwar.scraper import parse_god_cap
            cap_semaphore = asyncio.Semaphore(8)

            async def _check_cap(t):
                suid = t.get("suid")
                name = t["name"]
                # Use cache — if previously capped, still capped
                if cap_cache is not None and name in cap_cache:
                    return name, cap_cache[name], None, None
                if not suid:
                    return name, True, None, None
                async with cap_semaphore:
                    try:
                        html = await session.get_as("home", suid)
                        used, max_cap = parse_god_cap(html)
                        # 'God Cap: X/Y' on the home page is USED/MAX → available = max - used.
                        avail = (max_cap - used) if max_cap else 0
                        is_capped = max_cap > 0 and avail <= 0
                        if cap_cache is not None:
                            cap_cache[name] = is_capped
                        return name, is_capped, avail, max_cap
                    except Exception:
                        return name, False, None, None

            cap_results = await asyncio.gather(*[_check_cap(t) for t in sorted_trustees])
            capped_names = [name for name, is_capped, *_ in cap_results if is_capped]
            dbg(f"caps: {len(sorted_trustees) - len(capped_names)} available / {len(capped_names)} capped of {len(sorted_trustees)} | room_id={room_id}")
            for name, is_capped, cur, max_cap in cap_results:
                if cur is not None:
                    dbg(f"  cap {name}: {cur}/{max_cap} available → {'CAPPED' if is_capped else 'ok'}")

            # 1) CAP CHECK (strict): every account must have a cap available, or bail.
            if capped_names:
                return False, 0, f"Raid not formed — capped: {', '.join(capped_names)}"

            # 2) RAGE CHECK (strict): every account must meet the god's rage-to-join, or bail.
            god_db = db.get_prime_god(god["name"])
            rage_to_join = god_db.get("rage_to_join", 0) if god_db else 0
            if rage_to_join:
                low_rage = [t["name"] for t in sorted_trustees if t.get("rage", 0) < rage_to_join]
                if low_rage:
                    return False, 0, (f"Raid not formed — low rage (<{rage_to_join:,}): "
                                      f"{', '.join(low_rage)}")
            dbg("pre-flight passed: all accounts have caps + rage")
            pre_capped_count = 0

            # Navigate all characters to the god's room and capture the form URL
            # Try each trustee (highest rage first) as former until one can form
            form_url = None
            former = None
            former_suid = None
            _target_mob_id = god.get("mob_id") or god.get("god_id")   # stable god id for presence check
            god_seen = False                      # god mob present in room (even if capped)
            room_reached = False                  # some scout actually reached the god's room

            if room_id:
                from outwar.scraper import find_path
                nav_semaphore = asyncio.Semaphore(10)

                async def _navigate_to_god(t, try_as_former=False):
                    nonlocal form_url, former, former_suid, god_seen, room_reached
                    suid = t.get("suid")
                    async with nav_semaphore:
                        try:
                            import json as _json
                            raw = await session.get_as("ajax_changeroomb.php?room=0&lastroom=0", suid)
                            try:
                                loc = _json.loads(raw)
                            except Exception:
                                return
                            cur_room = int(loc.get("curRoom", 0))

                            if cur_room == room_id:
                                room_reached = True
                                for mob in loc.get("roomDetailsNew", []):
                                    if mob.get("type") == 1:   # god mob present in room
                                        god_seen = True
                                if try_as_former and not form_url:
                                    for mob in loc.get("roomDetailsNew", []):
                                        if mob.get("type") == 1 and mob.get("canForm"):
                                            mob_id = mob.get("mobId")
                                            h = mob.get("h", "")
                                            if mob_id:
                                                form_url = f"formraid.php?target=M{mob_id}&h={h}"
                                                former = t
                                                former_suid = suid
                                return

                            if not cur_room:
                                return

                            path = find_path(cur_room, room_id)
                            last = cur_room
                            last_data = None
                            for step in path[1:]:
                                step_raw = await session.get_as(f"ajax_changeroomb.php?room={step}&lastroom={last}", suid)
                                last = step
                                if step == room_id:
                                    try:
                                        last_data = _json.loads(step_raw)
                                    except Exception:
                                        pass

                            if last_data:
                                room_reached = True
                                for mob in last_data.get("roomDetailsNew", []):
                                    if mob.get("type") == 1:   # god mob present in room
                                        god_seen = True
                            if last_data and try_as_former and not form_url:
                                for mob in last_data.get("roomDetailsNew", []):
                                    if mob.get("type") == 1 and mob.get("canForm"):
                                        mob_id = mob.get("mobId")
                                        h = mob.get("h", "")
                                        if mob_id:
                                            form_url = f"formraid.php?target=M{mob_id}&h={h}"
                                            former = t
                                            former_suid = suid

                        except Exception as e:
                            logger.warning("RAID", f"Navigation error for {t.get('name')}: {e}")

                # Try each trustee as former until one can form (not capped)
                for candidate in sorted_trustees:
                    await _navigate_to_god(candidate, try_as_former=True)
                    if form_url:
                        break
                    # Not-spawned short-circuit: a scout reached the room but the god
                    # mob isn't there -> it's not spawned. Skip without walking the roster.
                    if room_reached and not god_seen:
                        return False, 0, "not spawned"

                if not form_url:
                    # Reached the god but couldn't form -> spawned but capped/full.
                    if god_seen:
                        return False, 0, "capped — could not form"
                    dbg("form: no account could form (none reached room / canForm false)")
                    return False, 0, "not spawned"
                dbg(f"form: former={former['name'] if former else '?'} form_url={'yes' if form_url else 'no'}")

                # Navigate remaining trustees
                others = [t for t in sorted_trustees if t.get("suid") != former_suid]
                await asyncio.gather(*[_navigate_to_god(t, try_as_former=False) for t in others])

            else:
                # No room_id — just use highest rage as former
                former = sorted_trustees[0]
                former_suid = former.get("suid")
                pre_capped_count = 0

            # Form the raid (as the former, per-request cookie)
            if form_url:
                await session.post_as(form_url, {
                    "formtime": "2",
                    "submit": "Join this Raid!",
                    "bomb": "none"
                }, former_suid)
            else:
                await session.post_as(
                    f"formraid.php?target={god_id}",
                    {"formtime": "2", "submit": "Join this Raid!", "bomb": "none"},
                    former_suid
                )

            # Fetch forming raid URL (as the former)
            from outwar.scraper import parse_raid_link
            forming_html = await session.get_as(
                f"crew_raidsforming.php?uid={former_suid}&id={former_suid}&server=1",
                former_suid
            )
            raid_url = parse_raid_link(forming_html, god["name"])
            dbg(f"raid_url: {raid_url or 'NONE (form did not create a forming raid)'}")
            if not raid_url:
                return False, 0, "Could not find forming raid."

            # Check rage requirements and cap status before joining
            god_db = db.get_prime_god(god["name"])
            rage_to_join = god_db.get("rage_to_join", 0) if god_db else 0
            capped_count  = 0
            low_rage_count = 0

            # Filter out low rage before joining
            joiners = []
            for t in sorted_trustees:
                if t.get("suid") == former_suid:
                    continue
                if rage_to_join and t.get("rage", 0) < rage_to_join:
                    low_rage_count += 1
                    continue
                joiners.append(t)

            # Join concurrently, then VERIFY who actually made it in before launching.
            # Prime raids have a hard minimum-to-launch and zero roster margin, so a
            # single rate-limited join (which does NOT auto-retry) would otherwise drop
            # the raid below minimum and launch it empty (damage=0). We therefore:
            #   1) fire all joins, classifying each result (SUCCESS vs RATE_LIMITED/etc)
            #   2) re-join ONLY the accounts we've confirmed did NOT join (safe — no
            #      double-join risk, because a confirmed-failed join never landed)
            #   3) launch only once the confirmed count meets the minimum
            # Concurrency is tunable via settings 'prime_join_concurrency' (default 8),
            # matching the boss-join throttle so primes don't blow past it at a hardcoded
            # value. Effective concurrency is still min(this, host_connection_limit).
            from outwar.session import RequestStatus
            try:
                _prime_conc = int(db.get_settings().get("prime_join_concurrency", 8))
            except Exception:
                _prime_conc = 8
            join_semaphore = asyncio.Semaphore(max(1, _prime_conc))

            # Minimum accounts needed in the raid to launch (the joiners we intended).
            _min_to_launch = len(joiners)

            async def _join_one(t):
                """Attempt one join. Returns (joined_ok, capped) based on real status."""
                nonlocal capped_count
                suid = t.get("suid")
                if not suid:
                    return (False, False)
                try:
                    async with join_semaphore:
                        result = await session.request_result(
                            "POST", raid_url,
                            data={"submit": "Join this Raid!", "raidjoin": "1"},
                            cookies={"ow_userid": str(suid)}, is_action=True,
                        )
                    # A rate-limited / non-success action did NOT land — report not-joined.
                    if result.status != RequestStatus.SUCCESS:
                        return (False, False)
                    html = result.html or ""
                    content_start = html.find('<div id="content"')
                    content_area = html[content_start:content_start+3000] if content_start > 0 else html[5000:10000]
                    if any(x in content_area.lower() for x in (
                        "capped", "cap limit", "already reached your",
                        "you have reached your", "reached the maximum"
                    )):
                        capped_count += 1
                        return (False, True)   # capped: don't retry, won't help
                    return (True, False)
                except Exception as e:
                    logger.warning("RAID", f"Join error for {t.get('name')}: {e}")
                    return (False, False)

            # Round 1: everyone joins.
            results = await asyncio.gather(*[_join_one(t) for t in joiners])
            joined = {t["suid"] for t, (ok, _cap) in zip(joiners, results) if ok}
            missing = [t for t, (ok, cap) in zip(joiners, results) if not ok and not cap]

            # Rounds 2-3: re-join ONLY the confirmed-missing (rate-limited) accounts,
            # with a short settle between rounds so the rate-limit window clears.
            _rejoin_rounds = 0
            while missing and _rejoin_rounds < 2:
                _rejoin_rounds += 1
                await asyncio.sleep(2)   # let the rate-limit window clear before retrying
                dbg(f"re-joining {len(missing)} missing account(s), round {_rejoin_rounds}")
                retry_results = await asyncio.gather(*[_join_one(t) for t in missing])
                for t, (ok, _cap) in zip(list(missing), retry_results):
                    if ok:
                        joined.add(t["suid"])
                missing = [t for t, (ok, cap) in zip(missing, retry_results) if not ok and not cap]

            await asyncio.sleep(0.5)
            _confirmed = len(joined)
            dbg(f"joiners attempted: {len(joiners)} · confirmed in: {_confirmed} · "
                f"still missing: {len(missing)} · join-capped: {capped_count} "
                f"(low-rage skipped: {low_rage_count})")

            # Verify-before-launch: if we couldn't get enough accounts in, DON'T launch
            # an under-strength raid (it would just read damage=0 and waste the attempt).
            if _confirmed < _min_to_launch:
                dbg(f"NOT launching — only {_confirmed}/{_min_to_launch} confirmed in raid")
                return False, 0, (f"Under-strength: {_confirmed}/{_min_to_launch} joined "
                                  f"(rate-limited joins dropped)")

            # Extract raidid from raid_url
            import re as _re
            raidid_match = _re.search(r"raidid=(\d+)", raid_url)
            if not raidid_match:
                return False, 0, "Could not extract raid ID for launch."
            raidid = raidid_match.group(1)
            launch_url = f"joinraid.php?raidid={raidid}&launchraid=yes"

            # Fire the launch and immediately fetch raidattack in parallel (both as former)
            async def _do_launch():
                return await session.get_as(launch_url, former_suid)

            async def _fetch_attack():
                # Small delay to let the animation start
                await asyncio.sleep(0.5)
                return await session.get_as(f"raidattack.php?raidid={raidid}", former_suid)

            launch_resp, attack_html = await asyncio.gather(_do_launch(), _fetch_attack())

            content_start = attack_html.find('<div id="content"')
            content_area = attack_html[content_start:] if content_start > 0 else attack_html
            dbg(f"raidid={raidid} launched; attack page head: {content_area[:160].strip()!r}")

            if "already been defeated" in content_area[:2000] or "invalid raid" in content_area[:2000]:
                await asyncio.sleep(1)
                attack_html = await session.get_as(f"raidattack.php?raidid={raidid}", former_suid)
                content_start = attack_html.find('<div id="content"')
                content_area = attack_html[content_start:] if content_start > 0 else attack_html

            damage = 0
            hp_pct = None

            # Check win from raidattack page
            won = "has won" in content_area.lower() or "won!" in content_area.lower()

            # Record win stats in background — non-blocking
            if won:
                async def _record_win_stats():
                    try:
                        from outwar.scraper import parse_character_stats_profile as _parse_stats
                        stat_semaphore = asyncio.Semaphore(8)

                        async def _fetch_stat(t):
                            suid = t.get("suid")
                            if not suid:
                                return None
                            try:
                                async with stat_semaphore:
                                    html = await session.get_as("profile", suid)
                                return _parse_stats(html)
                            except Exception:
                                return None

                        stat_results = await asyncio.gather(*[_fetch_stat(t) for t in sorted_trustees])
                        stat_results = [s for s in stat_results if s]
                        if stat_results:
                            avg_power = sum(s.get("power", 0) for s in stat_results) // len(stat_results)
                            avg_ele   = sum(s.get("elemental", 0) for s in stat_results) // len(stat_results)
                            db.record_raid_win(god["name"], god_id, avg_power, avg_ele, len(stat_results))
                    except Exception as e:
                        logger.warning("RAID", f"Win stat recording error: {e}")

                asyncio.create_task(_record_win_stats())

            # If won, mark all participants as potentially capped in cache
            # (they gained a cap, may now be at max — will be re-checked next attempt)
            if won and cap_cache is not None:
                for t in sorted_trustees:
                    cap_cache.pop(t["name"], None)  # remove from cache to force re-check

            # If raidattack expired, check the joinraid page for result
            if not won and ("already been defeated" in content_area or "invalid raid" in content_area):
                join_check = await session.get_as(f"joinraid.php?raidid={raidid}", former_suid)
                join_content_start = join_check.find('<div id="content"')
                join_content = join_check[join_content_start:join_content_start + 8000] if join_content_start > 0 else join_check
                won = "has won" in join_content.lower() or "won!" in join_content.lower() or "defeated" in join_content.lower()

            dmg_match = _re.search(r"Total Attacker Damage[:\s]+([\d,]+)", content_area)
            if dmg_match:
                try:
                    damage = int(dmg_match.group(1).replace(",", ""))
                except ValueError:
                    pass
            # God's remaining HP% — on the raidattack page it shows as a standalone percentage
            # span in the defender box, e.g. "> 7%</span>" (note the leading space). Take the
            # LAST such value (the god's final HP after the whole round).
            pcts = _re.findall(r">\s*(\d+(?:\.\d+)?)\s*%\s*</span>", content_area)
            if not pcts:
                pcts = _re.findall(r">\s*(\d+(?:\.\d+)?)\s*%\s*<", content_area)
            if pcts:
                try:
                    v = float(pcts[-1])
                    if 0 <= v <= 100:
                        hp_pct = v
                except ValueError:
                    pass

            # Stash the raw attack page so !rmdebug can dump it for parser tuning.
            if debug_log is not None:
                self._last_attack_html = attack_html

            # Build result note
            dbg(f"result: won={won} hp%={hp_pct} damage={damage}")
            notes = []
            if pre_capped_count:
                notes.append(f"{pre_capped_count} skipped (capped)")
            if low_rage_count:
                notes.append(f"{low_rage_count} skipped (low rage < {rage_to_join:,})")
            if capped_count:
                notes.append(f"{capped_count} blocked on join (capped)")
            if not won:
                if hp_pct is not None:
                    notes.append(f"god left at ~{hp_pct:.1f}% HP")
                if damage:
                    notes.append(f"{damage:,} dmg dealt")
            note = " · ".join(notes) if notes else None

            return won, damage, note

        except Exception as e:
            logger.error("RAID", f"God raid error: {e}")
            return False, 0, None
        finally:
            session._session.cookie_jar.update_cookies(
                {"ow_userid": str(session.user_id)}, response_url=SIGIL_URL
            )

    def _resolve_group(self, group: str) -> list:
        # Delegates to the single canonical impl in database.resolve_group
        return db.resolve_group(group)

    async def _check_group_caps(self, trustees: list, required_caps: int) -> tuple[list, list, str]:
        """
        Fetch current caps for all trustees.
        Returns (available, capped, error_msg).
        error_msg is set if group doesn't have enough caps for required_caps attempts.
        """
        from outwar.scraper import parse_god_cap
        semaphore = asyncio.Semaphore(8)

        async def _fetch(t):
            suid = t.get("suid")
            if not suid:
                return t["name"], 0, 0
            try:
                async with semaphore:
                    html = await self.session.get_as("home", suid)
                used, max_cap = parse_god_cap(html)
                avail = (max_cap - used) if max_cap else 0
                return t["name"], avail, max_cap
            except Exception:
                return t["name"], 0, 0

        results = await asyncio.gather(*[_fetch(t) for t in trustees])
        # cap_map values are (available, max) read straight off the toolbar.
        cap_map = {name: (cur, max_cap) for name, cur, max_cap in results}

        # Toolbar = AVAILABLE/MAX. Available to raid when max unknown (0) or available > 0.
        available = [t for t in trustees if cap_map.get(t["name"], (0, 0))[1] == 0 or
                     cap_map.get(t["name"], (0, 0))[0] > 0]
        capped    = [t for t in trustees if t not in available]

        # Each account needs at least required_caps caps AVAILABLE (the first number directly).
        enough = [t for t in available
                  if cap_map.get(t["name"], (0, 0))[1] == 0 or
                  cap_map.get(t["name"], (0, 0))[0] >= required_caps]

        if len(enough) < len(trustees) - len(capped):
            low_cap = [t["name"] for t in available if t not in enough]
            error = (f"⚠️ {len(low_cap)} account(s) don't have {required_caps} caps remaining: "
                     f"{', '.join(low_cap[:5])}{'...' if len(low_cap) > 5 else ''}")
        else:
            error = None

        return available, capped, error


    # ------------------------------------------------------------------
    # World raid mobs — Badge raids and TCE
    # ------------------------------------------------------------------

    WORLD_RAID_MOBS = {
        # Badge raids
        "crawling":    {"name": "Crawling Monstrosity", "mob_id": 2748, "room_id": 26207},
        "demonic":     {"name": "Demonic Barbarian",    "mob_id": 3927, "room_id": 27531},
        "elexo":       {"name": "The Elexocutioner",    "mob_id": 3928, "room_id": 27530},
        "conductor":   {"name": "Conductor of Fire",    "mob_id": 3926, "room_id": 27532},
        # TCE
        "tce":         {"name": "The Chaotic Elemental","mob_id": 4382, "room_id": 28132},
    }

    async def _do_world_raid(self, trustees: list, mob: dict) -> tuple[bool, int, str]:
        """Form and launch a world mob raid. Same flow as prime god raids."""
        mob_id  = mob["mob_id"]
        room_id = mob["room_id"]
        session = self.session

        sorted_trustees = sorted(trustees, key=lambda t: t.get("rage", 0), reverse=True)
        if not sorted_trustees:
            return False, 0, None

        try:
            from outwar.scraper import find_path
            nav_semaphore = asyncio.Semaphore(10)
            form_url   = None
            former     = None
            former_suid = None
            god_seen     = False   # god mob present in the room (even if capped)
            room_reached = False   # a scout actually reached the god's room
            # Target god's NAME is the authoritative identifier. Mobs.txt mob_ids are
            # NOT reliable (e.g. Freezebreed is 1288 live but 944 in Mobs.txt), so we
            # identify the room entry by name and form using THAT entry's own live
            # mobId/h. Shared rooms hold 2+ gods; name-match forms on the right one.
            _target_name = (mob.get("name") or "").strip().lower()

            def _find_target(details):
                """Return the roomDetailsNew entry whose name matches our target god."""
                for m in details:
                    if (m.get("type") == 1
                            and (m.get("name") or "").strip().lower() == _target_name):
                        return m
                return None

            async def _navigate(t, try_as_former=False):
                nonlocal form_url, former, former_suid, god_seen, room_reached
                suid = t.get("suid")
                async with nav_semaphore:
                    try:
                        import json as _json
                        raw = await session.get_as("ajax_changeroomb.php?room=0&lastroom=0", suid)
                        try:
                            loc = _json.loads(raw)
                        except Exception:
                            return
                        cur_room = int(loc.get("curRoom", 0))

                        if cur_room == room_id:
                            room_reached = True
                            _m = _find_target(loc.get("roomDetailsNew", []))
                            if _m:
                                god_seen = True   # OUR target god present (spawned)
                                if try_as_former and not form_url and _m.get("canForm"):
                                    _mid = _m.get("mobId")
                                    if _mid:
                                        form_url    = f"formraid.php?target=M{_mid}&h={_m.get('h','')}"
                                        former      = t
                                        former_suid = suid
                            return

                        if not cur_room:
                            return

                        path = find_path(cur_room, room_id)
                        last = cur_room
                        last_data = None
                        for step in path[1:]:
                            step_raw = await session.get_as(f"ajax_changeroomb.php?room={step}&lastroom={last}", suid)
                            last = step
                            if step == room_id:
                                try:
                                    last_data = _json.loads(step_raw)
                                except Exception:
                                    pass

                        if last_data:
                            room_reached = True
                            _m = _find_target(last_data.get("roomDetailsNew", []))
                            if _m:
                                god_seen = True
                                if try_as_former and not form_url and _m.get("canForm"):
                                    _mid = _m.get("mobId")
                                    if _mid:
                                        form_url    = f"formraid.php?target=M{_mid}&h={_m.get('h','')}"
                                        former      = t
                                        former_suid = suid
                    except Exception as e:
                        logger.warning("RAID", f"Navigation error for {t.get('name')}: {e}")

            for candidate in sorted_trustees:
                await _navigate(candidate, try_as_former=True)
                if form_url:
                    break
                # Not-spawned short-circuit: a scout reached the room but the god
                # mob isn't there -> it's dead/not spawned. Skip immediately instead
                # of walking every account to an empty room.
                if room_reached and not god_seen:
                    return False, 0, "not spawned"

            if not form_url:
                # Reached the god but couldn't form -> spawned but capped/full.
                if god_seen:
                    return False, 0, "capped — could not form"
                return False, 0, "not spawned"


            others = [t for t in sorted_trustees if t.get("suid") != former_suid]
            await asyncio.gather(*[_navigate(t, try_as_former=False) for t in others])

            # Form (as former, per-request cookie)
            await session.post_as(form_url, {
                "formtime": "2", "submit": "Join this Raid!", "bomb": "none"
            }, former_suid)

            # Get raid URL (as former)
            from outwar.scraper import parse_raid_link
            forming_html = await session.get_as(
                f"crew_raidsforming.php?uid={former_suid}&id={former_suid}&server=1",
                former_suid
            )
            raid_url = parse_raid_link(forming_html, mob["name"])
            if not raid_url:
                return False, 0, "Could not find forming raid."

            import re as _re
            raidid_match = _re.search(r"raidid=(\d+)", raid_url)
            if not raidid_match:
                return False, 0, "Could not extract raid ID."
            raidid = raidid_match.group(1)

            # Learn this god's join limits (min/max joiners) once, from the raid
            # page — saved to join_limits.json and reused to size future rosters.
            _alias = mob.get("alias")
            if _alias and _alias not in db.get_join_limits():
                try:
                    from outwar.scraper import parse_join_limits
                    _raid_page = await session.get_as(raid_url, former_suid)
                    _lim = parse_join_limits(_raid_page)
                    if _lim:
                        db.set_join_limit(_alias, _lim[0], _lim[1])
                        logger.info("SLAYER", f"Learned join limits for {mob['name']}: "
                              f"{_lim[0]}-{_lim[1]} (saved)")
                    else:
                        logger.warning("SLAYER", f"Could not parse join limits for {mob['name']} "
                              f"— will size on next encounter")
                except Exception as _e:
                    logger.warning("SLAYER", f"Join-limit learn error for {mob['name']}: {_e}")

            # Join concurrently
            join_semaphore = asyncio.Semaphore(10)
            joiners = [t for t in sorted_trustees if t.get("suid") and t.get("suid") != former_suid]

            async def _join_world(t):
                suid = t.get("suid")
                try:
                    async with join_semaphore:
                        await session.post_as(raid_url, {
                            "submit": "Join this Raid!", "raidjoin": "1"
                        }, suid)
                except Exception as e:
                    logger.warning("RAID", f"Join error for {t['name']}: {e}")

            await asyncio.gather(*[_join_world(t) for t in joiners])
            await asyncio.sleep(0.5)

            # Launch and detect result
            launch_url = f"joinraid.php?raidid={raidid}&launchraid=yes"

            async def _do_launch():
                return await session.get_as(launch_url, former_suid)

            async def _fetch_attack():
                await asyncio.sleep(0.5)
                return await session.get_as(f"raidattack.php?raidid={raidid}", former_suid)

            _, attack_html = await asyncio.gather(_do_launch(), _fetch_attack())

            content_start = attack_html.find('<div id="content"')
            content_area  = attack_html[content_start:] if content_start > 0 else attack_html

            if "already been defeated" in content_area[:2000] or "invalid raid" in content_area[:2000]:
                await asyncio.sleep(1)
                attack_html   = await session.get_as(f"raidattack.php?raidid={raidid}", former_suid)
                content_start = attack_html.find('<div id="content"')
                content_area  = attack_html[content_start:] if content_start > 0 else attack_html

            won    = "has won" in content_area.lower() or "won!" in content_area.lower()
            damage = 0
            drops  = ""
            dmg_match = _re.search(r"Total Attacker Damage[:\s]+([\d,]+)", content_area)
            if dmg_match:
                try:
                    damage = int(dmg_match.group(1).replace(",", ""))
                except ValueError:
                    pass

            # Parse drops from popup attribute e.g. popup(event,'<b>Item1<br>Item2 x3</b>')
            if won:
                drop_match = _re.search(r"popup\(event,'<b>(.*?)</b>'\)\s*\"\s*>[\d]+ items", content_area)
                if not drop_match:
                    drop_match = _re.search(r"popup\(event,'<b>(.*?)</b>'", content_area)
                if drop_match:
                    raw_drops = drop_match.group(1)
                    # Replace <br> separators with comma before stripping all HTML
                    raw_drops = _re.sub(r"<br\s*/?>", ", ", raw_drops, flags=_re.IGNORECASE)
                    drops = _re.sub(r"<[^>]+>", "", raw_drops).strip().strip(",")

            return won, damage, drops

        except Exception as e:
            logger.error("RAID", f"World raid error: {e}")
            return False, 0, None
        finally:
            session._session.cookie_jar.update_cookies(
                {"ow_userid": str(session.user_id)}, response_url=SIGIL_URL
            )

    @commands.command(name="badge")
    async def badge_raid(self, ctx, group: str, tries: int = 1):
        """
        Hit all 3 badge raid mobs with a group.
        Usage: !badge <group> [tries]
        Raids Crawling Monstrosity, Demonic Barbarian, The Elexocutioner and Conductor of Fire.
        """
        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        badge_mobs = [
            self.WORLD_RAID_MOBS["crawling"],
            self.WORLD_RAID_MOBS["demonic"],
            self.WORLD_RAID_MOBS["elexo"],
            self.WORLD_RAID_MOBS["conductor"],
        ]

        await ctx.send(
            f"🏆 Starting badge raids with **{len(trustees)}** characters · **{tries}** tries each..."
        )

        total_wins   = 0
        total_damage = 0
        start_time   = datetime.now()

        for mob in badge_mobs:
            mob_wins   = 0
            mob_damage = 0
            spawned    = True
            for attempt in range(1, tries + 1):
                won, damage, note = await self._do_world_raid(trustees, mob)
                if note and ("not spawned" in note.lower() or "could not form" in note.lower()):
                    await ctx.send(f"⚠️ **{mob['name']}** is not spawned — skipping.")
                    spawned = False
                    break
                if damage:
                    mob_damage   += damage
                    total_damage += damage
                if won:
                    mob_wins   += 1
                    total_wins += 1
                    msg = f"🏆 **{mob['name']}** — Win on attempt {attempt}!"
                    if damage:
                        msg += f" Damage: {damage:,}"
                    if note:
                        msg += f"\n📦 {note}"
                    await ctx.send(msg)
                    break
                else:
                    msg = f"**{mob['name']}** attempt {attempt}/{tries} — no win."
                    await ctx.send(msg)
                if attempt < tries:
                    await asyncio.sleep(2)

        elapsed = (datetime.now() - start_time).seconds
        embed = discord.Embed(
            title=f"🏆 Badge Raids Complete",
            color=discord.Color.gold() if total_wins == len(badge_mobs) else discord.Color.blue()
        )
        embed.add_field(name="Wins",         value=f"{total_wins}/{len(badge_mobs)}", inline=True)
        embed.add_field(name="Total Damage", value=f"{total_damage:,}",               inline=True)
        embed.add_field(name="Time",         value=f"{elapsed}s",                     inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="tce")
    async def tce_raid(self, ctx, group: str, tries: int = 1):
        """
        Hit The Chaotic Elemental with a group.
        Usage: !tce <group> [tries]
        """
        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        mob = self.WORLD_RAID_MOBS["tce"]
        await ctx.send(
            f"⚡ Hitting **{mob['name']}** with **{len(trustees)}** characters · **{tries}** tries..."
        )

        total_wins   = 0
        total_damage = 0
        start_time   = datetime.now()

        for attempt in range(1, tries + 1):
            won, damage, note = await self._do_world_raid(trustees, mob)
            if note and ("not spawned" in note.lower() or "could not form" in note.lower()):
                await ctx.send(f"⚠️ **{mob['name']}** is not spawned.")
                return
            if damage:
                total_damage += damage
            if won:
                total_wins += 1
                msg = f"🏆 **Win** on attempt {attempt}!"
                if damage:
                    msg += f" Damage: {damage:,}"
                if note:
                    msg += f"\n📦 {note}"
                await ctx.send(msg)
            else:
                msg = f"Attempt {attempt}/{tries} — no win."
                await ctx.send(msg)
            if attempt < tries:
                await asyncio.sleep(2)

        elapsed = (datetime.now() - start_time).seconds
        embed = discord.Embed(
            title=f"⚡ TCE Complete",
            color=discord.Color.gold() if total_wins > 0 else discord.Color.blue()
        )
        embed.add_field(name="Attempts",     value=str(tries),          inline=True)
        embed.add_field(name="Wins",         value=str(total_wins),     inline=True)
        embed.add_field(name="Total Damage", value=f"{total_damage:,}", inline=True)
        embed.add_field(name="Time",         value=f"{elapsed}s",       inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="crest")
    async def crest_raid(self, ctx, group: str, target: str = "both", tries: int = 1):
        """
        Hit Grouz and/or Morrik for crests.
        Usage: !crest <group> [grouz|morrik|both] [tries]
        Example: !crest lod1 both 3
        """
        crest_mobs = {
            "grouz":  {"name": "Grouz the Darkener",    "mob_id": 4041, "room_id": 28118},
            "morrik": {"name": "Morrik the Spellcaster", "mob_id": 4040, "room_id": 28117},
        }

        target = target.lower()
        if target == "both":
            targets = [crest_mobs["grouz"], crest_mobs["morrik"]]
        elif target in crest_mobs:
            targets = [crest_mobs[target]]
        else:
            await ctx.send(f"Unknown target `{target}`. Use `grouz`, `morrik`, or `both`.")
            return

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        names = " + ".join(t["name"] for t in targets)
        await ctx.send(f"⚔️ Hitting **{names}** with **{len(trustees)}** characters · **{tries}** tries each...")

        start_time   = datetime.now()
        total_wins   = 0
        total_damage = 0

        for mob in targets:
            for attempt in range(1, tries + 1):
                won, damage, note = await self._do_world_raid(trustees, mob)
                if note and ("not spawned" in note.lower() or "could not form" in note.lower()):
                    await ctx.send(f"⚠️ **{mob['name']}** is not spawned — skipping.")
                    break
                if damage:
                    total_damage += damage
                if won:
                    total_wins += 1
                    msg = f"🏆 **{mob['name']}** — Win on attempt {attempt}!"
                    if damage:
                        msg += f" Damage: {damage:,}"
                    if note:
                        msg += f"\n📦 {note}"
                    await ctx.send(msg)
                    break
                else:
                    msg = f"**{mob['name']}** attempt {attempt}/{tries} — no win."
                    await ctx.send(msg)
                if attempt < tries:
                    await asyncio.sleep(2)

        elapsed = (datetime.now() - start_time).seconds
        embed = discord.Embed(
            title="⚔️ Crest Raids Complete",
            color=discord.Color.gold() if total_wins == len(targets) else discord.Color.blue()
        )
        embed.add_field(name="Wins",         value=f"{total_wins}/{len(targets)}", inline=True)
        embed.add_field(name="Total Damage", value=f"{total_damage:,}",            inline=True)
        embed.add_field(name="Time",         value=f"{elapsed}s",                  inline=True)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # God Slayer — verification commands (data only; raiding comes next)
    # ------------------------------------------------------------------

    @commands.command(name="slayer-list", aliases=["slayerlist"])
    async def slayer_list(self, ctx):
        """Show the daily God-Slayer targets resolved to their rooms (from Mobs.txt)."""
        from outwar.scraper import resolve_slayer_targets
        resolved, unresolved = resolve_slayer_targets()
        lines = [f"{r['alias']:<10} room {r['room']:<6} {r['name']}" for r in resolved]
        header = f"🗡️ **God-Slayer targets** — {len(resolved)} resolved" + (
                 f", {len(unresolved)} unresolved" if unresolved else "")
        # chunk into code blocks under Discord's limit
        await ctx.send(header)
        buf = "```\n"
        for ln in lines:
            if len(buf) + len(ln) + 5 > 1900:
                await ctx.send(buf + "```")
                buf = "```\n"
            buf += ln + "\n"
        await ctx.send(buf + "```")
        if unresolved:
            await ctx.send("⚠️ Unresolved: " + ", ".join(f"{a} ({n})" for a, n in unresolved))

    @commands.command(name="slayer-needs", aliases=["slayerneeds"])
    async def slayer_needs(self, ctx, character: str):
        """Show which daily slayer gods an account still needs (hasn't slayed yet)."""
        from outwar import database as db
        from outwar.scraper import parse_god_slayer, resolve_slayer_targets
        trustees = db.get_trustees()
        t = next((x for x in trustees if x["name"].lower() == character.lower()), None)
        if not t or not t.get("suid"):
            await ctx.send(f"`{character}` not found in trustees or has no SUID.")
            return
        html = await self.session.get_as("profile", t["suid"])
        slayed = {g["name"].lower() for g in parse_god_slayer(html)}
        resolved, _ = resolve_slayer_targets()
        needs = [r for r in resolved if r["name"].lower() not in slayed]
        have = len(resolved) - len(needs)
        if not needs:
            await ctx.send(f"✅ **{t['name']}** has slayed all {len(resolved)} daily targets.")
            return
        names = ", ".join(r["alias"] for r in needs)
        await ctx.send(f"🗡️ **{t['name']}** — needs **{len(needs)}** of {len(resolved)} "
                       f"(has {have}):\n{names}"[:1900])

    @commands.command(name="slayer-stop", aliases=["slayerstop"])
    async def slayer_stop(self, ctx):
        """Stop an in-progress slayer run after the current god."""
        self._slayer_stop = True
        await ctx.send("🛑 Slayer will stop after the current god.")

    @commands.command(name="slayer")
    async def slayer(self, ctx, crew: str = "LoD", mode: str = "needed"):
        """Raid the daily God-Slayer gods for a crew. Needers join first, others backfill.
        Usage: !slayer <crew> [needed|all]
          needed (default) → only raid gods at least one account still needs
          all              → raid all 56 targets (full clear for drops)"""
        import io as _io
        from outwar.scraper import parse_god_slayer, resolve_slayer_targets
        trustees = [t for t in self._resolve_group(crew) if t.get("suid")]
        if not trustees:
            await ctx.send(f"No accounts found for crew/group `{crew}`.")
            return
        targets, unresolved = resolve_slayer_targets()
        self._slayer_stop = False

        msg = await ctx.send(
            f"🗡️ **Slayer — {crew}**: reading God Slayer pages for {len(trustees)} accounts…")
        sem = asyncio.Semaphore(10)

        async def _fetch_slayed(t):
            async with sem:
                try:
                    html = await self.session.get_as("profile", t["suid"])
                    return t["name"], {g["name"].lower() for g in parse_god_slayer(html)}
                except Exception:
                    return t["name"], set()

        slayed_by = dict(await asyncio.gather(*[_fetch_slayed(t) for t in trustees]))

        # choose which gods to raid
        plan = []
        for tgt in targets:
            needers = [t for t in trustees
                       if tgt["name"].lower() not in slayed_by.get(t["name"], set())]
            if mode.lower() == "all" or needers:
                plan.append((tgt, needers))

        # Route-ordering: group gods by area so clusters (e.g. the Foundry gods)
        # get cleared together instead of zig-zagging across the map and back.
        # Accounts navigate from their current room, so proximity ordering cuts
        # real walking. Gods whose room isn't in Areas.txt sort to the end.
        from outwar.scraper import room_to_area_map
        _area = room_to_area_map()
        plan.sort(key=lambda p: (_area.get(int(p[0].get("room", 0) or 0), 10**9),
                                 int(p[0].get("room", 0) or 0)))

        await msg.edit(content=f"🗡️ **Slayer — {crew}**: raiding {len(plan)} gods "
                               f"({'all' if mode.lower()=='all' else 'with needers'})… "
                               f"`!slayer-stop` to halt.")

        results = []
        wins = 0
        join_limits = db.get_join_limits()   # learned min/max joiners per god
        from outwar.scraper import size_slayer_roster
        for i, (tgt, needers) in enumerate(plan, 1):
            if self._slayer_stop:
                break
            # One message per god: post "Raiding X…", then edit THAT line to the result.
            line = await ctx.send(f"⚔️ Raiding **{tgt['name']}** with **{crew}**…")
            non = [t for t in trustees if t not in needers]
            # Roster-sizing: if we've learned this god's join limits, send only enough
            # to fill toward MAX (needers first, then backfill) — avoids walking the
            # whole roster to a small-party god. Unknown limits -> send all, learn below.
            jl = join_limits.get(tgt["alias"])
            if jl and jl.get("max"):
                roster = size_slayer_roster(needers, non, jl["max"], jl.get("min", 0))
            else:
                roster = needers + non   # unknown limits — send all, learn this run
            mob = {"name": tgt["name"], "mob_id": tgt["mob_id"],
                   "room_id": tgt["room"], "alias": tgt["alias"]}
            try:
                won, dmg, note = await self._do_world_raid(roster, mob)
            except Exception as e:
                won, dmg, note = False, 0, f"error: {e}"
            # Refresh learned limits in case _do_world_raid just learned this god's.
            if tgt["alias"] not in join_limits:
                _fresh = db.get_join_limits().get(tgt["alias"])
                if _fresh:
                    join_limits[tgt["alias"]] = _fresh
            if won:
                wins += 1
            results.append((tgt["alias"], won, len(needers), dmg, note))
            if note == "not spawned":
                display = f"**{tgt['name']}** — ⚠️ not spawned (skipped)"
            elif note and note.startswith("capped"):
                display = f"**{tgt['name']}** — 🔒 capped / full"
            else:
                outcome = "WIN" if won else "LOSE"
                drops = f" — {note}" if (won and note) else ""
                display = f"**{tgt['name']}** — {outcome}{drops}"
            try:
                await line.edit(content=display)
            except Exception:
                pass

        lines = [f"Slayer run — {crew}  ({'all' if mode.lower()=='all' else 'needers'})",
                 f"Gods raided: {len(results)} · Wins: {wins}", ""]
        for alias, won, nd, dmg, note in results:
            tag = "WIN " if won else "miss"
            lines.append(f"{alias:<10} {tag}  needers:{nd:<3} dmg:{dmg:>12,}  {note or ''}".rstrip())
        if unresolved:
            lines += ["", "Unresolved targets: " + ", ".join(a for a, _ in unresolved)]
        buf = _io.BytesIO("\n".join(lines).encode("utf-8"))
        await ctx.send(
            content=f"🗡️ **Slayer complete — {crew}** · {wins}/{len(results)} wins"
                    + (" · stopped early" if self._slayer_stop else ""),
            file=discord.File(buf, filename=f"slayer_{crew}.txt"))


async def setup(bot):
    await bot.add_cog(RaidCommands(bot))