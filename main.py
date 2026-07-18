import sys
# Force UTF-8 output before anything else runs. Under pythonw/the supervisor on
# Windows, the default pipe encoding is cp1252 and can't encode characters like
# "→" used in debug logs — which crashed raids with a 'charmap' codec error.
# Setting UTF-8 here (first thing) makes all output robust.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import asyncio
import os
import discord
from discord.ext import commands
from config import load_config
from outwar.session import OutwarSession, LoginError
from outwar import logger


def _usage_hint(cmd) -> str:
    """Prefer a 'Usage:' line from the command docstring; else build from signature."""
    if cmd is None:
        return ""
    doc = cmd.help or ""
    for line in doc.splitlines():
        s = line.strip()
        if s.lower().startswith("usage:"):
            return s[6:].strip()
    return f"!{cmd.qualified_name} {cmd.signature}".strip()


async def main():
    config = load_config()

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True

    bot = commands.Bot(command_prefix=config["prefix"], intents=intents, help_command=None)

    # Login to Outwar
    logger.info("MAIN", "Logging into Outwar...")
    session = OutwarSession()
    try:
        await session.login(config["username"], config["password"])
        logger.info("SESSION", f"Outwar login successful — username: {config['username']}, user_id: {session.user_id}")
        # Publish the bot account so the dashboard shows whose trustees the totals reflect.
        try:
            from outwar import status_writer
            status_writer.publish_account(config["username"], session.user_id)
        except Exception:
            pass
    except LoginError as e:
        logger.error("SESSION", f"Outwar login failed: {e}")
        return

    bot.outwar    = session
    bot.config    = config
    bot.todo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TODO.md")

    # Session health monitoring — post to log channel on re-login
    async def _on_relogin(success: bool, error: str = None):
        from outwar.database import get_alert_channel
        if hasattr(bot, 'health'):
            bot.health.log_relogin()
            if not success and error:
                bot.health.log_error("session_relogin", error)
        channel_id = get_alert_channel("log")
        channel    = bot.get_channel(channel_id) if channel_id else None
        if not channel:
            channel = bot.get_channel(config["channel"])
        if channel:
            if success:
                await channel.send("\u26a0\ufe0f **Session expired** \u2014 DeathBot re-logged in successfully.")
            else:
                await channel.send(f"\U0001f534 **Session expired and re-login FAILED**: `{error}`")

    session.on_relogin = _on_relogin

    # Load cogs — auth first so global check is registered before any other cog
    extensions = (
        "cogs.auth",
        "cogs.health",
        "cogs.character_commands",
        "cogs.boss_commands",
        "cogs.boss_raid_commands",
        "cogs.database_commands",
        "cogs.raid_commands",
        "cogs.admin_commands",
        "cogs.god_monitor",
        "cogs.primewatcher",
        "cogs.misc_commands",
        "cogs.backpack_commands",
        "cogs.crew_history_commands",
        "cogs.utility_commands",
        "cogs.crawler_commands",
        "cogs.help_commands",
    )
    for extension in extensions:
        try:
            await bot.load_extension(extension)
            logger.info("COGS", f"Loaded {extension}")
        except Exception as e:
            logger.error("COGS", f"Failed to load {extension}: {e}")

    @bot.event
    async def on_ready():
        logger.info("MAIN", f"Bot ready as {bot.user}")
        # Publish the Discord server(s) this bot is in, so the dashboard can label
        # the instance with the real server name.
        try:
            from outwar import status_writer
            status_writer.publish_guilds(bot.guilds)
        except Exception:
            pass
        channel = bot.get_channel(config["channel"])
        if channel:
            await channel.send("DeathBot is online")
        else:
            logger.warning("MAIN", f"Could not find channel ID {config['channel']} — check your config.")

    @bot.event
    async def on_command_error(ctx, error):
        from cogs.auth import unauth_gif, is_authorised
        from outwar.database import get_alert_channel
        if isinstance(error, commands.CheckFailure):
            logger.warning("AUTH", f"CheckFailure for {ctx.author} ({ctx.author.id}) running !{ctx.command}: {error}")
            try:
                await ctx.send(unauth_gif())
            except Exception as send_err:
                # GIF send failed (embed perms, etc.) — fall back to plain text so
                # the user still gets a clear response, and log why for the owner.
                logger.warning("AUTH", f"Failed to send unauthorised GIF: {send_err}")
                try:
                    await ctx.send("🚫 You don't have access to that command yet. Ask an admin to add you.")
                except Exception as send_err2:
                    logger.error("AUTH", f"Fallback unauthorised message also failed: {send_err2}")
        elif isinstance(error, commands.CommandNotFound):
            # No-access users get the GIF for anything they type. Authorised users
            # get a helpful "unknown command" nudge so typos aren't silently ignored.
            attempted = ctx.message.content.split()[0] if ctx.message.content.split() else "?"
            if not is_authorised(ctx.author.id, "member"):
                try:
                    await ctx.send(unauth_gif())
                except Exception:
                    try:
                        await ctx.send("🚫 You don't have access to the bot yet. Ask an admin to add you.")
                    except Exception:
                        pass
            else:
                try:
                    await ctx.send(f"❓ `{attempted}` isn't a command. Type `!help` to see what's available.")
                except Exception:
                    pass
            return
        elif isinstance(error, commands.MissingRequiredArgument):
            usage = _usage_hint(ctx.command)
            name  = ctx.command.name if ctx.command else ""
            await ctx.send(
                f"⚠️ Missing the `{error.param.name}` argument.\n"
                f"Usage: `{usage}`\n"
                f"More: `!help {name}`"
            )
        elif isinstance(error, commands.BadArgument):
            usage = _usage_hint(ctx.command)
            name  = ctx.command.name if ctx.command else ""
            await ctx.send(
                f"⚠️ That didn't look right.\n"
                f"Usage: `{usage}`\n"
                f"More: `!help {name}`"
            )
        else:
            cmd      = ctx.command.name if ctx.command else "unknown"
            err_str  = str(error)
            logger.error("COMMAND", f"{cmd}: {err_str}")
            await ctx.send("An error occurred while running that command. Check the log channel.")
            # Log to health monitor
            if hasattr(bot, 'health'):
                bot.health.log_error(cmd, err_str)
            # Post to log channel
            log_ch_id = get_alert_channel("log")
            log_ch    = bot.get_channel(log_ch_id) if log_ch_id else None
            if log_ch:
                await log_ch.send(
                    f"⚠️ **Command error** in `!{cmd}` by {ctx.author.mention}:\n`{err_str[:200]}`"
                )

    await bot.start(config["token"])

if __name__ == "__main__":
    asyncio.run(main())
