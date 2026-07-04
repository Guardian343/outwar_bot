import discord
from discord.ext import commands
from outwar import database as db
from cogs import embed_style as es


class DatabaseCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    @commands.command(name="groups")
    async def groups_command(self, ctx, command: str = None, group_name: str = None, *, character_names: str = None):
        """Manage character groups. Usage: ?groups [add|update|delete|<name>] [group_name] [characters...]"""
        if group_name:
            group_name = group_name.upper()

        if not command:
            groups = db.get_groups()
            embed = es.info_embed("👥 Groups")
            if not groups:
                embed.add_field(name="Groups", value="There aren't any groups")
            else:
                chunk = ""
                for g in groups:
                    line = g["name"] + "\n"
                    if len(chunk) + len(line) > 1000:
                        embed.add_field(name=f"Groups ({len(groups)})", value=chunk, inline=False)
                        chunk = ""
                    chunk += line
                if chunk:
                    embed.add_field(name=f"Groups ({len(groups)})", value=chunk, inline=False)
            await ctx.send(embed=embed)
            return

        cmd = command.lower()

        if cmd == "add":
            if not group_name or not character_names:
                await ctx.send("Usage: `!groups add <GROUP_NAME> <characters...>`")
                return
            if db.add_group(group_name, character_names):
                await ctx.send(f"Group **{group_name}** added!")
            else:
                await ctx.send(f"Group **{group_name}** already exists!")

        elif cmd == "update":
            if not group_name or not character_names:
                await ctx.send("Usage: `!groups update <GROUP_NAME> <characters...>`")
                return
            if db.update_group(group_name, character_names):
                await ctx.send(f"Group **{group_name}** updated!")
            else:
                await ctx.send(f"Group **{group_name}** doesn't exist!")

        elif cmd in ("delete", "remove"):
            if not group_name:
                await ctx.send("Usage: `!groups delete <GROUP_NAME>`")
                return
            if db.delete_group(group_name):
                await ctx.send(f"Group **{group_name}** removed!")
            else:
                await ctx.send(f"Group **{group_name}** doesn't exist!")

        else:
            # Treat command as a group name lookup
            group = db.get_group(command.upper())
            embed = es.info_embed(f"👥 Group {command.upper()}")
            if group:
                chars = db.group_to_list(group)
                names_str = " ".join(chars)
                embed.description = f"**Characters ({len(chars)})**\n{names_str}"
            else:
                embed.description = "Group not found!"
            await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Crews
    # ------------------------------------------------------------------

    @commands.command(name="crews")
    async def crews_command(self, ctx, command: str = None, crew_name: str = None, *, full_name: str = None):
        """Manage crews. Usage: ?crews [add|update|delete] [crew_name] [full_name]"""
        if crew_name:
            crew_name = crew_name.upper()

        if not command:
            import re as _re_c
            def _norm_c(s):
                return _re_c.sub(r"[^a-z0-9]", "", (s or "").lower())
            trustees = db.get_trustees()
            aliases  = db.get_all_aliases()          # {alias_lower: full_name}
            alias_by_crew = {}                       # normalized full_name -> [aliases]
            for al, fn in aliases.items():
                alias_by_crew.setdefault(_norm_c(fn), []).append(al)

            # Group by crew_id when we have it (distinguishes same-named crews),
            # otherwise fall back to grouping by crew name.
            groups = {}
            for t in trustees:
                cname = (t.get("crew") or "").strip() or "—"
                cid   = t.get("crew_id")
                key   = f"id:{cid}" if cid is not None else f"nm:{cname.lower()}"
                g = groups.setdefault(key, {"name": cname, "id": cid, "count": 0})
                g["count"] += 1
                if cid is not None:
                    g["id"] = cid
                if cname != "—":
                    g["name"] = cname

            if not groups:
                await ctx.send(embed=es.info_embed(
                    "🏰 Crews", "No crews found yet — run the trustee scan to populate them."))
                return

            rows = sorted(groups.values(), key=lambda x: x["count"], reverse=True)
            missing_ids = any(g["id"] is None for g in rows)
            lines = []
            for g in rows:
                al = alias_by_crew.get(_norm_c(g["name"]), [])
                alias_str = f"  [{', '.join(sorted(al))}]" if al else ""
                id_str = str(g["id"]) if g["id"] is not None else "—"
                lines.append(f"`{id_str:>7}`  {g['name']}{alias_str}  · {g['count']}")

            embed = es.info_embed(f"🏰 Crews ({len(rows)})")
            chunk = ""
            for line in lines:
                if len(chunk) + len(line) + 1 > 1000:
                    embed.add_field(name="\u200b", value=chunk, inline=False)
                    chunk = ""
                chunk += line + "\n"
            if chunk:
                embed.add_field(name="\u200b", value=chunk, inline=False)
            foot = "ID · Crew Name [aliases] · members"
            if missing_ids:
                foot += "  —  re-run the trustee scan to fill in missing IDs"
            embed.set_footer(text=foot)
            await ctx.send(embed=embed)
            return

        cmd = command.lower()

        if cmd == "add":
            if not crew_name or not full_name:
                await ctx.send("Usage: `!crews add <SHORT_NAME> <Full Crew Name>`")
                return
            if db.add_crew(crew_name, full_name):
                await ctx.send(f"Crew **{crew_name}** added!")
            else:
                await ctx.send(f"Crew **{crew_name}** already exists!")

        elif cmd == "update":
            if not crew_name or not full_name:
                await ctx.send("Usage: `!crews update <SHORT_NAME> <New Full Name>`")
                return
            if db.update_crew(crew_name, full_name):
                await ctx.send(f"Crew **{crew_name}** updated!")
            else:
                await ctx.send(f"Crew **{crew_name}** doesn't exist!")

        elif cmd in ("delete", "remove"):
            if not crew_name:
                await ctx.send("Usage: `!crews delete <SHORT_NAME>`")
                return
            if db.delete_crew(crew_name):
                await ctx.send(f"Crew **{crew_name}** removed!")
            else:
                await ctx.send(f"Crew **{crew_name}** doesn't exist!")

        else:
            await ctx.send(f"Unknown command `{command}`. Use add, update, or delete.")


    # ------------------------------------------------------------------
    # Aliases
    # ------------------------------------------------------------------

    @commands.command(name="alias")
    async def alias(self, ctx, action: str = None, shortname: str = None, *, full_name: str = None):
        """
        Manage crew name aliases.
        Usage:
            !alias add <shortname> <Full Crew Name>
            !alias remove <shortname>
        """
        if not action:
            await ctx.send("Usage: `!alias add <shortname> <Full Crew Name>` or `!alias remove <shortname>`")
            return

        action = action.lower()

        if action == "add":
            if not shortname or not full_name:
                await ctx.send("Usage: `!alias add <shortname> <Full Crew Name>`")
                return
            db.set_custom_alias(shortname, full_name)
            await ctx.send(f"✅ Alias `{shortname.lower()}` → **{full_name}** saved.")

        elif action == "remove":
            if not shortname:
                await ctx.send("Usage: `!alias remove <shortname>`")
                return
            if db.remove_custom_alias(shortname):
                await ctx.send(f"✅ Alias `{shortname.lower()}` removed.")
            else:
                await ctx.send(f"Alias `{shortname.lower()}` not found in custom aliases.")

        else:
            await ctx.send(f"Unknown action `{action}`. Use `add` or `remove`.")

    @commands.command(name="aliases")
    async def aliases(self, ctx):
        """Show all crew aliases — both built-in and custom."""
        built_in = db.CREW_ALIASES
        custom = db.get_custom_aliases()

        embed = es.info_embed("🏷️ Crew Aliases")

        # Built-in aliases
        built_in_lines = "\n".join(
            f"`{k}` → {v}" for k, v in sorted(built_in.items())
        )
        embed.add_field(
            name=f"Built-in ({len(built_in)})",
            value=built_in_lines[:1024],
            inline=False
        )

        # Custom aliases
        if custom:
            custom_lines = "\n".join(
                f"`{k}` → {v}" for k, v in sorted(custom.items())
            )
            embed.add_field(
                name=f"Custom ({len(custom)})",
                value=custom_lines[:1024],
                inline=False
            )
        else:
            embed.add_field(
                name="Custom",
                value="None yet — use `!alias add <shortname> <Full Crew Name>` to add one.",
                inline=False
            )

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(DatabaseCommands(bot))
