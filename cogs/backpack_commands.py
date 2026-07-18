"""
backpack_commands.py

Backpack cataloguing and counting for a single account.

    !bp scan <tab> [account]     — discover item names in a tab, merge into the archive
    !bp scan all [account]       — scan every tab at once
    !bp archive [tab]            — show what's been catalogued so far
    !count <what> <account>      — count items an account holds

WHY THE ARCHIVE EXISTS
----------------------
Potions/keys are used by matching their EXACT in-game name against the backpack.
A hand-written name that's even slightly wrong fails *silently* ("not in
backpack"), which is the worst kind of bug. `!bp scan` reads the real names
straight off the backpack pages, so the archive is ground truth.

It also means `!count` can tell you what an account is MISSING (archived items it
holds none of), and new items Outwar adds show up on the next scan instead of
needing a code change.

SCANNING IS A MAINTENANCE JOB
-----------------------------
Run it once to seed the archive, then again when new items appear. It always
MERGES — never replaces — because a scan reads ONE account, and that account
won't hold every item in the game. Replacing would wipe out everything it
happens not to own.
"""

import asyncio
import discord
from discord.ext import commands
from outwar import database as db, logger
from outwar.scraper import parse_backpack_items, parse_teleport_destination
from cogs import embed_style as es
from cogs.pagination import paginate, chunk_lines, stamp_footers

# Friendly words → the real backpack tab names used by the site.
# The site's tabs are: potion, key, quest, regular, orb.
TAB_ALIASES = {
    "potion": "potion", "potions": "potion", "pot": "potion", "pots": "potion",
    "key": "key", "keys": "key", "teleporter": "key", "teleporters": "key",
    "quest": "quest", "quests": "quest",
    "regular": "regular", "general": "regular", "misc": "regular",
    "orb": "orb", "orbs": "orb",
}
ALL_TABS = ("potion", "key", "quest", "regular", "orb")

TAB_LABEL = {
    "potion": "Potions", "key": "Keys", "quest": "Quest Items",
    "regular": "General", "orb": "Orbs",
}

# Lines per page. Discord embeds cap around 4096 chars; 25 item lines sits well
# under that and stays readable on a phone.
PER_PAGE = 25


def _resolve_tab(word: str):
    """'pots' -> 'potion'. Returns None if it isn't a tab word."""
    return TAB_ALIASES.get((word or "").strip().lower())


class BackpackCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def session(self):
        return self.bot.outwar

    # ---- helpers --------------------------------------------------------

    def _resolve_account(self, account: str):
        """
        Find one trustee by name.

        With no name given, prefer the bot's OWN logged-in account — it's the
        predictable choice. Falling back to "first trustee with a suid" would pick
        an arbitrary account, which for cataloguing might be a near-empty alt.

        Either way the caller reports which account was scanned, because no single
        account holds every item — the archive is meant to fill in as you scan a
        few different accounts.
        """
        trustees = db.get_trustees()
        if account:
            return next((t for t in trustees
                         if t.get("name", "").lower() == account.strip().lower()), None)
        own = getattr(self.session, "user_id", None)
        if own:
            hit = next((t for t in trustees if str(t.get("suid")) == str(own)), None)
            if hit:
                return hit
        return next((t for t in trustees if t.get("suid")), None)

    async def _fetch_tab(self, tab: str, suid: int) -> list:
        """Read one backpack tab for one account and return every item in it."""
        html = await self.session.get_as(
            f"ajax/backpackcontents.php?tab={tab}", suid)
        return parse_backpack_items(html)

    async def _scan_teleporters(self, items: list, suid: int) -> tuple[int, list, int]:
        """
        For the KEY tab: check each key's rollover text to work out whether it's a
        teleporter and where it goes, then merge into the teleporter knowledge base.

        This is why `!bp scan keys` replaces the old `!scan-teleporters` — a key IS
        a teleporter or it isn't, so there's no reason to scan the same tab twice.

        Returns (teleporters_found, newly_discovered_names, total_known).
        """
        sem = asyncio.Semaphore(5)   # be gentle: one rollover request per key

        async def _roll(item):
            async with sem:
                try:
                    roll = await self.session.get_as(
                        f"item_rollover.php?id={item['item_id']}&data=0", suid)
                except Exception:
                    return None
                dest, kind = parse_teleport_destination(roll)
                if not dest:
                    return None
                return {**item, "destination": dest, "kind": kind}

        results = await asyncio.gather(*[_roll(i) for i in items])
        teleporters = [r for r in results if r]
        if not teleporters:
            return 0, [], len(db.get_teleporters())
        total, new = db.merge_teleporters(teleporters)
        return len(teleporters), new, total

    # ---- !bp ------------------------------------------------------------

    @commands.group(name="bp", invoke_without_command=True)
    async def bp(self, ctx):
        """Backpack hub."""
        tabs = ", ".join(f"`{t}`" for t in ALL_TABS)
        await ctx.send(embed=es.info_embed(
            "🎒 Backpack Commands",
            description=(
                "**Cataloguing** — run occasionally, when new items appear\n"
                "`!bp scan <tab> [account]` — catalogue item names from a tab\n"
                "`!bp scan all [account]` — catalogue every tab\n"
                "`!bp archive [tab]` — show what's catalogued\n\n"
                "**Counting**\n"
                "`!count <tab> <account>` — everything in a tab, + what's missing\n"
                "`!count <search> <account>` — items matching any part of a name\n\n"
                f"Tabs: {tabs} (`general` = `regular`)\n"
                "Scanning **keys** also works out which are teleporters and where "
                "they go — no separate teleporter scan needed.\n"
                "Lists only ever **grow**: a rescan adds new finds and never wipes "
                "what's already known."
            )
        ))

    @bp.command(name="scan")
    async def bp_scan(self, ctx, tab: str = None, *, account: str = None):
        """
        Catalogue the item names in a backpack tab. Merges into the archive.
        Usage: !bp scan potions [account] · !bp scan all [account]
        """
        if not tab:
            tabs = ", ".join(f"`{t}`" for t in ALL_TABS)
            await ctx.send(f"Usage: `!bp scan <tab|all> [account]`\nTabs: {tabs}")
            return

        want_all = tab.strip().lower() == "all"
        resolved = None if want_all else _resolve_tab(tab)
        if not want_all and not resolved:
            tabs = ", ".join(f"`{t}`" for t in ALL_TABS)
            await ctx.send(f"Unknown tab `{tab}`. Try: {tabs}, or `all`.")
            return

        t = self._resolve_account(account)
        if not t:
            await ctx.send(f"Account `{account}` not found in trustees."
                           if account else
                           "No trustees with a suid — run `!scan-trustees` first.")
            return
        suid = t.get("suid")
        if not suid:
            await ctx.send(f"**{t['name']}** has no suid recorded.")
            return

        targets = list(ALL_TABS) if want_all else [resolved]
        status = await ctx.send(
            f"🎒 Scanning {'all tabs' if want_all else TAB_LABEL[resolved]} "
            f"for **{t['name']}**…")

        lines, grand_new = [], 0
        for tb in targets:
            try:
                items = await self._fetch_tab(tb, suid)
            except Exception as e:
                lines.append(f"• **{TAB_LABEL[tb]}** — failed: {e}")
                continue
            if not items:
                lines.append(f"• **{TAB_LABEL[tb]}** — empty on this account")
                continue
            total, new = db.merge_item_archive(tb, items)
            grand_new += len(new)
            bit = f"• **{TAB_LABEL[tb]}** — {len(items)} held, {total} catalogued"
            if new:
                shown = ", ".join(new[:8]) + (f" +{len(new)-8} more" if len(new) > 8 else "")
                bit += f"\n   🆕 **{len(new)} new:** {shown}"

            # Keys get a second pass: work out which are teleporters and where they
            # go. Folded in here so one `!bp scan keys` does both jobs.
            if tb == "key":
                try:
                    tp_found, tp_new, tp_total = await self._scan_teleporters(items, suid)
                    grand_new += len(tp_new)
                    bit += (f"\n   🌀 **{tp_found}** teleporter"
                            f"{'s' if tp_found != 1 else ''} here · {tp_total} known")
                    if tp_new:
                        tshown = ", ".join(tp_new[:6]) + (
                            f" +{len(tp_new)-6} more" if len(tp_new) > 6 else "")
                        bit += f"\n   🆕 **{len(tp_new)} new teleporter"
                        bit += f"{'s' if len(tp_new) != 1 else ''}:** {tshown}"
                except Exception as e:
                    bit += f"\n   ⚠️ teleporter check failed: {e}"

            lines.append(bit)
            logger.info("BACKPACK",
                        f"[SCAN] {t['name']} tab={tb} held={len(items)} "
                        f"new={len(new)} catalogued={total}")

        desc = "\n".join(lines) or "Nothing found."
        if grand_new:
            desc += f"\n\n✅ **{grand_new}** new name{'s' if grand_new != 1 else ''} added to the archive."
        else:
            desc += "\n\nNo new names — the archive already knows everything this account holds."
        if not account:
            desc += (f"\n\n_No account given, so this scanned **{t['name']}**. "
                     f"No single account holds every item — scan a few different "
                     f"accounts and the archive fills in._")
        await status.edit(content=None, embed=es.info_embed(
            f"🎒 Scan — {t['name']}", description=desc[:4000]))

    @bp.command(name="archive")
    async def bp_archive(self, ctx, tab: str = None):
        """Show the catalogued item names. Usage: !bp archive [tab]"""
        archive = db.get_item_archive()
        if not archive:
            await ctx.send("The archive is empty — run `!bp scan all` to build it.")
            return

        if tab:
            resolved = _resolve_tab(tab)
            if not resolved:
                tabs = ", ".join(f"`{t}`" for t in ALL_TABS)
                await ctx.send(f"Unknown tab `{tab}`. Try: {tabs}")
                return
            names = sorted(archive.get(resolved, {}).keys())
            if not names:
                await ctx.send(f"Nothing catalogued for **{TAB_LABEL[resolved]}** yet.")
                return
            lines = [f"• {n}" for n in names]
            pages = [es.info_embed(
                f"🗂️ Archive — {TAB_LABEL[resolved]} ({len(names)})",
                description="\n".join(chunk))
                for chunk in chunk_lines(lines, PER_PAGE)]
            stamp_footers(pages)
            await paginate(self.bot, ctx, pages)
            return

        lines = [f"• **{TAB_LABEL.get(tb, tb)}** — {len(items)} item"
                 f"{'s' if len(items) != 1 else ''}"
                 for tb, items in archive.items()]
        await ctx.send(embed=es.info_embed(
            "🗂️ Item Archive",
            description="\n".join(lines) + "\n\n`!bp archive <tab>` to list one."))

    # ---- !count ---------------------------------------------------------

    @commands.command(name="rare", aliases=["rareitems"])
    async def rare(self, ctx, action: str = None, *, name: str = None):
        """
        Manage which items get highlighted (bold) in drop announcements.

          !rare                    — list the current rare items
          !rare add <text>         — highlight any drop containing this text
          !rare remove <text>      — stop highlighting it

        Matching is substring-based, so you can add a STEM to catch a whole family
        at once: `!rare add Catalyst of` highlights every "Catalyst of X" drop.
        Use a distinctive stem — a short one like "Fire" would also match
        "Firepower" etc. Rare drops are bolded, NOT starred (⭐ = focused crews).
        """
        act = (action or "").lower()
        if act in ("add", "remove") and name:
            if act == "add":
                ok = db.add_rare_item(name)
                await ctx.send(
                    f"✅ Drops containing **{name}** will be highlighted "
                    f"(matches anything with that text in the name)."
                    if ok else f"**{name}** is already on the rare list.")
            else:
                ok = db.remove_rare_item(name)
                await ctx.send(f"✅ Removed **{name}** from the rare list."
                               if ok else f"**{name}** wasn't on the rare list.")
            return

        items = db.get_rare_items()
        if not items:
            await ctx.send("No rare items set. Add one with `!rare add <text>` — "
                           "e.g. `!rare add Catalyst of` bolds every Catalyst drop.")
            return
        desc = "\n".join(f"• {i}" for i in sorted(items))
        await ctx.send(embed=es.info_embed(
            f"✨ Rare Items ({len(items)})",
            description=desc + "\n\n_Any drop containing these (as text) shows in "
                               "**bold**. Add a stem like `Catalyst of` to catch a "
                               "whole family._"))

    @commands.command(name="pots", aliases=["potgroups", "potions"])
    async def pots(self, ctx, group: str = None):
        """
        Show the potion groups (the sets you can cast with !pw pots).

          !pots           — list every group and how many pots it holds
          !pots free      — list the actual potions in one group
        """
        from outwar.constants import POT_GROUPS, POT_GROUP_LABELS, POTIONS

        if group:
            key = group.strip().lower()
            if key not in POT_GROUPS:
                opts = ", ".join(f"`{k}`" for k in POT_GROUPS)
                await ctx.send(f"Unknown pot group `{group}`. Try: {opts}")
                return
            names = [POTIONS.get(p, p) for p in POT_GROUPS[key]]
            desc = "\n".join(f"• {n}" for n in names)
            await ctx.send(embed=es.info_embed(
                f"🧪 {POT_GROUP_LABELS.get(key, key)} ({len(names)} pots)",
                description=desc))
            return

        # Overview of all groups.
        lines = []
        for k, pots in POT_GROUPS.items():
            label = POT_GROUP_LABELS.get(k, k)
            preview = ", ".join(POTIONS.get(p, p) for p in pots[:3])
            more = f" +{len(pots) - 3} more" if len(pots) > 3 else ""
            lines.append(f"**{label}** (`{k}`) — {len(pots)} pots\n   _{preview}{more}_")
        await ctx.send(embed=es.info_embed(
            "🧪 Potion Groups",
            description="\n".join(lines)
            + "\n\n`!pots <group>` for the full list · `!pw pots <watcher> <group>` to assign."))

    @commands.command(name="count")
    async def count(self, ctx, what: str = None, *, account: str = None):
        """
        Count what ONE account holds.

          !count pots <account>              — every potion + totals, and what's missing
          !count keys <account>              — same for keys
          !count "Lost Artifact of" <acct>   — everything matching a word/phrase

        A phrase matches anywhere in the name, so `Lost Artifact of` finds all of
        them without needing a wildcard character.
        """
        if not what:
            await ctx.send("Usage: `!count <tab|search> <account>`\n"
                           "e.g. `!count pots Guardian` · `!count \"Lost Artifact of\" Guardian`")
            return

        t = self._resolve_account(account)
        if not t:
            await ctx.send(f"Account `{account}` not found in trustees."
                           if account else "Give me an account name.")
            return
        suid = t.get("suid")
        if not suid:
            await ctx.send(f"**{t['name']}** has no suid recorded.")
            return

        tab = _resolve_tab(what)
        if tab:
            await self._count_tab(ctx, t, suid, tab)
        else:
            await self._count_search(ctx, t, suid, what)

    async def _count_tab(self, ctx, t, suid, tab):
        """Everything in one tab, with a 'missing' page from the archive."""
        status = await ctx.send(f"🔎 Counting {TAB_LABEL[tab]} for **{t['name']}**…")
        try:
            items = await self._fetch_tab(tab, suid)
        except Exception as e:
            await status.edit(content=f"Failed to read {TAB_LABEL[tab]}: {e}")
            return

        # Sum quantities per name (a tab can list the same item in several stacks).
        held = {}
        for it in items:
            held[it["item_name"]] = held.get(it["item_name"], 0) + it.get("quantity", 1)
        # Zero counts are never shown — an absent item is reported under "missing".
        held = {k: v for k, v in held.items() if v > 0}

        lines = [f"`{v:>4}` × {k}"
                 for k, v in sorted(held.items(), key=lambda x: (-x[1], x[0]))]
        total_qty = sum(held.values())
        header = f"**{len(held)}** distinct · **{total_qty}** total"

        pages = []
        if lines:
            for chunk in chunk_lines(lines, PER_PAGE):
                e = es.info_embed(f"🔎 {TAB_LABEL[tab]} — {t['name']}",
                                  description=f"{header}\n\n" + "\n".join(chunk))
                pages.append(e)
        else:
            pages.append(es.info_embed(f"🔎 {TAB_LABEL[tab]} — {t['name']}",
                                       description="_Holds none of these._"))

        # Missing = catalogued for this tab, but held in zero quantity.
        archived = set(db.get_item_archive().get(tab, {}).keys())
        missing = sorted(archived - set(held.keys()))
        if not archived:
            pages[-1].description += (
                f"\n\n_Nothing catalogued for {TAB_LABEL[tab]} yet, so no missing "
                f"list. Run_ `!bp scan {tab}`_._")
        elif not missing:
            pages[-1].description += "\n\n✅ **Missing:** none — holds every catalogued item."
        else:
            # Missing gets its own page(s) — it can be long, and it's the bit
            # you're usually looking for.
            mlines = [f"• {m}" for m in missing]
            for chunk in chunk_lines(mlines, PER_PAGE):
                pages.append(es.info_embed(
                    f"❌ Missing {TAB_LABEL[tab]} — {t['name']}",
                    description=f"**{len(missing)}** catalogued item"
                                f"{'s' if len(missing) != 1 else ''} not held\n\n"
                                + "\n".join(chunk)))

        stamp_footers(pages)
        await paginate(self.bot, ctx, pages, msg=status)

    async def _count_search(self, ctx, t, suid, needle):
        """Substring search across every tab for one account, paginated."""
        status = await ctx.send(f"🔎 Searching **{t['name']}** for “{needle}”…")
        needle_l = needle.strip().strip('"').lower()

        found = {}   # name -> {"qty": n, "tabs": set()}
        errors = []
        for tb in ALL_TABS:
            try:
                items = await self._fetch_tab(tb, suid)
            except Exception as e:
                errors.append(f"{TAB_LABEL[tb]}: {e}")
                continue
            for it in items:
                if needle_l in it["item_name"].lower():
                    rec = found.setdefault(it["item_name"], {"qty": 0, "tabs": set()})
                    rec["qty"] += it.get("quantity", 1)
                    rec["tabs"].add(TAB_LABEL[tb])

        if not found:
            note = ("\n\n_Some tabs failed: " + "; ".join(errors) + "_") if errors else ""
            await status.edit(content=None, embed=es.info_embed(
                f"🔎 “{needle}” — {t['name']}",
                description=f"No matches.{note}"))
            return

        rows = sorted(found.items(), key=lambda x: (-x[1]["qty"], x[0]))
        lines = [f"`{v['qty']:>4}` × {k}" for k, v in rows]
        total = sum(v["qty"] for _, v in rows)
        header = f"**{len(rows)}** distinct · **{total}** total"

        pages = []
        for chunk in chunk_lines(lines, PER_PAGE):
            pages.append(es.info_embed(
                f"🔎 “{needle}” — {t['name']}",
                description=f"{header}\n\n" + "\n".join(chunk)))
        if errors:
            pages[-1].description += "\n\n_Some tabs failed: " + "; ".join(errors) + "_"

        stamp_footers(pages)
        await paginate(self.bot, ctx, pages, msg=status)


async def setup(bot):
    await bot.add_cog(BackpackCommands(bot))
