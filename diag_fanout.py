"""
DIAGNOSTIC (read-only, safe): pin down WHY the concurrent get_as fan-out gets
logged-out responses (which breaks both potion casting AND live rage reads).

Run on the Pi:  venv/bin/python diag_fanout.py

It does NOT change anything. It just fetches the 'home' page for a handful of
trustees three ways and reports how many come back logged-out, so we can see
whether the cause is:
  (A) concurrency (many at once) — logged-out only when concurrent, fine when serial
  (B) the per-request ow_userid cookie merging with the shared jar
  (C) something else (all fail, or none fail)
"""
import asyncio, sys
sys.path.insert(0, '.')
from config import load_config
from outwar.session import OutwarSession
from outwar import database as db


def _looks_logged_out(html: str) -> bool:
    if not html:
        return True
    low = html.lower()
    # crude logged-out markers; the real check is _is_logged_out but this is enough
    return ("login_username" in low or "password" in low[:2000]) and "backpack" not in low


async def main():
    cfg = load_config()
    s = OutwarSession()
    await s.login(cfg['username'], cfg['password'])
    print("bot suid:", s.user_id)

    trustees = db.get_trustees(server_id=1)
    picks = [t for t in trustees if t.get("suid")][:12]
    suids = [int(t["suid"]) for t in picks]
    print(f"testing {len(suids)} trustees\n")

    # --- Test 1: SERIAL (one at a time) ---
    serial_out = 0
    for suid in suids:
        html = await s.get_as("home", suid)
        if _looks_logged_out(html):
            serial_out += 1
    print(f"[1] SERIAL     : {serial_out}/{len(suids)} logged-out")

    # small gap
    await asyncio.sleep(2)

    # --- Test 2: CONCURRENT (all at once, like the real fan-out) ---
    async def one(suid):
        html = await s.get_as("home", suid)
        return _looks_logged_out(html)
    results = await asyncio.gather(*[one(suid) for suid in suids])
    conc_out = sum(1 for r in results if r)
    print(f"[2] CONCURRENT : {conc_out}/{len(suids)} logged-out")

    await asyncio.sleep(2)

    # --- Test 3: CONCURRENT AGAIN (does it worsen on repeat?) ---
    results2 = await asyncio.gather(*[one(suid) for suid in suids])
    conc_out2 = sum(1 for r in results2 if r)
    print(f"[3] CONCURRENT#2: {conc_out2}/{len(suids)} logged-out")

    print("\nINTERPRETATION:")
    print("  serial fine + concurrent fails  -> concurrency/cookie-jar race (the fix target)")
    print("  all three fail                  -> session/auth problem, not concurrency")
    print("  all three fine                  -> couldn't reproduce right now (try after an xx:10 cycle)")

    await s.close()


asyncio.run(main())
