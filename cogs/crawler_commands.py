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
from collections import deque
from discord.ext import commands
from datetime import datetime
from yarl import URL

SIGIL_URL = URL("https://sigil.outwar.com")
# Shipped seed map (read-only baseline); crawled output lives in database/ so deploys
# (which replace code but exclude database/) never overwrite a real crawl.
MAP_SEED  = os.path.join(os.path.dirname(__file__), "..", "outwar", "map_graph.json")
MAP_PATH  = os.path.join(os.path.dirname(__file__), "..", "database", "map_graph.json")
MOBS_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "crawl_mobs.json")

# Rate limit — requests per second
CRAWL_DELAY    = 0.3   # seconds between moves
PROGRESS_EVERY = 100   # post update every N rooms


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
                mobs.append({"id": int(mid), "name": name,
                             "type": mob.get("type"), "raid": mob.get("type") == 1})
            except (ValueError, TypeError):
                pass
    return {"actual_room": actual_room, "exits": connected, "mobs": mobs,
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

            def _record(parsed, rid):
                nonlocal new_mobs
                for m in parsed["mobs"]:
                    key = str(m["id"])
                    if key not in crawl_mobs:
                        crawl_mobs[key] = {"id": m["id"], "name": m["name"],
                                           "type": m["type"], "raid": m["raid"],
                                           "rooms": [rid]}
                        new_mobs += 1
                        self._stats["new_mobs"] = new_mobs
                    elif rid not in crawl_mobs[key]["rooms"]:
                        crawl_mobs[key]["rooms"].append(rid)
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
                    print(f"Crawl error moving {current}->{nxt}: {e}")
                    continue

                if parsed is None:
                    self._stats["errors"] += 1
                    continue
                if parsed["actual_room"] != nxt:
                    # couldn't enter (key-locked / restricted) — stay put, try next neighbour
                    self._stats["locked"] += 1
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
                    self._save(map_graph, crawl_mobs)

            # Final save
            self._save(map_graph, crawl_mobs)

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
            print(f"Crawl fatal error: {e}")
        finally:
            self._crawling = False

    def _save(self, map_graph: dict, crawl_mobs: dict):
        """Save map graph and mob data to disk (database/, which survives deploys)."""
        os.makedirs(os.path.dirname(MAP_PATH), exist_ok=True)
        with open(MAP_PATH, "w") as f:
            json.dump({str(k): v for k, v in map_graph.items()}, f)
        with open(MOBS_PATH, "w") as f:
            json.dump(crawl_mobs, f, indent=2)
        # Refresh the live pathfinder so find_path() uses the freshly crawled map
        try:
            from outwar import scraper
            scraper._map_graph = {int(k): v for k, v in map_graph.items()}
        except Exception:
            pass

    # ------------------------------------------------------------------
    # !crawl-stop
    # ------------------------------------------------------------------

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
        mob_lines = [f"  • {m['id']} — {m['name']}" + (" (raid)" if m["raid"] else "")
                     for m in parsed["mobs"][:15]]
        match = "✅ exits match map" if got and set(got) == set(known) else (
                "⚠️ exits differ from map" if got else "❌ no exits parsed")
        msg = (
            f"🔎 **Room {room}** as **{trustee['name']}**\n"
            f"Arrived: {arrived_str}\n"
            f"Exits parsed: **{len(got)}** {got[:12]}\n"
            f"Map says: **{len(known)}** {known[:12]}  →  {match}\n"
            f"Mobs parsed: **{len(parsed['mobs'])}**\n" + ("\n".join(mob_lines) if mob_lines else "  (none)")
        )
        if not parsed["exits"]:
            msg += f"\n\n⚠️ Exits empty — response keys were: `{parsed['raw_keys']}` (tells me the right exits key)."
        await ctx.send(msg[:1900])

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
