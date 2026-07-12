"""
primewatcher.py — Auto-raid Prime Gods on spawn (Phase 1: configuration).

A "prime watcher" is a named, independently on/off-able set of:
  - groups  : bot groups used to raid (each kept INTACT — accounts are never mixed
              across groups), each with an optional skill setting (none / class / raid)
  - primes  : Prime Gods to cap, each with its own cap target (per prime, not shared)

You can have several watchers at once, e.g. one for groups 1-3 on gods A/B/C and
another for groups 4-10 on gods D-I, and turn each on or off separately.

Phase 2 (the raiding engine — xx:10 scheduler, intact-group cap-aware retry, the
per-cycle breakdown, and rec auto-lowering) plugs into this config without changing it.
"""

import asyncio
import re
import random
from datetime import datetime, timedelta, date

import discord
from discord.ext import commands

from cogs import embed_style as es
from cogs.auth import is_authorised
from outwar import database as db, logger
from outwar.scraper import parse_prime_god_page

SKILL_CHOICES = ("none", "class", "raid")


class _Shim:
    """Minimal ctx stand-in so the engine can drive _cast_skill_group, which only
    uses ctx.send. Sends route to the watcher's report channel (or are dropped)."""
    def __init__(self, channel):
        self.channel = channel

    async def send(self, *args, **kwargs):
        if self.channel:
            try:
                return await self.channel.send(*args, **kwargs)
            except Exception:
                pass


def _norm(name: str) -> str:
    return name.strip().lower()


def _skill_label(p: str) -> str:
    return "no skills" if p == "none" else f"{p} skills"


class PrimeWatcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._task = None
        self._started = False

    @commands.Cog.listener()
    async def on_ready(self):
        # Start the scheduler once the bot is connected (loop is running here).
        if not self._started:
            self._started = True
            try:
                self._task = asyncio.create_task(self._scheduler())
            except Exception as e:
                logger.error("PW", f"failed to start scheduler: {e}")

    def cog_unload(self):
        if self._task:
            self._task.cancel()

    async def cog_check(self, ctx):
        # Whole watcher system is admin-only (it controls raiding).
        return is_authorised(ctx.author.id, "admin")

    # ----- helpers ---------------------------------------------------------
    def _get(self, name: str):
        return db.get_primewatchers().get(_norm(name))

    def _save(self, name: str, watcher: dict):
        data = db.get_primewatchers()
        data[_norm(name)] = watcher
        db.save_primewatchers(data)

    def _resolve_group(self, group: str):
        """Resolve a group name from the saved (autorank) groups.
        Accepts the full name (e.g. LOD4) or a bare number (4) if unambiguous.
        Returns (resolved_name, error_message_or_None)."""
        g = db.get_group(group)
        if g:
            return g["name"], None
        if group.isdigit():
            cands = [
                gg["name"] for gg in db.get_groups()
                if gg["name"][-len(group):] == group and gg["name"][:-len(group)].isalpha()
            ]
            if len(cands) == 1:
                return cands[0], None
            if len(cands) > 1:
                return None, f"`{group}` is ambiguous — matches {', '.join(cands)}. Use the full name."
        return None, None

    # ----- group / overview ------------------------------------------------
    @commands.group(name="primewatcher", aliases=["pw"], invoke_without_command=True)
    async def primewatcher(self, ctx):
        """Prime God auto-raiding. Use !pw help for the full command list."""
        data = db.get_primewatchers()
        if not data:
            await ctx.send(embed=es.info_embed(
                "🛰️ Prime Watchers",
                "No watchers yet.\n\n"
                "**Set one up:**\n"
                "`!pw create <name>`\n"
                "`!pw add-group <name> <group…> [none|class|raid]`\n"
                "`!pw add-prime <name> [caps] <god…>`\n"
                "`!pw on <name>`\n\n"
                "`!pw help` for everything.",
            ))
            return
        lines = []
        for w in data.values():
            state = "🟢 ON" if w.get("enabled") else "⚪ off"
            mode = "🔓 open" if w.get("mode", "closed") == "open" else "🔒 closed"
            lines.append(
                f"**{w['name']}** — {state} · {mode} · "
                f"{len(w.get('groups', []))} group(s) · {len(w.get('primes', {}))} prime(s)"
            )
        await ctx.send(embed=es.info_embed(
            "🛰️ Prime Watchers",
            "\n".join(lines) + "\n\nUse `!pw show <name>` for details, `!pw help` for commands.",
        ))

    @primewatcher.command(name="help")
    async def pw_help(self, ctx):
        await ctx.send(embed=es.info_embed(
            "🛰️ Prime Watcher — Commands",
            "**Setup**\n"
            "`!pw create <name>` — make a new watcher\n"
            "`!pw delete <name>` — remove a watcher\n"
            "`!pw add-group <name> <group…> [none|class|raid]` — add one or more groups (skills apply to all)\n"
            "`!pw remove-group <name> <group>`\n"
            "`!pw add-prime <name> [caps] <god…>` — add one or more primes (bare number = shared cap; god:N per-god)\n"
            "`!pw remove-prime <name> <prime>`\n"
            "`!pw set-crew <name> <crew>` — crew to count caps for (default Legion of Death)\n\n"
            "**Run**\n"
            "`!pw on <name>` / `!pw off <name>` — enable/disable\n"
            "`!pw show <name>` — full details of one watcher\n"
            "`!pw` — overview of all watchers\n\n"
            "_Each group is kept intact — accounts are never mixed across groups. "
            "Caps are total per prime, not per group._",
        ))

    # ----- create / delete -------------------------------------------------
    @primewatcher.command(name="create")
    async def pw_create(self, ctx, *, name: str):
        if self._get(name):
            await ctx.send(f"A watcher named **{name}** already exists.")
            return
        self._save(name, {
            "name": name.strip(),
            "enabled": False,
            "crew": "Legion of Death",
            "groups": [],
            "primes": {},
            "mode": "closed",   # default: groups stay intact. !pw open <name> to pool.
        })
        await ctx.send(
            f"✅ Created watcher **{name.strip()}**.\n"
            f"Now add groups (`!pw add-group {name.strip()} <group…> [skills]`) and "
            f"primes (`!pw add-prime {name.strip()} [caps] <god…>`)."
        )

    @primewatcher.command(name="delete")
    async def pw_delete(self, ctx, *, name: str):
        data = db.get_primewatchers()
        if _norm(name) not in data:
            await ctx.send(f"No watcher named **{name}**.")
            return
        removed = data.pop(_norm(name))
        db.save_primewatchers(data)
        await ctx.send(f"🗑️ Deleted watcher **{removed['name']}**.")

    # ----- groups ----------------------------------------------------------
    @primewatcher.command(name="add-group")
    async def pw_add_group(self, ctx, name: str, *args: str):
        """Add one or more groups at once. An optional trailing skills tier (none/class/raid)
        applies to all of them. e.g. `!pw add-group myw LOD11 LOD12 LOD13 raid`"""
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**. Create it with `!pw create {name}`.")
            return
        tokens = list(args)
        if not tokens:
            await ctx.send("Usage: `!pw add-group <watcher> <group> [group2 …] [none|class|raid]`")
            return
        skills = "none"
        if tokens[-1].lower() in SKILL_CHOICES:
            skills = tokens.pop().lower()
        if not tokens:
            await ctx.send("Give me at least one group to add.")
            return
        added, skipped = [], []
        for grp in tokens:
            resolved, err = self._resolve_group(grp)
            if err or not resolved:
                skipped.append(f"{grp}")
                continue
            gu = resolved.upper()
            w["groups"] = [g for g in w["groups"] if g["group"].upper() != gu]
            w["groups"].append({"group": gu, "skills": skills})
            added.append(gu)
        self._save(name, w)
        lines = []
        if added:
            lines.append(f"✅ Added {len(added)} group{'s' if len(added) != 1 else ''} "
                         f"({_skill_label(skills)}) to **{w['name']}**: {', '.join(added)}")
        if skipped:
            available = [g["name"] for g in db.get_groups()]
            hint = f" Available: {', '.join(available[:30])}" if available else ""
            lines.append(f"⚠️ Not found: {', '.join(skipped)}.{hint}")
        await ctx.send("\n".join(lines) or "Nothing added.")

    @primewatcher.command(name="remove-group")
    async def pw_remove_group(self, ctx, name: str, group: str):
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        before = len(w["groups"])
        w["groups"] = [g for g in w["groups"] if g["group"].upper() != group.upper()]
        self._save(name, w)
        if len(w["groups"]) < before:
            await ctx.send(f"✅ Removed group **{group.upper()}** from **{w['name']}**.")
        else:
            await ctx.send(f"Group **{group}** wasn't in **{w['name']}**.")

    # ----- primes ----------------------------------------------------------
    @primewatcher.command(name="add-prime")
    async def pw_add_prime(self, ctx, name: str, *args: str):
        """Add one or more primes at once. A bare number is the shared cap for all; use god:N
        for a per-god cap. e.g. `!pw add-prime myw 1 kretok sarcrina volgan`  or
        `!pw add-prime myw kretok sarcrina volgan:2` (default ×1, volgan ×2)."""
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**. Create it with `!pw create {name}`.")
            return
        tokens = list(args)
        if not tokens:
            await ctx.send("Usage: `!pw add-prime <watcher> [caps] <god> [god2 …]`  "
                           "(caps default 1; use god:N for a per-god cap)")
            return
        shared_cap = None
        entries = []  # (god_token, explicit_cap_or_None)
        for tok in tokens:
            if tok.isdigit():
                if shared_cap is None:
                    shared_cap = int(tok)
                continue
            if ":" in tok or "=" in tok:
                sep = ":" if ":" in tok else "="
                g, _, c = tok.partition(sep)
                try:
                    entries.append((g, int(c)))
                except ValueError:
                    entries.append((g, None))
                continue
            entries.append((tok, None))
        if shared_cap is None:
            shared_cap = 1
        if not entries:
            await ctx.send("Give me at least one prime to add.")
            return
        added, skipped = [], []
        for g_tok, c in entries:
            cap = c if c is not None else shared_cap
            if cap < 1 or cap > 50:
                skipped.append(f"{g_tok} (caps 1–50)")
                continue
            god = db.get_prime_god(g_tok)
            if not god:
                skipped.append(f"{g_tok} (unknown)")
                continue
            w["primes"][god["name"]] = cap
            added.append(f"{god['name']} ×{cap}")
        self._save(name, w)
        lines = []
        if added:
            lines.append(f"✅ **{w['name']}** will cap {len(added)} prime"
                         f"{'s' if len(added) != 1 else ''}: " + " · ".join(added))
        if skipped:
            lines.append(f"⚠️ Skipped: {', '.join(skipped)}")
        await ctx.send("\n".join(lines) or "Nothing added.")

    @primewatcher.command(name="remove-prime")
    async def pw_remove_prime(self, ctx, name: str, *, prime: str):
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        god = db.get_prime_god(prime)
        target = god["name"] if god else prime
        if target in w["primes"]:
            w["primes"].pop(target)
            self._save(name, w)
            await ctx.send(f"✅ Removed **{target}** from **{w['name']}**.")
        else:
            await ctx.send(f"**{target}** wasn't in **{w['name']}**.")

    # ----- crew ------------------------------------------------------------
    @primewatcher.command(name="set-crew")
    async def pw_set_crew(self, ctx, name: str, *, crew: str):
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        w["crew"] = crew.strip()
        self._save(name, w)
        await ctx.send(f"✅ **{w['name']}** will count caps for crew **{crew.strip()}**.")

    # ----- on / off --------------------------------------------------------
    @primewatcher.command(name="on")
    async def pw_on(self, ctx, *, name: str):
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        if not w["groups"] or not w["primes"]:
            await ctx.send(f"⚠️ **{w['name']}** needs at least one group and one prime before it can run.")
            return
        w["enabled"] = True
        self._save(name, w)
        await ctx.send(
            f"🟢 **{w['name']}** is ON — it will check at :10 past each hour and raid any "
            f"watched prime that's up until its cap target is met."
        )

    @primewatcher.command(name="off")
    async def pw_off(self, ctx, *, name: str):
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        w["enabled"] = False
        self._save(name, w)
        await ctx.send(f"⚪ **{w['name']}** is OFF.")

    @primewatcher.command(name="open")
    async def pw_open(self, ctx, *, name: str):
        """Pool ALL this watcher's accounts across its groups. For each prime, the
        bot picks the required number of members (max_members) by most caps
        available — so bigger primes (20/30-man) can be filled from several
        groups. Use `!pw closed <name>` to revert to intact groups."""
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        w["mode"] = "open"
        self._save(name, w)
        await ctx.send(
            f"🔓 **{w['name']}** is now **OPEN** — accounts are pooled across all its "
            f"groups and picked by most caps available to fill each prime's required size."
        )

    @primewatcher.command(name="closed")
    async def pw_closed(self, ctx, *, name: str):
        """Revert to the default: each group stays intact, accounts never mixed."""
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        w["mode"] = "closed"
        self._save(name, w)
        await ctx.send(
            f"🔒 **{w['name']}** is now **CLOSED** — each group stays in its own lane "
            f"(accounts never mixed across groups)."
        )

    # ----- show ------------------------------------------------------------
    @primewatcher.command(name="show")
    async def pw_show(self, ctx, *, name: str):
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        state = "🟢 ON" if w.get("enabled") else "⚪ off"
        glines = "\n".join(
            f"• **{g['group']}** — {_skill_label(g.get('skills', g.get('pots','none')))}" for g in w["groups"]
        ) or "_none yet_"

        crew = w.get("crew", "Legion of Death")
        primes = list(w.get("primes", {}).items())

        async def _inspect(prime_name, target):
            god = db.get_prime_god(prime_name)
            label = god["name"] if god else prime_name
            head = f"• **{label}** — {target} cap{'s' if target != 1 else ''}"
            if not god:
                return f"{head}\n   ⚠️ Not in god database"
            try:
                html = await self.bot.outwar.get(f"primegods?mobid={god['god_id']}")
                page = parse_prime_god_page(html)
            except Exception:
                return f"{head}\n   ⚠️ Couldn't read live status"
            if not page.get("spawned"):
                return f"{head}\n   💤 Not spawned"
            cur = self._crew_caps_on_spawn(page, crew)
            if cur >= target:
                return f"{head}\n   ✅ Complete ({cur}/{target})"
            rem = target - cur
            return f"{head}\n   🟠 {rem} cap{'s' if rem != 1 else ''} remaining ({cur}/{target})"

        msg = await ctx.send("⏳ Checking live cap status…")
        if primes:
            results = await asyncio.gather(*[_inspect(p, c) for p, c in primes])
            plines = "\n".join(results)
        else:
            plines = "_none yet_"
        try:
            await msg.delete()
        except Exception:
            pass

        _mode = w.get("mode", "closed")
        _mode_lbl = "🔓 open (accounts pooled across groups)" if _mode == "open" \
            else "🔒 closed (groups kept intact)"
        _groups_hdr = "**Groups** (pooled — open mode)" if _mode == "open" \
            else "**Groups** (kept intact)"
        await ctx.send(embed=es.info_embed(
            f"🛰️ {w['name']}",
            f"Status: {state}\nMode: {_mode_lbl}\nCrew: **{crew}**\n\n"
            f"{_groups_hdr}\n{glines}\n\n"
            f"**Primes** (caps are per prime)\n{plines}",
        ))


    # ======================================================================
    # Phase 2 — raiding engine
    # ======================================================================

    async def _scheduler(self):
        """Fire a cycle at :10 past every hour for all enabled watchers."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now()
            nxt = now.replace(minute=10, second=0, microsecond=0)
            if now >= nxt:
                nxt += timedelta(hours=1)
            await asyncio.sleep(max(1, (nxt - now).total_seconds()))
            try:
                await self._run_all_enabled()
            except Exception as e:
                logger.error("PW", f"scheduler cycle error: {e}")

    async def _report_channel(self, w):
        """Resolve where to post: the watcher's own channel, else the Log/#primewatcher
        alert channel, else summary/gods. Falls back to fetch_channel on a cache miss."""
        candidates = [w.get("channel")]
        for key in ("log", "summary", "gods", "god"):
            try:
                candidates.append(db.get_alert_channel(key))
            except Exception:
                pass
        for cid in candidates:
            if not cid:
                continue
            ch = self.bot.get_channel(int(cid))
            if ch:
                return ch
            try:
                return await self.bot.fetch_channel(int(cid))
            except Exception:
                continue
        return None

    async def _run_all_enabled(self, dry_run=False, only=None, channel=None):
        data = db.get_primewatchers()
        for key, w in data.items():
            if only and key != only:
                continue
            if not dry_run and not w.get("enabled"):
                continue
            ch = channel or await self._report_channel(w)
            try:
                await self._run_cycle(w, dry_run, ch)
            except Exception as e:
                logger.error("PW", f"cycle error for {w.get('name')}: {e}")
                if ch:
                    await ch.send(f"⚠️ Prime Watcher **{w.get('name')}** cycle error: `{e}`")

    @staticmethod
    def _crew_caps_on_spawn(page, crew):
        """How many caps the crew currently has on this spawn's leaderboard."""
        cl = crew.lower()
        for s in page.get("stats", []):
            sc = s.get("crew", "").lower()
            if sc == cl or cl in sc or sc in cl:
                return s.get("kills", 0)
        return 0

    def _assign_groups(self, groups, prime_names, seed_day):
        """Even-spread assignment that rotates daily: one primary group per prime,
        cycling through the bundle, rotated by the day number so it differs each day."""
        if not groups:
            return {}
        n = len(groups)
        rot = seed_day % n
        rotated = groups[rot:] + groups[:rot]
        return {prime: rotated[i % n] for i, prime in enumerate(prime_names)}

    async def _cast_for_group(self, group_name, skills, channel):
        """Cast the configured skills on a group before it raids."""
        if skills == "none":
            return
        char_cog = self.bot.get_cog("CharacterCommands")
        if not char_cog:
            return
        try:
            if skills == "class":
                from cogs.character_commands import CLASS_SKILLS
                await char_cog._cast_skill_group(_Shim(channel), group_name, CLASS_SKILLS, "Class")
            elif skills == "raid":
                from cogs.boss_raid_commands import (
                    BOSS_SKILLS_CLASS, BOSS_SKILLS_PRES, BOSS_SKILLS_MISC, ROTATING_SKILLS
                )
                raid_skills = (BOSS_SKILLS_CLASS
                               + [s for s in BOSS_SKILLS_PRES if s not in ROTATING_SKILLS]
                               + BOSS_SKILLS_MISC)
                await char_cog._cast_skill_group(_Shim(channel), group_name, raid_skills, "Raid Skills")
        except Exception as e:
            logger.warning("PW", f"skill cast failed for {group_name}: {e}")

    @staticmethod
    def _hp_from_note(note):
        """Pull the leftover HP% out of a raid note like 'god left at ~9.0% HP'."""
        if not note:
            return None
        m = re.search(r"~?([\d.]+)\s*%\s*HP", note)
        return float(m.group(1)) if m else None

    def _render_cycle(self, w, dry_run, status):
        """Render the single cycle message. Mirrors !pw show icons:
        ✅ complete · 🟠 in progress / not met · 💤 not spawned · 🔴 all capped · ⚠️ error."""
        head = f"\U0001F6F0\uFE0F {w['name']} \u2014 {'Dry Run' if dry_run else 'Cycle'} @ {datetime.now():%H:%M}"
        lines = []
        for st in status:
            g = st["god"]; tgt = st["target"]; got = st["got"]; at = st["attempts"]
            grp = ", ".join(st["groups"]) or "\u2014"
            best = f" \u00b7 best {st['best_hp']:.0f}% HP" if st.get("best_hp") is not None else ""
            state = st["state"]
            if state == "not_spawned":
                lines.append(f"\U0001F4A4 **{g}** \u2014 not spawned")
            elif state == "met":
                lines.append(f"\u2705 **{g}** \u2014 already at cap ({got}/{tgt}) \u00b7 not raided this cycle")
            elif state == "queued":
                lines.append(f"\u23F3 **{g}** \u2014 queued ({got}/{tgt})")
            elif state == "raiding":
                lines.append(f"\U0001F5E1\uFE0F **{g}** ({grp}) \u2014 raiding\u2026 {at}/10 \u00b7 {got}/{tgt}{best}")
            elif state == "done_met":
                lines.append(f"\u2705 **{g}** ({grp}) \u2014 {got}/{tgt} caps \u00b7 {at} raid(s)")
            elif state == "capped":
                lines.append(f"\U0001F534 **{g}** \u2014 all groups capped")
            elif state == "error":
                lines.append(f"\u26A0\uFE0F **{g}** \u2014 {st.get('note', 'error')}")
            else:  # done_unmet
                note = f" \u00b7 {st['note']}" if st.get("note") else ""
                lines.append(f"\U0001F7E0 **{g}** ({grp}) \u2014 {got}/{tgt} caps \u00b7 {at} raid(s){best}{note}")
        body = "\n".join(lines) if lines else "Nothing configured."
        body += "\n\n\u23F3 Next cycle at :10 past the hour"
        return head, body[:4000]

    async def _send_cycle_report(self, channel, w, dry_run, status):
        if not channel:
            return None
        head, body = self._render_cycle(w, dry_run, status)
        try:
            return await channel.send(embed=es.info_embed(head, body))
        except Exception:
            return None

    async def _edit_cycle_report(self, msg, w, dry_run, status):
        if not msg:
            return
        head, body = self._render_cycle(w, dry_run, status)
        try:
            await msg.edit(embed=es.info_embed(head, body))
        except Exception:
            pass

    async def _run_cycle(self, w, dry_run, channel):
        """One :10 cycle for a single watcher. Posts ONE message that updates in place
        (no per-attempt spam), then settles into the final per-prime breakdown."""
        raid_cog = self.bot.get_cog("RaidCommands")
        if not raid_cog:
            return
        crew = w.get("crew", "Legion of Death")
        groups = w.get("groups", [])
        seed_day = (date.today() - date(2020, 1, 1)).days

        # 1) Inspect each watched prime -> ordered status list
        status = []   # one dict per prime, in config order
        spawned = []  # (status_index, god, current, target, page)
        for prime_name, target in w.get("primes", {}).items():
            god = db.get_prime_god(prime_name)
            label = god["name"] if god else prime_name
            st = {"god": label, "target": target, "got": 0, "attempts": 0,
                  "groups": [], "best_hp": None, "state": "queued", "note": ""}
            if not god:
                st["state"] = "error"; st["note"] = "not in god database"
                status.append(st); continue
            try:
                html = await self.bot.outwar.get(f"primegods?mobid={god['god_id']}")
                page = parse_prime_god_page(html)
            except Exception:
                st["state"] = "error"; st["note"] = "page fetch failed"
                status.append(st); continue
            if not page.get("spawned"):
                st["state"] = "not_spawned"
                status.append(st); continue
            current = self._crew_caps_on_spawn(page, crew)
            st["got"] = current
            if current >= target:
                st["state"] = "met"
                status.append(st); continue
            spawned.append((len(status), god, current, target, page))
            status.append(st)

        # 2) Assign groups (even spread, rotates daily)
        assignment = self._assign_groups(
            groups, [status[idx]["god"] for idx, *_ in spawned], seed_day)

        # 3) Post the single live message, then raid each prime
        report = await self._send_cycle_report(channel, w, dry_run, status)
        self._last_edit = 0.0   # monotonic timestamp of last live edit (throttle)

        async def _live_edit(force=False):
            now = asyncio.get_event_loop().time()
            if force or (now - self._last_edit) >= 2.5:
                self._last_edit = now
                await self._edit_cycle_report(report, w, dry_run, status)

        # Watcher mode: "closed" (default) keeps each group intact — accounts never
        # mixed. "open" pools ALL the watcher's accounts and picks the required number
        # (max_members) by MOST CAPS AVAILABLE, so bigger primes (20/30-man) can be
        # filled from several groups and cap usage stays even across the pool.
        watcher_mode = w.get("mode", "closed")

        async def _open_pool_for(god) -> list:
            """Pool every account across the watcher's groups, drop capped, and
            return them sorted by most caps available (desc). Sized by the caller."""
            seen, pool = set(), []
            for g in groups:
                for t in (raid_cog._resolve_group(g["group"]) or []):
                    suid = t.get("suid")
                    if suid and suid not in seen:
                        seen.add(suid)
                        pool.append(t)
            if not pool:
                return []
            # Read caps for the whole pool once, then rank by availability.
            from outwar.scraper import parse_god_cap
            sem = asyncio.Semaphore(8)
            async def _caps(t):
                suid = t.get("suid")
                if not suid:
                    return (t, 0, 0)
                try:
                    async with sem:
                        html = await raid_cog.session.get_as("home", suid)
                    used, mx = parse_god_cap(html)
                    return (t, (mx - used) if mx else 999, mx)
                except Exception:
                    return (t, 0, 0)
            capped_info = await asyncio.gather(*[_caps(t) for t in pool])
            # Available = unknown max (0 -> treat as open) or caps remaining > 0.
            avail = [(t, a) for (t, a, mx) in capped_info if mx == 0 or a > 0]
            avail.sort(key=lambda x: x[1], reverse=True)   # most caps first
            return [t for (t, _a) in avail]

        for idx, god, current, target, page in spawned:
            st = status[idx]
            god_name = god["name"]
            assigned = assignment.get(god_name) or (groups[0] if groups else None)
            got = current
            attempts = 0
            groups_used = []
            order = ([assigned] + [g for g in groups if g["group"] != assigned["group"]]
                     ) if assigned else []

            st["state"] = "raiding"
            await _live_edit(force=True)

            # ---- OPEN MODE: pool accounts across groups, pick by most-caps ----
            if watcher_mode == "open":
                # How many accounts this prime needs (scraped max_members), default 10.
                _god_db = db.get_prime_god(god_name) or {}
                need = _god_db.get("max_members") or god.get("max_members") or 10
                while got < target and attempts < 10:
                    pool = await _open_pool_for(god)
                    if len(pool) < need:
                        st["note"] = f"pool short: {len(pool)}/{need} available (caps)"
                        break
                    squad = pool[:need]           # top `need` by caps available
                    groups_used = ["(open pool)"]
                    # Cast skills for each source group represented (skills per group).
                    for g in groups:
                        await self._cast_for_group(g["group"], g.get("skills", g.get("pots", "none")), channel)
                    won, dmg, rnote = await raid_cog._do_god_raid(None, god, squad)
                    attempts += 1
                    if won:
                        got += 1
                    else:
                        hp = self._hp_from_note(rnote)
                        if hp is not None and (st["best_hp"] is None or hp < st["best_hp"]):
                            st["best_hp"] = hp
                    st["got"] = got; st["attempts"] = attempts; st["groups"] = list(groups_used)
                    await _live_edit()
                    if got < target and attempts < 10:
                        await asyncio.sleep(3)   # same pacing as closed mode
                # finalise + skip the closed-mode loop for this god
                st["got"] = got; st["attempts"] = attempts; st["groups"] = list(groups_used)
                if got >= target:
                    st["state"] = "done_met"
                elif attempts >= 10:
                    st["state"] = "done_unmet"; st["note"] = "10-attempt limit"
                elif not groups_used:
                    st["state"] = "done_unmet"; st["note"] = st.get("note") or "pool out of caps"
                else:
                    st["state"] = "done_unmet"; st["note"] = st.get("note") or "pool out of caps"
                await _live_edit(force=True)
                continue

            gi = 0
            _skip_prime = False   # set True on a RAGE failure → stop this prime for the cycle
            while got < target and attempts < 10 and gi < len(order):
                grp = order[gi]
                gname = grp["group"]
                trustees = raid_cog._resolve_group(gname)
                if not trustees:
                    gi += 1
                    continue
                avail, capped, err = await raid_cog._check_group_caps(trustees, 1)
                if capped:
                    gi += 1   # a member is capped -> fall back to next group
                    continue
                if gname not in groups_used:
                    groups_used.append(gname)

                if dry_run:
                    st["note"] = "would raid (dry run)"
                    got = target
                    break

                skills = grp.get("skills", grp.get("pots", "none"))
                await self._cast_for_group(gname, skills, channel)

                while got < target and attempts < 10:
                    _avail_now, capped_now, _capwarn = await raid_cog._check_group_caps(trustees, 1)
                    if capped_now:
                        # NOTE: capped_now is True if ANY member is capped. Log which,
                        # so we can see whether the group broke because it's genuinely
                        # unusable or because a single account tipped over.
                        _capped_names = [t.get("name") for t in capped_now] if isinstance(capped_now, list) else capped_now
                        logger.warning(
                            "PRIMEWATCHER",
                            f"{god_name}: {gname} caps check broke loop after {attempts} "
                            f"attempt(s) — capped: {_capped_names}"
                        )
                        break  # a member capped out -> fall back to next group
                    won, dmg, rnote = await raid_cog._do_god_raid(None, god, trustees)

                    # Categorise the "not won" outcome — three distinct behaviours:
                    #   RAGE failure  → skip this PRIME entirely for the cycle. Rage
                    #     regenerates, so by the next hourly cycle the accounts will
                    #     have enough. Trying OTHER groups now is pointless (they'll
                    #     hit the same wall) and wastes their attempts — so we stop the
                    #     whole prime, not just this group.
                    #   CAPS failure  → move to the NEXT group. This group's accounts
                    #     are capped, but another group may still have caps.
                    #   JOIN/under-strength/launched-lost → the raid formed; keep the
                    #     existing retry loop (transient, or a real raid).
                    _note_l = (rnote or "").lower()
                    _rage_fail = "low rage" in _note_l or "form rage" in _note_l
                    _caps_fail = (
                        ("capped" in _note_l or "could not form" in _note_l
                         or "not spawned" in _note_l)
                        and not _rage_fail
                    )

                    if _rage_fail:
                        logger.warning(
                            "PRIMEWATCHER",
                            f"{god_name}: RAGE too low for {gname} — {rnote}; "
                            f"skipping this prime for the cycle (rage recovers next hour)"
                        )
                        st["note"] = rnote
                        _skip_prime = True
                        break  # break group loop; _skip_prime stops the prime below

                    if _caps_fail:
                        logger.warning(
                            "PRIMEWATCHER",
                            f"{god_name}: {gname} can't form (caps/spawn) — {rnote}; "
                            f"trying next group"
                        )
                        st["note"] = rnote
                        break  # move to next group — another may have caps

                    # Otherwise the raid formed (launched-and-lost, or under-strength after
                    # the rejoin). Count it as an attempt and keep the existing behaviour.
                    attempts += 1
                    if won:
                        got += 1
                    else:
                        _hp = self._hp_from_note(rnote)
                        if _hp is not None and (st["best_hp"] is None or _hp < st["best_hp"]):
                            st["best_hp"] = _hp

                    st["got"] = got
                    st["attempts"] = attempts
                    st["groups"] = list(groups_used)
                    await _live_edit()   # throttled live tick (attempts + best HP)
                    # Pace attempts: firing prime raids back-to-back with no gap
                    # saturates the shared connection budget and rate-limits the
                    # joins (which then drop, since actions don't auto-retry). A
                    # short settle between attempts lets the budget clear. Only
                    # pause if we're going to loop again.
                    if got < target and attempts < 10:
                        await asyncio.sleep(3)
                gi += 1
                # On a RAGE failure, stop the whole prime for this cycle — don't try
                # the remaining groups (they'd hit the same rage wall; rage recovers by
                # next hourly cycle). Caps failures already just `gi += 1` to the next.
                if _skip_prime:
                    break
                # Settle between groups too, so the next group's join burst doesn't
                # start while the previous one's connections are still draining.
                if got < target and gi < len(order):
                    await asyncio.sleep(3)

            # finalise this prime's state
            st["got"] = got
            st["attempts"] = attempts
            st["groups"] = list(groups_used)
            if got >= target:
                st["state"] = "done_met"
            elif _skip_prime:
                st["state"] = "done_unmet"; st["note"] = st.get("note") or "low rage — retry next cycle"
            elif not groups_used:
                st["state"] = "capped"
            elif attempts >= 10:
                st["state"] = "done_unmet"; st["note"] = "10-attempt limit"
            else:
                st["state"] = "done_unmet"; st["note"] = "groups out of caps"
            await _live_edit(force=True)

    # ----- dry run / channel ----------------------------------------------
    @primewatcher.command(name="dryrun")
    async def pw_dryrun(self, ctx, *, name: str):
        """Simulate a cycle for a watcher without raiding — shows the plan."""
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        await ctx.send(f"🔎 Dry-running **{w['name']}** — reading spawns and caps, no raids…")
        await self._run_all_enabled(dry_run=True, only=_norm(name), channel=ctx.channel)

    @primewatcher.command(name="channel")
    async def pw_channel(self, ctx, *, name: str):
        """Set where this watcher posts its per-cycle breakdown (run in the target channel)."""
        w = self._get(name)
        if not w:
            await ctx.send(f"No watcher named **{name}**.")
            return
        w["channel"] = ctx.channel.id
        self._save(name, w)
        await ctx.send(f"✅ **{w['name']}** will post breakdowns in {ctx.channel.mention}.")


    @primewatcher.command(name="capdebug")
    async def pw_capdebug(self, ctx, group: str = None):
        """Dump /crew_capstatus + home AS an in-crew raider (LoDRaid isn't in the crew).
        Usage: !pw capdebug [group]  — defaults to the first trustee if no group given."""
        import io
        raid_cog = self.bot.get_cog("RaidCommands")

        # Pick an in-crew raiding account to fetch as (NOT LoDRaid, which isn't in the crew).
        trustee = None
        if group and raid_cog:
            members = raid_cog._resolve_group(group)
            trustee = next((t for t in members if t.get("suid")), None)
        if trustee is None:
            allt = db.get_trustees()
            trustee = next((t for t in allt if t.get("suid")), None)

        if not trustee or not trustee.get("suid"):
            await ctx.send("Couldn't find a trustee with a suid to fetch as. Give me a group: `!pw capdebug <group>`.")
            return

        suid = trustee["suid"]
        who = trustee.get("name", str(suid))
        await ctx.send(f"Fetching cap pages as **{who}** (suid {suid})…")

        sent = False
        for path in ("crew_capstatus", "home"):
            try:
                html = await self.bot.outwar.get_as(path, suid)
            except Exception as e:
                await ctx.send(f"Fetch of `{path}` failed: `{e}`")
                continue
            buf = io.BytesIO(html.encode("utf-8"))
            await ctx.send(
                f"`/{path}` (as {who}) — send me this file so I can build the cap-timestamp parser.",
                file=discord.File(buf, filename=f"{path}.html"),
            )
            sent = True
        if not sent:
            await ctx.send("Couldn't fetch either page — tell me and I'll adjust.")


async def setup(bot):
    await bot.add_cog(PrimeWatcher(bot))
