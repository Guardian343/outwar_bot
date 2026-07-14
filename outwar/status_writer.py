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
    bosses: iterable of Boss objects (name, full_name, spawned, hp, hp_pct).
    Publishes the current boss roster with live HP (absolute + %).
    """
    try:
        payload = []
        for b in bosses:
            payload.append({
                "name": getattr(b, "name", "") or getattr(b, "full_name", ""),
                "full_name": getattr(b, "full_name", ""),
                "spawned": bool(getattr(b, "spawned", False)),
                "hp": int(getattr(b, "hp", 0) or 0),
                "hp_pct": float(getattr(b, "hp_pct", 0.0) or 0.0),
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
