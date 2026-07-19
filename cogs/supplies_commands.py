"""
supplies_commands.py

Buy-max supplies and set an all-defence stance across every character on a
stored RGA, using that RGA's saved SSID.

    !supplies <rga>          — buy max + set style 5 (All Defence) on the whole roster
    !supplies <rga> preview  — show the roster + current supply % without changing

MECHANICS (verified from the OWMod userscript)
----------------------------------------------
- Buy max:   POST /supplies      body: buymax=Buy Max
- Set style: GET  /supplies.php?style=N     (1 = All Attack ... 5 = All Defence)
- Each request is made AS a character via rg_sess_id + suid + serverid params
  (cookieless), so the bot acts on an RGA it isn't logged into.

Buy-max is safe to run bluntly: supplies cap at 100%, and clicking "Buy Max" at
100% spends no gold — so there's nothing destructive to guard against.
"""

import asyncio
import discord
from discord.ext import commands
from outwar import ssid_store as store, logger
from cogs import embed_style as es

DEFENCE_STYLE = 5   # 1 = All Attack ... 5 = All Defence


class SuppliesCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _resolve_rga(self, ctx, rga: str):
        """
        Find a stored SSID entry by RGA name. A normal user may only target their
        OWN stored RGA; owner/admins may target anyone's.
        """
        from cogs.ssid_commands import _is_admin
        entries = store.all_entries()
        want = (rga or "").strip().lower()

        # Non-admins: only their own entry, and only if the name matches.
        own = store.get_ssid(ctx.author.id)
        if not _is_admin(self.bot, ctx.author.id):
            if own and own.get("rga", "").lower() == want:
                return own
            return None
        # Admin: match any stored RGA by name.
        for e in entries.values():
            if e.get("rga", "").lower() == want:
                return e
        return None

    @commands.command(name="supplies", aliases=["supply", "resupply"])
    async def supplies(self, ctx, rga: str = None, mode: str = None):
        """Buy max supplies + set All Defence across an RGA's roster."""
        if not rga:
            await ctx.send("Usage: `!supplies <rga> [preview]`")
            return

        entry = self._resolve_rga(ctx, rga)
        if not entry:
            await ctx.send(f"No stored RGA called **{rga}** you can act on. "
                           f"(You can only supply your own RGA unless you're an admin.)")
            return

        ssid = entry["ssid"]
        sid = entry.get("server_id", 1)
        preview = (mode or "").lower() == "preview"
        rga_name = entry.get("rga", rga)

        roster = await store.fetch_roster(ssid, sid)
        if not roster:
            await ctx.send(f"❌ Couldn't read that RGA's roster — the SSID "
                           f"may have expired. Re-add it with `!sess add`.")
            return

        if preview:
            lines = "\n".join(f"• {c['name']} (suid {c['suid']})" for c in roster)
            await ctx.send(embed=es.info_embed(
                f"🔎 {rga_name} — {len(roster)} characters",
                description=lines + f"\n\n_Run `!supplies {rga}` to buy max + set "
                                    f"All Defence on all of them._"))
            return

        import time as _t
        status = await ctx.send(f"🛒 Buying supplies on **{rga_name}**…")
        t0 = _t.monotonic()

        done, failed = 0, []
        sem = asyncio.Semaphore(6)   # gentle concurrency, like other roster actions

        async def _do(char):
            nonlocal done
            async with sem:
                try:
                    # Buy max (no-op if already 100%, spends nothing).
                    await store.sess_post("supplies", {"buymax": "Buy Max"},
                                          ssid, char["suid"], sid)
                    # Set all-defence stance.
                    await store.sess_get(f"supplies.php?style={DEFENCE_STYLE}",
                                         ssid, char["suid"], sid)
                    done += 1
                except Exception as e:
                    failed.append(char["name"])
                    logger.warning("SUPPLIES",
                                   f"{char['name']} (suid {char['suid']}) failed: {e}")

        await asyncio.gather(*[_do(c) for c in roster])

        secs = _t.monotonic() - t0
        msg = f"✅ Bought supplies in {secs:.1f}s for **{rga_name}**"
        if failed:
            shown = ", ".join(failed[:10]) + (f" +{len(failed)-10}" if len(failed) > 10 else "")
            msg += f"\n⚠️ Failed on {len(failed)}: {shown}"
        await status.edit(content=msg)
        logger.info("SUPPLIES", f"{rga_name}: {done}/{len(roster)} done, "
                                f"{len(failed)} failed in {secs:.1f}s")


async def setup(bot):
    await bot.add_cog(SuppliesCommands(bot))
