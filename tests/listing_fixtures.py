"""Real gallery fixtures for tests of listing fields and CLI readers."""
from pathlib import Path


def write_gallery(folder):
    from PIL import Image
    folder = Path(folder)
    for name in ("cover.png", "preview-1.png", "preview-2.png", "preview-3.png"):
        Image.new("RGB", (1200, 1600), "white").save(folder / name)


def save_store_link(pack, db_path):
    import guardrails
    import tg_listed
    aid = int(Path(pack).name.split("-", 1)[0])
    conn = guardrails.connect(str(db_path))
    conn.execute("INSERT OR IGNORE INTO approvals (id,ts,kind,title,payload,status) VALUES (?,?,'public_draft',?,?,'approved')",
                 (aid,"2026-11-20T09:00:00+00:00", "Draft: Printable planner", "Planner files"))
    conn.commit()
    conn.close()
    result = tg_listed.set_listed(aid, "https://example.gumroad.com/l/planner", str(db_path))
    assert result.get("ok"), result
    return "https://example.gumroad.com/l/planner"
