"""
ssid_commands.py

RGA session-ID (SSID) storage and expiry monitoring.

Commands
--------
  !sess add <SSID>        — store your RGA session id (validates it live first)
  !sess                   — pull YOUR stored SSID (account link + SSID, privately)
  !sess remove            — delete your stored SSID
  !sess list              — (owner/admin) list everyone's stored RGAs
  !sess get <@user|name>  — (owner/admin) pull a specific person's SSID

Security model
--------------
- SSIDs are encrypted at rest (see ssid_store).
- A normal user can only see their OWN SSID.
- Owner + admins can see any SSID (they run the crews) — a deliberate trust call.
- Replies with a full SSID are sent EPHEMERALLY (only the requester sees them) in
  a server channel, or plainly in a DM. Note: ephemeral hides it from others in
  the channel but the SSID is still plaintext to the requester's own client.

Expiry
------
An SSID stays valid until a fresh re-login kills it. A live SSID returns a
character roster from accounts.php; a dead one returns nothing. A background task
polls every stored SSID ~every 15 min: when one comes back empty it's expired, so
the bot DMs the owner and removes the dead entry.
"""

import asyncio
import discord
from discord.ext import commands, tasks
from outwar import ssid_store as store, logger
from cogs import embed_style as es

# Bot is on Sigil for now. Torax is a far-future "if others want it" thing —
# when that comes, this becomes per-server, but there's no point building it yet.
DEFAULT_SERVER = 1
SERVER_HOST = {1: "https://sigil.outwar.com", 2: "https://torax.outwar.com"}
POLL_MINUTES = 15


def _account_link(entry: dict) -> str:
    """
    The direct login link for an SSID, matching the in-game switch URL:
    home?rg_sess_id=<SSID>&serverid=<N>&suid=<primary suid>
    """
    sid = entry.get("server_id", 1)
    host = SERVER_HOST.get(sid, SERVER_HOST[1])
    link = f"{host}/home?rg_sess_id={entry['ssid']}"
    if entry.get("suid"):
        link += f"&suid={entry['suid']}"
    link += f"&serverid={sid}"
    return link


def _pull_line(entry: dict) -> str:
    """One plain-text line: 'rga: SSID home-link' — matches the reference bot."""
    return f"{entry.get('rga', 'RGA')}: {entry['ssid']} {_account_link(entry)}"


def _is_admin(bot, user_id: int) -> bool:
    """Owner or admin — reuse the bot's existing auth model."""
    try:
        from cogs.auth import is_authorised
        return is_authorised(user_id, "admin")
    except Exception:
        return False


class SsidCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.expiry_poll.start()

    def cog_unload(self):
        self.expiry_poll.cancel()

    @property
    def session(self):
        return self.bot.outwar

    # ---- helpers --------------------------------------------------------

    async def _private_send(self, ctx, content=None, embed=None):
        """
        Send a reply only the requester can see: ephemeral in a server channel,
        plain in a DM. (Text commands can't be truly ephemeral, so in a channel
        we DM the user and leave a small note.)
        """
        is_dm = isinstance(ctx.channel, discord.DMChannel)
        if is_dm:
            await ctx.send(content=content, embed=embed)
            return
        # In a server channel, deliver privately via DM.
        try:
            await ctx.author.send(content=content, embed=embed)
            await ctx.send(f"{ctx.author.mention} — sent you a DM. 📬",
                           delete_after=15)
        except discord.Forbidden:
            await ctx.send("I can't DM you — enable DMs from server members, "
                           "or run this in my DMs.", delete_after=20)

    # ---- !sess ----------------------------------------------------------

    @commands.group(name="sess", aliases=["ssid"], invoke_without_command=True)
    async def sess(self, ctx):
        """Pull YOUR stored SSID (rga: SSID link), sent privately."""
        entry = store.get_ssid(ctx.author.id)
        if not entry:
            await ctx.send("You have no SSID stored. Add one with `!sess add <SSID>`.")
            return
        await self._private_send(ctx, content=_pull_line(entry))

    @sess.command(name="add")
    async def sess_add(self, ctx, ssid: str = None):
        """Store your RGA session id. Validates it against Outwar first."""
        if not ssid:
            await ctx.send("Usage: `!sess add <SSID>`")
            return
        ssid = ssid.strip()
        # basic sanity — Outwar session ids are 32 hex chars
        if len(ssid) < 16:
            await ctx.send("That doesn't look like a session id. It should be the "
                           "long `rg_sess_id` value (~32 characters).")
            return

        status = await ctx.send("🔍 Checking that session id against Outwar…")
        ok, rga, roster = await store.validate_ssid(ssid, DEFAULT_SERVER)
        if not ok:
            await status.edit(content="❌ That session id didn't return an account "
                                      "— it may be wrong, or already expired. "
                                      "Re-login to Outwar and grab a fresh one.")
            return
        suid = roster[0]["suid"] if roster else ""
        store.set_ssid(ctx.author.id, ssid, rga, DEFAULT_SERVER, suid=suid)
        # Tidy the raw SSID out of channel history where we can.
        try:
            if not isinstance(ctx.channel, discord.DMChannel):
                await ctx.message.delete()
        except Exception:
            pass
        await status.edit(content=f"✅ Added session for **{rga}** "
                                  f"({len(roster)} character(s)).")

    @sess.command(name="remove", aliases=["delete", "del"])
    async def sess_remove(self, ctx):
        """Delete your stored SSID."""
        if store.remove_ssid(ctx.author.id):
            await ctx.send("✅ Your stored SSID has been removed.")
        else:
            await ctx.send("You had no SSID stored.")

    @sess.command(name="list")
    async def sess_list(self, ctx):
        """(Owner/admin) List everyone's stored RGAs — names only, no SSIDs."""
        if not _is_admin(self.bot, ctx.author.id):
            await ctx.send("That's an owner/admin command.")
            return
        entries = store.all_entries()
        if not entries:
            await ctx.send("No SSIDs stored yet.")
            return
        lines = []
        for did, e in entries.items():
            user = self.bot.get_user(int(did))
            who = user.name if user else f"id {did}"
            lines.append(f"• **{e.get('rga', '?')}** — {who} "
                         f"(server {e.get('server_id', 1)}, "
                         f"added {e.get('added', '?')[:10]})")
        await self._private_send(ctx, embed=es.info_embed(
            f"🔑 Stored SSIDs ({len(entries)})", description="\n".join(lines)
            + "\n\n_`!sess get <@user>` to pull a specific SSID._"))

    @sess.command(name="get")
    async def sess_get(self, ctx, who: str = None):
        """(Owner/admin) Pull a specific person's SSID (link + SSID), privately."""
        if not _is_admin(self.bot, ctx.author.id):
            await ctx.send("That's an owner/admin command.")
            return
        if not who:
            await ctx.send("Usage: `!sess get <@user | rga name>`")
            return

        entries = store.all_entries()
        target = None
        # by mention / id
        if ctx.message.mentions:
            target = str(ctx.message.mentions[0].id)
        else:
            w = who.strip().lstrip("@").lower()
            for did, e in entries.items():
                user = self.bot.get_user(int(did))
                if (e.get("rga", "").lower() == w
                        or (user and user.name.lower() == w)):
                    target = did
                    break
        if not target or target not in entries:
            await ctx.send(f"No stored SSID matches `{who}`.")
            return
        e = entries[target]
        await self._private_send(ctx, content=_pull_line(e))

    # ---- expiry poll ----------------------------------------------------

    @tasks.loop(minutes=POLL_MINUTES)
    async def expiry_poll(self):
        """
        Every ~15 min, check each stored SSID. An empty roster means it died
        (fresh re-login) — DM the owner and remove the dead entry.
        """
        try:
            entries = store.all_entries()
            for did, e in list(entries.items()):
                roster = await store.fetch_roster(e["ssid"], e.get("server_id", 1))
                if roster:
                    store.mark_ok(int(did))
                    continue
                # Expired — alert the owner, then remove.
                user = self.bot.get_user(int(did))
                if user:
                    try:
                        await user.send(embed=es.warn_embed(
                            f"⚠️ Your SSID for {e.get('rga', 'your RGA')} expired",
                            description=("Your stored session id stopped working — "
                                         "usually because the account was logged in "
                                         "fresh (instead of via the SSID), which "
                                         "resets it.\n\n"
                                         "Re-login to Outwar, grab the new session "
                                         "id, and store it again with "
                                         "`!sess add <SSID>`.")))
                    except Exception:
                        pass
                store.remove_ssid(int(did))
                logger.info("SSID", f"[EXPIRY] {e.get('rga', '?')} (user {did}) "
                                    f"expired and was removed")
                await asyncio.sleep(1)   # be gentle between checks
        except Exception as ex:
            logger.warning("SSID", f"expiry poll error: {ex}")

    @expiry_poll.before_loop
    async def _before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(SsidCommands(bot))
