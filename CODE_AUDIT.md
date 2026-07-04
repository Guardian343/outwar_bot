# DeathBot — Full Code Audit (Phase 1 map)

**Purpose:** one place to work down from, safest → riskiest. Nothing here is a
deploy yet — it's the map. We act on it one isolated change at a time, in safe
windows, never mid-high-value-boss.

## ✅ Batch 1 — BUILT (awaiting deploy + live test)
Removed 20 confirmed-dead functions (verified zero refs incl. main.py — `log_relogin`
was spared, it's called from main.py:48). Stripped the launch-capture instrumentation.
Deduped `_resolve_group` → single `database.resolve_group`, 3 cogs now delegate.
All files syntax-clean. `[TIMING]` prints kept for Batch 2 diagnostics.

## Safety flags
- 🟢 **SAFE** — dead code / temp debug / unused. Removing changes no behaviour. Worst case: nothing happens.
- 🟡 **MODERATE** — duplication or drift that needs a look before touching; verify which copy is live, test, then rework.
- 🔴 **RISKY** — raid- or state-critical duplication. Consolidate one at a time, keep the old path until the new one is proven, safe-window deploys only.

**Blanket caveat for every removal:** grep can't see dynamic dispatch
(`getattr`, string-keyed calls). Before deleting anything below, one last check
that it isn't referenced dynamically. All items were already confirmed against
tree-wide references and framework/command/task invocation.

---

## 🟢 SAFE — genuine dead code (defined, never called anywhere in the tree)

These survived a tree-wide reference scan that excludes command handlers,
`@tasks.loop`, event listeners, and UI callbacks — so they're real orphans.

| Function | Location | Note |
|---|---|---|
| `require_auth` | cogs/auth.py:87 | Orphaned auth decorator. Zero refs. Superseded by the `is_authorised` / MEMBER/ADMIN command lists. |
| `_md_currently_active` | cogs/boss_raid_commands.py:583 | MD-majority-active scan, superseded by `_md_has_30min_left` / `_md_done_this_cycle`. |
| `_display_stat` | cogs/character_commands.py:601 | Unused display helper. |
| `_display_full_stats` | cogs/misc_commands.py:428 | Unused display helper. |
| `error_embed` | cogs/embed_style.py:79 | Unused embed builder (siblings `info_embed`/`warn_embed` are used; this one isn't). |
| `log_relogin` | cogs/health.py:37 | Health hook never called (`log_error`/`log_raid_failure`/`log_raid_success` are). |
| `get_trustee` | outwar/database.py:173 | Zero call-sites (previously noted — its absence is what avoids an O(n²) loop). |
| `is_excluded` | outwar/database.py:332 | Superseded by `get_excluded()` + inline checks. |
| `set_md_cast_bulk` | outwar/database.py:747 | Bulk MD writer no longer called (per-account `set_md_cast` is used). Verify. |
| `set_owner` | outwar/database.py:692 | Owner set elsewhere. Verify. |
| `format_cooldown` | outwar/scraper.py:1188 | Unused formatter. |
| `mob_room_by_name` | outwar/scraper.py:1348 | Unused lookup. |
| `parse_crew_boss_loot` | outwar/scraper.py:483 | Unused parser. Verify vs loot features. |
| `parse_envoy_loot_page` | outwar/scraper.py:1247 | Unused parser. Verify vs envoy features. |
| `parse_markdown_status` | outwar/scraper.py:1155 | Superseded by `md_status_from_cast` (arithmetic) — this one scraped the page. |
| `slayed_god_ids` | outwar/scraper.py:1308 | Unused slayer helper. |
| `cast_skill` | outwar/session.py:280 | OLD skill API — superseded by cog's `_cast_skill_post`. |
| `get_skill_cooldown` | outwar/session.py:253 | OLD skill API. |
| `is_skill_cast` | outwar/session.py:273 | OLD skill API. |
| `is_skill_trained` | outwar/session.py:266 | OLD skill API. |
| `render_gods_up_table` | outwar/table_image.py:568 | Superseded by `render_gods_table`. Verify. |

**Cluster worth noting:** the four `outwar/session.py` skill methods
(`cast_skill`, `get_skill_cooldown`, `is_skill_cast`, `is_skill_trained`) look
like a whole superseded skill API — the cog does its own skill POSTs now. Likely
removable as a group, which also shrinks `session.py`.

## 🟢 SAFE — temporary instrumentation we added (strip once its job is done)

| What | Location | Status |
|---|---|---|
| `[RAID-LAUNCH-MSGS]` / `[RAID-LAUNCH-CONTENT]` capture (+ the `_content`/`_msgs` block) | boss_raid_commands.py ~977–982 | **Done its job** — proved the launch body is useless; the real signal is the raidattack page. Spams ~900 chars/raid. Strip now. |
| `[TIMING]` prints | boss_raid_commands.py 1058, 1162, 1196, 1203 | Keep the stats/spawned ones until the raid-loop consolidation is verified; **1196 & 1203 sit in Loop A and never fire on normal starts** — dead prints in practice. Strip all together at the end of the raid work. |

---

## 🟡 MODERATE — drift / duplication that needs a look, but bounded

### Command surface (112 commands)
- **56/56 hyphen split** — no convention. Pick hyphens-for-multiword and keep every current name as a hidden alias so nothing you type today breaks.
- **16 commands missing from `!help`:** alerting, crawl-test, crew-lock, crew-unlock, crews-locked, envoy-fetch, excluded, god-export, god-rec-import, optimise-all, rmdebug, slayer, slayer-list, slayer-needs, slayer-stop, standings.
- **Legacy + redispatch layer:** `!autoboss` still exists as its own command; `boss_commands.py` has a `boss` group whose subcommands `_redispatch` to the old flat commands (`boss auto`→`autoboss`, `boss raid`→`bossraid`, etc.). Works, but it's two names per action. Decide: is the `boss` group canonical (retire flat names to aliases) or vice versa.
- **Fix that prevents recurrence:** auto-generate `!help` from registered commands so the "16 undocumented" gap can't happen again.

### Shared raid primitives across two subsystems
- `boss_raid_commands.py` (crew bosses) and `raid_commands.py` (gods/prime gods) **each** have their own `formraid.php` / `joinraid.php…launchraid` / join-fan-out / result-read code. Not duplication to delete — two legitimately different raid types — but the **form/join/launch/read primitives could be extracted to one shared module**. Relevant now: the god system already uses `formraid.php?target=` and already reads results, so its logic is a reference for the boss-side poll-form rewrite.

### MD-helper family (after dead one is removed)
- `_md_has_30min_left`, `_md_done_this_cycle` remain and do different jobs — fine. Just don't re-add a third scanner; if another is needed, factor a single `_md_scan(predicate)`.

### File giants (split candidates, low urgency)
- boss_raid_commands.py **2237**, raid_commands.py **2150**, god_monitor.py **1478**, scraper.py **1432**. Big but coherent; splitting is polish, not correctness. Do *after* the raid-loop consolidation so we're not moving a moving target.

---

## 🔴 RISKY — the real structural problem: multiple raid loops

**This is the big one and the reason green flag + timing "disappeared."**

`_run_autoboss` (boss_raid_commands.py) contains **two** steady-state raid loops:
- **Loop A (~1193):** has the `first_raid_done` latch, the green flag (1256), and the `[TIMING]` instrumentation. Runs on the **MD-already-active** path.
- **Loop B (~1701):** the "inner raid loop" — the actual steady-state raider — with **no green flag, no timing.** Runs on the **fresh-MD-cast** path (i.e. your normal start).

On top of that, **`raidboss` (1914)** and **`bossraid` (1946)** are separate commands with their **own** `_do_boss_raid` call sites (1942, 2034, 2066). So crew-boss raiding is spread across **up to four launch paths**, only one of which has the features we've been adding.

**Consequences already observed:**
- No green flag on normal runs (it's in Loop A; you run Loop B).
- No `[TIMING]` lines (same).
- Cadence analysis was partly done against Loop A's structure while Loop B runs.
- Any fix (poll-form, etc.) built into one path silently no-ops on the others.

**The fix (dedicated safe-window job):** consolidate to **one** crew-boss raid loop that both MD-fresh and MD-active starts feed into, carrying: the green flag (fire on first confirmed launch, decoupled from stats), the poll-form cadence loop (`formraid.php?target=` + `"trying to form raids too fast"` marker), and a single set of instrumentation. Then `raidboss`/`bossraid` either call the same core or are retired if redundant.

**Build rules for this one:** fail-safe to the current compute-and-wait path; heavy per-attempt logging so a first-run problem shows immediately; poll-form and the aiohttp per-host connector swap are **separate deploys** so only one variable changes at a time.

---

## Recommended execution order

1. **Batch 1 (🟢, next restart):** delete `require_auth` + `_md_currently_active`; strip the `[RAID-LAUNCH-*]` capture. Tiny, obvious, quiets the console.
2. **Batch 2 (🟢, same or next restart):** remove the rest of the verified dead functions, grouped by file (session skill-API cluster; scraper orphans; database orphans; embed/health/display helpers). One file per commit so a break is trivially traceable.
3. **Batch 3 (🔴, dedicated safe window):** raid-loop consolidation — the big design job, deployed **alone**. Green flag + poll-form land here.
4. **Batch 4 (🟡):** command-surface cleanup — one convention, aliases for old names, auto-generated `!help`.
5. **Batch 5 (🟡, optional):** extract shared raid primitives; split the giant files.

---

## Per-file status (Phase 1)

| File | Lines | Status |
|---|---|---|
| admin_commands.py | 793 | ✅ Clean — no dead code, no dup loops. |
| boss_raid_commands.py | 2237 | 🔴 Multi-loop duplication + 1 dead fn + temp prints. |
| raid_commands.py | 2150 | 🟡 Separate god-raid system; shares raid primitives; deeper per-file pass pending. |
| outwar/database.py | 754 | 🟢 ~4 dead helpers. |
| outwar/scraper.py | 1432 | 🟢 ~6 dead parsers/helpers. |
| outwar/session.py | — | 🟢 Dead skill-API cluster (4 fns). |
| auth.py | — | 🟢 1 dead (`require_auth`). |
| embed_style / health / character_commands / misc_commands | — | 🟢 1 dead helper each. |
| god_monitor.py | 1478 | ⏳ Deeper per-file pass pending (large; poll loops + envoy paths). |
| primewatcher.py | 738 | ⏳ Deeper pass pending — known CAST-DEBUG console flood to gate behind a flag. |
| boss_commands.py | 468 | 🟡 Redispatch layer (command-surface item). |
| database_commands / utility_commands / crawler_commands / help_commands | — | ⏳ Not yet deep-dived. |

---

## Phase 1b — block-level duplication (near-duplicate blocks inside LIVE functions)

Found by block-hashing across all cogs. These are copy-pasted blocks *inside*
functions that are themselves live — invisible to the dead-function scan, and
exactly the "branches / near-duplicate blocks" layer.

### 🟡 Cross-file duplicated blocks (extract to one shared copy)
- **`_resolve_group` — verbatim triplicate (byte-identical, confirmed by md5).** Same "resolve group/crew/name → trustee list" method in **misc_commands.py:534, utility_commands.py:247, raid_commands.py:1596** — and the same logic is inlined again in `boss_raid_commands.py` raidboss/bossraid entry (≈1922, 1974). **4–5 copies → 1.** Cleanest high-value consolidation in the codebase: lift to `database.py` (or a shared `util`), import everywhere. Low risk (pure function of db state).
- **Reaction paginator — duplicate (~40 lines).** The ⏮◀▶⏭ paginated-embed + `on_raw_reaction` listener block in **god_monitor.py:1234 and character_commands.py:525.** Extract to a shared paginator helper.
- **Loot-unscramble parser — duplicate (~15 lines).** The `onmouseover` loot-cell unscramble in **god_monitor.py:508 and boss_commands.py:219.** Belongs in `scraper.py` as one function.

### 🟡 Within-file repeats (low priority)
- primewatcher.py 188 / 243 (add-group vs add-prime share a block)
- misc_commands.py 552 / 616
- utility_commands.py 103 / 213

### 🔴 Confirmed: the raid-primitive is duplicated in BOTH raid systems
- **Boss raids:** the `consecutive_timeouts` / `except asyncio.TimeoutError` retry wrapper is duplicated across the launch paths (**1205 = Loop A, 1707 = Loop B**), plus the command-entry boilerplate repeats in raidboss/bossraid. Hard, line-numbered confirmation of the multi-loop problem in the 🔴 section above.
- **God raids (`raid_commands.py`):** the room-nav + `formraid.php?target=M{mob_id}` **form-discovery primitive is copy-pasted ~14 times** across `raid_mob_once`, `raid_god_multi`, `raid_queue`, etc. (blocks at 941, 1025, 1062, 1185, 1329, 1354, 1475, 1687, 1712, 1882, 2000…). Same class of problem as the boss loops.
- **Implication:** both raid systems would benefit from **one shared form/join/launch/read core** — and that's the same core the poll-form rewrite should live in. Build it once, both systems use it.

## Dead-branch pass — result: clean
No `if False` / `if True` / unreachable branches. Only 3 "legacy" comment markers
(scraper.py 339/587 legacy alias maps — intentional; boss_raid_commands.py:995
"legacy callers" — refers to the multi-loop callers already flagged) and **2**
TODO/FIXME total. Branch-level, the code is tidy — the debt is *duplication*, not
dead branches.

## Coverage status of this audit
- ✅ **Function-level dead code:** tree-wide, complete (all cogs + core).
- ✅ **Block-level duplication:** tree-wide block-hash, complete.
- ✅ **Dead branches:** scanned, essentially none.
- ⏳ **Remaining (lower value):** semantic dead code — logic that runs but is
  functionally superseded (e.g. whether raidboss/bossraid are still needed once
  the loop is consolidated; whether `set_md_cast_bulk`/`set_owner` truly have no
  runtime path). These need judgment during the actual consolidation, not grep.
