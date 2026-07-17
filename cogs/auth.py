import discord
from discord.ext import commands
from outwar import database as db
from functools import wraps

import random

# Rotated at random each time an unauthorised user is blocked.
UNAUTHORISED_GIFS = [
    "https://media.tenor.com/s7PJL8J9tagAAAAd/couple-passion-access-denied-stopped.gif",
    "https://tenor.com/view/access-denied-nope-try-again-no-denied-gif-14051588",
    "https://tenor.com/view/tts-skitarii-access-denied-access-denied-gif-19967616",
    "https://tenor.com/view/access-denied-fail-no-access-willkommen%C3%B6sterreich-testet-wot-gif-20424761",
    "https://tenor.com/view/access-denied-gif-17045600277531152747",
]


def unauth_gif() -> str:
    """Return a random 'access denied' GIF URL."""
    return random.choice(UNAUTHORISED_GIFS)


# Backwards-compatible alias (some code imports the single constant)
UNAUTHORISED_GIF = UNAUTHORISED_GIFS[0]
OWNER_ID = 412681493157249044

# ---------------------------------------------------------------------------
# Permission levels
# ---------------------------------------------------------------------------
# owner  — everything
# admin  — all except owner-only
# member — read-only / utility commands
# none   — GIF

# Owner only — bot configuration, session management
OWNER_COMMANDS = {
    "auth", "unauth", "auth-list",
    "set-alert-channel", "alerting",
    "summary-set", "summary-remove", "summary-list", "summary-now",
    "focusdrops", "unfocusdrops", "focuslist",
    "scan-trustees", "update-trustees", "remove-trustees", "clear-trustees",
    "autorank", "optimise", "optimise-all", "optimize-all", "optimiseall",
    "standings", "rankings",
    "scan-keys", "scankeys", "scan-teleporters",
    "scores", "score",
    "crews-locked", "locked-crews", "crewlocked",
    "crew-lock", "crewlock", "crew-unlock", "crewunlock",
    "exclude", "include", "unexclude", "excluded",
    "slayer-list", "slayerlist", "slayer-needs", "slayerneeds",
    "slayer", "slayer-stop", "slayerstop",
    "session-set", "session-get",
    "restart",
}

# Admin — can control raiding, casting, bot actions
ADMIN_COMMANDS = {
    "autoboss", "boss-stop", "boss-proceed", "boss-group", "raidboss", "boss-window", "bossraid",
    "cast", "cast-all", "cast-afflic", "cast-class", "cast-fero", "cast-pres", "cast-ss",
    "drink", "drink-all", "reset-md",
    "rg", "rq", "rm", "rmdebug", "god-set", "god-rec-import", "primeupdate",
    "primewatcher", "pw",
    "crawl", "crawl-stop", "poll-now",
    "giveaway", "envoy-drops", "boss-pots",
    "alert-channels", "check-trustees", "check-item",
    "guard-start", "guard-stop", "envoy-pool", "envoy-fetch",
    # !bp scan — writes the item archive and hits the site, so admin+.
    # NOTE: the auth check reads ctx.command.name, which for a subcommand is the
    # SUBcommand's own name ("scan"), not the group's ("bp").
    "scan",
}

# Member — view only, no actions
MEMBER_COMMANDS = {
    "status", "ping", "health",
    "gods", "up", "god", "god-list", "god-export", "beatable", "can-beat", "canbeat", "prime-stats", "prime-drops",
    "bosslist", "boss-status", "boss-records", "boss",
    "envoy-shop", "cast-raid",
    "check-md", "check-md",
    "commands", "todo", "complete",
    "alias", "aliases", "crews", "groups",
    "compare", "skills", "show-mr", "get-sessid",
    "crawl-status", "envoys",
    "rage", "who", "top", "bottom", "top-all",
    "group-stats", "pcaps", "eligible", "uncapped",
    "badge", "tce", "crest", "rg", "rq",
}


# Public — anyone can run these, even with no access level yet
PUBLIC_COMMANDS = {"whoami", "guide", "commands"}


def is_authorised(user_id: int, min_level: str = "member") -> bool:
    order      = {"owner": 3, "admin": 2, "member": 1, "none": 0}
    user_level = "owner" if user_id == OWNER_ID else db.get_user_level(user_id)
    return order.get(user_level, 0) >= order.get(min_level, 1)


# ---------------------------------------------------------------------------
# Global check — attached to bot in main.py
# ---------------------------------------------------------------------------

async def global_auth_check(ctx: commands.Context) -> bool:
    cmd        = ctx.command.name if ctx.command else ""
    user_id    = ctx.author.id
    user_level = "owner" if user_id == OWNER_ID else db.get_user_level(user_id)
    order      = {"owner": 3, "admin": 2, "member": 1, "none": 0}
    user_order = order.get(user_level, 0)

    # Public — anyone may run, regardless of access level
    if cmd in PUBLIC_COMMANDS:
        return True

    # Owner only
    if cmd in OWNER_COMMANDS:
        if user_order >= 3:
            return True
        raise commands.CheckFailure("owner_only")

    # Admin+ (raiding, casting, actions)
    if cmd in ADMIN_COMMANDS:
        if user_order >= 2:
            return True
        raise commands.CheckFailure("admin_only")

    # Member+ (viewing, read-only)
    if user_order >= 1:
        return True
    raise commands.CheckFailure("unauthorised")


# ---------------------------------------------------------------------------
# Auth cog
# ---------------------------------------------------------------------------

class AuthCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def publish_access_list(self):
        """
        Publish the auth list WITH resolved Discord names to status.json, so the
        dashboard shows who each person is instead of a bare ID. Tries the cache
        (get_user) first, then the API (fetch_user) so names resolve even without
        the privileged members intent. Safe: never raises into callers.
        """
        try:
            from outwar import status_writer

            async def resolve(uid):
                u = self.bot.get_user(uid)
                if u is None:
                    # Not in cache (e.g. no members intent) — ask the API directly.
                    try:
                        u = await self.bot.fetch_user(uid)
                    except Exception:
                        u = None
                name = None
                if u is not None:
                    name = getattr(u, "global_name", None) or getattr(u, "name", None)
                # IDs MUST be strings — Discord IDs exceed JavaScript's safe integer
                # range, so if they reach the browser as numbers the last digits get
                # rounded off. Keep them as strings end to end.
                return {"id": str(uid), "name": name or str(uid)}

            auth = db.get_auth()
            owner = [await resolve(OWNER_ID)]
            # Don't list the owner again under admins/members if their ID is there.
            admins = [await resolve(uid) for uid in auth.get("admins", []) if uid != OWNER_ID]
            members = [await resolve(uid) for uid in auth.get("members", []) if uid != OWNER_ID]
            status_writer.publish_access(owner, admins, members)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_ready(self):
        # Publish the resolved auth list once the bot is connected.
        await self.publish_access_list()
        # Publish resolved alert-channel names + envoy pool context for the dashboard.
        self.publish_settings_meta()

    def publish_settings_meta(self):
        """
        Publish human-friendly settings context: alert-channel NAMES (resolved from
        their IDs) and the last envoy loot pool. Read-only for the dashboard.
        """
        try:
            from outwar import status_writer
            settings = db.get_settings()
            channels = {}
            for atype in ("gods", "bosses", "envoys", "drops", "summary", "log"):
                cid = settings.get(f"alert_channel_{atype}")
                if cid is None:
                    continue
                ch = self.bot.get_channel(int(cid)) if str(cid).isdigit() else None
                name = getattr(ch, "name", None)
                channels[atype] = {"id": str(cid), "name": ("#" + name) if name else str(cid)}
            envoy_last = settings.get("envoy_loot_pool")
            status_writer.publish_settings_meta(channels, envoy_last)
        except Exception:
            pass

    @commands.command(name="restart")
    async def restart(self, ctx):
        """Restart the bot process. Owner only."""
        if ctx.author.id != OWNER_ID:
            await ctx.send(unauth_gif())
            return
        await ctx.send("🔄 Restarting bot...")
        import os, sys
        os.execv(sys.executable, [sys.executable] + sys.argv)

    @commands.command(name="auth")
    async def auth(self, ctx, flag: str, member: discord.Member):
        """Grant a user access. Usage: !auth -m @user (member) · !auth -a @user (admin)"""
        if not is_authorised(ctx.author.id, "owner"):
            await ctx.send(unauth_gif())
            return

        flag_map = {
            "-m": "member", "m": "member", "member": "member", "mem": "member",
            "-a": "admin",  "a": "admin",  "admin": "admin",
        }
        level = flag_map.get(flag.lower())
        if not level:
            await ctx.send(
                "Usage: `!auth -m @user` for **member** (view only) · "
                "`!auth -a @user` for **admin** (full control)."
            )
            return

        db.remove_auth(member.id)
        db.add_auth(level, member.id)
        await self.publish_access_list()  # keep the dashboard in sync
        await ctx.send(
            f"✅ **{member.display_name}** is now **{level}**"
            f"{' — can run raids, casting and all actions.' if level == 'admin' else ' — can view stats and status.'}"
        )

    @commands.command(name="whoami")
    async def whoami(self, ctx):
        """Show your access level and what you can do."""
        uid = ctx.author.id
        if uid == OWNER_ID:
            level = "owner"
        else:
            level = db.get_user_level(uid) or "none"

        blurb = {
            "owner":  "Full control, plus user management (`!auth`).",
            "admin":  "Run raids, cast skills, and use every action command.",
            "member": "View stats, status, gods and drops (read-only).",
            "none":   "No access yet — ask an admin to add you with `!auth -m @you`.",
        }[level]

        icon = {"owner": "👑", "admin": "🛡️", "member": "👁️", "none": "🚫"}[level]
        embed = discord.Embed(
            title=f"{icon} You are: {level}",
            description=f"{blurb}\n\nType `!help` to see what you can do.",
            colour=discord.Colour(0x5865F2),
        )
        await ctx.send(embed=embed)

    @commands.command(name="unauth")
    async def unauth(self, ctx, member: discord.Member):
        """Remove a user's access. Usage: !unauth @user"""
        if not is_authorised(ctx.author.id, "owner"):
            await ctx.send(unauth_gif())
            return
        removed = db.remove_auth(member.id)
        if removed:
            await ctx.send(f"✅ **{member.display_name}** removed from **{removed}**.")
            await self.publish_access_list()  # keep the dashboard in sync
        else:
            await ctx.send(f"**{member.display_name}** had no access level set.")

    @commands.command(name="auth-list")
    async def auth_list(self, ctx):
        """Show all authorised users."""
        if not is_authorised(ctx.author.id, "owner"):
            await ctx.send(unauth_gif())
            return
        auth = db.get_auth()
        embed = discord.Embed(title="Bot Access List", colour=discord.Colour(0x5865F2))
        owner = self.bot.get_user(OWNER_ID)
        embed.add_field(
            name="👑 Owner",
            value=owner.mention if owner else f"ID: {OWNER_ID}",
            inline=False
        )

        admin_mentions = []
        for uid in auth.get("admins", []):
            u = self.bot.get_user(uid)
            admin_mentions.append(u.mention if u else f"ID: {uid}")
        embed.add_field(
            name=f"🛡️ Admins ({len(admin_mentions)})",
            value="\n".join(admin_mentions) if admin_mentions else "None",
            inline=False
        )

        member_mentions = []
        for uid in auth.get("members", []):
            u = self.bot.get_user(uid)
            member_mentions.append(u.mention if u else f"ID: {uid}")
        embed.add_field(
            name=f"👤 Members ({len(member_mentions)})",
            value="\n".join(member_mentions) if member_mentions else "None",
            inline=False
        )
        await ctx.send(embed=embed)

    async def _get_rga_name(self, session_id: str) -> str:
        """Fetch the RGA username for a given session ID."""
        try:
            import aiohttp
            from yarl import URL
            sigil = URL("https://sigil.outwar.com")
            async with aiohttp.ClientSession() as s:
                s.cookie_jar.update_cookies({"rg_sess_id": session_id}, response_url=sigil)
                async with s.get("https://sigil.outwar.com/home") as resp:
                    html = await resp.text()
            import re as _re
            # RGA name appears in the toolbar or header
            m = _re.search(r'class=["\']toolbar_username["\'][^>]*>([^<]+)<', html)
            if not m:
                m = _re.search(r'Logged in as[:\s]+([A-Za-z0-9_]+)', html)
            if not m:
                m = _re.search(r'owchar=\d+.*?transnick=([^&"]+)', html)
            return m.group(1).strip() if m else None
        except Exception:
            return None

    @commands.command(name="session-set")
    async def session_set(self, ctx, *, content: str = None):
        """
        Store session IDs via DM. One per line.
        Format: RGAname: sessionid  OR just: sessionid (RGA name looked up automatically)
        """
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.message.delete()
            await ctx.author.send(
                "⚠️ For security, please send session IDs via DM only.\n"
                "Use `!session-set` here in this DM with your session IDs."
            )
            return
        if not content:
            await ctx.send("Usage:\n```\n!session-set\nRGAname: sessionid\nsessionid\n```")
            return

        import re as _re
        settings = db.get_settings()
        sessions = settings.get("user_sessions", {})
        if str(ctx.author.id) not in sessions:
            sessions[str(ctx.author.id)] = {}
        user_sessions = sessions[str(ctx.author.id)]
        if not isinstance(user_sessions, dict):
            user_sessions = {}

        stored  = []
        skipped = []

        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # Try "RGAname: sessionid" format
            m = _re.match(r"^(.+?):\s*([a-f0-9]{32})$", line, _re.IGNORECASE)
            if m:
                rga_name   = m.group(1).strip()
                session_id = m.group(2).strip().lower()
                user_sessions[rga_name] = session_id
                stored.append(f"`{rga_name}`")
            else:
                # Bare session ID — look up RGA name
                m2 = _re.match(r"^([a-f0-9]{32})$", line, _re.IGNORECASE)
                if m2:
                    session_id = m2.group(1).lower()
                    rga_name = await self._get_rga_name(session_id)
                    if rga_name:
                        user_sessions[rga_name] = session_id
                        stored.append(f"`{rga_name}`")
                    else:
                        # Store with fallback name
                        fallback = f"session_{len(user_sessions)+1}"
                        user_sessions[fallback] = session_id
                        stored.append(f"`{fallback}` (RGA name not found)")
                else:
                    skipped.append(line[:30])

        sessions[str(ctx.author.id)] = user_sessions
        settings["user_sessions"] = sessions
        db.save_settings(settings)

        msg = f"✅ Stored **{len(stored)}** session(s): {', '.join(stored)}"
        if skipped:
            msg += f"\n⚠️ Skipped {len(skipped)} unrecognised line(s): {', '.join(f'`{s}`' for s in skipped)}"
        await ctx.send(msg)

    @commands.command(name="session-get")
    async def session_get(self, ctx):
        """Display your stored session IDs. DM only."""
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.message.delete()
            await ctx.author.send("⚠️ Use `!session-get` here in DM for security.")
            return
        settings = db.get_settings()
        sessions = settings.get("user_sessions", {})
        user_sessions = sessions.get(str(ctx.author.id), {})
        if not user_sessions:
            await ctx.send("No session IDs stored. Use `!session-set` to add them.")
            return
        lines = [f"`{rga}`: `{sid}`" for rga, sid in user_sessions.items()]
        await ctx.send("**Your stored sessions:**\n" + "\n".join(lines))


async def setup(bot):
    await bot.add_cog(AuthCommands(bot))

    # Register global check
    bot.add_check(global_auth_check)
