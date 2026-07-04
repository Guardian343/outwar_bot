"""
embed_style.py — one place that defines how every DeathBot embed looks.

Goal: a clean, consistent visual language across spawn/death alerts, drop
summaries, reports and lists. Import the helpers here instead of building
raw discord.Embed objects, so the look is defined once and applied everywhere.

Design language (mirrors the cleaner drop-summary layout):
  • A small accent colour bar per category (Discord shows the left stripe).
  • A short summary line in the description (counts / totals), not crammed
    into field names.
  • Full-width fields with the headline carrying the key number, details
    bulleted underneath.
  • A consistent footer brand so every embed reads as part of the same bot.
"""

import discord
from datetime import datetime, timezone


# ── Category colours ────────────────────────────────────────────────────────
# One colour per *meaning*, used consistently everywhere.
COLOR_SPAWN   = discord.Color.from_rgb(0xF0, 0x9F, 0x27)  # amber — something appeared
COLOR_DEATH   = discord.Color.from_rgb(0xD8, 0x5A, 0x30)  # coral — something died
COLOR_DROPS   = discord.Color.from_rgb(0x7F, 0x77, 0xDD)  # purple — loot
COLOR_REPORT  = discord.Color.from_rgb(0x1D, 0x9E, 0x75)  # teal — an action completed
COLOR_INFO    = discord.Color.from_rgb(0x37, 0x8A, 0xDD)  # blue — neutral info / lists
COLOR_WARN    = discord.Color.from_rgb(0xBA, 0x75, 0x17)  # dark amber — caution
COLOR_ERROR   = discord.Color.from_rgb(0xA3, 0x2D, 0x2D)  # red — problem

BRAND_FOOTER  = "DeathBot · LoD"

# Icons used in headlines — kept consistent so categories are recognisable.
ICON_SPAWN  = "🌟"
ICON_DEATH  = "💀"
ICON_DROPS  = "📦"
ICON_REPORT = "✅"
ICON_STATS  = "📊"
ICON_HEALTH = "🏥"
ICON_BOSS   = "⚔️"
ICON_GOD    = "👑"
ICON_ENVOY  = "✉️"
ICON_NODROP = "❌"
ICON_STAR   = "⭐"


def base_embed(title: str, color: discord.Color, description: str = None) -> discord.Embed:
    """Create an embed with the house style already applied."""
    embed = discord.Embed(title=title, color=color, description=description)
    embed.timestamp = datetime.now(timezone.utc)
    embed.set_footer(text=BRAND_FOOTER)
    return embed


def spawn_embed(title: str, description: str = None) -> discord.Embed:
    return base_embed(title, COLOR_SPAWN, description)


def death_embed(title: str, description: str = None) -> discord.Embed:
    return base_embed(title, COLOR_DEATH, description)


def drops_embed(title: str, description: str = None) -> discord.Embed:
    return base_embed(title, COLOR_DROPS, description)


def report_embed(title: str, description: str = None) -> discord.Embed:
    return base_embed(title, COLOR_REPORT, description)


def info_embed(title: str, description: str = None) -> discord.Embed:
    return base_embed(title, COLOR_INFO, description)


def warn_embed(title: str, description: str = None) -> discord.Embed:
    return base_embed(title, COLOR_WARN, description)


def kills_label(kills: int, pct: float = None) -> str:
    """Consistent '6 kills (18.8%)' / '1 kill' formatting."""
    if not kills:
        return ""
    unit = "kill" if kills == 1 else "kills"
    if pct:
        return f"{kills} {unit} ({pct:.1f}%)"
    return f"{kills} {unit}"


def crew_header(crew_name: str, is_focus: bool = False, kills: int = 0, pct: float = None) -> str:
    """Build a consistent crew field headline, e.g. '⭐ Charmin Ultra — 6 kills (18.8%)'."""
    star = f"{ICON_STAR} " if is_focus else ""
    kl   = kills_label(kills, pct)
    return f"{star}{crew_name} — {kl}" if kl else f"{star}{crew_name}"


def bullet_list(items, prefix: str = "• ") -> str:
    """Render a list of strings as a bulleted block."""
    return "\n".join(f"{prefix}{it}" for it in items)
