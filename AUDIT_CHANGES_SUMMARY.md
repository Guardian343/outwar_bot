# DeathBot — Summary of Changes Since the Full Audit

This document lists every change made during and after the code audit, what it
does, and whether it is still in place. Share with your friend for review.

---

## ⚠️ The big one: the `serverid=1` regression (NOW REVERTED)

**What happened:** On a friend's advice, `serverid=1` was added to the skill-cast
URLs (`cast_skills.php?serverid=1` plus a `serverid` form field) and to the
`skills_info.php` polls.

**The effect:** Outwar did NOT accept `serverid` on `cast_skills.php`. Instead of
processing the cast, the server returned a frame/wrapper page (HTML starting with
`#outerdiv` / `#inneriframe`, 160×600px — an ad-frame size). Every skill on every
account returned this page, so the debug classified all 17 skills as `ERROR` and
`cast_ok` ended at 0/187. No skills were cast for an entire MD cycle.

**Status: FULLY REVERTED.** All cast and skills_info API calls are back to their
plain pre-audit form:
- `cast_skills.php` (no serverid) — main cast, MD retry, SiN, LS recast
- `skills_info.php?id=...` (no serverid) — MD/LS polls

The only `serverid=1` references that remain are two **display-only** profile links
shown in Discord embeds (`sin_url`, `former_url`). They are not API calls and do
not affect anything.

---

## Knock-on effect: polluted MD state → "ready in 11 hours" (FIX PROVIDED)

**Why it shows 660 min / 11h when your longest real cooldown is 396 min:**
During the failed cast run, the network-poll fallback stored inferred `cast_at`
timestamps in `md_state.json`. Because the cast never actually succeeded, some of
those timestamps were written as if the account had *just* cast — so the bot now
computes `ready_at = cast_at + 648 min` ≈ 11 hours out.

Replacing `boss_raid_commands.py` stops NEW bad data, but the **already-polluted
`md_state.json` on disk will keep reporting the wrong time** until those stale
timestamps age out or a real cast overwrites them.

**FIX: new `!reset-md` command** (admin-only). It clears all stored MD timestamps.
On the next `!autoboss`, the bot re-polls each account's real cooldown from the
game instead of trusting the bad stored values. Run `!reset-md` once before your
next `!autoboss` and the 11-hour figure will be replaced by the real cooldowns.

---

## Skill casting

- **CAST_DEBUG flag** (currently `True`, near the top of `_cast_all_skills`):
  logs per-skill outcome (CAST / ACTIVE / NOTRAINED / RATELIMIT / ERROR / EMPTY /
  OTHER), the raw response when MD doesn't cast, a per-account summary, and a final
  list of accounts not in `cast_ok`. **Set to `False` once a clean cast is
  confirmed** to silence it.
- **MD cast FIRST:** `all_skills` now begins with Markdown, then class, then the
  rest. Previously MD was 6th (after 5 class skills); if an account failed partway
  through, MD could be missed. Now the critical skill always goes first.
- **MD-active skip logic removed (earlier change, still in place):** the bot always
  casts all 17 skills on all accounts every cycle. The game returns "already cast"
  for active skills and moves on. This was a deliberate correctness-over-speed
  choice.
- **Sequential per-account casting retained.** Your friend's concurrent-per-character
  idea (skill_sem=3 inside account_sem=10) is a valid speed optimisation but was
  deferred so the debug logs stay clean while diagnosing skips. Easy to switch on
  later.

## Timing constants (unchanged, for reference)
- `MD_ACTIVE_SECS = 264 * 60` (4h 24m active)
- `MD_TOTAL_CYCLE_SECS = 648 min` (active + cooldown, from cast)
- `LS_RECAST_SECS = 163 * 60` (162 min cooldown from cast + 1 min buffer)

---

## Raid launch (fixed twice)

1. **Removed the `asyncio.wait_for(get_as(...), timeout=60)` wrapper** around the
   launch. `get_as` already has its own internal retry/timeout (25 attempts, 60s
   each). The outer `wait_for` would cancel a launch that had actually succeeded on
   the server, returning `launch_ts=0` → the loop thought it failed → re-formed,
   re-joined, waited 42s, and looped forever. This was the "endless waiting to
   launch" bug.
2. **Reverted serverid from the launch URL** — it's back to exactly
   `joinraid.php?raidid={raidid}&launchraid=yes` as before the audit.
3. **Added `[RAID-DEBUG]` logging:** logs the parsed raid_url/raidid/former before
   launch, the exact launch URL, and the launch response. Helps confirm the launch
   actually fires.

**Note:** the "Waiting 42s before launch" message is **correct** — Outwar enforces a
60-second minimum between launches, so the bot waits the remainder. Not a bug.

---

## Raid counter (fixed)

- Counter was inflating to ~10,000+. Cause: `_do_boss_raid` returns `damage=0`
  (not `None`) on error, and the old `if damage is None` guard let error-returns
  increment the counter. **Fix:** both loops now `if damage is None or launch_ts == 0:
  sleep(30); continue` — failed raids (no real launch) are not counted, and the
  30s sleep prevents a tight error loop from ever spinning the count up.
- Counter resets to 0 each MD cycle, so a mid-cycle restart won't show prior raids.

---

## Prime god loot link (fixed)

- Death-embed loot links used `god_id` as the `spawnid` → "Invalid spawn ID."
  Those are different values; the real `spawnid` only exists after death and lives
  on the god's page. **Fix:** the death embed now fetches `primegods?mobid={god_id}`,
  parses the page, and extracts the real `spawnid` (same method the working drops
  code uses). Resolved concurrently so the embed isn't slowed; falls back to an
  unlinked name if the spawnid can't be resolved yet.

---

## HP% parsing (corrected after initial mistake)

- **Prime gods are TIME-based** (die on a timer; the progress bar is time remaining,
  not health) → `God.hp_pct` is NOT populated. The misleading parse was removed.
- **Crew raid bosses are HEALTH-based** (crews attack until 0% HP) → `Boss` dataclass
  gained an `hp_pct` field and the parse from `card-user_occupation` is wired in.
  Captured for future use (e.g. `!boss-status`); not read anywhere yet, so nothing
  breaks.

---

## parse_max_rage (fixed — was broken before the audit)

- `parse_max_rage` had lost its `def` line in an earlier edit, leaving its body as
  dead code after `parse_character_stats_profile`'s `return`. `character_commands.py`
  imported it → `!show-mr` silently returned 0 for every account. **Fix:** restored
  the function definition. `!show-mr` also switched from cookie-mutation to `get_as`.
- Scanned the whole codebase for other "orphaned body after return" patterns — none
  remain (the `get_sse` instance was fixed earlier; this was the last one).

---

## Dead-code cleanup (cosmetic, low risk)

- Removed unused imports across 9 files (timezone, urlencode, Optional, parse_envoys,
  parse_god_stats_page, parse_rage, parse_markdown_status, format_cooldown,
  format_time_remaining, OWNER_ID, a standalone `import discord`, plus 9 unused
  imports in boss_commands.py and 4 in health.py).
- Removed dead local variables (now_ts, raid_start ×2, ab_name; unused tuple-unpack
  results renamed to throwaways).
- `parse_crew_boss_loot` function is still in scraper.py — only an unused *import*
  of it was removed from boss_commands.py. The `boss_drops` command parses crew loot
  inline and is unchanged.

---

## Envoy drops (untouched — your WIP is intact)

No envoy logic was changed. `parse_envoys`, the `Envoy` dataclass,
`_process_envoy_changes`, and `_post_envoy_drops` (pool detection, 60-min SSE
timeout, multi-part embeds, trustee highlighting) are all exactly as you left them.
The envoy-death → `_post_envoy_drops` trigger is in place and ready for your
next-cycle test.

---

## What to do on the next run

1. Replace the whole `outwar_bot` folder with the latest export.
2. Run `!reset-md` once to clear the polluted MD timestamps.
3. Wait for MD to recharge, then `!autoboss lod`.
4. Watch the `[CAST-DEBUG]` lines — you should now see `MD=CAST` / `MD=ACTIVE`
   instead of `MD=ERROR`.
5. Once a clean cast is confirmed, set `CAST_DEBUG = False` to silence the logs.
