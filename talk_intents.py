"""CC1-3: understand plain Tenglish / English owner text (offline, no AI, no network).

    detect("moodo pack sell chey")  ->  {"intent": "sell", "command": "/sell 3", "numbers": [3],
                                         "confidence": 0.95, "risky": False, "alternatives": [...]}

This module only UNDERSTANDS text. It never runs anything and never builds a reply:
tg_talk.py takes the "command" and runs the same code the real command runs.

How it works
  1. Numbers: digits, Telugu number words okati..iravai (also moodo / moodava style), English
     words one..twenty and first..twentieth -> a list of ints. Number tokens become "NUM".
  2. Every word is mapped to a small set of meaning tokens (SYNONYMS: "pampu", "send", "pampinchu"
     -> SEND; "ee roju", "ivala", "today" -> TODAY ...). One-letter typos of longer words still map.
  3. Each intent lists word-combinations with a confidence ("FILE" + "SEND" = 0.95 ...). The best
     match wins; the next different commands are the alternatives used for suggestions.

Greetings, "menu", "help" and anything starting with "/" are never answered here (tg_menu and the
built-in commands own them).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

RUN_CONFIDENCE = 0.75       # at or above: run the command
SUGGEST_CONFIDENCE = 0.45   # between: offer two suggestions; below: not for us
TYPO_PENALTY = 0.9
MAX_TEXT = 160

# ---- numbers -----------------------------------------------------------------
TELUGU_NUMBERS = {
    1: ("okati", "okato", "okatava", "modati", "modato", "modatidi"),
    2: ("rendu", "rendo", "rendava", "rendodi"),
    3: ("moodu", "mudu", "moodo", "mudo", "moodava", "mudava", "moododi"),
    4: ("nalugu", "naalugu", "nalugo", "naalugo", "nalugava", "naalugava"),
    5: ("aidu", "ayidu", "aido", "ayido", "aidava", "ayidava"),
    6: ("aaru", "aaro", "aarava"),
    7: ("edu", "yedu", "edo", "yedo", "edava", "yedava"),
    8: ("enimidi", "enimido", "enimidava"),
    9: ("tommidi", "thommidi", "tommido", "thommido", "tommidava"),
    10: ("padi", "pado", "padava"),
    11: ("padakondu", "padakondo", "padakondava"),
    12: ("pannendu", "pannendo", "pannendava"),
    13: ("padamoodu", "padamudu", "padamoodo", "padamoodava"),
    14: ("padnalugu", "padnaalugu", "padnalugo", "padnalugava"),
    15: ("padihenu", "padiheno", "padihenava"),
    16: ("padaharu", "padahaaru", "padaharo", "padaharava"),
    17: ("padihedu", "padihedo", "padihedava"),
    18: ("paddenimidi", "paddenimido", "paddenimidava"),
    19: ("pandommidi", "pandommido", "pandommidava"),
    20: ("iravai", "iravayi", "iravayyo", "iravaiyo", "iravayyava"),
}
ENGLISH_NUMBERS = {
    1: ("one", "first"), 2: ("two", "second"), 3: ("three", "third"), 4: ("four", "fourth"),
    5: ("five", "fifth"), 6: ("six", "sixth"), 7: ("seven", "seventh"), 8: ("eight", "eighth"),
    9: ("nine", "ninth"), 10: ("ten", "tenth"), 11: ("eleven", "eleventh"), 12: ("twelve", "twelfth"),
    13: ("thirteen", "thirteenth"), 14: ("fourteen", "fourteenth"), 15: ("fifteen", "fifteenth"),
    16: ("sixteen", "sixteenth"), 17: ("seventeen", "seventeenth"), 18: ("eighteen", "eighteenth"),
    19: ("nineteen", "nineteenth"), 20: ("twenty", "twentieth"),
}
NUMBER_WORDS: Dict[str, int] = {}
for _value, _words in list(TELUGU_NUMBERS.items()) + list(ENGLISH_NUMBERS.items()):
    for _word in _words:
        NUMBER_WORDS[_word] = _value
# Words that are also everyday words ("edo" = something, "one" = a). "oka" (a) is never a number.
# They count as a number only right next to pack / draft / post.
AMBIGUOUS_NUMBER_WORDS = {"edo", "yedo", "one", "padi", "pado"}
NUMBER_NOUNS = {"pack", "packs", "draft", "drafts", "post", "posts", "number", "no", "id", "product"}
_DIGITS = re.compile(r"^#?(\d{1,6})(?:st|nd|rd|th|va|o)?$")


def _words(text: Any) -> List[str]:
    lowered = str(text or "").lower().replace("’", "'")
    lowered = re.sub(r"[^\w\s#/'-]", " ", lowered)
    lowered = lowered.replace("-", " ").replace("'", "")
    return [word for word in lowered.split() if word]


def extract_numbers(text: Any) -> List[int]:
    """All numbers in the text, in order: digits, Telugu words (okati..iravai), English words."""
    return _numbers_and_words(_with_phrases(_words(text)))[0]


def _with_phrases(words: List[str]) -> List[str]:
    """Replace known multi-word spellings ("ee roju", "one by one") by their meaning tokens."""
    joined = " " + " ".join(words) + " "
    for phrase, replacement in PHRASES:
        joined = joined.replace(" " + phrase + " ", " " + replacement + " ")
    return joined.split()


def _numbers_and_words(words: List[str]) -> Tuple[List[int], List[str]]:
    numbers: List[int] = []
    rest: List[str] = []
    for index, word in enumerate(words):
        match = _DIGITS.match(word)
        if match:
            numbers.append(int(match.group(1)))
            rest.append("NUM")
            continue
        if word in NUMBER_WORDS:
            near = {words[index - 1] if index else "", words[index + 1] if index + 1 < len(words) else ""}
            if word not in AMBIGUOUS_NUMBER_WORDS or near & NUMBER_NOUNS:
                numbers.append(NUMBER_WORDS[word])
                rest.append("NUM")
                continue
        rest.append(word)
    return numbers, rest


# ---- words -> meaning tokens ---------------------------------------------------
PHRASES = [   # multi-word spellings, replaced before single words (longest first at import time)
    ("ee roju", "TODAY"), ("ee rojuki", "TODAY"), ("ee vaaram", "WEEK"), ("ee varam", "WEEK"), ("ee week", "WEEK"),
    ("this week", "WEEK"), ("ee nela", "MONTH"), ("this month", "MONTH"), ("how many", "HOWMANY"),
    ("how much", "HOWMANY"), ("ela undi", "HOW"), ("ela unnav", "HOW"), ("ela unnadi", "HOW"),
    ("ela unnayi", "HOW"), ("ela nadustundi", "HOW"), ("ela nadustondi", "HOW"), ("ela nadustunnayi", "HOW"),
    ("em jarigindi", "WHAT HAPPENED"), ("auto approve", "AUTOAPPROVE"), ("last run", "LOG"),
    ("one by one", "ONEBYONE"), ("okkokkati ga", "ONEBYONE"), ("landing page", "LANDING"),
    ("store kit", "KIT"), ("listing kit", "KIT"), ("what next", "WHAT NEXT"), ("next enti", "WHAT NEXT"),
    ("next em", "WHAT NEXT"), ("to do", "DO"), ("change log", "VERSION"), ("ab test", "ABTEST"),
    ("health check", "HEALTH"), ("quiet hours", "SETTING"), ("good morning", "GREETING"),
    ("good evening", "GREETING"), ("good night", "GREETING"), ("em chestunnav", "GREETING"),
    ("thank you", "GREETING"), ("ela cheyali", "HELP"),
]
PHRASES.sort(key=lambda item: -len(item[0]))

_SYNONYM_GROUPS = {
    "SHOW": "chupinchu chupi chupinchandi choopinchu chupettu chupistava chupiyi chudu choodu chudali chudandi "
            "choodali show display view kanipinchu cheppu cheppandi cheppava tell ivvu ivvandi give see "
            "check telusukovali telupu kavali kaavali want need",
    "SEND": "pampu pampinchu pampandi pampava pampi pampiyi pampistava send share forward pampali",
    "HOWMANY": "entha enni yenni yentha ennunnayi count total mottam motham",
    "WHAT": "em emi enti yenti emiti what edi evi which emunnayi emundi",
    "HOW": "ela yela how",
    "TODAY": "eeroju ivala ivvala ivaala today nedu eeroj",
    "TOMORROW": "repu tomorrow repati",
    "WEEK": "vaaram varam week weekly eevaaram vaaraniki",
    "MONTH": "nela month monthly",
    "HAPPENED": "jarigindi jarigay jarigayi jarigina ayyindi happened jarigindo",
    "DID": "chesav chesavu chesindi chesaru did",
    "DRAFT": "draft drafts draftlu",
    "PENDING": "pending waiting migilina aagina approval",
    "PACK": "pack packs packlu product products",
    "FILE": "file files filelu",
    "SELL": "sell ammu ammali ammadam ammandi selling",
    "GUMROAD": "gumroad",
    "DO": "chey cheyi cheyyi cheyali cheyyali cheddam cheseddam cheyandi cheyadaniki cheyyadaniki "
          "cheyalsindi cheyalsina",
    "NOW": "ippudu ipudu now ippude",
    "NEXT": "next taruvata tarvata tarwata",
    "STEP": "step pani work",
    "STATUS": "status paristhithi sthithi situation",
    "ROBOT": "robot bot system agents agent",
    "PROBLEM": "problem problems error errors samasya samasyalu issue issues fail failed failure failures "
               "tappu tappulu doctor",
    "POST": "post posts postlu posting",
    "DONE": "chesanu chesa chesesanu ayipoyindi ayyipoyindi done finished complete completed posted",
    "READY": "ready sellable siddham",
    "SALES": "sales ammakalu ammakam orders",
    "MONEY": "dabbu dabbulu money earnings sampadana income revenue",
    "CAME": "vachindi vachayi vachaya vachinda came",
    "REPORT": "report summary saramsham vivaralu details detail review",
    "LIST": "list jabita",
    "COVER": "cover covers",
    "PIN": "pin pins pinlu pinterest",
    "CAROUSEL": "carousel slides slide",
    "REEL": "reel reels",
    "BLOG": "blog",
    "LANDING": "landing",
    "LAUNCH": "launch",
    "KIT": "kit kits",
    "MARKETING": "marketing mkt",
    "PIPELINE": "pipeline",
    "PROGRESS": "progress",
    "HEALTH": "health arogyam",
    "LOG": "log logs",
    "IDEA": "idea ideas ideaalu alochana alochanalu",
    "KEYWORD": "keyword keywords",
    "SEO": "seo",
    "LINT": "lint",
    "FAQ": "faq faqs",
    "VERSION": "version versions changelog",
    "DUP": "dup duplicate duplicates",
    "BOARD": "board boards",
    "CALENDAR": "calendar ics",
    "OPS": "ops operations",
    "DASHBOARD": "dashboard",
    "SETTING": "setting settings setup configuration",
    "AUTOAPPROVE": "autoapprove",
    "ASSET": "asset assets logo logos",
    "OFFICE": "office",
    "DECIDE": "decide nirnayinchi",
    "ONEBYONE": "okkokkati okkokati",
    "RULE": "rule rules",
    "CUBE": "cube cubes",
    "APPROVE": "approve accept aamodinchu",
    "REJECT": "reject vaddu tirooskarinchu",
    "PAUSE": "pause aapu aapeyi aapesey",
    "RESUME": "resume modalupettu",
    "GREETING": "hi hii hiii hello hey hai namaste namaskaram gm thanks thankyou ok okay super",
    "MENU": "menu options commands",
    "HELP": "help sahayam",
}
SYNONYMS: Dict[str, str] = {}
for _token, _spellings in _SYNONYM_GROUPS.items():
    for _spelling in _spellings.split():
        SYNONYMS.setdefault(_spelling, _token)
TOKENS = set(_SYNONYM_GROUPS) | {"NUM", "ABTEST"}
_TYPO_WORDS = sorted(word for word in SYNONYMS if len(word) >= 5)


def _one_edit_apart(left: str, right: str) -> bool:
    """True when the two words differ by one changed, missing, extra or swapped letter."""
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        diffs = [i for i in range(len(left)) if left[i] != right[i]]
        if len(diffs) == 1:
            return True
        return (len(diffs) == 2 and diffs[1] == diffs[0] + 1
                and left[diffs[0]] == right[diffs[1]] and left[diffs[1]] == right[diffs[0]])
    short, long_ = (left, right) if len(left) < len(right) else (right, left)
    for index in range(len(long_)):
        if long_[:index] + long_[index + 1:] == short:
            return True
    return False


def tokens_of(text: Any) -> Tuple[set, List[int], bool]:
    """(meaning tokens, numbers, used_typo_fix) for a text."""
    numbers, words = _numbers_and_words(_with_phrases(_words(text)))
    found, typo = set(), False
    for word in words:
        if word in TOKENS:
            found.add(word)
        elif word in SYNONYMS:
            found.add(SYNONYMS[word])
        elif len(word) >= 5:
            near = next((known for known in _TYPO_WORDS if _one_edit_apart(word, known)), None)
            if near:
                found.add(SYNONYMS[near])
                typo = True
    return found, numbers, typo


# ---- intents ---------------------------------------------------------------------
def _asset(token: str) -> List[Tuple[Tuple[str, ...], float]]:
    # The word alone ("carousel", "landing page") runs the command too: the plugin then asks for the pack number.
    return [((token, "SEND"), 0.95), ((token, "NUM"), 0.92), ((token, "PACK"), 0.9), ((token, "SHOW"), 0.9),
            ((token,), 0.8)]


# name, command template, patterns [(tokens that must all be present, confidence)], help line
INTENTS: List[Dict[str, Any]] = [
    {"name": "daily", "command": "daily", "help": "Ee roju em jarigindo report",
     "patterns": [(("TODAY", "HAPPENED"), 0.95), (("TODAY", "WHAT", "DID"), 0.92), (("TODAY", "REPORT"), 0.9),
                  (("WHAT", "HAPPENED"), 0.85), (("TODAY", "STEP", "WHAT"), 0.85), (("WHAT", "DID"), 0.8)]},
    {"name": "week", "command": "/week", "help": "Ee vaaram summary",
     "patterns": [(("WEEK", "HAPPENED"), 0.96), (("WEEK", "HOW"), 0.92), (("WEEK", "REPORT"), 0.92),
                  (("WEEK", "WHAT"), 0.85), (("WEEK",), 0.6)]},
    {"name": "list", "command": "/list", "help": "Approval kosam wait chestunna drafts",
     "patterns": [(("DRAFT", "PENDING"), 0.95), (("DRAFT", "HOWMANY"), 0.95), (("DRAFT", "LIST"), 0.92),
                  (("DRAFT", "SHOW"), 0.85), (("PENDING", "HOWMANY"), 0.85), (("PENDING", "WHAT"), 0.82),
                  (("PENDING", "SHOW"), 0.82), (("DRAFT", "WHAT"), 0.8), (("DRAFT",), 0.6), (("PENDING",), 0.6)]},
    {"name": "view", "command": "/view {n}", "help": "Draft motham chadavadaniki",
     "patterns": [(("DRAFT", "NUM", "SHOW"), 0.96), (("DRAFT", "NUM", "REPORT"), 0.92),
                  (("DRAFT", "NUM", "WHAT"), 0.88), (("DRAFT", "NUM"), 0.7)]},
    {"name": "decide", "command": "/decide", "help": "Drafts okkokkati ga decide cheyadaniki",
     "patterns": [(("DECIDE",), 0.9), (("DRAFT", "ONEBYONE"), 0.97)]},
    {"name": "packs", "command": "/packs", "help": "Ready upload packs list",
     "patterns": [(("PACK", "HOWMANY"), 0.9), (("PACK", "LIST"), 0.9), (("PACK", "SHOW"), 0.8),
                  (("PACK", "WHAT"), 0.78)]},
    {"name": "pack", "command": "/pack {n}", "help": "Pack details",
     "patterns": [(("PACK", "NUM", "REPORT"), 0.95), (("PACK", "NUM", "SHOW"), 0.9), (("PACK", "NUM", "WHAT"), 0.85),
                  (("PACK", "NUM"), 0.6)]},
    {"name": "files", "command": "/files {n}", "help": "Pack files pampistundi",
     "patterns": _asset("FILE")},
    {"name": "covers", "command": "/covers {n}", "help": "Cover variants pampistundi", "patterns": _asset("COVER")},
    {"name": "pins", "command": "/pins {n}", "help": "Pins pampistundi", "patterns": _asset("PIN")},
    {"name": "carousel", "command": "/carousel {n}", "help": "Carousel slides pampistundi",
     "patterns": _asset("CAROUSEL")},
    {"name": "reel", "command": "/reel {n}", "help": "Reel frames pampistundi", "patterns": _asset("REEL")},
    {"name": "blog", "command": "/blog {n}", "help": "Blog post pampistundi", "patterns": _asset("BLOG")},
    {"name": "landing", "command": "/landing {n}", "help": "Landing page pampistundi", "patterns": _asset("LANDING")},
    {"name": "launch", "command": "/launch {n}", "help": "Launch checklist pampistundi", "patterns": _asset("LAUNCH")},
    {"name": "kit", "command": "/kit {n}", "help": "Store listing kit pampistundi", "patterns": _asset("KIT")},
    {"name": "marketing", "command": "/marketing {n}", "help": "Pack marketing files list",
     "patterns": [(("MARKETING", "FILE"), 0.97), (("MARKETING", "NUM"), 0.92), (("MARKETING", "SHOW"), 0.92),
                  (("MARKETING", "PACK"), 0.92), (("MARKETING",), 0.72)]},
    {"name": "sell", "command": "/sell {n}", "help": "Gumroad lo pettadaniki anni okesari",
     "patterns": [(("SELL", "WHAT", "DO"), 0.98), (("SELL", "READY", "WHAT"), 0.97), (("SELL", "NUM"), 0.96),
                  (("GUMROAD", "NUM"), 0.96), (("GUMROAD", "WHAT"), 0.9),
                  (("SELL", "DO"), 0.92), (("SELL", "PACK"), 0.92), (("SELL", "WHAT"), 0.9), (("GUMROAD",), 0.85),
                  (("SELL",), 0.8)]},
    {"name": "ready", "command": "/ready {n}", "help": "Packs sell ki ready aa kaada",
     "patterns": [(("READY", "NUM"), 0.93), (("READY", "PACK"), 0.93), (("READY", "HOWMANY"), 0.93),
                  (("READY", "WHAT"), 0.86), (("READY", "LIST"), 0.9), (("READY",), 0.65)]},
    {"name": "sales", "command": "/sales {period}", "help": "Sales summary",
     "patterns": [(("SALES", "TODAY"), 0.96), (("SALES", "WEEK"), 0.97), (("SALES", "MONTH"), 0.96),
                  (("SALES",), 0.9), (("MONEY", "HOWMANY"), 0.88), (("MONEY", "CAME"), 0.88), (("MONEY",), 0.7)]},
    {"name": "today", "command": "/today", "help": "Ee roju post cheyalsina content",
     "patterns": [(("TODAY", "POST"), 0.96), (("POST", "WHAT"), 0.88), (("POST", "SHOW"), 0.8)]},
    {"name": "post_tomorrow", "command": "/post tomorrow", "help": "Repati posts preview",
     "patterns": [(("TOMORROW", "POST"), 0.96)]},
    {"name": "post_week", "command": "/post week", "help": "Ee vaaram posts list",
     "patterns": [(("WEEK", "POST"), 0.98)]},
    {"name": "posted", "command": "/posted {n}", "help": "Post complete ayyindi ani mark",
     "patterns": [(("POST", "DONE", "NUM"), 0.99), (("POST", "DONE"), 0.9)]},
    {"name": "status", "command": "/status", "help": "System status",
     "patterns": [(("STATUS",), 0.92), (("ROBOT", "HOW"), 0.92), (("ROBOT", "WHAT", "DO"), 0.8),
                  (("ROBOT", "STEP"), 0.8)]},
    {"name": "pipeline", "command": "/pipeline", "help": "Pipeline progress",
     "patterns": [(("PIPELINE",), 0.92), (("PROGRESS",), 0.85)]},
    {"name": "doctor", "command": "/doctor", "help": "Problems / doctor report",
     "patterns": [(("PROBLEM", "WHAT"), 0.92), (("PROBLEM",), 0.85)]},
    {"name": "health", "command": "/health", "help": "System health check", "patterns": [(("HEALTH",), 0.93)]},
    {"name": "logs", "command": "/logs", "help": "Last run summary", "patterns": [(("LOG",), 0.9)]},
    {"name": "ideas", "command": "/ideas", "help": "Scout ideas scores", "patterns": [(("IDEA",), 0.9)]},
    {"name": "keywords", "command": "/keywords", "help": "Top keywords", "patterns": [(("KEYWORD",), 0.9)]},
    {"name": "seo", "command": "/seo {n}", "help": "Pack SEO score", "patterns": [(("SEO",), 0.93)]},
    {"name": "lint", "command": "/lint {n}", "help": "Listing rules check", "patterns": [(("LINT",), 0.93)]},
    {"name": "faq", "command": "/faq {n}", "help": "Buyer FAQ", "patterns": [(("FAQ",), 0.93)]},
    {"name": "versions", "command": "/versions {n}", "help": "Pack version / changes",
     "patterns": [(("VERSION",), 0.93)]},
    {"name": "dup", "command": "/dup", "help": "Duplicate content check", "patterns": [(("DUP",), 0.9)]},
    {"name": "boards", "command": "/boards", "help": "Pinterest board plan", "patterns": [(("BOARD",), 0.93)]},
    {"name": "calendar", "command": "/calendar", "help": "Post plan calendar file",
     "patterns": [(("CALENDAR",), 0.93)]},
    {"name": "ops", "command": "/ops", "help": "Weekly operations summary", "patterns": [(("OPS",), 0.9)]},
    {"name": "dashboard", "command": "/dashboard", "help": "Ops dashboard file",
     "patterns": [(("DASHBOARD",), 0.94)]},
    {"name": "settings", "command": "/settings", "help": "Robot settings", "patterns": [(("SETTING",), 0.9)]},
    {"name": "autoapprove", "command": "/autoapprove", "help": "Auto-approve settings",
     "patterns": [(("AUTOAPPROVE",), 0.95)]},
    {"name": "assets", "command": "/assets", "help": "Saved logos / assets", "patterns": [(("ASSET",), 0.9)]},
    {"name": "office", "command": "/office", "help": "Office layout", "patterns": [(("OFFICE",), 0.9)]},
    {"name": "rules", "command": "/rules", "help": "Learned rules", "patterns": [(("RULE",), 0.9)]},
    {"name": "cubes", "command": "/cubes", "help": "Cubes on/off list", "patterns": [(("CUBE",), 0.9)]},
    {"name": "next", "command": "next", "help": "Ippudu em cheyali",
     "patterns": [(("WHAT", "DO", "NOW"), 0.97), (("WHAT", "NEXT"), 0.93), (("NEXT", "STEP"), 0.93),
                  (("NOW", "WHAT"), 0.88), (("WHAT", "DO"), 0.86), (("NEXT", "DO"), 0.86)]},
    # Risky: never run from plain talk, only offered as an exact command to tap.
    {"name": "approve", "command": "/approve {n}", "help": "Draft approve cheyadaniki", "risky": True,
     "patterns": [(("APPROVE",), 0.9)]},
    {"name": "reject", "command": "/reject {n}", "help": "Draft reject cheyadaniki", "risky": True,
     "patterns": [(("REJECT",), 0.9)]},
    {"name": "pause", "command": "/pause", "help": "Robot ni aapadaniki", "risky": True,
     "patterns": [(("PAUSE",), 0.9)]},
    {"name": "resume", "command": "/resume", "help": "Robot ni malli start cheyadaniki", "risky": True,
     "patterns": [(("RESUME",), 0.9)]},
]
INTENT_BY_NAME = {intent["name"]: intent for intent in INTENTS}
_NOT_OURS = {"GREETING", "MENU", "HELP"}


def command_for(intent: Dict[str, Any], tokens: set, numbers: List[int]) -> str:
    """Fill the command template with the first number / the sales period."""
    command = intent["command"]
    if "{n}" in command:
        command = command.replace("{n}", str(numbers[0]) if numbers else "").strip()
    if "{period}" in command:
        period = "today" if "TODAY" in tokens else "week" if "WEEK" in tokens else "month" if "MONTH" in tokens else ""
        command = command.replace("{period}", period).strip()
    return command


def _score(intent: Dict[str, Any], tokens: set) -> Tuple[float, int]:
    best, size = 0.0, 0
    for needed, confidence in intent["patterns"]:
        if all(token in tokens for token in needed) and (confidence, len(needed)) > (best, size):
            best, size = confidence, len(needed)
    return best, size


def detect(text: Any) -> Optional[Dict[str, Any]]:
    """Understand one owner message. None = not something this module should answer."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw or raw.startswith("/") or len(raw) > MAX_TEXT:
        return None
    tokens, numbers, typo = tokens_of(raw)
    if not tokens or tokens & _NOT_OURS and not (tokens - _NOT_OURS - {"NUM"}):
        return None
    if tokens & {"MENU", "HELP"}:
        return None            # "help", "menu", "ela cheyali ..." stay with tg_menu
    if numbers:
        tokens.add("NUM")
    ranked = []
    for order, intent in enumerate(INTENTS):
        confidence, size = _score(intent, tokens)
        if confidence <= 0:
            continue
        if typo:
            confidence *= TYPO_PENALTY
        ranked.append((round(confidence, 3), size, -order, intent))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[:3], reverse=True)
    confidence, _size, _order, intent = ranked[0]
    if confidence < SUGGEST_CONFIDENCE:
        return None
    command = command_for(intent, tokens, numbers)
    alternatives, seen = [], {command}
    for other_confidence, _s, _o, other in ranked[1:]:
        other_command = command_for(other, tokens, numbers)
        if other_command in seen or other_confidence < SUGGEST_CONFIDENCE:
            continue
        seen.add(other_command)
        alternatives.append({"intent": other["name"], "command": other_command, "confidence": other_confidence,
                             "help": other["help"], "risky": bool(other.get("risky"))})
    return {"intent": intent["name"], "command": command, "numbers": numbers, "confidence": confidence,
            "risky": bool(intent.get("risky")), "help": intent["help"], "typo": typo,
            "alternatives": alternatives[:3], "tokens": sorted(tokens)}


def should_run(found: Optional[Dict[str, Any]]) -> bool:
    """True when the understanding is confident enough (and safe) to run without asking."""
    return bool(found) and not found["risky"] and found["confidence"] >= RUN_CONFIDENCE


def suggestions(found: Dict[str, Any]) -> List[Dict[str, str]]:
    """Exactly two commands to offer when unsure: the best guess and the next best (or /menu)."""
    options = [{"command": found["command"], "help": found["help"]}]
    for other in found.get("alternatives", []):
        if not other.get("risky") or found.get("risky"):
            options.append({"command": other["command"], "help": other["help"]})
            break
    if len(options) < 2:
        fallback = {"approve": "/list", "reject": "/list", "pause": "/status", "resume": "/status"}.get(
            found["intent"], "/menu")
        options.append({"command": fallback, "help": {"/list": "Waiting drafts chupisthundi",
                                                      "/status": "System status",
                                                      "/menu": "Anni commands"}[fallback]})
    return options[:2]
