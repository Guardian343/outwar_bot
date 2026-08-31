"""
scraper.py — All HTML parsing logic for Outwar pages.
"""
import re
import os
import json
from collections import deque
from dataclasses import dataclass
from typing import Optional
from bs4 import BeautifulSoup
from outwar import logger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Character:
    name: str = ""
    crew: str = ""
    level: int = 0
    experience: int = 0
    power: int = 0
    atk: int = 0
    hp: int = 0
    elemental: int = 0
    elemental_resist: int = 0
    chaos: int = 0
    god_slayer: int = 0
    wilderness: int = 0
    max_rage: Optional[int] = None
    crew_id: Optional[int] = None


@dataclass
class Boss:
    name: str = ""
    full_name: str = ""
    spawned: bool = False
    stats_url: str = ""
    priority: int = -1
    boss_id: int = -1
    hp: int = 0
    hp_pct: float = 0.0
    rage_to_form: int = 5000
    rage_to_join: int = 5000
    last_killed: str = ""
    spawn_days: int = 0
    md_form: int = 1250
    md_join: int = 94


@dataclass
class God:
    god_id: int = 0
    name: str = ""
    short_name: str = ""
    spawned: bool = False
    hp_pct: float = 0.0
    room_id: int = None


@dataclass
class GodDrop:
    crew_name: str = ""
    damage: str = ""
    loot: str = ""


@dataclass
class Envoy:
    envoy_id: int = 0          # target number (1-8)
    name: str = ""             # the player appearing as this envoy
    spawned: bool = False
    stats_url: str = ""
    title: str = ""            # e.g. "Mob Envoy", "PvP Envoy", "Alvar Envoy", "PP Envoy (Hard)"
    combat: str = ""           # combat system: Mob / PvP / Raid
    rage: int = 0              # rage cost to attack
    image: str = ""            # avatar image URL


@dataclass
class Trustee:
    name: str = ""
    url: str = ""
    level: int = 0
    crew: str = ""
    rage: int = 0
    suid: Optional[int] = None


# ---------------------------------------------------------------------------
# Character scraping
# ---------------------------------------------------------------------------

def parse_character_profile(html: str, name: str) -> Optional[Character]:
    """Parse the Outwar profile page for a character."""
    soup = BeautifulSoup(html, "lxml")
    char = Character(name=name)

    def _int(text: str) -> int:
        try:
            return int(text.replace(",", "").replace(" ", "").strip())
        except (ValueError, AttributeError):
            return 0

    level_node = soup.select_one("#divProfile .level, [class*='level']")
    if level_node:
        char.level = _int(level_node.text)

    rows = soup.select("#divProfile table tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)
            if "power" in label:
                char.power = _int(value)
            elif "attack" in label and "elemental" not in label:
                char.atk = _int(value)
            elif "hit points" in label or "hp" == label:
                char.hp = _int(value)
            elif "elemental" in label and "resist" not in label:
                char.elemental = _int(value)
            elif "elemental resist" in label:
                char.elemental_resist = _int(value)
            elif "chaos" in label:
                char.chaos = _int(value)
            elif "god slayer" in label:
                char.god_slayer = _int(value)
            elif "wilderness" in label:
                char.wilderness = _int(value)
            elif "experience" in label:
                char.experience = _int(value)
            elif "level" in label:
                char.level = _int(value)

    return char if char.level > 0 or char.power > 0 else None


def parse_god_cap(html: str) -> tuple[int, int]:
    """
    Parse God Cap from the home page.
    Returns (current_caps, max_caps) e.g. (6, 10).
    """
    soup = BeautifulSoup(html, "lxml")
    # Looks for "God Cap: 6/10" anywhere on the page
    text = soup.get_text()
    m = re.search(r"God Cap[:\s]+(\d+)\s*/\s*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def parse_character_stats_profile(html: str) -> dict:
    """
    Parse power, elemental, chaos, faction and faction level from the profile page.
    Returns dict with keys: power, elemental, chaos, faction, faction_level.
    """
    soup = BeautifulSoup(html, "lxml")
    stats = {"power": 0, "elemental": 0, "chaos": 0, "faction": "", "faction_level": 0}

    def _int(text: str) -> int:
        try:
            return int(re.sub(r"[^\d]", "", text))
        except (ValueError, AttributeError):
            return 0

    rows = soup.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)
            if label == "total power":
                stats["power"] = _int(value)
            elif label == "elemental attack":
                stats["elemental"] = _int(value)
            elif label == "chaos damage":
                stats["chaos"] = _int(value)
            elif label == "faction":
                # e.g. "Alvar (3)"
                m = re.match(r"(.+?)\s*\((\d+)\)", value)
                if m:
                    stats["faction"] = m.group(1).strip()
                    stats["faction_level"] = int(m.group(2))
                elif value and value.lower() != "none":
                    stats["faction"] = value
                    stats["faction_level"] = 0

    return stats


def parse_item_augments(html: str) -> list:
    """
    Parse an item's augment slots from its item_rollover.php?id=<itemID> page.
    Structure (confirmed from the OWMod script's parseSelectedItem + SLOT_RE):
      - The FIRST non-gem <img> is the item's own icon (skip it).
      - Every subsequent <img> that is either augslot.jpg (an EMPTY slot) or has
        itempopup(event,'ITEMID_SLOT') (a FILLED augment) is an augment slot, in
        slot order (1, 2, 3…).
    Returns an ordered list of slots:
      [{"filled": bool, "img": src, "aug_id": "ITEMID_SLOT" or None}, ...]
    """
    soup = BeautifulSoup(html, "lxml")

    def _is_gem(img):
        src = (img.get("src") or "")
        over = (img.get("onmouseover") or img.get("ONMOUSEOVER") or "")
        return ("augslot.jpg" in src.lower()) or bool(re.search(r"itempopup\(event,'\d+_\d+'\)", over))

    slots = []
    seen_item_icon = False
    for img in soup.find_all("img"):
        if not _is_gem(img):
            # First non-gem img = the item's own icon; skip just that one.
            seen_item_icon = True
            continue
        src = img.get("src") or ""
        over = img.get("onmouseover") or img.get("ONMOUSEOVER") or ""
        if "augslot.jpg" in src.lower():
            slots.append({"filled": False, "img": src, "aug_id": None})
        else:
            m = re.search(r"itempopup\(event,'(\d+_\d+)'\)", over)
            slots.append({"filled": True, "img": src, "aug_id": m.group(1) if m else None})
    return slots


def parse_equipment_paperdoll(html: str) -> dict:
    """
    Parse the equipped-items paperdoll from a profile.php page. Each item is an
    absolutely-positioned <img> inside a container div that carries the background
    'thedude.png' paperdoll. We read each item's real left/top/width/height so the
    layout can be replicated faithfully (not approximated as a grid).

    Returns:
      {
        "bg_w": int, "bg_h": int,               # paperdoll container size
        "items": [
          {"id","img","alt","x","y","w","h"},   # one per equipped item / gem
          ...
        ],
      }
    An empty items list means none were found (page structure differed).
    """
    soup = BeautifulSoup(html, "lxml")

    # Find the equipment container: a div whose style references thedude.png.
    container = None
    for div in soup.find_all("div", style=True):
        if "thedude.png" in (div.get("style") or ""):
            container = div
            break
    if container is None:
        return {"bg_w": 300, "bg_h": 385, "items": []}

    def _px(style, prop):
        m = re.search(rf"{prop}\s*:\s*(\d+)px", style or "")
        return int(m.group(1)) if m else 0

    cstyle = container.get("style") or ""
    bg_w = _px(cstyle, "width") or 300
    bg_h = _px(cstyle, "height") or 385

    items = []
    # Each item sits in an absolutely-positioned child div; the img is inside.
    for cell in container.find_all("div", recursive=False):
        cstyle = cell.get("style") or ""
        x = _px(cstyle, "left")
        y = _px(cstyle, "top")
        w = _px(cstyle, "width")
        h = _px(cstyle, "height")
        # A cell may hold multiple imgs (e.g. a row of 3 orbs) — lay them across.
        imgs = cell.find_all("img")
        n = len(imgs)
        for i, img in enumerate(imgs):
            src = img.get("src")
            if not src:
                continue
            over = img.get("onmouseover") or img.get("ONMOUSEOVER") or ""
            m = re.search(r"itempopup\(event,'(\d+)'\)", over)
            iid = m.group(1) if m else ""
            # If several imgs share one cell, split the cell width across them.
            sub_w = (w // n) if (n > 1 and w) else w
            items.append({
                "id": iid,
                "img": src,
                "alt": img.get("alt", ""),
                "x": x + (i * sub_w if n > 1 else 0),
                "y": y,
                "w": sub_w or 40,
                "h": h or 40,
            })
    return {"bg_w": bg_w, "bg_h": bg_h, "items": items}


def parse_skill_crests(html: str) -> list:
    """
    Parse the SKILL CRESTS row (separate section, skillcrest.png background).
    Returns [{"id","img","alt","x","y","w","h"}, ...] in page order.
    """
    soup = BeautifulSoup(html, "lxml")
    container = None
    for div in soup.find_all("div", style=True):
        if "skillcrest.png" in (div.get("style") or ""):
            container = div
            break
    if container is None:
        return []

    def _px(style, prop):
        m = re.search(rf"{prop}\s*:\s*(\d+)px", style or "")
        return int(m.group(1)) if m else 0

    crests = []
    for cell in container.find_all("div", recursive=False):
        cstyle = cell.get("style") or ""
        img = cell.find("img")
        if not img or not img.get("src"):
            continue
        over = img.get("onmouseover") or img.get("ONMOUSEOVER") or ""
        m = re.search(r"itempopup\(event,'(\d+)'\)", over)
        crests.append({
            "id": m.group(1) if m else "",
            "img": img.get("src"),
            "alt": img.get("alt", ""),
            "x": _px(cstyle, "left"), "y": _px(cstyle, "top"),
            "w": _px(cstyle, "width") or 60, "h": _px(cstyle, "height") or 60,
        })
    return crests


def parse_full_profile(html: str) -> dict:
    """
    Rich profile parse for the !profile command card. Reads every label/value row
    in the profile stats table generically (so it captures whatever Outwar shows —
    Experience, Power, Hit Points, Elemental Attack, Elemental Resist, Chaos Damage,
    Growth Yesterday, Wilderness Level, God Slayer Level, Faction, Parent, etc.),
    plus name, level, class, and crew from the page chrome.

    Returns a dict with:
      name, level, klass, crew, faction, faction_name, faction_level,
      and a `stats` dict of {label: value_str} for every profile row found.
    """
    soup = BeautifulSoup(html, "lxml")
    out = {
        "name": "", "level": 0, "klass": "", "crew": "",
        "faction": "", "faction_name": "", "faction_level": 0,
        "stats": {},
    }

    # --- Name: usually the page's main heading ---
    for sel in ("h1", "h2", ".profile_name", ".char_name"):
        node = soup.select_one(sel)
        if node:
            txt = node.get_text(strip=True).replace("†", "").strip()
            if txt:
                out["name"] = txt
                break

    # --- Level + class: often "Level 95 · Gangster" style text near the name ---
    body_txt = soup.get_text(" ", strip=True)
    m = re.search(r"Level\s+(\d+)", body_txt)
    if m:
        out["level"] = int(m.group(1))
    for kls in ("Gangster", "Mobster", "Bomber", "Merc", "Elemental", "Chaos", "Monster"):
        if re.search(rf"\b{kls}\b", body_txt):
            out["klass"] = kls
            break

    # --- Crew: the crew_profile / crew_home link ---
    for pat in ("crew_profile?id=", "crew_home?id="):
        for link in soup.find_all("a", href=lambda h: h and pat in h):
            text = link.get_text(strip=True).replace("†", "").strip()
            if text:
                out["crew"] = text
                break
        if out["crew"]:
            break

    # --- Profile stat rows — ONLY keep known profile stats. The profile page also
    # lists a character's underlings/minions as label/value rows (ELITE10=76, etc.),
    # which would otherwise flood the card and make the image huge (~1900px → Discord
    # shrinks it). An allowlist of the real profile stats keeps just those and drops
    # the underling rows entirely.
    KNOWN_STATS = {
        "character class", "total experience", "growth yesterday", "total power",
        "attack", "elemental attack", "hit points", "chaos damage",
        "elemental resist", "wilderness level", "god slayer level", "parent",
        "faction", "experience", "power",  # a few aliases just in case
    }
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True)
            value = cells[1].get_text(strip=True)
            if not label or not value:
                continue
            low = label.lower()
            if low not in KNOWN_STATS:
                continue  # skip underlings and anything not a recognised stat
            out["stats"][label] = value
            if low == "faction":
                fm = re.match(r"(.+?)\s*\((\d+)\)", value)
                if fm:
                    out["faction_name"] = fm.group(1).strip()
                    out["faction"] = fm.group(1).strip()
                    out["faction_level"] = int(fm.group(2))
                elif value and value.lower() != "none":
                    out["faction_name"] = value
                    out["faction"] = value

    # --- Preferred Player: the header shows /images/profile/ProPP.png with a
    #     "Preferred Player" popup. The filename alone is a reliable, specific flag
    #     (it only appears for PP accounts), so key off that.
    out["is_preferred"] = ("ProPP.png" in html) or ("Preferred Player" in html)

    # --- Custom profile picture: <img id="profile-pic-fx" class="profilepic"
    #     src="https://upload.outwar.com/uploaded/sNNN.png">. Match on either the id
    #     or the class, then take the src. Only a REAL uploaded pic should show — a
    #     missing/placeholder src leaves profile_pic None so the renderer skips the
    #     panel (no empty box for accounts without a custom pic).
    out["profile_pic"] = None
    pic = soup.select_one("img#profile-pic-fx, img.profilepic")
    if pic and pic.get("src"):
        src = pic["src"].strip()
        # Ignore obvious placeholders/blanks; keep only a genuine uploaded image.
        if src and "blank" not in src.lower() and "default" not in src.lower():
            out["profile_pic"] = src

    return out
    """Extract max rage value from world page."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one("header ul li div div:nth-of-type(4) p")
    if node:
        onmouseover = node.get("onmouseover", "")
        pos = onmouseover.find("Maximum:")
        if pos != -1:
            start = pos + 21
            end = onmouseover.find("<", start)
            return onmouseover[start:end].strip()
    return None


# ---------------------------------------------------------------------------
# Trustee scraping (from /myaccount page)
# ---------------------------------------------------------------------------

def parse_trustee_list(html: str) -> list[dict]:
    """
    Parse the Outwar /myaccount page to extract all trusteed characters.
    Looks for any link containing suid= in the href.
    """
    soup = BeautifulSoup(html, "lxml")
    trustees = []
    seen_suids = set()

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        m = re.search(r"suid=(\d+)", href)
        if not m:
            continue

        suid = int(m.group(1))
        if suid in seen_suids:
            continue

        name = link.get_text(strip=True)
        if not name or len(name) < 2:
            continue

        seen_suids.add(suid)
        url = f"https://sigil.outwar.com/world?suid={suid}&serverid=1"
        trustees.append({
            "name":  name,
            "suid":  suid,
            "url":   url,
            "level": 0,
            "crew":  "",
            "rage":  0,
        })

    return trustees


def parse_character_crew_and_level(html: str) -> tuple[str, int, int, "Optional[int]"]:
    """
    Parse a character's world/home page to get crew name, level, rage, and crew id.
    Returns (crew_name, level, rage, crew_id).
    """
    soup = BeautifulSoup(html, "lxml")
    crew = ""
    crew_id = None
    level = 0

    # Crew name — the actual crew link is <a href="/crew_profile?id=XXXX">†Crew Name†</a>
    # The nav sidebar uses /crew_profile without an id param — skip those
    for link in soup.find_all("a", href=lambda h: h and "crew_profile?id=" in h):
        text = link.get_text(strip=True).replace("†", "").strip()
        if text:
            crew = text
            _m = re.search(r"crew_profile\?id=(\d+)", link.get("href", ""))
            if _m:
                crew_id = int(_m.group(1))
            break

    # Also check crew_home?id= pattern
    if not crew:
        for link in soup.find_all("a", href=lambda h: h and "crew_home?id=" in h):
            text = link.get_text(strip=True).replace("†", "").strip()
            if text:
                crew = text
                _m = re.search(r"crew_home\?id=(\d+)", link.get("href", ""))
                if _m:
                    crew_id = int(_m.group(1))
                break

    # Level from toolbar
    node = soup.select_one(".toolbar_level")
    if node:
        try:
            level = int(node.get_text(strip=True).replace(",", ""))
        except ValueError:
            pass

    # Fallback level from text
    if not level:
        for tag in soup.find_all(["p", "span", "div", "li", "td", "b", "font"]):
            text = tag.get_text(strip=True)
            m = re.search(r"^Level[:\s]+(\d+)$", text, re.IGNORECASE)
            if m:
                level = int(m.group(1))
                break

    rage = parse_rage(html)
    return crew, level, rage, crew_id


# ---------------------------------------------------------------------------
# Rage scraping
# ---------------------------------------------------------------------------

def parse_rage(html: str) -> int:
    """Extract current rage from any Outwar page via the toolbar."""
    soup = BeautifulSoup(html, "lxml")
    # toolbar_rage class is present on all pages
    node = soup.select_one(".toolbar_rage")
    if node:
        try:
            return int(node.get_text(strip=True).replace(",", ""))
        except ValueError:
            pass
    # Fallback: regex search
    m = re.search(r'toolbar_rage[^>]*>\s*([\d,]+)', html)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return 0


def parse_rage_cost(html: str) -> dict:
    """
    Read the STATED rage cost off a raid form/join page.

    Outwar's form and join pages both contain a line of the exact form:
        (It will take <b> 2500 </b> of your <b> 64,767 </b> rage to form raid)
        (It will take <b> 270 </b> of your <b> 1,013 </b> rage to join this raid)

    This is the authoritative cost for the CURRENT account in its CURRENT state,
    so it is automatically correct for whether MD (Master's Disk) is active or not
    — no need to store separate with/without-MD values or measure before/after a
    join (which is what the boss path historically did indirectly). Reading the
    number directly is exact and works BEFORE the action is taken, which lets
    pre-flight block an under-rage form/join before it happens.

    Returns a dict:
        {"cost": int, "current": int, "action": "form"|"join"}  on a match, or
        {} if the line isn't present (e.g. the account already has enough context,
        or the page isn't a form/join page).

    Works identically for boss and prime pages — the only difference is the
    trailing action text ("to form raid" vs "to join this raid").
    """
    if not html:
        return {}
    # Tolerant of the <b> tags, whitespace, and comma-grouped numbers. The action
    # word ("form" / "join") is captured so callers know which cost this is.
    m = re.search(
        r"It will take\s*(?:<b>)?\s*([\d,]+)\s*(?:</b>)?\s*of your\s*"
        r"(?:<b>)?\s*([\d,]+)\s*(?:</b>)?\s*rage\s*to\s*(form|join)",
        html, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return {}
    try:
        cost = int(m.group(1).replace(",", ""))
        current = int(m.group(2).replace(",", ""))
    except ValueError:
        return {}
    action = "form" if m.group(3).lower() == "form" else "join"
    return {"cost": cost, "current": current, "action": action}

def _md(cost: int) -> int:
    """Rage cost with Markdown Level 10 active (75% reduction)."""
    return max(1, int(cost * 0.25))


BOSS_ATTRIBUTES = {
    # name: (short, priority, hp, rage_to_form, rage_to_join, spawn_days, md_form, md_join)
    "Cosmos, Great All Being":   ("cosmos",   6, 99_360_000_000,    2500, 375,  7,  _md(2500), _md(375)),
    "Death, Reaper of Souls":    ("death",    5, 289_792_000_000,   3750, 375,  10, _md(3750), _md(375)),
    "Maekrix, Dreaded Striker":  ("mae",      4, 319_800_000_000,   1250, 750,  14, _md(1250), _md(750)),
    "Blackhand Reborn":          ("bh",       3, 568_000_000_000,   1875, 1125, 14, _md(1875), _md(1125)),
    "Zyrak, Vision of Madness":  ("zyrak",    2, 1_200_000_000_000, 1875, 1125, 14, _md(1875), _md(1125)),
    "Triworld Simulation":       ("triworld", 1, 2_400_000_000_000, 3750, 375,  14, _md(3750), _md(375)),
    # Legacy names
    "Cosmos":    ("cosmos",   6, 99_360_000_000,    2500, 375,  7,  _md(2500), _md(375)),
    "Death":     ("death",    5, 289_792_000_000,   3750, 375,  10, _md(3750), _md(375)),
    "Maekrix":   ("mae",      4, 319_800_000_000,   1250, 750,  14, _md(1250), _md(750)),
    "Blackhand": ("bh",       3, 568_000_000_000,   1875, 1125, 14, _md(1875), _md(1125)),
    "Zyrak":     ("zyrak",    2, 1_200_000_000_000, 1875, 1125, 14, _md(1875), _md(1125)),
    "Nulak":     ("nulak",    1, 905_000_000_000,   1875, 1125, 14, _md(1875), _md(1125)),
    "Arkron":    ("arkron",   1, 905_000_000_000,   1875, 1125, 14, _md(1875), _md(1125)),
    "Triworld":  ("triworld", 1, 2_400_000_000_000, 3750, 375,  14, _md(3750), _md(375)),
}


def get_boss_attributes(full_name: str) -> tuple:
    """
    Look up boss attributes by full name, falling back to a partial match,
    then sane defaults if completely unknown.
    Returns (short, priority, hp, rage_to_form, rage_to_join, spawn_days, md_form, md_join)
    """
    if full_name in BOSS_ATTRIBUTES:
        return BOSS_ATTRIBUTES[full_name]
    # Partial match — e.g. "Maekrix, Dreaded Striker" vs "Maekrix"
    for name, attrs in BOSS_ATTRIBUTES.items():
        if name.split(",")[0].strip().lower() == full_name.split(",")[0].strip().lower():
            return attrs
    # Unknown boss — sane defaults
    default_rage = 1875
    return (None, 99, 500_000_000_000, default_rage, default_rage // 2, 14,
            _md(default_rage), _md(default_rage // 2))


def parse_bosses(html: str) -> list[Boss]:
    """
    Parse the crew_bossspawns page.
    Each boss is a card with class component-card_4.
    Spawned = image links to formraid.php, has HP% and stats link.
    Dead = image has _grey in filename.
    """
    soup = BeautifulSoup(html, "lxml")
    bosses = []

    cards = soup.find_all("div", class_="component-card_4")

    for card in cards:
        try:
            # Boss name
            name_tag = card.find("h3", class_="card-user_name")
            if not name_tag:
                continue
            full_name = name_tag.get_text(strip=True)

            # Spawned check — image src contains _grey if dead
            img = card.find("img")
            spawned = img is not None and "_grey" not in img.get("src", "")

            # Stats URL and boss ID
            stats_url = ""
            boss_id = -1

            if spawned:
                # Spawned boss — formraid link has target ID, stats link separate
                form_link = card.find("a", href=lambda h: h and "formraid.php" in h)
                if form_link:
                    m = re.search(r"target=(\d+)", form_link.get("href", ""))
                    if m:
                        boss_id = int(m.group(1))

                stats_link = card.find("a", href=lambda h: h and "boss_stats.php" in h)
                if stats_link:
                    stats_url = stats_link.get("href", "").lstrip("/")
            else:
                # Dead boss — main link goes to stats
                stats_link = card.find("a", href=lambda h: h and "boss_stats.php" in h)
                if stats_link:
                    stats_url = stats_link.get("href", "").lstrip("/")

            # Last killed info from onmouseover
            last_killed = None
            link = card.find("a", href=True)
            if link:
                onmouseover = link.get("onmouseover", "")
                m = re.search(r"Last Killed on.*?<b>(.*?)</b>", onmouseover)
                if m:
                    last_killed = re.sub(r'<[^>]+>', '', m.group(1)).strip()

            # HP% from occupation paragraph — crew raid bosses are HEALTH-based.
            # Crews attack until this reaches 0% and the boss dies.
            hp_pct = 0.0
            occ = card.find("p", class_="card-user_occupation")
            if occ and spawned:
                pct_text = occ.get_text(strip=True)
                m = re.search(r"([\d.]+)%", pct_text)
                if m:
                    hp_pct = float(m.group(1))

            attrs = get_boss_attributes(full_name)
            short_name, priority, hp, rage_to_form, rage_to_join, spawn_days = attrs[:6]
            md_form = attrs[6] if len(attrs) > 6 else max(1, int(rage_to_form * 0.25))
            md_join = attrs[7] if len(attrs) > 7 else max(1, int(rage_to_join * 0.25))

            bosses.append(Boss(
                name=short_name or full_name.lower().split(",")[0].replace(" ", ""),
                full_name=full_name,
                spawned=spawned,
                stats_url=stats_url,
                priority=priority,
                boss_id=boss_id,
                hp=hp,
                hp_pct=hp_pct,
                rage_to_form=rage_to_form,
                rage_to_join=rage_to_join,
                last_killed=last_killed or "",
                spawn_days=spawn_days,
                md_form=md_form,
                md_join=md_join,
            ))
        except Exception as e:
            logger.warning("SCRAPER", f"Error parsing boss card: {e}")

    return bosses


def parse_boss_damage(html: str) -> tuple[str, int]:
    """Parse boss stats page, return (formatted message, total_damage)."""
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("#content-header-row div table tbody tr")
    message = ""
    total_damage = 0

    for row in rows:
        name_cell = row.select_one("td:nth-of-type(1)")
        dmg_cell = row.select_one("td:nth-of-type(2)")
        if name_cell and dmg_cell:
            player = name_cell.get_text(strip=True).replace("_", "\\_")
            dmg_text = dmg_cell.get_text(strip=True)
            try:
                dmg_val = int(dmg_text.replace(",", "").split()[0])
                total_damage += dmg_val
            except (ValueError, IndexError):
                pass
            message += f"**{player}**\n{dmg_text}\n\n"

    return message, total_damage


def unscramble_loot(scrambled: str) -> str:
    items = scrambled.split("|")
    result = []
    prev = ""
    count = 0

    for item in items:
        if prev != item and count > 0:
            if prev != "No Items":
                result.append(f"{count}x {prev}")
            count = 0
        prev = item
        count += 1
        if prev == "No Items":
            return "No Items"

    if prev and prev != "No Items":
        result.append(f"{count}x {prev}")

    return " | ".join(result)


# ---------------------------------------------------------------------------
# Prime Gods scraping
# ---------------------------------------------------------------------------

GOD_SHORT_NAMES = {
    # Animations
    "Animation of Supremacy":       "anisupremacy",
    "Animation of Chaos":           "anichaos",
    "Animation of Power":           "anipower",
    "Animation of Elements":        "aniele",
    "Animation of Versatility":     "anivers",
    # Demons
    "Rezun, Demon of Madness":      "rezun",
    "Banok, Demon of Insanity":     "banok",
    "Envar, Demon of Lunacy":       "envar",
    # Harbingers / Betrayers
    "Valzek, Harbinger of Death":   "valzek",
    "Valzek, Harbringer of Death":  "valzek",  # typo variant
    "Agnar, Astral Betrayer":       "agnar",
    # Deities
    "Shayar, the Shadow Deity":     "shayar",
    "Kinark, the Kinetic Deity":    "kinark",
    "Firan, the Fire Deity":        "firan",
    "Arcon, the Arcane Deity":      "arcon",
    "Holgor, the Holy Deity":       "holgor",
    # Twins
    "Villax, Twin of Strength":     "villax",
    "Rillax, Twin of Wisdom":       "rillax",
    # Balancers / Beasts
    "Thanox, Balancer of Chaos":    "thanox",
    "Murfax, Beast of the Caves":   "murfax",
    "Gregov, Knight of the Woods":  "gregov",
    "Dexor, Victor of Veldara":     "dexor",
    # Dragons
    "Balerion, Dragon of Dread":    "balerion",
    "Viserion, the Necrodragon":    "viserion",
    # Underworld
    "Dlanod, the Crazed Chancellor":"dlanod",
    "Straya, the Underworld Ruler": "straya",
    "Skarthul the Avenged":         "skarthul",
    # Desolation / Shadow / Masters
    "Nafir, God of Desolation":     "nafir",
    "Raiyar, the Shadow Master":    "raiyar",
    "Esquin, the Kinetic Master":   "esquin",
    "Crolvak, the Fire Master":     "crolvak",
    "Xynak, the Arcane Master":     "xynak",
    "Bolkor, the Holy Master":      "bolkor",
    # Rune gods tier 2
    "Archdevil Yirkon":             "yirkon",
    "Keeper of Nature":             "keeper",
    "Akkel the Enflamed Warrior":   "akkel",
    "Nayark the Mummified Sorcerer":"nayark",
    "Amalgamated Apparition":       "amalgamated",
    # Rune gods tier 1
    "Zikkir the Dark Archer":       "zikkir",
    "Volgan the Living Ironbark":   "volgan",
    "Jorun the Blazing Swordsman":  "jorun",
    "Ancient Magus Tarkin":         "tarkin",
    "Sarcrina the Astral Priestess":"sarcrina",
    # Legacy
    "Karvaz, Lord of Alsayic":      "karvaz",
    "Felroc, Overseer of Hellfire": "felroc",
    "Kretok, Descendant of Nature": "kretok",
    "Q-SEC Commander":              "qsec",
    "Ormsul the Putrid":            "ormsul",
    "Gorganus of the Wood":         "gorganus",
    "Anvilfist":                    "anvilfist",
    "Lacuste of the Swarm":         "lacuste",
    "Sylvanna TorLai":              "sylvanna",
}

# Common in-game aliases / abbreviations -> short_name
GOD_ALIASES = {
    # Animations
    "aoe":           "aniele",
    "ani ele":       "aniele",
    "ele ani":       "aniele",
    "aoc":           "anichaos",
    "ani chaos":     "anichaos",
    "aop":           "anipower",
    "ani power":     "anipower",
    "aov":           "anivers",
    "ani vers":      "anivers",
    "ani sup":       "anisupremacy",
    "aos":           "anisupremacy",
    # Demons
    "rez":           "rezun",
    # Masters
    "ray":           "raiyar",
    "esq":           "esquin",
    "crol":          "crolvak",
    "xyn":           "xynak",
    "bol":           "bolkor",
    # Deities
    "kin":           "kinark",
    "fir":           "firan",
    "arc":           "arcon",
    "hol":           "holgor",
    "sha":           "shayar",
    # Twins
    "vil":           "villax",
    "ril":           "rillax",
    # Dragons
    "bal":           "balerion",
    "vis":           "viserion",
    # Others
    "amalg":         "amalgamated",
    "sarc":          "sarcrina",
    "mag":           "tarkin",
    "qsec":          "qsec",
    "q-sec":         "qsec",
    "keeper":        "keeper",
    "gorgan":        "gorganus",
    "anvil":         "anvilfist",
    "lac":           "lacuste",
    "syl":           "sylvanna",
    "sylv":          "sylvanna",
    "gorg":          "gorganus",
    "orm":           "ormsul",
    "amal":          "amalgamated",
    "ag":            "agnar",
    "val":           "valzek",
    "skar":          "skarthul",
    "viser":         "viserion",
    "baler":         "balerion",
    "aniversa":      "anivers",
}


def parse_rec_stats_block(text: str):
    """
    Parse a recommended-stats block such as:
        -qsec (75k/15k) -planes (kretok/felroc/karvaz) (80/20) -dexor (85/27/250) ...

    Rules: first number = power, second = ele, optional third = chaos.
    Power and ele are in thousands (the trailing 'k' is optional), chaos is literal.
    A group like (a/b/c) before the stats means the stats apply to every god named.

    Returns (entries, skipped):
      entries = [{"label":str, "names":[str], "power":int, "ele":int, "chaos":int|None}]
      skipped = [(raw_entry, reason)]  — lines that need manual handling
    """
    entries, skipped = [], []
    raw_entries = re.split(r'(?:^|\s)-(?=[A-Za-z])', text.strip())
    stat_re = re.compile(r'^\s*(\d+)\s*k?\s*/\s*(\d+)\s*k?(?:\s*/\s*(\d+)\s*k?)?\s*$', re.I)
    for raw in raw_entries:
        raw = raw.strip()
        if not raw:
            continue
        groups = re.findall(r'\(([^()]*)\)', raw)
        label  = raw.split('(')[0].strip()
        stats, name_group = [], None
        for g in groups:
            g = g.strip()
            m = stat_re.match(g)
            if m:
                stats.append(m)
            elif '/' in g and not any(c.isdigit() for c in g):
                name_group = g
            # otherwise it's a note (e.g. "resist is key") — ignore
        if len(stats) > 1:
            skipped.append((raw, "multiple stat blocks — pick one")); continue
        if not stats:
            skipped.append((raw, "no clean power/ele found")); continue
        m = stats[0]
        power = int(m.group(1)) * 1000
        ele   = int(m.group(2)) * 1000
        chaos = int(m.group(3)) if m.group(3) else None
        if name_group:
            names = [n.strip() for n in name_group.split('/') if n.strip()]
        elif any(c.isdigit() for c in label) or '/' in label:
            skipped.append((raw, "names interleaved with stats")); continue
        else:
            names = [label]
        entries.append({"label": label, "names": names,
                        "power": power, "ele": ele, "chaos": chaos})
    return entries, skipped


def parse_gods(html: str) -> list[God]:
    """
    Parse the primegods page.
    Each god is a <span class="mobbox"> or <span class="mobbox grey">
    grey = dead, no grey class = spawned.
    HP% is shown as a progress bar width when spawned.
    """
    soup = BeautifulSoup(html, "lxml")
    gods = []

    for span in soup.find_all("span", class_="mobbox"):
        try:
            a = span.find("a", href=True)
            if not a:
                continue

            href = a.get("href", "")
            m_id = re.search(r"mobid=(\d+)", href)
            if not m_id:
                continue
            mob_id = int(m_id.group(1))

            img = a.find("img")
            if not img:
                continue

            onmouseover = img.get("onmouseover", "")
            m_name = re.search(r"popup\(event,'(.+?)',1\)", onmouseover)
            name = m_name.group(1) if m_name else ""
            if not name:
                continue

            # grey class means dead
            classes = span.get("class", [])
            spawned = "grey" not in classes

            # Prime gods are TIME-based, not health-based — the progress bar
            # on the page shows time remaining, not HP, so there's no hp_pct to read.

            gods.append(God(
                god_id=mob_id,
                name=name,
                short_name=GOD_SHORT_NAMES.get(name, name.lower().replace(" ", "").replace(",", "")[:12]),
                spawned=spawned,
            ))

        except Exception as e:
            logger.warning("SCRAPER", f"Error parsing god: {e}")

    return gods


def parse_god_stats_page(html: str) -> tuple[list[GodDrop], bool]:
    """Parse a Prime God's stats page. Returns (drops, is_dead)."""
    soup = BeautifulSoup(html, "lxml")
    drops = []

    rows = soup.select("#content-header-row div table tbody tr")
    is_dead = len(rows) > 0

    for row in rows:
        try:
            name_cell = row.select_one("td:nth-of-type(1)")
            dmg_cell  = row.select_one("td:nth-of-type(2)")
            loot_cell = row.select_one("td:nth-of-type(3)")

            crew_name = name_cell.get_text(strip=True).replace("_", "\\_") if name_cell else ""
            damage    = dmg_cell.get_text(strip=True) if dmg_cell else ""

            loot_str = ""
            if loot_cell:
                raw = loot_cell.get("onmouseover", "")
                scrambled = (raw
                    .replace("popup(event,'", "")
                    .replace("<br>','808080')", "")
                    .replace("','808080')", "")
                    .replace("<br>", "|")
                    .replace("\\", ""))
                loot_str = unscramble_loot(scrambled)

            if crew_name:
                drops.append(GodDrop(
                    crew_name=crew_name,
                    damage=damage,
                    loot=loot_str or "No Items",
                ))
        except Exception as e:
            logger.warning("SCRAPER", f"Error parsing god drop row: {e}")

    return drops, is_dead


def parse_prime_god_page(html: str) -> dict:
    """
    Parse an individual Prime God's page.
    Returns dict with:
        max_members, atk, ele_dmg, spawn_chance,
        spawned, time_remaining_secs, stats (list of crew dicts),
        loot_url, stats_url
    """
    soup = BeautifulSoup(html, "lxml")
    result = {
        "max_members":        None,
        "atk":                None,
        "ele_dmg":            None,
        "spawn_chance":       None,
        "spawned":            False,
        "time_remaining_secs": None,
        "room_id":            None,
        "stats":              [],
        "loot_url":           None,
        "stats_url":          None,
    }

    # Spawned status
    spawned_node = soup.find("h1", style=lambda s: s and "color:#00c100" in s)
    result["spawned"] = spawned_node is not None

    # Room ID — look for world.php?room= link
    for a in soup.find_all("a", href=True):
        m = re.search(r"world\.php\?room=(\d+)", a["href"])
        if m:
            result["room_id"] = int(m.group(1))
            break
    # Also try ajax_changeroomb pattern
    if not result["room_id"]:
        for a in soup.find_all("a", href=True):
            m = re.search(r"room=(\d+)", a["href"])
            if m:
                result["room_id"] = int(m.group(1))
                break

    # Countdown timestamp from JS: var countdown = 1780842600 - ...
    scripts = soup.find_all("script", type="text/javascript")
    for script in scripts:
        if script.string and "countdown" in script.string:
            m = re.search(r"var countdown = (\d+) -", script.string)
            if m:
                import time
                spawn_end = int(m.group(1))
                remaining = spawn_end - int(time.time())
                result["time_remaining_secs"] = max(0, remaining)
                break

    # Stats from divQuestText divs
    for div in soup.find_all("div", class_="divQuestText"):
        text = div.get_text(strip=True)
        m = re.search(r"Attack:\s*([\d,]+)", text)
        if m:
            result["atk"] = int(m.group(1).replace(",", ""))
        m = re.search(r"Ele Damage:\s*([\d,]+)", text)
        if m:
            result["ele_dmg"] = int(m.group(1).replace(",", ""))
        m = re.search(r"Max Members:\s*(\d+)", text)
        if m:
            result["max_members"] = int(m.group(1))
        m = re.search(r"Spawn Chance.*?([\d.]+)%", text)
        if m:
            result["spawn_chance"] = float(m.group(1))

    # Current spawn stats — grid items with crew name + kills
    grid_items = soup.find_all("div", class_="grid-item")
    i = 0
    while i < len(grid_items) - 1:
        crew_link = grid_items[i].find("a", href=lambda h: h and "crew_profile" in h)
        if crew_link:
            crew_name = crew_link.get_text(strip=True)
            kills_div = grid_items[i + 1]
            kills_text = kills_div.get_text(strip=True)
            # Format: "2 (100.0%)"
            m = re.search(r"(\d+)\s*\(([\d.]+)%\)", kills_text)
            if m:
                result["stats"].append({
                    "crew":    crew_name,
                    "kills":   int(m.group(1)),
                    "pct":     float(m.group(2)),
                })
            i += 2
        else:
            i += 1

    # Loot URL — use Spawn History (first entry = most recent completed kill)
    # First two links are "Previous Spawn Stats" and current pending spawn
    # Third link onwards are completed spawns in the history table
    loot_links = soup.find_all("a", href=lambda h: h and "primegod_loot" in h)

    # Find unique spawnids — first unique one after the first is the last completed kill
    seen = []
    for link in loot_links:
        href = link.get("href", "")
        if href not in seen:
            seen.append(href)

    # seen[0] = most recent kill (just died)
    # seen[1] = previous kill
    if seen:
        result["loot_url"] = seen[0].lstrip("/")

    return result


def format_time_remaining(seconds: int) -> str:
    """Format seconds into a human readable string."""
    if seconds <= 0:
        return "Expired"
    days    = seconds // 86400
    hours   = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


# ---------------------------------------------------------------------------
# Envoy scraping
# ---------------------------------------------------------------------------

def parse_envoy_overview(html: str) -> list[Envoy]:
    """
    Parse the envoy OVERVIEW page (envoy_overview) — the current card-based layout.
    Each envoy is a <div class="envoy-card"> with:
      .envoy-title            → type ("Mob Envoy", "PvP Envoy", "Alvar Envoy", "PP Envoy (Hard)"…)
      .envoy-name a           → the player appearing as the envoy
      a[href*="target=N"]     → the target number
      .combat-system-value    → combat system (Mob / PvP / Raid)
      Rage stat-row .stat-value → rage cost
      .envoy-image img[src]   → avatar
    Returns a list of Envoy, one per card, in page order (targets 1-8).
    """
    soup = BeautifulSoup(html, "lxml")
    envoys = []

    for card in soup.select("div.envoy-card"):
        try:
            title_el = card.select_one(".envoy-title")
            title = title_el.get_text(strip=True) if title_el else ""

            name_el = card.select_one(".envoy-name a") or card.select_one(".envoy-name")
            name = name_el.get_text(strip=True) if name_el else ""

            # target number from any href with target=N
            target = 0
            link = card.select_one('a[href*="target="]')
            if link:
                m = re.search(r"target=(\d+)", link.get("href", ""))
                if m:
                    target = int(m.group(1))

            combat_el = card.select_one(".combat-system-value")
            combat = combat_el.get_text(strip=True) if combat_el else ""

            # Rage: the stat-row whose label is "Rage"
            rage = 0
            for row in card.select(".stat-row"):
                lab = row.select_one(".stat-label")
                val = row.select_one(".stat-value")
                if lab and val and lab.get_text(strip=True).lower() == "rage":
                    rage = int(re.sub(r"[^\d]", "", val.get_text()) or 0)
                    break

            img_el = card.select_one(".envoy-image img")
            image = img_el.get("src", "") if img_el else ""

            # "locked" cards (envoy-card-locked) are not currently attackable/open
            classes = card.get("class", [])
            spawned = "envoy-card-open" in classes or "envoy-card-locked" not in classes

            envoys.append(Envoy(
                envoy_id=target, name=name, spawned=spawned,
                stats_url=f"envoy?target={target}" if target else "",
                title=title, combat=combat, rage=rage, image=image,
            ))
        except Exception as e:
            logger.warning("SCRAPER", f"Error parsing envoy card: {e}")

    return envoys


def parse_envoys(html: str) -> list[Envoy]:
    """Parse envoy entries from the primegods page."""
    soup = BeautifulSoup(html, "lxml")
    envoys = []

    containers = soup.select("body > center > div > div > div > div > div > div")

    for container in containers:
        for span in container.find_all("span", recursive=False):
            try:
                a = span.find("a")
                if not a:
                    continue
                href = a.get("href", "")
                if "mobid=" not in href and "target=" not in href:
                    continue
                img = a.find("img")
                if not img:
                    continue

                onmouseover = img.get("onmouseover", "")
                name_match = re.search(r"event,'(.+?)'", onmouseover)
                name = name_match.group(1) if name_match else ""

                if not name or name in GOD_SHORT_NAMES:
                    continue

                spawned = "grey" not in " ".join(span.get("class", []))
                id_match = re.search(r"(?:mobid|target)=(\d+)", href)
                envoy_id = int(id_match.group(1)) if id_match else -1

                stats_link = span.select_one("a[href*='stats'], a[href*='bossid']")
                stats_url = stats_link.get("href", "").lstrip("/") if stats_link else href.lstrip("/")

                if envoy_id != -1:
                    envoys.append(Envoy(
                        envoy_id=envoy_id,
                        name=name,
                        spawned=spawned,
                        stats_url=stats_url,
                    ))
            except Exception as e:
                logger.warning("SCRAPER", f"Error parsing envoy: {e}")

    return envoys


def parse_envoy_name(html: str) -> str | None:
    """Extract the envoy's name from its target page. The envoy's actual name is
    in the .envoy-title div (e.g. 'Mob Envoy'). NOTE: the .envoy-name div is NOT
    the envoy name — it's the account chosen to receive the buff each cycle.
    """
    soup = BeautifulSoup(html, "lxml")
    el = soup.find("div", class_="envoy-title")
    if el:
        name = el.get_text(strip=True)
        return name or None
    return None


def parse_envoy_buff_account(html: str) -> str | None:
    """Extract the account chosen to receive this envoy's buff (the .envoy-name div)."""
    soup = BeautifulSoup(html, "lxml")
    el = soup.find("div", class_="envoy-name")
    if el:
        name = el.get_text(strip=True)
        return name or None
    return None


def parse_envoy_leaderboard(html: str) -> list[dict]:
    """Parse the Leaderboard from an envoy target page (envoy?target=<id>).
    The leaderboard is a CSS grid, not a table: a .grid-container inside the
    .leaderboard-section titled 'Leaderboard' (NOT 'Spawn History'), with cells
    as .grid-item divs — first 4 are headers (Rank/Character/Level/Attacks), then
    players in groups of 4.
    Returns a list of {rank, name, profile_id, level, attacks}.
    """
    soup = BeautifulSoup(html, "lxml")
    board = None
    for sec in soup.find_all("div", class_="leaderboard-section"):
        title = sec.find("div", class_="leaderboard-title")
        if title and "leaderboard" in title.get_text(strip=True).lower():
            board = sec
            break
    if not board:
        return []
    grid = board.find("div", class_="grid-container")
    if not grid:
        return []
    cells = grid.find_all("div", class_="grid-item")
    if len(cells) < 8:  # need at least headers + 1 player
        return []
    rows = []
    data = cells[4:]  # skip the 4 header cells
    for k in range(0, len(data) - 3, 4):
        try:
            rank = data[k].get_text(strip=True).rstrip(".")
            name_cell = data[k + 1]
            a = name_cell.find("a")
            name = a.get_text(strip=True) if a else name_cell.get_text(strip=True)
            profile_id = None
            if a and a.get("href"):
                m = re.search(r"profile\?id=(\d+)", a["href"])
                profile_id = int(m.group(1)) if m else None
            level = data[k + 2].get_text(strip=True)
            attacks = data[k + 3].get_text(strip=True)
            rows.append({
                "rank": rank, "name": name, "profile_id": profile_id,
                "level": level, "attacks": attacks,
            })
        except Exception as e:
            logger.warning("SCRAPER", f"Error parsing leaderboard row: {e}")
    return rows


def parse_envoy_latest_pool(html: str) -> int | None:
    """Extract the latest pool number from an envoy target page's Spawn History
    (links like envoy_loot/<pool>/<envoy_id>). Returns the max pool seen, or None.
    """
    pools = [int(m) for m in re.findall(r"envoy_loot/(\d+)/\d+", html)]
    return max(pools) if pools else None


# ---------------------------------------------------------------------------
# Equipment page scraping
# ---------------------------------------------------------------------------

def parse_equipment_page(html: str, item_name: str) -> list[dict]:
    """Scan a character's equipment page for items matching item_name."""
    soup = BeautifulSoup(html, "lxml")
    found = []

    for img in soup.select("div img[alt]"):
        alt = img.get("alt", "")
        if item_name.lower() in alt.lower():
            found.append({"item_name": alt, "item_id": None, "quantity": 1})
            if "Crest" not in item_name:
                break

    return found


def parse_loot_status(data: str) -> dict | None:
    """Extract the loot roll status from an SSE stream. Status codes (from the game's
    client JS): 1='Preparing loot', 2='Loot rolling starting soon...', 2.5='Rolling!',
    3='Loot completed'. Returns {"status": <code>, "label": <text>} for the LAST status
    message seen (the current state), or None if no status message is present.
    """
    import json
    labels = {
        1: "Preparing loot",
        2: "Loot rolling starting soon…",
        2.5: "Rolling!",
        3: "Loot completed",
    }
    last = None
    for line in data.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("data: "):
            line = line[6:]
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("messageType") == "status":
            try:
                code = float(event.get("status"))
                code = int(code) if code == int(code) else code  # 3.0 → 3, keep 2.5
                last = {"status": code, "label": labels.get(code, f"Status {code}")}
            except Exception:
                continue
    return last


def parse_prime_god_loot(data: str) -> list[dict]:
    """
    Parse prime god loot from SSE stream (ajax/timedgod_loot_sse.php).
    Each line is: data: {"messageType": "...", ...}
    """
    import json

    # Detect SSE format
    is_sse = 'data: {' in data or '"messageType"' in data

    if is_sse:
        item_names: dict[int, str] = {}
        crew_names: dict[int, str] = {}
        winners:    dict[int, int] = {}
        points_winners: dict[int, int] = {}

        for line in data.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Strip SSE "data: " prefix
            if line.startswith("data: "):
                line = line[6:]
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = event.get("messageType", "")

            if msg_type == "loottogive":
                loot_data = event.get("data", "[]")
                if isinstance(loot_data, str):
                    try:
                        loot_data = json.loads(loot_data)
                    except Exception:
                        loot_data = []
                for i, item in enumerate(loot_data):
                    item_names[i] = item.get("name", f"Item {i}")

            elif msg_type == "crewinfo":
                crew_data = event.get("crew", "{}")
                if isinstance(crew_data, str):
                    try:
                        crew_data = json.loads(crew_data)
                    except Exception:
                        crew_data = {}
                crew_id   = crew_data.get("id")
                crew_name = crew_data.get("name", "")
                if crew_id is not None:
                    try:
                        crew_names[int(crew_id)] = crew_name
                    except (ValueError, TypeError):
                        crew_names[crew_id] = crew_name

            elif msg_type == "rolling_end":
                item_idx = event.get("item_index")
                crew_id  = event.get("crewid")
                if item_idx is not None and crew_id is not None:
                    try:
                        winners[int(item_idx)] = int(crew_id)
                    except (ValueError, TypeError):
                        winners[item_idx] = crew_id

            elif msg_type == "points":
                crew_id = event.get("crewid")
                pts     = event.get("points", 0)
                if crew_id is not None:
                    try:
                        points_winners[int(crew_id)] = int(pts)
                    except (ValueError, TypeError):
                        pass

        # Build crew loot from winners
        crew_loot: dict[str, dict] = {}
        for item_idx, crew_id in winners.items():
            item_name = item_names.get(item_idx, f"Item {item_idx}")
            crew_name = crew_names.get(crew_id, f"Crew {crew_id}")
            if crew_name not in crew_loot:
                crew_loot[crew_name] = {"item_counts": {}, "points": 0}
            counts = crew_loot[crew_name]["item_counts"]
            counts[item_name] = counts.get(item_name, 0) + 1

        for crew_id, pts in points_winners.items():
            crew_name = crew_names.get(crew_id, f"Crew {crew_id}")
            if crew_name not in crew_loot:
                crew_loot[crew_name] = {"item_counts": {}, "points": 0}
            crew_loot[crew_name]["points"] += pts

        result = []
        for crew, loot in crew_loot.items():
            item_counts = loot["item_counts"]
            points      = loot["points"]
            items = []
            for item_name, count in item_counts.items():
                items.append(f"{item_name} x{count}" if count > 1 else item_name)
            if points > 0:
                items.append(f"{points} points")
            # True number of individual drops: each rolled item counts as its
            # quantity (so "Amulet Chest x3" = 3), and an accumulated-points award
            # counts as a single drop. len(items) would wrongly count lines.
            drop_count = sum(item_counts.values()) + (1 if points > 0 else 0)
            if items:
                result.append({
                    "crew":        crew,
                    "items":       items,
                    "item_counts": dict(item_counts),
                    "points":      points,
                    "drop_count":  drop_count,
                })
        return result

# ---------------------------------------------------------------------------
# Backpack scraping
# ---------------------------------------------------------------------------

def parse_backpack_for_item(html: str, item_name: str) -> list[dict]:
    """Return list of {item_name, item_id, quantity} found in backpack."""
    soup = BeautifulSoup(html, "lxml")
    found = []

    for div in soup.find_all("div", recursive=True):
        img = div.find("img")
        if img:
            name = img.get("data-name", "")
            if item_name.lower() in name.lower():
                item_id = img.get("data-iid", "")
                qty_str = img.get("data-itemidqty", "1")
                try:
                    qty = int(qty_str)
                except ValueError:
                    qty = 1
                found.append({"item_name": name, "item_id": item_id, "quantity": qty})

    return found


# ---------------------------------------------------------------------------
# Raid scraping
# ---------------------------------------------------------------------------

def parse_raid_link(html: str, boss_full_name: str) -> str:
    """Find the join link for a forming raid matching the boss name."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("#content-header-row div:nth-of-type(2) form div div table")
    if not table:
        return ""

    for row in table.find_all("tr"):
        name_cell = row.select_one("td:nth-of-type(1) a b")
        if name_cell and name_cell.get_text(strip=True) == boss_full_name:
            link = row.select_one("td:nth-of-type(1) a")
            if link:
                return link.get("href", "").lstrip("/")

    return ""


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

_map_graph = {}

def _load_map_graph() -> dict:
    """Load and cache the room adjacency graph from map_graph.json."""
    global _map_graph
    if _map_graph:
        return _map_graph
    import json
    import os
    here = os.path.dirname(__file__)
    db_path = os.path.join(here, "..", "database", "map_graph.json")
    seed_path = os.path.join(here, "map_graph.json")
    # Prefer the crawled, persistent copy in database/; fall back to the shipped seed.
    path = db_path if os.path.exists(db_path) else seed_path
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # Keys are strings in JSON — convert to int for consistency with room IDs
        _map_graph = {int(k): v for k, v in raw.items()}
    except Exception:
        _map_graph = {}
    return _map_graph


def bfs_nearest(start: int, targets: set, limit: int = 5) -> list:
    """Single outward BFS from `start`, returning up to `limit` of the nearest rooms
    that are in `targets`, as (room_id, distance) sorted nearest-first. Far cheaper
    than running find_path to every target: one traversal, stops once `limit` targets
    are found. `start` itself counts as distance 0 if it's a target."""
    graph = _load_map_graph()
    if start not in graph:
        return []
    found = []
    if start in targets:
        found.append((start, 0))
    visited = {start}
    queue = deque([(start, 0)])
    while queue and len(found) < limit:
        node, dist = queue.popleft()
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                nd = dist + 1
                if neighbor in targets:
                    found.append((neighbor, nd))
                    if len(found) >= limit:
                        break
                queue.append((neighbor, nd))
    return found[:limit]


def find_path(start: int, goal: int) -> list:
    """BFS shortest path from start room to goal room. Returns list of room IDs including start."""
    if start == goal:
        return [start]
    graph = _load_map_graph()
    if start not in graph or goal not in graph:
        return []
    visited = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited[neighbor] = node
                if neighbor == goal:
                    path = []
                    cur = goal
                    while cur is not None:
                        path.append(cur)
                        cur = visited[cur]
                    return list(reversed(path))
                queue.append(neighbor)
    return []


def get_latest_envoy_pool(html: str) -> int | None:
    """
    Parse the spawn history table on an envoy page to find the most recent
    loot pool number. Returns the pool number (e.g. 47) or None if not found.
    The loot links look like: /envoy_loot/47/3
    """
    import re
    # Find all envoy_loot links and take the highest pool number
    matches = re.findall(r"/envoy_loot/(\d+)/\d+", html)
    if matches:
        return max(int(m) for m in matches)
    return None


def parse_god_slayer(html: str):
    """Parse the GOD SLAYER block from a character profile page.

    Returns a list of dicts: {god_id, name, first_slayed, kill_count} for every
    god the account has slayed. Presence on the page means slayed (kill_count >= 1);
    the sprite's background-position only encodes a kill-count/mastery tier, not status.
    """
    import re as _re
    out = []
    for div in _re.findall(r'<div class="divGodSlayerImg".*?</div>', html, _re.S):
        mid = _re.search(r'godslayer/(\d+)\.png', div)
        pop = _re.search(
            r"<b>(.*?)</b><br>First Slayed:\s*(.*?)<br>Kill Count:\s*(\d+)", div, _re.S)
        if not mid or not pop:
            continue
        out.append({
            "god_id": int(mid.group(1)),
            "name": pop.group(1).strip(),
            "first_slayed": pop.group(2).strip(),
            "kill_count": int(pop.group(3)),
        })
    return out


_reference_mobs = None   # {name_lower: {"id", "name", "rooms"}}


def load_reference_mobs():
    """Load Mobs.txt (shipped reference data) into a name-keyed dict, cached.
    Mobs.txt maps every mob's Id and Rooms; we key by name because the God Slayer
    image ids do NOT reliably match mob ids, but names match exactly."""
    global _reference_mobs
    if _reference_mobs is not None:
        return _reference_mobs
    import json
    import os
    path = os.path.join(os.path.dirname(__file__), "Mobs.txt")
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for m in json.load(f):
                name = m.get("Name")
                if not name:
                    continue
                # first occurrence wins; keep the entry that actually has rooms
                key = name.lower()
                if key not in out or (m.get("Rooms") and not out[key]["rooms"]):
                    out[key] = {"id": m.get("Id"), "name": name,
                                "rooms": m.get("Rooms") or []}
    except Exception:
        out = {}
    _reference_mobs = out
    return _reference_mobs


# The definitive 114-god God Slayer roster (from the game's God Slayer page).
# The daily gods are SLAYER_TARGETS (63); the God-Slayer PRIMES are the
# remaining 51 (roster minus daily). NOTE: this is NOT the same as the live
# prime-watcher list (prime_gods.json), which also contains EVENT primes like
# 'Zhulian Friar' that are not part of God Slayer — using that list for the
# split broke the maths. This roster is the single source of truth.
GOD_SLAYER_ROSTER = [
    "Ebliss, Fallen Angel of Despair",
    "Brutalitar, Lord of the Underworld",
    "Dreg nor, Keeper of the Infernal Essence",
    "King Ashnar, Lord of the Unliving",
    "Nar Zhul, Slayer of All",
    "Great Lord Ganeshan",
    "Lady Ariella",
    "Lord Narada",
    "Lord Suka",
    "Lord Varan",
    "Synge, The Red Dragon",
    "Rancid, Lord of Thugs",
    "Terrance, Rebel of Rallis",
    "Zertan, The Collector",
    "Quiver, The Renegade",
    "Garland, The Lord Keeper",
    "Tylos, The Lord Master",
    "Jazzmin, Maiden of Vitality",
    "Sigil, Lich of Woe",
    "Ganja the Stone Golem",
    "Lord Sibannac",
    "Smoot the Yeti",
    "Bloodchill the Grizzly",
    "Ag Nabak the Abomination",
    "Wanhiroeaz the Devourer",
    "Vitkros, Hydra of the Deep",
    "Hyrak, Bringer of Nightmares",
    "Mistress of the Sword",
    "Traxodon the Plaguebringer",
    "Kro Shuk, Doomslayer",
    "Murderface",
    "The Emerald Assassin",
    "Detox",
    "Samatha Dark-Soul",
    "Anguish",
    "Threk, King of Lords",
    "Crane",
    "Gnorb",
    "Nessam",
    "Pinosis",
    "Shadow",
    "Tsort",
    "Lord Xordam",
    "Skybrine The Inescapable",
    "Windstrike The Vile",
    "Emperor Neudeus, Controller of the Universe",
    "Slashbrood, Devourer of the Blackness",
    "Howldroid, Tormentor of the Pit",
    "Hackerphage, Protector of the Gateway",
    "Numerocure, The Black Messenger of Evil",
    "Lady Chaos, Queen of the Abyss",
    "Rotborn, Eater of the Dead",
    "Melt Bane, The Forbidden Demon Dragon",
    "Baron Mu, Dark Rider of the Undead",
    "Freezebreed, The Frozen Manipulator",
    "Sylvanna TorLai",
    "Lacuste of the Swarm",
    "Anvilfist",
    "Gorganus of the Wood",
    "Ormsul the Putrid",
    "Old World Drake",
    "Animated Captain",
    "Beast of Cards",
    "Noxious Slug",
    "Q-SEC Commander",
    "Jade Dragonite",
    "Varsanor, Master of Darkness",
    "Grivvek, Protector of the Brood",
    "Crantos, Defender of Ultimation",
    "Kretok, Descendant of Nature",
    "Felroc, Overseer of Hellfire",
    "Karvaz, Lord of Alsayic",
    "Sarcrina the Astral Priestess",
    "Ancient Magus Tarkin",
    "Jorun the Blazing Swordsman",
    "Volgan the Living Ironbark",
    "Zikkir the Dark Archer",
    "Amalgamated Apparition",
    "Nayark the Mummified Sorcerer",
    "Akkel the Enflamed Warrior",
    "Keeper of Nature",
    "Archdevil Yirkon",
    "Bolkor, the Holy Master",
    "Xynak, the Arcane Master",
    "Crolvak, the Fire Master",
    "Esquin, the Kinetic Master",
    "Raiyar, the Shadow Master",
    "Nafir, God of Desolation",
    "Skarthul the Avenged",
    "Straya, the Underworld Ruler",
    "Dlanod, the Crazed Chancellor",
    "Viserion, the Necrodragon",
    "Balerion, Dragon of Dread",
    "Dexor, Victor of Veldara",
    "Gregov, Knight of the Woods",
    "Murfax, Beast of the Caves",
    "Thanox, Balancer of Chaos",
    "Rillax, Twin of Wisdom",
    "Villax, Twin of Strength",
    "Holgor, the Holy Deity",
    "Arcon, the Arcane Deity",
    "Firan, the Fire Deity",
    "Kinark, the Kinetic Deity",
    "Shayar, the Shadow Deity",
    "Agnar, Astral Betrayer",
    "Valzek, Harbinger of Death",
    "Envar, Demon of Lunacy",
    "Banok, Demon of Insanity",
    "Rezun, Demon of Madness",
    "Animation of Versatility",
    "Animation of Elements",
    "Animation of Power",
    "Animation of Chaos",
    "Animation of Supremacy",
]
# Daily God-Slayer gods (63) — the world gods that spawn DAILY, used for
# God Slayer levels. This is the full 114 God Slayer roster MINUS the 51
# Prime gods (cycle-based, handled by the prime watcher). Derived by diffing
# the game's God Slayer page against the prime list.
SLAYER_TARGETS = {
    "nabak": "Ag Nabak the Abomination", "anguish": "Anguish", "captain": "Animated Captain",
    "baron": "Baron Mu, Dark Rider of the Undead", "beastcards": "Beast of Cards", "grizzly": "Bloodchill the Grizzly",
    "brut": "Brutalitar, Lord of the Underworld", "crane": "Crane", "crantos": "Crantos, Defender of Ultimation",
    "detox": "Detox", "dreg": "Dreg nor, Keeper of the Infernal Essence", "ebliss": "Ebliss, Fallen Angel of Despair",
    "neudeus": "Emperor Neudeus, Controller of the Universe", "freeze": "Freezebreed, The Frozen Manipulator", "ganja": "Ganja the Stone Golem",
    "garland": "Garland, The Lord Keeper", "gnorb": "Gnorb", "ganeshan": "Great Lord Ganeshan",
    "grivvek": "Grivvek, Protector of the Brood", "hacker": "Hackerphage, Protector of the Gateway", "howldroid": "Howldroid, Tormentor of the Pit",
    "hyrak": "Hyrak, Bringer of Nightmares", "jade": "Jade Dragonite", "jazzmin": "Jazzmin, Maiden of Vitality",
    "ash": "King Ashnar, Lord of the Unliving", "kro": "Kro Shuk, Doomslayer", "ariella": "Lady Ariella",
    "ladychaos": "Lady Chaos, Queen of the Abyss", "narada": "Lord Narada", "sib": "Lord Sibannac",
    "suka": "Lord Suka", "varan": "Lord Varan", "xordam": "Lord Xordam",
    "melt": "Melt Bane, The Forbidden Demon Dragon", "mistress": "Mistress of the Sword", "murderface": "Murderface",
    "nar": "Nar Zhul, Slayer of All", "nessam": "Nessam", "noxious": "Noxious Slug",
    "numerocure": "Numerocure, The Black Messenger of Evil", "drake": "Old World Drake", "pinosis": "Pinosis",
    "quiver": "Quiver, The Renegade", "rancid": "Rancid, Lord of Thugs", "rot": "Rotborn, Eater of the Dead",
    "samatha": "Samatha Dark-Soul", "shadow": "Shadow", "sigil": "Sigil, Lich of Woe",
    "skybrine": "Skybrine The Inescapable", "slashbrood": "Slashbrood, Devourer of the Blackness", "smoot": "Smoot the Yeti",
    "synge": "Synge, The Red Dragon", "terrance": "Terrance, Rebel of Rallis", "emerald": "The Emerald Assassin",
    "threk": "Threk, King of Lords", "trax": "Traxodon the Plaguebringer", "tsort": "Tsort",
    "tylos": "Tylos, The Lord Master", "varsanor": "Varsanor, Master of Darkness", "vitkros": "Vitkros, Hydra of the Deep",
    "wanh": "Wanhiroeaz the Devourer", "wind": "Windstrike The Vile", "zertan": "Zertan, The Collector",
}

def god_slayer_primes():
    """The 51 God-Slayer PRIME gods = the 114 roster minus the 63 daily gods.
    Derived from GOD_SLAYER_ROSTER so daily + prime always partition to exactly 114
    (unlike prime_gods.json, which includes non-roster event primes)."""
    daily = {v.lower() for v in SLAYER_TARGETS.values()}
    return [g for g in GOD_SLAYER_ROSTER if g.lower() not in daily]




def order_rooms_by_proximity(rooms: list, start: int = 11) -> list:
    """Order a list of room IDs into an efficient visiting sequence using greedy
    nearest-neighbour over the REAL map graph (BFS distances), clustered by zone so
    nearby gods are cleared together. Starts from `start` (room 11 = the universal
    teleport anchor by default). Rooms the graph can't reach are appended at the end
    in their original order.

    This replaces the old Areas.txt-based sort, which mis-ordered because Areas.txt
    is incomplete (doesn't cover the Dimension zones) — gods in uncovered rooms fell
    into a catch-all bucket and split clusters, causing the observed zig-zag (raid a
    god, leave the area, come back for one 2-3 rooms away).
    """
    import json as _json, os as _os
    rooms = [int(r) for r in rooms if r]
    if len(rooms) <= 1:
        return rooms

    # Load room -> zone (from the crawl); rooms with no zone get a per-room bucket.
    zpath = _os.path.join(_os.path.dirname(__file__), "..", "database", "room_zones.json")
    try:
        room_zones = _json.load(open(zpath, encoding="utf-8"))
    except Exception:
        room_zones = {}

    remaining = set(rooms)
    zone_of = {r: (room_zones.get(str(r)) or f"__room_{r}") for r in remaining}
    ordered = []
    cur = start

    # Greedy: repeatedly find the nearest remaining room; when we enter its zone,
    # clear all of that zone's remaining rooms before moving on.
    while remaining:
        nxt = bfs_nearest(cur, remaining, limit=1)
        if not nxt:
            # Unreachable from here — append the rest in original order and stop.
            ordered.extend([r for r in rooms if r in remaining])
            break
        entry, _ = nxt[0]
        zone = zone_of[entry]
        ordered.append(entry); remaining.discard(entry); cur = entry
        # clear the rest of this zone greedily
        zone_rooms = {r for r in remaining if zone_of[r] == zone}
        while zone_rooms:
            nz = bfs_nearest(cur, zone_rooms, limit=1)
            if not nz:
                break
            r2, _ = nz[0]
            ordered.append(r2); remaining.discard(r2); zone_rooms.discard(r2); cur = r2
    return ordered


def resolve_slayer_targets():
    """Return [{"alias", "name", "room", "mob_id"}] for every daily slayer god,
    plus a list of any that couldn't be resolved against Mobs.txt."""
    mobs = load_reference_mobs()
    resolved, unresolved = [], []
    for alias, name in SLAYER_TARGETS.items():
        m = mobs.get(name.lower())
        if m and m["rooms"]:
            resolved.append({"alias": alias, "name": name,
                             "room": m["rooms"][0], "mob_id": m["id"]})
        else:
            unresolved.append((alias, name))
    return resolved, unresolved


# ---------------------------------------------------------------------------
# Crew rankings (/ajax/rankings.php?type=<category>) — JSON endpoint
# ---------------------------------------------------------------------------

def parse_crew_rankings(raw: str):
    """Parse the /ajax/rankings.php JSON payload into a list of
    {rank, id, name, stat}. For crew categories, `id` is the crew id.
    Returns [] on any parse failure."""
    import json
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out = []
    for r in data.get("results", []) or []:
        raw_stat = r.get("stat")
        try:
            stat = int(str(raw_stat).replace(",", "")) if raw_stat not in (None, "") else 0
        except (ValueError, TypeError):
            stat = 0
        rid = r.get("id")
        try:
            rid = int(rid)
        except (ValueError, TypeError):
            pass
        out.append({
            "rank": int(r.get("rank", 0) or 0),
            "id":   rid,
            "name": (r.get("name") or "").strip(),
            "stat": stat,
        })
    return out


# ---------------------------------------------------------------------------
# Backpack keys / teleporter knowledge base
# ---------------------------------------------------------------------------

def parse_backpack_items(html: str) -> list:
    """Return ALL items in a backpack tab as {item_name, item_id, quantity}.
    (parse_backpack_for_item filters by name; this returns everything.)"""
    soup = BeautifulSoup(html, "lxml")
    items, seen = [], set()
    for img in soup.find_all("img"):
        name = img.get("data-name", "")
        iid = img.get("data-iid", "")
        if not name or not iid or iid in seen:
            continue
        seen.add(iid)
        try:
            qty = int(img.get("data-itemidqty", "1"))
        except (ValueError, TypeError):
            qty = 1
        items.append({"item_name": name, "item_id": iid, "quantity": qty})
    return items


def parse_teleport_destination(rollover_html: str):
    """If an item's rollover describes a teleporter, return (destination, kind)
    where kind is 'reusable' or 'consumable'; else (None, None). Name-agnostic —
    keys on the destination sentence, which comes in two phrasings:
      reusable   -> 'Activate to warp to the <Area> of ...'   (permanent, no cost)
      consumable -> 'Teleports you to the <Area>.'            (one-time key, DEPLETES)
    The bot should only ever auto-use reusables; consumables are reserved."""
    import re
    text = BeautifulSoup(rollover_html, "lxml").get_text(" ", strip=True)
    m = re.search(r"[Aa]ctivate to warp to (?:the\s+)?(.+?)(?:\s+of the\s+|\.|$)", text)
    if m:
        return m.group(1).strip(), "reusable"
    m = re.search(r"[Tt]eleports you to (?:the\s+)?(.+?)\.", text)
    if m:
        return m.group(1).strip(), "consumable"
    return None, None


_AREA_MAP_CACHE = None

def room_to_area_map() -> dict:
    """Return {room_id: area_id} built from Areas.txt (cached). Lets us group
    gods by area so a run clears clusters together instead of zig-zagging."""
    global _AREA_MAP_CACHE
    if _AREA_MAP_CACHE is not None:
        return _AREA_MAP_CACHE
    import json, os
    path = os.path.join(os.path.dirname(__file__), "Areas.txt")
    m = {}
    try:
        for a in json.load(open(path, encoding="utf-8")):
            for r in a.get("Rooms", []):
                try:
                    m[int(r)] = a.get("Id", 0)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    _AREA_MAP_CACHE = m
    return m


def parse_join_limits(html: str):
    """Parse a slayer god's join page for its party size limits.
    Looks for 'Minimum: 20 , Maximum 60' -> (20, 60). Returns (min, max) or None.
    These are the MIN accounts needed to launch and the MAX that can join — NOT
    prime-god raid caps (a different game mechanic)."""
    import re
    if not html:
        return None
    m = re.search(r"Minimum:\s*(\d+)\s*,\s*Maximum\s*(\d+)", html, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def size_slayer_roster(needers, non_needers, max_join, min_join=0, scores=None):
    """Choose which accounts to send to a slayer god, given its join limits.
    - Fill toward MAX with needers first (they get the completion), then backfill
      with the strongest non-needers (by `scores` if given, else existing order).
    - Never exceed max_join; if the total is below min_join the raid can't launch,
      so backfill up to at least min_join even if it means extra non-needers.
    Returns the sized roster (list of trustee dicts)."""
    if max_join is None or max_join <= 0:
        return needers + non_needers          # no known limit -> send everyone (today's behaviour)

    roster = list(needers[:max_join])         # needers first, capped at max
    remaining = [t for t in non_needers if t not in roster]
    if scores:
        remaining.sort(key=lambda t: -scores.get(t.get("name"), 0.0))
    # Backfill toward max (protects win rate for small crews — fill to max, not min)
    for t in remaining:
        if len(roster) >= max_join:
            break
        roster.append(t)
    # Ensure we can at least launch (reach min); needers may already exceed max
    # in which case min is moot. Only matters when roster < min_join.
    return roster


def parse_crew_cap_status(html: str) -> dict:
    """Parse the 'Crew Member Status' table from crew_capstatus into
    {account_name: {"used": int, "max": int, "next_expiry": str}}.

    Row format: <tr><td>AberamaGold</td><td>10/10</td><td>08/13/26 03:13am</td></tr>
    where 10/10 = used/max caps and the last cell is when the next cap frees up.
    Isolates the 'Crew Member Status' section first so the left-side 'Player Cap
    Status' table isn't picked up.
    """
    import re
    if not html:
        return {}
    lower = html.lower()
    start = lower.find("crew member status")
    section = html[start:] if start >= 0 else html

    out = {}
    row_re = re.compile(
        r"<tr>\s*<td>\s*([^<]+?)\s*</td>\s*"
        r"<td>\s*(\d+)\s*/\s*(\d+)\s*</td>\s*"
        r"<td>\s*([^<]*?)\s*</td>",
        re.I | re.S,
    )
    for m in row_re.finditer(section):
        name = m.group(1).strip()
        used = int(m.group(2))
        mx   = int(m.group(3))
        expiry = m.group(4).strip()
        if name:
            out[name] = {"used": used, "max": mx, "next_expiry": expiry or None}
    return out
