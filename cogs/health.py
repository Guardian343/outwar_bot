"""
Bot health monitoring — error logging, raid failure detection, health command.
"""
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone
from outwar import database as db

BOT_START_TIME = datetime.now(timezone.utc)


class HealthMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot          = bot
        self._errors      = []       # list of (timestamp, command, error_str)
        self._last_raid   = None     # datetime of last successful raid
        self._raid_fails  = 0        # consecutive raid failures
        self._relogins    = 0        # session re-logins since start
        self.health_loop.start()

    def cog_unload(self):
        self.health_loop.cancel()

    def log_error(self, source: str, error: str):
        """Called from anywhere to record an error."""
        self._errors.append((datetime.now(timezone.utc), source, error))
        if len(self._errors) > 100:
            self._errors = self._errors[-100:]

    def log_raid_success(self):
        self._last_raid  = datetime.now(timezone.utc)
        self._raid_fails = 0

    def log_raid_failure(self):
        self._raid_fails += 1

    def log_relogin(self):
        self._relogins += 1

    @tasks.loop(minutes=1)
    async def health_loop(self):
        """Post critical errors to log channel."""
        # Check for consecutive raid failures
        if self._raid_fails >= 3:
            channel_id = db.get_alert_channel("log")
            channel    = self.bot.get_channel(channel_id) if channel_id else None
            if channel:
                await channel.send(
                    f"🔴 **AutoBoss alert:** {self._raid_fails} consecutive raid failures. "
                    f"Raids may have stalled — check `!boss-status`."
                )
            self._raid_fails = 0  # Reset to avoid spam

    @health_loop.before_loop
    async def before_health(self):
        await self.bot.wait_until_ready()

    @commands.command(name="health")
    async def health(self, ctx):
        """Show bot health status."""
        now     = datetime.now(timezone.utc)
        uptime  = now - BOT_START_TIME
        hrs, r  = divmod(int(uptime.total_seconds()), 3600)
        mins    = r // 60

        # Session age
        session     = self.bot.outwar
        session_age = "Unknown"
        if hasattr(session, '_last_login'):
            age_secs   = (now - session._last_login).total_seconds()
            session_age = f"{int(age_secs//3600)}h {int((age_secs%3600)//60)}m"

        # AutoBoss status
        boss_cog  = self.bot.cogs.get("BossRaidCommands")
        boss_str  = "⏹️ Not running"
        if boss_cog and boss_cog._running:
            s     = boss_cog._status
            phase = s.get("phase", "raiding")
            boss_str = f"⚔️ {phase.replace('_',' ').title()} — {s.get('boss','—')}"

        # Last raid
        last_raid_str = "None this session"
        if self._last_raid:
            ago = int((now - self._last_raid).total_seconds() // 60)
            last_raid_str = f"{ago}m ago"

        # Recent errors
        recent_errors = self._errors[-5:] if self._errors else []

        embed = discord.Embed(
            title="🏥 Bot Health",
            colour=discord.Colour.green() if not recent_errors else discord.Colour.orange()
        )
        embed.add_field(name="Uptime",         value=f"{hrs}h {mins}m",   inline=True)
        embed.add_field(name="Re-logins",      value=str(self._relogins), inline=True)
        embed.add_field(name="Last Raid",      value=last_raid_str,        inline=True)
        embed.add_field(name="AutoBoss",       value=boss_str,             inline=False)
        embed.add_field(name="Errors (session)", value=str(len(self._errors)), inline=True)
        embed.add_field(name="Consecutive Fails", value=str(self._raid_fails), inline=True)

        if recent_errors:
            err_lines = "\n".join(
                f"`{e[1]}`: {e[2][:60]}" for e in recent_errors
            )
            embed.add_field(name="Recent Errors", value=err_lines[:1024], inline=False)

        embed.set_footer(text=f"{now.strftime('%H:%M UTC')}  ·  DeathBot · LoD")
        await ctx.send(embed=embed)


async def setup(bot):
    cog = HealthMonitor(bot)
    await bot.add_cog(cog)
    bot.health = cog  # expose for other cogs to call
