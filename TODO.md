# DeathBot To-Do List
Last updated: 2026-08-02

## 🔴 Critical (Fix Before Next Major Session)

- [x] **MD cycle ended early, wasting MD (the real "mixed status" cause)** — FIXED 2026-08-02
  ROOT CAUSE (found after tracing the actual loop, not the display): the inner raid loop broke when a
  **50% MAJORITY** of accounts' MD expired (line ~1164, `majority_expired`), NOT when the last account
  expired. So with even a 3-min cast spread, once ~130/197 crossed the active→cooldown line the cycle
  moved on, leaving the other ~67 with ~2 min of UNUSED MD active → that's the "67 ready/130 cooldown"
  message, and wasted MD. The 50% threshold was a deliberate "don't let a straggler hold the loop" choice
  but conflicted with Liam's explicit requirement.
  FIX: cycle now runs until the LAST account's MD expires (`all_expired = len(still_active_now) == 0`).
  Every account's MD is spent by cycle end; no fragmentation. Safe because casts are tightly grouped
  (verified 3-min spread) and stale previous-cycle records are already filtered from md_end_times.
  ALSO FIXED: the "Raids continue for Xh" DISPLAY used min() (earliest account) — now uses max() to match.
  ALSO ADDED: **drift monitor** — existing >5min drift logging now ESCALATES to a channel alert when any
  account is ≥15 min out of sync (Liam's concern: large drift + run-to-last = wasted rage). Surfaces drift
  before it becomes a rage problem.
  ⚠️ VERIFY AFTER DEPLOY: watch that casting stays tightly grouped across sessions (doesn't creep to
  15/30/60 min). If the drift alert fires, investigate casting reliability at the source.
  NOTE: the SECONDARY 50% gate at line ~1146 is triggered by a BOSS DYING (not MD expiry) and decides:
  keep raiding the next boss on still-active MD, ELSE fall through to WAIT FOR RECHARGE (you can't recast
  MD mid-cooldown — it must recharge). After this fix both real scenarios land correctly: boss died →
  everyone still active → continue; MD expired → everyone expired → wait. So the 50% there is now largely
  inert. The recharge-wait (`_wait_for_md_recharge`) ALREADY waits for the LAST account (max_ready), which
  is correct. Left the gate unchanged — low-impact, touches the boss-died path, needs live testing.


- [ ] **Verify background pot task vs raid session isolation** `🔴 Hard`
  Historically a cookie-jar race: `_cast_boss_pots_bg` switched `ow_userid` per account while
  `_do_boss_raid` ran concurrently. The migration to per-request `_as` calls (post_as/get_as pass
  ow_userid per request, no shared cookie mutation) should have removed this, but it has NOT been
  confirmed end-to-end in production with pots now doing their initial cast in the background.
  **Action:** confirm during the upcoming test run that joins/launches never fire under the wrong account.

## 🏗️ Infrastructure (Pi / deployment)

- [ ] **Auto-git deploy** `🟡 Medium` — **HIGH PRIORITY, build first (force multiplier)**
  Pi auto-pulls + restarts on git push, so every future deploy is faster and the wrong-command class of
  mistakes (see the two-bots flood, 2026-07-29) can't happen. Design agreed:
  • Poll GitHub every **5 min** (traffic negligible, deploys rare — not every 1 min).
  • **CRITICAL: never restart mid-activity.** Detect change → WAIT until bot idle (no PW cycle / boss raid /
    active casts) → then restart. Investigate what "busy" signal the bot exposes (maybe status.json) or add a
    lightweight busy-marker.
  • Validate code loads before restart; keep old version + alert on failure.
  • Poll method (no inbound exposure). Restart via `deathbot-supervisor` only.
- [ ] **Uptime monitoring (Uptime Kuma)** `⚡ Easy`
  Watches bot + dashboard, phone alert if either goes down. ~20 min setup, runs on the Pi.
- [ ] **Dashboard envoy pool display is orphaned** `⚡ Easy` (fold into envoy feature)
  `status.json` `envoy_pool_last`=49 is stale; `settings.json` `envoy_loot_pool`=50 is correct. NO current
  code writes `envoy_pool_last` (publish_settings_meta doesn't exist in status_writer; auth.py's call fails
  silently in try/except). Cosmetic. Fix: write a real publisher, call it when the pool changes (not just
  on_ready), reconcile "last completed" vs "next" semantics.

## 🧪 Test & Verify (do these first — directive: test the last ~24h of changes before anything else)

- [ ] **Full production test of recent changes** `🧪`
  Skill/pots ordering, 30-min pot guard, !bossraid, !rm/!rg live spawn check, restyled summary,
  per-cycle Best Raid, drop-count fix + aggregation, and the new help system all need a real run.
- [ ] **Turn off CAST_DEBUG** `⚡ Easy`
  Set `CAST_DEBUG = False` in `boss_raid_commands.py` once one clean cast cycle is confirmed in the log.
- [ ] **Confirm drop-summary aggregation choices** `🧪`
  Amulets are combined by COUNT ("Amulets x3"); points are summed as value×quantity ("93 points x2" → 186).
  Verify both read correctly against a live spawn; adjust if amulet value or naming should be preserved.

## 🟡 Medium Priority

- [ ] **Raid count / join concurrency** `🟡 Medium` — DEFERRED until the test run above is done
  ~194 raids per 264-min cycle ≈ 82s/raid. The 60s launch gap is a floor; the rest is form + joining
  ~187 accounts at only `Semaphore(3)` + settle + launch + fetch. Likely lever: raise the join semaphore.
  **Action:** add a per-raid timing log (form/join/launch/fetch) FIRST to confirm the join is the cost,
  then cautiously raise concurrency and watch for join rate-limiting. Do NOT change blind.

- [ ] **Apply drop-count fix to the envoy drop summary** `⚡ Easy`
  `god_monitor.py` envoy summary still counts line items (`len(items)`), the same bug just fixed on the
  god summary. Switch it to the parser's `drop_count`.

- [ ] **Background task lifecycle — boss change** `🟡 Medium`
  When a boss dies and a new one spawns, the previous cycle's `_cast_boss_pots_bg` / `_recast_ls_bg`
  may still run against the old `sorted_t`. Both only check `self._stop_flag` (set by `!boss-stop`).
  **Fix:** per-cycle cancel token the outer loop sets when moving to a new boss.

- [ ] **Inline imports inside coroutines** `🟡 Medium`
  `import re`, `from bs4 import BeautifulSoup`, scraper imports etc. appear inside async functions in
  several cogs. Cached so not a perf issue, but messy. **Fix:** move to file-level imports.

- [ ] **Audit silent `except: pass` blocks** `🟡 Medium`
  We've been bitten TWICE by silent failures: (1) envoy auto-fetch dead code, (2) the dashboard pool
  publisher (`status_writer.publish_settings_meta` doesn't exist; auth.py's call fails silently). A
  bare `except: pass` hides real breakage. **Fix:** hunt down these blocks and make them at least LOG
  (logger.warning) so bugs surface instead of vanishing. Do carefully — some may be intentional.

- [ ] **Centralise MD constants** `🟡 Medium`
  `648` (total MD cooldown) and `264` (MD active) live in `boss_raid_commands.py`. Add `MD_TOTAL_MINS`,
  `MD_ACTIVE_MINS`, `MD_THRESHOLD_MINS` to `constants.py` and import from there.

- [ ] **Raid history logging** `🟡 Medium`
  Save every raid attempt (boss, group, damage, char count, timestamp) to JSON. Add `!boss-history`.

- [ ] **Staggered Last Stand recast messages** `🟡 Medium`
  Multiple LS recast messages per session (e.g. "recast 3", "recast 40", "recast 140") instead of one batch.
  Root cause (diagnosed): (1) initial casts NOT simultaneous — Semaphore(10) casts in ~20 waves across ~197
  accounts + retry backoff, so `_ls_cast_times` are spread over minutes; (2) 5-min check interval quantises
  them into different buckets; (3) rate-limit retries during recast reshuffle cast times → drift compounds.
  Functional (LS stays up) — cosmetic noise + real timer drift. Fix options: quiet the message (like potions),
  OR batch accounts coming due before the next check together, OR tighten the interval. Watch drift never
  grows enough to lapse LS before the check catches it.

- [ ] **First-raid-completed / MD-recharge flag** `🟡 Medium`
  The "first raid completed" flag only posts when raids first START, not when MD recharges mid-session.
  Fine for single-crew; needs addressing for MULTI-CREW boss raiding (which crew's MD recharged, per-crew
  completion tracking). Design with the real multi-crew flow in mind.

- [ ] **Event boss handling** `🟡 Medium` (observe first, 3-monthly event)
  Event boss acts like a normal boss on crew_bossspawns (appears + auto-spawns when event ends, goes grey
  when killed but stays on page ~1 week for loot rolling, then removed). Bot SHOULD auto-handle spawn/death/
  drops with no special-casing (keys off page not fixed list). WATCH this cycle: (1) does the drop summary
  come through complete given the larger/slower loot pool vs the fixed 15s wait? — ties into loot-completion
  polling above; (2) minor cosmetic — default spawn_days=14 may show a meaningless "next spawn window" for
  the dead-on-page week (suppress for unknown bosses if it looks wrong). No pre-emptive work — observe, fix after.

## ⚡ Quick Wins

- [ ] **Potion stock check** `⚡ Easy`
  Before boss raids, scan group backpacks and alert if a required pot is missing on more than X accounts.
- [ ] **`!leaderboard`** `⚡ Easy`
  Top characters by power/ele/chaos as an image table.
- [ ] **Cap reset notifications** `⚡ Easy`
  Post to channel when LoD accounts are ready to raid Prime Gods again after the daily cap reset.
- [ ] **Loot-completion polling (best practice)** `🟡 Medium`
  Replace the fixed `await asyncio.sleep(15)` before reading a boss stats page for drops with polling for
  the page's real "Status: Loot completed" flag. Self-adjusts to pool size — fixes the event-boss slow-roll
  risk and makes envoy auto-fetch robust. Applies to normal bosses, event bosses, AND envoys (one pattern,
  many wins). To verify: exact text/location of the marker, whether boss stats pages and envoy loot pages
  use the same wording, and a sensible max-wait timeout (~30-60 min, matches existing envoy SSE).
- [ ] **pcaps orange threshold — tune** `⚡ Easy`
  Currently orange at 80% caps used. Liam may drop to 70% after testing, OR switch to an absolute
  "caps remaining ≤ N" rule (more consistent across max caps 10/11/12/13, directly answers "can I still
  raid this account?"). One-line change either way.
- [ ] **`!god set <name> room 0` = clear** `⚡ Easy`
  Adds an "unset manual room" so a stale event-prime override can fall back to table/crawl again. Manual
  room currently wins over everything, so there's no way to clear it except setting the correct table value.

## 💡 Suggested Ideas (streamlining — make the bot easy for new members)

- [ ] **Command groups / subcommands** `🟡 Medium` — `!boss` group is DONE (pilot). Roll the same
      pattern out to the other families next: `!cast all|class|pres|fero|afflic|ss|raid`,
      `!god list|set|rm|rg|rq`, `!summary set|now|list|remove`, `!alert channels|set`,
      `!trustees scan|update|check`. Old names stay as classic commands so nothing breaks.
- [ ] **Consolidated `!status` dashboard** `⚡ Easy`
  Merge `status` + `boss-status` + `health` + `check-md` into one at-a-glance view: autoboss state,
  MD across the group, current boss, alerts on/off, last drop.
- [ ] **More short aliases for hot commands** `⚡ Easy`
  Done: ab, br, bstop/bs, bstat. Add a few more (e.g. cast shortcuts) as members request them.
- [ ] **Pinned "Getting Started" guide** `⚡ Easy`
  A short channel-pinned walkthrough for new members: how to start a raid, read the summary, check status.
- [ ] **Worked examples in `!help <command>`** `⚡ Easy`
  Extend per-command help to include a concrete example line for the most-used commands.

## 🔵 Pending Features

- [ ] **`!primewatcher` Phase 2 — raiding engine** `🔴 Hard`
      Rules (do not alter):
      • Trigger: xx:40 each hour, for every ENABLED watcher.
      • Target is PER SPAWN, absolute: "[crew] must have [X] caps on the CURRENT spawn stats". Read the
        crew's current caps off the prime's live leaderboard; if below X, top up toward X this cycle.
        NOT "+X every cycle". When the prime respawns, the leaderboard resets and it chases X again.
      • Resume within a spawn across cycles (got 1, lost 9 → next :40 needs X-1, not X).
      • Groups come from !autorank (groups.json); each group raided INTACT, never mix accounts.
      • Even spread: within a watcher, assign a different group to each spawned prime and ROTATE the
        assignment daily (randomise/seed by date) so cap usage spreads — e.g. day 1: g4→Rillax, g5→Villax,
        g6→Zikkir…; day 2: g9→Rillax, g10→Villax, g4→Zikkir… A group can place multiple caps (limited by
        each account's cap budget, not 1/group). FALLBACK: if a prime's assigned group is capped/insufficient
        before its target is met, fall back to the next available group in the bundle to finish (consistent
        caps across gods/days).
      • TWO different cap displays exist in-game:
          - TOOLBAR (top of every page) = AVAILABLE/MAX.
          - The "God Cap: X/Y" on the HOME/PROFILE page (what parse_god_cap reads; links to /crew_capstatus)
            = USED/MAX. The /crew_capstatus page is also USED/MAX (and holds the per-cap regen times).
        Confirmed by Liam 2026-06-29 (Hawthorne read 2/10 on home = 2 USED = 8 available).
      • Bot reads the HOME value (used/max) and converts: available = MAX − USED; capped iff available ≤ 0.
        MAX is per-account, can be >10 (upgrades give up to +3). Each cap regens 7 days after its own use (rolling).
      • ✅ Every parse_god_cap consumer converts used→available, then shows/compares AVAILABLE/MAX:
        `_do_god_raid`, `_check_group_caps`, `!caps`, `!uncapped`, `!who`, and the table images.
      • TODO: parse /crew_capstatus (used/max + per-cap timestamps) for accurate "capped until <date+time>".
      • Up to 10 raid attempts per prime per cycle, or stop when the per-spawn target is met.
      • Per-group pots: none / class / raid (raid includes class), cast before that group raids.
      • Runs alongside autoboss (boss raids don't use caps).
      • Per-cycle breakdown: what it raided + caps before→after + group used, OR why not
        ("not spawned", "target already met", "all groups capped until <date+time>, suspended until restored").
      • Rec auto-lower on a confirmed cap: set rec = min(current, winning group's avg power/ele/chaos). Lower only.
- [ ] **Envoy cycle feature** `🔴 Hard` — **build at Aug 6 rollover (~17:30 UTC)**
      Cycle ends 2026-08-06 17:30 UTC (`var countdown = <unix>` on envoy_overview, currently 1786037400).
      Three sub-features, all share the cycle lifecycle:
      1. **Cycle alerts** (timestamp-based): "1 day left", "new cycle started", configurable thresholds +
         channel. Buildable anytime (doesn't need rollover).
      2. **Auto-fetch** (pool-increment based): watch `/envoy_loot/<pool>/<envoy>` history (shared pool,
         increments by 1, currently 50). If latest_pool > last_fetched_pool → fetch all 8 via existing
         `_post_envoy_drops`. Persist `{cycle_end_ts, last_fetched_pool}` to disk → restart-proof +
         self-backfilling (fixes original downtime-miss bug — old trigger watched spawned→despawn but
         envoys never despawn = dead code).
      3. **Live leaderboards**: 1 embed per envoy (8), edited every 24h, header shows countdown. Data
         CONFIRMED on `envoy?target=<id>` (Rank|Character|Level|Attacks). At rollover: replace with loot
         winners, then fresh leaderboards.
      • **Build FIRST (before Aug 6): auto-dump on rollover** — extend `!envoy-debug` to fire automatically
        when the timestamp jumps, save before/after HTML to Pi (Liam is at work when it rolls). Fallback:
        cron dump every ~2h around Aug 6.
      • **Loot-completion polling** (see Quick Wins / best-practice): use the page's "Status: Loot completed"
        flag instead of a fixed wait, so large pools (envoys, event bosses) finish before parsing.
      • OPEN (check AT rollover): does pool 51 appear in history the moment it's fetchable, or early while
        still rolling? + validate leaderboard parse selector against live data.
      • `!envoy-debug` (owner-only, hidden, misc_commands.py) STAYS DEPLOYED for the rollover.

- [ ] **RGA stats + export commands** `🟡 Medium` (part of Session ID expansion)
      Bot scrapes ONE RGA via its stored SSID (same as Freak's Bloop; no dependency on his tool).
      • `!rga stats <rga>` — totals + averages as a table image (like !status/!pcaps). Stats: exp, power,
        ele, chaos, attack, hp, max_rage, wilderness, faction/loyalty, level, resist, RPT, slayer.
      • `!rga export <rga>` — full per-char export as a DOWNLOADABLE .html file attachment (NOT hosted;
        Bloop also downloads). Tabs: Overview(sortable)/Augments/Backpack; gear w/ item IDs + hover popups
        (item_rollover.php loads browser-side). Reference: Bloop v69 export format Liam shared.
      • Always ONE RGA per command (selected in the command); multi-session = the library to pick from,
        NEVER aggregates across RGAs. Stats table buildable early on current single-SSID.
      • Foundation exists: scraper.py Character model + parse_character_profile/parse_character_stats_profile;
        bot already sends HTML files (raidattack.html).

- [ ] **Session ID expansion** `🟡 Medium`
  Multi-RGA raiding, cross-RGA skill casting, daily quests (artifact hand-ins for God Slayer), badge
  automation. Foundation (SSID storage + expiry detection) is done.
  Build order: (1) **multi-session handling** [foundational — store/route multiple SSIDs, select one per
  command, never aggregate], (2) **RGA stats table** [see above], (3) **RGA HTML export** [see above],
  (4) **skill training to a user-defined order** [train skills by priority until points run out; casting
  exists, training/spending-points is new; needs a config format for the order], (5) **PvP Brawl
  automation** [BIG, own mini-project — bi-weekly PvP comp; read who's signed up + a Liam-defined group;
  HARD PART = detect if a specific skill is ACTIVE on a target; then hit accounts based on detection].
  Note: SSID auto-refresh is explicitly NOT wanted — account holder mints SSIDs, bot only consumes them.
- [ ] **Key quests & dungeon automation** `🔴 Hard`
  Automate key quests and dungeon runs end-to-end:
  • Multi-step quest/dungeon navigation, including "talk to mob" steps — these need per-account session
    IDs to POST the talk/advance actions (ties into the Session ID expansion item above).
  • Max "supplies" as part of a run, and set the supply allocation to max HP.
  Depends on: per-account session-ID handling + the unified `get_as`/`post_as` request convention.
- [ ] **Auto-raid on god spawn** `🟡 Medium`
  Auto-hit prime gods with a configured group on spawn: `!auto-raid set zikkir lod1 wins=3`.
- [ ] **Trustee auto-scan** `🟡 Medium`
  Scheduled weekly re-scan; stale rage/level affects former selection and group stats.
- [ ] **`!cast-check <group>`** `🟡 Medium`
  Show active/cooldown/missing skills across a group as an image.
- [ ] **`!schedule`** `🔴 Hard`
  Schedule a raid at a time: `!schedule lod1 zikkir 06:00`.
- [ ] **`!crew-stats <crew>`** `🟡 Medium`
  Live crew totals for power/ele/chaos.
- [ ] **World Map Crawler** `🔴 Hard`
  Walk all accessible rooms, discover mobs/raids, update local map/mob data.

## 🔻 Lowest Priority

- [ ] **Crew Vault deposit/award** `🔴 Hard`
  Endpoint confirmed: `POST ajax/backpack_action.php` (action=cv, itemids[], answer, qty). Blocked on
  account ownership + storing per-account security answers until everything else is rock solid.

## ✅ Completed

### Sessions 2026-07-22 → 08-02 (Pi migration + fixes)
- [x] **`!boss raid` recognises event bosses** — the raid command matched names only against the
      hardcoded BOSS_PRIORITY list, so event bosses (e.g. Solkaar) weren't raidable even though !boss dmg
      and !boss list saw them (those read the live spawn page). Fixed: _get_spawned_bosses now includes
      spawned bosses not in BOSS_PRIORITY (appended after priority ones), and the name lookup matches live
      spawned bosses first, then falls back to BOSS_PRIORITY.
- [x] **Raspberry Pi 4 migration COMPLETE** — bot + FastAPI supervisor now run 24/7 on a Pi 4 (8GB),
      survive reboots. Supervisor owns the bot (Route 2) as systemd service `deathbot-supervisor`; standalone
      `deathbot` service deleted + MASKED (caused a two-bots session flood on 07-29 via `restart deathbot`).
      Tailscale for remote access; HTTPS via `tailscale serve` → dashboard installable as a phone PWA.
      Dashboard temp reading verified accurate. Restart is ALWAYS `sudo systemctl restart deathbot-supervisor`
      (or the dashboard button) — never `deathbot`.
- [x] **Automatic database backups** — `~/backup_database.sh` on Pi tars `database/` weekly (Sun 3am cron),
      keeps last 7, aborts on empty source. Off-device copies pulled to PC manually on demand.
- [x] **State-wipe bug fixed** (god_monitor.py) — gods/envoys/bosses monitors rebuilt state each poll and
      blindly overwrote; an empty parse (page-load failure) wiped the baseline, silently losing spawn/death
      transitions (incl. the missed envoy drops). Added `if not <list>: return` guards to all three handlers.
      NOTE: bot logs go to the supervisor/dashboard buffer, NOT journald.
- [x] **God room feature — three-tier lookup** — `_resolve_god_room`: manual `!god set <name> room <id>`
      (wins) → hardcoded GOD_ROOMS table → crawl_mobs.json matched by god_id==crawl mob id. `!god info` shows
      the room + its source. Fixes event primes not in the table (crawl auto-picks up once crawled; manual
      override for when accounts can't reach the room yet). TESTED live on Zhulian Friar (god_id 3146).
- [x] **`!pcaps` used/max fix** — was showing REMAINING (`cur/max`); now shows USED (`max-cur/max`).
- [x] **`!pcaps` orange tier** — caps colour: green (<80% used) / ORANGE (80%+ used, nearly capped) / red
      (fully capped) / grey (error). Scales correctly across max caps 10/11/12/13.
- [x] **`!boss list` SPAWNED fix** — status was computed purely from kill-window math, ignoring live spawned
      state (showed "Window passed"/"NEAR" for alive bosses). Now checks `boss.spawned` first → shows SPAWNED
      (green) with NO window timing; window math only runs when the boss is dead.
- [x] **Boss raid potion spam quieted** — the `🧪 Potions` message now posts ONCE on the initial cast
      ("Potions cast:"); the 5-min background recast loop runs silently (`silent=True`). Skills/raid-start/
      boss-defeated/errors/LS-recast messages unchanged.
- [x] **`!envoy-debug`** (owner-only, hidden, misc_commands.py) — dumps raw envoy pages to `~/*.html` for the
      envoy-feature investigation. STAYS DEPLOYED until the envoy feature is built at the Aug 6 rollover.

### Session 2026-06-28
- [x] **`!primewatcher` Phase 2 — engine CORE built**: xx:40 scheduler · per-spawn cap target read off
      the leaderboard (resume-aware) · even-spread daily group rotation · intact groups · A→B fallback
      when a group is out of caps · 10-attempt/target retry · per-group skills (class/raid) · per-cycle
      breakdown · `!pw dryrun <name>` (simulate, no raids) · `!pw channel` (where breakdowns post).
      DEFERRED refinements: (1) rec auto-lowering on confirmed cap, (2) exact "capped until <date+time>"
      from the God Cap hover timestamps (currently reports "out of caps" without the date).
- [x] Fixed `!rm`/`!rg` cap inversion — God Cap now read as AVAILABLE/MAX (was treating first num as used)
- [x] Primewatcher per-group setting renamed pots → **skills** (none/class/raid)
- [x] `!primewatcher` (`!pw`) **Phase 1 — config** — multi-instance watcher setup: create/delete,
      add-group (intact, with none/class/raid pots), add-prime (caps per prime), set-crew, on/off,
      show, overview. Admin-only. Stored in primewatchers.json
- [x] `!god-export` — dump live recommended power/ele/chaos to a .txt on demand
- [x] `!god-list` — added Rec Chaos column + pagination (22/page) so it's readable
- [x] `!todo` is now a dropdown — shows each category with its item count, and a category picker
      (like `!up`) that displays the selected category's items privately (ephemeral)
- [x] Daily 9am summary now has a "Yesterday's Focused Drops" section (between Server Bosses and
      AutoBoss) — consolidates the previous day's focused-crew drops: items, Amulet Chest x N, Points x N.
      Focused-crew drops are recorded to focus_drops.json as god kills happen (last 14 days kept)
- [x] `!boss` group pilot — `!boss auto|raid|single|stop|status|group|records|pots|proceed|window`
      route to the classic commands (logic untouched); old names (`!boss-stop`, `!autoboss`…) still work
- [x] No-access users now always get the unauthorised GIF — including on unknown commands — with a
      plain-text fallback if the GIF can't send (e.g. missing embed perms)
- [x] Unauthorised GIF now rotates from a list of 5; authorised users get a "`!x` isn't a command,
      try !help" nudge on typos instead of silence
- [x] `!up` rebuilt — text list of spawned gods + dropdown; picking one shows live kills, time,
      max members and rec Pwr/Ele/Chaos privately (ephemeral)
- [x] Rec stats — added `rec_chaos`; `!god-set` accepts it; shown in `!god` and the `!up` panel
- [x] `!god-rec-import` — bulk-set rec power/ele/chaos from a pasted block (previews, then `apply`)
- [x] Auth simplified — `!auth -m @user` (member) / `!auth -a @user` (admin); fixed missing decorator
- [x] `!whoami` (public) shows your access level; `!guide` (public) is a new-user walkthrough; help
      and guide are public so newcomers can orient before being granted access
- [x] Skill/pot ordering: ALL skills (incl. Prestige) cast blocking, raids start, pots cast in background
- [x] 30-min MD guard on pots — initial cast and recasts skip when <30 min MD remains for the group
- [x] `!bossraid <group> [count] [boss]` — counted or unlimited crew-boss raids, NO skills/pots, flexible
      arg order, progress every 30 raids, respects `!boss-stop`, refuses if a session is running
- [x] `!rm` / `!rg` live spawn check — fetches the god's page at command time (fixed Felroc false "not spawned")
- [x] Boss Raids Summary restyled — brand teal accent, summary line, `DeathBot · LoD` footer
- [x] Summary gained Avg/Raid (computed) and Best Raid; Best Raid resets per MD cycle (final stop = whole run)
- [x] Drop summary count fixed — sums real quantities (e.g. 9 not 6); all duplicates counted
- [x] Drop summary aggregation — points combined into one figure, amulets combined into one line
- [x] Help overhaul — complete categories incl. Boss Raiding + Alerts; `!help`/`!h` aliases;
      `!help <command>` shows usage for ANY command; friendlier missing/bad-argument errors with usage

### Earlier
- [x] Raid timing optimised — form+join immediately after attack result, 60s game limit before launch
- [x] LS recast + pot casting moved to background loops (pots stop when MD <30m)
- [x] Skill cast sem=10 — faster cast cycle; MD verify pass removed
- [x] Autoboss target boss, mixed-MD `!boss-proceed` prompt, minimum raid threshold, live countdowns
- [x] `!check-md` active/cooldown/ready via skills_info.php + 384min threshold
- [x] Auth system command coverage fixed; three-tier Owner/Admin/Member auth
- [x] Prime god drops (SSE parsing + retry); boss drops; daily summary; boss window entry alerts
- [x] Full code audit (Jun 18) — all files parse OK, dead code removed
