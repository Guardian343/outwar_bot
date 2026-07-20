"""
constants.py — Skill IDs, potion names, boss pot lists, items.
"""

# ---------------------------------------------------------------------------
# Skills (Outwar skill IDs)
# ---------------------------------------------------------------------------

class Skill:
    # Class Skills
    EMPOWER                 = 3
    STEALTH                 = 4
    ON_GUARD                = 7
    TELEPORT                = 27
    VITAMIN_X               = 22
    FORTIFY                 = 28
    STREET_SMARTS           = 25
    MASTERFUL_FEROCITY      = 3182
    MASTERFUL_PRESERVATION  = 3183
    MASTERFUL_AFFLICTION    = 3184

    # Ferocity Skills
    BOOST                   = 9
    PROTECTION              = 26
    ACCURATE_STRIKE         = 29
    DARK_STRENGTH           = 312
    SWIFTNESS               = 87
    HASTE                   = 3024
    MASTERFUL_LOOTING       = 17
    CIRCUMSPECT             = 3008
    BLOODLUST               = 5
    STONE_SKIN              = 3007
    LOYAL_FEROCITY          = 3199

    # Preservation Skills
    MASTERFUL_RAIDING       = 3013
    MARKDOWN                = 3014
    LAST_STAND              = 3010
    STRENGTH_IN_NUMBERS     = 3015
    FORCEFIELD              = 3009
    BLESSING_FROM_ABOVE     = 2
    ENCHANT_ARMOR           = 3011
    ELEMENTAL_POWER         = 3012
    EXECUTIONER             = 3025
    ELEMENTAL_BARRIER       = 3006
    LOYAL_PRESERVATION      = 3200

    # Affliction Skills
    HITMAN                  = 36
    UPROAR                  = 33
    KILLING_SPREE           = 35
    AMBUSH                  = 8
    BLIND                   = 10
    POISON_DART             = 16
    CIRCLE_OF_PROTECTION    = 14
    SUNDER_ARMOR            = 21
    VANISH                  = 3016
    TIME_WARP               = 3017
    LOYAL_AFFLICTION        = 3201

    # Misc Skills
    SHIELD_WALL             = 46
    GOD_SLAYER              = 3174
    DAILY_GRIND             = 2996
    TRIWORLD_INFLUENCE      = 3197

# ---------------------------------------------------------------------------
# Skill groups (for !cast-pres, !cast-fero, !cast-afflic, !cast-class)
# ---------------------------------------------------------------------------

CLASS_SKILLS = [
    # On Guard and Street Smarts deliberately excluded — the !guard-start loop
    # keeps those two permanently recast on their own cooldown timers. Masterful
    # Ferocity/Affliction excluded too (DC/PvP skills, no raid use). Masterful
    # Preservation is kept here as it's wanted on class casts.
    Skill.EMPOWER,
    Skill.STEALTH,
    Skill.VITAMIN_X,
    Skill.FORTIFY,
    Skill.MASTERFUL_PRESERVATION,
]

FEROCITY_SKILLS = [
    Skill.BOOST,
    Skill.PROTECTION,
    Skill.ACCURATE_STRIKE,
    Skill.DARK_STRENGTH,
    Skill.SWIFTNESS,
    Skill.HASTE,
    Skill.MASTERFUL_LOOTING,
    Skill.CIRCUMSPECT,
    Skill.BLOODLUST,
    Skill.STONE_SKIN,
    Skill.LOYAL_FEROCITY,
    Skill.MASTERFUL_FEROCITY,
]

PRESERVATION_SKILLS = [
    # Markdown and Strength in Numbers intentionally NOT here — MD is a boss-raid
    # skill (cast via the raid path, never on prime groups) and SiN rotates
    # separately. Masterful Preservation is included (moved in from class).
    Skill.MASTERFUL_RAIDING,
    Skill.LAST_STAND,
    Skill.FORCEFIELD,
    Skill.BLESSING_FROM_ABOVE,
    Skill.ENCHANT_ARMOR,
    Skill.ELEMENTAL_POWER,
    Skill.EXECUTIONER,
    Skill.ELEMENTAL_BARRIER,
    Skill.LOYAL_PRESERVATION,
    Skill.MASTERFUL_PRESERVATION,
]

AFFLICTION_SKILLS = [
    Skill.HITMAN,
    Skill.UPROAR,
    Skill.KILLING_SPREE,
    Skill.AMBUSH,
    Skill.BLIND,
    Skill.POISON_DART,
    Skill.CIRCLE_OF_PROTECTION,
    Skill.SUNDER_ARMOR,
    Skill.VANISH,
    Skill.TIME_WARP,
    Skill.LOYAL_AFFLICTION,
    Skill.MASTERFUL_AFFLICTION,
]

# Misc quick set — the boss-specific damage skills, for !cast misc.
MISC_SKILLS = [
    Skill.SHIELD_WALL,
    Skill.GOD_SLAYER,
    Skill.TRIWORLD_INFLUENCE,
]

SKILL_NAMES = {
    # Class
    Skill.EMPOWER:                "Empower",
    Skill.STEALTH:                "Stealth",
    Skill.ON_GUARD:               "On Guard",
    Skill.TELEPORT:               "Teleport",
    Skill.VITAMIN_X:              "Vitamin X",
    Skill.FORTIFY:                "Fortify",
    Skill.STREET_SMARTS:          "Street Smarts",
    Skill.MASTERFUL_FEROCITY:     "Masterful Ferocity",
    Skill.MASTERFUL_PRESERVATION: "Masterful Preservation",
    Skill.MASTERFUL_AFFLICTION:   "Masterful Affliction",
    # Ferocity
    Skill.BOOST:                  "Boost",
    Skill.PROTECTION:             "Protection",
    Skill.ACCURATE_STRIKE:        "Accurate Strike",
    Skill.DARK_STRENGTH:          "Dark Strength",
    Skill.SWIFTNESS:              "Swiftness",
    Skill.HASTE:                  "Haste",
    Skill.MASTERFUL_LOOTING:      "Masterful Looting",
    Skill.CIRCUMSPECT:            "Circumspect",
    Skill.BLOODLUST:              "Bloodlust",
    Skill.STONE_SKIN:             "Stone Skin",
    Skill.LOYAL_FEROCITY:         "Loyal Ferocity",
    # Preservation
    Skill.MASTERFUL_RAIDING:      "Masterful Raiding",
    Skill.MARKDOWN:               "Markdown",
    Skill.LAST_STAND:             "Last Stand",
    Skill.STRENGTH_IN_NUMBERS:    "Strength in Numbers",
    Skill.FORCEFIELD:             "Forcefield",
    Skill.BLESSING_FROM_ABOVE:    "Blessing from Above",
    Skill.ENCHANT_ARMOR:          "Enchant Armor",
    Skill.ELEMENTAL_POWER:        "Elemental Power",
    Skill.EXECUTIONER:            "Executioner",
    Skill.ELEMENTAL_BARRIER:      "Elemental Barrier",
    Skill.LOYAL_PRESERVATION:     "Loyal Preservation",
    # Affliction
    Skill.HITMAN:                 "Hitman",
    Skill.UPROAR:                 "Uproar",
    Skill.KILLING_SPREE:          "Killing Spree",
    Skill.AMBUSH:                 "Ambush",
    Skill.BLIND:                  "Blind",
    Skill.POISON_DART:            "Poison Dart",
    Skill.CIRCLE_OF_PROTECTION:   "Circle of Protection",
    Skill.SUNDER_ARMOR:           "Sunder Armor",
    Skill.VANISH:                 "Vanish",
    Skill.TIME_WARP:              "Time Warp",
    Skill.LOYAL_AFFLICTION:       "Loyal Affliction",
    # Misc
    Skill.SHIELD_WALL:            "Shield Wall",
    Skill.GOD_SLAYER:             "God Slayer",
    Skill.DAILY_GRIND:            "Daily Grind",
    Skill.TRIWORLD_INFLUENCE:     "Triworld Influence",
}

SKILL_ALIASES = {
    # Class
    "empower":              Skill.EMPOWER,
    "emp":                  Skill.EMPOWER,
    "stealth":              Skill.STEALTH,
    "onguard":              Skill.ON_GUARD,
    "guard":                Skill.ON_GUARD,
    "teleport":             Skill.TELEPORT,
    "tp":                   Skill.TELEPORT,
    "vitaminx":             Skill.VITAMIN_X,
    "vitx":                 Skill.VITAMIN_X,
    "vx":                   Skill.VITAMIN_X,
    "vitamin":              Skill.VITAMIN_X,
    "fortify":              Skill.FORTIFY,
    "fort":                 Skill.FORTIFY,
    "streetsmarts":         Skill.STREET_SMARTS,
    "ss":                   Skill.STREET_SMARTS,
    "street":               Skill.STREET_SMARTS,
    "masterfulferocity":    Skill.MASTERFUL_FEROCITY,
    "mf":                   Skill.MASTERFUL_FEROCITY,
    "masterfulpreservation":Skill.MASTERFUL_PRESERVATION,
    "masterfulaffliction":  Skill.MASTERFUL_AFFLICTION,
    # Ferocity
    "boost":                Skill.BOOST,
    "protection":           Skill.PROTECTION,
    "prot":                 Skill.PROTECTION,
    "accuratestrike":       Skill.ACCURATE_STRIKE,
    "accurate":             Skill.ACCURATE_STRIKE,
    "darkstrength":         Skill.DARK_STRENGTH,
    "dark":                 Skill.DARK_STRENGTH,
    "ds":                   Skill.DARK_STRENGTH,
    "swiftness":            Skill.SWIFTNESS,
    "swift":                Skill.SWIFTNESS,
    "haste":                Skill.HASTE,
    "masterfullooting":     Skill.MASTERFUL_LOOTING,
    "looting":              Skill.MASTERFUL_LOOTING,
    "circumspect":          Skill.CIRCUMSPECT,
    "circ":                 Skill.CIRCUMSPECT,
    "bloodlust":            Skill.BLOODLUST,
    "bl":                   Skill.BLOODLUST,
    "stoneskin":            Skill.STONE_SKIN,
    "stone":                Skill.STONE_SKIN,
    "loyalferocity":        Skill.LOYAL_FEROCITY,
    "lf":                   Skill.LOYAL_FEROCITY,
    # Preservation
    "masterfulraiding":     Skill.MASTERFUL_RAIDING,
    "markdown":             Skill.MARKDOWN,
    "md":                   Skill.MARKDOWN,
    "laststand":            Skill.LAST_STAND,
    "last":                 Skill.LAST_STAND,
    "ls":                   Skill.LAST_STAND,
    "strengthinnumbers":    Skill.STRENGTH_IN_NUMBERS,
    "sin":                  Skill.STRENGTH_IN_NUMBERS,
    "strength":             Skill.STRENGTH_IN_NUMBERS,
    "forcefield":           Skill.FORCEFIELD,
    "ff":                   Skill.FORCEFIELD,
    "blessingfromabove":    Skill.BLESSING_FROM_ABOVE,
    "blessing":             Skill.BLESSING_FROM_ABOVE,
    "bfa":                  Skill.BLESSING_FROM_ABOVE,
    "enchantarmor":         Skill.ENCHANT_ARMOR,
    "enchant":              Skill.ENCHANT_ARMOR,
    "ea":                   Skill.ENCHANT_ARMOR,
    "elementalpower":       Skill.ELEMENTAL_POWER,
    "elepower":             Skill.ELEMENTAL_POWER,
    "ep":                   Skill.ELEMENTAL_POWER,
    "executioner":          Skill.EXECUTIONER,
    "exec":                 Skill.EXECUTIONER,
    "elementalbarrier":     Skill.ELEMENTAL_BARRIER,
    "barrier":              Skill.ELEMENTAL_BARRIER,
    "eb":                   Skill.ELEMENTAL_BARRIER,
    "loyalpreservation":    Skill.LOYAL_PRESERVATION,
    "lp":                   Skill.LOYAL_PRESERVATION,
    # Affliction
    "hitman":               Skill.HITMAN,
    "uproar":               Skill.UPROAR,
    "killingspree":         Skill.KILLING_SPREE,
    "spree":                Skill.KILLING_SPREE,
    "ks":                   Skill.KILLING_SPREE,
    "ambush":               Skill.AMBUSH,
    "blind":                Skill.BLIND,
    "poisondart":           Skill.POISON_DART,
    "poison":               Skill.POISON_DART,
    "pd":                   Skill.POISON_DART,
    "circleofprotection":   Skill.CIRCLE_OF_PROTECTION,
    "circle":               Skill.CIRCLE_OF_PROTECTION,
    "cop":                  Skill.CIRCLE_OF_PROTECTION,
    "sunderarmor":          Skill.SUNDER_ARMOR,
    "sunder":               Skill.SUNDER_ARMOR,
    "vanish":               Skill.VANISH,
    "timewarp":             Skill.TIME_WARP,
    "warp":                 Skill.TIME_WARP,
    "tw":                   Skill.TIME_WARP,
    "loyalaffliction":      Skill.LOYAL_AFFLICTION,
    "la":                   Skill.LOYAL_AFFLICTION,
    # Misc
    "shieldwall":           Skill.SHIELD_WALL,
    "shield":               Skill.SHIELD_WALL,
    "sw":                   Skill.SHIELD_WALL,
    "godslayer":            Skill.GOD_SLAYER,
    "gs":                   Skill.GOD_SLAYER,
    "slayer":               Skill.GOD_SLAYER,
    "dailygrind":           Skill.DAILY_GRIND,
    "daily":                Skill.DAILY_GRIND,
    "dg":                   Skill.DAILY_GRIND,
    "triworldinfluence":    Skill.TRIWORLD_INFLUENCE,
    "triworld":             Skill.TRIWORLD_INFLUENCE,
    "ti":                   Skill.TRIWORLD_INFLUENCE,
}


def resolve_skill(name: str) -> tuple[int, str]:
    """
    Resolve a skill name or alias to (skill_id, skill_name).
    Returns (None, None) if not found.
    """
    key = name.lower().replace(" ", "").replace("-", "").replace("_", "")
    skill_id = SKILL_ALIASES.get(key)
    if skill_id is not None:
        return skill_id, SKILL_NAMES.get(skill_id, name)
    return None, None


# ---------------------------------------------------------------------------
# Potions
# ---------------------------------------------------------------------------

POTIONS = {
    # Full keys
    "nvile":      "Natas Vile",
    "wvile":      "White Vile",
    "avile":      "Arcane Vile",
    "svile":      "Shadow Vile",
    "kvile":      "Kinetic Vile",
    "fvile":      "Fire Vile",
    "vilee":      "Vile Energy",
    "rems":       "Remnant Solice",
    "rems8":      "Remnant Solice Lev 8",
    "rems9":      "Remnant Solice Lev 9",
    "rems10":     "Remnant Solice Lev 10",
    "rems11":     "Remnant Solice Lev 11",
    "vile":       "Vile",
    "resist":     "Potion of Elemental Resistance",
    "bubble":     "Bubble Gum",
    "starburst":  "Starburst",
    "skittle":    "Skittles",
    "snickers":   "Snickers Bar",
    "m&ms":       "M&Ms",
    "reese":      "Reeses Peanut Butter Cup",
    "minor":      "Minor Chaos Philter",
    "major":      "Major Chaos Philter",
    "amdir":      "Potion of Amdir",
    "kix":        "Kix Potion",
    "star":       "Star Power",
    "kit":        "Kit Kat",
    "tootsie":    "Tootsie Roll",
    "squid":      "Squidberry Juice",
    "strength":   "Strength Potion",
    "sresist":    "Shadow Resistance",
    "kresist":    "Kinetic Resistance",
    "wonderland": "Wonderland Potion",
    # --- Zombie potions ---
    "zombie1":    "Zombie Potion 1",
    "zombie2":    "Zombie Potion 2",
    "zombie3":    "Zombie Potion 3",
    "zombie4":    "Zombie Potion 4",
    "zombie5":    "Zombie Potion 5",
    "zombie6":    "Zombie Potion 6",
    # --- Pot pack ---
    "alsayic":    "Potion of Enraged Alsayic",
    "dose":       "Dose of Destruction",
    "mushroom":   "Funny Little Mushroom",
    "pumpkin":    "Pumpkin Juice",
    "sammy":      "Sammy Sosa's Special Sauce",
    "holy":       "Bottle of Holy Slaughter",
    "burning":    "Flask of Burning Souls",
    "lightning":  "Flask of Conjured Lightning",
    "flaming":    "Flask of Flaming Death",
    "forbidden":  "Flask of Forbidden Knowledge",
    "supernova":  "Flask of Super Nova",
    "juicebox":   "Olympian Juicebox",
    "push":       "Olympian Push",
    # --- High end ---
    "insanity":   "Vial of Insanity",
    "demonic":    "Demonic Madness",
    "tincture":   "Triworld Tincture",
    "seething":   "Seething Echoes",
    # Aliases
    "rem":        "Remnant Solice",
    "eleresm":    "Potion of Elemental Resistance",
    "wonder":     "Wonderland Potion",
    "wl":         "Wonderland Potion",
}

# ---------------------------------------------------------------------------
# Named potion groups — so you can cast a whole set by one name instead of
# listing pots individually (e.g. in primewatcher groups, or !drink).
#
# IMPORTANT: potions are matched by their EXACT in-game name (see POTIONS above)
# against the backpack. If a name here doesn't match the item exactly, that pot
# is silently reported as "not in backpack" rather than erroring — so if a pot
# never seems to cast, check its name string first.
# ---------------------------------------------------------------------------
POT_GROUPS = {
    # The freebies — always available, cheap to keep up.
    "free": ["rems", "resist", "squid", "amdir", "kix"],
    # Zombie potion series.
    "zombies": ["zombie1", "zombie2", "zombie3", "zombie4", "zombie5", "zombie6"],
    # The standard purchasable pot pack.
    "potpack": [
        "alsayic", "dose", "mushroom", "pumpkin", "sammy", "holy",
        "burning", "lightning", "flaming", "forbidden", "supernova",
        "juicebox", "push",
    ],
    # The expensive/rare top-tier pots.
    "highend": ["insanity", "demonic", "tincture", "seething"],
    # Chaos philters.
    "chaos": ["minor", "major"],
}

# "all" = every potion group combined, de-duplicated, order preserved.
POT_GROUPS["all"] = list(dict.fromkeys(
    POT_GROUPS["free"] + POT_GROUPS["chaos"] + POT_GROUPS["zombies"]
    + POT_GROUPS["potpack"] + POT_GROUPS["highend"]
))

# Friendly labels for display in Discord / the dashboard.
POT_GROUP_LABELS = {
    "free":    "Free Pots",
    "zombies": "Zombies",
    "potpack": "Pot Pack",
    "highend": "High End",
    "chaos":   "Chaos Pots",
    "all":     "All Pots",
}

# Boss-specific pot lists
# Duration in seconds each boss potion lasts
POT_DURATIONS = {
    "rems":       15360,  # 4h 16m
    "rems8":      15360,
    "rems9":      15360,
    "rems10":     15360,
    "rems11":     15360,
    "kix":        3960,   # 1h 6m
    "minor":      3960,   # 1h 6m
    "amdir":      3960,   # 1h 6m
    "squid":      3960,   # 1h 6m
    "resist":     3960,   # 1h 6m — confirm if different
    "wonderland": 5940,   # 1h 39m
}

BOSS_POTS = {
    # Base bosses — Remnant Solice, Squidberry Juice, Elemental Resistance, Kix, Amdir
    "cosmos":    ["rems", "squid", "resist", "kix", "amdir"],
    "mae":       ["rems", "squid", "resist", "kix", "amdir"],
    "death":     ["rems", "squid", "resist", "kix", "amdir"],
    "blackhand": ["rems", "squid", "resist", "kix", "amdir"],
    "maekrix":   ["rems", "squid", "resist", "kix", "amdir"],
    # Higher bosses — all above plus Minor Chaos Philter and Wonderland Potion
    "zyrak":     ["rems", "squid", "resist", "kix", "amdir", "minor", "wonderland"],
    "triworld":  ["rems", "squid", "resist", "kix", "amdir", "minor", "wonderland"],
}

DRINK_ALL_ORDER = [
    "rems", "vile", "resist", "bubble", "starburst", "skittle",
    "snickers", "m&ms", "reese", "minor", "amdir", "kix",
    "star", "kit", "tootsie", "squid",
]

# Backpack tab for each potion type
BACKPACK_TABS = {
    "regular": "&tab=regular",
    "quest":   "&tab=quest",
    "orb":     "&tab=orb",
    "potion":  "&tab=potion",
    "key":     "&tab=key",
}

# ---------------------------------------------------------------------------
# Items (for ?check-item)
# Keys are the short names users type. Values:
#   name       — full item name as it appears in Outwar HTML data-name attributes
#   level      — minimum character level required to equip/have the item
#   tab        — backpack tab: regular/quest/orb/potion/key/equipped
#   equipped   — True = check equipment page instead of backpack
#   count      — True = show quantity per character
#   grouped    — True = just list names together, False = group by item tier/id
# ---------------------------------------------------------------------------

ITEMS = {
    # Chaos Gems (equipped slot)
    "chaosgem":  {"name": "Chaos Gem",                      "level": 1,  "tab": "equipped", "equipped": True,  "count": False, "grouped": False},
    "gem":       {"name": "Chaos Gem",                      "level": 1,  "tab": "equipped", "equipped": True,  "count": False, "grouped": False},
    # Runes (equipped slot)
    "rune":      {"name": "Rune",                           "level": 1,  "tab": "equipped", "equipped": True,  "count": False, "grouped": False},
    "erune":     {"name": "Elemental Rune",                 "level": 1,  "tab": "equipped", "equipped": True,  "count": False, "grouped": False},
    # Crests (equipped slot)
    "crest":     {"name": "Crest",                          "level": 1,  "tab": "equipped", "equipped": True,  "count": False, "grouped": False},
    # Orbs
    "orb":       {"name": "Orb",                            "level": 1,  "tab": "orb",      "equipped": False, "count": False, "grouped": True},
    # Potions
    "resist":    {"name": "Potion of Elemental Resistance", "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "bubble":    {"name": "Bubble Gum",                     "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "rems":      {"name": "Remnant Solice",                 "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "vile":      {"name": "Vile",                           "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "amdir":     {"name": "Potion of Amdir",                "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "kix":       {"name": "Kix Potion",                     "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "squid":     {"name": "Squidberry Juice",               "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "minor":     {"name": "Minor Chaos Philter",            "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "wonderland":{"name": "Wonderland Potion",              "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "snickers":  {"name": "Snickers Bar",                   "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "starburst": {"name": "Starburst",                      "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "skittle":   {"name": "Skittles",                       "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "reese":     {"name": "Reeses Peanut Butter Cup",       "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "kit":       {"name": "Kit Kat",                        "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "tootsie":   {"name": "Tootsie Roll",                   "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "star":      {"name": "Star Power",                     "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    "m&ms":      {"name": "M&Ms",                           "level": 1,  "tab": "potion",   "equipped": False, "count": False, "grouped": True},
    # Keys
    "key":       {"name": "Key",                            "level": 1,  "tab": "key",      "equipped": False, "count": False, "grouped": True},
    # Regular items with quantity tracking
    "chaosore":  {"name": "Chaos Ore",                      "level": 1,  "tab": "regular",  "equipped": False, "count": True,  "grouped": False},
    "badgerep":  {"name": "Badge Reputation",               "level": 1,  "tab": "regular",  "equipped": False, "count": True,  "grouped": False},
    # Quest
    "questitem": {"name": "Quest",                          "level": 1,  "tab": "quest",    "equipped": False, "count": False, "grouped": True},
}

# ---------------------------------------------------------------------------
# ?top-all exclusions — bot/alt accounts that should never appear in rankings
# ---------------------------------------------------------------------------

TOP_ALL_EXCLUDED_NAMES = {
    "guardianliam", "brabbit2005", "cashmoney", "scaryface", "mooncake",
    "don_carlos_gambino", "temporalworm", "qanon", "ownageleader", "dq006",
    "darkempire", "dqst2", "dq089", "ghostface", "cashymccashface",
    "4doorsmorewhores", "anorak", "aone", "ashgraven", "avocato",
    "blimblamtheklorblok", "cipher", "fraskenhaur", "garygoodspeed",
    "helperhula", "helperstevil", "johngoodspeed", "juggalojoe", "katrider",
    "krombopulosmichael", "lazarustrap", "lhue", "littlecato", "lkvn",
    "lordcommander", "northtexasjuggalo", "oreskis", "quinnergon",
    "revolioclockbergjr", "scaryabradolflincler", "scaryaugs", "scarybethsmith",
    "scarybirdperson", "scarycarrots", "scarycelestial", "scaryclockwork",
    "scarydawg", "scarydrxenonbloom", "scarygazorpian", "scarygodlyprotection",
    "scarygods", "scaryjerrysmith", "scarymeanieface", "scarymorty",
    "scarymrmeeseeks", "scarypenguin", "scarypotions", "scarypwnagerhino",
    "scaryquest", "scaryrhino", "scaryrick", "scarysquanchy", "scaryunity",
    "sherylgoodspeed", "slutface", "tacticaltaint", "thehypnotoad",
    "thelegendros", "theorder", "timeswapsammy", "triboremenendez",
    "twisteddemon", "ventrexian", "werthrent", "xscary10", "xscary12",
    "xscary14", "xscary19", "xscary20", "xscary21", "xscary30", "xscary31",
    "xscary33", "xscary34", "xscary43", "zmobsy",
}

TOP_ALL_EXCLUDED_SUBSTRINGS = ["pwnage", "beastly", "darkqueen", "queen"]

# ---------------------------------------------------------------------------
# ?giveaway — Discord participants (name -> user ID)
# Add or remove names here as your group changes
# ---------------------------------------------------------------------------

GIVEAWAY_USERS = {
    "rabbit": 390295499518902273,
    "cash":   528366318278148102,
    "scary":  414499511369728011,
    "liam":   412681493157249044,
    "dq":     542182498801680384,
    "ppb":    486402907411972109,
}
