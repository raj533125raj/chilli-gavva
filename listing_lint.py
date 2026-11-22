#!/usr/bin/env python3
"""Lint a hand-upload pack's listing.txt before the owner publishes it.

Offline check of one upload pack folder (or all packs with --all):
title length, description length, tag count/length/duplicates, money amounts,
store brand names, shouting caps words, and a What you get deliverables list.
PASS/WARN/FAIL lines are printed; exit 1 when any single check fails.
Prices are the owner's decision anywhere public, so money amounts and store
brand names are hard failures, never suggestions.
"""
import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ALL_PACKS_ROOT = REPO_ROOT / "exported_content" / "products" / "upload"

TITLE_MIN, TITLE_MAX = 20, 100
DESC_MIN, DESC_MAX = 300, 3000
TAG_TARGET, TAG_MAX_LEN = 13, 20
WHAT_GET_MIN_LINES = 3

CAPS_ALLOWED = {"PDF", "A4", "US"}
_MONEY = re.compile(
    r"(?:[$₹]\s*\d|\bRs\.?\s*\d|\bINR\s*\d|\bUSD\s*\d"
    r"|\d\s*[$₹]|\d\s*Rs\.?(?!\w)|\d\s*\bINR\b|\d\s*\bUSD\b)")
_BRAND = re.compile(r"\b(?:Etsy|Amazon|Canva)\b", re.I)
_NOTION = re.compile(r"\bNotion\b", re.I)
_CAPS = re.compile(r"\b[A-Z]{5,}\b")

_SECTIONS = (
    ("title", re.compile(r"^Title:?\s*([^\n]*)$", re.I)),
    ("description", re.compile(r"^Description:?\s*([^\n]*)$", re.I)),
    ("tags", re.compile(r"^Tags:?\s*([^\n]*)$", re.I)),
    ("get", re.compile(r"^(?:What you get|What's included|What's inside|Included files):?\s*([^\n]*)$", re.I)),
)


def parse_listing(text):
    """Split listing.txt into title, description, tags and What-you-get lines.

    Returns {title, description, tags, get} where description/get/tags include
    the inline text after the header plus the section lines underneath it.
    """
    found = {"title": "", "description": "", "tags": "", "get": ""}
    for key, _pattern in _SECTIONS:
        found[key] = {"inline": "", "lines": []}
    current = None
    for raw in str(text).replace("’", "'").splitlines():
        line = raw.strip()
        for key, pattern in _SECTIONS:
            match = pattern.match(line)
            if match:
                current = key
                found[key]["inline"] = match.group(1).strip()
                break
        else:
            if current:
                found[current]["lines"].append(line)
    return found


def lint_text(text, name="listing text"):
    """Run every check over one listing text; returns (check, level, message) tuples."""
    results = []
    missing = []

    def add(check, level, message):
        results.append((check, level, message))

    s = parse_listing(text)
    title = s["title"]["inline"]
    if not s["title"]["inline"] and not s["title"]["lines"] and not title:
        add("title", "FAIL", f"{name}: Title line missing")
    elif len(title) < TITLE_MIN or len(title) > TITLE_MAX:
        add("title", "FAIL", f"title {len(title)} chars; must be {TITLE_MIN}-{TITLE_MAX}")
    else:
        add("title", "PASS", f"title {len(title)} chars")

    has_description = s["description"]["inline"] or s["description"]["lines"]
    description = ("\n".join([s["description"]["inline"]] + s["description"]["lines"])
                   if s["description"]["inline"] else "\n".join(s["description"]["lines"])).strip()
    if not description:
        add("description", "FAIL", f"{name}: Description section missing")
    elif len(description) < DESC_MIN or len(description) > DESC_MAX:
        add("description", "FAIL", f"description {len(description)} chars; must be {DESC_MIN}-{DESC_MAX}")
    else:
        add("description", "PASS", f"description {len(description)} chars")

    tags = []
    tracker = {"seen": set(), "dups": []}
    for tag in _split_tags(s["tags"]):
        folded = tag.casefold()
        if folded in tracker["seen"]:
            tracker["dups"].append(tag)
            continue
        tracker["seen"].add(folded)
        tags.append(tag)
    if tracker["dups"]:
        add("tag duplicates", "FAIL", "duplicate tags (case-insensitive): "
            + ", ".join(tracker["dups"][:3]))
    else:
        add("tag duplicates", "PASS", "no duplicate tags")
    if len(tags) != TAG_TARGET:
        add("tags", "FAIL", f"{len(tags)} tags; must be exactly {TAG_TARGET}")
    else:
        add("tags", "PASS", f"{len(tags)} tags")
    too_long = [t for t in tags if len(t) > TAG_MAX_LEN]
    if too_long:
        add("tag length", "FAIL", f"tag longer than {TAG_MAX_LEN} chars: " + ", ".join(too_long[:3]))
    else:
        add("tag length", "PASS", f"every tag <= {TAG_MAX_LEN} chars")

    has_get = s["get"]["inline"] or s["get"]["lines"]
    items = [line for line in [s["get"]["inline"]] + s["get"]["lines"] if line]
    if len(items) < WHAT_GET_MIN_LINES:
        add("what you get", "FAIL", f"\"What you get\" needs {WHAT_GET_MIN_LINES}+ lines; found {len(items)}")
    else:
        add("what you get", "PASS", f"{len(items)} deliverable lines")

    body = "\n".join([title, description, ", ".join(tags), "\n".join(items)])
    for result in (_check_money(body, name), _check_brands(body)):
        if result:
            add(*result)
    caps = _check_caps(body)
    if caps:
        add(*caps)
    return results


def _split_tags(section):
    for tag in (t.strip() for line in [section["inline"]] + section["lines"]
                for t in line.split(",")):
        if tag:
            yield tag


def _check_money(text, name):
    if _MONEY.search(text):
        return ("money", "FAIL",
                f"{name}: money amount found ($/Rs/INR/USD with digits); the owner sets the price")
    return None


def _check_brands(text):
    brands = sorted({brand.title() for brand in _BRAND.findall(text)})
    bare_notion = any(not re.search(r"\bfor\s+$", text[max(0, m.start() - 40):m.start()], re.I)
                      for m in _NOTION.finditer(text))
    if brands or bare_notion:
        detail = (brands + (["Notion"] if bare_notion else []))
        return ("brands", "FAIL",
                f"store brand name ({', '.join(detail)}); Notion is allowed only as \"for Notion\"")
    return None


def _check_caps(text):
    words = [w for w in _CAPS.findall(text) if w not in CAPS_ALLOWED]
    if words:
        return ("caps", "WARN", "all-caps words longer than 4 letters (PDF/A4/US exempt): "
                + ", ".join(sorted(set(words))))
    return None


_BRAND_WORDS = {"etsy", "amazon", "canva", "notion"}


def _description_words(description, existing_tags):
    """Extract clean candidate tag words from description: no brands, <= 20 chars, deduped."""
    seen = {t.casefold() for t in existing_tags}
    words = []
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9' -]*", description):
        word = raw.strip(" -'").strip()
        if not word or len(word) > TAG_MAX_LEN or len(word) < 3:
            continue
        folded = word.casefold()
        if folded in _BRAND_WORDS or folded in seen:
            continue
        seen.add(folded)
        words.append(word)
    return words


def suggest_for(text, name="listing text"):
    """Generate a concrete suggestion for each WARN/FAIL check.

    Returns a list of suggestion strings, one per failing or warning check.
    """
    results = lint_text(text, name=name)
    s = parse_listing(text)
    title = s["title"]["inline"]
    tags = list(_split_tags(s["tags"]))
    description = ("\n".join([s["description"]["inline"]] + s["description"]["lines"])
                   if s["description"]["inline"] else "\n".join(s["description"]["lines"])).strip()
    items = [line for line in [s["get"]["inline"]] + s["get"]["lines"] if line]

    suggestions = []
    for check, level, message in results:
        if level == "PASS":
            continue
        if check == "title" and len(title) > TITLE_MAX:
            cut = title[:TITLE_MAX]
            sp = cut.rfind(" ")
            if sp > 0:
                suggestions.append(
                    f"title too long: cut at a word -> '{cut[:sp]}' ({len(cut[:sp])} chars)")
            else:
                suggestions.append(
                    f"title too long: '{cut}' ({len(cut)} chars, single long word)")
        elif check == "tag duplicates":
            if ":" in message:
                dups = message.split(":", 1)[1].strip()
                suggestions.append(f"duplicate tags: remove these repeats -> {dups}")
        elif check == "tags":
            m = re.search(r"(\d+) tags; must be exactly (\d+)", message)
            if m and int(m.group(1)) < int(m.group(2)):
                needed = int(m.group(2)) - int(m.group(1))
                candidates = _description_words(description, tags)[:needed]
                if candidates:
                    suggestions.append(
                        f"fewer than {TAG_TARGET} tags: consider adding -> "
                        + ", ".join(candidates))
                else:
                    suggestions.append(
                        f"fewer than {TAG_TARGET} tags: add more descriptive tags "
                        f"(no brands, each <= {TAG_MAX_LEN} chars)")
        elif check == "caps":
            words_part = message.split(":", 1)[1].strip() if ":" in message else message
            words = [w for w in words_part.split(", ") if w]
            title_case = [w.capitalize() for w in words]
            suggestions.append(
                f"all-caps words: use Title Case -> {', '.join(title_case)}")
        elif check == "what you get":
            suggestions.append(
                'missing "What you get": add at least 3 deliverable lines, e.g.:\n'
                "- A4 PDF\n- US Letter PDF\n- Readme and personal use licence")
    return suggestions


SUGGEST_ROOT = REPO_ROOT / "exported_content" / "products" / "lint"


def write_suggestions(pack_name, suggestions):
    """Write suggestions to exported_content/products/lint/<pack>.suggest.txt.

    Never touches the pack folder itself. Returns the written path.
    """
    SUGGEST_ROOT.mkdir(parents=True, exist_ok=True)
    path = SUGGEST_ROOT / f"{pack_name}.suggest.txt"
    lines = []
    if suggestions:
        lines.append(f"Suggestions for {pack_name}:")
        lines.append("")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"{i}. {s}")
    else:
        lines.append(f"No suggestions for {pack_name} (all checks PASS).")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def lint_folder(pack_folder, name=None):
    """Lint one pack folder's listing.txt -> (results, ok)."""
    pack_folder = Path(pack_folder)
    path = pack_folder / "listing.txt"
    if not path.is_file() and (pack_folder / "listing.md").is_file():
        path = pack_folder / "listing.md"
    label = name or pack_folder.name or str(pack_folder)
    if not path.is_file():
        return [("file", "FAIL", "Pack listing.txt file pettandi.")] + quality_checks("", pack_folder), False
    results = quality_checks(path.read_text(encoding="utf-8-sig"), pack_folder)
    ok = not any(level == "FAIL" for _check, level, _message in results)
    if not ok:
        fixes = [message for _,level,message in results if level != "PASS"]
        for index,(check,level,message) in enumerate(results):
            if level == "FAIL":
                results[index] = (check,level,message + "\nNext: " + "; ".join(fixes[:3]))
                break
    return results, ok


def quality_checks(text, pack_folder=None, main_keyword=None):
    """Gumroad quality gate, with fixed Tenglish guidance that never quotes private input.

    lint_text remains the legacy store-independent thirteen-tag lint API.
    Real upload folders use this stricter Gumroad gate via lint_folder.
    """
    from listing_fields import parse_fields, _check_money
    import secret_guard
    fields = parse_fields(text)
    title = fields["title"]
    tags = [part.strip().lstrip("-*• ") for part in re.split(r"[,\n]", fields["tags"]) if part.strip().lstrip("-*• ")]
    keyword = main_keyword or fields["main_keyword"] or (tags[0] if tags else "")
    tokens = lambda value: re.findall(r"\w+", str(value).casefold())
    first = tokens(title)[:5]
    wanted = tokens(keyword)
    keyword_ok = bool(wanted) and any(first[i:i+len(wanted)] == wanted for i in range(len(first)))
    description = fields["description"]
    body = "\n".join(fields[key] for key in ("description", "what_you_get", "how_to_use", "license", "format", "size"))
    items = [line for line in fields["what_you_get"].splitlines() if re.match(r"\s*(?:[-*•]|\d+[.)])\s+\S", line)]
    brand = re.compile(r"\b(?:etsy|amazon|canva|notion|google|microsoft|excel|gumroad|topmate|payhip|goodnotes|apple|ipad|chatgpt|openai|shopify)\b", re.I)
    cover, previews = [], []
    if pack_folder is not None:
        root = Path(pack_folder).resolve()
        if root.is_dir():
            for path in sorted(root.iterdir()):
                if (not path.is_file() or path.is_symlink() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp")):
                    continue
                if re.match(r"^cover(?:[-_.]|$)", path.name, re.I):
                    cover.append(path.name)
                elif re.match(r"^preview(?:[-_.]|$)", path.name, re.I):
                    previews.append(path.name)
    rules = [
        ("title", 40 <= len(title) <= 80, "Title 40-80 characters lo rayandi."),
        ("keyword", keyword_ok, "Main keyword ni Title modati 5 padallo pettandi; modati Tag lo ade topic ivvandi."),
        ("description", 300 <= len(description) <= 3000, "Description 300-3000 characters lo buyer ki upayogam cheppandi."),
        ("what you get", bool(items), "What you get kinda nijamaina files ni bullet list ga rayandi."),
        ("format", bool(re.search(r"\b(?:PDF|DOCX|XLSX|CSV|SVG|PNG|JPG|JPEG|ZIP|PPTX)\b", body, re.I)),
         "Files format, udaharanaki PDF, Description lo rayandi."),
        ("page count/size", bool(re.search(r"\b\d+\s*(?:pages?|sheets?|slides?|MB|KB)\b|\bA[345]\b|\b(?:US\s+)?Letter\b|\d+\s*[x×]\s*\d+", body, re.I)),
         "Page count leda size (A4 / Letter) Description lo rayandi."),
        ("how to use", bool(fields["how_to_use"] or re.search(r"\bhow to use\b|\binstructions\b|\bdownload\b.{0,160}\b(?:open|print|import)\b", body, re.I | re.S)),
         "How to use steps rayandi: Download, open, print leda import ela cheyalo cheppandi."),
        ("license", bool(fields["license"] or re.search(r"\blicen[cs]e\b|\b(?:personal|commercial) use\b", body, re.I)),
         "License lo personal use leda commercial use rules cheppandi."),
        ("tags", 3 <= len(tags) <= 10, "3-10 Tag words enchukondi."),
        ("tag duplicates", len({tag.casefold() for tag in tags}) == len(tags), "Repeat ayina Tags teeseyandi."),
        ("tag length", all(len(tag) <= TAG_MAX_LEN for tag in tags), "Prathi Tag 20 characters lopala pettandi."),
        ("brands", not brand.search("\n".join((title, body, ", ".join(tags)))), "Brand names teesesi product pani gurinchi rayandi."),
        ("money", _check_money(text) is None, "Public text lo amounts teeseyandi; price owner decide chestaru."),
        ("secrets", not secret_guard.scan_text(text), "Private vivaralu teesesi listing malli check cheyandi."),
    ]
    if pack_folder is not None:
        rules += [("cover", bool(cover), "Pack lo cover.png leda cover image pettandi."),
                  ("previews", len(previews) >= 3, "Pack lo kanisam 3 preview images pettandi.")]
    return [(name, "PASS" if passed else "FAIL", "Check bagundi." if passed else fix)
            for name,passed,fix in rules]


def quality_report(pack_folder, main_keyword=None):
    """Canonical listing score, using exactly sell_ready's existing lint formula."""
    folder = Path(pack_folder)
    source = folder / "listing.txt"
    if not source.is_file() and (folder / "listing.md").is_file():
        source = folder / "listing.md"
    if source.is_file():
        results = quality_checks(source.read_text(encoding="utf-8-sig"), folder, main_keyword)
    else:
        results = [("file", "FAIL", "Pack listing.txt file pettandi.")] + quality_checks("", folder)
    fixes = [message for _,level,message in results if level != "PASS"]
    score = max(0, 100 - 20 * sum(level == "FAIL" for _,level,_ in results)
                - 5 * sum(level == "WARN" for _,level,_ in results))
    next_line = "Next: " + ("; ".join(fixes[:3]) if fixes else "Listing chusi Store lo meere upload cheyandi.")
    return {"score": score, "ok": not fixes, "checks": results, "fixes": fixes,
            "top_fixes": fixes[:3], "next": next_line,
            "tenglish_summary": f"Listing score {score}/100.\n{next_line}"}


def lint_all(packs_root=None):
    """Lint every pack folder with a listing.txt under packs_root -> [(label, results, ok)]."""
    root = Path(packs_root) if packs_root else ALL_PACKS_ROOT
    if not root.is_dir():
        return [(str(root), [("file", "FAIL", f"packs root not found: {root}")], False)]
    out = []
    for pack in sorted(p for p in root.iterdir() if (p / "listing.txt").is_file()):
        results, ok = lint_folder(pack)
        out.append((pack.name, results, ok))
    if not out:
        out.append((str(root), [("file", "FAIL", f"no listing.txt packs found under {root}")], False))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", nargs="?", help="upload pack folder containing listing.txt")
    parser.add_argument("--all", action="store_true",
                        help=f"lint every pack under {ALL_PACKS_ROOT}")
    parser.add_argument("--packs-root", help="override the --all root folder")
    parser.add_argument("--suggest", action="store_true",
                        help="write concrete fix suggestions to exported_content/products/lint/<pack>.suggest.txt")
    args = parser.parse_args(argv)
    if args.all and args.folder:
        parser.error("use either a pack folder or --all, not both")
    any_fail = False
    if args.all:
        for label, results, ok in lint_all(args.packs_root):
            print(f"== {label}")
            for check, level, message in results:
                print(f"{level} {check}: {message}")
            any_fail = any_fail or not ok
            if args.suggest:
                pack_path = Path(args.packs_root or ALL_PACKS_ROOT) / label
                text = (pack_path / "listing.txt").read_text(encoding="utf-8-sig")
                suggestions = suggest_for(text, name=label)
                write_suggestions(label, suggestions)
                for s in suggestions:
                    print(f"  SUGGEST: {s}")
    elif args.folder:
        results, ok = lint_folder(args.folder)
        for check, level, message in results:
            print(f"{level} {check}: {message}")
        any_fail = not ok
        if args.suggest:
            pack_path = Path(args.folder)
            text = (pack_path / "listing.txt").read_text(encoding="utf-8-sig")
            suggestions = suggest_for(text, name=pack_path.name)
            write_suggestions(pack_path.name, suggestions)
            for s in suggestions:
                print(f"  SUGGEST: {s}")
    else:
        parser.error("give an upload pack folder or --all")
    print("FAIL listing lint" if any_fail else "PASS listing lint")
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
