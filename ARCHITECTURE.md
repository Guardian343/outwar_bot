# DeathBot — Architecture & Contributor Guide

This document is for anyone reading or modifying the code (especially if you're
**not** the one running the bot live). It explains how the bot is put together,
how the main flows work, and — most importantly — the **non-obvious constraints
and landmines** where an innocent-looking "fix" would actually reintroduce a bug
we deliberately designed around. Read the Landmines section before changing raid,
session, or cast logic.

---

## What the bot is

A Python Discord bot (discord.py) that automates raiding and account management
for the Outwar browser game (sigil.outwar.com). One person runs it locally; other
contributors work on the source and push to GitHub. So **the code is the only
window a contributor has into runtime behaviour** — hence this doc.

---

## Layout

```
main.py                 Entry point. Loads config, logs into Outwar, loads cogs, starts the bot.
config.py               Loads settings: secrets from .env, non-secrets from config.json.

outwar/                 Core library (no Discord here — pure game/data logic)
  session.py              Authenticated HTTP layer. ONE shared aiohttp session, per-request
                          account cookies. All game traffic goes through here. Retry policy lives here.
  scraper.py              HTML/JSON parsers: profiles, rankings, room data, pathfinding (find_path),
                          slayer targets, join-limit parsing, teleporter parsing.
  database.py             JSON-backed persistence. Reads/writes files under database/ (see below).
  constants.py            Skill IDs, god rooms, static game constants.
  Areas.txt / Mobs.txt    Reference data (room->area map, mob reference). NOTE: Mobs.txt IDs are
                          unreliable — see Landmines.
  map_graph.json          Room adjacency graph for pathfinding.

cogs/                   Discord command modules (one Cog per area)
  auth.py                 Command authorisation (who can run what).
  boss_raid_commands.py   Crew-boss autoboss loop: MD/skill casting, form/join/launch, cadence.
  raid_commands.py        God raids. TWO functions: _do_world_raid (SLAYER) and _do_god_raid (PRIME).
  primewatcher.py         Background task: watches for prime god spawns, triggers prime raids.
  god_monitor.py          Background task: monitors god/boss spawns.
  admin_commands.py       Optimise, scoring (!score/!scores), scan-keys, crew locks.
  health.py               Health/status tracking.
  (others)                character, boss, database, crawler, misc, utility, help, embed_style.

database/               LIVE RUNTIME STATE — git-ignored. NEVER committed. See Landmines.
```

---

## Key flows

**Startup** (`main.py`): load config -> create Outwar session + log in -> load all
cogs -> start Discord bot -> background monitors (primewatcher, god_monitor) start.

**Boss raid** (`boss_raid_commands.py`): cast MD/skills across the crew -> then a
steady raid loop: form a raid -> join all accounts -> launch -> collect stats ->
repeat. The loop respects the game's ~60s form cooldown (see Landmines).

**Slayer raid** (`raid_commands.py::_do_world_raid`): for each daily slayer god,
scout the room -> if the target god is present, form on it -> size the roster to
the god's join limits -> join -> launch. Learns each god's min/max joiners live.

**Prime raid** (`raid_commands.py::_do_god_raid`): triggered by primewatcher when a
prime god spawns. Similar shape to slayer but a separate function.

---

## Configuration: two files, two jobs

- **`.env`** (git-ignored) — secrets only: `DISCORD_BOT_TOKEN`, `OUTWAR_USERNAME`,
  `OUTWAR_PASSWORD`. `.env.example` is the committed template.
- **`config.json`** (git-ignored) — non-secret bot config: prefix, channel IDs.
- **`database/settings.json`** (git-ignored) — live-tunable runtime settings:
  `host_connection_limit`, `boss_join_concurrency`, scoring weights, excluded
  accounts, etc. Read at runtime with sensible defaults if a key is absent.

Because these are git-ignored, their **values** are local to the operator. Shared
knowledge about *how to set them* lives in this doc, not in the files.

---

## LANDMINES — read before changing raid / session / cast code

These are places where the code is written a specific way **on purpose**, and a
reasonable-looking change would break something. Each explains the trap.

### 1. Action requests do NOT retry — a rate-limited join DROPS the account
`session.py` splits requests into read-only (GET) and action (POST). Read-only
requests retry safely (up to 5x). **Action requests (join, cast, form, launch)
retry only ONCE and return empty on a rate-limit.** Why: a rate-limited action may
have already succeeded server-side; blindly retrying could double-execute it (join
twice, launch twice). Consequence: if the join step is run at too-high concurrency
and sigil rate-limits some joins, **those accounts silently don't join the raid.**
- **Do NOT** add blind retries to join/cast/launch to "fix" missing accounts.
- The correct lever is concurrency (below), tuned so joins never rate-limit.

### 2. Two concurrency limits interact — effective = the LOWER of the two
Join speed is governed by BOTH `boss_join_concurrency` (the join semaphore) AND
`host_connection_limit` (the aiohttp per-host cap, which throttles ALL traffic).
Effective concurrency = min(the two). Raising one without the other does nothing.
Because of Landmine #1, the tuning target is the **highest** concurrency where the
`[TIMING] join took…` log line shows **`rate-limited=0`**. Past that, you drop
accounts (not just slow down). Zero is the target, not "low".

### 3. Match gods by NAME, not by stored mob_id
`Mobs.txt` mob IDs are unreliable (e.g. Freezebreed is mobId 1288 live but 944 in
Mobs.txt). The game's room data (`roomDetailsNew`) gives each mob's authoritative
`name` AND its live `mobId`/`h`. Slayer targeting matches the room entry by **name**,
then forms using **that entry's own live mobId/h**. This is critical in shared rooms
(2+ raidable gods in one room, e.g. Freezebreed + Wrevernd), where forming on the
"first formable mob" raids the WRONG god and logs a false LOSE.
- **Do NOT** "optimise" this back to matching stored mob_ids.

### 4. Slayer and Prime are TWO functions with parallel logic
`_do_world_raid` = SLAYER. `_do_god_raid` = PRIME. They share very similar
navigation/scout/form logic but are **separate functions with different parameter
names** (slayer's target dict is `mob`; prime's is `god`). A fix in one often needs
porting to the other — but carefully: copying code between them without renaming the
target variable causes `name 'mob' is not defined` / `name 'god' is not defined`
runtime errors (these pass a syntax check but crash when the line executes).
- NOTE: as of writing, the NAME-match fix (Landmine #3) is in the SLAYER function
  only. The PRIME function still uses ID-matching and has the latent wrong-god bug.

### 5. The ~60s cadence floor is the game, not a bug
A boss raid can't be re-formed faster than the game's ~60s form cooldown. Observed
cadence (~65-73s) is 60s (form gate) + join time + overhead. **Sub-60s is impossible.**
Don't chase it. The only real levers are join speed (concurrency) and trimming
overhead. Target is ~65s, not lower.

### 6. The ad-frame: Outwar serves ADS instead of results
On a random % of `cast_skills.php` (and other) requests, Outwar returns a 160x600
ad page (identified by `#outerdiv` + `#inneriframe` in the HTML) instead of the real
result. This is NOT a failure — the underlying action often succeeded; the ad just
hid the confirmation. The MD cast path has a **deliberate safeguard**: if an account
is blocked only by ad-frames, it is KEPT in the raid (MD assumed active) rather than
excluded, because benching a buffed account is worse than a rare unbuffed cycle.
- **Do NOT** treat an ad-frame response as a hard failure.

### 7. `database/` is sacred — never commit it, never assume its contents
The `database/` folder holds live runtime state (trustees, sessions, settings,
learned join-limits, teleporter KB, MD state). It is git-ignored so it's never
committed and never overwritten by a code deploy. Code must read it defensively
(sensible defaults if a key/file is absent) — never assume a particular value is
present, because a fresh install won't have it.

---

## Deploy model (how the operator runs it)

Contributor edits code -> commits -> pushes to GitHub. Operator pulls, restarts the
bot, runs locally. `database/` (live state) is untouched by pulls, so the bot keeps
its learned data across updates. Only the operator runs the bot; contributors work
on source only.

## Before pushing
- Syntax check: `python -c "import ast,glob; [ast.parse(open(f).read()) for f in glob.glob('**/*.py',recursive=True)]"`
- A syntax check does NOT catch wrong-variable-name bugs (Landmine #4) — those only
  surface at runtime. Read your variable references against the function's actual scope.
- Never commit `database/`, `.env`, or `config.json` (all git-ignored already).
