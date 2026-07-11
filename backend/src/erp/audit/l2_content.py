"""L2 软证据规则 R7（促销宣称）+ R8（敏感内容），移植自 walmart-audit-system l2_rules。

两者均 penalty=0（不自行否决）→ 命中收进 audit_hit 并surface到 L3 user prompt，
由 L3 结合上下文 + 37 政策块（Offensive Content 等）判是否真违规。扫 title + 前 3 条
bullet（不扫 long_description，避免 "premium quality" 类词在长文里误杀）。

正则表逐字移植（源仓精心校准，勿轻改）。
"""

import re

# ── R7：促销/宣称性内容（Walmart Content Standards）──

_PROMO_PHRASES_STRONG = [
    r"\bpremium\s+quality\b",
    r"\bcommercial\s+grade\b",
    r"\bindustrial\s+grade\b",
    r"\bprofessional\s+grade\b",
    r"\bheavy\s+duty\b",
    r"\bmilitary\s+grade\b",
    r"\bmedical\s+grade\b",
    r"\bfood[-\s]grade\b",
    r"#\s*1\b",
    r"\bno\.\s*1\b",
    r"\btop\s+rated\b",
    r"\bbest\s+seller\b",
    r"\bbest\s+selling\b",
    r"\bbest[-\s]in[-\s]class\b",
    r"\bhighest\s+rated\b",
    r"\bworld'?s\s+best\b",
    r"\bworld'?s\s+(?:leading|greatest|finest)\b",
    r"\bbest[-\s]ever\b",
    r"\b(?:fda|usda|epa|ul|etl)[-\s]approved\b",
    r"\b100%\s+(?:guaranteed|pure|natural|organic|authentic|genuine)\b",
    r"\blifetime\s+(?:warranty|guarantee)\b",
    r"\bmoney[-\s]back\s+guarantee\b",
    r"\bsatisfaction\s+guaranteed\b",
]

_PROMO_PHRASES_SOFT = [
    r"\bultra[-\s]premium\b",
    r"\bhigh[-\s]quality\b",
    r"\btop[-\s]quality\b",
    r"\bsuper[-\s]strong\b",
    r"\bsuper[-\s]durable\b",
    r"\bextra[-\s]strong\b",
    r"\bultra[-\s]durable\b",
    r"\bunbeatable\b",
    r"\bunmatched\b",
    r"\bsecond[-\s]to[-\s]none\b",
    r"\bas[-\s]seen[-\s]on[-\s]tv\b",
    r"\bfactory[-\s]direct\b",
    r"\bamazon'?s\s+choice\b",
]

_PROMO_STRONG_RE = re.compile("|".join(_PROMO_PHRASES_STRONG), re.IGNORECASE)
_PROMO_SOFT_RE = re.compile("|".join(_PROMO_PHRASES_SOFT), re.IGNORECASE)

_ALLCAPS_RUN_RE = re.compile(r"(?:\b[A-Z][A-Z0-9]{1,}\b[\s\-]+){2,}\b[A-Z][A-Z0-9]{1,}\b")

_ALLCAPS_NOISE_TOKENS = {
    "USB", "LED", "LCD", "OLED", "HDMI", "AC", "DC", "RGB", "IP",
    "GHZ", "MHZ", "HZ", "FM", "AM", "3D", "4K", "8K", "HD", "UHD",
    "PACK", "PCS", "CT", "GSM", "LBS", "OZ", "ML", "FL", "FT",
    "ISO", "ASTM", "ANSI", "NRTL", "UL", "ETL", "CE", "FCC", "RoHS",
    "USA", "US", "EU", "UK", "CA", "NBA", "NFL", "MLB", "NHL",
    "PRO", "PLUS", "MAX", "MINI", "XXL", "XL", "L", "M", "S", "XS",
}  # fmt: skip


def _scan_allcaps_runs(title: str) -> list[str]:
    """连续 3+ 全大写词（噪声 token 过滤后），返回去重原文片段。"""
    if not title:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for m in _ALLCAPS_RUN_RE.finditer(title):
        span = m.group(0).strip()
        if span in seen:
            continue
        tokens = [t for t in re.split(r"[\s\-]+", span) if t]
        real = [t for t in tokens if t.upper() not in _ALLCAPS_NOISE_TOKENS]
        if len(real) >= 3:  # noqa: PLR2004
            seen.add(span)
            hits.append(span)
    return hits


def scan_promotional(title: str, bullets: list[str]) -> dict[str, object] | None:
    """R7：命中强促销词或全大写滥用 → 证据 dict；仅软命中/无命中 → None。"""
    scan = (title or "") + "\n" + " ".join((bullets or [])[:3])
    if not scan.strip():
        return None
    strong = sorted({m.group(0) for m in _PROMO_STRONG_RE.finditer(scan)})
    soft = sorted({m.group(0) for m in _PROMO_SOFT_RE.finditer(scan)})
    allcaps = _scan_allcaps_runs(title or "")
    if not (strong or allcaps):  # 只软命中不触发（避免过激），与源仓一致
        return None
    return {
        "strong_phrases": strong,
        "soft_phrases": soft,
        "allcaps_runs": allcaps,
        "walmart_policy": "Content Standards",
    }


# ── R8：Walmart 严格合规敏感内容（文化/宗教/政治/历史/武器/成人/物质/卡通IP）──

_R8_SENSITIVE_PATTERNS: dict[str, list[str]] = {
    "cultural_day": [
        r"\bjuneteenth\b",
        r"\bblack\s+history\s+month\b",
        r"\bafro[-\s]american\b",
        r"\bafrican[-\s]american\b",
        r"\bafro\s+(?:dope|pride|king|queen)\b",
        r"\beid\s+mubarak\b",
        r"\bramadan\b",
        r"\bhanukkah\b",
        r"\bkwanzaa\b",
    ],
    "religious_single_faith": [
        r"\bbible\s+(?:poster|verse|map|study)\b",
        r"\b12\s+tribes\s+of\s+israel\b",
        r"\bmosque\b",
        r"\bsultan\s+ahmed\b",
        r"\bislam(?:ic)?\s+(?:poster|prayer|art)\b",
        r"\btorah\b",
        r"\bhindu\s+(?:poster|deity|god|goddess)\b",
        r"\bbuddha\s+(?:statue|poster|figurine)\b",
    ],
    "political_sensitive": [
        r"\bmaga\b",
        r"\bdeep\s+state\b",
        r"\bpolice\s+state\b",
        r"\bcommander\s+in\s+(?:crap|chief)\b",
        r"\bliberal\s+tears\b",
        r"\btrump\s+(?:derangement|syndrome|2024|2028)\b",
        r"\btrump\s+(?:hat|sticker|flag|pin|shirt)\b",
        r"\bbiden\s+(?:sucks|fail)\b",
        r"\blet'?s\s+go\s+brandon\b",
        r"\bfjb\b",
    ],
    "historical_intolerance": [
        r"\bconfederate\b",
        r"\bussr\s+(?:army|flag|emblem)\b",
        r"\bsoviet\s+(?:army|flag|emblem|red\s+star)\b",
        r"\bthird\s+reich\b",
        r"\bwehrmacht\b",
        r"\bss\s+(?:bolts|runes)\b",
        r"\bnazi\b",
        r"\bkkk\b",
        r"\bhamas\b",
        r"\btaliban\b",
    ],
    "weapons_decorative": [
        r"\bhunting\s+(?:gifts?|gear)\b",
        r"\bbullet\s+(?:tumbler|cup|mug|bottle|keychain|necklace|earring)\b",
        r"\b(?:deer|buck)\s+hunting\s+(?:gift|gear)\b",
        r"\bgun\s+(?:deer|keychain|pillow|tumbler|mug)\b",
        r"\bpickaxe\s+(?:prop|model|cosplay)\b",
        r"\bmachete\s+(?:prop|toy)\b",
        r"\btactical\s+(?:cosplay|party)\b",
    ],
    "adult_innuendo": [
        r"\bcouples\s+pillow\s+for\s+intimacy\b",
        r"\bintimacy\s+(?:pillow|set|toy|aid)\b",
        r"\bsensual\s+(?:massage|oil|gift)\b",
        r"\berotic\s+(?:art|gift|game)\b",
        r"\bbondage\s+(?:kit|gear|set)\b",
    ],
    "substance_decorative": [
        r"\bwine\s+and\s+(?:whisky|cigar)\b",
        r"\bwhisky\s+(?:paint\s+by\s+numbers|on\s+wood)\b",
        r"\bmarijuana\s+(?:leaf|leaves|art|sticker)\b",
        r"\bcannabis\s+(?:leaf|art|sticker)\b",
        r"\bweed\s+(?:leaf|sticker|sign)\b",
    ],
    "cartoon_ip_character": [
        r"\b(?:mickey|minnie)\s+mouse\b",
        r"\b(?:donald|daisy)\s+duck\b",
        r"\b(?:goofy|pluto|chip\s+(?:and|&|n)\s+dale)\b",
        r"\bdisney(?:\s+(?:princess|frozen|aladdin|cars))?\b",
        r"\b(?:elsa|anna|olaf|sven)\b",
        r"\b(?:moana|maui)\b",
        r"\b(?:simba|mufasa|nala|scar|timon|pumbaa|lion\s+king)\b",
        r"\b(?:winnie\s+the\s+pooh|tigger|eeyore|piglet)\b",
        r"\b(?:lilo\s*(?:and|&)?\s*stitch|\bstitch\s+(?:plush|toy|doll|costume|sticker|backpack)|stitch\s+&\s+lilo)\b",
        r"\b(?:woody|buzz\s+lightyear|toy\s+story|jessie\s+toy)\b",
        r"\b(?:nemo|dory|finding\s+(?:nemo|dory))\b",
        r"\b(?:incredibles|wall[-\s]?e|ratatouille|up\s+pixar)\b",
        r"\b(?:lightning\s+mcqueen|cars\s+(?:movie|disney))\b",
        r"\b(?:little\s+mermaid|ariel\s+(?:disney|princess)|cinderella|snow\s+white|rapunzel|tangled|brave\s+pixar|aladdin|jasmine|tiana)\b",
        r"\b(?:marvel|avengers|x[-\s]?men)\b",
        r"\b(?:iron\s+man|spider[-\s]?man|captain\s+america|thor\s+(?:marvel|hammer)|hulk\s+(?:marvel|smash)|black\s+widow|black\s+panther|ant[-\s]?man|doctor\s+strange|deadpool|wolverine|hawkeye|loki\s+marvel|scarlet\s+witch)\b",
        r"\b(?:dc\s+comics|justice\s+league)\b",
        r"\b(?:batman|superman|wonder\s+woman|aquaman|harley\s+quinn|joker\s+dc)\b",
        r"\b(?:pokemon|pok[eé]mon|pikachu|charizard|squirtle|bulbasaur|eevee|mewtwo|jigglypuff)\b",
        r"\b(?:super\s+mario|mario\s+(?:bros|kart|party|odyssey)|luigi\s+nintendo|princess\s+peach|bowser|toad\s+nintendo|yoshi)\b",
        r"\b(?:donkey\s+kong|sonic\s+(?:hedgehog|the))\b",
        r"\b(?:zelda|link\s+(?:zelda|nintendo)|breath\s+of\s+the\s+wild)\b",
        r"\b(?:hello\s+kitty|sanrio|kuromi|my\s+melody|cinnamoroll|pompompurin|gudetama|keroppi|pochacco)\b",
        r"\b(?:studio\s+ghibli|totoro|kiki'?s\s+delivery|spirited\s+away|princess\s+mononoke|howl'?s\s+moving)\b",
        r"\b(?:naruto|sasuke\s+naruto|kakashi)\b",
        r"\b(?:one\s+piece|luffy\s+pirate|zoro\s+anime)\b",
        r"\b(?:dragon\s+ball|goku\s+(?:dbz|saiyan)|vegeta\s+dbz)\b",
        r"\b(?:demon\s+slayer|kimetsu\s+no\s+yaiba|tanjiro|nezuko)\b",
        r"\b(?:attack\s+on\s+titan|jujutsu\s+kaisen|gojo\s+satoru|chainsaw\s+man)\b",
        r"\b(?:my\s+hero\s+academia|deku\s+anime)\b",
        r"\b(?:star\s+wars|mandalorian|grogu|baby\s+yoda|darth\s+vader|stormtrooper|jedi|sith\s+lord|millennium\s+falcon|kylo\s+ren|rey\s+star\s+wars)\b",
        r"\b(?:harry\s+potter|hogwarts|dumbledore|hermione|gryffindor|slytherin|hufflepuff|ravenclaw|voldemort|wizarding\s+world|fantastic\s+beasts)\b",
        r"\b(?:peppa\s+pig|george\s+pig|paw\s+patrol|chase|skye\s+paw|marshall\s+pup)\b",
        r"\b(?:bluey|cocomelon|coco\s+melon|baby\s+shark|miss\s+rachel)\b",
        r"\b(?:my\s+little\s+pony|mlp\s+(?:plush|toy)|equestria)\b",
        r"\b(?:trolls?\s+(?:movie|world)|despicable\s+me|minions?\s+(?:movie|plush|toy)|gru\s+despicable|shrek\s+(?:movie|donkey))\b",
        r"\b(?:scooby[-\s]?doo|tom\s+(?:and|&)\s+jerry|smurfs)\b",
        r"\b(?:sponge\s*bob|patrick\s+star|squidward|sandy\s+cheeks|bikini\s+bottom)\b",
        r"\b(?:rugrats|teletubbies|sesame\s+street|elmo\s+sesame|big\s+bird|cookie\s+monster\s+sesame)\b",
        r"\b(?:teenage\s+mutant\s+ninja\s+turtles|tmnt|leonardo\s+turtle|donatello\s+turtle|raphael\s+turtle)\b",
        r"\b(?:power\s+rangers|transformers|optimus\s+prime|bumblebee\s+transformers|megatron)\b",
        r"\b(?:fortnite|minecraft|roblox|among\s+us|rainbow\s+friends)\b",
        r"\blego\s+(?:set|bricks|block|figure|minifig|kit|movie|character)\b",
        r"\b(?:five\s+nights\s+at\s+freddy'?s|fnaf|huggy\s+wuggy|poppy\s+playtime)\b",
        r"\b(?:barbie\s+(?:doll|movie|fashion)|bratz\s+doll)\b",
    ],
}

_R8_COMPILED: dict[str, list[re.Pattern[str]]] = {
    subtype: [re.compile(p, re.IGNORECASE) for p in pats]
    for subtype, pats in _R8_SENSITIVE_PATTERNS.items()
}


def scan_sensitive(title: str, bullets: list[str]) -> dict[str, object] | None:
    """R8：命中任一敏感子类 → 证据 dict（含 matches_by_subtype + 扁平命中）；无命中 → None。"""
    scan = (title or "") + "\n" + " ".join((bullets or [])[:3])
    if not scan.strip():
        return None
    by_type: dict[str, list[str]] = {}
    for subtype, patterns in _R8_COMPILED.items():
        matched = [m.group(0) for pat in patterns if (m := pat.search(scan))]
        if matched:
            by_type[subtype] = sorted(set(matched))
    if not by_type:
        return None
    all_matched = sorted({p for lst in by_type.values() for p in lst})
    return {
        "subtypes": list(by_type.keys()),
        "matches_by_subtype": by_type,
        "matched": all_matched,
    }
