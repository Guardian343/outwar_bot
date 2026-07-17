"""
database.py — JSON-backed persistence for groups, crews, trustees, settings and prime gods.
All paths are relative to the bot's base directory so it works on any machine.
"""
import json
import os
import re
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(BASE_DIR, "database")


def _db_path(filename: str) -> str:
    os.makedirs(DB_DIR, exist_ok=True)
    return os.path.join(DB_DIR, filename)


def _read(filename: str) -> list:
    path = _db_path(filename)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else []


def _write(filename: str, data: list):
    with open(_db_path(filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

def get_groups() -> list[dict]:
    return _read("groups.json")


def get_group(name: str) -> Optional[dict]:
    for g in get_groups():
        if g["name"].upper() == name.upper():
            return g
    return None


def get_primewatchers() -> dict:
    """All configured prime watchers, keyed by lowercased name."""
    return _read_dict("primewatchers.json")


def save_primewatchers(data: dict):
    _write_dict("primewatchers.json", data)


def add_group(name: str, character_names: str) -> bool:
    groups = get_groups()
    if any(g["name"].upper() == name.upper() for g in groups):
        return False
    groups.append({"name": name.upper(), "character_names": character_names})
    _write("groups.json", groups)
    return True


def update_group(name: str, character_names: str) -> bool:
    groups = get_groups()
    for g in groups:
        if g["name"].upper() == name.upper():
            g["character_names"] = character_names.replace("  ", " ").strip()
            _write("groups.json", groups)
            return True
    return False


def delete_group(name: str) -> bool:
    groups = get_groups()
    new = [g for g in groups if g["name"].upper() != name.upper()]
    if len(new) == len(groups):
        return False
    _write("groups.json", new)
    return True


def group_to_list(group: dict) -> list[str]:
    """Parse group character_names string into a list of character names."""
    names_str = group.get("character_names", "")
    quoted = re.findall(r'"[^"]*"', names_str)
    for q in quoted:
        names_str = names_str.replace(q, "")
    result = [q.strip('"') for q in quoted]
    result += [n for n in names_str.split() if n]
    return result


def delete_groups_by_prefix(prefix: str):
    """Delete all groups whose name starts with a given prefix."""
    groups = get_groups()
    new = [g for g in groups if not g["name"].upper().startswith(prefix.upper())]
    _write("groups.json", new)
    return len(groups) - len(new)


def bulk_add_groups(new_groups: list[dict]) -> int:
    """Add multiple groups at once."""
    groups = get_groups()
    existing_names = {g["name"].upper() for g in groups}
    added = 0
    for g in new_groups:
        if g["name"].upper() not in existing_names:
            groups.append({"name": g["name"].upper(), "character_names": g["character_names"]})
            existing_names.add(g["name"].upper())
            added += 1
    _write("groups.json", groups)
    return added


# ---------------------------------------------------------------------------
# Crews
# ---------------------------------------------------------------------------

def get_crews() -> list[dict]:
    return _read("crews.json")


def get_crew(name: str) -> Optional[dict]:
    for c in get_crews():
        if c["name"].upper() == name.upper():
            return c
    return None


def add_crew(name: str, full_name: str) -> bool:
    crews = get_crews()
    if any(c["name"].upper() == name.upper() for c in crews):
        return False
    crews.append({"name": name.upper(), "full_name": full_name})
    _write("crews.json", crews)
    return True


def update_crew(name: str, full_name: str) -> bool:
    crews = get_crews()
    for c in crews:
        if c["name"].upper() == name.upper():
            c["full_name"] = full_name
            _write("crews.json", crews)
            return True
    return False


def delete_crew(name: str) -> bool:
    crews = get_crews()
    new = [c for c in crews if c["name"].upper() != name.upper()]
    if len(new) == len(crews):
        return False
    _write("crews.json", new)
    return True


# ---------------------------------------------------------------------------
# Trustees
# ---------------------------------------------------------------------------

def get_trustees() -> list[dict]:
    return _read("trustees.json")


def save_trustees(trustees: list[dict]):
    _write("trustees.json", trustees)


def get_trustees_by_crew(crew_full_name: str) -> list[dict]:
    return [t for t in get_trustees() if t.get("crew", "") == crew_full_name]


def get_trustees_by_group(group_name: str) -> list[dict]:
    group = get_group(group_name)
    if not group:
        return []
    names = group_to_list(group)
    trustees = get_trustees()
    return [t for t in trustees if t["name"] in names]


# ---------------------------------------------------------------------------
# Settings  (stored in settings.json as a dict)
# ---------------------------------------------------------------------------

def get_settings() -> dict:
    path = _db_path("settings.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def save_settings(settings: dict):
    with open(_db_path("settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


# ---------------------------------------------------------------------------
# Boss death timestamps — the bot's own precise UTC record of when a boss
# despawned. Used to compute spawn windows without depending on the ambiguous
# timezone of the page's "last killed" string.
# ---------------------------------------------------------------------------

def get_boss_deaths() -> dict:
    """{boss_full_name: iso_utc_timestamp}."""
    path = _db_path("boss_deaths.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def record_boss_death(boss_name: str, when_iso: str = None):
    """Record the moment a boss was observed to despawn (UTC ISO). Idempotent per
    death because callers only invoke it on the spawned→despawned transition."""
    from datetime import datetime, timezone
    deaths = get_boss_deaths()
    deaths[boss_name] = when_iso or datetime.now(timezone.utc).isoformat()
    with open(_db_path("boss_deaths.json"), "w", encoding="utf-8") as f:
        json.dump(deaths, f, indent=2)


def get_boss_death_dt(boss_name: str):
    """Return the recorded UTC death datetime for a boss, or None."""
    from datetime import datetime
    iso = get_boss_deaths().get(boss_name)
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Locked crews — members must NEVER be moved by optimisation. Keyed by crew id
# (symbol-proof); a normalized-name fallback covers rosters not yet re-scanned.
# ---------------------------------------------------------------------------

_DEFAULT_LOCKED_CREW_IDS   = [903, 18948]                 # The Spectrum, Need for Speed
_DEFAULT_LOCKED_CREW_NAMES = ["thespectrum", "needforspeed"]


def _norm_crew(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def get_locked_crews():
    """Return (locked_id_set, locked_normalized_name_set)."""
    s = get_settings()
    ids = s.get("locked_crew_ids", _DEFAULT_LOCKED_CREW_IDS)
    names = s.get("locked_crew_names", _DEFAULT_LOCKED_CREW_NAMES)
    id_set = set()
    for x in ids:
        try:
            id_set.add(int(x))
        except (ValueError, TypeError):
            pass
    return id_set, {_norm_crew(n) for n in names}


def is_crew_locked(crew_id, crew_name=None) -> bool:
    """True if a trustee in this crew must be protected from optimisation."""
    id_set, name_set = get_locked_crews()
    if crew_id is not None:
        try:
            if int(crew_id) in id_set:
                return True
        except (ValueError, TypeError):
            pass
    return _norm_crew(crew_name) in name_set if crew_name else False


def add_locked_crew(crew_id=None, crew_name=None):
    s = get_settings()
    ids = list(s.get("locked_crew_ids", _DEFAULT_LOCKED_CREW_IDS))
    names = list(s.get("locked_crew_names", _DEFAULT_LOCKED_CREW_NAMES))
    if crew_id is not None:
        try:
            iv = int(crew_id)
            if iv not in [int(x) for x in ids]:
                ids.append(iv)
        except (ValueError, TypeError):
            pass
    if crew_name and _norm_crew(crew_name) not in {_norm_crew(n) for n in names}:
        names.append(_norm_crew(crew_name))
    s["locked_crew_ids"] = ids
    s["locked_crew_names"] = names
    save_settings(s)


def remove_locked_crew(crew_id=None, crew_name=None):
    s = get_settings()
    ids = [int(x) for x in s.get("locked_crew_ids", _DEFAULT_LOCKED_CREW_IDS)]
    names = [_norm_crew(n) for n in s.get("locked_crew_names", _DEFAULT_LOCKED_CREW_NAMES)]
    if crew_id is not None:
        try:
            ids = [x for x in ids if x != int(crew_id)]
        except (ValueError, TypeError):
            pass
    if crew_name:
        names = [n for n in names if n != _norm_crew(crew_name)]
    s["locked_crew_ids"] = ids
    s["locked_crew_names"] = names
    save_settings(s)


# ---------------------------------------------------------------------------
# Excluded accounts (never used in boss raids or optimise)
# ---------------------------------------------------------------------------

def get_excluded() -> list:
    """List of excluded account names (original casing preserved)."""
    return get_settings().get("excluded_accounts", [])


def add_excluded(names: list) -> list:
    """Add names to the exclude list. Returns the names actually added (new)."""
    settings = get_settings()
    cur = settings.get("excluded_accounts", [])
    have = {n.lower() for n in cur}
    added = []
    for n in names:
        if n.lower() not in have:
            cur.append(n)
            have.add(n.lower())
            added.append(n)
    settings["excluded_accounts"] = cur
    save_settings(settings)
    return added


def remove_excluded(names: list) -> list:
    """Remove names from the exclude list. Returns the names actually removed."""
    settings = get_settings()
    cur = settings.get("excluded_accounts", [])
    drop = {n.lower() for n in names}
    removed = [n for n in cur if n.lower() in drop]
    settings["excluded_accounts"] = [n for n in cur if n.lower() not in drop]
    save_settings(settings)
    return removed


# ---------------------------------------------------------------------------
# Alert channels
# ---------------------------------------------------------------------------

def get_alert_channel(alert_type: str) -> Optional[int]:
    """Get the Discord channel ID for a given alert type (gods, bosses, envoys)."""
    return get_settings().get(f"alert_channel_{alert_type}")


def set_alert_channel(alert_type: str, channel_id: int):
    settings = get_settings()
    settings[f"alert_channel_{alert_type}"] = channel_id
    save_settings(settings)


# ---------------------------------------------------------------------------
# God/Envoy spawn state tracking
# ---------------------------------------------------------------------------

def get_god_state() -> dict:
    return get_settings().get("god_state", {})


def save_god_state(state: dict):
    settings = get_settings()
    settings["god_state"] = state
    save_settings(settings)


def get_envoy_state() -> dict:
    return get_settings().get("envoy_state", {})


def save_envoy_state(state: dict):
    settings = get_settings()
    settings["envoy_state"] = state
    save_settings(settings)


def get_boss_state() -> dict:
    """Returns dict of {boss_full_name: spawned_bool} from last poll."""
    return get_settings().get("boss_state", {})


def save_boss_state(state: dict):
    settings = get_settings()
    settings["boss_state"] = state
    save_settings(settings)


# ---------------------------------------------------------------------------
# Prime Gods database
# ---------------------------------------------------------------------------

def get_prime_gods() -> list[dict]:
    return _read("prime_gods.json")


def save_prime_gods(gods: list[dict]):
    _write("prime_gods.json", gods)


def get_prime_god(name: str) -> Optional[dict]:
    """Look up a god by full name, short name, alias, or partial match."""
    from outwar.scraper import GOD_ALIASES
    name_lower = name.lower().strip()

    # Check aliases first
    resolved = GOD_ALIASES.get(name_lower, name_lower)

    for g in get_prime_gods():
        if (g.get("short_name", "").lower() == resolved or
                g.get("short_name", "").lower() == name_lower or
                g.get("name", "").lower() == name_lower or
                name_lower in g.get("name", "").lower() or
                name_lower in g.get("short_name", "").lower()):
            return g
    return None


def update_prime_god(god_id: int, updates: dict):
    gods = get_prime_gods()
    for g in gods:
        if g.get("god_id") == god_id:
            g.update(updates)
            save_prime_gods(gods)
            return True
    return False


def upsert_prime_god(god: dict):
    gods = get_prime_gods()
    for i, g in enumerate(gods):
        if g.get("god_id") == god.get("god_id"):
            gods[i].update(god)
            save_prime_gods(gods)
            return
    gods.append(god)
    save_prime_gods(gods)


# ---------------------------------------------------------------------------
# Crew name aliases
# ---------------------------------------------------------------------------

CREW_ALIASES = {
    "to":     "The Order",
    "lod":    "†Legion of Death†",
    "ownage": "~Ownage~",
    "de":     "Dark Empire",
    "dp":     "Dark Plague",
    "dc":     "Dark Carnival",
    "ehb":    "Expired Hooker Bots",
    "pphb":   "PurePwnageHookerBots",
    "bhb":    "BeastlyHookerBots",
    "biz":    "Bizaar | Bizzar",
    "elite":  "~ELITES~",
    "gv":     "Gorilla Voltage",
    "an":     "†Ad Nauseum†",
    "cc":     "Crook County",
    "ap":     "Absolute Power",
    "hw":     "Hatchet Warrior",
    "hh":     "Heartbroken&Homicidal",
    "sin":    "S i N",
    "mml":    "Marvelous Missing Link",
}


def get_custom_aliases() -> dict:
    """Return user-defined aliases from settings.json."""
    return get_settings().get("crew_aliases", {})


def set_custom_alias(shortname: str, full_name: str):
    """Add or update a custom crew alias."""
    settings = get_settings()
    aliases = settings.get("crew_aliases", {})
    aliases[shortname.lower()] = full_name
    settings["crew_aliases"] = aliases
    save_settings(settings)


def remove_custom_alias(shortname: str) -> bool:
    """Remove a custom crew alias. Returns True if it existed."""
    settings = get_settings()
    aliases = settings.get("crew_aliases", {})
    if shortname.lower() in aliases:
        del aliases[shortname.lower()]
        settings["crew_aliases"] = aliases
        save_settings(settings)
        return True
    return False


def get_all_aliases() -> dict:
    """Return merged dict of built-in and custom aliases. Custom overrides built-in."""
    merged = dict(CREW_ALIASES)
    merged.update(get_custom_aliases())
    return merged


def normalize_crew(name: str) -> str:
    """Resolve a crew short name to its full name, checking custom aliases first."""
    all_aliases = get_all_aliases()
    return all_aliases.get(name.lower(), name)


# ---------------------------------------------------------------------------
# Raid win stats — track minimum power/ele used to win each god
# ---------------------------------------------------------------------------

def _read_dict(filename: str) -> dict:
    path = _db_path(filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        return json.loads(content) if content else {}


def _write_dict(filename: str, data: dict):
    with open(_db_path(filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def record_raid_win(god_name: str, god_id: int, avg_power: int, avg_ele: int, member_count: int):
    """
    Record a winning raid's average power and ele.
    Updates the prime god's rec_power and rec_ele if this win used lower stats
    than any previously recorded win (i.e. tracks the minimum viable stats).
    """
    stats = _read_dict("raid_win_stats.json")
    key   = str(god_id)

    existing = stats.get(key, {})
    prev_power = existing.get("min_power", avg_power + 1)
    prev_ele   = existing.get("min_ele",   avg_ele   + 1)
    wins       = existing.get("wins", 0)

    new_min_power = min(prev_power, avg_power)
    new_min_ele   = min(prev_ele,   avg_ele)

    stats[key] = {
        "god_name":    god_name,
        "god_id":      god_id,
        "min_power":   new_min_power,
        "min_ele":     new_min_ele,
        "wins":        wins + 1,
        "last_members": member_count,
    }
    _write_dict("raid_win_stats.json", stats)

    # Auto-update recommended stats on the prime god record
    # Only update if we have a new minimum (better data)
    if new_min_power < prev_power or new_min_ele < prev_ele:
        gods = get_prime_gods()
        for g in gods:
            if g.get("god_id") == god_id:
                # Only update if not manually set
                if not g.get("rec_power_custom"):
                    g["rec_power"] = new_min_power
                if not g.get("rec_ele_custom"):
                    g["rec_ele"] = new_min_ele
                break
        save_prime_gods(gods)


def get_raid_win_stats(god_id: int) -> Optional[dict]:
    stats = _read_dict("raid_win_stats.json")
    return stats.get(str(god_id))


def get_focus_crews() -> list[str]:
    """Return list of crew full names that should be highlighted in drop summaries."""
    return get_settings().get("focus_crews", [])


def add_focus_crew(full_name: str):
    settings = get_settings()
    crews = settings.get("focus_crews", [])
    if full_name.lower() not in [c.lower() for c in crews]:
        crews.append(full_name)
        settings["focus_crews"] = crews
        save_settings(settings)
        return True
    return False


def remove_focus_crew(full_name: str):
    settings = get_settings()
    crews = settings.get("focus_crews", [])
    new = [c for c in crews if c.lower() != full_name.lower()]
    if len(new) != len(crews):
        settings["focus_crews"] = new
        save_settings(settings)
        return True
    return False


def record_focus_drops(date_str: str, item_counts: dict, points: int):
    """
    Accumulate a day's focused-crew drops so the daily summary can show them.
    Stored as {date_str: {"item_counts": {name: count}, "points": int}}.
    Keeps only the last 14 days.
    """
    data = _read_dict("focus_drops.json")
    rec  = data.get(date_str, {"item_counts": {}, "points": 0})
    counts = rec.get("item_counts", {})
    for name, cnt in (item_counts or {}).items():
        counts[name] = counts.get(name, 0) + cnt
    rec["item_counts"] = counts
    rec["points"]      = rec.get("points", 0) + (points or 0)
    data[date_str]     = rec

    # Prune to the most recent 14 dates
    if len(data) > 14:
        for old in sorted(data.keys())[:-14]:
            data.pop(old, None)

    _write_dict("focus_drops.json", data)


def get_focus_drops(date_str: str) -> dict:
    """Return {"item_counts": {...}, "points": int} for a date (empty if none)."""
    data = _read_dict("focus_drops.json")
    return data.get(date_str, {"item_counts": {}, "points": 0})


def get_summary_crews() -> list[str]:
    """Return list of crew full names included in the daily summary."""
    return get_settings().get("summary_crews", [])


def add_summary_crew(full_name: str) -> bool:
    settings = get_settings()
    crews = settings.get("summary_crews", [])
    if full_name.lower() not in [c.lower() for c in crews]:
        crews.append(full_name)
        settings["summary_crews"] = crews
        save_settings(settings)
        return True
    return False


def remove_summary_crew(full_name: str) -> bool:
    settings = get_settings()
    crews = settings.get("summary_crews", [])
    new = [c for c in crews if c.lower() != full_name.lower()]
    if len(new) != len(crews):
        settings["summary_crews"] = new
        save_settings(settings)
        return True
    return False


# ---------------------------------------------------------------------------
# Auth / permissions
# ---------------------------------------------------------------------------

def get_auth() -> dict:
    """Return {owner_id, admins: [id,...], members: [id,...]}"""
    return get_settings().get("auth", {"owner_id": None, "admins": [], "members": []})

def save_auth(auth: dict):
    settings = get_settings()
    settings["auth"] = auth
    save_settings(settings)

def add_auth(level: str, user_id: int) -> bool:
    auth = get_auth()
    key = "admins" if level == "admin" else "members"
    if user_id not in auth[key]:
        auth[key].append(user_id)
        save_auth(auth)
        return True
    return False

def remove_auth(user_id: int) -> str:
    """Remove from whichever level they're in. Returns level removed from or None."""
    auth = get_auth()
    for key in ("admins", "members"):
        if user_id in auth[key]:
            auth[key].remove(user_id)
            save_auth(auth)
            return key
    return None

def get_user_level(user_id: int) -> str:
    """Return 'owner', 'admin', 'member', or 'none'."""
    auth = get_auth()
    if user_id == auth.get("owner_id"):
        return "owner"
    if user_id in auth.get("admins", []):
        return "admin"
    if user_id in auth.get("members", []):
        return "member"
    return "none"


# ---------------------------------------------------------------------------
# MD state — persistent per-account cast timestamps
# ---------------------------------------------------------------------------

def get_md_state() -> dict:
    """Return {suid_str: {"cast_at": ts, "name": str}} for all tracked accounts."""
    return _read_dict("md_state.json")


def save_md_state(state: dict):
    _write_dict("md_state.json", state)


def set_md_cast(suid: int, name: str, cast_at: float):
    state = get_md_state()
    state[str(suid)] = {"cast_at": cast_at, "name": name}
    save_md_state(state)



def resolve_group(group: str) -> list:
    """Resolve a group/crew/name string to a list of trustee dicts.
    Resolution order: RGA group -> crew (by full name) -> exact account name.
    Single canonical implementation (was duplicated verbatim across
    misc_commands, utility_commands, raid_commands, and inline in boss raids)."""
    all_trustees = get_trustees()
    rga_group = get_group(group)
    if rga_group:
        names = set(group_to_list(rga_group))
        return [t for t in all_trustees if t["name"] in names]
    crew = get_crew(group)
    crew_full = crew["full_name"] if crew else normalize_crew(group)
    by_crew = get_trustees_by_crew(crew_full)
    if by_crew:
        return by_crew
    return [t for t in all_trustees if t["name"].lower() == group.lower()]


# ---------------------------------------------------------------------------
# Teleporter knowledge base (teleporters.json)
#   { "<item_id>": {"name":..., "destination":..., "room": <int|null>} }
# Destination -> room mapping is filled in once, from Areas.txt.
# ---------------------------------------------------------------------------

def get_teleporters() -> dict:
    return _read_dict("teleporters.json")


def save_teleporters(mapping: dict):
    _write_dict("teleporters.json", mapping)


# ---------------------------------------------------------------------------
# Slayer god join limits (min/max accounts per raid) — learned live from the
# god's form page, then reused. NOT prime-god raid caps (different mechanic).
#   join_limits.json: { "<alias>": {"min": 20, "max": 60} }
# ---------------------------------------------------------------------------

def get_join_limits() -> dict:
    return _read_dict("join_limits.json")


def save_join_limits(mapping: dict):
    _write_dict("join_limits.json", mapping)


def set_join_limit(alias: str, min_join: int, max_join: int):
    """Learn/update one god's join limits and persist."""
    data = get_join_limits()
    data[alias] = {"min": int(min_join), "max": int(max_join)}
    save_join_limits(data)


# ---------------------------------------------------------------------------
# Item archive — the catalogue of item names we've actually SEEN in-game,
# discovered by `!bp scan`. This is ground truth: names come straight from the
# backpack pages, so nothing here is a guess.
#
# Why it exists: potions/keys are matched by their EXACT in-game name. Keeping a
# scanned catalogue means we can (a) verify hand-written names, (b) list what an
# account is MISSING, and (c) notice when Outwar adds new items.
#
# Shape: {tab: {item_name: {"iid": str, "first_seen": iso, "last_seen": iso}}}
# ---------------------------------------------------------------------------

def get_item_archive() -> dict:
    """The full scanned item catalogue, keyed by backpack tab."""
    path = _db_path("item_archive.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_item_archive(archive: dict):
    with open(_db_path("item_archive.json"), "w", encoding="utf-8") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)


def merge_item_archive(tab: str, items: list) -> tuple[int, list]:
    """
    MERGE scanned items into the archive for one tab. Never replaces — a scan
    runs against a single account, which won't hold every item in the game, so
    replacing would wipe out everything that account happens not to own.

    items: [{"item_name": str, "item_id": str, ...}]
    Returns (total_in_tab_after, [newly_discovered_names]).
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    archive = get_item_archive()
    tab_items = archive.get(tab, {})
    new_names = []
    for it in items:
        name = (it.get("item_name") or "").strip()
        if not name:
            continue
        if name not in tab_items:
            tab_items[name] = {"iid": it.get("item_id", ""),
                               "first_seen": now, "last_seen": now}
            new_names.append(name)
        else:
            tab_items[name]["last_seen"] = now
            if it.get("item_id"):
                tab_items[name]["iid"] = it["item_id"]
    archive[tab] = tab_items
    save_item_archive(archive)
    return len(tab_items), new_names


def merge_teleporters(found: list) -> tuple[int, list]:
    """
    MERGE discovered teleporters into teleporters.json. Like the item archive,
    this only ever EXPANDS — a rescan adds new finds and refreshes the
    destination/kind, but never wipes knowledge we already have.

    Critically it PRESERVES any 'room' already mapped: room numbers are worked
    out by hand, so blindly re-writing them as None on every scan would throw
    that work away.

    found: [{"item_id", "item_name", "destination", "kind"}]
    Returns (total_known_after, [newly_discovered_names]).
    """
    kb = get_teleporters()
    new_names = []
    for s in found:
        key = str(s.get("item_id"))
        if not key or key == "None":
            continue
        existing = kb.get(key, {})
        if not existing:
            new_names.append(s.get("item_name", key))
        kb[key] = {
            "name":        s.get("item_name") or existing.get("name", ""),
            "destination": s.get("destination") or existing.get("destination"),
            "kind":        s.get("kind") or existing.get("kind"),
            # Keep any room we've already mapped — never reset it to None.
            "room":        existing.get("room"),
        }
    save_teleporters(kb)
    return len(kb), new_names
