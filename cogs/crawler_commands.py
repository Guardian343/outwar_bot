"""
crawler_commands.py — World map crawler.
Walks all accessible rooms from a starting point, discovers mobs and raids,
updates map_graph.json and mob data.

Usage: !crawl <character_name>
       !crawl-stop
       !crawl-status
"""

import asyncio
import json
import os
import re
from collections import deque
from discord.ext import commands
from datetime import datetime
from yarl import URL
from outwar import logger

SIGIL_URL = URL("https://sigil.outwar.com")
# Shipped seed map (read-only baseline); crawled output lives in database/ so deploys
# (which replace code but exclude database/) never overwrite a real crawl.
MAP_SEED  = os.path.join(os.path.dirname(__file__), "..", "outwar", "map_graph.json")
MAP_PATH  = os.path.join(os.path.dirname(__file__), "..", "database", "map_graph.json")
MOBS_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "crawl_mobs.json")
LOCKED_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "locked_rooms.json")
ZONES_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "room_zones.json")

# Rate limit — requests per second
CRAWL_DELAY    = 0.3   # seconds between moves
PROGRESS_EVERY = 100   # post update every N rooms


def parse_locked_room(raw: str):
    """
    Detect a key-locked room response and extract the required key/item/effect name.
    Trying to enter a locked room returns an HTML swal2 pop-up. Known phrasings:
      - "To enter this room you must be carrying <KEY>."          (carried item)
      - "You must have <EFFECT> cast on you to enter this room."  (active effect/disguise)
      - "...you must have <X> to enter..."                         (generic fallback)
    Returns the required key/effect name (str), or None if it's not a recognised
    lock message (so callers can treat a no-match as a transient failure, NOT a lock).
    """
    if not raw:
        return None
    patterns = [
        r"must be carrying\s+(.+?)\s*[.<]",            # carried item
        r"must have\s+(.+?)\s+cast on you",            # active effect / disguise
        r"must have\s+(.+?)\s+to enter",               # generic "must have X to enter"
        r"you must have\s+(.+?)\s*[.<]",               # generic fallback
    ]
    for pat in patterns:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            key = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if key:
                return key
    return None


def _is_locked_response(raw: str) -> bool:
    """True if the response looks like a genuine lock pop-up (so we can distinguish a
    real lock from a transient bounce even when the specific key phrasing isn't parsed)."""
    if not raw:
        return False
    low = raw.lower()
    return ("to enter this room" in low) or ("must be carrying" in low) or \
           ("must have" in low and "to enter" in low)


def parse_room_payload(raw: str):
    """Parse one ajax_changeroomb.php response into {actual_room, exits, mobs, raw_keys}.
    Shared by the live crawl and the !crawl-test probe so the probe validates the real logic."""
    try:
        data = json.loads(raw)
    except Exception:
        return None
    try:
        actual_room = int(data.get("curRoom", 0))
    except Exception:
        actual_room = None
    # Exits: the room response uses directional keys (north/south/east/west, and
    # sometimes up/down), each holding a destination room id (0/empty = no exit).
    # Older/list forms (exits / roomExits) are still supported as a fallback.
    connected = []
    exits = data.get("exits", []) or data.get("roomExits", [])
    if exits:
        for ed in exits:
            dest = ed.get("room") or ed.get("dest") or ed.get("id") if isinstance(ed, dict) else ed
            if dest is not None:
                try:
                    connected.append(int(dest))
                except (ValueError, TypeError):
                    pass
    else:
        for direction in ("north", "south", "east", "west", "up", "down"):
            v = data.get(direction)
            try:
                rid = int(v)
            except (ValueError, TypeError):
                continue
            if rid > 0:
                connected.append(rid)
    mobs = []
    for mob in data.get("roomDetailsNew", []) or []:
        mid = mob.get("mobId")
        name = mob.get("name") or mob.get("mobName")
        if mid and name:
            try:
                mtype = mob.get("type")
                # Category detection (confirmed from real room JSON, 2026-08-26):
                #   - RAID     → type == 1
                #   - TALK/QUEST → has qmsg (quest message) or noAttack == True
                #   - ATTACK   → everything else (type 2 attackable mobs)
                # NOTE: type does NOT separate attack vs talk (both are type 2) — the
                # qmsg/noAttack flags are what distinguish a quest/talk mob.
                is_raid = (mtype == 1)
                is_quest = bool(mob.get("qmsg")) or mob.get("noAttack") is True
                if is_raid:
                    category = "raid"
                elif is_quest:
                    category = "talk"
                else:
                    category = "attack"
                entry = {
                    "id": int(mid), "name": name, "type": mtype,
                    "raid": is_raid, "category": category,
                }
                # Capture useful extras when present (level/rage aid raid-targeting).
                if mob.get("level") is not None:
                    try: entry["level"] = int(mob["level"])
                    except (ValueError, TypeError): pass
                if mob.get("rage") is not None:
                    try: entry["rage"] = int(mob["rage"])
                    except (ValueError, TypeError): pass
                mobs.append(entry)
            except (ValueError, TypeError):
                pass
    return {"actual_room": actual_room, "exits": connected, "mobs": mobs,
            "zone": (data.get("name") or "").strip() or None,
            "raw_keys": list(data.keys())}


class CrawlerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot     = bot
        self.session = bot.outwar
        self._crawling  = False
        self._stop_flag = False
        self._stats     = {}

    # ------------------------------------------------------------------
    # !crawl <character>
    # ------------------------------------------------------------------

    @commands.command(name="crawl")
    async def crawl(self, ctx, character: str, start: str = "11"):
        """
        Walk all accessible rooms with a character and update map/mob data.
        Usage: !crawl <character> [start_room | here]
          • !crawl Liam            → teleport to room 11, then crawl that zone
          • !crawl Liam 26794      → teleport to room 26794, then crawl that zone
          • !crawl Liam here       → crawl from the character's CURRENT room
                                      (use after positioning via a teleporter)
        crawl_mobs.json accumulates across runs, so sweep each zone to build the full table.
        """
        if self._crawling:
            await ctx.send("⚠️ A crawl is already running. Use `!crawl-stop` to stop it.")
            return

        # Find the character in trustees
        from outwar import database as db
        trustees = db.get_trustees()
        trustee  = next((t for t in trustees if t["name"].lower() == character.lower()), None)
        if not trustee:
            await ctx.send(f"Character `{character}` not found in trustees.")
            return

        suid = trustee.get("suid")
        if not suid:
            await ctx.send(f"`{character}` has no SUID — cannot switch to this account.")
            return

        self._crawling  = True
        self._stop_flag = False
        self._stats = {
            "character":   trustee["name"],
            "suid":        suid,
            "started":     datetime.now(),
            "visited":     0,
            "new_rooms":   0,
            "new_mobs":    0,
            "locked":      0,
            "errors":      0,
        }

        where = "current room" if start.lower() == "here" else f"room {start}"
        await ctx.send(
            f"🗺️ Starting world crawl as **{trustee['name']}**...\n"
            f"Start point: {where}. Updates every {PROGRESS_EVERY} rooms.\n"
            f"Use `!crawl-stop` to stop at any time."
        )

        asyncio.create_task(self._run_crawl(ctx, suid, trustee["name"], start))

    async def _run_crawl(self, ctx, suid: int, char_name: str, start: str = "11"):
        try:
            # Load existing map graph — prefer a prior crawl in database/, else the seed
            base_map = MAP_PATH if os.path.exists(MAP_PATH) else MAP_SEED
            with open(base_map) as f:
                raw = json.load(f)
            # Keys may be strings or ints depending on source
            map_graph = {int(k): [int(x) for x in v] for k, v in raw.items()}

            # Load existing crawl mobs
            if os.path.exists(MOBS_PATH):
                with open(MOBS_PATH) as f:
                    crawl_mobs = json.load(f)
            else:
                crawl_mobs = {}

            # Position the crawl character at the start point.
            # "here" = wherever the character currently is (e.g. moved via a teleporter);
            # otherwise teleport to the given room via world.php.
            if start.lower() != "here":
                await self.session.get_as(f"world.php?room={start}", suid)
                await asyncio.sleep(0.5)

            # Get current room to confirm where we actually are
            raw_loc = await self.session.get_as("ajax_changeroomb.php?room=0&lastroom=0", suid)
            try:
                loc = json.loads(raw_loc)
                start_room = int(loc.get("curRoom", 11))
            except Exception:
                start_room = 11

            await ctx.send(f"📍 Starting from room **{start_room}**")

            # DFS walk — ajax_changeroomb only moves to an ADJACENT room, so we
            # cannot jump to arbitrary rooms. We walk the known map one step at a
            # time and backtrack to the parent when a room's neighbours are done.
            original_rooms = set(map_graph)
            visited   = set()
            new_rooms = 0
            new_mobs  = 0
            # Locked rooms discovered THIS run: {room_id: {"key": name, "from": room}}.
            # Only rooms this character got BOUNCED from (i.e. it lacks the key) land
            # here — an account WITH the key enters fine and learns nothing about the
            # lock, which is exactly why merging across differently-keyed accounts
            # (below, in _save) builds the full picture.
            locked_rooms = {}
            room_zones = {}   # room_id(str) → zone name (e.g. "Holy Dimension")

            def _record(parsed, rid):
                nonlocal new_mobs
                zone = parsed.get("zone")
                if zone:
                    room_zones[str(rid)] = zone   # room → zone name (e.g. "Holy Dimension")
                for m in parsed["mobs"]:
                    key = str(m["id"])
                    if key not in crawl_mobs:
                        crawl_mobs[key] = {"id": m["id"], "name": m["name"],
                                           "type": m["type"], "raid": m["raid"],
                                           "category": m.get("category"),
                                           "level": m.get("level"),
                                           "rage": m.get("rage"),
                                           "zone": zone,
                                           "rooms": [rid]}
                        new_mobs += 1
                        self._stats["new_mobs"] = new_mobs
                    else:
                        if rid not in crawl_mobs[key]["rooms"]:
                            crawl_mobs[key]["rooms"].append(rid)
                        # backfill zone if we didn't have it before
                        if zone and not crawl_mobs[key].get("zone"):
                            crawl_mobs[key]["zone"] = zone
                # keep map connectivity fresh from the live exits
                for dest in parsed["exits"]:
                    if dest not in map_graph.setdefault(rid, []):
                        map_graph[rid].append(dest)
                    if rid not in map_graph.setdefault(dest, []):
                        map_graph[dest].append(rid)

            # We are standing in start_room — record it before walking
            visited.add(start_room)
            self._stats["visited"] = 1
            try:
                raw0 = await self.session.get_as(
                    f"ajax_changeroomb.php?room={start_room}&lastroom={start_room}", suid)
                p0 = parse_room_payload(raw0)
                if p0 and p0["actual_room"] == start_room:
                    _record(p0, start_room)
            except Exception:
                self._stats["errors"] += 1

            stack = [start_room]   # path stack; stack[-1] is the room we're standing in

            while stack and not self._stop_flag:
                current = stack[-1]
                # pick the next unvisited neighbour of the room we're in
                nxt = None
                for n in map_graph.get(current, []):
                    if n not in visited:
                        nxt = n
                        break

                if nxt is None:
                    # all neighbours done — step back to the parent (adjacent)
                    stack.pop()
                    if stack:
                        parent = stack[-1]
                        try:
                            await self.session.get_as(
                                f"ajax_changeroomb.php?room={parent}&lastroom={current}", suid)
                            await asyncio.sleep(CRAWL_DELAY)
                        except Exception:
                            self._stats["errors"] += 1
                    continue

                # step into the neighbour (one adjacent move)
                visited.add(nxt)
                self._stats["visited"] = len(visited)
                try:
                    raw = await self.session.get_as(
                        f"ajax_changeroomb.php?room={nxt}&lastroom={current}", suid)
                    await asyncio.sleep(CRAWL_DELAY)
                    parsed = parse_room_payload(raw)
                except Exception as e:
                    self._stats["errors"] += 1
                    logger.warning("CRAWLER", f"Failed to move from room {current} to {nxt}: {e}")
                    continue

                if parsed is None:
                    # A failed JSON parse is usually the key-locked HTML swal2 pop-up.
                    # Record as locked ONLY if it actually looks like a lock message —
                    # otherwise it's a transient failure (network blip / redirect) and
                    # must NOT be recorded as a permanent lock (that produced false
                    # positives like room 37879, which is actually freely enterable).
                    if _is_locked_response(raw):
                        key = parse_locked_room(raw)  # may be None if phrasing is new
                        locked_rooms[nxt] = {"key": key, "from": current}
                        self._stats["locked"] += 1
                    else:
                        self._stats["errors"] += 1
                    continue
                if parsed["actual_room"] != nxt:
                    # Bounced to a different room than intended. Only treat as LOCKED if
                    # the response is genuinely a lock pop-up; an unconfirmed bounce with
                    # no lock message is a transient issue → error, not a false lock.
                    if _is_locked_response(raw):
                        key = parse_locked_room(raw)
                        locked_rooms[nxt] = {"key": key, "from": current}
                        self._stats["locked"] += 1
                    else:
                        self._stats["errors"] += 1
                    continue

                if nxt not in original_rooms:
                    new_rooms += 1
                    self._stats["new_rooms"] = new_rooms
                _record(parsed, nxt)
                stack.append(nxt)

                if len(visited) % PROGRESS_EVERY == 0:
                    await ctx.send(
                        f"\U0001F5FA\uFE0F **Crawl progress** \u2014 {len(visited):,} rooms visited \u00b7 "
                        f"{new_rooms:,} new rooms \u00b7 {new_mobs:,} new mobs \u00b7 "
                        f"depth {len(stack)} \u00b7 {self._stats['locked']} locked")
                    self._save(map_graph, crawl_mobs, locked_rooms, char_name, room_zones)

            # Final save
            self._save(map_graph, crawl_mobs, locked_rooms, char_name, room_zones)

            elapsed = int((datetime.now() - self._stats["started"]).total_seconds())
            mins, secs = divmod(elapsed, 60)
            hrs,  mins = divmod(mins, 60)
            elapsed_str = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"

            stop_reason = "stopped by user" if self._stop_flag else "complete"

            await ctx.send(
                f"✅ **Crawl {stop_reason}** — **{char_name}**\n"
                f"Rooms visited: **{len(visited):,}** · "
                f"New rooms: **{new_rooms:,}** · "
                f"New mobs: **{new_mobs:,}**\n"
                f"Locked/skipped: {self._stats['locked']} · "
                f"Errors: {self._stats['errors']} · "
                f"Time: {elapsed_str}\n"
                f"Map and mob data saved."
            )

        except Exception as e:
            await ctx.send(f"❌ Crawl failed: {e}")
            logger.error("CRAWLER", f"Fatal crawl error: {e}")
        finally:
            self._crawling = False

    def _save(self, map_graph: dict, crawl_mobs: dict, locked_rooms: dict = None,
              char_name: str = None, room_zones: dict = None):
        """Save map graph and mob data to disk (database/, which survives deploys)."""
        os.makedirs(os.path.dirname(MAP_PATH), exist_ok=True)
        with open(MAP_PATH, "w") as f:
            json.dump({str(k): v for k, v in map_graph.items()}, f)
        with open(MOBS_PATH, "w") as f:
            json.dump(crawl_mobs, f, indent=2)

        # ---- Room → zone name: MERGE across crawls, only ever grow ----
        # The zone name (e.g. "Holy Dimension") comes free in the room JSON ('name'),
        # so we record room → zone. Merge so partial crawls accumulate coverage.
        if room_zones:
            zmerged = {}
            try:
                if os.path.exists(ZONES_PATH):
                    with open(ZONES_PATH) as f:
                        zmerged = json.load(f)
            except Exception:
                zmerged = {}
            zmerged.update({str(k): v for k, v in room_zones.items() if v})
            with open(ZONES_PATH, "w") as f:
                json.dump(zmerged, f, indent=2)

        # ---- Locked-room knowledge: MERGE across crawls, only ever GROW ----
        # Key facts about the world (room → required key) are learned from whichever
        # account gets BOUNCED. An account WITH the key enters fine and learns nothing
        # about the lock — so a keyed account's crawl must NEVER erase a keyless
        # account's key-requirement knowledge. We merge complementary knowledge:
        #   - key:         the required key/item (once known from ANY crawl, kept).
        #   - blocked_for: characters observed to lack the key (couldn't enter).
        # (Reachability — who CAN enter — is implicit in each account's own map; the
        # global table here just records the world fact "room X needs key Y".)
        if locked_rooms is not None:
            merged = {}
            try:
                if os.path.exists(LOCKED_PATH):
                    with open(LOCKED_PATH) as f:
                        merged = json.load(f)
            except Exception:
                merged = {}
            for rid, info in locked_rooms.items():
                rid = str(rid)
                existing = merged.get(rid, {})
                # Keep any key we already knew; fill it in if this crawl learned it.
                key = info.get("key") or existing.get("key")
                blocked = set(existing.get("blocked_for", []))
                if char_name:
                    blocked.add(char_name)
                merged[rid] = {
                    "key":         key,
                    "from":        info.get("from") or existing.get("from"),
                    "blocked_for": sorted(blocked),
                }
            with open(LOCKED_PATH, "w") as f:
                json.dump(merged, f, indent=2)

        # Refresh the live pathfinder so find_path() uses the freshly crawled map
        try:
            from outwar import scraper
            scraper._map_graph = {int(k): v for k, v in map_graph.items()}
        except Exception:
            pass

    # ------------------------------------------------------------------
    # !crawl-stop
    # ------------------------------------------------------------------

    @commands.command(name="locked")
    async def locked_rooms_cmd(self, ctx):
        """Show key-locked rooms discovered by the crawl (room → required key)."""
        try:
            with open(LOCKED_PATH) as f:
                locked = json.load(f)
        except Exception:
            locked = {}
        if not locked:
            await ctx.send("No key-locked rooms recorded yet. Run a crawl first.")
            return
        by_key = {}
        for rid, info in locked.items():
            k = info.get("key") or "Unknown key"
            by_key.setdefault(k, []).append(rid)
        lines = [f"🔒 **Key-locked rooms** ({len(locked)} total)"]
        for k in sorted(by_key):
            rooms = ", ".join(sorted(by_key[k], key=lambda x: int(x)))
            lines.append(f"**{k}**: {rooms}")
        # Chunk on LINE boundaries (never split a room number mid-way like the old
        # fixed-width slice did).
        buf = ""
        for line in lines:
            if len(buf) + len(line) + 1 > 1900:
                await ctx.send(buf)
                buf = ""
            buf += (line + "\n")
        if buf:
            await ctx.send(buf)

    @commands.command(name="locked-clean")
    async def locked_clean_cmd(self, ctx, *room_ids):
        """
        Remove specific rooms from the locked list (false positives), or clear all
        'Unknown key' entries. Usage:
          !locked-clean 37879 39241        → remove those specific rooms
          !locked-clean unknown            → remove ALL 'Unknown key' entries (the old
                                             false positives; genuine unknown-format
                                             locks will re-populate on the next crawl)
        """
        try:
            with open(LOCKED_PATH) as f:
                locked = json.load(f)
        except Exception:
            await ctx.send("No locked-rooms file to clean.")
            return
        before = len(locked)
        if len(room_ids) == 1 and room_ids[0].lower() == "unknown":
            locked = {r: v for r, v in locked.items() if v.get("key")}
            removed = before - len(locked)
            note = "all 'Unknown key' entries"
        else:
            for rid in room_ids:
                locked.pop(str(rid), None)
            removed = before - len(locked)
            note = f"{removed} specified room(s)"
        with open(LOCKED_PATH, "w") as f:
            json.dump(locked, f, indent=2)
        await ctx.send(f"🧹 Removed {note}. Locked rooms: {before} → {len(locked)}.")

    @commands.command(name="crawl-test")
    async def crawl_test(self, ctx, character: str, room: int):
        """Probe a SINGLE room without crawling — validates exits + mob parsing.
        Usage: !crawl-test <character> <room>"""
        from outwar import database as db
        from outwar import scraper
        trustees = db.get_trustees()
        trustee = next((t for t in trustees if t["name"].lower() == character.lower()), None)
        if not trustee or not trustee.get("suid"):
            await ctx.send(f"`{character}` not found in trustees or has no SUID.")
            return
        suid = trustee["suid"]
        raw = await self.session.get_as(f"ajax_changeroomb.php?room={room}&lastroom=0", suid)
        parsed = parse_room_payload(raw)
        if parsed is None:
            await ctx.send(f"⚠️ Room {room}: response was not JSON. First 200 chars:\n```\n{raw[:200]}\n```")
            return

        graph = scraper._load_map_graph()
        known = sorted(graph.get(int(room), []))
        got = sorted(parsed["exits"])
        arrived = parsed["actual_room"] == room
        arrived_str = "✅ yes" if arrived else f"❌ no (landed in {parsed['actual_room']} — likely key-locked)"
        mob_lines = [f"  • {m['id']} — {m['name']}  [{m.get('category','?')}"
                     + (f" L{m['level']}" if m.get('level') else "")
                     + (f" {m['rage']}r" if m.get('rage') else "") + "]"
                     for m in parsed["mobs"][:15]]
        match = "✅ exits match map" if got and set(got) == set(known) else (
                "⚠️ exits differ from map" if got else "❌ no exits parsed")
        msg = (
            f"🔎 **Room {room}** as **{trustee['name']}**"
            + (f"  ·  🗺️ {parsed.get('zone')}" if parsed.get("zone") else "") + "\n"
            f"Arrived: {arrived_str}\n"
            f"Exits parsed: **{len(got)}** {got[:12]}\n"
            f"Map says: **{len(known)}** {known[:12]}  →  {match}\n"
            f"Mobs parsed: **{len(parsed['mobs'])}**\n" + ("\n".join(mob_lines) if mob_lines else "  (none)")
        )
        if not parsed["exits"]:
            msg += f"\n\n⚠️ Exits empty — response keys were: `{parsed['raw_keys']}` (tells me the right exits key)."
        else:
            # Always surface the JSON keys — tells us if a zone/room-name field is present.
            msg += f"\n\n🔑 JSON keys: `{parsed['raw_keys']}`"
        await ctx.send(msg[:1900])

    @commands.command(name="crawl-raw")
    async def crawl_raw(self, ctx, character: str, room: int):
        """
        Dump the RAW mob JSON for one room — reveals the exact fields available on
        each mob (type, icon, category, etc.) so we can map attackable/talkable/raid.
        Usage: !crawl-raw <char> <room>
        """
        from outwar import database as db
        trustees = db.get_trustees()
        trustee = next((t for t in trustees if t["name"].lower() == character.lower()), None)
        if not trustee or not trustee.get("suid"):
            await ctx.send(f"`{character}` not found in trustees or has no SUID.")
            return
        suid = trustee["suid"]
        try:
            raw = await self.session.get_as(f"ajax_changeroomb.php?room={room}&lastroom=0", suid)
            data = json.loads(raw)
            # Show the top-level SCALAR fields (name/description/pic/etc.) — this is
            # where a room/zone name would live (e.g. 'name' for "Holy Dimension").
            scalars = {k: v for k, v in data.items()
                       if isinstance(v, (str, int, float, bool)) or v is None}
            import pprint
            top = pprint.pformat(scalars, width=70)
            details = data.get("roomDetailsNew", [])
            msg = f"**Room {room} — top-level fields:**\n```\n{top[:900]}\n```"
            if details:
                sample = pprint.pformat(details[:1], width=70)
                msg += f"\n**First mob:**\n```\n{sample[:700]}\n```"
            await ctx.send(msg[:1900])
        except Exception as e:
            await ctx.send(f"Error: `{e}`")

    @commands.command(name="zones")
    async def zones_cmd(self, ctx):
        """Show zones discovered by the crawl and how many rooms each contains."""
        try:
            with open(ZONES_PATH) as f:
                zones = json.load(f)
        except Exception:
            zones = {}
        if not zones:
            await ctx.send("No zones recorded yet. Run a crawl with the zone-capture update.")
            return
        counts = {}
        for rid, zname in zones.items():
            counts[zname] = counts.get(zname, 0) + 1
        lines = [f"🗺️ **Zones** ({len(counts)} zones, {len(zones)} rooms mapped)"]
        for z in sorted(counts, key=lambda x: -counts[x]):
            lines.append(f"**{z}**: {counts[z]} rooms")
        buf = ""
        for line in lines:
            if len(buf) + len(line) + 1 > 1900:
                await ctx.send(buf); buf = ""
            buf += line + "\n"
        if buf:
            await ctx.send(buf)

    @commands.command(name="crawl-stop")
    async def crawl_stop(self, ctx):
        """Stop the currently running crawl."""
        if not self._crawling:
            await ctx.send("No crawl is currently running.")
            return
        self._stop_flag = True
        await ctx.send("⏹️ Stopping crawl after current room... please wait.")

    # ------------------------------------------------------------------
    # !crawl-status
    # ------------------------------------------------------------------

    @commands.command(name="crawl-status")
    async def crawl_status(self, ctx):
        """Show the current crawl progress."""
        if not self._crawling:
            await ctx.send("No crawl is currently running.")
            return

        s = self._stats
        elapsed = int((datetime.now() - s["started"]).total_seconds())
        mins, secs = divmod(elapsed, 60)
        hrs,  mins = divmod(mins, 60)
        elapsed_str = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"

        rate = s["visited"] / elapsed if elapsed > 0 else 0

        await ctx.send(
            f"🗺️ **Crawl in progress** — {s['character']}\n"
            f"Rooms visited: **{s['visited']:,}** · "
            f"New rooms: **{s['new_rooms']:,}** · "
            f"New mobs: **{s['new_mobs']:,}**\n"
            f"Locked: {s['locked']} · Errors: {s['errors']} · "
            f"Rate: {rate:.1f} rooms/s · Elapsed: {elapsed_str}"
        )


async def setup(bot):
    await bot.add_cog(CrawlerCommands(bot))
