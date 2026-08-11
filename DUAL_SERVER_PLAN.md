# Dual-Server Support (Sigil + Torax) — Build Plan & Progress

**Decision:** ONE server-aware bot (not two instances). Rationale: two bot
processes on one Pi reintroduce the session-flood risk we fixed by consolidating
to one supervisor service. One server-aware bot keeps the clean single-process
model; the cost is threading server context through the code (worth it as a
foundation for the session-ID work).

**Servers:** `1 = Sigil` (sigil.outwar.com, default), `2 = Torax`
(torax.outwar.com). Liam does EVERYTHING on both (raids, caps, envoys, gods,
bosses).

**Channel model (Liam):** channels renamed to server-prefixed — `sigil-gods`,
`sigil-envoys`, `torax-gods`, `torax-envoys`, etc. The bot RESOLVES SERVER FROM
THE CHANNEL a command runs in (channel = context, no flags). Alerts post to the
matching server-prefixed channel.

**Safety principle:** every server-aware entry point DEFAULTS to server 1
(Sigil). So the code becomes *capable* of two servers while behaving exactly as
before until callers explicitly pass server 2. Nothing about current Sigil
behaviour changes until Phases 3–5 wire the second server in.

---

## PHASE 1 — Server-aware foundation ✅ DONE (2026-08-10, safe/non-breaking)

- **NEW `outwar/servers.py`** — single source of truth: `SERVER_HOSTS`,
  `SERVER_NAMES`, `SERVER_ALIASES`, `RAMPID_HOST`, and helpers `host_for()`,
  `name_for()`, `login_url_for()`, `resolve_server()`, `server_from_channel()`.
  `resolve_server` maps a channel name/alias → id (sigil-gods→1, torax-envoys→2,
  unprefixed→1). TESTED.
- **`outwar/session.py`** — `BASE_URL`/`LOGIN_URL` now derive from the registry
  (back-compat, still Sigil). `request_result`, `get`, `get_as`, `post`,
  `post_as`, `get_sse` all take an optional `server_id=DEFAULT_SERVER` and build
  the URL via `host_for(server_id)`. Bot's own LOGIN stays Sigil (its account is
  a Sigil account; Torax is reached via serverid/ow_userid, not a 2nd login).
- **`outwar/ssid_store.py`** — its `_SERVER_HOST`/`_RAMPID_HOST` now come from
  the central registry (was already dual-server capable). Unified.
- **`cogs/ssid_commands.py`** — its duplicate `SERVER_HOST`/`DEFAULT_SERVER` now
  come from the registry.
- **VERIFIED:** all changed files parse; 11/12 cogs load (the 12th,
  crawler_commands, fails identically on ORIGINAL code — a test-harness quirk,
  not this change). No behaviour change: everything still defaults to Sigil.

## PHASE 2 — Channel↔server resolution (needs Liam's renamed channels)

- Add a helper on the bot/cogs: given `ctx` (a command) or an alert type, resolve
  the server id from the channel name via `servers.server_from_channel`.
- Extend the alert-channel config to be per-server: today `get_alert_channel(type)`
  returns one channel; it needs `get_alert_channel(type, server_id)` →
  the `sigil-<type>` or `torax-<type>` channel. Store both.
- **BLOCKED ON:** Liam renaming channels to `sigil-*` / `torax-*` and telling the
  bot their IDs (or the bot auto-discovers by name prefix).

## PHASE 3 — Monitors poll BOTH servers

- `cogs/god_monitor.py`: the god/boss/envoy poll loops currently poll Sigil only
  (hardcoded host + `ow_userid` cookie against `_SIGIL_URL`). Make each poll run
  once per active server, passing `server_id`; post alerts to that server's
  channel. The cookie-jar `ow_userid` set must target the correct host
  (`host_for(server_id)`), not hardcoded Sigil.
- Envoy rollover/auto-dump, leaderboards, countdown alerts → per server.
- Boss spawns, god spawns → per server, per server's channel.
- Embed "View »" links must use `host_for(server_id)`.
- **DAILY SUMMARY must be built PER SERVER (Liam flagged).** `_post_daily_summary`
  is a COMPOSITE — its three parts are all server-specific: (1) boss section
  (`crew_bossspawns` fetch), (2) yesterday's focused-crew drops, (3) per-crew
  summary (`summary_crews` loop + boss-raiding status). So the summary loop must
  run ONCE PER ACTIVE SERVER: fetch that server's bosses via
  `session.get("crew_bossspawns", server_id=N)`, read that server's focus drops +
  summary crews, and post to that server's summary channel (`sigil-chat` /
  `torax-chat`). Same format, different data per server. The only genuinely shared
  bits would be non-game text; everything game-derived is per-server.

## PHASE 4 — Commands use the invoking channel's server

- `!pcaps`, `!raid`, boss raids, `!envoy *`, character/stat commands, etc.:
  resolve `server_id` from `ctx.channel` and pass it to every `session.get*` /
  `get_as` call and every profile/link URL built.
- The per-account cookie-selection (`ow_userid` against SIGIL_URL) in the cogs
  must switch to `host_for(server_id)`.
- Crew/trustee lookups may need a server field (a crew name can exist on both
  servers) — see data model note below.

## PHASE 5 — Config, data model, cleanup

- Settings: which servers are active; per-server alert-channel map. NOTE alert
  channels are stored by ID (permanent across renames) — renaming channels does
  NOT break settings. Per-server storage likely `alert_channel_<type>_<server>`
  (e.g. alert_channel_summary_sigil / _torax) or a nested map.
- **Data model — several things are implicitly Sigil today and need a server
  dimension when Torax comes online:**
  - **trustees/SSIDs**: an account/char is on a specific server. Trustees need a
    `server_id` tag so crew lookups + `get_as` target the right host. Audit
    `trustees.json` / ssids. (A crew name can exist on BOTH servers.)
  - **summary_crews** (`db.get_summary_crews`): which crews appear in the daily
    summary — must be per-server (Sigil summary vs Torax summary list differ).
  - **focused crews** (⭐) + **focus drops** (`db.get_focus_drops`): focused-crew
    tracking and yesterday's focus-drop rollup are per-server. The daily summary's
    focus-drops line and the boss/crew status must read the right server's data.
  - Migration: existing data is Sigil — tag it server_id=1 on first run so nothing
    is lost, then let Torax data be added with server_id=2.
- Sweep remaining hardcoded `sigil.outwar.com` (see list below) and route through
  `host_for(server_id)`.

---

## Remaining hardcoded hosts to route through the registry (Phases 3–5)

These are intentionally LEFT for the phased work because they're entangled with
per-request character-selection cookies and alert context — changing them blind
would risk current Sigil behaviour. Grep list captured 2026-08-10:

- `outwar/scraper.py:233` — world URL (per-account, needs server_id)
- `outwar/session.py:122` — cookie_jar.filter_cookies("…sigil…") in login
  (login stays Sigil, but review when Torax login/session considered)
- `cogs/admin_commands.py` — BASE_URL const + 2× SIGIL_URL cookie sets
- `cogs/auth.py` — sigil URL + home fetch (RGA-name path)
- `cogs/boss_commands.py` — BASE_URL const
- `cogs/boss_raid_commands.py` — SIGIL_URL + 3× profile/boss link URLs
- `cogs/character_commands.py` — BASE_URL + SIGIL_URL + cookie set
- `cogs/crawler_commands.py` — SIGIL_URL
- `cogs/god_monitor.py` — BASE_URL + _SIGIL_URL + cookie set + 4× embed links
- `cogs/misc_commands.py` — BASE_URL const + 2× SIGIL_URL cookie sets (pcaps etc.)
- `cogs/raid_commands.py` — SIGIL_URL + SSE url + referer + 4× embed links
- `cogs/utility_commands.py` — SIGIL_URL

Pattern for each: replace the hardcoded host with `host_for(server_id)` where
`server_id` is resolved from the invoking channel (commands) or the poll's server
(monitors), and set the `ow_userid` cookie against that same host.

---

## Prep Liam can do while away (no code)

- Rename Discord channels to `sigil-*` and `torax-*` (gods, envoys, primewatcher,
  bosses, drops, etc.). This unblocks Phase 2.
- Decide: auto-discover channels by name prefix, or configure IDs explicitly?
  (Auto-discover by prefix is less config and matches the channel-as-context
  model — recommended.)
