# Multi-Crew Autoboss — Build Plan

Goal: run `!boss auto` on 2–3+ crews at once, each holding a flat ~60s cadence.

**Key fact (confirmed by Liam):** the raid cooldown is **per-crew, not per-IP**. Other
bots run 5 crews at a steady 60s. So the ceiling is **inside our own bot**, not Sigil's
rate limits. Everything below is about removing our own bottlenecks and contention.

---

## Already done (before this plan)

- **Join concurrency configurable** — `boss_join_concurrency` in `settings.json`,
  default **10** (was a hard `Semaphore(3)`, ~64 join waves for 190 accounts). Tune live.
- **Post-launch stats backgrounded** — in the main autoboss loop the 5s settle +
  attack-page fetch + parse (~8s) no longer sits between launches. It runs as a
  background task and its damage/record drains into the session totals on the next pass.
- **Green-flag "first raid complete" message** — the first raid of a run executes its
  stats inline (immediate damage), then posts
  `🚩 <crew> is up and raiding — first raid complete: <dmg> · <chars>/<total>`.
  Every raid after that uses the fast backgrounded path. This message is the **release
  trigger** the ordered gate (step 4) depends on.

---

## Build order (do in this sequence — each step is safe to ship alone)

### 1. Per-crew state — `CrewSession`  *(the hard blocker — do first)*
All run-state currently lives on the cog instance and would be clobbered by a 2nd crew.
Move it onto a `CrewSession` object, one per crew; cog keeps `self._sessions = {crew_id: CrewSession}`
(keyed by **crew ID**, resolved from the alias — see step 2's keying rule, since names aren't unique).

Fields to move off `self` onto the session:
`_running`, `_stop_flag`, `_status`, `_pending_stats`, `_ls_cast_times`,
`_ls_bg_running`, `_sin_index`, and the per-run locals
(`session_raids/damage/best`, `launch_ts`, `first_raid_done`, `total_session_*`).

- Thread the session through `_run_autoboss`, `_do_boss_raid`, `_collect_raid_stats`,
  and the other two raid callers.
- `!boss auto crew2` → new `CrewSession` + its own `_run_autoboss` as an independent
  asyncio task. `!boss stop crew2` flips only that session's stop flag.
- **Acceptance:** one crew behaves exactly as today. No behaviour change — only *where
  state lives*. Prove this stable before anything else.

### 2. Per-crew data directory  *(shard ALL per-crew state — Liam's idea, and it's the right one)*
The **trustee scan** builds a per-crew data set: `database/crews/<crew>/` (or flat
`database/<crew>__*.json`), one set per crew, containing that crew's own:

- **`trustees.json`** — that crew's ~200 accounts. So `get_trustees_by_crew` reads a small
  file directly instead of re-parsing the whole 2,750-account roster and filtering.
- **`md_state.json`** — that crew's MD cast times (keyed by suid). **This is the fix for the
  O(n²) MD-write hotspot.** Every MD read/write during a crew's raid touches only its
  ~200-entry file, so file size is **bounded by crew size (fixed), not the total roster** —
  it stays fast no matter how many accounts or crews exist. Strictly better than batching
  the global file (which keeps writing one ever-growing file and still contends across crews).
- **`crew_stats.json`** — `total_session_damage`, `total_raids`, and
  `aggregate_avg_session_damage` = total_session_damage / total_raids (avg per raid; already
  how the summary computes it — store the two totals, derive on display); `best_session_damage`
  (biggest single raid this session); and **`best_by_boss`** = `{ "<boss_key>": {"best":<dmg>,
  "ts":<iso>} }`, the all-time single-raid record **per boss, per crew** — beating your own
  boss record replaces the number; existing "new record" wording kept.

**Why sharding by crew is the correct model:**
- File size is bounded to crew size (~200) forever — writes never degrade as the roster grows.
- Each crew touches only its own files, so **last-write-wins cannot happen** and no lock is
  needed — the same property that makes multi-crew safe.
- One consistent principle for trustees, MD, and stats.

**Crew moves self-heal — no complex migration needed.** MD cooldown is per-account
(game-side). If an account moves crew and the new crew's `md_state.json` doesn't have it, the
existing `_check_md_now` **network-poll fallback** re-establishes its cast time into the new
crew's file — one poll, no wasted cast. So the scan just re-partitions by current membership;
anything it misses heals on first use.

**Where the crew context comes from:** raids and slayer already run for a specific crew, so
`set_md_cast` / `get_md_state` gain a `crew` argument and route to that crew's file — the
caller always knows which crew it's raiding.

**File key (crew ID — NOT the name).** Crew names are **not unique**: several crews share a
base name and differ only by decorative symbols at the start/end. Slugifying a name deletes
exactly those symbols, so name-keyed folders would **collide and mix two crews' data**. The
canonical key is therefore the **game crew ID** (unique, symbol-proof): the per-crew directory
is `crews/<crew_id>/`.

- **`!crew-add <crewid> <alias>`** registers a crew: fetch `crew_home?id=<crewid>` for the
  crew's name + roster, store `{id, name, alias}`, and tag those member accounts with that
  `crew_id`. Then `!boss auto LoD` → alias → `crew_id` → `crews/<crew_id>/`.
- **Roles cleanly separated:** the **alias** is what you type, the **name** is for display,
  the **crew ID** is the stable key on disk. Two "Legion of Death" crews are just two IDs.
- **Trustee scan must capture `crew_id`** per account (not just the crew name) so partitioning
  keys on ID. The scraper already parses `crew_home?id=` links, so this is a small addition.
- The alias→crew_id map lives alongside the existing aliases plumbing; `normalize_crew` stays
  for display/name resolution, but **file routing uses the ID**.

**What stays global (small, infrequent, safe):** `settings.json` (config), `boss_deaths.json`,
alert-channel map. Read-mostly, not per-account, so no O(n²) and negligible contention.

- Any true cross-crew view (combined leaderboard) is produced by **reading all crew files**,
  never by writing a shared one.
- **RESOLVED:** the old global `boss_records` in `settings.json` is replaced by per-crew
  `best_by_boss`. No shared record write remains.

> **Timing note:** the per-crew `md_state` shard can be pulled forward as an early,
> standalone win (it's the thing that worsens as accounts grow) — it doesn't strictly need
> the full `CrewSession` refactor first, since the crew context is already available at every
> MD call site. Do it as its own tested change, not a blind merge.


### 3. Attempt-and-read launch model  *(what actually holds a flat 60s under load)*
Replace "compute remainder → sleep → launch" with "attempt launch → read the reply."

- Launch, then check the response for a **confirmed-launch marker**. If confirmed →
  record launch time and proceed. If not → short retry (the cooldown is still active).
- **Key off SUCCESS, not the reject text.** We do NOT need to capture the exact
  "you must wait" wording — which Liam can't reproduce by hand, since you can't
  form/join/relaunch inside 60s manually. Instead we detect the *success* response
  (which happens on **every** normal launch, so it's trivial to sample) and treat
  anything that isn't a confirmed launch as "still on cooldown → wait a beat and retry."
- Removes clock drift: under multi-crew load a computed sleep can wake late (loop busy
  with another crew), slipping the launch past 60s. Firing on the game's own gate
  eliminates that. Matters far more at 3 crews than at 1.
- **Capture plan (no manual timing needed):** temporarily log the raw launch-response
  text on every launch so we can see the exact success marker. If we also want the reject
  text for completeness, the bot can fire one deliberate *second* launch immediately after
  a success (it'll be rejected, no raid formed) purely to log the "must wait" response —
  a one-time probe, zero cadence impact. Neither needs Liam to hand-time anything.

### 4. Ordered startup gate + green-flag release  *(stampede fix)*
Problem: when the boss is despawned all crews sit idle; on respawn they **all dive in at
once** and phase-align, then collide every 60s forever.

- Each crew registers with an **index = the order Liam started it** (crew1=0, crew2=1…).
- A crew may begin its **first raid after an idle gap** (startup, or any respawn where it
  was idle) **only while holding the gate**.
- The gate admits the **lowest waiting index first**, one at a time.
- The holder runs its first raid; on the **green flag** (first-raid-complete) it
  **releases** to the next index. Crew 2 doesn't start forming until Crew 1 has landed a
  raid; Crew 3 waits on Crew 2.
- Steady-state raids **skip the gate**.
- **CRITICAL:** release in a `finally` with a timeout fallback. A failed/aborted first
  raid (error, boss vanished mid-form) must still release, or the line deadlocks.

This reproduces Liam's manual stagger (type command, wait for first raid, type next) —
but the bot awaits its own first-raid signal instead of a human eyeballing it.

### 5. Global concurrency ceiling  *(bounds aggregate load)*
Today: one shared `aiohttp.ClientSession` with the **default connector = 100 total
connections, unlimited per host**. Per-crew join semaphores don't know about each other,
so aggregate in-flight = Σ(crew concurrency) + background polls (LS/MD, god monitor,
primewatcher, stats). That climbs toward 100 as crews/concurrency grow. **Past ~100,
aiohttp silently queues** requests waiting for a free connection → latency → slipped
launches (no error, just slow).

- Fix: explicit `TCPConnector(limit=…)` (e.g. 150–200) **and/or** a global in-flight
  semaphore, so "max total load on Sigil" is **one dial we own**, not an implicit 100.
- Rule of thumb: keep Σ(crews × join concurrency) + background comfortably under the limit.

### 6. Serialised message sender  *(avoid Discord rate-limit collisions)*
All crews post into one `asyncio.Queue`; a single sender coroutine drains it and paces
sends/edits. Crews never block on a Discord call — drop the message and move on.
Simpler alternative: per-crew channels. The queue is the more robust version.

---

## Why 2 crews raiding in sync is safe (concurrency notes)

- Async I/O interleaves on one event loop — 2 crews joining at semaphore 10 = ~20
  requests in flight, well inside the 100-connection pool.
- **Concurrent I/O is free; concurrent CPU is not** (one loop, GIL). Only *synchronous*
  work serialises and stalls *all* crews — the offender is **file writes**, not the
  millisecond regex parses. Hence per-crew files (step 2) + fast/async writes.
- The green-flag stagger sequences the **one-time startup burst** (heavy MD/rage/skill
  setup), not steady-state cycles. Crews overlap in steady state **by design** — safe
  under a bounded aggregate (step 5). **Do not** engineer rigid phase-offset: it adds
  complexity and drifts apart anyway once launches fire on response instead of sleep-60.

---

## Open questions / to confirm when home
1. The **success marker** in the launch response — captured automatically by logging one
   launch (no manual timing). Unblocks step 3. (Reject/"must wait" text optional via a
   one-time double-launch probe.)
2. How often the boss actually despawns/respawns (how frequently the gate fires) — affects
   how much step 4 matters in practice.
3. Best `boss_join_concurrency` per crew under real rate limits — tune live.
4. **RESOLVED** — `aggregate_avg_session_damage` = total_session_damage / total_raids
   (average damage per raid; already how the summary computes it).
5. **RESOLVED** — records are **per crew, per boss**, stored in each crew's file as
   `best_by_boss`; beating your own boss record replaces the number; existing "new record"
   wording kept. The old global `boss_records` is dropped.

## Suggested sequencing
**0 (prerequisite).** Crew registry by ID: `!crew-add <crewid> <alias>` + capture `crew_id`
on the trustee scan. Everything per-crew keys on the ID, so this comes first (it's small).
Then 1 → 2 → 3 → 4, with 5 and 6 folded in alongside 1 (they're small and independent).
Steps 1 and 2 are the foundation; 3 is the flat-60s win; 4 is the stampede fix; 5/6 are
the safety rails that keep it honest under load.
