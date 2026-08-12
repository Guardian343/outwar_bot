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

## PHASE 3 — Monitors poll BOTH servers (⚠️ hardest phase — do in SAFE SUB-PHASES)

### ⚠️⚠️ CRITICAL TORAX-AUTH FINDINGS (2026-08-12) — READ BEFORE TOUCHING TORAX ⚠️⚠️

A morning of live debugging established how Torax access ACTUALLY works. The earlier
per-request-param approach was WRONG and briefly broke live Sigil monitoring. The
facts, now proven:

1. **The account's "current server" is SINGULAR and lives on Outwar's side.** The
   `myaccount.php?ac_serverid=N` switch is stateful and ACCOUNT-LEVEL, not per-request.
   When the bot logs in, it comes up as whatever server the account was last switched
   to. This morning the bot logged in as suid 933209 (Torax) because earlier probing/
   scanning had left the account switched to Torax — which BROKE Sigil (bot was acting
   as its Torax identity on the Sigil host → all Sigil pages returned the ~41KB
   not-logged-in fallback, no alerts fired).

2. **The bot account (LoDRaid) has a DIFFERENT suid per server:**
   - Sigil: **1157932**  ·  Torax: **933209**
   (Same rg_sess_id works on both — it's the SUID that differs. Sending the Sigil suid
   to Torax yields the fallback page; this was the whole Torax-read failure.)

3. **The single-login / single-cookie-jar model can only be on ONE server at a time.**
   Confirmed by Liam: same RGA in ONE browser can't do both servers; but **Firefox on
   Torax + Chrome on Sigil, same account, works concurrently.** The difference is the
   COOKIE JAR — two isolated jars = two independent sessions = both servers at once.
   One shared jar = the switch overwrites itself = one server at a time.

4. **RECOVERY PROCEDURE if the bot is stuck on the wrong server** (login shows the
   wrong suid, Sigil alerts dead): use `!get-sessid` to get the bot's SSID link, open
   it, hit `https://sigil.outwar.com/myaccount.php?ac_serverid=1` to switch the account
   back to Sigil, restart the bot. Login then picks up 1157932 and Sigil is healthy.
   (Proven this morning — no code needed.)

### ✅ THE CORRECT ARCHITECTURE (to build): TWO SESSIONS, ONE PER SERVER ("two browsers")

The bot must hold **two independent session objects, each with its OWN aiohttp cookie
jar** — the code equivalent of Chrome-on-Sigil + Firefox-on-Torax:
- **Sigil session** = the bot's CURRENT session, untouched. Own jar, suid 1157932,
  on Sigil. Works exactly as today. NEVER modified by Torax work (so Sigil can't break
  again).
- **Torax session** = a NEW second session object. Own isolated jar, logs in with the
  SAME credentials, switches ITSELF to Torax (`ac_serverid=2`), holds Torax's cookies +
  suid 933209. Because its jar is isolated, switching it to Torax does NOT touch the
  Sigil session — just like Firefox switching doesn't affect Chrome.
- Monitors pick the session by server: Sigil monitor → Sigil session; Torax monitor →
  Torax session. Fully concurrent, ONE login per server (Liam's requirement), no
  interference.

This REPLACES the failed `get_server`/cookieless-param approach for real Torax reads.
(`get_server` currently routes Sigil→cookie path, Torax→cookieless — the Torax branch
is the part that doesn't truly authenticate and must be swapped for the 2nd session.)

**BUILD STEPS (do DELIBERATELY, not live-hacked — this morning showed live session
hacking knocks out Sigil):**
1. A second session instance (reuse OutwarSession with its own jar, or a subclass) that
   logs in, switches to Torax, and re-reads suid 933209. Add keep-alive / re-login on
   expiry, and a switch-to-Torax-on-init.
2. PROVE it on a probe FIRST: the Torax session fetches `primegods` and returns the
   REAL ~92KB / 43-row page (NOT the 41KB fallback) before wiring anything live.
3. Wire the Torax monitor (`_poll_gods_for(2)` etc.) to use the Torax session.
4. Keep the Sigil session 100% untouched throughout. Test with active_servers=[1] first
   (Sigil-only, must be identical), then [1,2].

**Data storage is ALREADY split and does NOT need cloning** — per-server keys
(`god_state_2`, `excluded_accounts_by_server`, trustee `server_id` tags, per-server
alert channels) all exist. One DB, keyed by server. The blocker was only ever the live
SESSION, never the storage.

Rationale for sub-phasing: the god monitor's state (`_last_gods`, caches) loads
from the DB (god_state/boss_state/envoy_state) and threads through 3 change-
processors. Converting to per-server touches the monitor AND the DB state layer —
if Sigil's "was-spawned" state mixes with Torax's, you get FALSE spawn/despawn
alerts. So: convert one subsystem at a time, each behaving identically on Sigil-
only first, then enable Torax and watch. NEVER a single big sweep.

**MASTER SWITCH:** `db.get_active_servers()` (defaults to [1] = Sigil only). Monitors
loop over this. Torax stays dark until `db.set_active_servers([1,2])`. So all the
per-server machinery can land + be tested while the bot still only polls Sigil.

**SUB-PHASE 3a — server-aware DB state + master switch ✅ DONE 2026-08-11 (safe):**
- `db.get_active_servers()` / `set_active_servers()` — defaults [1].
- god/envoy/boss state functions take `server_id=1`: server 1 uses the LEGACY bare
  key (`god_state`) so existing Sigil data is untouched; server 2+ uses suffixed
  (`god_state_2`). Fully separate, tested. god_monitor's existing no-arg calls →
  default Sigil → zero behaviour change.

**SUB-PHASE 3b — god poll per server ✅ DONE 2026-08-11 (built while Liam asleep; SAFE, Sigil-identical):**
- NEW primitive `session.get_server(path, server_id)` — per-request cookieless fetch
  using the bot's OWN rg_sess_id + suid + serverid (via ssid_store.sess_get). This
  is the concurrency-safe multi-server fetch proven by !server-probe. Falls back to
  cookie get() pre-login.
- `_poll_gods()` now loops `db.get_active_servers()` → `_poll_gods_for(server_id)`:
  fetches primegods via get_server, per-server caches (`_gods_cache[sid]`), per-server
  state. Dashboard god publish + envoy rollover guarded to server 1 (Sigil) for now.
- `_process_god_changes(gods, server_id)`: per-server state (get/save_god_state(sid)),
  posts to `_get_alert_channel("gods", server_id)`, embed links use host_for(server_id).
- `_post_god_drops(channel, god, server_id)` + `get_sse(..., server_id)` server-aware
  (SSE appends bot's rg_sess_id/suid/serverid for non-Sigil).
- `_process_envoy_changes(envoys, server_id)` — accepts server_id but early-returns for
  non-Sigil (full envoy conversion is 3d). Caches made dict-consistent.
- VERIFIED: parses + loads; with active_servers=[1] behaves EXACTLY as before (legacy
  god_state key, Sigil-only loop). Torax state separate, only touched if enabled.
- ⚠️ **3b's Torax path (get_server cookieless) does NOT actually authenticate on Torax**
  — see the CRITICAL TORAX-AUTH FINDINGS above. The god-poll per-server STRUCTURE is
  correct and Sigil-safe, but the Torax fetch must be swapped to use the 2nd (Torax)
  session once that's built. Sigil path (cookie) is fine and proven.
- `get_server` was fixed to route server 1 → cookie path (Sigil, rock-solid) and only
  non-default → cookieless. This is why Sigil is safe; the cookieless Torax branch is
  the placeholder to be replaced by the 2nd-session fetch.
**SUB-PHASE 3c — boss poll per server.** Same pattern for bosses.
**SUB-PHASE 3d — envoy per server** (rollover/auto-dump/leaderboards/countdown).
**SUB-PHASE 3e — daily summary per server.** `_post_daily_summary` is a COMPOSITE
(boss section + yesterday's focus drops + per-crew summary + boss-raid status) —
all server-specific. Run the summary loop ONCE PER ACTIVE SERVER, fetch that
server's bosses via `session.get("crew_bossspawns", server_id=N)`, read that
server's focus drops + summary crews, post to that server's summary channel.

## PHASE 4 — Commands use the invoking channel's server

- `!pcaps`, `!raid`, boss raids, `!envoy *`, character/stat commands, etc.:
  resolve `server_id` from `ctx.channel` and pass it to every `session.get*` /
  `get_as` call and every profile/link URL built.
- The per-account cookie-selection (`ow_userid` against SIGIL_URL) in the cogs
  must switch to `host_for(server_id)`.
- Crew/trustee lookups may need a server field (a crew name can exist on both
  servers) — see data model note below.

## PHASE 5 — Config, data model, cleanup

**TRUSTEE SERVER-TAGGING — PARTLY DONE 2026-08-11 ✅ (bot side):**
- Confirmed (Liam): trustees are PER-SERVER on Outwar — Sigil trustees show on
  Sigil's myaccount, Torax on Torax's (separate pages). So the server tag comes
  from WHICH server you scan, not page-parsing.
- `db.get_trustees(server_id=None)` — migration-on-read: untagged trustee →
  server 1 (Sigil). `get_trustees_by_crew(crew, server_id=None)` too.
- `db.save_trustees_for_server(sid, list)` — replaces ONLY that server's trustees,
  PRESERVES the others (a Sigil re-scan must not wipe Torax). Tags the batch.
- `db.trustee_counts_by_server()` → {sid: count} for the dashboard.
- `!scan-trustees` is server-aware: scans the myaccount of the channel's server
  (default Sigil), enriches via that host, merge-saves. To populate Torax: run
  `!scan-trustees` in a torax-prefixed channel.
- Dashboard publish (auth.publish_settings_meta + status_writer): now writes
  `channels_by_server` and `trustees` {total, by_server, server_names} to
  status.json's settings_meta.
- ⚠️ **DASHBOARD FRONT-END still needed (separate `deathbot_supervisor` repo):**
  the data is now PUBLISHED to status.json, but rendering "N total / N Sigil /
  N Torax trustees" and the per-server alert-channel list must be done in the
  supervisor repo's dashboard HTML/JS. Not in this repo — do when we work there.

- Settings: which servers are active; per-server alert-channel map. DONE via
  Phase 2 (alert_channel_<type>_<server> keys, ID-based).
- **Remaining data model:** summary_crews, focused crews (⭐), focus_drops still
  implicitly Sigil — need per-server dimension (see Phase 3 summary note).
- Sweep remaining hardcoded `sigil.outwar.com` (list below) via host_for(server_id).

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
