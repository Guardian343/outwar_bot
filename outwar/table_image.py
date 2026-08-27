"""
table_image.py — Renders a styled table as a PNG image for Discord posting.
Mimics the dark-themed cap/stats table style.
"""

from PIL import Image, ImageDraw, ImageFont, ImageSequence


def pick_item_frame(im: "Image.Image") -> "Image.Image":
    """
    Outwar item/augment icons are often animated GIFs with a glow/fade effect. PIL
    opens them on frame 0, which for a fade is usually the DIMMEST frame (item nearly
    invisible, only the background glow) — so composited items looked like "just the
    background effect". This scans all frames and returns the one with the most visible
    content (highest mean luminance over opaque pixels). Static images pass through.
    """
    try:
        frames = [fr.convert("RGBA") for fr in ImageSequence.Iterator(im)]
        if len(frames) <= 1:
            return im.convert("RGBA")

        def _score(f):
            px = f.load()
            w, h = f.size
            step = max(1, min(w, h) // 24)
            tot, n = 0.0, 0
            for yy in range(0, h, step):
                for xx in range(0, w, step):
                    r, g, b, a = px[xx, yy]
                    if a > 20:
                        tot += (r + g + b); n += 1
            return (tot / n) if n else 0.0

        return max(frames, key=_score)
    except Exception:
        try:
            return im.convert("RGBA")
        except Exception:
            return im
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
TEXT_ORANGE  = (255, 150,  50)   # warning orange — nearly capped
TEXT_BLUE    = (130, 185, 255)   # brighter blue
DIVIDER      = ( 40,  44,  80)
SLOT_BG      = ( 24,  27,  52)   # paperdoll slot fill (behind each item)
SLOT_EDGE    = ( 70,  76,  120)  # paperdoll slot frame

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


# Faction colours — exact Outwar in-game colour codes:
#   Alvar = elemental (blue) · Delruk = power (orange) · Vordyn = chaos (purple)
FACTION_COLOURS = {
    "alvar":  (0x3a, 0xa0, 0xff),   # #3aa0ff
    "delruk": (0xff, 0x8a, 0x1a),   # #ff8a1a
    "vordyn": (0xc4, 0x4d, 0xff),   # #c44dff
}


def faction_colour(name: str) -> tuple:
    """RGB for a faction name (case-insensitive), stripping any '(level)' suffix.
    Falls back to a neutral dim colour for unknown factions."""
    if not name:
        return TEXT_DIM
    key = name.strip().lower().split(" (")[0].split("(")[0].strip()
    return FACTION_COLOURS.get(key, TEXT_DIM)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return draw.textlength(text, font=font)


def render_table(
    title: str,
    subtitle: str,
    columns: list[dict],   # [{"key": str, "label": str, "align": "left"|"right"|"center", "color_fn": callable}]
    rows: list[dict],
    footer: str = "",
    accent: tuple = None,
    footer_segments: list = None,   # optional [(text, rgb), ...] drawn as a centred coloured line below the footer
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
    _seg_rows = 1 if footer_segments else 0
    footer_block = (ROW_H * (len(footer_lines) + _seg_rows) + 8) if (footer_lines or footer_segments) else 0
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
    if footer or footer_segments:
        footer_lines = footer.split("\n") if footer else []
        n_lines = len(footer_lines) + (1 if footer_segments else 0)
        footer_h = ROW_H * n_lines + 8
        draw.rectangle([0, row_y, total_w, row_y + footer_h], fill=BG_HEADER)
        draw.rectangle([PADDING_X, row_y, total_w - PADDING_X, row_y + 1], fill=DIVIDER)
        for i, line in enumerate(footer_lines):
            # Centre each footer line horizontally.
            line_w = _text_w(draw, line, font_footer)
            x = max(PADDING_X, (total_w - line_w) / 2)
            draw.text((x, row_y + 8 + i * ROW_H), line, font=font_footer, fill=TEXT_DIM)
        # Coloured segment line (e.g. faction totals, each in its faction colour),
        # centred as a single row below the plain footer lines.
        if footer_segments:
            gap = _text_w(draw, "   ", font_footer)
            seg_ws = [_text_w(draw, str(t), font_footer) for t, _ in footer_segments]
            total_seg_w = sum(seg_ws) + gap * (len(footer_segments) - 1)
            x = max(PADDING_X, (total_w - total_seg_w) / 2)
            y = row_y + 8 + len(footer_lines) * ROW_H
            for (text, rgb), w in zip(footer_segments, seg_ws):
                draw.text((x, y), str(text), font=font_footer, fill=rgb)
                x += w + gap

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
        # Nearly capped: 80%+ of caps used (but not fully). cur is remaining,
        # so used-fraction = (max - cur) / max >= 0.8.
        if (row["max"] - row["cur"]) / row["max"] >= 0.8:
            return TEXT_ORANGE
        return TEXT_GREEN

    def name_color(row):
        if row.get("error") or row.get("max", 0) == 0:
            return TEXT_DIM
        if row["cur"] <= 0:
            return TEXT_RED
        return TEXT_WHITE

    columns = [
        {"key": "name",     "label": "Character",  "align": "left",   "color_fn": name_color},
        {"key": "faction",  "label": "Faction",    "align": "left",   "color_fn": lambda r: faction_colour(r.get("faction_name") or r.get("faction"))},
        {"key": "caps_str", "label": "Caps",       "align": "center", "color_fn": cap_color},
        {"key": "next_cap", "label": "Next Cap",   "align": "center", "color_fn": lambda r: TEXT_DIM},
        {"key": "crew",     "label": "Crew",       "align": "left",   "color_fn": lambda r: TEXT_DIM},
        {"key": "rage_str", "label": "Rage",       "align": "right",  "color_fn": lambda r: TEXT_GOLD},
    ]

    rows = []
    for r in results:
        rows.append({
            "name":     r["name"],
            "faction":  r.get("faction", "—"),
            # Show USED / max, not remaining. cur is remaining, so used = max - cur.
            "caps_str": f"{r['max'] - r['cur']}/{r['max']}" if r.get("max") else "—",
            "crew":     r.get("crew", "—"),
            "next_cap": r.get("next_cap", "—"),
            "rage_str": f"{r.get('rage', 0):,}" if r.get("rage") else "—",
            "cur":      r.get("cur", 0),
            "max":      r.get("max", 0),
            "error":    r.get("error", False),
        })

    footer = f"{not_capped} not capped  ·  {capped} capped  ·  {len(results)} total"

    # Faction-level totals (like Bloop's "Alvar (78)  Vordyn (46)  Delruk (41)"),
    # each rendered in its faction colour. Sum each faction's levels, ranked highest.
    faction_totals: dict = {}
    for r in results:
        fname = r.get("faction_name")
        if fname and fname not in ("—", "None"):
            faction_totals[fname] = faction_totals.get(fname, 0) + int(r.get("faction_level", 0) or 0)
    seg = None
    if faction_totals:
        ranked = sorted(faction_totals.items(), key=lambda kv: kv[1], reverse=True)
        seg = [(f"{name} ({lvl})", faction_colour(name)) for name, lvl in ranked]

    return render_table(f"CAP STATUS — {group.upper()}", subtitle, columns, rows, footer,
                        footer_segments=seg)


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


def render_profile(profile: dict) -> io.BytesIO:
    """Render a rich character profile card from parse_full_profile() output.
    Shows the standard profile stats as a two-column card, faction in its colour."""
    name  = profile.get("name") or "—"
    level = profile.get("level", 0)
    klass = profile.get("klass", "")
    crew  = profile.get("crew", "")
    stats = profile.get("stats", {})

    columns = [
        {"key": "label", "label": "Stat",  "align": "left",  "color_fn": lambda r: TEXT_HEADER},
        {"key": "value", "label": "Value", "align": "right", "color_fn": lambda r: r.get("color", TEXT_WHITE)},
    ]

    # Preferred display order (only shown if present); anything else appended after.
    preferred = [
        "Character Class", "Total Experience", "Growth Yesterday",
        "Total Power", "Attack", "Hit Points", "Chaos Damage",
        "Elemental Attack", "Elemental Resist", "Wilderness Level",
        "God Slayer Level", "Parent", "Faction",
    ]
    rows = []
    if crew:
        rows.append({"label": "Crew", "value": crew, "color": TEXT_DIM})
    seen = set()
    for label in preferred:
        if label in stats:
            seen.add(label)
            colour = TEXT_WHITE
            if label == "Faction":
                colour = faction_colour(profile.get("faction_name"))
            elif label in ("Elemental Attack", "Elemental Resist"):
                colour = TEXT_GREEN
            elif label in ("Chaos Damage",):
                colour = TEXT_GOLD
            rows.append({"label": label, "value": stats[label], "color": colour})
    # Any remaining stat rows we didn't explicitly order.
    for label, value in stats.items():
        if label not in seen:
            rows.append({"label": label, "value": value, "color": TEXT_WHITE})

    subtitle_bits = []
    if level:
        subtitle_bits.append(f"LEVEL {level}")
    if klass:
        subtitle_bits.append(klass.upper())
    subtitle = "  ·  ".join(subtitle_bits)

    return render_table(f"{name.upper()}", subtitle, columns, rows, "")


def render_profile_full(profile: dict, paperdoll: dict = None, crests: list = None,
                        item_icons: dict = None, augments: list = None,
                        crown_icon=None, pic_icon=None) -> io.BytesIO:
    """
    Render a showpiece profile card faithful to Outwar's own layout: 2-column stat
    block on the left, the real equipment PAPERDOLL in the middle (items at their true
    coordinates), a skill-crests row beneath, and an EQUIPPED AUGMENTS grid down the
    right side (OW-Mod style).

    profile:    parse_full_profile() output
    paperdoll:  parse_equipment_paperdoll() output ({"bg_w","bg_h","items":[...]})
    crests:     parse_skill_crests() output (list)
    item_icons: {url: PIL.Image} — pre-downloaded icons (downloading happens in the
                command; the renderer only composites). Missing icons draw a slot box.
    augments:   list of {"img": url, "filled": bool} for every equipped augment slot,
                in order — rendered as a grid on the right. item_icons must contain the
                downloaded PIL image for each augment's img url.
    """
    paperdoll = paperdoll or {"bg_w": 300, "bg_h": 385, "items": []}
    crests = crests or []
    item_icons = item_icons or {}
    augments = augments or []

    name  = profile.get("name") or "—"
    level = profile.get("level", 0)
    klass = profile.get("klass", "")
    crew  = profile.get("crew", "")
    stats = profile.get("stats", {})

    font_title = _load_font(30, bold=True)
    font_sub   = _load_font(17, bold=True)
    font_lab   = _load_font(13, bold=True)
    font_val   = _load_font(14)
    font_sec   = _load_font(14, bold=True)

    # ---- Left column: 2-column stat block (like the real PLAYER INFO) ----
    preferred = [
        "Character Class", "Total Experience", "Growth Yesterday",
        "Total Power", "Attack", "Hit Points", "Chaos Damage",
        "Elemental Attack", "Elemental Resist", "Wilderness Level",
        "God Slayer Level", "Parent", "Faction",
    ]
    ordered = []
    if crew:
        ordered.append(("Crew", crew, TEXT_DIM))
    seen = set()
    for label in preferred:
        if label in stats:
            seen.add(label)
            colour = TEXT_WHITE
            if label == "Faction":
                colour = faction_colour(profile.get("faction_name"))
            elif label in ("Elemental Attack", "Elemental Resist"):
                colour = TEXT_GREEN
            elif label == "Chaos Damage":
                colour = TEXT_GOLD
            ordered.append((label, stats[label], colour))
    for label, value in stats.items():
        if label not in seen:
            ordered.append((label, value, TEXT_WHITE))

    # ---- Layout metrics ----
    PAD        = 26
    TITLE_H    = 92
    stat_h     = 46           # each stat cell (label above value)
    left_w     = 300
    dollscale  = 1.15         # scale the paperdoll up slightly for presence
    doll_w     = int(paperdoll["bg_w"] * dollscale)
    doll_h     = int(paperdoll["bg_h"] * dollscale)

    crest_h = 0
    if crests:
        crest_h = 40 + 72  # header + one crest row

    # ---- Augment grid metrics (right column, OW-Mod style) ----
    AUG_CELL   = 30           # each augment gem cell (icon + small gap)
    aug_cols   = 7            # gems per row (matches the OW-Mod's ~7-wide grid)
    aug_w      = 0
    aug_h      = 0
    if augments:
        aug_rows = (len(augments) + aug_cols - 1) // aug_cols
        aug_w    = aug_cols * AUG_CELL + PAD  # grid + a little breathing room
        aug_h    = 40 + aug_rows * AUG_CELL   # header + rows

    # ---- Profile picture panel metrics (left column, under the stats) ----
    # Only reserve space when a real pic exists; scaled to the left column width so
    # it reads as a proper panel (Bloop-style) rather than a thumbnail.
    PIC_W = 0
    PIC_H = 0
    if pic_icon is not None:
        PIC_W = left_w
        try:
            pw, ph = pic_icon.size
            PIC_H = int(PIC_W * (ph / pw)) if pw else 150
        except Exception:
            PIC_H = 150
        PIC_H = max(90, min(PIC_H, 220))   # clamp so a tall/odd pic can't dominate
        PIC_H += 26                        # header row ("PROFILE PICTURE")

    left_block  = TITLE_H + len(ordered) * stat_h + PIC_H + PAD
    crown_head  = 40 if (profile.get("is_preferred") and crown_icon is not None) else 0
    right_block = TITLE_H + crown_head + doll_h + crest_h + PAD
    aug_block   = TITLE_H + aug_h + PAD
    SIG_H = 24   # room for the "From DeathBot" signature strip at the bottom
    total_h = max(left_block, right_block, aug_block) + PAD + SIG_H
    total_w = PAD + left_w + PAD + doll_w + (PAD + aug_w if augments else 0) + PAD

    img  = Image.new("RGB", (total_w, total_h), BG_DARK)
    draw = ImageDraw.Draw(img)

    # ---- Title ----
    draw.rectangle([0, 0, total_w, TITLE_H], fill=BG_TITLE)
    draw.rectangle([0, 0, total_w, 4], fill=ACCENT)
    draw.text((PAD, 20), name, font=font_title, fill=TEXT_WHITE)
    sub_bits = []
    if level: sub_bits.append(f"Level {level}")
    if klass: sub_bits.append(klass)
    if sub_bits:
        draw.text((PAD, 58), "  ·  ".join(sub_bits), font=font_sub, fill=TEXT_GOLD)

    # ---- Left: PLAYER INFO, two stats per row ----
    draw.text((PAD, TITLE_H + 4), "PLAYER INFO", font=font_sec, fill=TEXT_HEADER)
    y = TITLE_H + 30
    col_w = left_w // 2
    for i, (label, value, colour) in enumerate(ordered):
        col = i % 2
        row = i // 2
        cx = PAD + col * col_w
        cy = y + row * stat_h
        draw.text((cx, cy), label.upper(), font=font_lab, fill=TEXT_DIM)
        draw.text((cx, cy + 16), str(value), font=font_val, fill=colour)

    # ---- Profile picture panel (left column, beneath the stats) ----
    if pic_icon is not None and PIC_H:
        px0 = PAD
        py0 = y + ((len(ordered) + 1) // 2) * stat_h + 6
        draw.text((px0, py0), "PROFILE PICTURE", font=font_sec, fill=TEXT_HEADER)
        py0 += 24
        panel_h = PIC_H - 26
        # backing panel + the fitted image
        draw.rectangle([px0 - 2, py0 - 2, px0 + PIC_W + 2, py0 + panel_h + 2],
                       fill=BG_HEADER, outline=DIVIDER)
        try:
            pic = pic_icon.convert("RGBA")
            pw, ph = pic.size
            # fit within the panel preserving aspect ratio, then centre
            scale = min(PIC_W / pw, panel_h / ph)
            nw, nh = max(1, int(pw * scale)), max(1, int(ph * scale))
            pic = pic.resize((nw, nh))
            ox = px0 + (PIC_W - nw) // 2
            oy = py0 + (panel_h - nh) // 2
            img.paste(pic, (ox, oy), pic)
        except Exception:
            pass

    # ---- Right: the equipment paperdoll at true coordinates ----
    dx0 = PAD + left_w + PAD
    dy0 = TITLE_H + 4
    draw.text((dx0, dy0), "EQUIPMENT", font=font_sec, fill=TEXT_HEADER)
    dy0 += 26
    # A Preferred-Player crown sits above the Head slot, so give the paperdoll extra
    # top headroom to fit it between the header and the head item.
    if profile.get("is_preferred") and crown_icon is not None:
        dy0 += 40
    # subtle backing panel
    draw.rectangle([dx0 - 6, dy0 - 6, dx0 + doll_w + 6, dy0 + doll_h + 6],
                   fill=BG_HEADER, outline=DIVIDER)
    for it in paperdoll["items"]:
        ix = dx0 + int(it["x"] * dollscale)
        iy = dy0 + int(it["y"] * dollscale)
        iw = max(8, int(it["w"] * dollscale))
        ih = max(8, int(it["h"] * dollscale))
        # Edge box (Bloop-style slot frame): a subtly filled, outlined cell behind
        # each item so equipped gear reads as slotted rather than floating.
        pad = 3
        draw.rectangle([ix - pad, iy - pad, ix + iw + pad, iy + ih + pad],
                       fill=SLOT_BG, outline=SLOT_EDGE, width=1)
        icon = item_icons.get(it["img"])
        if icon is not None:
            try:
                ic = icon.convert("RGBA").resize((iw, ih))
                img.paste(ic, (ix, iy), ic)
                continue
            except Exception:
                pass
        draw.rectangle([ix, iy, ix + iw, iy + ih], outline=DIVIDER)

    # ---- Preferred Player crown — hovering above the Head item (top-centre slot),
    #      Bloop-style. The Head slot is the top-most paperdoll item (smallest y);
    #      on a tie, the one nearest horizontal centre. Purely geometric, so it
    #      doesn't depend on Outwar's slot labelling. Skipped if not PP / no image.
    if profile.get("is_preferred") and crown_icon is not None and paperdoll["items"]:
        try:
            doll_mid = dx0 + doll_w // 2
            def _head_key(it):
                cx = dx0 + int(it["x"] * dollscale) + int(it["w"] * dollscale) // 2
                return (it["y"], abs(cx - doll_mid))
            head = min(paperdoll["items"], key=_head_key)
            hx = dx0 + int(head["x"] * dollscale)
            hy = dy0 + int(head["y"] * dollscale)
            hw = max(8, int(head["w"] * dollscale))
            # Crown HOVERS above the head slot (Bloop-style) — a little smaller than
            # the slot and sat fully clear of it with a gap, so it reads as a floating
            # badge rather than a helmet the character is wearing.
            cw = int(hw * 0.9)
            ch = cw  # ProPP.png is ~square
            cx = hx + (hw - cw) // 2
            GAP = 4
            cy = hy - ch - GAP          # bottom of crown sits a gap above the slot top
            cr = crown_icon.convert("RGBA").resize((cw, ch))
            img.paste(cr, (cx, cy), cr)
        except Exception:
            pass

    # ---- Skill crests row beneath the paperdoll ----
    if crests:
        cy0 = dy0 + doll_h + 16
        draw.text((dx0, cy0), "SKILL CRESTS", font=font_sec, fill=TEXT_HEADER)
        cy0 += 24
        # crests carry their own x within a 300-wide strip; scale to match doll
        for cr in crests:
            cx = dx0 + int(cr["x"] * dollscale)
            cyy = cy0 + int(cr["y"] * dollscale)
            cw = max(8, int(cr["w"] * dollscale))
            ch = max(8, int(cr["h"] * dollscale))
            icon = item_icons.get(cr["img"])
            if icon is not None:
                try:
                    ic = icon.convert("RGBA").resize((cw, ch))
                    img.paste(ic, (cx, cyy), ic)
                    continue
                except Exception:
                    pass
            draw.rectangle([cx, cyy, cx + cw, cyy + ch], outline=DIVIDER)

    # ---- EQUIPPED AUGMENTS grid (right column, OW-Mod style) ----
    if augments:
        ax0 = PAD + left_w + PAD + doll_w + PAD
        ay0 = TITLE_H + 4
        draw.text((ax0, ay0), "EQUIPPED AUGMENTS", font=font_sec, fill=TEXT_HEADER)
        ay0 += 28
        gem = AUG_CELL - 4  # icon size within the cell
        for idx, aug in enumerate(augments):
            r, c = divmod(idx, aug_cols)
            gx = ax0 + c * AUG_CELL
            gy = ay0 + r * AUG_CELL
            icon = item_icons.get(aug.get("img"))
            if icon is not None:
                try:
                    ic = icon.convert("RGBA").resize((gem, gem))
                    img.paste(ic, (gx, gy), ic)
                    continue
                except Exception:
                    pass
            # empty slot or missing icon → faint outlined box
            draw.rectangle([gx, gy, gx + gem, gy + gem], outline=DIVIDER)

    # ---- Signature strip: "From DeathBot" bottom-right ----
    font_sig = _load_font(12, bold=True)
    sig = "\u2756 From DeathBot"   # black diamond-minus glyph (renders in DejaVu)
    try:
        sw = draw.textlength(sig, font=font_sig)
    except Exception:
        sw = 90
    draw.text((total_w - PAD - sw, total_h - SIG_H + 2), sig,
              font=font_sig, fill=TEXT_DIM)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


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
        if s in ("SPAWNED", "ALIVE"):
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


def render_crew_caps_table(crew_name: str, rows: list[dict]) -> io.BytesIO:
    """Render a whole-crew cap table: Character / Caps / Next Cap only. Built for big
    crews (up to ~200 members) from a single crew_capstatus scrape. rows: list of
    {name, used, max, next_cap}."""
    total = len(rows)
    capped = sum(1 for r in rows if r.get("max", 0) > 0 and r["used"] >= r["max"])
    available = total - capped

    def cap_color(row):
        mx = row.get("max", 0)
        if mx == 0:
            return TEXT_DIM
        if row["used"] >= mx:
            return TEXT_RED
        if row["used"] / mx >= 0.8:
            return TEXT_ORANGE
        return TEXT_GREEN

    def name_color(row):
        mx = row.get("max", 0)
        if mx and row["used"] >= mx:
            return TEXT_RED
        return TEXT_WHITE

    columns = [
        {"key": "name",     "label": "Character", "align": "left",   "color_fn": name_color},
        {"key": "caps_str", "label": "Caps",      "align": "center", "color_fn": cap_color},
        {"key": "next_cap", "label": "Next Cap",  "align": "center", "color_fn": lambda r: TEXT_DIM},
    ]
    table_rows = []
    for r in rows:
        table_rows.append({
            "name":     r["name"],
            "caps_str": f"{r['used']}/{r['max']}" if r.get("max") else "—",
            "next_cap": r.get("next_cap", "—"),
            "used":     r.get("used", 0),
            "max":      r.get("max", 0),
        })

    subtitle = f"{capped} CAPPED  ·  {available} AVAILABLE  ·  {total} MEMBERS"
    footer = f"{crew_name} — whole-crew cap status"
    return render_table(f"CREW CAPS — {crew_name.upper()}", subtitle, columns, table_rows, footer)
