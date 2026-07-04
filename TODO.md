# DeathBot To-Do List
Last updated: 2026-06-29

## 🔴 Critical (Fix Before Next Major Session)

- [ ] **Verify background pot task vs raid session isolation** `🔴 Hard`
  Historically a cookie-jar race: `_cast_boss_pots_bg` switched `ow_userid` per account while
  `_do_boss_raid` ran concurrently. The migration to per-request `_as` calls (post_as/get_as pass
  ow_userid per request, no shared cookie mutation) should have removed this, but it has NOT been
  confirmed end-to-end in production with pots now doing their initial cast in the background.
  **Action:** confirm during the upcoming test run that joins/launches never fire under the wrong account.

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

- [ ] **Centralise MD constants** `🟡 Medium`
  `648` (total MD cooldown) and `264` (MD active) live in `boss_raid_commands.py`. Add `MD_TOTAL_MINS`,
  `MD_ACTIVE_MINS`, `MD_THRESHOLD_MINS` to `constants.py` and import from there.

- [ ] **Raid history logging** `🟡 Medium`
  Save every raid attempt (boss, group, damage, char count, timestamp) to JSON. Add `!boss-history`.

## ⚡ Quick Wins

- [ ] **Potion stock check** `⚡ Easy`
  Before boss raids, scan group backpacks and alert if a required pot is missing on more than X accounts.
- [ ] **`!leaderboard`** `⚡ Easy`
  Top characters by power/ele/chaos as an image table.
- [ ] **Cap reset notifications** `⚡ Easy`
  Post to channel when LoD accounts are ready to raid Prime Gods again after the daily cap reset.

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
- [ ] **Session ID expansion** `🟡 Medium`
  Multi-RGA raiding, cross-RGA skill casting, daily quests (artifact hand-ins for God Slayer), badge
  automation. Foundation (SSID storage + expiry detection) is done.
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
