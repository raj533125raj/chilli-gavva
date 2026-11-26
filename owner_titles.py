"""One word-safe buyer title for every owner-facing product surface."""
from pathlib import Path
import re


def owner_title(product):
    """Prefer the pack's listing profile; hide internal draft/agent/version labels."""
    info = product if isinstance(product,dict) else {'path':product} if isinstance(product,Path) else {'title':product}
    is_product = info.get('kind') not in ('scout_idea','trend_proposal','code_patch') and not str(info.get('kind','')).startswith('imp:')
    raw = str(info.get('title') or info.get('name') or '')
    path = info.get('path') or info.get('pack_dir') or info.get('folder')
    if path:
        path = Path(path)
        if not path.is_absolute():
            import product_builder
            path = Path(product_builder.PRODUCTS_DIR)/path
        candidates=[path/'listing.txt',path/'listing.md',path.parent/'upload'/path.name/'listing.txt']
        if path.parent.name=='marketing':
            candidates += [path.parent.parent/'upload'/path.name/'listing.txt',
                           path.parent.parent/'products'/'upload'/path.name/'listing.txt',
                           path.parent.parent/path.name/'listing.md']
        for candidate in candidates:
            if candidate.is_file() and not candidate.is_symlink():
                import listing_fields
                title = listing_fields.parse_fields(candidate.read_text(encoding='utf-8',errors='replace'))['title']
                if title:
                    raw=title;break
    internal = bool(re.search(r'Draft:|\[[^]]*Agent\]|\(v\d|\(rewritten\)',raw,re.I))
    clean = re.sub(r'^\s*Draft:\s*','',raw,flags=re.I)
    clean = re.sub(r'\[[^]]*(?:Agent|Scout Idea)[^]]*\]\s*','',clean,flags=re.I)
    clean = re.sub(r'\s*\((?:v\d+[^)]*|rewritten)\)','',clean,flags=re.I)
    clean = ' '.join(clean.split()).strip(' -')
    if internal and is_product:
        import product_builder
        kind = product_builder.draft_format(raw,str(info.get('payload') or ''))
        if 'adhd' in raw.lower():kind='adhd_planner'
        if kind:
            from buyer_check import TITLES
            clean = TITLES.get(kind,clean)
            if kind=='sheets' and 'freelanc' in raw.lower():
                clean='Freelancers Work Spreadsheet Organizing Daily Records'
    import secret_guard
    from listing_fields import MONEY_PATTERNS
    clean=secret_guard.scrub_text(clean)
    for pattern in MONEY_PATTERNS:clean=pattern.sub('[amount]',clean)
    if any(len(word)>90 for word in clean.split()):clean='Product Pack'
    words=(clean or 'Product Pack').split()
    if len(words)<6 and is_product:
        words += 'Digital Download For Personal Use'.split()[:6-len(words)]
    chosen=[]
    for word in words[:9]:
        chosen.append(word)
    return ' '.join(chosen)+('…' if len(chosen)<len(words) else '')
