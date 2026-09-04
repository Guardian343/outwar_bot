# AutoBoss Raid Speed — Investigation & Plan (parked until post-holiday)

## The goal
Get from **~205 raids/cycle → 250 raids** across the 264-minute MD active window.
(Theoretical ceiling is ~264: one raid per 60s game-enforced launch limit.)

- 264 min = 15,840s
- 250 raids → budget **63.4s/raid** (60s floor + ~3s overhead — tight but doable)
- 205 raids → actual **~77s/raid** → **~17s of overhead per raid** that isn't overlapping the 60s wait

## The observed symptom (Liam, empirical)
Bot does **2–4 raids in quick succession, then stalls ~2 minutes** (timing-wise, not a literal
120s), then resumes. A periodic burst-then-stall pattern — NOT a uniform 17s tax per raid.
This points to **something firing periodically that blocks/starves the raid loop**, not
constant per-raid drag.

---

## What the code actually does (traced, with line numbers in cogs/boss_raid_commands.py)

### The 60s floor is real and unavoidable
`_do_boss_raid` line 1022–1028: `remainder = max(0, 60 - elapsed)` then `sleep(remainder)`.
Game enforces 60s between launches. Everything else must fit INSIDE that wait to not add time.

### Per-raid sequence (inside `_do_boss_raid`, all before the 60s wait → should overlap)
1. Fetch `crew_bossspawns` (rage_to_form) — 1 req
2. Rage-check top 10 accounts to find former — 10 concurrent reqs (sem=10)
3. Former live rage (cost measure) — 1 req
4. Form raid (`formraid.php`) — 1 req
5. Fetch forming page, parse raid_url/raidid — 1 req
6. Join all accounts (`joinraid`) — N concurrent reqs (sem=`boss_join_concurrency`, default 10)
7. `sleep(1)` join settle (line 1020)
8. Wait `60 - elapsed` (line 1026)
9. Launch (`joinraid.php?launchraid=yes`) — 1 req (get_as, 25-attempt internal retry)
10. Stats: backgrounded if `background_stats=True` (line 1056) — non-blocking after 1st raid

### The inner raid loop (steady state, line ~1208–1362)
Per iteration: **spawn-check** (`_get_spawned_bosses`, line 1226) → boss-alive check →
`_do_boss_raid` → count raid, drain background stats, `notify.send` embeds.

### Two background tasks — the prime suspects for the stall
- **LS recast** (`_recast_ls_bg`): `while: sleep(300)` — **every 5 min** (line 608).
  LS lasts ~162 min (`LS_RECAST_SECS = 163*60`, line 572).
- **Pot recast** (`_cast_boss_pots_bg`): `sleep(300)` — **every 5 min** (line 687).
  Pots last 66+ min.

Both use `Semaphore(10)` + `post_as` (designed non-blocking). BUT they share the same
`host_connection_limit` (default 10) connection pool as the raids.

---

## Leading theory (UNCONFIRMED — confirm before building)
**Connection-pool contention.** Every 5 min, the LS + pot recast tasks each fire up to 10
concurrent requests. They compete with active raids for the same ~10-connection host pool.
Raids get starved of connections until the recast traffic drains → the ~2-min front-end stall
Liam observes. The 5-min cadence is wildly over-frequent: checking every 5 min for buffs that
last 66–162 min means ~11–13 redundant flood events per buff lifetime.

**Confidence:** medium. The over-frequency is definitely real and wasteful. Whether it's the
*whole* stall story (vs launch-retry latency, or `notify.send` Discord rate-limiting between
raids) is NOT proven. Other candidate culprits:
- Launch `get_as` slow-retrying (line 1030, 25 attempts × 60s) on some raids
- Per-raid `notify.send` embeds hitting Discord rate limits (adds latency AFTER launch,
  outside the 60s overlap)

---

## The plan (ranked, post-holiday)

### STEP 0 — CONFIRM FIRST (do this before building anything)
Watch a real run. Does the ~2-min stall line up with a ~5-min cadence? If stalls hit roughly
every 5 min, the recast-pool theory is confirmed. If they're irregular or tied to Discord
posts, look at `notify.send`/launch-retry instead. The existing `[TIMING]` log lines
(spawned-check, join, _do_boss_raid returned, stats collection) have the data — but they're
`logger.info` and currently NOT surfacing (journald showed nothing). To see them: bump the
bot log level to INFO, OR add the timing to a **hidden/admin-only** command (NOT member-facing).

### STEP 1 — Fix the over-frequent recast checks (the one clean, safe lever)
Only if STEP 0 confirms. Tie recast check frequency to actual buff duration + a pre-expiry
buffer, instead of flat 5 min:
- Pot check: cadence ~ (shortest pot duration − buffer), e.g. every ~30–60 min not 5.
- LS check: cadence ~ (LS cooldown − buffer), e.g. every ~150 min not 5.
- Result: ~90% fewer background flood events → far less pool contention → fewer stalls.
- Safe: doesn't touch form/join/launch, doesn't touch spawn-polling, no member-facing toggle.

### STEP 2 — (if still short) investigate post-launch overhead
- `notify.send` per-raid embeds → consider batching / less frequent posting during steady state.
- Launch-retry latency → check if launches slow-retry; if so, why.

---

## Explicitly RULED OUT (decided during investigation — do NOT revisit)
- **Reduce spawn-check frequency / "wait for a death trigger":** NO. Outwar has no push event;
  polling IS the only way to detect death. AND in **priority mode** Liam needs to detect a
  higher-priority boss spawning ASAP to switch to it — so polling must stay every raid.
- **`!boss-pots on/off` toggle:** NO. Footgun — a member could toggle pots off for testing and
  forget, leaving real raids potless. Persistent member-facing state trap.
- **"Wasting rage raiding a dead boss":** not a real risk — you can't raid something that isn't
  there; the raid attempt just fails. So the spawn-check's job is purely (a) detect our boss
  died → stop, (b) detect a better boss spawned → switch. Both need frequent polling.

---

## Key facts to remember
- 60s launch floor is game-enforced and immovable — the ceiling is ~264 raids no matter what.
- The whole game is: keep ALL overhead inside that 60s wait. Anything running outside it
  (between launch and the next raid's wait-start) is additive and costs raids.
- Background recast tasks ARE async/non-blocking by design; the suspected issue is shared
  connection-pool starvation when they flood every 5 min, not the tasks themselves being sync.
- `host_connection_limit` default 10; `boss_join_concurrency` default 10 — both draw the same pool.
