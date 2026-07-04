"""
boss_raid_commands.py — Automated boss raiding.

!autoboss <group>           Start auto-raiding with a group
!autoboss <group> <boss>    Start on a specific boss
!raidboss <group>           One round of raids (no skill casting)
!boss-stop                  Stop the current boss raid session
!boss-status                Show current session status
"""

import asyncio
import re
import traceback
import discord
from discord.ext import commands
from datetime import datetime
from yarl import URL

from outwar import database as db
from outwar.constants import Skill
from outwar.scraper import parse_bosses
from cogs import embed_style as es

SIGIL_URL = URL("https://sigil.outwar.com")

# Boss priority order (highest first)
BOSS_PRIORITY = [
    "Triworld Simulation",
    "Zyrak, Vision of Madness",
    "Blackhand Reborn",
    "Maekrix, Dreaded Striker",
    "Death, Reaper of Souls",
    "Cosmos, Great All Being",
]

# Skills to cast on ALL accounts
BOSS_SKILLS_CLASS = [
    Skill.EMPOWER,
    Skill.STEALTH,
    Skill.VITAMIN_X,
    Skill.FORTIFY,
    Skill.MASTERFUL_PRESERVATION,
]

BOSS_SKILLS_PRES = [
    Skill.MARKDOWN,        # Must be active before raids start
    Skill.LAST_STAND,      # Cast + recast when recharged
    Skill.FORCEFIELD,
    Skill.BLESSING_FROM_ABOVE,
    Skill.ENCHANT_ARMOR,
    Skill.ELEMENTAL_POWER,
    Skill.EXECUTIONER,
    Skill.ELEMENTAL_BARRIER,
    Skill.LOYAL_PRESERVATION,
    Skill.STRENGTH_IN_NUMBERS,  # Handled separately (rotating)
]

BOSS_SKILLS_MISC = [
    Skill.SHIELD_WALL,
    Skill.GOD_SLAYER,
    Skill.TRIWORLD_INFLUENCE,
]

# Skills that need individual rotation (not all accounts)
ROTATING_SKILLS = {Skill.STRENGTH_IN_NUMBERS}

# Minimum cooldown times in seconds (fastest upgraded accounts)
# Cooldown counts down while skill is active — so total time from cast = duration + cooldown_remaining
SKILL_COOLDOWNS = {
    Skill.LAST_STAND: 162 * 60,   # 162 mins cooldown after expiry
    Skill.MARKDOWN:   648 * 60,   # 648 mins cooldown after expiry (NOT from cast)
}

MD_ACTIVE_SECS = 264 * 60   # 4h 24m — Level 10 MD duration
MD_TOTAL_CYCLE_SECS = SKILL_COOLDOWNS[Skill.MARKDOWN]  # 648 mins from cast (includes active + true cooldown)


def md_status_from_cast(cast_at: float, now: float = None) -> tuple[str, float]:
    """
    Pure function — given a known MD cast timestamp, return (status, ready_at).
    status: "active" | "cooldown" | "ready" | "unknown" (cast_at is None/0)
    ready_at: unix timestamp when this account became/becomes ready to recast.

    This is the single source of truth for MD timing. No sampling, no
    estimation — once we know exactly when an account's MD was cast,
    everything else is deterministic arithmetic.
    """
    if not cast_at:
        return "unknown", 0.0
    if now is None:
        now = datetime.now().timestamp()
    elapsed = now - cast_at
    ready_at = cast_at + MD_TOTAL_CYCLE_SECS
    if elapsed < MD_ACTIVE_SECS:
        return "active", ready_at
    if elapsed < MD_TOTAL_CYCLE_SECS:
        return "cooldown", ready_at
    # Full cycle (active + cooldown) has elapsed — genuinely ready to recast
    return "ready", ready_at


SKILL_NAMES = {
    Skill.EMPOWER:               "Empower",
    Skill.STEALTH:               "Stealth",
    Skill.VITAMIN_X:             "Vitamin X",
    Skill.FORTIFY:               "Fortify",
    Skill.MASTERFUL_PRESERVATION:"Masterful Preservation",
    Skill.MARKDOWN:              "Markdown",
    Skill.LAST_STAND:            "Last Stand",
    Skill.FORCEFIELD:            "Forcefield",
    Skill.BLESSING_FROM_ABOVE:   "Blessing From Above",
    Skill.ENCHANT_ARMOR:         "Enchant Armor",
    Skill.ELEMENTAL_POWER:       "Elemental Power",
    Skill.EXECUTIONER:           "Executioner",
    Skill.ELEMENTAL_BARRIER:     "Elemental Barrier",
    Skill.LOYAL_PRESERVATION:    "Loyal Preservation",
    Skill.STRENGTH_IN_NUMBERS:   "Strength in Numbers",
    Skill.SHIELD_WALL:           "Shield Wall",
    Skill.GOD_SLAYER:            "God Slayer",
    Skill.TRIWORLD_INFLUENCE:    "Triworld Influence",
}


class BossRaidCommands(commands.Cog):
    def __init__(self, bot):
        self.bot     = bot
        self._running          = False
        self._stop_flag        = False
        self._status           = {}
        self._ls_cast_times:   dict = {}
        self._ls_bg_running: bool = False
        self._sin_index = 0
        self._pending_stats: list = []   # backgrounded raid-stats tasks (main loop)

    @property
    def session(self):
        return self.bot.outwar   # Rotating Strength in Numbers index

    # ------------------------------------------------------------------
    # Helpers — skill checking
    # ------------------------------------------------------------------

    async def _cast_all_skills(self, trustees: list, ctx) -> dict:
        """
        Cast all boss skills on all trustees.
        Returns (duration_map, MD_DURATION, md_cast_time, md_already_active, still_missing_suids).
        """
        sorted_t   = sorted(trustees, key=lambda t: t.get("rage", 0), reverse=True)
        duration_map: dict = {}
        cast_start = datetime.now()
        # Timestamp marking the start of THIS cast run. Used by the MD-retry filter
        # to tell a fresh (this-cycle) cast_at apart from a stale one left over from
        # a previous cycle or inferred during cooldown polling.
        cycle_start_ts = cast_start.timestamp()

        # MD is the critical skill — cast it FIRST on every account so it always
        # goes through before any rate-limit/timeout can consume the attempt budget.
        # The rest follow in their normal order.
        all_skills = [Skill.MARKDOWN] + BOSS_SKILLS_CLASS + [
            s for s in BOSS_SKILLS_PRES
            if s != Skill.STRENGTH_IN_NUMBERS and s != Skill.MARKDOWN
        ] + BOSS_SKILLS_MISC

        # Always cast all skills on all accounts — no skipping based on MD state.
        # Skipping accounts with active MD caused LS to never be recast on restart
        # since those accounts were bypassed entirely. The game returns "already cast"
        # for active skills and moves on — recasting is safe and correct.
        md_already_active: set = set()

        await ctx.send(f"⚙️ Casting skills on **{len(sorted_t)}** accounts...")

        sem     = asyncio.Semaphore(10)
        cast_ok = set()

        # Saved MD timings (survive restarts). Used to skip re-casting MD that is
        # still genuinely active from a prior session — pure arithmetic, no game
        # request, so it never touches the ad-frame. Only the MD *cast* is skipped;
        # every account still casts LS and the rest, so this does NOT repeat the old
        # "skipped the whole account and LS never recast" regression.
        md_state_initial = db.get_md_state()

        # DEBUG: track every skill response per account to diagnose skips.
        # Set to False once the skip issue is resolved to silence the spam.
        CAST_DEBUG = True

        async def _cast_skill_post(suid: int, skill_id: int, name: str) -> str:
            """POST a single skill cast. post_as handles retries and timeouts internally."""
            resp = await self.session.post_as("cast_skills.php", {
                "castskillid": str(skill_id),
                "cast":        "Cast Skill",
            }, suid)
            if not resp:
                print(f"[CAST] {name} (suid={suid}) skill {skill_id} — EMPTY response after all retries")
            return resp

        async def _cast_account(t):
            suid = t.get("suid")
            if not suid:
                if CAST_DEBUG:
                    print(f"[CAST-DEBUG] SKIP {t.get('name','?')} — no suid!")
                return
            async with sem:
                md_recorded   = False
                skills_missed = []
                skill_results = []  # (skill_id, short_outcome) for debug summary
                for skill_id in all_skills:
                    # Mid-session restart: if MD is still active from a prior cast
                    # (saved timing), skip ONLY the MD cast — don't hit the game for
                    # a skill the account already has. LS and the rest still cast.
                    if skill_id == Skill.MARKDOWN:
                        _ca = (md_state_initial.get(str(suid)) or {}).get("cast_at")
                        if _ca and md_status_from_cast(_ca)[0] == "active":
                            md_already_active.add(suid)
                            md_recorded = True
                            skill_results.append((skill_id, "ACTIVE-SAVED"))
                            continue
                    resp = await _cast_skill_post(suid, skill_id, t["name"])
                    resp_lower = resp.lower() if resp else ""

                    # Classify the outcome for debugging
                    if not resp:
                        outcome = "EMPTY"
                    elif "you just cast" in resp_lower:
                        outcome = "CAST"
                    elif "already cast" in resp_lower:
                        outcome = "ACTIVE"
                    elif "not trained" in resp_lower or "not high enough" in resp_lower:
                        outcome = "NOTRAINED"
                    elif "too many" in resp_lower or "rate limit" in resp_lower:
                        outcome = "RATELIMIT"
                    elif "error" in resp_lower:
                        outcome = "ERROR"
                    else:
                        outcome = "OTHER"
                    skill_results.append((skill_id, outcome))

                    if skill_id == Skill.MARKDOWN:
                        if "You just cast" in resp:
                            db.set_md_cast(suid, t["name"], datetime.now().timestamp())
                            cast_ok.add(suid)
                            md_recorded = True
                        elif "already cast" in resp_lower:
                            if not md_recorded:
                                cast_ok.add(suid)
                                md_already_active.add(suid)
                                md_recorded = True
                        elif CAST_DEBUG:
                            # MD didn't cast and isn't active — log the FULL response
                            print(f"[CAST-DEBUG] {t['name']} (suid={suid}) MD did NOT cast/activate. "
                                  f"Response (first 200 chars): {resp[:200]!r}")
                    if skill_id == Skill.LAST_STAND:
                        if "You just cast" in resp or "already cast" in resp_lower:
                            self._ls_cast_times[suid] = datetime.now().timestamp()
                    if not resp or ("error" in resp_lower and "already" not in resp_lower):
                        skills_missed.append(skill_id)
                    await asyncio.sleep(0.3)

                if CAST_DEBUG:
                    # Compact per-account summary: which skills cast/active vs failed
                    failed = [f"{sid}:{out}" for sid, out in skill_results
                              if out not in ("CAST", "ACTIVE")]
                    md_outcome = next((out for sid, out in skill_results if sid == Skill.MARKDOWN), "?")
                    if failed:
                        print(f"[CAST-DEBUG] {t['name']} (suid={suid}) MD={md_outcome} "
                              f"| {len(skill_results)-len(failed)}/{len(skill_results)} ok "
                              f"| FAILED: {', '.join(failed)}")
                    else:
                        print(f"[CAST-DEBUG] {t['name']} (suid={suid}) MD={md_outcome} | ALL {len(skill_results)} skills ok")

                if skills_missed:
                    print(f"[CAST] {t['name']} missed skills: {skills_missed}")

        if CAST_DEBUG:
            print(f"[CAST-DEBUG] Starting cast on {len(sorted_t)} accounts. "
                  f"all_skills count = {len(all_skills)}: {all_skills}")

        await asyncio.gather(*[_cast_account(t) for t in sorted_t])

        if CAST_DEBUG:
            print(f"[CAST-DEBUG] Cast complete. cast_ok={len(cast_ok)}, "
                  f"md_already_active={len(md_already_active)}, "
                  f"total accounts={len(sorted_t)}")
            # List accounts NOT in cast_ok — these are the skipped/failed ones
            not_ok = [t['name'] for t in sorted_t
                      if t.get('suid') and t['suid'] not in cast_ok]
            if not_ok:
                print(f"[CAST-DEBUG] Accounts NOT in cast_ok ({len(not_ok)}): {', '.join(not_ok)}")

        # Verification — retry MD only, up to 3 rounds
        for retry_round in range(3):
            md_state_check = db.get_md_state()

            def _md_done_this_cycle(t) -> bool:
                """
                True only if this account genuinely cast/activated MD during THIS run.
                cast_ok / md_already_active are populated this cycle and are authoritative.
                A cast_at in md_state only counts if it was written during this cycle —
                a stale timestamp from a previous cycle (or one inferred while polling
                cooldowns) must NOT mask a real this-cycle failure, or the account never
                gets retried.
                """
                suid = t.get("suid")
                if suid in cast_ok or suid in md_already_active:
                    return True
                ca = (md_state_check.get(str(suid)) or {}).get("cast_at")
                if not ca:
                    return False
                # Cast happened during THIS run — definitely done.
                if ca >= cycle_start_ts:
                    return True
                # Prior-cycle cast (mid-session restart): trust the deterministic
                # timing. If MD is still genuinely ACTIVE, it's done — skip the
                # retry entirely (no game request, no ad-frame). Only "active"
                # counts; cooldown/ready still fall through to a real re-cast.
                if md_status_from_cast(ca)[0] == "active":
                    md_already_active.add(suid)
                    return True
                return False

            missing = [
                t for t in sorted_t
                if t.get("suid") and not _md_done_this_cycle(t)
            ]
            if not missing:
                break

            print(f"[CAST] Retry {retry_round+1}: {len(missing)} missing MD — "
                  f"{', '.join(t['name'] for t in missing)}")

            retry_sem = asyncio.Semaphore(5)

            async def _retry_md(t):
                suid = t.get("suid")
                async with retry_sem:
                    for attempt in range(3):
                        resp = await self.session.post_as("cast_skills.php", {
                            "castskillid": str(Skill.MARKDOWN),
                            "cast":        "Cast Skill",
                        }, suid)
                        resp_lower = resp.lower() if resp else ""
                        if "too many" in resp_lower or "rate limit" in resp_lower or "please wait" in resp_lower:
                            await asyncio.sleep(3.0 * (attempt + 1))
                            continue
                        # Ad interstitial — Outwar occasionally serves a 160x600 ad frame
                        # (#outerdiv/#inneriframe) instead of processing the cast. It's
                        # transient; wait briefly and retry rather than giving up.
                        if resp and "#outerdiv" in resp and "#inneriframe" in resp:
                            if CAST_DEBUG:
                                print(f"[CAST-DEBUG] RETRY MD hit ad-frame for {t['name']} "
                                      f"(suid={suid}) attempt {attempt+1} — retrying")
                            await asyncio.sleep(2.0 * (attempt + 1))
                            continue
                        if "You just cast" in resp or "already cast" in resp_lower:
                            db.set_md_cast(suid, t["name"], datetime.now().timestamp())
                            cast_ok.add(suid)
                            return
                        if CAST_DEBUG:
                            print(f"[CAST-DEBUG] RETRY MD failed for {t['name']} (suid={suid}) "
                                  f"attempt {attempt+1}. Response: {resp[:200]!r}")
                        break

            await asyncio.gather(*[_retry_md(t) for t in missing])

        # Hard failure report
        md_state_final = db.get_md_state()
        still_missing_names = [
            t["name"] for t in sorted_t
            if t.get("suid")
            and t["suid"] not in cast_ok
            and t["suid"] not in md_already_active
            and not (
                ((md_state_final.get(str(t["suid"])) or {}).get("cast_at") or 0) >= cycle_start_ts
            )
        ]
        if still_missing_names:
            await ctx.send(
                f"🚨 **{len(still_missing_names)} account(s) failed MD after 3 retries** — "
                f"excluded: {', '.join(still_missing_names)}"
            )
            print(f"[CAST] HARD FAILURE: {', '.join(still_missing_names)}")

        # SiN — single account, rotating, using post_as
        sin_caster = sorted_t[self._sin_index % len(sorted_t)]
        sin_suid   = sin_caster.get("suid")
        for attempt in range(3):
            resp = await self.session.post_as("cast_skills.php", {
                "castskillid": str(Skill.STRENGTH_IN_NUMBERS),
                "cast":        "Cast Skill",
            }, sin_suid)
            if resp and ("You just cast" in resp or "already cast" in resp.lower()):
                break
            self._sin_index += 1
            sin_caster = sorted_t[self._sin_index % len(sorted_t)]
            sin_suid   = sin_caster.get("suid")

        self._sin_index += 1
        sin_name = sin_caster.get("name", "Unknown")
        print(f"[SiN] Cast on {sin_name}")
        self._status.update({
            "sin_name": sin_name,
            "sin_url":  f"https://sigil.outwar.com/profile?transnick={sin_name}&serverid=1",
        })

        # Duration — use persistent cast_at values to calculate real remaining time.
        # For freshly cast accounts: cast_at = now, so remaining = MD_ACTIVE_SECS.
        # For already-active accounts: cast_at = original cast time (NOT overwritten),
        # so remaining = cast_at + MD_ACTIVE_SECS - now = real time left.
        # The minimum across all accounts is the true raid window.
        now_ts2      = datetime.now().timestamp()
        MD_DURATION  = MD_ACTIVE_SECS
        md_state_dur = db.get_md_state()
        remaining_list = []
        for t in sorted_t:
            suid = t.get("suid")
            if not suid:
                continue
            record = md_state_dur.get(str(suid))
            if record and record.get("cast_at"):
                remaining = record["cast_at"] + MD_ACTIVE_SECS - now_ts2
                if remaining > 0:
                    remaining_list.append(remaining)
        min_remaining = int(min(remaining_list)) if remaining_list else MD_ACTIVE_SECS
        md_cast_time  = now_ts2 + min_remaining - MD_ACTIVE_SECS
        print(f"[CAST] MD duration: {min_remaining//60:.0f}m remaining (min across {len(remaining_list)} accounts)")

        for t in sorted_t:
            suid = t.get("suid")
            if suid:
                duration_map[suid] = {Skill.MARKDOWN: MD_DURATION, Skill.LAST_STAND: 3600}

        elapsed_s     = int((datetime.now() - cast_start).total_seconds())
        md_expires_ts = int(now_ts2) + min_remaining
        hrs, mins_rem = divmod(min_remaining // 60, 60)
        duration_str  = f"{hrs}h {mins_rem}m" if hrs else f"{mins_rem}m"
        await ctx.send(
            f"✅ Skills cast in **{elapsed_s}s** · SiN: **{sin_name}** · MD active · "
            f"Raids continue for **{duration_str}** (until <t:{md_expires_ts}:t>)"
        )

        still_missing_suids = {t.get("suid") for t in sorted_t if t["name"] in still_missing_names}
        return duration_map, MD_DURATION, md_cast_time, md_already_active, still_missing_suids

    # ------------------------------------------------------------------
    # Boss detection
    # ------------------------------------------------------------------

    async def _get_spawned_bosses(self) -> list:
        """Fetch crew_bossspawns and return list of spawned boss names in priority order."""
        html = await self.session.get("crew_bossspawns")
        bosses = parse_bosses(html)
        spawned_names = [b.full_name for b in bosses if b.spawned]
        # Return in priority order
        return [b for b in BOSS_PRIORITY if b in spawned_names]

    # ------------------------------------------------------------------
    # Boss raiding
    # ------------------------------------------------------------------

    async def _get_live_rage(self, session, suid: int) -> int:
        """Fetch live rage for a single account."""
        try:
            html = await session.get_as("home", suid)
            m = re.search(r'class="toolbar_rage"[^>]*>\s*([\d,]+)', html)
            if not m:
                m = re.search(r'RAGE:\s*([\d,]+)', html)
            return int(m.group(1).replace(",", "")) if m else 0
        except Exception:
            return 0

    async def _recast_ls_bg(self, trustees: list, notify):
        """
        Background task — checks every 5 minutes and recasts Last Stand
        on any account whose LS has expired. Runs concurrently with raids.
        """
        ls_sem = asyncio.Semaphore(10)

        async def _cast_one(t):
            suid = t.get("suid")
            if not suid:
                return None
            async with ls_sem:
                for attempt in range(3):
                    resp = await self.session.post_as("cast_skills.php", {
                        "castskillid": str(Skill.LAST_STAND),
                        "cast":        "Cast Skill",
                    }, suid)
                    resp_lower = resp.lower() if resp else ""
                    if "too many" in resp_lower or "rate limit" in resp_lower or "please wait" in resp_lower:
                        await asyncio.sleep(3.0 * (attempt + 1))
                        continue
                    if "You just cast" in resp or "already cast" in resp_lower:
                        self._ls_cast_times[suid] = datetime.now().timestamp()
                        return t["name"]
                    break
            return None

        # Seed _ls_cast_times for any account not already recorded.
        # On restart _ls_cast_times is empty — poll the game to find actual
        # LS remaining time for a sample account, then seed all accounts.
        LS_RECAST_SECS  = 163 * 60   # Recast at 163 minutes after cast (162 min cooldown + 1 min buffer)

        # Only seed if _ls_cast_times is empty (restart with no prior cast data)
        if not self._ls_cast_times:
            try:
                sample = next((t for t in trustees if t.get("suid")), None)
                if sample:
                    import re as _re_ls
                    ls_html = await self.session.get_as(
                        f"skills_info.php?id={Skill.LAST_STAND}", sample["suid"]
                    )
                    m = _re_ls.search(r"(\d+)\s*minutes?\s*remaining", ls_html, _re_ls.I)
                    if m:
                        # skills_info shows cooldown remaining (counts from cast, regardless of active/expired)
                        ls_remaining_mins = int(m.group(1))
                        ls_cooldown_mins  = 162
                        elapsed_secs      = (ls_cooldown_mins - ls_remaining_mins) * 60
                        ls_cast_at        = datetime.now().timestamp() - elapsed_secs
                        print(f"[LS] Restart seed — {ls_remaining_mins}m remaining, recast in {ls_remaining_mins + 1}m")
                    elif "active" in ls_html.lower() or "cast" in ls_html.lower():
                        # LS is currently active but skills_info shows no remaining text
                        # Treat as just cast — will recast in 163 minutes
                        ls_cast_at = datetime.now().timestamp()
                        print(f"[LS] Restart seed — LS active, treating as just cast")
                    else:
                        # LS ready to cast immediately
                        ls_cast_at = datetime.now().timestamp() - LS_RECAST_SECS
                        print(f"[LS] Restart seed — LS ready to cast")
                    for t in trustees:
                        suid = t.get("suid")
                        if suid:
                            self._ls_cast_times[suid] = ls_cast_at
            except Exception as e:
                print(f"[LS] Could not seed cast times: {e}")

        while self._running and not self._stop_flag:
            await asyncio.sleep(300)  # check every 5 minutes

            if not self._running or self._stop_flag:
                break

            now_ts = datetime.now().timestamp()

            # Check MD still active before recasting LS
            md_state = db.get_md_state()
            active_count = 0
            total = 0
            for t in trustees:
                suid = t.get("suid")
                if not suid:
                    continue
                total += 1
                record = md_state.get(str(suid))
                if record and record.get("cast_at"):
                    status, _ = md_status_from_cast(record["cast_at"], now_ts)
                    if status == "active":
                        active_count += 1
            md_currently_active = total > 0 and active_count >= max(5, total * 0.5)
            if not md_currently_active:
                continue

            # Recast LS at 163 minutes after cast (162 min cooldown + 1 min buffer)
            due = [
                t for t in trustees
                if t.get("suid") and
                now_ts - self._ls_cast_times.get(t["suid"], 0) >= LS_RECAST_SECS
            ]

            if due:
                names = await asyncio.gather(*[_cast_one(t) for t in due])
                recast = [n for n in names if n]
                if recast:
                    await notify.send(f"⚡ Last Stand recast on **{len(recast)}** accounts")

    async def _cast_boss_pots_bg(self, trustees, boss_name, notify, pot_expiry_ref: dict):
        """
        Background task — checks every 5 minutes and recasts any expired potions.
        Runs concurrently with raids. Stops casting entirely once MD is no longer
        active for the group (checked against live persistent MD state, not a
        stale snapshot) — no point casting boss pots if nobody's raiding.
        """
        def _md_has_30min_left() -> bool:
            """
            True only if the majority of the group still has at least 30 minutes of
            MD active time left. Pots are a fixed-duration consumable — casting them
            with under half an hour of raiding left just wastes them, so we gate pot
            casting on this rather than on bare 'MD active'.
            """
            now_ts   = datetime.now().timestamp()
            md_state = db.get_md_state()
            enough   = 0
            total    = 0
            for t in trustees:
                suid = t.get("suid")
                if not suid:
                    continue
                total += 1
                record = md_state.get(str(suid))
                if record and record.get("cast_at"):
                    remaining = record["cast_at"] + MD_ACTIVE_SECS - now_ts
                    if remaining >= 30 * 60:
                        enough += 1
            return total > 0 and enough >= max(5, total * 0.5)

        if not _md_has_30min_left():
            print("[POTS] Under 30 min of MD left — skipping pot cast")
            return  # not enough raiding time left to justify casting pots

        # Initial cast — raids have already been announced and started; apply pots
        # now in the background rather than blocking the first raid on them. Then
        # recast on the 5-min timer as they expire.
        result = await self._cast_boss_pots(trustees, boss_name, notify, pot_expiry_ref)
        pot_expiry_ref.update(result)

        while self._running and not self._stop_flag:
            await asyncio.sleep(300)
            if not self._running or self._stop_flag:
                break
            # Stop recasting once under 30 min of MD remains — don't waste pots on
            # the tail end of a cycle that's about to stop raiding.
            if not _md_has_30min_left():
                print("[POTS] Under 30 min of MD left — stopping pot recasts")
                break
            result = await self._cast_boss_pots(trustees, boss_name, notify, pot_expiry_ref)
            pot_expiry_ref.update(result)

    async def _cast_boss_pots(self, trustees: list, boss_name: str, notify,
                               pot_expiry: dict = None) -> dict:
        """
        Cast boss-specific potions on all trustees.
        pot_expiry: {pot_key: timestamp} — only recasts pots that have expired.
        Returns updated pot_expiry dict.
        """
        from outwar.constants import BOSS_POTS, POTIONS, POT_DURATIONS
        from outwar.scraper import parse_backpack_for_item

        boss_key = boss_name.split(",")[0].strip().lower()
        pot_keys = None
        for key in BOSS_POTS:
            if key in boss_key or boss_key in key:
                pot_keys = BOSS_POTS[key]
                break
        if not pot_keys:
            print(f"[POTS] No pot config found for boss_key='{boss_key}'")
            return pot_expiry or {}

        now = datetime.now().timestamp()
        if pot_expiry is None:
            pot_expiry = {}

        # Only cast pots that have expired or haven't been cast yet
        pots_to_cast = [k for k in pot_keys if now >= pot_expiry.get(k, 0)]
        if not pots_to_cast:
            return pot_expiry  # Nothing to recast yet

        pot_names  = {k: POTIONS[k] for k in pots_to_cast if k in POTIONS}
        pot_counts    = {k: 0 for k in pots_to_cast}
        pot_active    = {k: 0 for k in pots_to_cast}
        # get_as/post_as pass suid per-request without touching the shared cookie jar
        # — safe for full concurrency at sem=10
        sem = asyncio.Semaphore(10)

        async def _cast_pots_for(t):
            suid = t.get("suid")
            if not suid:
                return
            async with sem:
                try:
                    html = await self.session.get_as("ajax/backpackcontents.php?tab=potion", suid)
                    for pot_key, pot_name in pot_names.items():
                        if pot_key == "rems":
                            tried_levels = (11, 10, 9, 8, "")
                            item = None
                            for lvl in tried_levels:
                                search  = f"Remnant Solice Lev {lvl}" if lvl else "Remnant Solice"
                                matches = parse_backpack_for_item(html, search)
                                if lvl == "":
                                    matches = [m for m in matches if "Lev" not in m["item_name"]]
                                if matches:
                                    item = matches[0]
                                    break
                        else:
                            matches = parse_backpack_for_item(html, pot_name)
                            item    = matches[0] if matches else None
                        if item:
                            resp = await self.session.post_as("ajax/backpack_action.php", {
                                "action":    "activate",
                                "itemids[]": item["item_id"],
                            }, suid)
                            resp_str = str(resp).lower()
                            if "already drank" in resp_str or "already consumed" in resp_str or "invalid item" in resp_str:
                                pot_active[pot_key] += 1
                            elif "not a high enough level" in resp_str and pot_key == "rems":
                                # Try progressively lower levels until one works
                                item_name = item["item_name"]
                                import re as _re3
                                lvl_m = _re3.search(r"Lev (\d+)", item_name)
                                current_lvl = int(lvl_m.group(1)) if lvl_m else 0
                                activated = False
                                for fallback_lvl in range(current_lvl - 1, 0, -1):
                                    fb_search = f"Remnant Solice Lev {fallback_lvl}"
                                    fb_matches = parse_backpack_for_item(html, fb_search)
                                    if fb_matches:
                                        fb_resp = await self.session.post_as("ajax/backpack_action.php", {
                                            "action":    "activate",
                                            "itemids[]": fb_matches[0]["item_id"],
                                        }, suid)
                                        fb_str = str(fb_resp).lower()
                                        if "error" not in fb_str:
                                            pot_counts[pot_key] += 1
                                            activated = True
                                            break
                                        elif "already" in fb_str:
                                            pot_active[pot_key] += 1
                                            activated = True
                                            break
                                if not activated:
                                    pass  # no suitable level found — skip silently
                            elif "error" in resp_str:
                                if "do not have permission" not in resp_str:
                                    print(f"[POTS] {t.get('name','?')}: {pot_key} error: {resp_str[:80]}")
                            else:
                                pot_counts[pot_key] += 1
                            await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"[POTS] error {t.get('name','?')}: {e}")

        await asyncio.gather(*[_cast_pots_for(t) for t in trustees])

        # Update expiry for any pot that is active (freshly cast OR already active)
        for pot_key in pots_to_cast:
            total_covered = pot_counts.get(pot_key, 0) + pot_active.get(pot_key, 0)
            if total_covered > 0:
                duration = POT_DURATIONS.get(pot_key, 3960)
                pot_expiry[pot_key] = now + duration - 60

        if any(pot_counts.values()) or any(pot_active.values()):
            pot_lines = []
            for k in pots_to_cast:
                fresh  = pot_counts.get(k, 0)
                active = pot_active.get(k, 0)
                if fresh + active > 0:
                    name = POTIONS.get(k, k)
                    if fresh > 0 and active > 0:
                        pot_lines.append(f"**{name}**: {fresh} cast, {active} already active")
                    elif fresh > 0:
                        pot_lines.append(f"**{name}**: {fresh} accounts")
                    else:
                        pot_lines.append(f"**{name}**: {active} already active")
            if pot_lines:
                await notify.send("🧪 Potions:\n" + "\n".join(pot_lines))
        else:
            print(f"[POTS] No potions cast for {boss_name} — accounts may not have pots in backpack")

        return pot_expiry

    async def _do_boss_raid(self, trustees: list, boss_name: str,
                            last_launch_ts: float = 0.0,
                            notify=None, background_stats: bool = False) -> tuple[int, bool, int, bool, float]:
        """
        Form, join and launch one boss raid.
        Returns (damage, drops).
        """
        import re as _re
        session  = self.session
        sorted_t = sorted(trustees, key=lambda t: t.get("rage", 0), reverse=True)

        try:
            # Find the boss's rage_to_form requirement
            from outwar.scraper import parse_bosses as _parse_bosses
            spawns_html = await session.get("crew_bossspawns")
            boss_obj    = next(
                (b for b in _parse_bosses(spawns_html)
                 if b.spawned and boss_name.split(",")[0].lower() in b.full_name.lower()),
                None
            )

            # Use discovered costs if available
            settings    = db.get_settings()
            boss_key    = boss_name.split(",")[0].strip().lower()
            boss_costs  = settings.get("boss_costs", {})
            known_costs = boss_costs.get(boss_key, {})
            rage_to_form = known_costs.get("md_form") or (boss_obj.md_form if boss_obj else 938)

            # Live rage check on top 10 only — find a valid former quickly
            rage_sem = asyncio.Semaphore(10)
            top_candidates = sorted_t[:10]

            async def _get_rage(t):
                suid = t.get("suid")
                if not suid:
                    return t, 0
                async with rage_sem:
                    try:
                        html = await session.get_as("home", suid)
                        import re as _re2
                        m = _re2.search(r'class="toolbar_rage"[^>]*>\s*([\d,]+)', html)
                        if not m:
                            m = _re2.search(r'RAGE:\s*([\d,]+)', html)
                        rage = int(m.group(1).replace(",", "")) if m else 0
                        return t, rage
                    except Exception:
                        return t, 0

            rage_results = await asyncio.gather(*[_get_rage(t) for t in top_candidates])

            formers = [(t, rage) for t, rage in rage_results if rage >= rage_to_form]

            if not formers:
                # No former found in top 10 — wait for rage reset
                now = datetime.now()
                secs_to_hour = (60 - now.minute) * 60 - now.second + 30
                return 0, True, secs_to_hour, False, 0.0

            # Former = highest rage from top candidates
            former      = max(formers, key=lambda x: x[1])[0]
            former_suid = former.get("suid")
            # Update status with former info
            self._status.update({
                "former":     former.get("name", "—"),
                "former_url": f"https://sigil.outwar.com/profile?transnick={former.get('name', '')}&serverid=1",
                "phase":      "forming",
            })
            bosses = _parse_bosses(spawns_html)

            target_boss = next(
                (b for b in bosses if b.spawned and
                 boss_name.split(",")[0].lower() in b.full_name.lower()),
                None
            )

            if not target_boss:
                return 0, False, 0, False, 0.0

            if target_boss.boss_id == -1:
                return 0, False, 0, False, 0.0

            # Store boss URL in status
            self._status.update({
                "boss_url": target_boss.stats_url or f"https://sigil.outwar.com/crew_bossspawns",
                "phase":    "forming",
            })

            # Get former's rage before forming to measure cost
            former_rage_before = await self._get_live_rage(session, former_suid)

            # Form raid using formraid.php with the boss target ID
            await session.post_as(f"formraid.php?target={target_boss.boss_id}", {
                "formtime": "2",
                "submit":   "Join this Raid!",
                "bomb":     "none",
            }, former_suid)

            # Measure form cost if not known
            if "md_form" not in known_costs and former_rage_before > 0:
                former_rage_after = await self._get_live_rage(session, former_suid)
                if former_rage_after < former_rage_before:
                    discovered_form = former_rage_before - former_rage_after
                    known_costs["md_form"] = discovered_form
                    boss_costs[boss_key]   = known_costs
                    settings["boss_costs"] = boss_costs
                    db.save_settings(settings)
                    print(f"Discovered md_form for {boss_key}: {discovered_form}")

            # Get raid URL from forming raids page
            from outwar.scraper import parse_raid_link
            forming_html = await session.get_as(
                f"crew_raidsforming.php?uid={former_suid}&id={former_suid}&server=1",
                former_suid
            )
            raid_url = parse_raid_link(forming_html, boss_name)
            if not raid_url:
                return 0, False, 0, False, 0.0

            raidid_m = _re.search(r"raidid=(\d+)", raid_url)
            if not raidid_m:
                print(f"[RAID-DEBUG] No raidid in raid_url: {raid_url!r}")
                return 0, False, 0, False, 0.0
            raidid = raidid_m.group(1)
            print(f"[RAID-DEBUG] raid_url={raid_url!r} raidid={raidid} former={former.get('name')} (suid={former_suid})")

            # Join all accounts concurrently using post_as — no cookie mutation,
            # no sequential blocking. This is critical: the sequential loop was
            # blocking the event loop for minutes, starving background tasks
            # (pot recasts, LS recasts, god poll) and causing missed join attempts.
            joiners     = [t for t in sorted_t if t.get("suid") != former_suid]
            measure_join_suid = joiners[0].get("suid") if joiners and "md_join" not in known_costs else None
            join_rage_before  = 0

            if measure_join_suid:
                join_rage_before = await self._get_live_rage(session, measure_join_suid)

            # Join concurrency is the dominant cost for large crews (190+ accounts).
            # Tunable via settings 'boss_join_concurrency' (default 10, was 3); the
            # session's per-request retry absorbs the occasional rate-limit.
            try:
                _join_conc = int(db.get_settings().get("boss_join_concurrency", 10))
            except Exception:
                _join_conc = 10
            join_sem = asyncio.Semaphore(max(1, _join_conc))

            async def _join_one(t):
                suid = t.get("suid")
                if not suid:
                    return
                async with join_sem:
                    try:
                        await session.post_as(raid_url, {
                            "submit":   "Join this Raid!",
                            "raidjoin": "1",
                        }, suid)
                    except Exception:
                        pass

            await asyncio.gather(*[_join_one(t) for t in joiners])

            # Measure join cost (only if not already known)
            if measure_join_suid and join_rage_before > 0 and not known_costs.get("md_join"):
                join_rage_after = await self._get_live_rage(session, measure_join_suid)
                if join_rage_after < join_rage_before:
                    discovered_join = join_rage_before - join_rage_after
                    known_costs["md_join"] = discovered_join
                    boss_costs[boss_key]   = known_costs
                    settings["boss_costs"] = boss_costs
                    db.save_settings(settings)
                    print(f"Discovered md_join for {boss_key}: {discovered_join}")

            await asyncio.sleep(3)  # Allow all join requests to settle

            # Enforce 60s game limit — wait only the remainder since last launch
            if last_launch_ts > 0:
                elapsed = datetime.now().timestamp() - last_launch_ts
                remainder = max(0, 60 - elapsed)
                if remainder > 0:
                    print(f"[RAID] Waiting {remainder:.1f}s before launch (60s game limit)")
                    await asyncio.sleep(remainder)

            # Launch — get_as has its own internal retry/timeout (25 attempts, 60s each).
            # Do NOT wrap in asyncio.wait_for — the outer cancel discards a successful
            # launch mid-flight and makes the bot think the raid failed, causing an
            # endless re-form/re-join/wait loop.
            launch_time = 0.0
            launch_path = f"joinraid.php?raidid={raidid}&launchraid=yes"
            print(f"[RAID-DEBUG] Launching: {launch_path} as suid={former_suid}")
            try:
                launch_html = await session.get_as(launch_path, former_suid)
                if launch_html:
                    launch_time = datetime.now().timestamp()
                    _ll = launch_html.lower()
                    if "launch" in _ll or "attack" in _ll or "raid has begun" in _ll or "damage" in _ll:
                        print(f"[RAID] Launched raid {raidid} OK")
                    else:
                        print(f"[RAID-DEBUG] Launch raid {raidid} unexpected response: {launch_html[:400]!r}")
                else:
                    print(f"[RAID] Launch returned empty for raid {raidid}")
                    return 0, False, 0, False, 0.0
            except Exception as e:
                print(f"[RAID] Launch error: {e}")
                return 0, False, 0, False, 0.0

            # Stats collection — either inline (legacy callers) or backgrounded so it
            # never blocks the next raid from forming. The 5s settle + attack-page
            # fetch + parse (~8s) was sitting on the critical path between launches.
            if background_stats:
                self._pending_stats.append(
                    asyncio.create_task(
                        self._collect_raid_stats(raidid, former_suid, boss_name,
                                                 boss_key, len(sorted_t))))
                return 0, False, 0, False, launch_time

            r = await self._collect_raid_stats(raidid, former_suid, boss_name,
                                               boss_key, len(sorted_t))
            return r["damage"], False, 0, r["new_record"], launch_time

        except asyncio.TimeoutError:
            print(f"Boss raid timed out for {boss_name}")
            return 0, False, 0, False, 0.0
        except Exception as e:
            print(f"Boss raid error: {e}")
            return 0, False, 0, False, 0.0

    async def _collect_raid_stats(self, raidid, former_suid, boss_name, boss_key, pool_count):
        """Fetch + parse the attack page, update live status and the all-time record.
        Returns {damage, chars, new_record, boss}. Designed to run as a background
        task so it never blocks the next raid from forming. Never sends alerts itself —
        the consumer of the returned dict does (keeps alerts in caller scope)."""
        import re as _re
        result = {"damage": 0, "chars": pool_count, "new_record": False, "boss": boss_name}
        _t_stats = datetime.now().timestamp()
        try:
            await asyncio.sleep(5)  # let the raid resolve before reading damage
            attack_html = await self.session.get_as(
                f"raidattack.php?raidid={raidid}", former_suid)

            damage = 0
            dmg_m = _re.search(r"Total Attacker Damage[:\s]+([\d,]+)", attack_html)
            if dmg_m:
                try:
                    damage = int(dmg_m.group(1).replace(",", ""))
                except ValueError:
                    pass
            name_matches = _re.findall(r'<b>([^<]+?)\s*<font\s', attack_html)
            actual_chars = len(set(name_matches)) if name_matches else pool_count

            result["damage"] = damage
            result["chars"]  = actual_chars
            self._status["last_raid_damage"] = damage
            self._status["last_raid_chars"]  = actual_chars
            if damage > self._status.get("best_raid", 0):
                self._status["best_raid"] = damage
            self._status["phase"] = "raiding"

            if damage > 0:
                settings     = db.get_settings()
                boss_records = settings.get("boss_records", {})
                prev_best    = boss_records.get(boss_key, {}).get("best", 0)
                if damage > prev_best:
                    boss_records[boss_key] = {"best": damage, "boss_full": boss_name}
                    settings["boss_records"] = boss_records
                    db.save_settings(settings)
                    result["new_record"] = True
        except Exception as e:
            print(f"[RAID] stats collection error: {e}")
        print(f"[TIMING] stats collection took {datetime.now().timestamp() - _t_stats:.1f}s "
              f"(raid {raidid})")
        return result

    # ------------------------------------------------------------------
    # Main autoboss loop
    # ------------------------------------------------------------------

    async def _run_autoboss(self, ctx, trustees: list, start_boss: str = None, group_name: str = None):
        try:
            alert_channel = None
            try:
                settings = db.get_settings()
                boss_ch_id = settings.get("alert_channels", {}).get("boss")
                if boss_ch_id:
                    alert_channel = self.bot.get_channel(int(boss_ch_id))
            except Exception:
                pass

            notify       = alert_channel or ctx
            session_start = datetime.now()
            self._status.update({"session_start": session_start, "phase": "waiting_boss"})

            # True session-wide totals — incremented on every single raid regardless
            # of how many boss cycles occur. session_raids/session_damage below are
            # PER-BOSS-CYCLE counts used for individual boss summaries; these track
            # the whole !autoboss run for the final stop summary.
            total_session_raids  = 0
            total_session_damage = 0
            first_raid_done      = False   # green-flag latch for this whole run

            await notify.send("🔁 AutoBoss is running — use `!boss-stop` to stop at any time.")

            # Persisted across outer loop iterations — only recheck/recast MD when it's
            # actually expired, not every time a boss dies and a new one is targeted
            md_end_times_persist: dict = {}

            # ── Outer loop: runs indefinitely until !boss-stop ──────────
            while not self._stop_flag:

                # 1. Wait for a boss to spawn
                spawned = await self._get_spawned_bosses()
                if not spawned:
                    self._status["phase"] = "waiting_boss"
                    await notify.send("⏳ No bosses spawned — checking every minute...")
                    while not self._stop_flag:
                        await asyncio.sleep(60)
                        spawned = await self._get_spawned_bosses()
                        if spawned:
                            await notify.send(f"🏴 **{spawned[0]}** has spawned!")
                            break
                    if self._stop_flag:
                        break

                manual_lock = False
                if start_boss:
                    matched = next((b for b in spawned if start_boss.lower() in b.lower()), None)
                    if matched:
                        current_boss = matched
                        manual_lock  = True  # ignore priority switching until this boss dies
                        # Do NOT clear start_boss here — it must persist across MD
                        # recharge cycles. Only cleared when the target boss actually
                        # dies (see death-detection blocks below).
                    else:
                        # Target currently not spawned — raid priority order for now,
                        # but keep trying to re-target it once it spawns again
                        current_boss = spawned[0]
                        await notify.send(
                            f"ℹ️ `{start_boss}` not currently spawned — "
                            f"raiding **{current_boss}** instead (priority order)"
                        )
                else:
                    current_boss = spawned[0]
                self._status.update({"boss": current_boss})

                # Skip the full MD recheck + skill recast if MD is still active for
                # the majority of accounts — only a boss died, not MD expiring
                now_check = datetime.now().timestamp()
                still_valid = [s for s, end in md_end_times_persist.items() if end > now_check]
                if md_end_times_persist and len(still_valid) >= max(5, len(md_end_times_persist) * 0.5):
                    # Reuse existing MD state — just restart the inner raid loop on the new boss
                    self._status["phase"] = "raiding"
                    await notify.send(f"🏴 Raiding **{current_boss}** with **{len(still_valid)}** accounts (MD still active)")

                    md_end_times = {s: e for s, e in md_end_times_persist.items() if e > now_check}
                    sorted_t = [t for t in trustees if t.get("suid") in md_end_times]

                    session_raids  = 0
                    session_damage = 0
                    session_best   = 0  # biggest single raid THIS MD cycle (resets per cycle)
                    launch_ts      = 0.0

                    while not self._stop_flag:
                        now_ts = datetime.now().timestamp()
                        still_active_now = [s for s, end in md_end_times.items() if end > now_ts]
                        # Break when majority have expired, not literally everyone —
                        # one drifted/straggler account shouldn't hold the loop hostage
                        majority_expired = len(still_active_now) < max(5, len(md_end_times) * 0.5)
                        if majority_expired:
                            break  # fall through to full MD recheck below

                        try:
                            _t_spawn = datetime.now().timestamp()
                            spawned = await self._get_spawned_bosses()
                            print(f"[TIMING] spawned-check took "
                                  f"{datetime.now().timestamp() - _t_spawn:.1f}s")
                        except Exception as e:
                            print(f"[RAID] Error checking spawned bosses: {e} — retrying in 5s")
                            await asyncio.sleep(5)
                            continue

                        if current_boss not in (spawned or []):
                            await notify.send(f"💀 **{current_boss}** has been defeated!")
                            if manual_lock and start_boss and start_boss.lower() in current_boss.lower():
                                start_boss = None  # target boss is dead — revert to priority order
                            elapsed = int((datetime.now() - self._status.get("started", datetime.now())).total_seconds())
                            mins, secs = divmod(elapsed, 60)
                            hrs, mins  = divmod(mins, 60)
                            elapsed_str = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
                            crew_name = self._status.get("source", group_name or "Unknown")
                            from outwar.table_image import render_boss_raid_summary
                            buf = render_boss_raid_summary(crew_name, current_boss, {
                                "raids":        session_raids,
                                "total_damage": session_damage,
                                "best_raid":    session_best,
                                "elapsed":      elapsed_str,
                                "resume_mins":  0,
                            })
                            await notify.send(file=discord.File(buf, filename="boss_summary.png"))
                            break

                        consecutive_timeouts = 0
                        MAX_TIMEOUTS         = 3
                        damage = under_minimum = secs_to_hour = new_record = None

                        while consecutive_timeouts < MAX_TIMEOUTS:
                            try:
                                _t_raid = datetime.now().timestamp()
                                print(f"[TIMING] raid start — background_stats(latch)={first_raid_done}")
                                damage, under_minimum, secs_to_hour, new_record, launch_ts = await asyncio.wait_for(
                                    self._do_boss_raid(sorted_t, current_boss, launch_ts,
                                                       notify=notify,
                                                       background_stats=first_raid_done),
                                    timeout=180
                                )
                                print(f"[TIMING] _do_boss_raid returned in "
                                      f"{datetime.now().timestamp() - _t_raid:.1f}s")
                                consecutive_timeouts = 0
                                break
                            except asyncio.TimeoutError:
                                consecutive_timeouts += 1
                                if hasattr(self.bot, 'health'):
                                    self.bot.health.log_raid_failure()
                                if consecutive_timeouts >= MAX_TIMEOUTS:
                                    await notify.send(
                                        f"🚨 Raid timed out **{MAX_TIMEOUTS}** times in a row on "
                                        f"**{current_boss}** — pausing for 5 minutes before trying again."
                                    )
                                    for _ in range(60):
                                        if self._stop_flag:
                                            break
                                        await asyncio.sleep(5)
                                else:
                                    await notify.send(
                                        f"⚠️ Raid timed out ({consecutive_timeouts}/{MAX_TIMEOUTS}) — "
                                        f"waiting 30s then retrying..."
                                    )
                                    await asyncio.sleep(30)
                            except Exception as e:
                                print(f"[RAID] Unexpected error in _do_boss_raid: {e}")
                                print(traceback.format_exc())
                                await notify.send(f"🚨 Raid error: `{e}` — retrying in 30s...")
                                await asyncio.sleep(30)
                                consecutive_timeouts += 1

                        if self._stop_flag:
                            break
                        if damage is None or launch_ts == 0:
                            # Raid didn't launch — wait 30s before retrying
                            await asyncio.sleep(30)
                            continue

                        if not first_raid_done:
                            # First raid of the run ran inline — damage is returned
                            # directly. Count it, then raise the green flag.
                            session_raids  += 1
                            total_session_raids  += 1
                            session_damage       += damage
                            total_session_damage += damage
                            session_best          = max(session_best, damage or 0)
                            if hasattr(self.bot, 'health'):
                                self.bot.health.log_raid_success()
                            if new_record:
                                await notify.send(
                                    f"🏆 **New record on {current_boss}!** {damage:,} damage")
                            _crew = self._status.get("source", group_name or "Crew")
                            _chars = self._status.get("last_raid_chars", len(sorted_t))
                            await notify.send(
                                f"🚩 **{_crew}** is up and raiding — first raid complete: "
                                f"**{damage:,}** damage · {_chars}/{len(sorted_t)} accounts.")
                            first_raid_done = True
                            self._status.update({"raids": session_raids, "damage": session_damage})
                        else:
                            # Steady state — raid launched; count it now and drain any
                            # completed background stats (this or prior raids).
                            session_raids  += 1
                            total_session_raids  += 1
                            if hasattr(self.bot, 'health'):
                                self.bot.health.log_raid_success()

                            for _st in [x for x in self._pending_stats if x.done()]:
                                self._pending_stats.remove(_st)
                                try:
                                    _r = _st.result()
                                except Exception:
                                    continue
                                session_damage       += _r["damage"]
                                total_session_damage += _r["damage"]
                                session_best          = max(session_best, _r["damage"])
                                if _r["new_record"]:
                                    await notify.send(
                                        f"🏆 **New record on {_r['boss']}!** {_r['damage']:,} damage")
                            self._status.update({"raids": session_raids, "damage": session_damage})

                    if self._stop_flag:
                        break
                    # Restore bot's own cookie before returning to outer loop —
                    # _do_boss_raid may have left it switched to a trustee account
                    continue  # back to top of outer loop to pick next boss

                # Full MD recheck + skill recast path (MD genuinely expired or first run)
                import re as _re2
                sem = asyncio.Semaphore(10)
                now_for_check = datetime.now().timestamp()

                # ── Step 1: consult persistent MD state first ──────────────
                # If we already know an account's cast_at and it's recent
                # enough to be trustworthy (within one full MD cycle), we
                # don't need to poll the network for it at all.
                md_state = db.get_md_state()

                async def _check_md_now(t):
                    """
                    Returns (trustee, status, remaining_mins).
                    status: "ready" (cast & still active, OR genuinely ready
                            to cast for the first time), "cooldown", "not_trained"
                    Uses persistent state when trustworthy; only polls the
                    network when we have no reliable record for this account.
                    """
                    suid = t.get("suid")
                    if not suid:
                        return t, "not_trained", 0

                    record = md_state.get(str(suid))
                    if record and record.get("cast_at"):
                        status, ready_at = md_status_from_cast(record["cast_at"], now_for_check)
                        if status == "active":
                            return t, "ready", 0  # still actively casting, include in raid group
                        if status == "ready":
                            return t, "ready", 0  # full cycle elapsed, genuinely ready to recast
                        if status == "cooldown":
                            remaining_mins = max(0, int((ready_at - now_for_check) / 60))
                            return t, "cooldown", remaining_mins

                    # No trustworthy record — poll the network once to establish ground truth
                    async with sem:
                        try:
                            html = await self.session.get_as(f"skills_info.php?id={Skill.MARKDOWN}", suid)
                            lvl_m = _re2.search(r"Markdown Level (\d+)", html)
                            level = int(lvl_m.group(1)) if lvl_m else 0
                            if level <= 1:
                                return t, "not_trained", 0
                            if "recharging" in html.lower():
                                cd_m2 = _re2.search(r"(\d+)\s*minutes?\s*remaining", html, _re2.I)
                                remaining = int(cd_m2.group(1)) if cd_m2 else 0
                                if remaining > 384:
                                    # Still active — back-calculate and store its real cast_at
                                    elapsed_mins = 648 - remaining
                                    inferred_cast_at = now_for_check - elapsed_mins * 60
                                    db.set_md_cast(suid, t["name"], inferred_cast_at)
                                    return t, "ready", 0
                                if remaining <= 1:
                                    # Effectively zero remaining — genuinely ready, not cooldown.
                                    # Don't classify as "cooldown" with a near-zero wait, that
                                    # creates a tight loop (immediate "recharged" -> instant recheck).
                                    elapsed_mins = 648 - remaining
                                    inferred_cast_at = now_for_check - elapsed_mins * 60
                                    db.set_md_cast(suid, t["name"], inferred_cast_at)
                                    return t, "ready", 0
                                # Genuinely on cooldown — back-calculate cast_at too
                                elapsed_mins = 648 - remaining
                                inferred_cast_at = now_for_check - elapsed_mins * 60
                                db.set_md_cast(suid, t["name"], inferred_cast_at)
                                return t, "cooldown", remaining
                            # Ready to cast — no active record needed, will get cast_at on cast
                            return t, "ready", 0
                        except Exception:
                            return t, "not_trained", 0

                results      = await asyncio.gather(*[_check_md_now(t) for t in trustees])
                md_ready     = [t for t, status, _ in results if status == "ready"]
                md_cooldown  = [t for t, status, _ in results if status == "cooldown"]
                md_not_train = [t for t, status, _ in results if status == "not_trained"]
                longest_cd_mins = max((mins for _, status, mins in results if status == "cooldown"), default=0)

                # Hard floor — refuse to raid with fewer than 5 raid-skilled accounts
                if 0 < len(md_ready) < 5:
                    if longest_cd_mins > 0:
                        wait_secs = longest_cd_mins * 60
                        ready_ts  = int(datetime.now().timestamp()) + wait_secs
                        await notify.send(
                            f"🚫 Only **{len(md_ready)}** account(s) have MD ready/active — "
                            f"below the minimum of 5 raid-skilled accounts needed.\n"
                            f"Waiting for the group to sync, ready <t:{ready_ts}:R> — then re-checking..."
                        )
                        await asyncio.sleep(wait_secs)
                    else:
                        await notify.send(
                            f"🚫 Only **{len(md_ready)}** account(s) have MD ready/active — "
                            f"below the minimum of 5 raid-skilled accounts needed. "
                            f"Waiting **15 minutes** then re-checking..."
                        )
                        await asyncio.sleep(900)
                    continue

                # If there's a genuine mix of ready and cooldown accounts, ask before proceeding
                if md_ready and md_cooldown:
                    await notify.send(
                        f"⚠️ Mixed MD status: **{len(md_ready)}** ready/active, "
                        f"**{len(md_cooldown)}** on cooldown, **{len(md_not_train)}** not trained.\n"
                        f"Reply `!boss-proceed` within 5 minutes to raid with just the "
                        f"**{len(md_ready)}** ready accounts, or do nothing to keep waiting for the rest to sync up."
                    )
                    proceed = False
                    def _check_proceed(m):
                        return (m.channel.id == ctx.channel.id
                                and m.content.strip().lower() == "!boss-proceed")
                    try:
                        await self.bot.wait_for("message", timeout=300, check=_check_proceed)
                        proceed = True
                    except asyncio.TimeoutError:
                        proceed = False

                    if not proceed:
                        # Recompute from persistent state NOW (not the stale longest_cd_mins
                        # from before the 5-minute prompt wait) — gives the exact real
                        # ready time for the slowest account with zero drift
                        fresh_state = db.get_md_state()
                        fresh_now   = datetime.now().timestamp()
                        ready_times = [
                            rec["cast_at"] + MD_TOTAL_CYCLE_SECS
                            for rec in fresh_state.values() if rec.get("cast_at")
                        ]
                        ready_ts = int(max(ready_times)) if ready_times else int(fresh_now + 900)
                        wait_secs = max(0, ready_ts - fresh_now)
                        await notify.send(
                            f"⏳ No response — waiting for the slowest account to recharge, "
                            f"ready <t:{ready_ts}:R>, so everyone is synced before resuming automatically..."
                        )
                        elapsed = 0
                        while elapsed < wait_secs and not self._stop_flag:
                            chunk = min(300, wait_secs - elapsed)
                            await asyncio.sleep(chunk)
                            elapsed += chunk
                        if not self._stop_flag:
                            await notify.send("✅ MD synced — re-checking all accounts...")
                        continue
                    else:
                        await notify.send(f"✅ Proceeding with **{len(md_ready)}** accounts.")

                if not md_ready:
                    # We already know exactly how many are on cooldown vs not
                    # trained from the persistent-state check above — no need
                    # to re-sample a single account via a fresh network poll.
                    if md_cooldown:
                        # Safety floor — never wait less than 60s even if the calculated
                        # remaining time is near-zero. Prevents any tight-loop scenario
                        # where "recharged" -> instant recheck -> "still cooldown" repeats.
                        safe_cd_mins = max(1, longest_cd_mins)
                        ready_ts = int(datetime.now().timestamp()) + safe_cd_mins * 60
                        self._status["phase"] = "waiting_md"
                        await notify.send(
                            f"⏳ MD on cooldown for all **{len(md_cooldown)}** trained accounts — "
                            f"ready <t:{ready_ts}:R>. Waiting for recharge before raiding..."
                        )
                        # Wait for full cooldown in chunks (so !boss-stop stays responsive)
                        total_wait = max(60, safe_cd_mins * 60)
                        elapsed    = 0
                        while elapsed < total_wait and not self._stop_flag:
                            await asyncio.sleep(min(300, total_wait - elapsed))
                            elapsed += 300
                        if not self._stop_flag:
                            await notify.send("✅ MD recharged — checking accounts...")
                        continue
                    else:
                        await notify.send(
                            f"⚠️ No accounts have MD trained/ready — "
                            f"({len(md_not_train)} not trained out of {len(trustees)} total). "
                            f"Waiting 10 mins before retrying..."
                        )
                        await asyncio.sleep(600)
                        continue

                # Save the current MD group to db so !boss-group works
                names_str  = " ".join(t["name"] for t in md_ready)
                group_name = self._status.get("group", f"boss_{self._status.get('source','').lower()}")
                existing   = db.get_group(group_name)
                if existing:
                    db.update_group(group_name, names_str)
                else:
                    db.add_group(group_name, names_str)

                await notify.send(
                    f"🔍 **{len(md_ready)}** accounts have MD trained "
                    f"({len(trustees) - len(md_ready)} excluded)"
                )

                sorted_t = sorted(md_ready, key=lambda t: t.get("rage", 0), reverse=True)

                # 3. Cast all skills and wait for MD
                _dmap, MD_DURATION, md_cast_time, md_already_active, still_missing_suids = \
                    await self._cast_all_skills(md_ready, ctx)
                if self._stop_flag:
                    break

                # Exclude accounts that failed to get MD cast after retries —
                # they will NOT join this raid cycle. They'll be picked up
                # again on the next full MD check once the issue is resolved.
                if still_missing_suids:
                    sorted_t = [t for t in sorted_t if t.get("suid") not in still_missing_suids]

                # Build md_cast_times from REAL persistent cast_at values — not an
                # estimation formula. The old formula assumed MD_DURATION reflected
                # a live-sampled "remaining time", which no longer applies now that
                # MD_DURATION is always the fixed 264min constant. Using the wrong
                # cast_at here was producing incorrect "resuming in Xh" estimates.
                now_ts = datetime.now().timestamp()
                md_state_for_ls = db.get_md_state()
                md_cast_times = {}
                for t in sorted_t:
                    suid = t.get("suid")
                    if not suid:
                        continue
                    record = md_state_for_ls.get(str(suid))
                    if record and record.get("cast_at"):
                        cast_at = record["cast_at"]
                        # Only trust cast_at if it's from this cycle — stale records
                        # from a previous cycle would produce an inflated wait time
                        if cast_at >= now_ts - MD_TOTAL_CYCLE_SECS:
                            md_cast_times[suid] = cast_at
                        else:
                            md_cast_times[suid] = now_ts  # treat as freshly cast
                    else:
                        md_cast_times[suid] = now_ts  # fallback
                    # NOTE: do NOT touch self._ls_cast_times here — it is managed
                    # exclusively by _recast_ls_bg which seeds from the game on
                    # startup and updates on each successful recast.

                # Single source of truth — every account's cast_at was recorded to
                # persistent storage the instant it was actually cast (or inferred
                # via network poll for accounts found already-active). Read it back
                # directly rather than estimating/guessing per-account here.
                # Only include accounts cast within the current cycle (cast_at must be
                # recent — within MD_TOTAL_CYCLE_SECS of now). Stale records from a
                # previous cycle would immediately count as "expired" and falsely
                # trigger the 50% expiry check, cutting raids short.
                md_state_now  = db.get_md_state()
                cycle_start   = now_ts - MD_TOTAL_CYCLE_SECS  # oldest valid cast_at
                md_end_times  = {}
                for t in sorted_t:
                    suid = t.get("suid")
                    if not suid:
                        continue
                    record = md_state_now.get(str(suid))
                    if record and record.get("cast_at"):
                        cast_at = record["cast_at"]
                        if cast_at >= cycle_start:
                            # Recent cast — include in expiry tracking
                            md_end_times[suid] = cast_at + MD_ACTIVE_SECS
                        # Stale records excluded — they won't count toward expiry

                # Wait for EVERYONE — no exclusions, no outlier tricks. If an account
                # genuinely missed its cast, that's a casting-reliability bug to fix
                # at the source (see verification pass below), not something to paper
                # over by averaging it out of the wait calculation.
                self._status["md_end_max"] = max(md_end_times.values()) if md_end_times else md_cast_time + MD_DURATION

                # Drift detection — two distinct signals:
                #
                # 1. Freshly-cast accounts: if MD is genuinely cast on the whole group
                #    together, NOBODY should need a fresh cast mid-cycle — everyone's
                #    MD should still be active together. Any account that needed a
                #    fresh cast this round means it was MISSED in an earlier cast
                #    cycle and has now fallen out of sync with the rest of the group.
                #    This is a real problem, not a normal/expected outcome.
                freshly_cast = [t for t in sorted_t if t.get("suid") not in md_already_active]
                if freshly_cast and md_already_active:
                    # Only worth flagging if SOME accounts were already active —
                    # if EVERYONE was freshly cast, that's a normal first/cold start
                    names = ", ".join(t["name"] for t in freshly_cast)
                    print(f"[MD] ⚠️ {len(freshly_cast)} account(s) needed a FRESH cast while "
                          f"{len(md_already_active)} others were already active — these were "
                          f"missed in an earlier cast cycle and are now out of sync: {names}")

                # 2. Already-active accounts whose cast_at still differs meaningfully
                #    (>5 min) from the group's common cast time — catches drift that
                #    predates this session, inherited from before this fix existed.
                if md_already_active:
                    all_cast_ats = [md_state_now[str(suid)]["cast_at"]
                                     for suid in md_already_active
                                     if str(suid) in md_state_now and md_state_now[str(suid)].get("cast_at")]
                    if all_cast_ats:
                        from statistics import median
                        group_cast_at = median(all_cast_ats)
                        drifted = [
                            (t["name"], md_state_now[str(t["suid"])]["cast_at"])
                            for t in sorted_t
                            if t.get("suid") in md_already_active
                            and str(t["suid"]) in md_state_now
                            and abs(md_state_now[str(t["suid"])].get("cast_at", group_cast_at) - group_cast_at) > 300
                        ]
                        if drifted:
                            drift_desc = ", ".join(
                                f"{name} ({(ts - group_cast_at) / 60:+.0f}m)" for name, ts in drifted
                            )
                            print(f"[MD] {len(drifted)} account(s) drifted from group cast time: {drift_desc}")

                md_end_times_persist.clear()
                md_end_times_persist.update(md_end_times)

                self._status.update({
                    "boss":    current_boss,
                    "raids":   0,
                    "damage":  0,
                    "started": datetime.now(),
                })

                self._status["phase"] = "raiding"
                await notify.send(f"🏴 Raiding **{current_boss}** with **{len(sorted_t)}** accounts")

                # Cast boss potions in the BACKGROUND — all skills (incl. the big
                # Pres damage buffs) are already cast above, so raids start now and
                # pots are applied as they're cast rather than holding up the first
                # raid. The bg task does the initial cast, then recasts every 5 min.
                from outwar.constants import BOSS_POTS, POT_DURATIONS
                asyncio.create_task(self._cast_boss_pots_bg(sorted_t, current_boss, notify, {}))

                # Background LS recast — only spawn once per autoboss session
                if not hasattr(self, '_ls_bg_running') or not self._ls_bg_running:
                    self._ls_bg_running = True
                    async def _ls_bg_wrapper():
                        try:
                            await self._recast_ls_bg(sorted_t, notify)
                        finally:
                            self._ls_bg_running = False
                    asyncio.create_task(_ls_bg_wrapper())

                # 4. Inner raid loop — runs while MD active and boss alive
                session_raids  = 0
                session_damage = 0
                session_best   = 0  # biggest single raid THIS MD cycle (resets per cycle)
                launch_ts      = 0.0  # tracks last launch time for 60s game limit

                while not self._stop_flag:
                    now_ts = datetime.now().timestamp()

                    # Check MD expiry — majority threshold so one drifted account
                    # doesn't hold the entire raid cycle hostage indefinitely
                    still_active_now = [s for s, end in md_end_times.items() if end > now_ts]
                    all_expired = bool(md_end_times) and len(still_active_now) < max(5, len(md_end_times) * 0.5)
                    if all_expired:
                        # Send session summary
                        elapsed = int((datetime.now() - self._status["started"]).total_seconds())
                        mins, secs = divmod(elapsed, 60)
                        hrs, mins  = divmod(mins, 60)
                        elapsed_str = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
                        crew_name = self._status.get("source", group_name or "Unknown")
                        from outwar.table_image import render_boss_raid_summary

                        # Calculate recharge time from cast time
                        MD_TOTAL_CYCLE = SKILL_COOLDOWNS[Skill.MARKDOWN]  # 648 mins from cast (duration included)
                        now_ts2        = datetime.now().timestamp()
                        resume_secs    = max(
                            0, max((cast_t + MD_TOTAL_CYCLE - now_ts2
                                    for cast_t in md_cast_times.values()), default=0)
                        )
                        resume_mins = int(resume_secs // 60) + 2

                        buf = render_boss_raid_summary(crew_name, current_boss, {
                            "raids":        session_raids,
                            "total_damage": session_damage,
                            "best_raid":    session_best,
                            "elapsed":      elapsed_str,
                            "resume_mins":  resume_mins,
                        })
                        await notify.send(file=discord.File(buf, filename="boss_summary.png"))
                        # Wait for MD recharge
                        self._status["phase"] = "waiting_md"
    
                        await self._wait_for_md_recharge(ctx, sorted_t, notify, md_cast_times)
                        break  # Break inner loop, outer loop will recheck boss + MD

                    # Check for higher priority boss / if boss still alive
                    try:
                        spawned = await self._get_spawned_bosses()
                    except Exception as e:
                        print(f"[RAID] Error fetching spawned bosses: {e} — skipping check")
                        spawned = [current_boss]  # assume still alive, don't break
                    if spawned and spawned[0] != current_boss and not manual_lock:
                        cur_priority = BOSS_PRIORITY.index(current_boss) if current_boss in BOSS_PRIORITY else 99
                        new_priority = BOSS_PRIORITY.index(spawned[0]) if spawned[0] in BOSS_PRIORITY else 99
                        if new_priority < cur_priority:
                            await notify.send(f"🔄 Higher priority boss: **{spawned[0]}** — finishing current raid then switching...")
                            current_boss = spawned[0]
                            self._status["boss"] = current_boss

                    # Check if current boss still alive
                    if current_boss not in (spawned or []):
                        await notify.send(f"💀 **{current_boss}** has been defeated!")
                        if manual_lock and start_boss and start_boss.lower() in current_boss.lower():
                            start_boss = None  # target boss is dead — revert to priority order
                        # Send partial summary
                        elapsed = int((datetime.now() - self._status["started"]).total_seconds())
                        mins, secs = divmod(elapsed, 60)
                        hrs, mins  = divmod(mins, 60)
                        elapsed_str = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
                        crew_name = self._status.get("source", group_name or "Unknown")
                        from outwar.table_image import render_boss_raid_summary
                        buf = render_boss_raid_summary(crew_name, current_boss, {
                            "raids":        session_raids,
                            "total_damage": session_damage,
                            "best_raid":    session_best,
                            "elapsed":      elapsed_str,
                            "resume_mins":  0,
                        })
                        await notify.send(file=discord.File(buf, filename="boss_summary.png"))
                        break  # Break inner loop, outer loop will wait for next boss

                    # Do raid — timeout after 3 minutes to prevent stalling
                    consecutive_timeouts = 0
                    MAX_TIMEOUTS         = 3
                    damage = under_minimum = secs_to_hour = new_record = None

                    while consecutive_timeouts < MAX_TIMEOUTS:
                        try:
                            _t0 = datetime.now().timestamp()
                            _gap = (_t0 - launch_ts) if launch_ts else 0.0
                            print(f"[TIMING] Loop B raid start — {_gap:.1f}s since last launch")
                            damage, under_minimum, secs_to_hour, new_record, launch_ts = await asyncio.wait_for(
                                self._do_boss_raid(sorted_t, current_boss, launch_ts),
                                timeout=180
                            )
                            print(f"[TIMING] Loop B _do_boss_raid returned in "
                                  f"{(datetime.now().timestamp() - _t0):.1f}s")
                            consecutive_timeouts = 0
                            break
                        except asyncio.TimeoutError:
                            consecutive_timeouts += 1
                            if hasattr(self.bot, 'health'):
                                self.bot.health.log_raid_failure()
                            if consecutive_timeouts >= MAX_TIMEOUTS:
                                await notify.send(
                                    f"🚨 Raid timed out **{MAX_TIMEOUTS}** times in a row on "
                                    f"**{current_boss}** — pausing for 5 minutes before trying again. "
                                    f"Use `!boss-stop` if the issue persists."
                                )
                                for _ in range(60):
                                    if self._stop_flag:
                                        break
                                    await asyncio.sleep(5)
                            else:
                                await notify.send(
                                    f"⚠️ Raid timed out ({consecutive_timeouts}/{MAX_TIMEOUTS}) — "
                                    f"waiting 30s then retrying..."
                                )
                                await asyncio.sleep(30)
                        except Exception as e:
                            import traceback
                            print(f"[RAID] Unexpected error in _do_boss_raid: {e}")
                            print(traceback.format_exc())
                            await notify.send(f"🚨 Raid error: `{e}` — retrying in 30s...")
                            await asyncio.sleep(30)
                            consecutive_timeouts += 1

                    if self._stop_flag:
                        break

                    if damage is None or launch_ts == 0:
                        # Raid didn't launch — wait 30s before retrying to prevent tight error loop
                        await asyncio.sleep(30)
                        continue

                    if under_minimum and secs_to_hour > 0:
                        # Not enough rage — wait for hour reset
                        wait_mins = secs_to_hour // 60
                        await notify.send(
                            f"⚠️ Low rage — waiting **{wait_mins}m** for hourly reset, then rejoining..."
                        )
                        for _ in range(secs_to_hour // 5):
                            if self._stop_flag:
                                break
                            await asyncio.sleep(5)
                        if not self._stop_flag:
                            # Get remaining accounts to join the existing raid
                            damage, _, __, _new_rec, launch_ts = await self._do_boss_raid(sorted_t, current_boss)

                    session_raids  += 1
                    session_damage += damage
                    session_best         = max(session_best, damage or 0)
                    total_session_raids  += 1
                    total_session_damage += damage
                    # Log to health monitor
                    if hasattr(self.bot, 'health'):
                        self.bot.health.log_raid_success()
                    if not first_raid_done:
                        # Green flag — first confirmed raid of the run (Loop B path).
                        _crew  = self._status.get("source", group_name or "Crew")
                        _chars = self._status.get("last_raid_chars", len(sorted_t))
                        await notify.send(
                            f"🚩 **{_crew}** is up and raiding — first raid complete: "
                            f"**{damage:,}** damage · {_chars}/{len(sorted_t)} accounts.")
                        first_raid_done = True
                    if new_record and damage > 0:
                        await notify.send(
                            f"🏆 **New top raid damage against {current_boss}!**\n"
                            f"**{damage:,}** damage in a single raid by **{self._status.get('source', group_name or 'Unknown').upper()}**"
                        )
                    self._status.update({"raids": session_raids, "damage": session_damage})


            # Final summary on stop
            elapsed = int((datetime.now() - session_start).total_seconds())
            mins, secs = divmod(elapsed, 60)
            hrs, mins  = divmod(mins, 60)
            elapsed_str  = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
            crew_name    = self._status.get("source", group_name or "Unknown")
            final_raids  = total_session_raids
            final_damage = total_session_damage
            print(f"Final summary: raids={final_raids} damage={final_damage}")
            from outwar.table_image import render_boss_raid_summary
            buf = render_boss_raid_summary(crew_name, self._status.get("boss", "—"), {
                "raids":        final_raids,
                "total_damage": final_damage,
                "best_raid":    self._status.get("best_raid", 0),
                "elapsed":      elapsed_str,
                "resume_mins":  0,
            })
            await notify.send(file=discord.File(buf, filename="boss_summary_final.png"))

        except Exception as e:
            await ctx.send(f"❌ Autoboss error: {e}")
            print(f"Autoboss error: {e}")
            if hasattr(self.bot, 'health'):
                self.bot.health.log_error("autoboss", str(e))
            log_ch_id = db.get_alert_channel("log")
            log_ch    = self.bot.get_channel(log_ch_id) if log_ch_id else None
            if log_ch:
                await log_ch.send(f"🔴 **AutoBoss crashed**: `{str(e)[:200]}`")
        finally:
            self._running = False
            if group_name:
                try:
                    db.delete_group(group_name)
                except Exception:
                    pass


    async def _wait_for_md_recharge(self, ctx, trustees: list, notify, cast_times: dict):
        """
        Wait until MD has recharged based on cast time + duration + cooldown.
        MD Level 10: 4h 24m active + ~4h 24m cooldown = ~8h 48m total cycle.
        """
        # MD cooldown (SKILL_COOLDOWNS) = total time from cast until ready again
        # This already includes the active duration — no need to add MD_DURATION
        MD_TOTAL_CYCLE = SKILL_COOLDOWNS[Skill.MARKDOWN]  # 648 mins from cast (duration included)

        # Calculate when the last account's MD will be ready to recast
        now_ts    = datetime.now().timestamp()
        ready_at  = {}
        for t in trustees:
            suid = t.get("suid")
            if not suid:
                continue
            cast_at  = cast_times.get(suid, now_ts - MD_TOTAL_CYCLE)
            ready_ts = cast_at + MD_TOTAL_CYCLE
            ready_at[t["name"]] = ready_ts

        max_ready    = max(ready_at.values()) if ready_at else now_ts
        secs_to_wait = max(0, max_ready - now_ts)

        if secs_to_wait <= 60:
            await notify.send("✅ MD recharged on all accounts — recasting now...")
            return

        slowest_name = max(ready_at, key=ready_at.get)
        ready_ts     = int(max_ready)
        await notify.send(
            f"⏳ MD cooldown active. Resuming <t:{ready_ts}:R> "
            f"(last account: **{slowest_name}**)\n"
            f"Sleeping until all accounts are ready..."
        )

        elapsed_sleep = 0
        chunk         = 300

        while elapsed_sleep < secs_to_wait and not self._stop_flag:
            sleep_this     = min(chunk, secs_to_wait - elapsed_sleep)
            await asyncio.sleep(sleep_this)
            elapsed_sleep += sleep_this

        if not self._stop_flag:
            await notify.send("✅ MD recharged — recasting skills and resuming raids...")

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(name="autoboss", aliases=["ab"])
    async def autoboss(self, ctx, group: str, boss: str = None):
        """
        Auto-raid bosses indefinitely. Waits for a boss to spawn, checks MD
        fresh each cycle, casts skills, and raids hands-free until stopped.
        Usage: !autoboss <group|crew>
        """
        if self._running:
            await ctx.send("⚠️ A boss raid session is already running. Use `!boss-stop` to stop it.")
            return

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        group_name      = f"boss_{group.lower()}"
        self._running       = True
        self._stop_flag     = False
        self._ls_cast_times = {}
        self._ls_bg_running = False
        self._status    = {
            "group":            group_name,
            "source":           group,
            "started":          datetime.now(),
            "raids":            0,
            "damage":           0,
            "boss":             "—",
            "boss_url":         "",
            "former":           "—",
            "former_url":       "",
            "sin_name":         "—",
            "sin_url":          "",
            "last_raid_damage": 0,
            "last_raid_chars":  0,
            "best_raid":        0,
            "md_end_max":       0,
            "phase":            "waiting_boss",
        }

        await ctx.send(
            f"✅ AutoBoss started for `{group}` (**{len(trustees)}** accounts)\n"
            f"MD checked fresh each time a boss spawns.\n"
            f"Use `!boss-stop` to stop · `!boss-status` for live stats"
        )


        asyncio.create_task(self._run_autoboss(ctx, trustees, boss, group_name))


    @commands.command(name="raidboss")
    async def raidboss(self, ctx, group: str, boss: str = None):
        """
        Do one round of boss raids without skill casting.
        Assumes skills are already active.
        Usage: !raidboss <group> [boss_name]
        """
        if self._running:
            await ctx.send("⚠️ A session is already running. Use `!boss-stop` to stop it.")
            return

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        spawned = await self._get_spawned_bosses()
        if boss:
            current_boss = next((b for b in BOSS_PRIORITY if boss.lower() in b.lower()), None)
        else:
            current_boss = spawned[0] if spawned else None

        if not current_boss:
            await ctx.send("❌ No bosses spawned or boss not found.")
            return

        await ctx.send(f"⚔️ Raiding **{current_boss}** with **{len(trustees)}** accounts...")

        sorted_t = sorted(trustees, key=lambda t: t.get("rage", 0), reverse=True)
        damage, _, __, _new_rec, launch_ts = await self._do_boss_raid(sorted_t, current_boss)
        await ctx.send(f"**{current_boss}** · {damage:,} dmg")

    @commands.command(name="bossraid", aliases=["br"])
    async def bossraid(self, ctx, group: str, *args):
        """
        Raid a CREW BOSS with NO skill or potion casting (assumes skills are
        already active). Prime Gods use !rm / !rg instead.

        Usage:
          !bossraid <group>                — raid the priority boss until you stop it or it dies
          !bossraid <group> <boss>         — raid <boss> until you stop it or it dies
          !bossraid <group> <count>        — raid the priority boss <count> times
          !bossraid <group> <count> <boss> — raid <boss> <count> times (order doesn't matter)

        Use `!boss-stop` to stop at any time.
        """
        if self._running:
            await ctx.send("⚠️ A session is already running. Use `!boss-stop` to stop it.")
            return

        # Flexible args — a number is the raid count, a word is the boss name.
        # No count given → run until stopped or the boss dies.
        count = None
        boss  = None
        for a in args:
            if a.isdigit() and count is None:
                count = int(a)
            elif boss is None:
                boss = a
        if count is not None and (count < 1 or count > 1000):
            await ctx.send("Raid count must be between 1 and 1000.")
            return

        trustees = self._resolve_group(group)
        if not trustees:
            await ctx.send(f"No characters found for `{group}`.")
            return

        spawned = await self._get_spawned_bosses()
        if boss:
            current_boss = next((b for b in BOSS_PRIORITY if boss.lower() in b.lower()), None)
            if not current_boss:
                await ctx.send(f"❌ Boss `{boss}` not recognised.")
                return
        else:
            current_boss = spawned[0] if spawned else None
        if not current_boss:
            await ctx.send("❌ No bosses spawned or boss not found.")
            return

        sorted_t = sorted(trustees, key=lambda t: t.get("rage", 0), reverse=True)

        self._running   = True
        self._stop_flag = False
        limit_str = f"**{count}** raid(s)" if count is not None else "until you stop it or the boss dies"
        await ctx.send(
            f"⚔️ Raiding **{current_boss}** with **{len(sorted_t)}** accounts — {limit_str}. "
            f"No skills or pots will be cast. Use `!boss-stop` to stop."
        )

        done         = 0
        total_damage = 0
        launch_ts    = 0.0
        try:
            while (count is None or done < count) and not self._stop_flag:
                # Re-pick the spawned boss in priority order each round, unless the
                # caller locked to a specific boss.
                try:
                    spawned = await self._get_spawned_bosses()
                except Exception as e:
                    print(f"[BOSSRAID] spawn check error: {e}")
                    spawned = [current_boss]

                if boss:
                    # Locked to a named boss — stop if it has died
                    if current_boss not in (spawned or []):
                        await ctx.send(f"💀 **{current_boss}** is no longer spawned — stopping after {done} raid(s).")
                        break
                else:
                    if spawned:
                        current_boss = spawned[0]
                    else:
                        await ctx.send(f"💀 No boss currently spawned — stopping after {done} raid(s).")
                        break

                # Do the raid — bounded so a stall can't hang the loop
                consecutive_timeouts = 0
                damage = under_minimum = secs_to_hour = None
                while consecutive_timeouts < 3:
                    try:
                        damage, under_minimum, secs_to_hour, _new_rec, launch_ts = await asyncio.wait_for(
                            self._do_boss_raid(sorted_t, current_boss, launch_ts),
                            timeout=180
                        )
                        break
                    except asyncio.TimeoutError:
                        consecutive_timeouts += 1
                        if consecutive_timeouts >= 3:
                            await ctx.send(f"🚨 Raid timed out 3× in a row — stopping after {done} raid(s).")
                            self._stop_flag = True
                        else:
                            await asyncio.sleep(30)
                    except Exception as e:
                        await ctx.send(f"🚨 Raid error: `{e}` — stopping after {done} raid(s).")
                        self._stop_flag = True
                        break

                if self._stop_flag:
                    break

                if damage is None or launch_ts == 0:
                    # Didn't launch — brief wait to avoid a tight error loop, no count
                    await asyncio.sleep(30)
                    continue

                if under_minimum and secs_to_hour and secs_to_hour > 0:
                    wait_mins = secs_to_hour // 60
                    await ctx.send(f"⚠️ Low rage — waiting **{wait_mins}m** for the hourly reset...")
                    for _ in range(secs_to_hour // 5):
                        if self._stop_flag:
                            break
                        await asyncio.sleep(5)
                    if not self._stop_flag:
                        damage, _, __, _nr, launch_ts = await self._do_boss_raid(sorted_t, current_boss)

                done         += 1
                total_damage += (damage or 0)
                if done % 30 == 0 and (count is None or done < count):
                    progress = f"{done}/{count}" if count is not None else str(done)
                    await ctx.send(f"… {progress} raids · {total_damage:,} dmg so far")
        finally:
            self._running   = False
            self._stop_flag = False

        await ctx.send(
            f"✅ Done — **{done}** raid(s) on **{current_boss}** · **{total_damage:,}** total damage"
        )

    @commands.command(name="boss-group")
    async def boss_group(self, ctx):
        """Show accounts in the current autoboss session."""
        if not self._running:
            await ctx.send("No boss raid session is running.")
            return
        s          = self._status
        group_name = s.get("group", "")
        group      = db.get_group(group_name)
        if not group:
            await ctx.send("No group data yet — boss hasn't spawned this session.")
            return
        members = db.group_to_list(group)
        chunks  = [members[i:i+50] for i in range(0, len(members), 50)]
        for i, chunk in enumerate(chunks):
            header = f"**{group_name}** — {len(members)} accounts" if i == 0 else "​"
            await ctx.send(f"{header}\n`{' · '.join(chunk)}`")

    @commands.command(name="boss-proceed")
    async def boss_proceed(self, ctx):
        """Confirm proceeding with a partial-readiness raid when prompted by autoboss."""
        if not self._running:
            await ctx.send("No boss raid session is waiting for confirmation.")
            return
        # The actual wait_for in _run_autoboss listens for this message directly

    @commands.command(name="boss-stop", aliases=["bstop", "bs"])
    async def boss_stop(self, ctx):
        """Stop the current boss raid session after the current raid completes."""
        if not self._running:
            await ctx.send("No boss raid session is running.")
            return
        self._stop_flag = True
        await ctx.send("⏹️ Stop signal sent — current raid will complete then session will end.")

    @commands.command(name="reset-md")
    async def reset_md(self, ctx):
        """
        Clear the stored MD cast timestamps. Use this if the bot is reporting
        wrong MD cooldown times (e.g. after a failed cast run polluted the
        state). On the next !autoboss the bot will re-poll the real cooldown
        from the game for every account instead of trusting stored timestamps.
        """
        state = db.get_md_state()
        count = len(state)
        db.save_md_state({})
        await ctx.send(
            f"🗑️ Cleared **{count}** stored MD timestamp(s). "
            f"The next `!autoboss` will re-poll real cooldowns from the game."
        )
        print(f"[CAST] MD state reset by {ctx.author} — cleared {count} records")

    @commands.command(name="boss-status", aliases=["bstat"])
    async def boss_status_cmd(self, ctx):
        """Show current boss raid session status."""
        if not self._running:
            await ctx.send("No boss raid session is running.")
            return

        s      = self._status
        phase  = s.get("phase", "raiding")
        source = s.get("source", s.get("group", "—")).upper()

        phase_labels = {
            "waiting_boss": "⏳ Waiting for boss to spawn",
            "waiting_md":   "⏳ Waiting for MD recharge",
            "forming":      "⚙️ Forming a raid",
            "raiding":      "⚔️ Raiding",
        }
        phase_str = phase_labels.get(phase, "⚔️ Raiding")

        md_end  = s.get("md_end_max", 0)
        md_secs = max(0, int(md_end - datetime.now().timestamp()))
        md_hrs  = md_secs // 3600
        md_mins = (md_secs % 3600) // 60
        md_s    = md_secs % 60
        if md_secs > 0:
            import datetime as _dt
            md_eta  = (_dt.datetime.now() + _dt.timedelta(seconds=md_secs)).strftime("%I:%M%p").lstrip("0")
            md_str  = f"{md_hrs}h {md_mins}m {md_s}s ({md_eta})"
        else:
            md_str  = "—"

        raids      = s.get("raids", 0)
        damage     = s.get("damage", 0)
        best       = s.get("best_raid", 0)
        last_dmg   = s.get("last_raid_damage", 0)
        last_chars = s.get("last_raid_chars", 0)
        avg_raid   = damage // raids if raids > 0 else 0
        avg_char   = damage // (raids * last_chars) if raids > 0 and last_chars > 0 else 0

        boss_name = s.get("boss", "—")
        former    = s.get("former", "—")
        sin_name  = s.get("sin_name", "—")

        # Retrieve all-time record for this boss
        settings   = db.get_settings()
        boss_key   = boss_name.split(",")[0].strip().lower()
        record_dmg = settings.get("boss_records", {}).get(boss_key, {}).get("best", 0)

        embed = discord.Embed(
            title=f"{es.ICON_BOSS} Boss Raid Status — {source}",
            colour=es.COLOR_INFO
        )
        embed.timestamp = datetime.now()
        embed.add_field(name="Status", value=phase_str, inline=False)

        embed.add_field(name="Target", value=boss_name, inline=True)
        embed.add_field(name="Former", value=former,    inline=True)
        embed.add_field(name="Sinner", value=sin_name,  inline=True)

        embed.add_field(name="Markdown Remaining", value=md_str,          inline=True)
        embed.add_field(name="Raids Completed",    value=f"{raids:,}",    inline=True)
        embed.add_field(name="Best This Session",  value=f"{best:,}",     inline=True)

        embed.add_field(name="Total Damage",       value=f"{damage:,}",   inline=True)
        embed.add_field(name="Avg Per Raid",        value=f"{avg_raid:,}", inline=True)
        embed.add_field(name="Avg Per Char",        value=f"{avg_char:,}", inline=True)

        if last_dmg:
            embed.add_field(
                name="Last Raid",
                value=f"{last_chars} characters dealt **{last_dmg:,}** damage to {boss_name}",
                inline=False
            )

        if record_dmg:
            embed.add_field(
                name="All-Time Record",
                value=f"{record_dmg:,} damage in a single raid against {boss_name}",
                inline=False
            )

        sin_text = f"Cast on {sin_name}" if sin_name not in ("—", "", "No SiN Cast") else "Not cast this session"
        embed.set_footer(text=f"SiN: {sin_text}  ·  {es.BRAND_FOOTER}")

        await ctx.send(embed=embed)

    def _resolve_group(self, group: str) -> list:
        all_trustees = db.get_trustees()
        excluded = {n.lower() for n in db.get_excluded()}
        def _keep(lst):
            return [t for t in lst if t["name"].lower() not in excluded]
        rga_group = db.get_group(group)
        if rga_group:
            names = set(db.group_to_list(rga_group))
            return _keep([t for t in all_trustees if t["name"] in names])
        crew = db.get_crew(group)
        crew_full = crew["full_name"] if crew else db.normalize_crew(group)
        by_crew = db.get_trustees_by_crew(crew_full)
        if by_crew:
            return _keep(by_crew)
        return _keep([t for t in all_trustees if t["name"].lower() == group.lower()])


async def setup(bot):
    await bot.add_cog(BossRaidCommands(bot))
