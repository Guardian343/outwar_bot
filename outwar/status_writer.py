"""
status_writer.py — publishes the bot's LIVE state to database/status.json so the
supervisor dashboard can show real cycle data (spawned gods/bosses, live HP,
primewatcher progress).

DESIGN NOTES (why this is safe to add):
  - It only READS state the bot already computes and WRITES one small JSON file.
    It never changes raid logic, scheduling, or any game behaviour.
  - Every write is wrapped so a failure here can NEVER crash a poll loop or raid.
  - It writes atomically (temp file + replace) so the dashboard never reads a
    half-written file.
  - If this module is removed, the bot is unaffected; the dashboard simply falls
    back to config-only data.

USAGE (from god_monitor.py, minimal footprint):
    from outwar.status_writer import publish_gods, publish_bosses, publish_primewatch

    # in _poll_gods(), after gods are fetched:
    publish_gods(gods)

    # in _poll_bosses(), after bosses are fetched:
    publish_bosses(bosses)

    # optionally, from primewatcher after a cycle:
    publish_primewatch(cycle_summary)

Each call updates only its section of status.json, preserving the others.
"""

import json
import os
import tempfile
from datetime import datetime, timezone

# status.json lives alongside the other database files.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_DIR = os.path.join(_BASE_DIR, "database")
_STATUS_PATH = os.path.join(_DB_DIR, "status.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> dict:
    """Read the current status.json, or {} if absent/unreadable."""
    try:
        with open(_STATUS_PATH, encoding="utf-8") as f:
            c = f.read().strip()
            return json.loads(c) if c else {}
    except Exception:
        return {}


def _write_atomic(data: dict):
    """
    Write status.json atomically so the dashboard never sees a partial file.
    Any failure is swallowed — publishing status must never break the bot.
    """
    try:
        os.makedirs(_DB_DIR, exist_ok=True)
        # write to a temp file in the same dir, then atomically replace
        fd, tmp = tempfile.mkstemp(dir=_DB_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, _STATUS_PATH)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    except Exception:
        pass  # never let status writing crash a caller


def _update(section: str, payload):
    """Merge one section into status.json, stamping when it was last updated."""
    data = _read()
    data[section] = payload
    data.setdefault("updated", {})
    if isinstance(data.get("updated"), dict):
        data["updated"][section] = _now_iso()
    _write_atomic(data)


# ---------------------------------------------------------------------------
# Public API — call these from the bot with the objects it already has.
# ---------------------------------------------------------------------------

def publish_gods(gods):
    """
    gods: iterable of God objects (name, short_name, spawned, hp_pct).
    Publishes the current god roster + which are spawned + HP%.
    """
    try:
        payload = []
        for g in gods:
            payload.append({
                "name": getattr(g, "name", ""),
                "short_name": getattr(g, "short_name", ""),
                "spawned": bool(getattr(g, "spawned", False)),
                "hp_pct": float(getattr(g, "hp_pct", 0.0) or 0.0),
            })
        _update("gods", payload)
    except Exception:
        pass


def publish_bosses(bosses):
    """
    bosses: iterable of Boss objects (name, full_name, spawned, hp, hp_pct,
    spawn_days, last_killed).

    Publishes the current boss roster with live HP, plus spawn-window timing so
    the dashboard can show "in window" and a meaningful countdown:
      - window_open_at: ISO time the spawn window opens (killed + 75% of the
        spawn interval), or None if we can't work it out yet.
      - in_window: True once now >= window_open_at (and not currently spawned).
    """
    from datetime import datetime, timezone, timedelta
    try:
        # Import lazily so this module stays dependency-free if the DB isn't set up.
        try:
            from outwar import database as _db
        except Exception:
            _db = None

        def _window_open_at(b):
            """When this boss's spawn window opens, as a UTC datetime, or None."""
            spawn_days = getattr(b, "spawn_days", 0) or 0
            if not spawn_days:
                return None
            killed_dt = None
            if _db is not None:
                try:
                    killed_dt = _db.get_boss_death_dt(getattr(b, "full_name", ""))
                except Exception:
                    killed_dt = None
            # Fall back to the page's kill string (assumed CST) if we've no record.
            if not killed_dt:
                raw = getattr(b, "last_killed", "") or ""
                if raw:
                    import re
                    clean = re.sub(r"<[^>]+>", "", raw).strip()
                    for fmt in ("%a, %d %b %Y %I:%M%p", "%a, %d %b %Y %I:%M %p",
                                "%m-%d-%y %I:%M%p", "%Y-%m-%d %H:%M"):
                        try:
                            cst = timezone(timedelta(hours=-6))
                            killed_dt = datetime.strptime(clean, fmt).replace(tzinfo=cst)
                            break
                        except ValueError:
                            continue
            if not killed_dt:
                return None
            # Window opens at killed + 75% of the spawn interval (matches the bot's
            # own window logic in god_monitor).
            return killed_dt + timedelta(days=spawn_days) * 0.75

        now = datetime.now(timezone.utc)
        payload = []
        for b in bosses:
            spawned = bool(getattr(b, "spawned", False))
            open_at = None if spawned else _window_open_at(b)
            in_window = bool(open_at and now >= open_at) and not spawned
            payload.append({
                "name": getattr(b, "name", "") or getattr(b, "full_name", ""),
                "full_name": getattr(b, "full_name", ""),
                "spawned": spawned,
                "hp": int(getattr(b, "hp", 0) or 0),
                "hp_pct": float(getattr(b, "hp_pct", 0.0) or 0.0),
                "in_window": in_window,
                "window_open_at": open_at.isoformat() if open_at else None,
            })
        _update("bosses", payload)
    except Exception:
        pass


def publish_primewatch(summary):
    """
    summary: a dict describing the latest primewatcher cycle, e.g.
        {
          "cycle_started": iso,
          "groups": [
            {"group": "LOD11", "prime": "Sarcrina", "got": 1, "target": 2,
             "result": "won"/"lost"/"skipped", "reason": "..."},
            ...
          ]
        }
    Free-form — whatever the primewatcher wants to expose. Stored as-is.
    """
    try:
        _update("primewatch", summary)
    except Exception:
        pass


def publish_account(username, user_id):
    """
    Publish which Outwar account the bot is logged in as. The dashboard shows this
    under the instance so it's clear WHOSE trustees the total reflects (the count
    is everything trusteed to this account, across all crews).
    """
    try:
        _update("account", {"username": username, "user_id": user_id})
    except Exception:
        pass


def publish_guilds(guilds):
    """
    guilds: iterable of discord.Guild. Publishes the Discord server(s) this bot
    instance is in (name + id + member count) so the dashboard can label the
    instance with the real server rather than a generic name. In your model each
    bot serves one server, so this is normally a single entry.
    """
    try:
        payload = []
        for g in guilds:
            payload.append({
                "id": getattr(g, "id", None),
                "name": getattr(g, "name", "") or "",
                "members": getattr(g, "member_count", None),
            })
        _update("guilds", payload)
    except Exception:
        pass


def publish_access(owner, admins, members):
    """
    Publish the dashboard-visible auth list with RESOLVED DISCORD NAMES so the
    dashboard shows who people are, not bare IDs.

    Each argument is a list of dicts: {"id": int, "name": str}. The caller (the
    auth cog) resolves names via bot.get_user() before calling this — the
    supervisor can't resolve Discord names itself since it only reads files.
    'owner' is a single-item list (or empty).
    """
    try:
        def clean(lst):
            out = []
            for u in (lst or []):
                out.append({"id": u.get("id"), "name": u.get("name") or str(u.get("id"))})
            return out
        _update("access", {
            "owner": clean(owner),
            "admins": clean(admins),
            "members": clean(members),
        })
    except Exception:
        pass
