"""
Server registry — the single source of truth for Outwar's game servers.

Outwar runs multiple game servers. Each has its own host but shares the same
page paths and the same account/session model (rg_sess_id + suid + serverid).
This module centralises the host lookup so the rest of the code can work in
terms of a numeric ``server_id`` instead of hardcoded hosts.

Server IDs (Outwar's own numbering, used in serverid= / support_server= params):
    1 = Sigil  (sigil.outwar.com)   — the primary/default server
    2 = Torax  (torax.outwar.com)

Design note (dual-server rollout): every server-aware entry point defaults to
``DEFAULT_SERVER`` (Sigil) so existing single-server behaviour is unchanged
until callers explicitly start passing server 2. That keeps Phase 1 safe.
"""

DEFAULT_SERVER = 1

# Numeric server id -> game host (https). Paths are shared across servers.
SERVER_HOSTS = {
    1: "https://sigil.outwar.com",
    2: "https://torax.outwar.com",
}

# Numeric server id -> human label (for alerts, channel prefixes, logs).
SERVER_NAMES = {
    1: "Sigil",
    2: "Torax",
}

# Lower-cased label / prefix -> server id. Used to resolve a server from a
# channel name prefix (e.g. "sigil-gods" -> 1, "torax-envoys" -> 2) or from
# user input. Keep every alias someone might type.
SERVER_ALIASES = {
    "sigil":  1,
    "s1":     1,
    "server1": 1,
    "1":      1,
    "torax":  2,
    "s2":     2,
    "server2": 2,
    "2":      2,
}

# The network-wide Rampid host (support.php etc.) is NOT per-game-server, but
# it takes a support_server param that mirrors the server id.
RAMPID_HOST = "http://rampidgaming.outwar.com"


def host_for(server_id: int = DEFAULT_SERVER) -> str:
    """Return the game host (https://…) for a server id. Falls back to Sigil."""
    try:
        return SERVER_HOSTS.get(int(server_id), SERVER_HOSTS[DEFAULT_SERVER])
    except (ValueError, TypeError):
        return SERVER_HOSTS[DEFAULT_SERVER]


def name_for(server_id: int = DEFAULT_SERVER) -> str:
    """Return the human label (e.g. 'Sigil') for a server id."""
    try:
        return SERVER_NAMES.get(int(server_id), SERVER_NAMES[DEFAULT_SERVER])
    except (ValueError, TypeError):
        return SERVER_NAMES[DEFAULT_SERVER]


def login_url_for(server_id: int = DEFAULT_SERVER) -> str:
    """Return the login endpoint for a server id."""
    return f"{host_for(server_id).replace('https://', 'http://')}/index.php"


def resolve_server(text: str, default: int = DEFAULT_SERVER) -> int:
    """Resolve a server id from arbitrary text (a channel name, an alias, or a
    raw id). Matches known aliases anywhere in the text, so 'sigil-gods' -> 1
    and 'torax-envoys' -> 2. Returns ``default`` if nothing matches.
    """
    if text is None:
        return default
    t = str(text).lower()
    # Exact alias (e.g. user typed "torax")
    if t in SERVER_ALIASES:
        return SERVER_ALIASES[t]
    # Prefix / substring match against server labels (channel names)
    for alias, sid in SERVER_ALIASES.items():
        if alias.isalpha() and alias in t:   # only word-aliases, not bare "1"/"2"
            return sid
    return default


def server_from_channel(channel) -> int:
    """Resolve the server id from a Discord channel by its name prefix
    (sigil-* / torax-*). Falls back to DEFAULT_SERVER. Accepts a channel object
    or a plain name string.
    """
    name = getattr(channel, "name", None) or str(channel or "")
    return resolve_server(name, DEFAULT_SERVER)
