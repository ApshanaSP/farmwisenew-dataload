"""Text features. Regexes for hazards, vulnerable places and repeat complaints are
ported from the grievance portal's scripts/lib/intel.js so every source is scored
the same way, in English, Tamil and Tanglish."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

import numpy as np

TAMIL_CHAR = re.compile(r"[஀-௿]")
LATIN_CHAR = re.compile(r"[A-Za-z]")
TANGLISH = re.compile(
    r"\b(irukku|iruku|illa|illai|pannunga|pannanum|pannom|romba|rombo|jaasthi|mudiyala|mudiyadhu|eduklala|edukala|"
    r"varudhu|varala|pogudhu|poga|paarunga|seekiram|konjam|thadava|nikkudhu|thengi|eriyala|pasanga|periyavanga|"
    r"kashtapadranga|bayama|bayapadranga|nadakka|pakkathula|kitta|aagudhu|aayiduchu|yaarum|sollunga|edunga|panna|"
    r"kosu|mazhai|thanni|thollai|vaaram|naala)\b", re.I)


def language_of(text: Any) -> str:
    s = text if isinstance(text, str) else ""
    tamil = len(TAMIL_CHAR.findall(s))
    latin = len(LATIN_CHAR.findall(s))
    if tamil > 0 and tamil >= latin * 0.5:
        return "ta"
    markers = len(TANGLISH.findall(s))
    if markers >= 2 or (markers == 1 and latin < 60):
        return "tanglish"
    return "en"


def is_junk(text: Any) -> bool:
    s = (text if isinstance(text, str) else "").strip()
    if len(s) < 12:
        return True
    letters = len(re.findall(r"[A-Za-z஀-௿]", s))
    if letters < 0.5 * len(s):
        return True
    if re.search(r"(.)\1{5,}", s):
        return True
    words = re.findall(r"[A-Za-z]+", s)
    if words and len(s) < 40 and not TAMIL_CHAR.search(s):
        vowelless = sum(1 for w in words if len(w) > 3 and not re.search(r"[aeiouy]", w, re.I))
        if vowelless >= max(1, len(words) // 2):
            return True
    return False


# ------------------------------------------------------------- severity cues --
HIGH_RISK_SUB = re.compile(
    r"electric shock|eb wire|spark|manhole|sewage|fallen tree|dengue|malaria|mosquito|biomedical|trap at home|"
    r"water entering|elderly medicine|pregnant women issues|damage to the electric pole", re.I)
ELEVATED_SUB = re.compile(
    r"stagnation of water|pot ?hole|street dogs|non burning of street lights|overflowing|burning of garbage|dark spot|"
    r"safety|barricading|obstruction of water flow|desilting|removal of garbage|open defecation|electrical wires|stray cattle", re.I)
LOW_SUB = re.compile(
    r"certificate|registration|opening and closing hours|online payment|project information|consultation|"
    r"information in advance|revision objection|name error", re.I)
TEXT_HAZARD = re.compile(
    r"live wire|electric shock|current shock|shock adikkudhu|open manhole|sewage overflow|electrocut|collapsed|caved in|"
    r"மின்சாரம் தாக்|மின் கம்பி|திறந்த நிலையில் உள்ள|இடிந்து", re.I)
VULNERABLE = [
    ("school", re.compile(r"school|பள்ளி|anganwadi|அங்கன்வாடி", re.I)),
    ("hospital", re.compile(r"hospital|மருத்துவமனை|health cent|\bphc\b|\buphc\b|சுகாதார நிலைய", re.I)),
    ("bus_stop", re.compile(r"bus stop|bus stand|பேருந்து நிறுத்த", re.I)),
    ("worship", re.compile(r"temple|kovil|koil|கோயில்|கோவில்|church|mosque|masjid|தேவாலய|பள்ளிவாசல்", re.I)),
    ("children", re.compile(r"children|kids|pasanga|kozhandhai|குழந்தை|students|மாணவ", re.I)),
    ("elderly", re.compile(r"elderly|old age|senior citizen|periyavanga|முதியோர்|முதியவர்|வயதான", re.I)),
    ("pregnant", re.compile(r"pregnant|கர்ப்பிணி", re.I)),
]
REPEAT = re.compile(
    r"already complained|already complaint|complained (twice|thrice|again|many times|\d+ times)|no action (taken|so far|till now)|"
    r"still not (done|cleared|fixed|repaired|attended)|reminder|repeated complaint|thadava complaint|evlo thadava|பலமுறை|"
    r"ஏற்கனவே[^.]*புகார்|மீண்டும் புகார்|நடவடிக்கை இல்லை", re.I)

NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
             "several": 3, "many": 5, "few": 2, "couple": 2}
_NUM = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|several|few)"


def _num(tok: str) -> int:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else NUM_WORDS.get(tok, 1)


DURATION = [
    (re.compile(r"(?:since|for|past|last)\s+" + _NUM + r"\s+(day|days|week|weeks|month|months)", re.I), None),
    (re.compile(_NUM + r"\s+(day|days|week|weeks|month|months)\s+(?:now|already|aachu|aagudhu|ah)", re.I), None),
    (re.compile(r"(\d+)\s*(naala|naal|vaaram)", re.I), "tanglish"),
    (re.compile(r"(\d+)\s*(நாட்களாக|நாட்கள்|வாரமாக|மாதமாக)"), "ta"),
]
UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30, "naala": 1, "naal": 1,
             "vaaram": 7, "நாட்களாக": 1, "நாட்கள்": 1, "வாரமாக": 7, "மாதமாக": 30}


def claimed_age_days(text: Any) -> float | None:
    s = text if isinstance(text, str) else ""
    for rx, _ in DURATION:
        m = rx.search(s)
        if m:
            return float(_num(m.group(1)) * UNIT_DAYS.get(m.group(2).lower() if m.group(2).isascii() else m.group(2), 1))
    if re.search(r"since last (monday|tuesday|wednesday|thursday|friday|saturday|sunday|week)", s, re.I):
        return 7.0
    return None


def vulnerable_flags(text: str) -> list[str]:
    return [name for name, rx in VULNERABLE if rx.search(text or "")]


def hazard(text: str) -> bool:
    return bool(TEXT_HAZARD.search(text or ""))


def repeat_claim(text: str) -> bool:
    return bool(REPEAT.search(text or ""))


CASUALTY_EN = re.compile(
    _NUM + r"\s+(?:\w+\s+){0,3}?(?:were\s+|was\s+|are\s+|have been\s+|got\s+)?(killed|dead|died|dies|die|injured|hurt|wounded|electrocuted)", re.I)
CASUALTY_TA = re.compile(r"(\d+)\s*(?:பேர்|நபர்கள்|பேருக்கு)?\s*(பலி|உயிரிழ|காயம்|படுகாய)")
DEATH_WORDS = {"killed", "dead", "died", "dies", "die", "electrocuted", "பலி", "உயிரிழ"}


def casualties(text: str) -> tuple[int, int]:
    """(dead, injured) numbers mentioned in a news text; 0 when none."""
    dead = injured = 0
    for m in CASUALTY_EN.finditer(text or ""):
        n = _num(m.group(1))
        if n > 200:
            continue
        if m.group(2).lower() in DEATH_WORDS:
            dead = max(dead, n)
        else:
            injured = max(injured, n)
    for m in CASUALTY_TA.finditer(text or ""):
        n = int(m.group(1))
        if n > 200:
            continue
        if m.group(2) in DEATH_WORDS:
            dead = max(dead, n)
        else:
            injured = max(injured, n)
    if not dead and re.search(r"\b(man|woman|boy|girl|youth|worker|person|labourer|student)\s+(was\s+)?(killed|dies|died|electrocuted|drowned)\b", text or "", re.I):
        dead = 1
    return dead, injured


# ------------------------------------------------------------------- hashing --

def normalize(text: Any) -> str:
    s = unicodedata.normalize("NFKC", text if isinstance(text, str) else "")
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"[^\w஀-௿]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def simhash64(text: Any) -> str:
    toks = normalize(text).split()
    shingles = [" ".join(toks[i:i + 3]) for i in range(max(1, len(toks) - 2))] if toks else [""]
    v = np.zeros(64)
    for sh in shingles:
        h = int.from_bytes(hashlib.md5(sh.encode("utf-8")).digest()[:8], "big")
        bits = np.array([(h >> i) & 1 for i in range(64)])
        v += np.where(bits == 1, 1, -1)
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return f"{out:016x}"


def translit_key(name: Any) -> str:
    """Loose Latin key so spelling variants meet: Purasaiwalkam ~ Purasawalkam, Sembiam ~ Sembium."""
    s = normalize(name).replace(" ", "")
    for a, b in (("zh", "l"), ("th", "t"), ("dh", "d"), ("aa", "a"), ("ee", "i"), ("oo", "u"), ("w", "v"),
                 ("ai", "a"), ("iu", "u"), ("ia", "a"), ("y", "i"), ("kk", "k"), ("pp", "p"), ("tt", "t"), ("mm", "m")):
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s)
