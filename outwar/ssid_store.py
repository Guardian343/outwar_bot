"""
ssid_store.py

Encrypted storage for RGA session IDs (rg_sess_id).

SECURITY NOTES — read before trusting this with anything valuable
-----------------------------------------------------------------
- SSIDs are encrypted at rest with Fernet (AES-128-CBC + HMAC). If the stored
  file leaks on its own, the SSIDs are unreadable without the key.
- The key lives beside the bot (env var OWMOD_SSID_KEY, or a key file the bot
  generates). This means encryption protects a LEAKED FILE — it does NOT protect
  against someone who can access the bot's machine/process, since they can reach
  both the file and the key. That's an inherent limit of a bot that must decrypt
  SSIDs to use them, not a flaw to fix.
- Ownership is by Discord user id. Admins/owner can read any entry (by design).

Storage shape (encrypted blob decrypts to):
    { discord_id: {"ssid": str, "rga": str, "server_id": int,
                   "added": iso, "last_ok": iso, "suid": str} }
"""

import os
import json
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

# Files live alongside the other bot database files.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_DIR = os.path.join(_BASE_DIR, "database")
_STORE_PATH = os.path.join(_DB_DIR, "ssids.enc")
_KEY_PATH = os.path.join(_DB_DIR, ".ssid_key")   # generated if no env key set


def _get_key() -> bytes:
    """
    Resolve the encryption key. Priority:
      1. OWMOD_SSID_KEY env var (preferred — keep it in .env, out of the DB dir)
      2. a generated key file (.ssid_key) — created once, chmod 600 where possible

    Using the env var is safer: it keeps the key OUT of the same folder as the
    encrypted store, so a folder/backup leak doesn't hand over both halves.
    """
    env = os.getenv("OWMOD_SSID_KEY")
    if env:
        return env.encode() if isinstance(env, str) else env

    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as f:
            return f.read().strip()

    # First run with no env key — generate one and lock the file down.
    os.makedirs(_DB_DIR, exist_ok=True)
    key = Fernet.generate_key()
    with open(_KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(_KEY_PATH, 0o600)   # best effort; a no-op on some Windows setups
    except Exception:
        pass
    return key


def _fernet() -> Fernet:
    return Fernet(_get_key())


def _load() -> dict:
    """Decrypt and return the full store, or {} if absent/unreadable."""
    if not os.path.exists(_STORE_PATH):
        return {}
    try:
        with open(_STORE_PATH, "rb") as f:
            blob = f.read().strip()
        if not blob:
            return {}
        raw = _fernet().decrypt(blob)
        return json.loads(raw.decode("utf-8"))
    except (InvalidToken, json.JSONDecodeError, OSError):
        # Wrong key or corrupt file — never crash the bot over it.
        return {}


def _save(data: dict):
    os.makedirs(_DB_DIR, exist_ok=True)
    raw = json.dumps(data).encode("utf-8")
    blob = _fernet().encrypt(raw)
    tmp = _STORE_PATH + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, _STORE_PATH)   # atomic — never leave a half-written store
    try:
        os.chmod(_STORE_PATH, 0o600)
    except Exception:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- public API ------------------------------------------------------------

def set_ssid(discord_id: int, ssid: str, rga: str, server_id: int = 1,
             suid: str = "") -> dict:
    """Store (or replace) the SSID for one Discord user. Returns the entry."""
    data = _load()
    entry = {
        "ssid": ssid.strip(),
        "rga": rga,
        "server_id": int(server_id),
        "added": _now(),
        "last_ok": _now(),
        "suid": str(suid or ""),
    }
    data[str(discord_id)] = entry
    _save(data)
    return entry


def get_ssid(discord_id: int) -> dict:
    """The entry for one Discord user, or None."""
    return _load().get(str(discord_id))


def remove_ssid(discord_id: int) -> bool:
    """Delete a user's entry. Returns False if there was nothing to remove."""
    data = _load()
    if str(discord_id) in data:
        del data[str(discord_id)]
        _save(data)
        return True
    return False


def all_entries() -> dict:
    """The whole store {discord_id: entry}. Owner/admin use only — enforce upstream."""
    return _load()


def mark_ok(discord_id: int):
    """Stamp last_ok when an SSID is confirmed live (used by the expiry poll)."""
    data = _load()
    e = data.get(str(discord_id))
    if e:
        e["last_ok"] = _now()
        _save(data)


# --- roster fetch / validation ---------------------------------------------
# An SSID authenticates via the rg_sess_id param alone (no cookies), so we fetch
# accounts.php with a CLEAN session — never the bot's own cookies. A live SSID
# returns a roster of characters; a dead one returns an empty/login page. That
# empty-roster signal is exactly how expiry is detected.

_SERVER_HOST = {1: "https://sigil.outwar.com", 2: "https://torax.outwar.com"}


def parse_roster(html: str) -> list:
    """
    Parse accounts.php into [{"suid": str, "name": str}].

    Mirrors the userscript: walk rows in document order, take the suid= anchor
    (the PLAY! link), read the character name from the row's cells, and STOP at
    the "Trustee Access" divider so shared/trustee characters don't bloat the
    roster.
    """
    import re
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    out, seen = [], set()
    for tr in soup.find_all("tr"):
        txt = re.sub(r"\s+", " ", tr.get_text() or "").strip()
        if re.search(r"trustee access", txt, re.I):
            break   # everything after this divider is shared-in, not owned
        anchor = None
        for a in tr.find_all("a"):
            if re.search(r"suid=\d+", a.get("href", ""), re.I):
                anchor = a
                break
        if not anchor:
            continue
        m = re.search(r"suid=(\d+)", anchor.get("href", ""), re.I)
        if not m:
            continue
        suid = m.group(1)
        if suid in seen:
            continue
        seen.add(suid)
        tds = [re.sub(r"\s+", " ", td.get_text() or "").strip()
               for td in tr.find_all("td")]
        name = next((t for t in tds if t and not re.fullmatch(r"play!?", t, re.I)
                     and not t.isdigit()), (anchor.get_text() or "").strip())
        out.append({"suid": suid, "name": name})
    return out


async def fetch_roster(ssid: str, server_id: int = 1) -> list:
    """
    Fetch an RGA's character roster using its SSID, with a CLEAN cookieless
    session. Returns [] for a dead/expired SSID (empty roster = expired).
    """
    import aiohttp
    host = _SERVER_HOST.get(int(server_id), _SERVER_HOST[1])
    url = f"{host}/accounts.php?ac_serverid={int(server_id)}&rg_sess_id={ssid}"
    try:
        # Fresh session, no cookie jar shared with the bot — the param is the
        # only credential, exactly like the standalone tools.
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                html = await r.text()
        return parse_roster(html)
    except Exception:
        return []


async def validate_ssid(ssid: str, server_id: int = 1):
    """
    Check an SSID by fetching its roster.
    Returns (ok: bool, rga_name: str, roster: list).
    rga_name is the first/primary character's name (best-effort RGA label).
    """
    roster = await fetch_roster(ssid, server_id)
    if not roster:
        return False, "", []
    return True, roster[0]["name"], roster


# --- SSID-authenticated per-character requests ------------------------------
# Outwar uses ONE RGA-wide session (rg_sess_id); `suid` names which character a
# request is for. Every request just carries rg_sess_id + suid + serverid as
# params, cookielessly — no separate "switch" step. This is how we act as each
# character on a stored RGA without the bot being logged into it.

def _sess_url(path: str, ssid: str, suid, server_id: int, extra: str = "") -> str:
    host = _SERVER_HOST.get(int(server_id), _SERVER_HOST[1])
    sep = "&" if "?" in path else "?"
    url = (f"{host}/{path.lstrip('/')}{sep}rg_sess_id={ssid}"
           f"&suid={suid}&serverid={int(server_id)}")
    if extra:
        url += "&" + extra.lstrip("&")
    return url


async def sess_get(path: str, ssid: str, suid, server_id: int = 1) -> str:
    """GET a path as a specific character on the RGA (cookieless, SSID-auth)."""
    import aiohttp
    url = _sess_url(path, ssid, suid, server_id)
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                return await r.text()
    except Exception:
        return ""


async def sess_post(path: str, data: dict, ssid: str, suid, server_id: int = 1) -> str:
    """POST a form as a specific character on the RGA (cookieless, SSID-auth)."""
    import aiohttp
    url = _sess_url(path, ssid, suid, server_id)
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, data=data,
                                 timeout=aiohttp.ClientTimeout(total=30)) as r:
                return await r.text()
    except Exception:
        return ""
