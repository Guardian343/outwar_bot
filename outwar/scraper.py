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
    envoy_id: int = 0
    name: str = ""
    spawned: bool = False
    stats_url: str = ""


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


def parse_max_rage(html: str):
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


# ---------------------------------------------------------------------------
# Boss scraping
# ---------------------------------------------------------------------------

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
            print(f"Error parsing boss card: {e}")

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
            print(f"Error parsing god: {e}")

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
            print(f"Error parsing god drop row: {e}")

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
                print(f"Error parsing envoy: {e}")

    return envoys


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


SLAYER_TARGETS = {
    "gnorb": "Gnorb", "nessam": "Nessam", "crane": "Crane",
    "wanh": "Wanhiroeaz the Devourer", "pinosis": "Pinosis", "tsort": "Tsort",
    "shadow": "Shadow", "xordam": "Lord Xordam", "mistress": "Mistress of the Sword",
    "hyrak": "Hyrak, Bringer of Nightmares", "ariella": "Lady Ariella",
    "vitkros": "Vitkros, Hydra of the Deep", "trax": "Traxodon the Plaguebringer",
    "ganeshan": "Great Lord Ganeshan", "suka": "Lord Suka", "narada": "Lord Narada",
    "varan": "Lord Varan", "smoot": "Smoot the Yeti",
    "dreg": "Dreg nor, Keeper of the Infernal Essence", "sib": "Lord Sibannac",
    "ash": "King Ashnar, Lord of the Unliving", "emerald": "The Emerald Assassin",
    "murder": "Murderface", "anguish": "Anguish", "samatha": "Samatha Dark-Soul",
    "detox": "Detox", "tylos": "Tylos, The Lord Master", "threk": "Threk, King of Lords",
    "jazzmin": "Jazzmin, Maiden of Vitality", "garland": "Garland, The Lord Keeper",
    "sigil": "Sigil, Lich of Woe", "terrance": "Terrance, Rebel of Rallis",
    "synge": "Synge, The Red Dragon", "rancid": "Rancid, Lord of Thugs",
    "quiver": "Quiver, The Renegade", "zertan": "Zertan, The Collector",
    "ebliss": "Ebliss, Fallen Angel of Despair", "wind": "Windstrike The Vile",
    "skybrine": "Skybrine The Inescapable", "brut": "Brutalitar, Lord of the Underworld",
    "grizzly": "Bloodchill the Grizzly", "ganja": "Ganja the Stone Golem",
    "kro": "Kro Shuk, Doomslayer", "nabak": "Ag Nabak the Abomination",
    "nar": "Nar Zhul, Slayer of All", "baron": "Baron Mu, Dark Rider of the Undead",
    "melt": "Melt Bane, The Forbidden Demon Dragon", "rot": "Rotborn, Eater of the Dead",
    "freeze": "Freezebreed, The Frozen Manipulator", "hacker": "Hackerphage, Protector of the Gateway",
    "jade": "Jade Dragonite", "drake": "Old World Drake", "captain": "Animated Captain",
    "crantos": "Crantos, Defender of Ultimation", "varsanor": "Varsanor, Master of Darkness",
    "grivvek": "Grivvek, Protector of the Brood",
}


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
