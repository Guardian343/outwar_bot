"""
table_image.py — Renders a styled table as a PNG image for Discord posting.
Mimics the dark-themed cap/stats table style.
"""

from PIL import Image, ImageDraw, ImageFont
import io

# Fonts
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD    = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Colours
BG_DARK      = (18,  20,  42)
BG_HEADER    = (30,  34,  66)
BG_ROW_A     = (28,  32,  58)
BG_ROW_B     = (22,  26,  50)
BG_TITLE     = (15,  17,  38)
ACCENT       = (120, 124, 255)   # indigo accent line
TEXT_HEADER  = (180, 185, 230)   # brighter column headers
TEXT_WHITE   = (248, 250, 255)   # near white
TEXT_DIM     = (155, 160, 200)   # was too dark, lifted significantly
TEXT_GREEN   = ( 72, 230, 170)   # brighter green
TEXT_RED     = (255, 100, 100)   # brighter red
TEXT_GOLD    = (255, 205,  60)   # brighter gold
TEXT_BLUE    = (130, 185, 255)   # brighter blue
DIVIDER      = ( 40,  44,  80)

ROW_H        = 34
HEADER_H     = 42
TITLE_H      = 80
PADDING_X    = 22
PADDING_Y    = 18
CORNER_R     = 12


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)
    except Exception:
        return ImageFont.load_default()


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return draw.textlength(text, font=font)


def render_table(
    title: str,
    subtitle: str,
    columns: list[dict],   # [{"key": str, "label": str, "align": "left"|"right"|"center", "color_fn": callable}]
    rows: list[dict],
    footer: str = "",
    accent: tuple = None,
) -> io.BytesIO:
    """
    Render a styled dark table as PNG.
    columns: list of {key, label, align, width (optional), color_fn (optional)}
    rows: list of dicts matching column keys
    accent: optional RGB tuple for the title accent lines (defaults to module ACCENT)
    Returns a BytesIO PNG.
    """
    accent = accent or ACCENT
    font_title   = _load_font(34, bold=True)
    font_sub     = _load_font(22, bold=True)
    font_header  = _load_font(13, bold=True)
    font_row     = _load_font(13)
    font_footer  = _load_font(12)

    # Measure column widths
    img_tmp  = Image.new("RGB", (1, 1))
    draw_tmp = ImageDraw.Draw(img_tmp)

    for col in columns:
        if "width" not in col:
            # Auto width from header + all row values
            w = int(_text_w(draw_tmp, col["label"], font_header)) + 32
            for row in rows:
                val = str(row.get(col["key"], ""))
                w = max(w, int(_text_w(draw_tmp, val, font_row)) + 32)
            col["width"] = w

    total_w = PADDING_X * 2 + sum(c["width"] for c in columns)

    # Ensure table is wide enough for the title text
    title_min_w = int(_text_w(draw_tmp, title, _load_font(34, bold=True))) + PADDING_X * 4
    if subtitle:
        sub_min_w = int(_text_w(draw_tmp, subtitle, _load_font(13))) + PADDING_X * 4
        title_min_w = max(title_min_w, sub_min_w)
    total_w = max(total_w, title_min_w)
    n_rows  = len(rows)

    title_block  = TITLE_H + 12 + (28 if subtitle else 0)
    header_block = HEADER_H
    rows_block   = ROW_H * n_rows
    footer_lines = footer.split("\n") if footer else []
    footer_block = (ROW_H * len(footer_lines) + 8) if footer_lines else 0
    total_h      = PADDING_Y + title_block + header_block + rows_block + footer_block + PADDING_Y

    img  = Image.new("RGB", (total_w, total_h), BG_DARK)
    draw = ImageDraw.Draw(img)

    # Title bar
    draw.rectangle([0, 0, total_w, title_block + PADDING_Y], fill=BG_TITLE)
    # Top accent line
    draw.rectangle([0, 0, total_w, 3], fill=accent)
    # Bottom accent line
    draw.rectangle([0, title_block + PADDING_Y - 3, total_w, title_block + PADDING_Y], fill=accent)

    title_x = PADDING_X
    if subtitle:
        # Centre title horizontally
        title_w = _text_w(draw, title, font_title)
        title_cx = (total_w - title_w) // 2
        draw.text((title_cx, PADDING_Y // 2 + 4), title, font=font_title, fill=TEXT_WHITE)
        sub_w = _text_w(draw, subtitle, font_sub)
        sub_cx = (total_w - sub_w) // 2
        draw.text((sub_cx, PADDING_Y // 2 + 44), subtitle, font=font_sub, fill=TEXT_GOLD)
    else:
        title_w = _text_w(draw, title, font_title)
        title_cx = (total_w - title_w) // 2
        title_cy = (title_block + PADDING_Y - 26) // 2
        draw.text((title_cx, title_cy), title, font=font_title, fill=TEXT_WHITE)

    # Column headers
    hdr_y = PADDING_Y + title_block
    draw.rectangle([0, hdr_y, total_w, hdr_y + HEADER_H], fill=BG_HEADER)
    x = PADDING_X
    for col in columns:
        lbl_w = _text_w(draw, col["label"], font_header)
        align = col.get("align", "left")
        if align == "right":
            lx = x + col["width"] - lbl_w - 12
        elif align == "center":
            lx = x + (col["width"] - lbl_w) // 2
        else:
            lx = x + 12
        draw.text((lx, hdr_y + (HEADER_H - 13) // 2), col["label"], font=font_header, fill=TEXT_HEADER)
        x += col["width"]

    # Divider under header
    draw.rectangle([PADDING_X, hdr_y + HEADER_H - 1, total_w - PADDING_X, hdr_y + HEADER_H], fill=DIVIDER)

    # Rows
    row_y = hdr_y + HEADER_H
    for i, row in enumerate(rows):
        bg = BG_ROW_A if i % 2 == 0 else BG_ROW_B
        draw.rectangle([0, row_y, total_w, row_y + ROW_H], fill=bg)

        x = PADDING_X
        for col in columns:
            val   = str(row.get(col["key"], ""))
            color = TEXT_WHITE
            if col.get("color_fn"):
                color = col["color_fn"](row) or TEXT_WHITE

            val_w = _text_w(draw, val, font_row)
            align = col.get("align", "left")
            if align == "right":
                vx = x + col["width"] - val_w - 12
            elif align == "center":
                vx = x + (col["width"] - val_w) // 2
            else:
                vx = x + 12

            draw.text((vx, row_y + (ROW_H - 13) // 2), val, font=font_row, fill=color)
            x += col["width"]

        row_y += ROW_H

    # Footer
    if footer:
        footer_lines = footer.split("\n")
        footer_h = ROW_H * len(footer_lines) + 8
        draw.rectangle([0, row_y, total_w, row_y + footer_h], fill=BG_HEADER)
        draw.rectangle([PADDING_X, row_y, total_w - PADDING_X, row_y + 1], fill=DIVIDER)
        for i, line in enumerate(footer_lines):
            draw.text((PADDING_X, row_y + 8 + i * ROW_H), line, font=font_footer, fill=TEXT_DIM)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def render_caps_table(group: str, results: list[dict]) -> io.BytesIO:
    """Render a cap status table image."""
    capped     = sum(1 for r in results if not r["error"] and r["max"] > 0 and r["cur"] <= 0)
    not_capped = sum(1 for r in results if not r["error"] and r["max"] > 0 and r["cur"] > 0)

    if capped == len(results):
        subtitle = "ALL CAPPED"
    elif not_capped == len(results):
        subtitle = "NOT CAPPED"
    else:
        subtitle = f"{not_capped} NOT CAPPED  ·  {capped} CAPPED"

    def cap_color(row):
        if row.get("error") or row.get("max", 0) == 0:
            return TEXT_DIM
        if row["cur"] <= 0:
            return TEXT_RED
        return TEXT_GREEN

    def name_color(row):
        if row.get("error") or row.get("max", 0) == 0:
            return TEXT_DIM
        if row["cur"] <= 0:
            return TEXT_RED
        return TEXT_WHITE

    columns = [
        {"key": "name",     "label": "Character",  "align": "left",   "color_fn": name_color},
        {"key": "faction",  "label": "Faction",    "align": "left",   "color_fn": lambda r: TEXT_BLUE},
        {"key": "caps_str", "label": "Caps",       "align": "center", "color_fn": cap_color},
        {"key": "crew",     "label": "Crew",       "align": "left",   "color_fn": lambda r: TEXT_DIM},
        {"key": "rage_str", "label": "Rage",       "align": "right",  "color_fn": lambda r: TEXT_GOLD},
    ]

    rows = []
    for r in results:
        rows.append({
            "name":     r["name"],
            "faction":  r.get("faction", "—"),
            "caps_str": f"{r['cur']}/{r['max']}" if r.get("max") else "—",
            "crew":     r.get("crew", "—"),
            "rage_str": f"{r.get('rage', 0):,}" if r.get("rage") else "—",
            "cur":      r.get("cur", 0),
            "max":      r.get("max", 0),
            "error":    r.get("error", False),
        })

    footer = f"{not_capped} not capped  ·  {capped} capped  ·  {len(results)} total"
    return render_table(f"CAP STATUS — {group.upper()}", subtitle, columns, rows, footer)


def render_stats_table(group: str, results: list[dict]) -> io.BytesIO:
    """Render a group stats table image."""

    def _fmt(n):
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(n)

    avg_power = sum(r["power"]     for r in results) // max(len(results), 1)
    avg_ele   = sum(r["elemental"] for r in results) // max(len(results), 1)
    avg_chaos = sum(r["chaos"]     for r in results) // max(len(results), 1)

    # Faction totals
    faction_totals: dict[str, int] = {}
    for r in results:
        faction = r.get("faction") or "None"
        if faction.lower() == "none":
            continue
        flvl = r.get("faction_level", 0)
        faction_totals[faction] = faction_totals.get(faction, 0) + flvl

    faction_str = "  ·  ".join(
        f"{name} ({total})" for name, total in sorted(faction_totals.items())
    ) if faction_totals else ""

    columns = [
        {"key": "name",       "label": "Character",  "align": "left",   "color_fn": None},
        {"key": "faction_str","label": "Faction",    "align": "left",   "color_fn": lambda r: TEXT_BLUE},
        {"key": "power_str",  "label": "Power",      "align": "right",  "color_fn": lambda r: TEXT_WHITE},
        {"key": "ele_str",    "label": "Elemental",  "align": "right",  "color_fn": lambda r: TEXT_GREEN},
        {"key": "chaos_str",  "label": "Chaos",      "align": "right",  "color_fn": lambda r: TEXT_GOLD},
    ]

    rows = []
    for r in results:
        faction = r.get("faction") or "None"
        flvl    = r.get("faction_level", 0)
        rows.append({
            "name":        r["name"],
            "faction_str": f"{faction} ({flvl})" if flvl else faction,
            "power_str":   _fmt(r["power"]),
            "ele_str":     _fmt(r["elemental"]),
            "chaos_str":   _fmt(r["chaos"]),
        })

    footer_lines = [
        f"Avg Power: {_fmt(avg_power)}  ·  Avg Ele: {_fmt(avg_ele)}  ·  Avg Chaos: {_fmt(avg_chaos)}  ·  {len(results)} characters",
    ]
    if faction_str:
        footer_lines.append(faction_str)

    return render_table(f"GROUP STATS — {group.upper()}", "", columns, rows, "\n".join(footer_lines))


def render_rage_table(group: str, results: list[dict]) -> io.BytesIO:
    """Render a rage table image with visual bars."""
    max_rage = max((r["rage"] for r in results), default=1) or 1
    total    = sum(r["rage"] for r in results)
    avg      = total // max(len(results), 1)

    def _bar(rage):
        filled = round((rage / max_rage) * 12)
        return "█" * filled + "░" * (12 - filled)

    def rage_color(row):
        pct = row["rage"] / max_rage if max_rage else 0
        if pct >= 0.8:
            return TEXT_GREEN
        if pct >= 0.4:
            return TEXT_WHITE
        return TEXT_RED

    columns = [
        {"key": "name",     "label": "Character", "align": "left",  "color_fn": None},
        {"key": "bar",      "label": "Rage",       "align": "left",  "color_fn": rage_color},
        {"key": "rage_str", "label": "",           "align": "right", "color_fn": rage_color},
    ]

    rows = [
        {
            "name":     r["name"],
            "bar":      _bar(r["rage"]),
            "rage_str": f"{r['rage']:,}",
            "rage":     r["rage"],
        }
        for r in results
    ]

    footer = f"Total: {total:,}  ·  Avg: {avg:,}  ·  {len(results)} characters"
    return render_table(f"RAGE — {group.upper()}", "", columns, rows, footer)


def render_who_table(name: str, data: dict) -> io.BytesIO:
    """Render a single character info card as an image."""
    cap_cur = data.get("cap_cur", 0)
    cap_max = data.get("cap_max", 0)
    capped  = cap_max > 0 and cap_cur <= 0

    columns = [
        {"key": "label", "label": "Stat",  "align": "left",  "color_fn": lambda r: TEXT_HEADER},
        {"key": "value", "label": "Value", "align": "right", "color_fn": lambda r: r.get("color", TEXT_WHITE)},
    ]

    def _fmt(n):
        if not n:
            return "—"
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(n)

    faction = data.get("faction") or "None"
    flvl    = data.get("faction_level", 0)
    rows = [
        {"label": "Crew",       "value": data.get("crew") or "—",                          "color": TEXT_DIM},
        {"label": "Level",      "value": str(data.get("level", "—")),                       "color": TEXT_WHITE},
        {"label": "Rage",       "value": f"{data.get('rage', 0):,}",                        "color": TEXT_GOLD},
        {"label": "Power",      "value": _fmt(data.get("power", 0)),                        "color": TEXT_WHITE},
        {"label": "Elemental",  "value": _fmt(data.get("elemental", 0)),                    "color": TEXT_GREEN},
        {"label": "Chaos",      "value": _fmt(data.get("chaos", 0)),                        "color": TEXT_GOLD},
        {"label": "Faction",    "value": f"{faction} ({flvl})" if flvl else faction,        "color": TEXT_BLUE},
        {"label": "God Cap",    "value": f"{cap_cur}/{cap_max}" if cap_max else "—",        "color": TEXT_RED if capped else TEXT_GREEN},
    ]

    subtitle = "CAPPED" if capped else "NOT CAPPED"
    return render_table(f"CHARACTER — {name.upper()}", subtitle, columns, rows, "")


def render_status_table(data: dict) -> io.BytesIO:
    """Render bot status as an image card."""
    columns = [
        {"key": "label", "label": "Info",   "align": "left",  "color_fn": lambda r: TEXT_HEADER},
        {"key": "value", "label": "Value",  "align": "right", "color_fn": lambda r: r.get("color", TEXT_WHITE)},
    ]

    rows = [
        {"label": "Uptime",           "value": data.get("uptime", "—"),         "color": TEXT_GREEN},
        {"label": "Session User",     "value": data.get("session_user", "—"),   "color": TEXT_WHITE},
        {"label": "Trustees",         "value": str(data.get("trustees", 0)),    "color": TEXT_WHITE},
        {"label": "Gods in DB",       "value": str(data.get("gods", 0)),        "color": TEXT_WHITE},
        {"label": "Currently Spawned","value": str(data.get("spawned", 0)),     "color": TEXT_GOLD},
        {"label": "Groups",           "value": str(data.get("groups", 0)),      "color": TEXT_WHITE},
        {"label": "God Alerts",       "value": data.get("god_channel", "Not set"),  "color": TEXT_BLUE if data.get("god_channel") else TEXT_RED},
        {"label": "Boss Alerts",      "value": data.get("boss_channel", "Not set"), "color": TEXT_BLUE if data.get("boss_channel") else TEXT_RED},
    ]

    return render_table("DEATHBOT STATUS", "", columns, rows, "")


def render_ranking_table(title: str, ranked: list[dict], stat_label: str) -> io.BytesIO:
    """
    Render a ranking table as an image.
    ranked: list of {rank, name, value}
    """
    if not ranked:
        return render_table(title, "", [], [], "")

    total   = sum(r["value"] for r in ranked)
    average = total // len(ranked) if ranked else 0

    def _fmt(n):
        return f"{n:,}"

    subtitle = f"Total: {_fmt(total)}  ·  Average: {_fmt(average)}"

    columns = [
        {"key": "rank",  "label": "#",        "align": "right", "color_fn": lambda r: TEXT_DIM,   "width": 40},
        {"key": "name",  "label": "Character", "align": "left",  "color_fn": None},
        {"key": "value", "label": stat_label,  "align": "right", "color_fn": lambda r: TEXT_GREEN},
    ]

    rows = [
        {"rank": str(r["rank"]), "name": r["name"], "value": _fmt(r["value"])}
        for r in ranked
    ]

    # Footer with total/average only — names sent as separate text
    return render_table(title, subtitle, columns, rows, "")


def render_boss_table(bosses: list[dict]) -> io.BytesIO:
    """
    Render a boss status table image.
    bosses: list of {name, spawned, hp_pct, status, spawn_window}
    """
    def status_color(row):
        s = row.get("status", "").upper()
        if s == "ALIVE":
            return TEXT_GREEN
        if s == "NEAR":
            return TEXT_GOLD
        return TEXT_RED

    def hp_color(row):
        pct = row.get("hp_pct", 0)
        if pct > 50:
            return TEXT_GREEN
        if pct > 20:
            return TEXT_GOLD
        return TEXT_RED

    columns = [
        {"key": "name",         "label": "Boss",         "align": "left",  "color_fn": None},
        {"key": "status",       "label": "Status",       "align": "center","color_fn": status_color},
        {"key": "hp_str",       "label": "HP",           "align": "right", "color_fn": hp_color},
        {"key": "spawn_window", "label": "Spawn Window", "align": "left",  "color_fn": lambda r: TEXT_DIM},
    ]

    rows = []
    for b in bosses:
        rows.append({
            "name":         b["name"],
            "status":       b.get("status", "—"),
            "hp_str":       b.get("hp_str", "—"),
            "hp_pct":       b.get("hp_pct", 0),
            "spawn_window": b.get("spawn_window", "—"),
        })

    return render_table("BOSS SPAWNS FOR SIGIL", "", columns, rows, "")


def render_uncapped_table(god_name: str, required: int, ready: list, not_ready: list) -> io.BytesIO:
    """
    Render uncapped groups table.
    ready/not_ready: list of (group_name, available, total, capped_n)
    """
    # Use short name if available
    from outwar.scraper import GOD_SHORT_NAMES
    short = GOD_SHORT_NAMES.get(god_name, god_name.split(",")[0].split(" ")[0])
    title    = "UNCAPPED"
    subtitle = short.upper()

    all_rows = []
    for name, avail, total, capped in ready:
        all_rows.append({
            "group":   name,
            "status":  "READY",
            "avail":   f"{avail}/{total}",
            "capped":  str(capped) if capped else "0",
            "_ready":  True,
        })
    for name, avail, total, capped in not_ready:
        all_rows.append({
            "group":   name,
            "status":  "NOT READY",
            "avail":   f"{avail}/{total}",
            "capped":  str(capped) if capped else "0",
            "_ready":  False,
        })

    def status_color(row):
        return TEXT_GREEN if row.get("_ready") else TEXT_RED

    def group_color(row):
        return TEXT_WHITE if row.get("_ready") else TEXT_DIM

    columns = [
        {"key": "group",  "label": "Group",     "align": "left",   "color_fn": group_color},
        {"key": "status", "label": "Status",    "align": "center", "color_fn": status_color},
        {"key": "avail",  "label": "Available", "align": "center", "color_fn": status_color},
        {"key": "capped", "label": "Capped",    "align": "center", "color_fn": lambda r: TEXT_RED if r["capped"] != "0" else TEXT_DIM},
    ]

    footer = f"Requires {required} members  ·  {len(ready)} ready  ·  {len(not_ready)} not ready"
    return render_table(title, subtitle, columns, all_rows, footer)


def render_raid_summary(god_name: str, data: dict) -> io.BytesIO:
    """Render a raid summary as an image."""
    from outwar.scraper import GOD_SHORT_NAMES
    short = GOD_SHORT_NAMES.get(god_name, god_name.split(",")[0].split(" ")[0])
    title    = "RAID SUMMARY"
    subtitle = short.upper()

    columns = [
        {"key": "label", "label": "Stat",  "align": "left",  "color_fn": lambda r: TEXT_HEADER},
        {"key": "value", "label": "Value", "align": "right", "color_fn": lambda r: r.get("color", TEXT_WHITE)},
    ]

    def _fmt(n):
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(n)

    rows = [
        {"label": "Group",        "value": data.get("group", "—"),           "color": TEXT_WHITE},
        {"label": "Attempts",     "value": str(data.get("attempts", 0)),      "color": TEXT_WHITE},
        {"label": "Wins",         "value": str(data.get("wins", 0)),          "color": TEXT_GREEN if data.get("wins", 0) > 0 else TEXT_RED},
        {"label": "Win Rate",     "value": data.get("win_rate", "0%"),        "color": TEXT_GREEN if data.get("wins", 0) > 0 else TEXT_GOLD},
        {"label": "Total Damage", "value": _fmt(data.get("total_damage", 0)),"color": TEXT_GOLD},
        {"label": "Time",         "value": data.get("elapsed", "—"),          "color": TEXT_DIM},
    ]

    won = data.get("wins", 0) >= data.get("target_wins", 1)
    footer = "✓ Target reached" if won else "✗ Target not reached"
    return render_table(title, subtitle, columns, rows, footer)


def render_gods_table(gods: list[dict], spawned_only: bool = False) -> io.BytesIO:
    """Render prime god spawn status table."""
    if spawned_only:
        display = [g for g in gods if g.get("spawned")]
    else:
        display = gods

    def name_color(row):
        return TEXT_WHITE

    columns = [
        {"key": "name",   "label": "God",       "align": "left",   "color_fn": name_color},
        {"key": "short",  "label": "Alias",     "align": "left",   "color_fn": lambda r: TEXT_BLUE},
        {"key": "rec",    "label": "Rec",       "align": "center", "color_fn": lambda r: TEXT_DIM},
    ]

    rows = []
    for g in sorted(display, key=lambda x: x.get("name", "")):
        rows.append({
            "name":  g.get("name", "—"),
            "short": g.get("short_name") or "—",
            "rec":   str(g.get("recommended", "—")),
        })

    subtitle = f"{len(display)} currently spawned" if spawned_only else f"{sum(1 for g in gods if g.get('spawned'))} spawned  ·  {sum(1 for g in gods if not g.get('spawned'))} dead"
    return render_table("PRIME GODS", subtitle, columns, rows, "")


def render_boss_raid_summary(crew_name: str, boss_name: str, data: dict) -> io.BytesIO:
    """Render a boss raid session summary image."""
    def _fmt(n):
        if not n: return "—"
        if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
        if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
        if n >= 1_000: return f"{n/1_000:.1f}K"
        return str(n)

    raids        = data.get("raids", 0) or 0
    total_damage = data.get("total_damage", 0) or 0
    best_raid    = data.get("best_raid", 0) or 0
    avg_raid     = (total_damage // raids) if raids else 0
    elapsed      = data.get("elapsed", "—")

    columns = [
        {"key": "label", "label": "Stat",  "align": "left",  "color_fn": lambda r: (150, 200, 185)},
        {"key": "value", "label": "Value", "align": "right", "color_fn": lambda r: r.get("color", TEXT_WHITE)},
    ]

    rows = [
        {"label": "Crew",         "value": crew_name,            "color": TEXT_WHITE},
        {"label": "Boss",         "value": boss_name,            "color": TEXT_WHITE},
        {"label": "Raids",        "value": str(raids),           "color": TEXT_WHITE},
        {"label": "Total Damage", "value": _fmt(total_damage),   "color": TEXT_GOLD},
        {"label": "Avg / Raid",   "value": _fmt(avg_raid),       "color": TEXT_GREEN},
        {"label": "Best Raid",    "value": _fmt(best_raid),      "color": TEXT_GOLD},
        {"label": "Time",         "value": elapsed,              "color": TEXT_DIM},
    ]

    # New visual identity: brand teal accent + summary line + DeathBot · LoD footer
    BRAND_TEAL = (29, 158, 117)
    summary    = f"{raids} raids · {_fmt(total_damage)} dmg · {elapsed}"
    resuming   = data.get("resume_mins", 0)
    footer     = (f"DeathBot · LoD   ·   Raids resuming in {resuming} min"
                  if resuming > 0 else "DeathBot · LoD   ·   Session complete")
    return render_table("BOSS RAIDS COMPLETE", summary, columns, rows, footer, accent=BRAND_TEAL)


def render_boss_records_table(records: dict) -> io.BytesIO:
    """Render all-time best raid damage per boss."""
    def _fmt(n):
        if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
        if n >= 1_000_000:     return f"{n/1_000_000:.1f}M"
        if n >= 1_000:         return f"{n/1_000:.1f}K"
        return str(n)

    columns = [
        {"key": "boss",   "label": "Boss",         "align": "left",  "color_fn": lambda r: TEXT_WHITE},
        {"key": "damage", "label": "Best Raid",     "align": "right", "color_fn": lambda r: TEXT_GOLD},
    ]
    rows = []
    for key, val in sorted(records.items(), key=lambda x: x[1].get("best", 0), reverse=True):
        rows.append({
            "boss":   val.get("boss_full", key.title()),
            "damage": _fmt(val.get("best", 0)),
        })

    if not rows:
        rows = [{"boss": "No records yet", "damage": "—"}]

    return render_table("BOSS RAID RECORDS", "ALL TIME BEST", columns, rows)


def render_compare_table(char1: dict, char2: dict) -> io.BytesIO:
    """Render a side-by-side stat comparison of two characters."""
    def _fmt(n):
        if not n: return "—"
        if isinstance(n, int) and n >= 1_000: return f"{n:,}"
        return str(n)

    name1 = char1.get("name", "Char 1")
    name2 = char2.get("name", "Char 2")

    columns = [
        {"key": "stat",  "label": "Stat",  "align": "left",  "color_fn": lambda r: (186, 190, 240)},
        {"key": "val1",  "label": name1,   "align": "right", "color_fn": lambda r: r.get("c1", TEXT_WHITE)},
        {"key": "val2",  "label": name2,   "align": "right", "color_fn": lambda r: r.get("c2", TEXT_WHITE)},
    ]

    def _compare(v1, v2):
        """Return colours — green for higher, red for lower, white for equal."""
        try:
            n1, n2 = int(str(v1).replace(",","")), int(str(v2).replace(",",""))
            if n1 > n2:   return TEXT_GREEN, TEXT_RED
            elif n1 < n2: return TEXT_RED,   TEXT_GREEN
        except (ValueError, TypeError):
            pass
        return TEXT_WHITE, TEXT_WHITE

    stats = [
        ("Level",    "level",    "level"),
        ("Power",    "power",    "power"),
        ("Elemental","elemental","elemental"),
        ("Chaos",    "chaos",    "chaos"),
        ("HP",       "hp",       "hp"),
        ("ATK",      "atk",      "atk"),
        ("Rage",     "rage",     "rage"),
        ("Crew",     "crew",     "crew"),
        ("Faction",  "faction",  "faction"),
    ]

    rows = []
    for label, k1, k2 in stats:
        v1 = char1.get(k1, "—")
        v2 = char2.get(k2, "—")
        c1, c2 = _compare(v1, v2)
        rows.append({"stat": label, "val1": _fmt(v1), "val2": _fmt(v2), "c1": c1, "c2": c2})

    return render_table("CHARACTER COMPARE", f"{name1} vs {name2}", columns, rows)
