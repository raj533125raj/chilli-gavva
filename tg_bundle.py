"""Telegram plugin: tg_bundle.py — CCB-3: "/bundle 3,5,7" = one Gumroad bundle from 2-4 READY packs.

  /bundle 3,5,7     1) every pack must be SELLABLE or ALMOST (sell_ready, which runs the truth check)
                       and all packs must be for the same audience
                    2) the EXISTING bundle_builder.build_bundle makes the folder
                       (bundle.zip with each member's product.zip, combined cover, combined listing, LICENSE)
                    3) the bundle is checked against its own listing: bundle.zip holds exactly the member
                       zips, byte for byte, and the listing names exactly those members, no money
                    4) cover as photo, listing.txt + bundle.zip as documents, Tenglish upload steps
  /bundle, bundle, bundle chey   (no numbers) -> help + bundle ideas from the ready packs

Also: "bundle 3 5", "3,5 bundle chey", "pack 3 5 7 bundle chey".

Rules kept:
  - Plugin interface: COMMANDS + handle(text, ctx). Sends only through ctx callbacks.
  - telegram_inbox is never imported here. bundle_builder.py is used as it is (not rewritten).
  - Nothing is listed, published or sold: the owner uploads by hand. Price: owner decides.
  - No money amounts. Only files inside the bundle folder are sent.
"""
from __future__ import annotations

import hashlib
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent

COMMANDS = [
    {
        "cmd": "/bundle",
        "words": ["bundle", "bundle chey", "bundle cheyi", "bundles", "bundle ideas"],
        "help_en": "Make one Gumroad bundle from 2-4 ready packs of the same audience: /bundle 3,5,7",
        "help_te": "Same audience unna 2-4 ready packs ni okka Gumroad bundle ga cheyandi: /bundle 3,5,7",
    },
]

MIN_PACKS, MAX_PACKS = 2, 4
MAX_FILE_BYTES = 45 * 1024 * 1024        # Telegram bot upload limit is 50 MB
MONEY_RE = re.compile(
    r"(?:[$₹€£]\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:USD|INR|EUR|GBP|dollars?|rupees?|cents?)\b|\bRs\.?\s?\d[\d.,]*)",
    re.IGNORECASE)
_DO = r"(?:\s+(?:chey|cheyi|cheyyi|cheyali|cheyandi|kavali|pettu))?"
_NUMS = r"((?:#?\d{1,9}\s*(?:,|\s|and|&|\+)\s*)+#?\d{1,9})"
_PATTERNS = [
    re.compile(rf"^/bundle(?:@\w+)?\s+(?:packs?\s+)?{_NUMS}$", re.IGNORECASE),
    re.compile(rf"^bundles?\s+(?:packs?\s+)?{_NUMS}{_DO}$", re.IGNORECASE),
    re.compile(rf"^(?:packs?\s+)?{_NUMS}\s+(?:ni\s+|tho\s+)?bundle{_DO}$", re.IGNORECASE),
    re.compile(rf"^bundle\s+(?:chey|cheyi|cheyyi|cheyali|cheyandi)\s+(?:packs?\s+)?{_NUMS}$", re.IGNORECASE),
]
_HELP = re.compile(r"^(?:/bundle(?:@\w+)?|bundles?|bundle\s+(?:chey|cheyi|cheyyi|cheyali|cheyandi|ideas|list))$",
                   re.IGNORECASE)


def clean(text: Any) -> str:
    """No money amounts ever leave this plugin."""
    return MONEY_RE.sub("[amount]", str(text))


def parse(text: Any) -> Optional[Tuple[str, List[int]]]:
    """("build", [ids]) / ("help", []) or None when the text is not for this plugin."""
    if not isinstance(text, str):
        return None
    raw = " ".join(text.strip().split()).rstrip("?.! ").strip()
    if not raw or len(raw) > 80:
        return None
    if _HELP.match(raw):
        return "help", []
    for pattern in _PATTERNS:
        match = pattern.match(raw)
        if match:
            return "build", [int(number) for number in re.findall(r"\d+", match.group(1))]
    if re.match(r"^/bundle(?:@\w+)?\b", raw, re.IGNORECASE):
        return "help", []                  # "/bundle abc" -> show the right format
    return None


# ---- folders -------------------------------------------------------------------------------
def products_dir(ctx: Optional[Dict[str, Any]] = None) -> Path:
    value = (ctx or {}).get("products_dir") or os.environ.get("CHILLI_PRODUCTS_DIR")
    return Path(value).resolve() if value else REPO_ROOT / "exported_content" / "products"


def bundles_dir(ctx: Optional[Dict[str, Any]] = None) -> Path:
    """Private, git-ignored folder (exported_content/products/ is in .gitignore)."""
    return products_dir(ctx) / "bundles"


def marketing_for(pack_dir: Path, ctx: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    try:
        import tg_sell
        return tg_sell.marketing_base(pack_dir, ctx)
    except Exception:
        return None


def ready_packs(ctx: Optional[Dict[str, Any]] = None) -> Dict[int, Path]:
    import sell_ready
    found: Dict[int, Path] = {}
    for number, _title, path in sell_ready.find_all_ready_packs(products_dir(ctx)):
        if number is not None and Path(path).parent.name != "bundles":
            found.setdefault(number, Path(path))
    return found


# ---- checks ----------------------------------------------------------------------------------
def check_members(ids: List[int], ctx: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Every member must exist, be SELLABLE / ALMOST and share one audience. Returns (members, problems)."""
    import bundle_builder
    import sell_ready
    problems: List[str] = []
    unique = list(dict.fromkeys(ids))
    if len(unique) != len(ids):
        problems.append("Okate pack rendu sarlu ichharu — prati pack okkasari matrame.")
    if not MIN_PACKS <= len(unique) <= MAX_PACKS:
        problems.append(f"Bundle lo {MIN_PACKS} nundi {MAX_PACKS} packs undali (meeru {len(unique)} ichharu).")
        return [], problems
    packs = ready_packs(ctx)
    members = []
    for number in unique:
        folder = packs.get(number)
        if folder is None:
            problems.append(f"Pack #{number} ready upload pack dorakaledu (/ready chudandi).")
            continue
        evaluation = sell_ready.evaluate_pack(folder, marketing_for(folder, ctx))
        info = bundle_builder._extract_pack_info(folder)
        members.append({"id": number, "folder": folder, "status": evaluation.get("status"),
                        "title": evaluation.get("title") or info.get("title") or folder.name,
                        "audience": info.get("audience", "general"), "fixes": evaluation.get("fixes") or []})
    for member in members:
        if member["status"] not in (sell_ready.STATUS_SELLABLE, sell_ready.STATUS_ALMOST):
            first = f" Mundu: {member['fixes'][0]}" if member["fixes"] else ""
            problems.append(f"Pack #{member['id']} {member['status']} — bundle lo pettakudadu.{first} (/sell {member['id']})")
    audiences = {member["audience"] for member in members}
    if len(audiences) > 1:
        detail = ", ".join(f"#{member['id']} {member['audience']}" for member in members)
        problems.append(f"Packs okate audience kosam kaavu ({detail}). Same audience packs matrame kalapandi.")
    return members, problems


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_bundle(folder: Path, members: List[Dict[str, Any]]) -> List[str]:
    """The bundle against its own listing: exact member zips inside, every member named, no money,
    nothing else promised. Returns the problems (empty = passed)."""
    problems: List[str] = []
    needed = ("bundle.zip", "listing.txt", "cover.png", "LICENSE.txt")
    for name in needed:
        if not (folder / name).is_file():
            problems.append(f"{name} ledu")
    if problems:
        return problems
    try:
        with zipfile.ZipFile(folder / "bundle.zip") as archive:
            inside = {info.filename: _sha256(archive.read(info)) for info in archive.infolist() if not info.is_dir()}
    except (OSError, zipfile.BadZipFile):
        return ["bundle.zip open avvaledu"]
    expected = [_sha256((member["folder"] / "product.zip").read_bytes()) for member in members]
    if sorted(inside.values()) != sorted(expected):
        problems.append("bundle.zip lo unna files packs product.zip tho match avvaledu")
    if len(inside) != len(members):
        problems.append(f"bundle.zip lo {len(inside)} files unnayi, {len(members)} kavali")
    for name in inside:
        if name.startswith(("/", "\\")) or ".." in name.split("/") or "__MACOSX" in name:
            problems.append(f"bundle.zip lo unsafe path: {name}")
    listing = (folder / "listing.txt").read_text(encoding="utf-8")
    for member in members:
        if str(member["title"]) not in listing:
            problems.append(f"listing lo Pack #{member['id']} ({member['title']}) peru ledu")
    count = re.search(r"\bGet (\d+) complete\b", listing)
    if count and int(count.group(1)) != len(members):
        problems.append(f"listing {count.group(1)} products ani cheptondi, bundle lo {len(members)} unnayi")
    if MONEY_RE.search(listing):
        problems.append("listing lo money amount undi")
    return problems


# ---- replies ------------------------------------------------------------------------------------
def help_text(ctx: Dict[str, Any]) -> str:
    """Format + real bundle ideas (bundle_builder.suggest_bundles over SELLABLE / ALMOST packs)."""
    lines = ["📦 Bundle: same audience unna 2-4 ready packs ni okka Gumroad product ga cheyandi.",
             "Ila pampandi: /bundle 3,5,7", ""]
    try:
        import bundle_builder
        import sell_ready
        packs = ready_packs(ctx)
        good = {number: folder for number, folder in packs.items()
                if sell_ready.evaluate_pack(folder, marketing_for(folder, ctx)).get("status")
                in (sell_ready.STATUS_SELLABLE, sell_ready.STATUS_ALMOST)}
        by_name = {folder.name: number for number, folder in good.items()}
        ideas = []
        for suggestion in bundle_builder.suggest_bundles(list(good.values()))[:3]:
            numbers = [by_name.get(Path(str(member.get("folder") or member.get("pack_id"))).name)
                       for member in suggestion.get("packs", [])]
            if numbers and all(numbers):
                ideas.append("/bundle " + ",".join(str(number) for number in sorted(numbers)))
        if ideas:
            lines.append("Ideas (same audience, ready packs):")
            lines += [f"• {idea}" for idea in ideas]
        elif len(good) >= MIN_PACKS:
            lines.append(f"Ready packs: {', '.join('#' + str(n) for n in sorted(good))}. "
                         "Okate audience unnavi choose chesi pampandi.")
        else:
            lines.append("Bundle ki kanisam 2 ready (SELLABLE / ALMOST) packs kavali. Ippudu: /ready")
    except Exception as error:
        lines.append(f"Ideas chupinchalekapoya ({type(error).__name__}). Format: /bundle 3,5,7")
    lines += ["", "Robot edi list / publish cheyadu. Price: owner decides."]
    return clean("\n".join(lines))


def upload_steps(bundle_folder: Path, members: List[Dict[str, Any]]) -> str:
    numbers = ", ".join(f"#{member['id']}" for member in members)
    steps = [
        "gumroad.com lo login avvandi (login meere cheyali).",
        "'Products' > 'New product' click chesi 'Digital product' select cheyandi.",
        "listing.txt lo unna Title ni Name box lo paste cheyandi.",
        "Price meere type cheyandi (Price: owner decides).",
        "Description box lo listing.txt lo unna Description + What you get paste cheyandi.",
        "'Cover' lo cover.png upload cheyandi.",
        "'Content' lo bundle.zip upload cheyandi.",
        "Tags listing.txt nundi okkokkati add cheyandi.",
        "Preview chusi correct ga unte 'Publish' meere click cheyandi. Robot emi publish cheyadu.",
    ]
    lines = [f"BUNDLE UPLOAD STEPS — packs {numbers}", f"Folder: {bundle_folder.name}", ""]
    lines += [f"[ ] {index}. {step}" for index, step in enumerate(steps, 1)]
    return clean("\n".join(lines))


def _send_file(kind: str, path: Path, caption: str, ctx: Dict[str, Any], problems: List[str]) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        problems.append(f"{path.name}: file chadavalekapoya")
        return False
    if size > MAX_FILE_BYTES:
        problems.append(f"{path.name}: chala pedda file ({size // (1024 * 1024)} MB) — Mac lo bundle folder "
                        "nundi direct ga upload cheyandi")
        return False
    sender = ctx.get("send_photo" if kind == "photo" else "send_document")
    ok = sender(str(path), caption=caption) if callable(sender) else False
    if ok is False and kind == "photo" and callable(ctx.get("send_document")):
        ok = ctx["send_document"](str(path), caption=caption)
    if ok is False:
        problems.append(f"{path.name}: Telegram ki pampadam fail ayyindi")
        return False
    return True


def handle_build(ids: List[int], ctx: Dict[str, Any]) -> Dict[str, Any]:
    import bundle_builder
    members, problems = check_members(ids, ctx)
    if problems:
        return {"text": clean("Bundle cheyaledu:\n" + "\n".join(f"- {problem}" for problem in problems))}
    target = bundles_dir(ctx)
    try:
        folder = Path(bundle_builder.build_bundle([member["folder"] for member in members], out_dir=target))
    except Exception as error:
        return {"text": clean(f"Bundle build fail ayyindi ({type(error).__name__}: {str(error)[:160]}).")}
    folder = folder.resolve()
    if target.resolve() not in folder.parents:
        return {"text": "Bundle folder bundles folder bayata vachindi — pampadam aapesanu."}
    check_problems = verify_bundle(folder, members)
    if check_problems:
        return {"text": clean("Bundle check fail ayyindi — upload cheyakandi:\n"
                              + "\n".join(f"- {problem}" for problem in check_problems))}
    titles = "\n".join(f"{index}. Pack #{member['id']} — {member['title']}" for index, member in enumerate(members, 1))
    head = clean(f"📦 Bundle ready ({len(members)} packs, audience: {members[0]['audience']}). Check pass ✓\n{titles}\n\n"
                 "bundle.zip lo prati pack product.zip unchanged ga undi; listing lo anni packs perlu unnayi.")
    if not callable(ctx.get("send_text")):
        return {"text": head + "\n\n" + upload_steps(folder, members)}
    ctx["send_text"](head)
    send_problems: List[str] = []
    _send_file("photo", folder / "cover.png", "Bundle cover (Gumroad 'Cover' lo upload cheyandi)", ctx, send_problems)
    _send_file("document", folder / "listing.txt", "Bundle listing.txt — Title, Description, Tags copy cheyandi", ctx,
               send_problems)
    _send_file("document", folder / "bundle.zip", "bundle.zip — Gumroad 'Content' lo upload cheyandi", ctx, send_problems)
    text = upload_steps(folder, members)
    if send_problems:
        text += "\n\nGamaninchandi:\n" + "\n".join(f"- {problem}" for problem in send_problems)
    ctx["send_text"](clean(text))
    return {"handled": True}


def handle(text: str, ctx: dict) -> Optional[dict]:
    """Standard Telegram plugin entry point."""
    parsed = parse(text)
    if parsed is None:
        return None
    ctx = ctx if isinstance(ctx, dict) else {}
    action, ids = parsed
    try:
        if action == "build":
            return handle_build(ids, ctx)
        return {"text": help_text(ctx)}
    except Exception as error:
        return {"text": clean(f"/bundle lo problem vachindi ({type(error).__name__}). /doctor tho chudandi.")}
