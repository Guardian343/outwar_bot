# Outwar Discord Bot (Python)

## Setup

1. **Install Python 3.11+**

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Create your config file**
   ```bash
   cp config.json.example config.json
   ```
   Then edit `config.json` with your Discord token, Outwar credentials, and channel IDs.

4. **Create the database folder** (first time only)
   ```bash
   mkdir database
   echo "[]" > database/groups.json
   echo "[]" > database/crews.json
   echo "[]" > database/trustees.json
   ```

5. **Run the bot**
   ```bash
   python main.py
   ```

## Trustees

Trustees are stored in `database/trustees.json`. Each entry looks like:
```json
[
  {
    "name": "CharacterName",
    "url": "https://sigil.outwar.com/world.php?suid=12345&id=12345&server=1",
    "level": 80,
    "crew": "The Order",
    "rage": 5000,
    "suid": 12345
  }
]
```
Use `?update-trustees` after editing this file to reload without restarting.

## Commands

### Character
| Command | Description |
|---------|-------------|
| `?stats <group>` | Show stats for a group or single character |
| `?show-ele <group>` | Show elemental damage for a group |
| `?show-chaos <group>` | Show chaos damage for a group |
| `?show-mr <group>` | Show max rage for a group or crew |
| `?cast-ss <group>` | Cast Street Smarts on a group/crew |
| `?cast-all <name>` | Cast Empower/Stealth/VitaminX/Fortify on one trustee |
| `?drink <crew> <potion>` | Use a potion on all in a group/crew |
| `?drink-all <crew>` | Use all standard potions on a crew |
| `?boss-pots <boss> [crew]` | Use boss-specific pots on a crew |
| `?check-md <crew>` | Check Markdown status for a crew |
| `?check-trustees` | List all trusted accounts |
| `?update-trustees` | Reload trustees from database/trustees.json |

### Bosses
| Command | Description |
|---------|-------------|
| `?bosslist` | List all server bosses and HP% |
| `?bossstatus <boss> [crew]` | Damage report for a boss |
| `?autoboss <crew> [target]` | Skill + raid a boss automatically |
| `?raidboss <crew> [target]` | Raid with already-skilled characters |
| `?raidstatus <crew>` | Show current raid loop status |

### Prime Gods
| Command | Description |
|---------|-------------|
| `!primeupdate` | Scrape Prime Gods page and update the local database (HP, short name, stats URL, spawn status, recommended group size) |
| `!gods` | Show all gods and spawn status from the database |
| `!god <name>` | Show full details for a specific god |
| `!god-list` | List all gods with short names and recommended sizes |
| `!god-set <name> <field> <value>` | Manually update a field. Fields: `recommended`, `hp`, `short_name` |
| `!raid <group> <god>` | Show info for a specific god (legacy) |

**Example workflow:**
```
!primeupdate              — build/refresh the database from the live page
!gods                     — see what's spawned
!god rezun                — full details on Rezun
!god-set rezun recommended 40   — override the recommended group size
```
Recommended group sizes default to sensible values but can be overridden per god with `!god-set`. Re-running `!primeupdate` will not overwrite manually set values.



### God Monitor
| Command | Description |
|---------|-------------|
| `?set-alert-channel <type> [#channel]` | Set alert channel for `gods`, `bosses`, or `envoys`. Defaults to current channel |
| `?alert-channels` | Show all configured alert channels |
| `?godstatus` | Manually check current Prime God spawn status |
| `?god-drops <name>` | Fetch and display drops for a god by name |
| `?envoys` | Show current envoy spawn status |
| `?envoy-drops <name>` | Fetch and display drops for an envoy |
| `?poll-now` | Manually trigger a god/envoy poll (requires Manage Channels) |

**Setup:**
1. Run `?set-alert-channel gods #your-channel` to direct god spawn/death alerts
2. Run `?set-alert-channel envoys #your-channel` for envoy alerts (falls back to gods channel if not set)
3. The bot polls at every `:00` and `:30` automatically once started

**How alerts work:**
- On spawn: posts a gold embed with the god/envoy name and ID
- On death: posts a red embed, then immediately fetches the stats page and posts a drops embed showing each crew's damage and loot


| Command | Description |
|---------|-------------|
| `?scan-trustees` | Scrape trustee list from Outwar and build trustees.json automatically, including crew detection |
| `?autorank <crew> <stat> <size>` | Rank a crew by stat (ele/chaos/power), split into named groups of `size` |

**Autorank example:**
```
?autorank to ele 10
```
Creates groups `TO_ELE_1` (ranks 1–10), `TO_ELE_2` (ranks 11–20), etc. Re-running the command replaces the old groups automatically. You can then use these groups directly with other commands like `?cast-ss TO_ELE_1` or `?boss-pots cosmos TO_ELE_1`.


| Command | Description |
|---------|-------------|
| `?groups` | List all groups |
| `?groups add <NAME> <chars...>` | Add a group |
| `?groups update <NAME> <chars...>` | Update a group's characters |
| `?groups delete <NAME>` | Delete a group |
| `?groups <NAME>` | Show characters in a group |
| `?crews` | List all crews |
| `?crews add <SHORT> <Full Name>` | Add a crew |
| `?crews update <SHORT> <Full Name>` | Update a crew |
| `?crews delete <SHORT>` | Delete a crew |

### Misc
| Command | Description |
|---------|-------------|
| `?check-item <item> [group]` | Check who has an item. Prefix with `!` to find who's missing it |
| `?eligible` | Show level 79s close to level 80 (United Path eligible) |
| `?top <amount> <group>` | Top N by power, ele, and chaos in a group/crew |
| `?top-all <amount> <stat>` | Top N across all level 80+ trustees. Stats: power/ele/chaos |
| `?bottom <amount> <group>` | Bottom N in a group/crew |
| `?rgastats <group>` | Full stat summary (totals + averages) for a named group |
| `?crewstats <crew>` | Full stat summary for a crew |
| `?giveaway <prize> [exclude...]` | Pick a random winner. Exclude names to skip them |

**check-item examples:**
```
?check-item rems to          — who has Remnant Solice in The Order
?check-item !bubble mygroup  — who's MISSING Bubble Gum in mygroup
?check-item crest            — who has a Crest equipped (all trustees)
?check-item chaosore to      — chaos ore counts per character
```
Known item keys: chaosgem, gem, rune, erune, crest, orb, resist, bubble, rems, vile, amdir, kix, squid, minor, snickers, starburst, skittle, reese, kit, tootsie, star, m&ms, key, chaosore, badgerep

**giveaway example:**
```
?giveaway "Rare Item" rabbit liam   — picks winner, excluding rabbit and liam
```
Edit `GIVEAWAY_USERS` in `outwar/constants.py` to add/remove participants.

## Notes

- The bot works from any directory — all paths are relative to the bot folder.
- Never commit `config.json` — it contains your credentials. Add it to `.gitignore`.
- The raid loop runs as a background task so the bot stays responsive during raids.
- Each trustee account is logged in separately for skill casting/potion use.
