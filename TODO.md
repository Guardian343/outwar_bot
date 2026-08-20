# DeathBot To-Do List
Last updated: 2026-08-13

Legend: Easy / Medium / Hard / Test-verify

---

## 🎉 DUAL-SERVER IS LIVE (Sigil + Torax) — the big picture

The Torax auth saga is SOLVED. Key facts, all proven:
- ONE session, forced to Sigil on every login, reads BOTH servers via per-request
  `serverid=` + the correct per-server suid (Freak's method). NO second session, NO
  cookie-jar juggling, NO stateful `ac_serverid` switching needed for reads.
- Per-server suids: Sigil 1157932 / Torax 933209 (same rg_sess_id, different suid).
- The account's server state is singular on Outwar's side. The bot forces itself to
  Sigil on every login (switch to Sigil + SELECT the Sigil account — BOTH steps needed),
  so it can never get stuck on Torax again. 33+ hours stable.
- `ac_serverid` is banned from all live code paths (it's the account-level switch that
  caused the flip-loop). Only the accounts.php roster fetch uses it, and safely (isolated
  cookieless session with a trustee's ssid — never the bot's session).
- Reads need the account to be in a CREW on that server — a crewless account returns a
  page shell with zero data (this was the "empty Torax primegods" red herring). Torax bot
  account is now crewed.
- Full detail + recovery procedure in DUAL_SERVER_PLAN.md.

What's DONE and live: dual-server god monitoring (alerts to torax-gods etc.), startup
snapshots for both servers, and the both-servers trustee scan.
What's next: apply the SAME proven pattern to the other monitors (bosses, envoys,
summary) and to action commands (guard-start) — see Critical below.

---

## 🔴 Critical / Next Up (dual-server rollout — do these next, one at a time)

- [ ] 🩺 SELF-HEAL / WATCHDOG suite (holiday-critical — bot must survive ~2 weeks unattended)  [Hard]
  Model A: supervisor self-heals, external watchdog only alerts. Layers:
    L0 bot self-heals (throttle+early-bail+is_healthy — DONE) · L1 crash-monitor (EXISTS:
    process_manager.monitor_forever — restarts on process DEATH) · L2 health-monitor (NEW —
    catches ALIVE-but-WEDGED, the gap L1 misses, which is what bit us twice) · L3 give-up alert.
  Heartbeat: bot writes heartbeat(UTC)+healthy(is_healthy()) to status.json each cycle
    (bot already writes status.json); supervisor reads every ~60s. Two wedged conditions →
    15-min SUSTAINED clock (resets the instant bot reports healthy / heartbeat updates, so L0
    self-heal wins first): (a) stale heartbeat >15min, (b) healthy==False for 15min. Guard
    (CONSERVATIVE): max 3 restarts/30min then GIVE UP + loud alert. Grace ~3-5min after a
    restart before re-checking.
  Alerting tiers: 🟢 Discord log (routine restart) · 🟡 Discord+soft ntfy (2nd/3rd restart) ·
    🔴 loud ntfy+Discord (gave up / bot down / supervisor unreachable). ntfy = free push (bot/
    supervisor sends HTTP POST to an obscure topic; subscribe in the ntfy app). Discord = durable
    log you scroll back through. Self-heal is PRIMARY (Liam may be phoneless 12h on holiday); the
    alert is just so he's informed, not so he has to act.
  STAGED build (this auto-restarts the bot unattended — trust before arm):
    Stage 1: bot heartbeat in status.json (harmless — nothing reads it yet) ← NEXT
    Stage 2: supervisor health-monitor DETECT-ONLY (logs "would have restarted") 1-2 days
    Stage 3: ARM restart + 3-in-30 guard + grace
    Stage 4: alerting tiers + configure the EXISTING watchdog
  NB: supervisor repo (github.com/Guardian343/deathbot_supervisor) ALREADY has watchdog.py — an
    independent ntfy+Discord alerter with grace-fails + alert-once-on-down/recovery. But it ONLY
    ALERTS (no restart), has a CHANGEME ntfy topic + Windows/pi placeholder paths (yours = liam@
    /home/liam). Needs: real topic + Discord webhook, path fix, deploy. The RESTART logic goes in
    the SUPERVISOR (Model A), NOT the watchdog. process_manager already has start/stop/restart.
  Open Qs at build: does the bot's most frequent cycle tick often enough to beat a 15-min
    heartbeat? where is is_healthy() reachable from the status-writing code?

- [ ] 🔀 Boss death/respawn FLIP-FLOP — dual-server state bleed  [Medium] (fixed by 3c)
  Symptom (2026-08-13): Discord showed Maekrix "defeated!" then "spawned!" when it did NOT die
  on Sigil. Root cause (Liam's diagnosis): Maekrix was ALIVE on Sigil but DEAD on Torax; the
  boss monitor checks both servers but tracks boss state in a SHARED (not per-server) place, so
  it read Torax's dead-state → "defeated", then Sigil's alive-state next cycle → "spawned". Same
  bug CLASS as the name collision — dual-server data not scoped per-server. Also confused the
  raid into a "first-cycle produced nothing — retrying" loop (which DID self-recover). FIX: part
  of 3c — make boss poll + state PER-SERVER so Sigil's and Torax's same-named boss never
  flip-flop. WORKAROUND until then: run Sigil-only (!active-servers sigil) — sidesteps it entirely.

- [ ] 🧭 Phase 4 — CHANNEL→SERVER resolution (the proper fix for the name-collision bug class)  [Hard]
  Liam's insight: commands run in per-server channels (sigil-* / torax-*), so the CHANNEL
  already tells you the server. Instead of resolvers defaulting to Sigil, derive the server
  from the invoking channel via the existing helper `server_from_channel(ctx.channel)` (in
  outwar/servers.py — keys off the channel-name prefix, falls back to Sigil). Thread that
  server_id through every account/name resolver. This makes per-server scoping AUTOMATIC and
  retires the whole "unfiltered get_trustees()" bug class by construction — a command operates
  on the server of the channel it was run in, never merging both. Caveat: commands in
  non-prefixed channels / DMs fall back to Sigil (fine if server-specific commands live in the
  server-prefixed channels). Do the 🔴 resolver bucket below this way.
  🔴 resolver bucket (account/name resolvers that still call get_trustees() unfiltered — same
  bug class as the pcaps collision): backpack_commands L86 _resolve_account,
  character_commands L585 _resolve_target, misc_commands L563 _resolve_trustees,
  raid_commands L2560 slayer_needs, admin_commands L471 autorank, L880/L1016
  optimise_all/optimum, L1227 scores. (Immediate server_id-filter fix already covers
  resolve_group → pcaps/rage/potions; these are the remaining ones.)
  🟡 judgment calls (maybe want cross-server, decide per-command): utility who (find a name on
  either server?), utility deathbot_status (totals?), god_monitor L1177 envoy-drop name-match
  (recognise both servers' winners?), admin remove_trustees/clear_trustees (⚠️ MODIFY the
  store — scope carefully). 🟢 leave as-is: __init__ caches, the scan itself (wants all),
  crawler (single char), debug commands.

- [ ] ⚔️ Make `!guard-start` dual-server — but TEST THE TORAX CAST PATH FIRST  [Hard]
  Currently Sigil-only: it calls `db.get_trustees()` (server 1 default) and casts via
  `post_as` on a hardcoded SIGIL_URL. So it guards 751 Sigil trustees, ignores the 535 Torax.
  ⚠️ This is an ACTION command (casts skills on live accounts), not a read — everything
  proven for Torax so far is read-only. Before looping both servers, TEST the Torax cast
  path on ONE Torax trustee: does `post_as` work for Torax, or does casting need the
  cookieless `sess_post` path (the write-equivalent of the `sess_get` that we had to use for
  Torax trustee enrichment — `get_as`/cookie path did NOT work for Torax reads)? Once the
  right cast method is confirmed: loop `db.get_trustees(server_id=...)` per active server, use
  the verified method + correct host per server. Deploy and watch the first dual-server cast
  cycle carefully. Do NOT bolt this on untested — it's actions on up to 535 accounts.

- [ ] 🐲 3c — Boss poll per server  [Hard]
  Wire the boss monitor to poll both active servers, same proven pattern as gods
  (`get_server("crew_bossspawns", server_id)` -> `parse_bosses` -> per-server state -> per-server
  channel via `_get_alert_channel("bosses", server_id)`). ⚠️ Verify the Torax boss page reads
  real data first (the crew-context lesson: confirm the bot's Torax crew sees bosses). Torax
  boss channels already exist (torax-bosses). Startup snapshot for bosses already loops
  servers (done with the god snapshot) — this is the LIVE poll.

- [ ] 📜 3d — Envoy poll per server  [Medium] (after the envoy feature itself is sound)
  `_process_envoy_changes` currently early-returns for non-Sigil. Once the core envoy feature
  is rebuilt (see Pending), extend it per-server. Torax envoy channels exist (torax-envoys).

- [ ] 📊 3e — Per-server daily summary  [Medium]
  The 9am summary is Sigil-only. Make it post a Torax summary to torax-chat too. Depends on
  3c/3d so there's Torax boss/envoy data to summarise.

- [ ] 📜 `parse_envoys` doesn't match envoy pages at all  [Hard] — surfaced 2026-08-10
  `parse_envoys` returns 0 on ALL envoy pages — its selectors are built for the PRIMEGODS
  layout, not envoy pages. The monitor's `envoys = parse_envoys(primegods_html)` was always
  empty -> "Envoy parse returned nothing" every poll. The envoy feature does NOT need
  parse_envoys — everything comes from the target pages (leaderboard/name/pool/countdown, all
  built + tested). ACTION: rebuild envoy spawn-state detection on the target-page parsers;
  treat old parse_envoys as dead code to remove or rewrite. (Ties into the Envoy feature and 3d.)

- [ ] Verify background pot task vs raid session isolation  [Hard]
  Historically a cookie-jar race (`_cast_boss_pots_bg` switched `ow_userid` while `_do_boss_raid`
  ran). The move to per-request `_as` calls should have fixed it, but not confirmed end-to-end
  in production. Action: during the next test run, confirm joins/launches never fire under
  the wrong account.

---

## 🧪 Test & Verify (confirm recent work before piling on more)

- [ ] Let dual-server god monitoring + trustee scan bake  [Test]
  Confirm over a good stretch (ideally overnight, like Sigil's 33h): Torax god alerts keep
  flowing to the right channels, Sigil stays rock-solid, no session errors. THEN move to 3c.
- [ ] Full production test of older boss-raid changes  [Test]
  Skill/pots ordering, 30-min pot guard, !bossraid, !rm/!rg live spawn check, restyled summary,
  per-cycle Best Raid, drop-count fix + aggregation, help system — all still need a real run.
- [ ] Turn off CAST_DEBUG  [Easy] — set `CAST_DEBUG = False` in `boss_raid_commands.py`
  once one clean cast cycle is confirmed.
- [ ] Confirm drop-summary aggregation  [Test] — amulets combined by COUNT, points summed by
  value×qty. Verify against a live spawn.

---

## 🏗️ Infrastructure (Pi / deployment)

- [ ] Auto-git deploy  [Medium] — HIGH PRIORITY (force multiplier)
  Pi auto-pulls + restarts on git push. Design agreed: poll GitHub every 5 min; never restart
  mid-activity (wait until idle — no PW cycle / boss raid / active casts; investigate a "busy"
  signal, maybe status.json); validate code loads before restart; keep old version + alert on
  failure; restart via `deathbot-supervisor` only. Removes the wrong-command class of mistakes.
- [ ] Uptime monitoring (Uptime Kuma)  [Easy] — watches bot + dashboard, phone alert on
  downtime. ~20 min setup, runs on the Pi.
- [ ] 🖥️ Server Dashboard overhaul — now needs a SERVER dimension  [Hard]
  (Supervisor repo: github.com/Guardian343/deathbot_supervisor — separate from the bot repo.)
  The dashboard is Sigil-only. Now that dual-server is live, the dashboard should show both
  servers' data (gods, bosses, trustee counts, alert channels, active-servers state). Decision
  from earlier: do the dashboard overhaul AFTER the dual-server monitors are in, so it's built
  once against the full server-aware data model rather than twice. This is the natural capstone
  of the dual-server work. (Small-patch exceptions possible before then, e.g. primewatcher
  display + settings clutter.)
  BUG to fix in the redesign: "Copy Visible" on the Logs tab works on MOBILE but silently fails
  to copy to clipboard on DESKTOP browser (PC) — nothing lands to paste. Likely a clipboard-API
  secure-context/permissions issue or a mobile-vs-desktop code path difference. Low priority but
  captured.
- [ ] Pi-hole network ad-blocker  [Easy] — PARKED (Liam's aside). Revisit once dual-server
  is at full potential + dashboard updated. Runs fine alongside the bot; watch for a port 80
  clash with the supervisor dashboard, give the Pi a static IP, set a secondary DNS
  on the router as fallback. Future extra services best in Docker for isolation.

---

## 🟡 Medium Priority (bot internals / raids)

- [ ] 👥 MULTI-CREW concurrent boss raiding  [Hard] — real limitation, needs per-crew state
  LIMITATION (confirmed 2026-08-19): the bot can only run ONE boss session at a time. Starting
  `!boss auto lod` sets a SINGLE cog-wide `self._running=True`; a second `!boss auto <crew2>` then
  hits `if self._running: return` → "session already running". To raid crew2 you must !boss-stop
  crew1 first — no good if crew1 is mid-boss (and Liam's lower crew lacks raid skills, so he wants
  them raiding a boss the main crew can't/independently).
  ROOT CAUSE: ALL raid state is SINGLE-INSTANCE on the cog (line ~128-133): self._running,
  self._stop_flag, self._status, self._sin_index, self._ls_cast_times — one of each. The whole
  boss-raid system assumes one crew raids at a time.
  ⚠️ Simply removing the guard would NOT work — two sessions would COLLIDE on the shared
  self._status, the session cookie (both switching ow_userid mid-request), and LS-cast tracking —
  the same class of cross-contamination as the dual-server name-collision bug. The guard is
  currently PROTECTING against that.
  PROPER FIX (same pattern as dual-server scoping): key ALL raid state PER-CREW — e.g.
  self._sessions[crew], self._status[crew], self._ls_cast_times[crew], self._stop_flags[crew] —
  and run each crew's _run_autoboss as its own task. Significant refactor (touches the whole raid
  path). Relates to the "First-raid flag is per-run/single-crew" note. Big build — scope carefully.
  Interim options if needed sooner: (a) a SECOND bot instance for the lower crew (own process/token),
  (b) accept one-crew-at-a-time for now.

- [ ] 🔍 raidboss/bossraid/autoboss HANDLING inconsistencies (found 2026-08-19 review)  [Medium]
  Compared how the three boss-raid commands HANDLE things (not what they do). autoboss (run-forever,
  casts skills) + bossraid/br (raid spawned boss N times, no cast) are both hardened; raidboss (one
  round, no cast) is the OUTLIER — oldest/simplest, missed the hardening:
    (1) ⭐ NO _running GUARD: autoboss + bossraid both `if self._running: return` (blocks duplicate
        sessions + makes !boss-stop meaningful). raidboss sets NEITHER _running nor _stop_flag, so it
        can START MID-AUTOBOSS and collide on the shared session/cookie state — a real risk given the
        session history. HIGHEST-VALUE fix, tiny + safe: add the _running guard to raidboss.
    (2) NO TIMEOUT/ERROR PROTECTION: autoboss wraps _do_boss_raid in asyncio.wait_for(180s) + 3x
        timeout handling + try/except; bossraid does too. raidboss calls _do_boss_raid RAW — a hang
        hangs the command forever, an exception errors out ungracefully.
    (3) BOSS MATCHING: bossraid matches live-spawned-first THEN priority list (catches event bosses
        like Solkaar not in BOSS_PRIORITY). raidboss matches BOSS_PRIORITY ONLY → can't raid a live
        event boss. autoboss has its own logic.
    (4) NO RAGE HANDLING: raidboss ignores under_minimum silently (no wait/resume). Minor (one-shot).
  Intentional diff (KEEP): autoboss survives-and-continues on repeated timeout (pause 5min, keep
  going); bossraid STOPS the session on 3x timeout. Correct for run-forever vs bounded.
  Suggested fix order: (1) _running guard [tiny, do first], then (2)+(3) bring raidboss up to
  bossraid's resilience, or consider whether raidboss is even still needed vs bossraid.

- [x] !rm / !rg output improvements  [DONE 2026-08-19] — !rm now sends an upfront
  "⚔️ Raiding X with Y (N chars)…" acknowledgement (was silent until the end-result embed, which
  left the user unsure it was working). !rg per-attempt lines now surface the REASON a raid didn't
  form (⚠️ capped / low rage / couldn't form) vs just showing HP% on a loss — matching what !rm's
  note already showed. (cogs/raid_commands.py)

- [ ] Raid count / join concurrency  [Medium] — DEFERRED until the boss test run is done.
  ~200 raids/264-min cycle. TWO levers now identified: (1) the ~206s skill-cast eats ~3.5min/cycle
  (see the "Skill/pot casting SLOW" item — likely the bigger win), (2) join concurrency: ~194
  raids ~= 82s/raid, raise the join semaphore (currently Semaphore(3-7)). Action: per-raid timing
  log (form/join/launch/fetch) to confirm join is the cost, then raise concurrency cautiously,
  watch for join rate-limiting (logs show occasional rate-limited bursts).
- [ ] Background task lifecycle — boss change  [Medium]
  When a boss dies + a new one spawns, the old cycle's `_cast_boss_pots_bg` / `_recast_ls_bg`
  may still run against the old `sorted_t` (only check `_stop_flag`). Fix: per-cycle cancel
  token the outer loop sets when moving to a new boss.
- [ ] Inline imports inside coroutines  [Medium] — move file-level (cosmetic; cached so
  not a perf issue).
- [ ] Audit silent `except: pass` blocks  [Medium] — ⚠️ NEEDS OWNER JUDGMENT. 131 blocks;
  most are intentional best-effort. Do WITH Liam: make the critical-path ones
  (settings/publish/save/state writes — like the publish_settings_meta bug) log; leave cosmetic
  ones. Per-block judgment; guided session.
- [ ] Raid history logging  [Medium] — save every raid attempt to JSON; add `!boss-history`.
- [ ] Staggered Last Stand recast messages  [Medium] — multiple LS recast messages per
  session instead of one batch (initial casts aren't simultaneous -> spread cast times -> 5-min
  check quantises into buckets). Functional but noisy + real timer drift. Fix: quiet the message,
  OR batch accounts due before next check, OR tighten interval.
- [x] First-raid-completed / MD-recharge flag  [Medium] — FIXED 2026-08-19. first_raid_done now
  resets at the start of each genuine new MD cycle (the "full MD recheck + skill recast" path,
  AFTER the "MD still active" fast-path continue — so a boss dying mid-cycle does NOT re-flag,
  only a true MD expiry/recast does). Each recharged cycle now announces its first raid damage.
  NB: for MULTI-CREW the latch is still per-run/single-crew — revisit when multi-crew boss lands.

- [ ] ⚡ Skill/pot casting SLOW — 206s  [Hard] — HANDLE WITH CARE (rate-limit history!)
  Casting skills on ~197 accounts takes ~206s. ⚠️ DELIBERATE STABILITY TRADEOFF. We tried faster
  before and hit CONSTANT RATE LIMITS: accounts rate-limited mid-skill → FAILED to skill → EXCLUDED
  from the raid → raided with fewer than 197, losing damage every raid that cycle. Backed off to
  slow-but-complete to protect account count (= protect damage).
  ✅ CODE-LEVEL DIAGNOSIS DONE (2026-08-19, _cast_all_skills in boss_raid_commands.py ~line 144):
    • Concurrency = Semaphore(10) (line ~173).
    • Each account casts MULTIPLE skills SEQUENTIALLY in a for-loop (for skill_id in all_skills).
    • ⭐ THE COST: `await asyncio.sleep(0.3)` after EVERY single skill cast (line ~257), INSIDE the
      semaphore hold. So each account holds its slot for (n_skills × (request + 0.3s)). ~4-5 skills
      × 0.3s = ~1.5s of pure SLEEP per account, ×~20 batches of 10 ≈ ~200s. That's the 206s.
  ⭐ Liam's maths refutes "it's just account count": Bloop ~60 accts @ ~6s → 180 accts should be
    ~18s. We're at 206s. So there's a genuine ~10x structural inefficiency ON TOP of account count,
    and it's the 0.3s-per-skill sleep dominating — NOT irreducible rate-limit safety.
  APPROACHES to investigate (needs live testing + the old rate-limit logs, do carefully):
    (a) the 0.3s sleep is PER-SKILL — could it be per-ACCOUNT instead (one sleep after an account's
        whole skill set, not after each skill)? That alone could cut it ~4-5x.
    (b) raise Semaphore cautiously IN COMBINATION with (a), watching for the 429s that broke it.
    (c) retry rate-limited accounts before excluding them (transient 429 shouldn't drop an account).
    (d) whatever changes, PRESERVE the invariant: ALL accounts must skill (don't reintroduce the
        exclude-on-rate-limit regression).
  HIGH VALUE (206s = ~3.5min of every MD cycle) — but the rate limit is real; change + live-test.

- [ ] 🌀 Last Stand recast DRIFT — REAL root cause (corrected 2026-08-19)  [Medium]
  Observed 2026-08-17: 197 accts skilled at start (23:24), but LS recasts dribbled over 3.5 HOURS:
  4 @23:33, 20+156 @02:08, 3 @02:18, 18 @03:03.
  ❌ FIRST THEORY WRONG: "the 206s initial-cast spread smears the recasts." Liam correctly refuted
  this with arithmetic — a 3.5-min cast spread can only cause a ~3.5-min recast spread, caught in
  1-2 adjacent 5-min checks, NOT 3.5 hours. Discarded.
  ✅ REAL CAUSES (from reading _recast_ls_bg, boss_raid_commands.py ~line 502):
    (1) MD-ACTIVE GATE pauses recasting: line ~590 `if not md_currently_active: continue` skips the
        WHOLE recast cycle when MD drops below 50% active. Due accounts pile up, then all fire at
        once when MD returns → likely the "156 accounts @02:08" mega-batch (released backlog).
    (2) PER-ACCOUNT COOLDOWN VARIANCE vs a HARDCODED 162 min (line ~547 ls_cooldown_mins=162, and
        LS_RECAST_SECS=163min line ~532). Liam: cooldowns differ by account upgrades. Accounts cross
        the fixed 163-min line at genuinely different real times → waves, not one batch.
    (3) ⭐ FAILED-INITIAL-CAST → DEFAULT 0 BUG: if an account's LS didn't cleanly cast at start
        (rate-limited/empty resp), it never gets a _ls_cast_times entry, so
        _ls_cast_times.get(suid, 0) = 0, and `now_ts - 0 >= 163min` is ALWAYS TRUE → it's seen as
        immediately due and recast on the very next 5-min check. THIS is the suspicious "4 accounts
        @23:33" (9 min in — impossible for a real 162-min cooldown). Direct tie-in to the
        rate-limit-during-skilling problem: failed casts default to 0 and fire early.
  FIXES to consider: (a) give failed-initial-cast accounts a real cast_time or exclude them from the
  due-check until genuinely cast (don't let 0 mean "due now"); (b) make the cooldown per-account not
  a hardcoded 162; (c) reconsider the MD-gate so backlogs don't dam-and-release; (d) batching the
  "due before next check" into fewer messages is cosmetic on top. NOTE: (a) is the clearest bug.

- [ ] 🐢 Foreground task every few raids (~13s)  [Medium] — throughput drain, SAFER win than
  skilling (not rate-limit-gated). From timing logs: most raids ~1.6-2s, but every 3-4 raids one
  takes ~13s — the full form→join→launch→stats cycle (join across ~196 accounts ~4-5s) running in
  the FOREGROUND, blocking the next raid. Investigate: can the heavy join/stats cycle be
  backgrounded or slimmed? Why does the full cycle recur every few raids (re-form trigger)?

- [ ] 🗣️ Rage-pause/resume messages (regression?)  [Easy-Medium] — Liam used to get "waiting for
  rage / resuming"; now unsure if raids silently pause on low rage. Bloop: "Waiting to form a raid
  against <boss> for `CREW` · Limited rage." then "Resuming raids against <boss> for `CREW`." The
  boss loop HAS a "⚠️ Low rage — waiting Xm" message — VERIFY it still fires in autoboss, add the
  matching "resuming" message when it rejoins.

- [ ] ✨ First Strike embed  [Easy] — make the first-raid announcement a clean EMBED like Bloop:
  title "First Strike" (linked to raidattack.php?raidid=...), body "CREW did N damage to <boss>
  with X characters", "SiN was active" line. Currently a plain 🚩 message.

- [ ] 📊 !boss dmg — add projected drops (Bloop parity)  [Medium] — CONFIRMED ours does NOT show
  projected drops. Bloop's adds a per-crew "~ N drops" projection from damage share. Add a drops
  column. Drop-projection maths unknown — work out how drops-per-crew derive from damage % (likely
  threshold bands) before building. Enhancement to existing !boss dmg, not a new command.

- [ ] 📊 !pcaps — add faction levels + total (Bloop parity)  [Easy] — CONFIRMED ours renders
  differently: Bloop's cap-status table shows per-faction levels and a TOTAL faction levels row at
  the bottom (e.g. "Alvar (78)", "Vordyn (46) Delruk (41) Alvar (25)"). Add faction-level totals to
  our !pcaps render. (Screenshot ref: Bloop !gcaps AOE / VALZEK / AGNAR.)

- [ ] 👤 !profile <outwar name>  [Medium] — BUILD FROM SCRATCH. Bloop's !profile shows a rich
  character card: level, class, crew, Experience, Power, Hit Points, Elemental Attack, Elemental
  Resist, Chaos Damage, Growth Yesterday, Wilderness Level, God Slayer Level, Faction, Parent, the
  profile picture, equipped items grid, and skill crests, plus an "Open in Browser" link. Liam
  notes "shouldn't be too difficult." Needs: fetch + parse the profile page for a given name, render
  a card (reuse the existing table/card image renderer). (Screenshot ref: Bloop !profile
  beastofthestorms.)

- [ ] Event boss handling  [Medium] (observe first, 3-monthly) — should auto-handle via the
  live page; watch (1) drop summary completeness vs the fixed 15s wait (ties to loot-completion
  polling), (2) cosmetic "next spawn window" for the dead-on-page week.

---

## 🧩 De-hardcoding (no hand-maintained lists in code)

Pattern: move list -> JSON in database/, seed ONCE from the old constant (zero day-one change),
manage at runtime via command, mark the constant "SEED ONLY".

- [ ] Migrate next hardcoded lists — CREW_ALIASES, GOD_ROOMS, GIVEAWAY_USERS, ITEMS,
  any other hand-maintained dict/set. One at a time, behaviour-identical on seed. CREW_ALIASES +
  GOD_ROOMS likely most useful first.

---

## ⚡ Quick Wins

- [ ] Potion stock check  [Easy] — before boss raids, scan group backpacks, alert if a
  required pot is missing on more than X accounts.
- [ ] `!leaderboard`  [Easy] — top characters by power/ele/chaos as an image table.
- [ ] Cap reset notifications  [Easy] — post when LoD accounts can raid Prime Gods again
  after daily cap reset.
- [ ] Loot-completion polling  [Medium] — replace the fixed `sleep(15)` before reading a
  boss stats page with polling the real "Status: Loot completed" flag. Self-adjusts to pool
  size; fixes event-boss slow-roll + makes envoy auto-fetch robust. One pattern, many wins.
- [ ] pcaps orange threshold — tune  [Easy] — currently orange at 80% used; maybe drop to
  70%, or switch to absolute "caps remaining <= N". One-line change.

---

## 💡 Streamlining (make the bot easy for new members)

- [ ] Command groups / subcommands  [Medium] — `!boss` group done (pilot). Roll out to
  `!cast ...`, `!god ...`, `!summary ...`, `!alert ...`, `!trustees ...`. Old names stay.
- [ ] Consolidated `!status` dashboard  [Easy] — merge status + boss-status + health +
  check-md into one at-a-glance view.
- [ ] More short aliases for hot commands  [Easy] — add a few more as members request.
- [ ] Pinned "Getting Started" guide  [Easy] — short channel-pinned walkthrough.
- [ ] Worked examples in `!help <command>`  [Easy] — a concrete example line per hot command.

---

## 🔵 Pending Features (bigger builds)

- [ ] `!primewatcher` Phase 2 — raiding engine  [Hard] — engine CORE built 2026-06-28.
  Rules unchanged (xx:40 trigger; per-spawn absolute cap target read off the leaderboard,
  resume-aware; intact !autorank groups; even-spread daily rotation; A->B fallback; 10 attempts/
  target; per-group skills none/class/raid; runs alongside autoboss; per-cycle breakdown; rec
  auto-lower on confirmed cap). Cap reads: HOME value is USED/MAX -> convert to AVAILABLE/MAX
  (all consumers do this). DEFERRED refinements: (1) rec auto-lowering on confirmed cap,
  (2) parse /crew_capstatus (used/max + per-cap timestamps) for accurate "capped until <date+time>".
- [ ] Envoy cycle feature  [Hard] — most sub-parts BUILT (cycle alerts, auto-fetch,
  leaderboards, auto-board refresh, rollover auto-dump, pool parser — all done). Envoy names
  (all distinct): MOB, PVP, RAID, ALVAR, DELRUK, VORDYN, PP(HARD), PP(EASY). STILL TO BUILD /
  VERIFY at the next rollover (~Aug 20, unix 1787247000): the LOOT-WINNER display for the ended
  cycle, ⚠️ with the PP Hard pagination constraint (winner list can exceed the 4096-char
  embed cap -> measure real length from the Aug 20 dump, then split/truncate/fields). Also the
  dead `parse_envoys` cleanup above. `!envoy-debug` stays deployed for the rollover.
- [ ] RGA stats + export commands  [Medium] — `!rga stats <rga>` (totals/averages as image
  table) + `!rga export <rga>` (downloadable .html, Bloop-style). Always ONE RGA per command,
  never aggregate across RGAs. Foundation exists (Character model + profile parsers; bot already
  sends HTML files).
- [ ] `!profile [account]` + RGA stats  [Medium] — must use an include-excluded path so
  excluded accounts stay viewable (exclusions only affect raids/optimise, not viewing).
- [ ] Session ID expansion  [Medium] — build order: (1) multi-session handling (store/route
  multiple SSIDs, select one per command, never aggregate), (2) RGA stats table, (3) RGA HTML
  export, (4) skill training to a user-defined order, (5) PvP Brawl automation (BIG; hard part =
  detect if a specific skill is ACTIVE on a target). SSID auto-refresh explicitly NOT wanted —
  the account holder mints SSIDs, the bot only consumes them.
- [ ] Key quests & dungeon automation  [Hard] — multi-step quest/dungeon navigation incl.
  "talk to mob" steps (needs per-account SSIDs to POST advances); max supplies + set allocation
  to max HP. Depends on the Session ID expansion + `get_as`/`post_as` convention.
- [ ] Auto-raid on god spawn  [Medium] — `!auto-raid set zikkir lod1 wins=3`.
- [ ] Trustee auto-scan (scheduled)  [Medium] — weekly re-scan (now both servers). Stale
  rage/level affects selection + group stats. (The manual both-servers scan is done.)
- [ ] `!cast-check <group>`  [Medium] — active/cooldown/missing skills across a group as image.
- [ ] `!schedule`  [Hard] — `!schedule lod1 zikkir 06:00`.
- [ ] `!crew-stats <crew>`  [Medium] — live crew totals for power/ele/chaos.
- [ ] World Map Crawler  [Hard] — walk accessible rooms, discover mobs/raids, update map/mob data.

---

## 🔻 Lowest Priority

- [ ] Crew Vault deposit/award  [Hard] — endpoint confirmed (`POST ajax/backpack_action.php`,
  action=cv). Blocked on account ownership + per-account security answers until everything else
  is rock solid.

---

## 💭 Side Projects (non-bot — Liam's other ideas)

- [ ] Family calendar + chore tracker  [Hard] — PARKED (idea captured). Wants: subscription-
  free; eventually an app/APK on Alexa Show, tablets, phones; must sync with "normal" calendars.
  Smart approach when picked up: build a family-friendly FRONT-END on top of an existing calendar
  (e.g. Google Calendar via its API) so sync/reminders/cross-device come free, delivered as a
  web app / PWA that runs on every screen (avoids building separate native apps per platform).
  Native APKs possible later. Decide the calendar approach first. Not started — for after the bot
  work has momentum to spare.

---

## ✅ Completed

### Session 2026-08-13 (dual-server LIVE)
- [x] Session resilience is_healthy() FIX — detects logged-out DIRECTLY via a _known_logged_out
      flag (set on any genuine logged-out page, cleared on successful login / authenticated
      response), NOT via the circuit breaker. Fixes the flaw where the throttle stopped the breaker
      tripping → is_healthy never saw unhealthy → the early-bail never fired. Now the pot-loop
      early-bails fire on the FIRST logged-out sign. (outwar/session.py — packaged, DEPLOY PENDING.)
- [x] Live 197-account boss raid CLEAN post-name-collision-fix: formed/joined/launched, pots on
      192 accounts NO flood, first raid 98.4M dmg 197/197. Confirms the name-collision fix resolved
      pcaps=17, false-low-rage AND the pot flood (one root cause, three symptoms). PW raiding again.
- [x] Self-heal / watchdog suite DESIGNED (on paper, agreed) — see Critical. Model A, 15-min
      sustained clock, 3-restarts/30min guard, staged detect-only-first rollout. Supervisor repo
      already has a watchdog.py alerter to build on.
- [x] Resilience proven in the wild: a session blip during a boss raid (21:31) triggered ONE
      re-login + immediate recovery (throttle working — not the old hour-long flood); and the
      boss-flip-flop raid confusion SELF-RECOVERED via the retry loop. Bot rode out a messy moment
      unattended and came back on its own.

- [x] Dual-server NAME-COLLISION bug fixed (resolve_group) — after scanning BOTH servers'
      trustees into one store, names existing on Sigil AND Torax got two entries; resolve_group
      called get_trustees() unfiltered → a 10-name group resolved to 17 accounts (the cross-server
      duplicates), and the Torax dupes (empty enrichment → dash rage) caused false "low rage — raid
      not formed" AND likely the potion-fan-out flood. Fix: resolve_group + boss_raid _resolve_group
      now pass server_id to get_trustees()/get_trustees_by_crew() (default Sigil). Diagnosed by Liam
      via !pcaps lod1 (17, 6 dash-rage) vs !groups lod1 (correct 10). Proper end-state = Phase 4
      channel→server resolution (see Critical). NOTE: crewless ≠ no rage — rage accrues each turn
      regardless of crew; the dash is failed Torax enrichment, not crewlessness.
- [x] Session resilience piece 1 — re-login throttle + circuit breaker (session.py): max 1 re-login
      / 60s; >5 in 10 min trips the breaker (pauses re-login 5 min, treats as network problem not
      logout). Confirmed live: one re-login instead of dozens. ⚠️ Piece 2 (early-bail via is_healthy)
      built but INEFFECTIVE — throttle stops re-logins → breaker never trips → is_healthy never sees
      unhealthy → bail never fires. is_healthy() must detect logged-out DIRECTLY (next session).

- [x] Torax auth saga SOLVED — forced-Sigil-on-login (switch server + SELECT account, both
      steps); all `ac_serverid` removed from live code paths; per-server suids (Sigil 1157932 /
      Torax 933209). Bot self-corrects to Sigil every login -> can't get stuck on Torax. 33h stable.
- [x] Confirmed Freak's method works — one session reads BOTH servers via per-request
      `serverid=` + correct suid (cookieless for non-Sigil). No 2nd session / cookie-jar juggling
      needed. Verified: Sigil + Torax primegods both parse to the full 51 gods with correct spawn
      states. (The old "build a 2nd session" plan is SUPERSEDED — see DUAL_SERVER_PLAN.md.)
- [x] Root-caused the "empty Torax" red herring — a crewless account returns a data-less page
      shell; the Torax bot account just wasn't in a crew. Crewed -> full data.
- [x] Dual-server GOD monitoring live — per-server poll/state/cache/channel; Torax alerts land
      in torax-gods. Enabled via `!active-servers sigil torax`. First Torax cycle seeds baseline
      silently (empty-state guard) — no spam.
- [x] Startup snapshots loop both servers — "Currently Spawned" gods + bosses posted per active
      server to their own channels on boot.
- [x] Both-servers trustee scan — `!scan-trustees` (no arg) scans ALL active servers, enriches
      each with the correct per-server method (Sigil = cookie path; Torax = cookieless `sess_get`,
      since `get_as`/cookie did NOT work for Torax), saves per-server, posts a combined summary.
      Live result: 751 Sigil + 535 Torax = 1286 saved. `!scan-trustees sigil|torax|all` also work.
- [x] SSID expiry poll hardened — reverted roster fetch to the correct `ac_serverid` form
      (accounts.php genuinely needs it; isolated trustee-ssid session, never flips the bot) after a
      bad `serverid=` change falsely wiped stored SSIDs. Added a two-strike guard (2 consecutive
      empty rosters required before deleting) so a single failed read can't wipe entries again.
- [x] god_monitor `_get_alert_channel(alert_type, server_id=None)` — fixed the signature so god
      spawn alerts stop crashing (was called with server_id it didn't accept -> silent TypeError ->
      no god alerts). Restored god alerting.
- [x] Removed dangerous `!torax-auth-probe` — its `ac_serverid=2` on the bot's own session was a
      prime cause of the account flipping to Torax.
- [x] Trustee scan de-landmined — switched from `ac_serverid=` to the safe `get_server` method.

### Session 2026-08-10/11 (dual-server foundation + fixes)
- [x] Per-server `!exclude` (raids + optimise only) — per-server storage, live (no restart);
      excluded ONLY from raids (prime + boss) + optimise/scores, NOT guard-start/top/stats/caps.
- [x] database.py sync fix — re-added per-server alert channels, trustee server-tagging,
      per-server exclusions that the pushed cogs expected.
- [x] Dual-server Phase 1 foundation — `outwar/servers.py` single source of truth; server_id
      threaded through session request methods; ssid_store/ssid_commands unified. All defaults Sigil.
- [x] Dashboard envoy pool display fixed — wrote the missing `publish_settings_meta`; real pool
      + loot status now published.
- [x] Envoy drop-count fix applied to the envoy summary (true drop_count, not len(items)).
- [x] Centralised MD constants (MD_TOTAL_CYCLE_MINS 648 / MD_ACTIVE_MINS 264 / MD_COOLDOWN_MINS 384).
- [x] top-all exclusion list moved to `database/top_exclusions.json` (+ `!top-exclude` command).
- [x] `!god set <name> room 0` = clear the manual room override.

### Session 2026-08-02 (MD cycle fix)
- [x] MD cycle ended early (wasted MD) — FIXED — cycle now runs until the LAST account's MD
      expires (was a 50% majority break); display uses max(); added a >=15-min drift channel alert.

### Sessions 2026-07-22 -> 08-02 (Pi migration + fixes)
- [x] `!boss raid` recognises event bosses — live spawned bosses not in BOSS_PRIORITY now raidable.
- [x] Raspberry Pi 4 migration COMPLETE — bot + FastAPI supervisor run 24/7 (systemd
      `deathbot-supervisor`); standalone `deathbot` service deleted + MASKED (caused the 07-29
      two-bots flood). Tailscale + HTTPS PWA dashboard. Restart is ALWAYS `deathbot-supervisor`.
- [x] Automatic database backups — weekly tar of database/, keeps last 7.
- [x] State-wipe bug fixed — `if not <list>: return` guards on gods/envoys/bosses handlers so an
      empty parse can't wipe the baseline. (Bot logs go to the supervisor buffer, NOT journald.)
- [x] God room three-tier lookup — manual override -> GOD_ROOMS -> crawl_mobs.
- [x] `!pcaps` used/max fix + orange tier; `!boss list` SPAWNED fix; boss-raid pot spam quieted.

### Session 2026-06-28
- [x] `!primewatcher` Phase 2 engine CORE built (scheduler, per-spawn cap target, rotation,
      fallback, retries, per-group skills, breakdown, dryrun, channel).
- [x] `!rm`/`!rg` cap inversion fixed; PW per-group pots->skills rename; PW Phase 1 config.
- [x] `!god-export`, `!god-list` chaos column + pagination, `!todo` dropdown, daily summary
      "Yesterday's Focused Drops", `!boss` group pilot, unauthorised GIF handling, `!up` rebuild,
      rec_chaos, `!god-rec-import`, auth simplification, `!whoami`/`!guide`, skill/pot ordering,
      30-min pot guard, `!bossraid`, live spawn checks, restyled summary, drop-count/aggregation, help overhaul.

### Earlier
- [x] Raid timing optimised; LS recast + pots to background loops; skill cast sem=10; autoboss
      target/mixed-MD/threshold/countdowns; `!check-md`; three-tier auth; prime/boss drops + daily
      summary + boss window alerts; full code audit.
