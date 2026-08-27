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


def _lock_system(key: str):
    """Map a lock-key name to its dungeon 'system' + a sort key, so the flat
    96-room list collapses into the handful of keyed structures it really is.
    Pattern-matched against real key names; anything unrecognised falls to
    'Other' (safe — nothing is lost, it's just ungrouped)."""
    k = (key or "Unknown key")
    kl = k.lower()
    if kl.startswith("tower key floor"):
        m = re.search(r"floor\s+(\d+)", kl)
        return ("Tower", int(m.group(1)) if m else 0)
    if kl.startswith("text of"):
        return ("The Texts", k)
    if "grind door" in kl or "lost grind room" in kl:
        m = re.search(r"(\d+)", kl)
        return ("Grind", (0 if "grind door" in kl else 1, int(m.group(1)) if m else 0))
    if kl.startswith("sanctum specialty"):
        m = re.search(r"(\d+)", kl)
        return ("Sanctum Specialty", int(m.group(1)) if m else 0)
    if kl.startswith("ward of"):
        return ("Wards", k)
    return ("Other", k)


def _add_wrapped_field(embed, name, lines, inline=False):
    """Add lines to an embed field, splitting into continuation fields when the
    1024-char Discord field cap would be exceeded. Never splits a line mid-way."""
    buf = ""
    first = True
    for line in lines:
        add = (line + "\n")
        if len(buf) + len(add) > 1024:
            embed.add_field(name=(name if first else f"{name} (cont.)"),
                            value=buf.rstrip("\n") or "\u200b", inline=inline)
            first = False
            buf = ""
        buf += add
    if buf.strip():
        embed.add_field(name=(name if first else f"{name} (cont.)"),
                        value=buf.rstrip("\n"), inline=inline)


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
            f"🗺️ Starting world crawl as **{trustee['name']}** from {where}. "
            f"Live progress below — use `!crawl-stop` to stop at any time."
        )

        asyncio.create_task(self._run_crawl(ctx, suid, trustee["name"], start))

    def _build_progress_embed(self, char_name, visited, new_rooms, new_mobs,
                              counters, elapsed_str, done=False, stopped=False):
        """Build the live crawl embed. Shows total (accumulated in the DB) with the
        new-this-run count in parentheses, so a re-crawl of an already-mapped world
        still visibly ticks along instead of looking stuck on zeros."""
        import discord
        # Totals from the accumulated database.
        try:
            with open(MOBS_PATH) as f:
                all_mobs = json.load(f)
        except Exception:
            all_mobs = {}
        try:
            with open(ZONES_PATH) as f:
                all_zones = json.load(f)
        except Exception:
            all_zones = {}
        total_mobs = len(all_mobs)
        total_zones = len(set(all_zones.values())) if all_zones else 0
        total_npcs = sum(1 for m in all_mobs.values() if m.get("category") == "talk")
        total_raids = sum(1 for m in all_mobs.values() if m.get("category") == "raid")
        total_rooms = len(set(all_zones.keys())) if all_zones else visited

        if done:
            title = f"✅ Crawl complete with {char_name}" if not stopped \
                else f"⏹️ Crawl stopped with {char_name}"
            colour = discord.Color.green() if not stopped else discord.Color.orange()
        else:
            title = f"🗺️ Crawling with {char_name}"
            colour = discord.Color.blurple()

        embed = discord.Embed(title=title, color=colour)

        def line(total, new):
            return f"**{total:,}**" + (f"  (+{new:,})" if new else "")

        embed.add_field(name="Rooms explored", value=f"**{visited:,}**", inline=True)
        embed.add_field(name="New rooms", value=f"**{new_rooms:,}**", inline=True)
        embed.add_field(name="Zones", value=line(total_zones, counters["new_zones"]), inline=True)
        embed.add_field(name="Mobs", value=line(total_mobs, new_mobs), inline=True)
        embed.add_field(name="NPCs (talkable)", value=line(total_npcs, counters["new_npcs"]), inline=True)
        embed.add_field(name="Raids", value=line(total_raids, counters["new_raids"]), inline=True)
        footer = f"Locked/skipped: {self._stats['locked']} · Errors: {self._stats['errors']} · {elapsed_str}"
        embed.set_footer(text=footer)
        return embed

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
            # Discovery counters for the live embed. "new" = discovered THIS run;
            # totals are read from the accumulated database at render time.
            seen_zones = set()
            counters = {"new_zones": 0, "new_npcs": 0, "new_raids": 0}

            def _record(parsed, rid):
                nonlocal new_mobs
                zone = parsed.get("zone")
                if zone:
                    if zone not in seen_zones:
                        seen_zones.add(zone)
                        counters["new_zones"] += 1
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
                        # Tally the new mob by category for the embed.
                        cat = m.get("category")
                        if cat == "raid":
                            counters["new_raids"] += 1
                        elif cat == "talk":
                            counters["new_npcs"] += 1
                    else:
                        if rid not in crawl_mobs[key]["rooms"]:
                            crawl_mobs[key]["rooms"].append(rid)
                        # Backfill/refresh enrichment fields on re-visit so that a
                        # crawl with newer code upgrades OLD records (which may lack
                        # category/level/rage/zone) instead of leaving them stale.
                        rec = crawl_mobs[key]
                        if zone and not rec.get("zone"):
                            rec["zone"] = zone
                        if m.get("category") and not rec.get("category"):
                            rec["category"] = m["category"]
                        if m.get("level") and not rec.get("level"):
                            rec["level"] = m["level"]
                        if m.get("rage") and not rec.get("rage"):
                            rec["rage"] = m["rage"]
                        # Ensure raid flag reflects the (now-known) category.
                        if m.get("category") == "raid":
                            rec["raid"] = True
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

            def _elapsed_str():
                elapsed = int((datetime.now() - self._stats["started"]).total_seconds())
                m, s = divmod(elapsed, 60)
                h, m = divmod(m, 60)
                return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"

            # Single self-editing progress embed — replaces the old per-100-rooms flood.
            progress_msg = await ctx.send(embed=self._build_progress_embed(
                char_name, len(visited), new_rooms, new_mobs, counters, _elapsed_str()))

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
                    try:
                        await progress_msg.edit(embed=self._build_progress_embed(
                            char_name, len(visited), new_rooms, new_mobs,
                            counters, _elapsed_str()))
                    except Exception:
                        pass   # a transient edit failure shouldn't derail the crawl
                    self._save(map_graph, crawl_mobs, locked_rooms, char_name, room_zones)

            # Final save
            self._save(map_graph, crawl_mobs, locked_rooms, char_name, room_zones)

            # Flip the SAME live embed to its completed/stopped state (no new message).
            try:
                await progress_msg.edit(embed=self._build_progress_embed(
                    char_name, len(visited), new_rooms, new_mobs, counters,
                    _elapsed_str(), done=True, stopped=self._stop_flag))
            except Exception:
                # Fallback: if the original message is gone, post a fresh summary.
                await ctx.send(embed=self._build_progress_embed(
                    char_name, len(visited), new_rooms, new_mobs, counters,
                    _elapsed_str(), done=True, stopped=self._stop_flag))

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
    async def locked_rooms_cmd(self, ctx, *, system: str = ""):
        """
        Key-locked rooms discovered by the crawl, grouped by dungeon system.
          !locked            → grouped summary (Tower, Texts, Grind, …)
          !locked tower      → expand one system to its full room list
          !locked other      → the ungrouped one-off keys
        """
        import discord
        try:
            with open(LOCKED_PATH) as f:
                locked = json.load(f)
        except Exception:
            locked = {}
        if not locked:
            await ctx.send("No key-locked rooms recorded yet. Run a crawl first.")
            return

        # Build: system → { key_name → [room_ids] }, plus per-system room totals.
        systems = {}
        for rid, info in locked.items():
            key = info.get("key") or "Unknown key"
            sysname, sortk = _lock_system(key)
            bucket = systems.setdefault(sysname, {"keys": {}, "rooms": 0})
            bucket["keys"].setdefault(key, {"rooms": [], "sort": sortk})["rooms"].append(rid)
            bucket["rooms"] += 1

        # Fixed display order for the known systems; anything else trails alphabetically.
        order = ["Tower", "The Texts", "Grind", "Sanctum Specialty", "Wards", "Other"]
        sys_names = [s for s in order if s in systems] + \
                    sorted(s for s in systems if s not in order)

        system = system.strip().lower()

        # ---- Drill-down: expand one named system ----
        if system:
            match = next((s for s in sys_names if system in s.lower()), None)
            if not match:
                await ctx.send(f"🔎 No lock system matching `{system}`. "
                               f"Try: {', '.join(f'`{s}`' for s in sys_names)}")
                return
            bucket = systems[match]
            embed = discord.Embed(
                title=f"🔒 {match} — {bucket['rooms']} locked room(s)",
                color=discord.Color.dark_gold())
            keys_sorted = sorted(bucket["keys"], key=lambda k: bucket["keys"][k]["sort"])
            # Discord caps an embed at 25 fields. Systems with many keys (Tower has
            # 50 floors) would overflow AND read as noise as one-field-per-key — so
            # for anything over ~10 keys, render a single compact "key: rooms" list.
            if len(keys_sorted) > 10:
                lines = []
                for key in keys_sorted:
                    rooms = ", ".join(sorted(bucket["keys"][key]["rooms"], key=lambda x: int(x)))
                    lines.append(f"**{key}** — {rooms}")
                _add_wrapped_field(embed, f"{match} keys", lines)
            else:
                for key in keys_sorted:
                    rooms = ", ".join(sorted(bucket["keys"][key]["rooms"], key=lambda x: int(x)))
                    _add_wrapped_field(embed, key, [rooms])
            await ctx.send(embed=embed)
            return

        # ---- Default: grouped summary ----
        embed = discord.Embed(
            title=f"🔒 Key-locked rooms — {len(locked)} rooms across {len(sys_names)} systems",
            color=discord.Color.dark_gold())
        for s in sys_names:
            b = systems[s]
            nkeys = len(b["keys"])
            # A short, human summary of what's in the system.
            if s == "Tower":
                floors = sorted(int(re.search(r'(\d+)', k).group(1))
                                for k in b["keys"] if re.search(r'\d+', k))
                span = f"floors {floors[0]}–{floors[-1]}" if floors else f"{nkeys} keys"
                desc = f"{span} · {b['rooms']} rooms"
            else:
                desc = f"{nkeys} key(s) · {b['rooms']} rooms"
            embed.add_field(name=f"🗝️ {s}", value=desc, inline=False)
        embed.set_footer(text="Use !locked <system> to expand one (e.g. !locked tower).")
        await ctx.send(embed=embed)

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

    @commands.command(name="zone")
    async def zone_cmd(self, ctx, *, name: str = ""):
        """
        Deep-dive a single zone: rooms mapped, raids, quest-givers, top mobs by level,
        and how many rooms are key-locked (and behind which keys).
          !zone holy          → fuzzy match on zone name (contains, case-insensitive)
          !zone "Holy Dimension"  → exact match
        Reads only the crawl database — no live calls.
        """
        import discord
        name = name.strip()
        if not name:
            await ctx.send("Usage: `!zone <name>` (e.g. `!zone holy`). "
                           "Run `!zones` to list all mapped zones.")
            return

        try:
            with open(ZONES_PATH) as f:
                room_zones = json.load(f)   # {room_id_str: zone_name}
        except Exception:
            room_zones = {}
        if not room_zones:
            await ctx.send("No zones recorded yet — run a crawl with zone capture first.")
            return
        try:
            with open(MOBS_PATH) as f:
                mobs = json.load(f)
        except Exception:
            mobs = {}
        try:
            with open(LOCKED_PATH) as f:
                locked = json.load(f)       # {room_id_str: {"key":..., "from":...}}
        except Exception:
            locked = {}

        # ---- Resolve the zone name (exact if quoted, else contains) ----
        all_zone_names = sorted(set(room_zones.values()))
        exact = name.startswith('"') and name.endswith('"')
        needle = name.strip('"').lower()
        if exact:
            candidates = [z for z in all_zone_names if z.lower() == needle]
        else:
            candidates = [z for z in all_zone_names if needle in z.lower()]

        if not candidates:
            await ctx.send(f"🔎 No zone matching `{name}`. Run `!zones` to see mapped zones.")
            return
        if len(candidates) > 1:
            listing = ", ".join(f"`{z}`" for z in candidates[:15])
            more = "" if len(candidates) <= 15 else f" (+{len(candidates)-15} more)"
            await ctx.send(f"🔎 `{name}` matches {len(candidates)} zones: {listing}{more}\n"
                           f"Be more specific or quote the exact name.")
            return

        zone = candidates[0]

        # ---- Gather this zone's rooms and mobs ----
        zone_rooms = {int(rid) for rid, z in room_zones.items() if z == zone}
        raids, attackers, talkers = [], [], []
        for m in mobs.values():
            if m.get("zone") != zone:
                continue
            cat = m.get("category") or "attack"
            if cat == "raid":
                raids.append(m)
            elif cat == "talk":
                talkers.append(m)
            else:
                attackers.append(m)

        # Rooms in THIS zone that are key-locked, tallied by key.
        locked_here = {int(rid): info for rid, info in locked.items()
                       if int(rid) in zone_rooms}
        lock_by_key = {}
        for info in locked_here.values():
            k = info.get("key") or "Unknown key"
            lock_by_key[k] = lock_by_key.get(k, 0) + 1

        def lvl(m):
            return m.get("level") or 0

        embed = discord.Embed(title=f"🗺️  {zone}", color=discord.Color.teal())

        # Overview line — rooms mapped + mob tallies + lock coverage.
        overview = (f"**{len(zone_rooms):,}** rooms mapped  ·  "
                    f"🐉 {len(raids)} raids  ·  ⚔️ {len(attackers)} mobs  ·  "
                    f"📜 {len(talkers)} quest-givers")
        embed.description = overview

        # Raids — the headline content, show every one with level + room.
        if raids:
            rlines = []
            for m in sorted(raids, key=lvl, reverse=True):
                room_list = m.get("rooms") or []
                rooms = ", ".join(str(r) for r in sorted(room_list))
                l = f" (L{m['level']})" if m.get("level") else ""
                rlines.append(f"🐉 **{m['name']}**{l} — room {rooms}")
            _add_wrapped_field(embed, "Raids", rlines)

        # Top attackable mobs by level (cap the list so the card stays readable).
        if attackers:
            top = sorted(attackers, key=lvl, reverse=True)[:12]
            alines = []
            for m in top:
                l = f"L{m['level']}" if m.get("level") else "L?"
                r = f"{m.get('rage'):,}r" if m.get("rage") else ""
                alines.append(f"⚔️ {m['name']} — {l}{('  ·  ' + r) if r else ''}")
            title = "Top mobs by level" if len(attackers) > 12 else "Attackable mobs"
            _add_wrapped_field(embed, title, alines)

        # Quest-givers (names only — usually plentiful).
        if talkers:
            names = ", ".join(sorted(m["name"] for m in talkers))
            _add_wrapped_field(embed, f"Quest-givers ({len(talkers)})", [names])

        # Lock coverage — the honest bit: what's gated in this zone and behind which key.
        if lock_by_key:
            llines = [f"🔒 {n} room(s) — **{k}**" for k, n in
                      sorted(lock_by_key.items(), key=lambda kv: -kv[1])]
            _add_wrapped_field(embed, f"Locked rooms ({len(locked_here)})", llines)
            embed.set_footer(text="Locked rooms are walls this crawl account hit — "
                                  "another account with the key may map further.")
        else:
            embed.set_footer(text="No key-locked rooms recorded in this zone.")

        await ctx.send(embed=embed)

    @commands.command(name="find")
    async def find_cmd(self, ctx, *, query: str = ""):
        """
        Look up mobs, raids, quest-givers, or a room in the crawl database.
          !find <room number>   → everything in that room (zone + all mobs by category)
          !find <name>          → that mob's locations, grouped by zone (name = "contains")
          !find <name>*         → starts-with wildcard
          !find "exact name"    → exact match only
        Name search covers attackable mobs, raid mobs, AND quest-givers (talkable).
        """
        query = query.strip()
        if not query:
            await ctx.send("Usage: `!find <room#>` or `!find <mob name>` "
                           "(add `*` for starts-with, or quote for exact).")
            return

        try:
            with open(MOBS_PATH) as f:
                mobs = json.load(f)
        except Exception:
            await ctx.send("No crawl data yet — run a crawl first.")
            return
        try:
            with open(ZONES_PATH) as f:
                room_zones = json.load(f)
        except Exception:
            room_zones = {}

        def zone_of(rid):
            return room_zones.get(str(rid)) or "Unknown Area"

        def group_rooms_by_zone(rooms):
            grouped = {}
            for rid in rooms:
                grouped.setdefault(zone_of(rid), []).append(rid)
            return grouped

        # ---- ROOM lookup: all digits ----
        if query.isdigit():
            rid = int(query)
            here = [m for m in mobs.values() if rid in m.get("rooms", [])]
            zname = zone_of(rid)
            if not here:
                await ctx.send(f"🔎 **Room {rid}** ({zname}) — no mobs recorded here.")
                return
            cats = {"raid": [], "attack": [], "talk": []}
            for m in here:
                cats.get(m.get("category") or "attack", cats["attack"]).append(m)
            lines = [f"🔎 **Room {rid}**  ·  🗺️ {zname}"]
            labels = [("raid", "🐉 Raids"), ("attack", "⚔️ Attackable"), ("talk", "📜 Quest-givers")]
            for key, label in labels:
                if cats[key]:
                    lines.append(f"\n**{label}:**")
                    for m in cats[key]:
                        lvl = f" L{m['level']}" if m.get("level") else ""
                        lines.append(f"  • {m['name']}{lvl}")
            await self._send_chunked(ctx, "\n".join(lines))
            return

        # ---- NAME lookup: exact (quoted), starts-with (trailing *), or contains ----
        exact = query.startswith('"') and query.endswith('"')
        needle = query.strip('"').lower()
        starts = needle.endswith("*")
        needle_clean = needle.rstrip("*").strip()

        def matches(name):
            n = name.lower()
            if exact:
                return n == needle_clean
            if starts:
                return n.startswith(needle_clean)
            return needle_clean in n

        hits = [m for m in mobs.values() if matches(m.get("name", ""))]
        if not hits:
            await ctx.send(f"🔎 No mob/raid/quest-giver matching `{query}` found.")
            return

        # One hit → full zone/room breakdown. Many → compact name+count list.
        if len(hits) == 1:
            await self._send_chunked(ctx, self._format_mob_locations(hits[0], group_rooms_by_zone))
        else:
            lines = [f"🔎 **{len(hits)} matches for `{query}`:**"]
            for m in sorted(hits, key=lambda x: x.get("name", "")):
                cat = m.get("category") or "?"
                icon = {"raid": "🐉", "attack": "⚔️", "talk": "📜"}.get(cat, "•")
                nrooms = len(m.get("rooms", []))
                lines.append(f"  {icon} {m['name']} — {nrooms} room(s)")
            lines.append("\nUse `!find \"exact name\"` to see one mob's rooms by zone.")
            await self._send_chunked(ctx, "\n".join(lines))

    def _format_mob_locations(self, mob, group_fn):
        """Render 'Mob X is in: <Zone>: Room #,#' grouped by zone (multi-zone aware)."""
        cat = mob.get("category") or "?"
        icon = {"raid": "🐉", "attack": "⚔️", "talk": "📜"}.get(cat, "•")
        lvl = f" (L{mob['level']})" if mob.get("level") else ""
        grouped = group_fn(mob.get("rooms", []))
        lines = [f"{icon} **{mob['name']}**{lvl} is in:"]
        for zone in sorted(grouped):
            rooms = ", ".join(str(r) for r in sorted(grouped[zone]))
            lines.append(f"**{zone}:**")
            lines.append(f"Room {rooms}")
        return "\n".join(lines)

    async def _send_chunked(self, ctx, msg):
        """Send a long message split on line boundaries (Discord 2000-char cap)."""
        buf = ""
        for line in msg.split("\n"):
            if len(buf) + len(line) + 1 > 1900:
                await ctx.send(buf)
                buf = ""
            buf += line + "\n"
        if buf.strip():
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
