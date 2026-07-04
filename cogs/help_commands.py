"""
help_commands.py — Full command reference for DeathBot.
Usage:
  !help                 — overview of all categories
  !help <category>      — commands in a category
  !help <command>       — usage + description for one command
(!commands and !h are aliases of !help)
"""

import discord
from cogs import embed_style as es
from discord.ext import commands


CATEGORIES = {
    "raiding": {
        "title": "Boss Raiding",
        "emoji": "🔨",
        "description": "Crew boss raiding. Fire !autoboss once and it runs hands-free. Tip: these also work as `!boss <action>` — e.g. `!boss raid lod 100`, `!boss stop`.",
        "commands": [
            ("!autoboss <group> [boss]",        "Auto-raid spawned bosses hands-free until stopped — casts skills, raids, pots in the background, and survives MD recharges. Fire once."),
            ("!bossraid <group> [count] [boss]", "Raid a crew boss a set number of times (or until stopped). No skills/pots. e.g. !bossraid lod 100 cosmos"),
            ("!raidboss <group> [boss]",         "Do a single round of boss raids without casting skills"),
            ("!boss-stop",                       "Stop the running autoboss / bossraid session"),
            ("!boss-status",                     "Live session stats — raids, damage, MD, current boss"),
            ("!boss-group",                      "Show the accounts in the current session"),
            ("!boss-records",                    "All-time best raid damage per boss"),
            ("!boss-pots <crew>",                "Use boss-specific potions on a crew"),
            ("!boss-proceed",                    "Confirm a partial-readiness raid when autoboss prompts"),
            ("!reset-md",                        "Reset stored Markdown state for the group"),
        ]
    },
    "primeraids": {
        "title": "Prime God Raiding",
        "emoji": "⚔️",
        "description": "Prime God and world mob raiding.",
        "commands": [
            ("!rm <group> <god>",                "Hit a Prime God once with a group"),
            ("!rg <group> <god> <tries> [wins]", "Multi-attempt Prime God raid, stop after N wins"),
            ("!rq <group> <tries> <wins> <gods>","Queue multiple gods: !rq lod1 5 2 zikkir,firan"),
            ("!badge <group> [tries]",           "Hit all 4 badge mobs (Crawling, Demonic, Elex, Conductor)"),
            ("!tce <group> [tries]",             "Hit The Chaotic Elemental"),
            ("!crest <group> [grouz|morrik|both] [tries]", "Hit crest mobs Grouz and/or Morrik"),
            ("!uncapped <god>",                  "Show which groups have enough caps to hit a god"),
            ("!pcaps <group>",                   "Live cap status image for a group"),
        ]
    },
    "primewatcher": {
        "title": "Prime Watcher (auto-raid)",
        "emoji": "🛰️",
        "description": "Auto-raid Prime Gods on spawn. Each watcher is its own on/off bundle of groups + primes. (Setup is live; the raiding engine is being built.)",
        "commands": [
            ("!pw",                               "Overview of all watchers and their status"),
            ("!pw help",                          "Full Prime Watcher command list"),
            ("!pw create <name>",                "Create a new watcher"),
            ("!pw delete <name>",                "Delete a watcher"),
            ("!pw add-group <name> <group> [none|class|raid]", "Add a group (raid skills include class)"),
            ("!pw remove-group <name> <group>",  "Remove a group from a watcher"),
            ("!pw add-prime <name> <prime> <caps>", "Add a prime with its own cap target"),
            ("!pw remove-prime <name> <prime>",  "Remove a prime from a watcher"),
            ("!pw set-crew <name> <crew>",        "Set the crew caps are counted for"),
            ("!pw on <name>  /  !pw off <name>",  "Enable / disable a watcher"),
            ("!pw show <name>",                  "Full details of one watcher"),
        ]
    },
    "gods": {
        "title": "Prime Gods",
        "emoji": "⚡",
        "description": "Prime God database, status, drops and info.",
        "commands": [
            ("!up",                              "Show spawned gods with time remaining and rec stats"),
            ("!gods",                            "Show currently spawned gods"),
            ("!god <name>",                      "Full details for a god including win stats"),
            ("!god-list",                        "Reference table — all gods with aliases and rec stats"),
            ("!beatable <group> [strict]",       "Gods a group can beat — avg (or 'strict' = all members) vs rec"),
            ("!god-set <name> <field> <value>",  "Update a god field"),
            ("!prime-stats <god>",               "Show crew kill stats for a god"),
            ("!prime-drops <god>",               "Show drop table for a god"),
            ("!primeupdate",                     "Scrape all Prime God pages and rebuild the database"),
            ("!poll-now",                        "Force an immediate god/envoy poll"),
            ("!envoys",                          "Show current envoy spawn status"),
            ("!envoy-pool [number]",             "View or set the current envoy loot pool number"),
            ("!envoy-shop",                      "Display the Envoy Quartermaster shop"),
            ("!focusdrops <crew>",               "Highlight a crew in drop summaries"),
            ("!unfocusdrops <crew>",             "Remove a crew from drop highlights"),
            ("!focuslist",                       "Show crews currently highlighted in drop summaries"),
        ]
    },
    "bosses": {
        "title": "Boss Tracking",
        "emoji": "💀",
        "description": "Server boss status and spawn windows.",
        "commands": [
            ("!bosslist",                        "Boss status image with HP% and spawn windows"),
            ("!boss-window <boss> <desc>",       "Set spawn window description for a boss"),
        ]
    },
    "stats": {
        "title": "Stats & Rankings",
        "emoji": "📊",
        "description": "Character stats, rankings and group info.",
        "commands": [
            ("!who <character>",                 "Full stats, caps and rage for one character"),
            ("!compare <char1> <char2>",         "Compare two characters side by side"),
            ("!group-stats <group>",             "Power, ele, chaos and faction image for a group"),
            ("!rage <group>",                    "Live rage image for a group"),
            ("!show-mr <group|crew>",            "Show max rage for all characters in a group or crew"),
            ("!top <amount> <group> [stat]",     "Top N in a group. Stats: power/ele/chaos"),
            ("!top-all <amount> <stat>",         "Top N across all level 80+ trustees"),
            ("!bottom <amount> <group> [stat]",  "Bottom N in a group"),
            ("!optimise <crew>",                 "Optimisation suggestions for a crew"),
        ]
    },
    "skills": {
        "title": "Skills & Potions",
        "emoji": "✨",
        "description": "Cast skills and use potions.",
        "commands": [
            ("!cast <skill> <target>",           "Cast any skill on a crew, group, or character"),
            ("!cast-raid <target>",              "Cast the full boss-raid skill set (except SiN)"),
            ("!cast-ss <group>",                 "Cast Street Smarts on a group or crew"),
            ("!cast-all <character>",            "Cast Empower, Stealth, VitaminX and Fortify"),
            ("!cast-pres <target>",              "Cast all Preservation skills"),
            ("!cast-fero <target>",              "Cast all Ferocity skills"),
            ("!cast-afflic <target>",            "Cast all Affliction skills"),
            ("!cast-class <target>",             "Cast all Class skills"),
            ("!skills",                          "List all available skills and aliases"),
            ("!drink <crew> <potion>",           "Use a potion on all characters in a crew or group"),
            ("!drink-all <crew>",                "Use all standard potions on a crew"),
            ("!check-item <item> [group]",       "Check who has an item"),
            ("!check-md <crew>",                 "Check Markdown status for a crew"),
        ]
    },
    "database": {
        "title": "Groups & Crews",
        "emoji": "🗄️",
        "description": "Manage groups, crews and aliases.",
        "commands": [
            ("!groups",                          "List all groups"),
            ("!groups add <name> <chars>",       "Add a new group"),
            ("!groups update <name> <chars>",    "Update a group"),
            ("!groups delete <name>",            "Delete a group"),
            ("!autorank <crew> <stat> <n>",      "Auto-rank crew by stat into groups of N"),
            ("!crews",                           "List all crew aliases"),
            ("!crews add <short> <full name>",   "Add a crew alias"),
            ("!crews update <short> <name>",     "Update a crew alias"),
            ("!crews delete <short>",            "Delete a crew alias"),
            ("!alias add <short> <name>",        "Add a custom alias"),
            ("!alias remove <short>",            "Remove a custom alias"),
            ("!aliases",                         "Show all aliases"),
        ]
    },
    "alerts": {
        "title": "Alerts & Monitoring",
        "emoji": "🔔",
        "description": "Spawn alerts, daily summaries and background guards.",
        "commands": [
            ("!set-alert-channel <type> [#ch]",  "Set alert channel for gods/bosses/envoys/drops/log"),
            ("!alert-channels",                  "Show configured alert channels"),
            ("!summary-set <crew>",              "Add a crew to the daily summary"),
            ("!summary-list",                    "Show crews included in the daily summary"),
            ("!summary-remove <crew>",           "Remove a crew from the daily summary"),
            ("!summary-now",                     "Trigger the daily summary right now"),
            ("!guard-start",                     "Keep On Guard + Street Smarts active for all trustees"),
            ("!guard-stop",                      "Stop the guard task"),
            ("!health",                          "Show bot health status"),
        ]
    },
    "admin": {
        "title": "Admin",
        "emoji": "🔧",
        "description": "Setup and configuration (admin only).",
        "commands": [
            ("!scan-trustees",                   "Scrape trustee list from Outwar"),
            ("!update-trustees",                 "Reload trustees without restarting"),
            ("!check-trustees",                  "List all trusted accounts"),
            ("!auth -m @user",                   "Give a member view-only access (or -a for admin)"),
            ("!unauth <member>",                 "Remove a member's authorisation"),
            ("!auth-list",                       "List authorised members"),
            ("!get-sessid",                      "Show current Outwar session ID"),
            ("!session-set <cookie>",            "Set the Outwar session manually"),
            ("!session-get",                     "Show the stored Outwar session"),
            ("!restart",                         "Restart the bot"),
        ]
    },
    "utility": {
        "title": "Utility",
        "emoji": "🛠️",
        "description": "Status and everyday helpers.",
        "commands": [
            ("!guide",                           "New here? Start with this quick walkthrough"),
            ("!whoami",                          "Show your access level"),
            ("!status",                          "Bot health, uptime and session info"),
            ("!ping",                            "Check the bot is responding"),
            ("!eligible",                        "Show level 79s close to level 80"),
            ("!giveaway <prize> [exclude]",      "Pick a random winner from the giveaway pool"),
            ("!todo",                            "Show what's still to do"),
            ("!complete",                        "Show completed work"),
            ("!help",                            "Show the full command menu (also !commands)"),
            ("!help <command>",                  "Show usage for a single command"),
        ]
    },
    "crawler": {
        "title": "World Crawler",
        "emoji": "🗺️",
        "description": "Walk all accessible rooms and update map/mob data.",
        "commands": [
            ("!crawl <character>",               "Start crawling the world map as a character"),
            ("!crawl-stop",                      "Stop the running crawl"),
            ("!crawl-status",                    "Crawl progress — rooms visited, new rooms, new mobs"),
        ]
    },
}


# ---------------------------------------------------------------------------
# To-do list parsing + dropdown view
# ---------------------------------------------------------------------------

def _parse_todo(content: str):
    """Split TODO.md into categories by '## ' headers.
    Returns (last_updated, [ {title, body, count} ]). count = number of '- ' bullets."""
    last_updated = ""
    cats = []
    cur  = None
    for line in content.splitlines():
        if line.lower().startswith("last updated:"):
            last_updated = line.split(":", 1)[1].strip()
            continue
        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "body": [], "count": 0}
            cats.append(cur)
        elif cur is not None:
            cur["body"].append(line)
            if line.lstrip().startswith("- "):
                cur["count"] += 1
    # tidy each body: drop leading/trailing blank lines
    for c in cats:
        while c["body"] and not c["body"][0].strip():
            c["body"].pop(0)
        while c["body"] and not c["body"][-1].strip():
            c["body"].pop()
    return last_updated, cats


class _TodoCategorySelect(discord.ui.Select):
    """Dropdown of to-do categories; selecting one shows that category's items
    privately (ephemeral) to whoever selected it."""

    def __init__(self, categories):
        self.cat_map = {str(i): c for i, c in enumerate(categories)}
        options = [
            discord.SelectOption(
                label=c["title"][:100],
                description=f"{c['count']} item{'s' if c['count'] != 1 else ''}"[:100],
                value=str(i),
            )
            for i, c in enumerate(categories[:25])   # Discord caps selects at 25
        ]
        super().__init__(
            placeholder="Select a category to view…",
            min_values=1, max_values=1, options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        cat  = self.cat_map.get(self.values[0], {})
        body = "\n".join(cat.get("body", [])).strip() or "_Nothing here._"
        header = f"**{cat.get('title','')}** — {cat.get('count',0)} item(s)\n"
        # Chunk into code blocks under Discord's message limit
        chunks = [body[i:i+1800] for i in range(0, len(body), 1800)] or [""]
        for idx, ch in enumerate(chunks):
            prefix = header if idx == 0 else ""
            await interaction.followup.send(
                f"{prefix}```markdown\n{ch}\n```", ephemeral=True
            )


class _TodoView(discord.ui.View):
    def __init__(self, categories, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.message = None
        self.add_item(_TodoCategorySelect(categories))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class HelpCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _usage_for(self, cmd) -> str:
        """Prefer a 'Usage:' line from the docstring; else build from the signature."""
        doc = cmd.help or ""
        for line in doc.splitlines():
            s = line.strip()
            if s.lower().startswith("usage:"):
                return s[6:].strip()
        sig = cmd.signature.strip()
        return f"!{cmd.qualified_name} {sig}".strip()

    @commands.command(name="guide", aliases=["start", "quickstart", "getstarted"])
    async def guide(self, ctx):
        """Friendly getting-started walkthrough for new users."""
        embed = discord.Embed(
            title="👋 Welcome to DeathBot",
            description=(
                "The bot raids for you and tracks everything happening in Outwar. "
                "Here are the handful of commands you'll actually use — you don't need to learn them all."
            ),
            color=es.COLOR_INFO,
        )
        embed.add_field(
            name="👀 See what's up",
            value=(
                "`!up` — spawned Prime Gods (pick one for live stats)\n"
                "`!gods` — quick spawned-god list\n"
                "`!bosslist` — server boss status"
            ),
            inline=False,
        )
        embed.add_field(
            name="📊 Check stats",
            value=(
                "`!who <character>` — one character's stats\n"
                "`!group-stats <group>` — a whole group\n"
                "`!rage <group>` — live rage"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚔️ Raiding (admin)",
            value=(
                "`!autoboss <group>` — start hands-free boss raids (fire once)\n"
                "`!bossraid <group> [count]` — raid a crew boss without skills/pots\n"
                "`!boss-status` — live progress · `!boss-stop` — stop"
            ),
            inline=False,
        )
        embed.add_field(
            name="🧭 Find your way",
            value=(
                "`!help` — every command, grouped by category\n"
                "`!help <command>` — how to use any one command\n"
                "`!whoami` — your access level"
            ),
            inline=False,
        )
        embed.set_footer(text="Tip: most commands take a group or crew name, e.g. !rage lod1")
        await ctx.send(embed=embed)

    @commands.command(name="commands", aliases=["help", "h"])
    async def help_command(self, ctx, *, arg: str = None):
        """Show bot commands. Usage: !help | !help <category> | !help <command>"""
        if arg:
            key = arg.lower().strip().lstrip("!")

            # 1) A category?
            cat = CATEGORIES.get(key)
            if cat:
                embed = discord.Embed(
                    title=f"{cat['emoji']} {cat['title']}",
                    description=cat["description"],
                    color=es.COLOR_INFO,
                )
                chunk = ""
                for cmd, desc in cat["commands"]:
                    line = f"`{cmd}`\n{desc}\n\n"
                    if len(chunk) + len(line) > 1024:
                        embed.add_field(name="Commands", value=chunk, inline=False)
                        chunk = ""
                    chunk += line
                if chunk:
                    embed.add_field(name="Commands", value=chunk, inline=False)
                embed.set_footer(text="!help for all categories · !help <command> for details")
                await ctx.send(embed=embed)
                return

            # 2) A specific command?
            cmd = self.bot.get_command(key)
            if cmd:
                embed = discord.Embed(
                    title=f"Command: !{cmd.qualified_name}",
                    color=es.COLOR_INFO,
                )
                embed.add_field(name="Usage", value=f"`{self._usage_for(cmd)}`", inline=False)
                desc = (cmd.help or "—").strip().split("\n")[0]
                embed.add_field(name="What it does", value=desc, inline=False)
                if cmd.aliases:
                    embed.add_field(
                        name="Aliases",
                        value=", ".join(f"`!{a}`" for a in cmd.aliases),
                        inline=False,
                    )
                await ctx.send(embed=embed)
                return

            # 3) Neither
            valid = ", ".join(f"`{k}`" for k in CATEGORIES.keys())
            await ctx.send(
                f"No category or command called `{arg}`.\n"
                f"Categories: {valid}\nOr try `!help <command>`."
            )
            return

        # Overview
        embed = discord.Embed(
            title="⚔️ DeathBot — Command Help",
            description=(
                "**New here? Type `!guide`** for a 30-second walkthrough.\n\n"
                "Browse a category with `!help <category>`, or get details on any single "
                "command with `!help <command>` (e.g. `!help bossraid`)."
            ),
            color=es.COLOR_INFO,
        )
        for key, cat in CATEGORIES.items():
            embed.add_field(
                name=f"{cat['emoji']} {cat['title']}",
                value=f"`!help {key}` — {len(cat['commands'])} commands",
                inline=True,
            )
        embed.set_footer(text="Prefix: !  ·  !help <command> for usage on anything")
        await ctx.send(embed=embed)

    @commands.command(name="todo")
    async def todo(self, ctx):
        """Show the to-do list as a category overview with a dropdown to view each."""
        try:
            with open(self.bot.todo_path, "r", encoding="utf-8") as f:
                content = f.read()
            last_updated, cats = _parse_todo(content)
            if not cats:
                await ctx.send("To-do list is empty.")
                return

            if not last_updated:
                import os, datetime as _dt
                try:
                    last_updated = _dt.datetime.fromtimestamp(
                        os.path.getmtime(self.bot.todo_path)).strftime("%Y-%m-%d")
                except Exception:
                    last_updated = _dt.date.today().isoformat()

            lines = "\n".join(
                f"{c['title']} — **{c['count']}**" for c in cats
            )
            total = sum(c["count"] for c in cats)
            embed = discord.Embed(
                title="📋 DeathBot To-Do List",
                description=(
                    f"_Last updated: {last_updated}_ · **{total}** items total\n\n"
                    f"{lines}\n\n"
                    "Pick a category below to view its items (only you'll see it)."
                ),
                color=es.COLOR_INFO,
            )
            view = _TodoView(cats)
            msg  = await ctx.send(embed=embed, view=view)
            view.message = msg
        except Exception as e:
            await ctx.send(f"Error: `{e}`")

    @commands.command(name="complete")
    async def complete(self, ctx):
        """Show completed items from the to-do list."""
        try:
            with open(self.bot.todo_path, "r", encoding="utf-8") as f:
                content = f.read()
            parts = content.split("## Completed")
            if len(parts) < 2:
                await ctx.send("No completed items found.")
                return
            completed = ("## Completed" + parts[1]).strip()
            chunks = [completed[i:i+1900] for i in range(0, len(completed), 1900)]
            for chunk in chunks:
                await ctx.send(f"```markdown\n{chunk}\n```")
        except Exception as e:
            await ctx.send(f"Error: `{e}`")


async def setup(bot):
    await bot.add_cog(HelpCommands(bot))
