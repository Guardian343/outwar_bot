import asyncio
import re
import discord
from discord.ext import commands
from yarl import URL
from outwar import database as db
from cogs import embed_style as es
from outwar.scraper import (
    parse_character_profile,
    parse_backpack_for_item
)
from outwar.constants import (
    Skill, SKILL_NAMES, POTIONS, BOSS_POTS, DRINK_ALL_ORDER,
    PRESERVATION_SKILLS, FEROCITY_SKILLS, AFFLICTION_SKILLS, CLASS_SKILLS,
    resolve_skill
)
from outwar import logger

BASE_URL = "https://sigil.outwar.com"
SIGIL_URL = URL("https://sigil.outwar.com")


def _extract_suid(url: str) -> int:
    m = re.search(r"suid=(\d+)", url)
    return int(m.group(1)) if m else 0


class CharacterCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.trustees = db.get_trustees()

    @property
    def session(self):
        return self.bot.outwar

    # ------------------------------------------------------------------
    # Cookie switching helpers
    # ------------------------------------------------------------------

    def _switch_to(self, suid: int):
        """Switch the bot session context to a trustee character."""
        self.session._session.cookie_jar.update_cookies(
            {"ow_userid": str(suid)}, response_url=SIGIL_URL
        )

    def _switch_to_bot(self):
        """Switch the bot session context back to the bot account."""
        self.session._session.cookie_jar.update_cookies(
            {"ow_userid": str(self.session.user_id)}, response_url=SIGIL_URL
        )

    # ------------------------------------------------------------------
    # Trustee management
    # ------------------------------------------------------------------

    @commands.command(name="update-trustees")
    async def update_trustees(self, ctx):
        """Reload trustee list from database/trustees.json."""
        await ctx.send("Updating trustee list...")
        self.trustees = db.get_trustees()
        await ctx.send(f"Trustee list updated! ({len(self.trustees)} trustees loaded)")

    @commands.command(name="check-trustees")
    async def check_trustees(self, ctx):
        """Show all trusted accounts."""
        filtered = sorted(self.trustees, key=lambda t: t["name"])
        chunks = [filtered[i:i+100] for i in range(0, len(filtered), 100)]
        for chunk in chunks:
            embed = discord.Embed()
            embed.description = f"**Accounts trusteed: {len(filtered)}**\n\n" + " ".join(t["name"] for t in chunk)
            await ctx.send(embed=embed)

    @commands.command(name="get-sessid")
    async def get_session_id(self, ctx):
        await ctx.send(f"SessionId = `{self.session.session_id}`")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @commands.command(name="show-mr")
    async def show_max_rage(self, ctx, *, group: str):
        """Show max rage for all characters in a group or crew."""
        rga_group = db.get_group(group)
        crew = db.get_crew(group)

        if rga_group:
            await ctx.send(f"Getting max rage for group {group.upper()}")
            trustees = db.get_trustees_by_group(group)
        elif crew:
            await ctx.send(f"Getting max rage for crew {crew['full_name']}")
            trustees = db.get_trustees_by_crew(crew["full_name"])
        else:
            await ctx.send(f"Invalid group or crew name: `{group}`")
            return

        async def _get_max_rage(t):
            try:
                suid = t.get("suid") or _extract_suid(t.get("url", ""))
                if not suid:
                    return None
                html = await self.session.get_as(f"world?suid={suid}&serverid=1", suid)
                from outwar.scraper import parse_max_rage
                mr = parse_max_rage(html)
                return {"name": t["name"], "max_rage": int(mr) if mr else 0}
            except Exception:
                return None

        results = await asyncio.gather(*[_get_max_rage(t) for t in trustees])
        results = sorted([r for r in results if r], key=lambda x: x["max_rage"], reverse=True)

        embed = es.info_embed(f"💢 Max Rage — {group.upper()}")
        chunk = ""
        for r in results:
            line = f"{r['max_rage']:,} — {r['name']}\n"
            if len(chunk) + len(line) > 1000:
                embed.add_field(name="Max Rage", value=chunk, inline=False)
                chunk = ""
            chunk += line
        if chunk:
            embed.add_field(name="Max Rage", value=chunk, inline=False)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Skill casting
    # ------------------------------------------------------------------

    @commands.command(name="cast-ss")
    async def cast_street_smarts(self, ctx, *, group: str):
        """Cast Street Smarts on a group or crew."""
        await self._cast_skill_for_group(ctx, group, Skill.STREET_SMARTS)

    @commands.command(name="cast-pres")
    async def cast_preservation(self, ctx, *, target: str):
        """Cast all Preservation skills on a crew, group, or character."""
        await self._cast_skill_group(ctx, target, PRESERVATION_SKILLS, "Preservation")

    @commands.command(name="cast-fero")
    async def cast_ferocity(self, ctx, *, target: str):
        """Cast all Ferocity skills on a crew, group, or character."""
        await self._cast_skill_group(ctx, target, FEROCITY_SKILLS, "Ferocity")

    @commands.command(name="cast-afflic")
    async def cast_affliction(self, ctx, *, target: str):
        """Cast all Affliction skills on a crew, group, or character."""
        await self._cast_skill_group(ctx, target, AFFLICTION_SKILLS, "Affliction")

    @commands.command(name="cast-class")
    async def cast_class(self, ctx, *, target: str):
        """Cast all Class skills on a crew, group, or character."""
        await self._cast_skill_group(ctx, target, CLASS_SKILLS, "Class")

    @commands.command(name="cast-raid")
    async def cast_raid(self, ctx, *, target: str):
        """Cast all boss raid skills (except SiN) on a crew, group, or character."""
        from cogs.boss_raid_commands import BOSS_SKILLS_CLASS, BOSS_SKILLS_PRES, BOSS_SKILLS_MISC, ROTATING_SKILLS
        raid_skills = BOSS_SKILLS_CLASS + [s for s in BOSS_SKILLS_PRES if s not in ROTATING_SKILLS] + BOSS_SKILLS_MISC
        await self._cast_skill_group(ctx, target, raid_skills, "Raid Skills")

    @commands.command(name="cast")
    async def cast(self, ctx, skill_name: str, *, target: str):
        """
        Cast any skill on a crew, group, or single character.
        Usage: !cast <skill> <target>
        Run !skills to see all skill aliases.
        """
        skill_id, skill_label = resolve_skill(skill_name)

        if skill_id is None:
            await ctx.send(f"Unknown skill `{skill_name}`. Use `!skills` to see all available skills.")
            return

        trustees = self._resolve_target(target)
        if not trustees:
            await ctx.send(f"No characters found for `{target}`.")
            return

        await ctx.send(f"Casting **{skill_label}** on **{len(trustees)}** character(s)...")

        success     = 0
        failed      = 0
        not_trained = 0
        sem         = asyncio.Semaphore(10)

        async def _cast_one(t):
            nonlocal success, failed, not_trained
            suid = t.get("suid") or _extract_suid(t.get("url", ""))
            if not suid:
                failed += 1
                return
            async with sem:
                try:
                    resp = await self.session.post_as("cast_skills.php", {
                        "castskillid": str(skill_id),
                        "cast": "Cast Skill"
                    }, suid)
                    if "You just cast" in resp or "already cast" in resp.lower():
                        success += 1
                    else:
                        not_trained += 1
                except Exception as e:
                    logger.warning("GUARD", f"Failed to cast '{skill_name}' for {t['name']}: {e}")
                    failed += 1

        await asyncio.gather(*[_cast_one(t) for t in trustees])

        parts = [f"✅ **{success}** cast"]
        if not_trained:
            parts.append(f"⚠️ **{not_trained}** not trained")
        if failed:
            parts.append(f"{es.ICON_NODROP} **{failed}** error(s)")
        embed = es.report_embed(
            f"{skill_label} — Cast Report",
            description="  ·  ".join(parts)
        )
        await ctx.send(embed=embed)

    @commands.command(name="cast-all")
    async def cast_all(self, ctx, *, target: str):
        """Cast Empower, Stealth, VitaminX, Fortify on a crew, group, or single trustee."""
        trustees = self._resolve_target(target)
        if not trustees:
            await ctx.send(f"⚠️ No accounts found for `{target}` — not a known crew, group, or character name.")
            return

        skills = [Skill.EMPOWER, Skill.STEALTH, Skill.VITAMIN_X, Skill.FORTIFY]
        sem    = asyncio.Semaphore(10)
        succeeded = []
        failed    = []

        async def _cast_one(t):
            suid = t.get("suid") or _extract_suid(t.get("url", ""))
            if not suid:
                failed.append(t["name"])
                return
            async with sem:
                try:
                    self.session._session.cookie_jar.update_cookies(
                        {"ow_userid": str(suid)}, response_url=SIGIL_URL
                    )
                    any_success = False
                    for skill_id in skills:
                        resp = await self.session.post("cast_skills.php", {
                            "castskillid": str(skill_id),
                            "cast": "Cast Skill"
                        })
                        if "You just cast" in resp:
                            any_success = True
                        await asyncio.sleep(0.05)
                    if any_success:
                        succeeded.append(t["name"])
                    else:
                        failed.append(t["name"])
                except Exception:
                    failed.append(t["name"])
                finally:
                    self.session._session.cookie_jar.update_cookies(
                        {"ow_userid": str(self.session.user_id)}, response_url=SIGIL_URL
                    )

        # Single character — keep it simple, no progress message needed
        if len(trustees) == 1:
            await _cast_one(trustees[0])
            name = trustees[0]["name"]
            if name in succeeded:
                await ctx.send(f"✅ Skills cast on **{name}**!")
            else:
                await ctx.send(f"⚠️ Failed to cast skills on **{name}**.")
            return

        # Crew/group — show progress for larger batches
        msg = await ctx.send(f"Casting class skills on **{len(trustees)}** accounts...")
        await asyncio.gather(*[_cast_one(t) for t in trustees])
        await msg.delete()
        await ctx.send(
            f"✅ Class skills cast on **{len(succeeded)}/{len(trustees)}** accounts"
            + (f" — ⚠️ failed: {', '.join(failed[:15])}" + ("..." if len(failed) > 15 else "") if failed else "")
        )

    @commands.command(name="skills")
    async def skills_list(self, ctx):
        """Show all available skills and their aliases for use with !cast."""
        embed = discord.Embed(
            title="Available Skills",
            description="Use any alias with `!cast <skill> <target>`",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Class Skills", value=(
            "**Empower** — `emp`\n"
            "**Stealth** — `stealth`\n"
            "**On Guard** — `guard`\n"
            "**Teleport** — `tp`\n"
            "**Vitamin X** — `vx, vitx`\n"
            "**Fortify** — `fort`\n"
            "**Street Smarts** — `ss, street`\n"
            "**Masterful Ferocity** — `mf`\n"
            "**Masterful Preservation** — `mp`\n"
            "**Masterful Affliction** — `ma`"
        ), inline=True)
        embed.add_field(name="Ferocity Skills", value=(
            "**Boost** — `boost`\n"
            "**Protection** — `prot`\n"
            "**Accurate Strike** — `accurate`\n"
            "**Dark Strength** — `dark, ds`\n"
            "**Swiftness** — `swift`\n"
            "**Haste** — `haste`\n"
            "**Masterful Looting** — `looting`\n"
            "**Circumspect** — `circ`\n"
            "**Bloodlust** — `bl`\n"
            "**Stone Skin** — `stone`\n"
            "**Loyal Ferocity** — `lf`"
        ), inline=True)
        embed.add_field(name="Preservation Skills", value=(
            "**Masterful Raiding** — `mr`\n"
            "**Markdown** — `md`\n"
            "**Last Stand** — `last, ls`\n"
            "**Strength in Numbers** — `sin`\n"
            "**Forcefield** — `ff`\n"
            "**Blessing from Above** — `bfa`\n"
            "**Enchant Armor** — `ea`\n"
            "**Elemental Power** — `ep`\n"
            "**Executioner** — `exec`\n"
            "**Elemental Barrier** — `eb`\n"
            "**Loyal Preservation** — `lp`"
        ), inline=True)
        embed.add_field(name="Affliction Skills", value=(
            "**Hitman** — `hitman`\n"
            "**Uproar** — `uproar`\n"
            "**Killing Spree** — `spree, ks`\n"
            "**Ambush** — `ambush`\n"
            "**Blind** — `blind`\n"
            "**Poison Dart** — `poison, pd`\n"
            "**Circle of Protection** — `circle, cop`\n"
            "**Sunder Armor** — `sunder`\n"
            "**Vanish** — `vanish`\n"
            "**Time Warp** — `warp, tw`\n"
            "**Loyal Affliction** — `la`"
        ), inline=True)
        embed.add_field(name="Misc Skills", value=(
            "**Shield Wall** — `sw, shield`\n"
            "**God Slayer** — `gs, slayer`\n"
            "**Daily Grind** — `daily, dg`\n"
            "**Triworld Influence** — `triworld, ti`"
        ), inline=True)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Potions
    # ------------------------------------------------------------------

    @commands.command(name="drink")
    async def drink(self, ctx, crew_or_group: str, potion: str):
        """Use a potion on all characters in a group or crew."""
        await self._drink_potion(ctx, crew_or_group, potion)

    @commands.command(name="drink-all")
    async def drink_all(self, ctx, *, crew: str):
        """Use all standard potions on a crew with a 5s delay."""
        for pot in DRINK_ALL_ORDER:
            await self._drink_potion(ctx, crew, pot)
            await asyncio.sleep(5)
        await ctx.send("Done casting all pots!")

    @commands.command(name="boss-pots", aliases=["b-pots"])
    async def boss_pots(self, ctx, boss: str, crew: str = "to"):
        """Use boss-specific potions on a crew. Alias: !b-pots"""
        boss_key = boss.lower()
        if boss_key not in BOSS_POTS:
            await ctx.send(f"Unknown boss `{boss}`. Known: {', '.join(BOSS_POTS.keys())}")
            return
        pot_list = BOSS_POTS[boss_key]
        await ctx.send(f"Casting pots for **{boss}**.")
        for pot in pot_list:
            await self._drink_potion(ctx, crew, pot)
            await asyncio.sleep(8)
        await ctx.send(f"Done with pots for **{boss}**.")

    # ------------------------------------------------------------------
    # Markdown checking
    # ------------------------------------------------------------------

    @commands.command(name="check-md")
    async def check_md(self, ctx, *, crew_name: str):
        """Check Markdown status for a crew — paginated with reaction navigation."""
        crew = db.get_crew(crew_name)
        if not crew:
            await ctx.send(f"Crew `{crew_name}` not recognised.")
            return

        msg = await ctx.send(f"⏳ Checking MD for **{crew['full_name']}**...")
        trustees = db.get_trustees_by_crew(crew["full_name"])

        import re as _re
        from yarl import URL as _URL
        SIGIL = _URL("https://sigil.outwar.com")

        active_list   = []  # {name, secs_left}
        ready_list    = []  # name
        cooldown_list = []  # {name, secs}
        not_trained   = []  # name
        not_maxed     = []  # name

        # Use persistent MD state first — only network-poll for unknowns
        md_state = db.get_md_state()
        now_ts   = datetime.now().timestamp() if 'datetime' in dir() else __import__('datetime').datetime.now().timestamp()

        async def _check(t):
            suid = t.get("suid")
            if not suid:
                not_trained.append(t["name"])
                return

            record = md_state.get(str(suid))
            if record and record.get("cast_at"):
                from cogs.boss_raid_commands import md_status_from_cast, MD_ACTIVE_SECS
                status, ready_at = md_status_from_cast(record["cast_at"], now_ts)
                if status == "active":
                    secs_left = int((record["cast_at"] + MD_ACTIVE_SECS) - now_ts)
                    active_list.append({"name": t["name"], "secs": max(0, secs_left)})
                    return
                if status == "ready":
                    ready_list.append(t["name"])
                    return
                if status == "cooldown":
                    secs_remaining = int(ready_at - now_ts)
                    cooldown_list.append({"name": t["name"], "secs": max(0, secs_remaining)})
                    return

            # No record — poll network
            try:
                self.session._session.cookie_jar.update_cookies(
                    {"ow_userid": str(suid)}, response_url=SIGIL
                )
                html = await self.session.get("skills_info.php?id=3014")
            finally:
                self.session._session.cookie_jar.update_cookies(
                    {"ow_userid": str(self.session.user_id)}, response_url=SIGIL
                )

            lvl_m = _re.search(r"Markdown Level (\d+)", html)
            level = int(lvl_m.group(1)) if lvl_m else 0
            if level <= 1:
                not_trained.append(t["name"])
                return
            if level < 10:
                not_maxed.append(t["name"])
                return

            if "recharging" in html.lower():
                cd_m = _re.search(r"(\d+)\s*minutes?\s*remaining", html, _re.I)
                remaining_mins = int(cd_m.group(1)) if cd_m else 0
                if remaining_mins > 384:
                    active_secs = (remaining_mins - 384) * 60
                    active_list.append({"name": t["name"], "secs": active_secs})
                else:
                    cooldown_list.append({"name": t["name"], "secs": remaining_mins * 60})
                return

            ready_list.append(t["name"])

        for t in trustees:
            await _check(t)

        # Build pages
        highest_cd_secs = max((r["secs"] for r in cooldown_list), default=0)
        total = len(active_list) + len(ready_list) + len(cooldown_list) + len(not_trained) + len(not_maxed)

        def _fmt_time(secs):
            h, rem = divmod(int(secs), 3600)
            m = rem // 60
            return f"{h}h {m}m" if h else f"{m}m"

        def _make_summary():
            e = discord.Embed(
                title=f"Skill Check for {crew['full_name']}",
                colour=discord.Colour.blurple()
            )
            e.add_field(name="Markdown Status", value=(
                f"Not ready for **{_fmt_time(highest_cd_secs)}**" if highest_cd_secs > 0
                else "✅ Ready to cast" if ready_list
                else "✅ All active" if active_list
                else "—"
            ), inline=False)
            e.add_field(name="Active",      value=str(len(active_list)),   inline=True)
            e.add_field(name="Ready",       value=str(len(ready_list)),    inline=True)
            e.add_field(name="Cooldown",    value=str(len(cooldown_list)), inline=True)
            e.add_field(name="Not Trained", value=str(len(not_trained)),   inline=True)
            e.add_field(name="Not Maxed",   value=str(len(not_maxed)),     inline=True)
            e.set_footer(text="◀ ▶ to navigate pages")
            return e

        def _names_page(title, names, colour):
            e = discord.Embed(title=title, colour=colour)
            if not names:
                e.description = "*None*"
            else:
                # Split into chunks of ~50 names per field
                chunk = 50
                for i in range(0, len(names), chunk):
                    e.add_field(
                        name=f"{title} ({i+1}–{min(i+chunk, len(names))})" if len(names) > chunk else title,
                        value=" | ".join(names[i:i+chunk]),
                        inline=False
                    )
            e.set_footer(text=f"◀ ▶ to navigate  •  Page {{page}}/{{total}}")
            return e

        pages = [
            _make_summary(),
            _names_page("Active",      [r["name"] for r in active_list],   discord.Colour.green()),
            _names_page("Ready",       ready_list,                          discord.Colour.blue()),
            _names_page("Cooldown",    [r["name"] for r in sorted(cooldown_list, key=lambda x: x["secs"], reverse=True)], discord.Colour.orange()),
            _names_page("Not Trained", not_trained,                         discord.Colour.red()),
            _names_page("Not Maxed",   not_maxed,                           discord.Colour.dark_grey()),
        ]

        # Stamp page numbers
        for i, e in enumerate(pages):
            if e.footer and e.footer.text and "{page}" in e.footer.text:
                e.set_footer(text=e.footer.text.format(page=i+1, total=len(pages)))

        current = [0]

        await msg.delete()
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

        # Register listeners for both add and remove events
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_target(self, target: str) -> list:
        all_trustees = db.get_trustees()
        rga_group = db.get_group(target)
        if rga_group:
            names = set(db.group_to_list(rga_group))
            return [t for t in all_trustees if t["name"] in names]
        crew = db.get_crew(target)
        crew_full = crew["full_name"] if crew else db.normalize_crew(target)
        by_crew = db.get_trustees_by_crew(crew_full)
        if by_crew:
            return by_crew
        return [t for t in all_trustees if t["name"].lower() == target.lower()]

    async def _fetch_character(self, name: str):
        try:
            html = await self.session.get(f"profile.php?transnick={name}&server=1")
            return parse_character_profile(html, name)
        except Exception:
            return None

    async def _get_characters_for_group(self, group: str) -> list:
        rga_group = db.get_group(group)
        if not rga_group:
            return []
        names = db.group_to_list(rga_group)
        results = await asyncio.gather(*[self._fetch_character(n) for n in names])
        return [c for c in results if c]

    async def _cast_skill_group(self, ctx, target: str, skill_ids: list, group_label: str):
        """Cast a group of skills on all characters in a target."""
        trustees = self._resolve_target(target)
        if not trustees:
            await ctx.send(f"No characters found for `{target}`.")
            return

        await ctx.send(
            f"Casting **{group_label}** skills on **{len(trustees)}** character(s)..."
        )

        results = {}  # name -> {cast, skipped}

        async def _cast_group(t):
            suid = t.get("suid") or _extract_suid(t.get("url", ""))
            cast_count = 0
            skip_count = 0
            try:
                self._switch_to(suid)
                for skill_id in skill_ids:
                    resp = await self.session.post("cast_skills.php", {
                        "castskillid": str(skill_id),
                        "cast": "Cast Skill"
                    })
                    if "You just cast" in resp:
                        cast_count += 1
                    else:
                        skip_count += 1
            except Exception as e:
                logger.warning("GUARD", f"Cast group error for {t['name']}: {e}")
            finally:
                self._switch_to_bot()
            results[t["name"]] = {"cast": cast_count, "skipped": skip_count}

        await asyncio.gather(*[_cast_group(t) for t in trustees])

        total_cast = sum(r["cast"] for r in results.values())
        total_skip = sum(r["skipped"] for r in results.values())

        embed = es.report_embed(
            f"{group_label} Skills — Cast Report",
            description=(
                f"👥 **{len(trustees)}** character(s)  ·  "
                f"✅ **{total_cast}** cast  ·  "
                f"⚠️ **{total_skip}** skipped"
            )
        )
        await ctx.send(embed=embed)

    async def _cast_skill_for_group(self, ctx, group: str, skill_id: int):
        trustees = self._resolve_target(group)
        skill_name = SKILL_NAMES.get(skill_id, str(skill_id))

        if not trustees:
            await ctx.send(f"Group/Crew `{group}` not found!")
            return

        await ctx.send(f"Casting **{skill_name}** on {len(trustees)} characters...")
        success = 0

        async def _cast(t):
            nonlocal success
            try:
                suid = t.get("suid") or _extract_suid(t.get("url", ""))
                self._switch_to(suid)
                resp = await self.session.post("cast_skills.php", {
                    "castskillid": str(skill_id),
                    "cast": "Cast Skill"
                })
                if "You just cast" in resp:
                    success += 1
            except Exception as e:
                logger.warning("GUARD", f"Failed to cast '{skill_name}' for {t['name']}: {e}")
            finally:
                self._switch_to_bot()

        await asyncio.gather(*[_cast(t) for t in trustees])
        await ctx.send(f"**{skill_name}** cast on {success}/{len(trustees)} characters.")

    async def _drink_potion(self, ctx, crew_or_group: str, potion_key: str):
        potion_name = POTIONS.get(potion_key.lower())
        if not potion_name:
            await ctx.send(f"Unknown potion: `{potion_key}`")
            return

        trustees = self._resolve_target(crew_or_group)
        await ctx.send(f"Using **{potion_name}** for {len(trustees)} characters...")
        used           = []
        already_active = []
        missing        = []
        errors         = []
        sem = asyncio.Semaphore(5)

        async def _use_potion(t):
            suid = t.get("suid") or _extract_suid(t.get("url", ""))
            if not suid:
                errors.append(f"{t['name']} (no suid)")
                return
            async with sem:
                try:
                    self._switch_to(suid)
                    html = await self.session.get("ajax/backpackcontents.php?tab=potion")
                    items = parse_backpack_for_item(html, potion_name)
                    # For Remnant Solice, always use highest level available
                    if items and "remnant solice" in potion_name.lower():
                        import re as _re
                        def _rem_level(item):
                            m = _re.search(r"Lev\s*(\d+)", item["item_name"], _re.IGNORECASE)
                            return int(m.group(1)) if m else 0
                        items = sorted(items, key=_rem_level, reverse=True)
                    if items:
                        item = items[0]
                        resp = await self.session.post("ajax/backpack_action.php", {
                            "action":    "activate",
                            "itemids[]": item["item_id"],
                        })
                        resp_str = str(resp)
                        if "invalid item for action" in resp_str.lower():
                            # Most likely already active — the item exists in the
                            # backpack but can't be re-activated. Treat as success
                            # since the desired end state (potion active) is already met.
                            already_active.append(t["name"])
                        elif "error" in resp_str.lower():
                            errors.append(f"{t['name']}: {resp_str[:60]}")
                        else:
                            used.append(f"{t['name']} (id:{item['item_id']})")
                        logger.info("POT", f"{t['name']} used {potion_name} id={item['item_id']} resp={resp_str[:80]}")
                        await asyncio.sleep(0.2)
                    else:
                        missing.append(t["name"])
                except Exception as e:
                    errors.append(f"{t['name']}: {e}")
                finally:
                    self._switch_to_bot()

        await asyncio.gather(*[_use_potion(t) for t in trustees])

        embed = es.report_embed(f"🧪 Potion Report — {potion_name}")
        embed.add_field(
            name=f"✅ Used ({len(used)})",
            value=" | ".join(used)[:1024] if used else "None",
            inline=False
        )
        if already_active:
            embed.add_field(
                name=f"⏳ Already active ({len(already_active)})",
                value=" | ".join(already_active)[:1024],
                inline=False
            )
        if missing:
            embed.add_field(
                name=f"❌ Not in backpack ({len(missing)})",
                value=" | ".join(missing)[:1024],
                inline=False
            )
        if errors:
            embed.add_field(
                name=f"⚠️ Errors ({len(errors)})",
                value=" | ".join(errors)[:1024],
                inline=False
            )
        await ctx.send(embed=embed)

    @staticmethod
    def _add_char_field(embed: discord.Embed, title: str, chars: list):
        chunk = ""
        for c in chars:
            part = c["name"] + " | "
            if len(chunk) + len(part) > 1000:
                embed.add_field(name=title, value=chunk.rstrip(" |"), inline=False)
                chunk = ""
            chunk += part
        if chunk:
            embed.add_field(name=title, value=chunk.rstrip(" |"), inline=False)


async def setup(bot):
    await bot.add_cog(CharacterCommands(bot))
