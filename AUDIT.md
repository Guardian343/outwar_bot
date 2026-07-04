# Code Audit Findings
Date: 2026-06-07

## Commands to REMOVE (unused/broken/replaced)

| Command | File | Reason |
|---------|------|--------|
| `!raid` | raid_commands.py | Incomplete stub — shows partial god info, fully replaced by `!god` |
| `!rgastats` | misc_commands.py | Duplicate of `!group-stats` (older plain-text version) |
| `!crewstats` | misc_commands.py | Duplicate of `!group-stats` (older plain-text version) |
| `!stats` | character_commands.py | Replaced by `!group-stats` — old embed-based version |
| `!show-ele` | character_commands.py | Superseded by `!group-stats` which shows ele alongside other stats |
| `!show-chaos` | character_commands.py | Superseded by `!group-stats` |
| `!show-mr` | character_commands.py | Max rage not in group-stats — KEEP for now |
| `!godstatus` | god_monitor.py | Duplicate of `!gods` — both show god spawn status |
| `!bossstatus` | boss_commands.py | Replaced by `!bosslist` image |
| `!god-drops` | god_monitor.py | CHECK — may still be useful, review body |
| `!raidstatus` | boss_commands.py | Boss raid loop status — CHECK if boss raiding still works |
| `!autoboss` | boss_commands.py | REMOVED — will be rebuilt once boss raiding is tested |
| `!raidboss` | boss_commands.py | REMOVED — will be rebuilt once boss raiding is tested |

## Commands to KEEP but STANDARDISE

| Command | Issue | Fix |
|---------|-------|-----|
| `!up` | Sends plain text list | Convert to image using render_table |
| `!gods` | Sends plain embed | Convert to image |
| `!god` | Plain embed | Already good, minor cleanup |
| `!god-list` | Plain embed list | Convert to image |
| `!alert-channels` | Plain embed | Convert to image or keep as embed (admin only) |
| `!check-trustees` | Plain text dump | Paginate or convert |
| `!eligible` | Plain embed | Convert to image |
| `!envoys` | Plain text | Convert to image |
| `!check-item` | Plain embed | OK for now |
| `!drink` / `!drink-all` | OK | Minor cleanup |
| `!cast*` | OK | Minor cleanup |

## Performance Findings

1. `!stats` fetches profiles via `profile.php?transnick=X` (public page, no cookie switch) — these are slow
2. `!show-ele/chaos` do sequential fetches — already replaced by `!group-stats` which is concurrent
3. `!rgastats/crewstats` use `_display_full_stats` which fetches profiles — slow, replaced by `!group-stats`
4. Boss commands (`!autoboss`/`!raidboss`) use the old `AccountSession` login pattern per character — extremely slow
5. `!check-trustees` loads all 751 trustees into an embed without pagination

## Recommended Command Structure (post-audit)

### Raiding
- `!rm`, `!rg`, `!rq`, `!badge`, `!tce`, `!crest` ✅

### Prime Gods  
- `!gods`, `!up`, `!god`, `!god-list`, `!god-set`, `!prime-stats`, `!prime-drops`, `!primeupdate`, `!poll-now`

### Bosses
- `!bosslist`, `!boss-window` ✅ (remove `!bossstatus`)

### Stats
- `!group-stats`, `!pcaps`, `!rage`, `!who`, `!top`, `!bottom`, `!top-all`, `!uncapped` ✅

### Skills/Potions
- `!cast`, `!cast-ss`, `!cast-all`, `!cast-pres`, `!cast-fero`, `!cast-afflic`, `!cast-class`, `!skills`
- `!drink`, `!drink-all`, `!check-item`, `!check-md`

### Database
- `!groups`, `!crews`, `!alias`, `!aliases`, `!autorank`

### Admin
- `!scan-trustees`, `!update-trustees`, `!check-trustees`, `!set-alert-channel`, `!alert-channels`, `!get-sessid`

### Utility
- `!status`, `!rage`, `!who`, `!uncapped`, `!commands`, `!giveaway`, `!eligible`
