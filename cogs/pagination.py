"""
pagination.py

Shared reaction-based page navigation for long Discord output.

This is the same ⏮ ◀ ▶ ⏭ mechanism `!check-md` uses, pulled out so any command
can reuse it instead of copy-pasting the listener plumbing.

Not a cog — an importable helper, like embed_style.py. It isn't in main.py's
extension list and has no setup().

Usage:
    from cogs.pagination import paginate, chunk_lines

    pages = [embed1, embed2, ...]
    await paginate(self.bot, ctx, pages)

Only the person who ran the command can turn the pages. After `timeout` seconds
of no input the reactions are cleared and the message is left on its last page.
"""

import asyncio
import discord

# The navigation reactions, in the order they're added to the message.
NAV_EMOJIS = ("⏮", "◀", "▶", "⏭")
_NAV_SET = set(NAV_EMOJIS)


def chunk_lines(lines: list, per_page: int = 25) -> list:
    """Split a list of text lines into pages of at most `per_page` lines."""
    if not lines:
        return []
    return [lines[i:i + per_page] for i in range(0, len(lines), per_page)]


def stamp_footers(pages: list, suffix: str = ""):
    """
    Write 'Page X/Y' into every embed's footer.

    Keeps any existing footer text and appends the counter, so callers can set
    their own note (e.g. a hint) and still get numbering.
    """
    total = len(pages)
    for i, e in enumerate(pages):
        base = ""
        if e.footer and e.footer.text:
            base = e.footer.text + "  •  "
        tail = f"  •  {suffix}" if suffix else ""
        e.set_footer(text=f"{base}Page {i + 1}/{total}{tail}")
    return pages


async def paginate(bot, ctx, pages: list, *, timeout: float = 120.0, msg=None):
    """
    Show `pages` (a list of discord.Embed) with reaction navigation.

    bot     — needed to add/remove the raw reaction listeners
    ctx     — the command context; only ctx.author may navigate
    pages   — list of embeds; a single page is sent without any reactions
    timeout — seconds of inactivity before navigation stops (default 120)
    msg     — an existing message to edit (e.g. a "working…" status) instead of
              sending a new one

    Returns the message that was sent/edited.
    """
    if not pages:
        return None

    # One page needs no navigation — don't clutter it with reactions.
    if len(pages) == 1:
        if msg:
            await msg.edit(content=None, embed=pages[0])
            return msg
        return await ctx.send(embed=pages[0])

    if msg:
        await msg.edit(content=None, embed=pages[0])
    else:
        msg = await ctx.send(embed=pages[0])

    for emoji in NAV_EMOJIS:
        try:
            await msg.add_reaction(emoji)
        except Exception:
            pass  # missing perms shouldn't kill the command — the text still shows

    current = 0
    queue: asyncio.Queue = asyncio.Queue()

    async def on_reaction(payload):
        # Only react to the original caller, on this message, for our emojis.
        if (payload.message_id == msg.id
                and payload.user_id == ctx.author.id
                and str(payload.emoji) in _NAV_SET):
            queue.put_nowait(str(payload.emoji))

    # Listen for add AND remove, so a page turns whether the user clicks the
    # reaction on or off — no need to un-react between pages.
    bot.add_listener(on_reaction, "on_raw_reaction_add")
    bot.add_listener(on_reaction, "on_raw_reaction_remove")

    try:
        while True:
            try:
                emoji = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                break

            if emoji == "▶":
                current = (current + 1) % len(pages)     # wraps around
            elif emoji == "◀":
                current = (current - 1) % len(pages)
            elif emoji == "⏭":
                current = len(pages) - 1
            elif emoji == "⏮":
                current = 0

            try:
                await msg.edit(embed=pages[current])
            except Exception:
                break  # message deleted or edit failed — stop cleanly
    finally:
        bot.remove_listener(on_reaction, "on_raw_reaction_add")
        bot.remove_listener(on_reaction, "on_raw_reaction_remove")
        try:
            await msg.clear_reactions()
        except Exception:
            pass

    return msg
