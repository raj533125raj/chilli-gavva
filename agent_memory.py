"""Agent memory: facts agents learn across runs, stored in SQLite.

Inspired by Mem0 concept — agents build up a persistent knowledge base:
- What content topics performed well (approved vs rejected)
- What prompt approaches worked
- What keywords/angles to avoid
- Arbitrary key-value facts agents want to remember

All writes go through `add_fact()` with a source agent name and confidence.
Facts are retrieved by keyword search or tag.

CLI:
  python3 agent_memory.py           # list recent facts
  python3 agent_memory.py search <term>
  python3 agent_memory.py add <agent> <tag> <fact>
"""

import datetime
import json
import os
import sqlite3

import guardrails

# Share the safe default used by the rest of the workforce, including tests.
DB_DEFAULT = guardrails.DB_PATH


def connect(db_path=None):
    conn = sqlite3.connect(db_path or DB_DEFAULT)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_memory (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL,
            agent     TEXT NOT NULL,
            tag       TEXT NOT NULL,
            fact      TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            reinforced INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_tag ON agent_memory(tag)")
    conn.commit()
    return conn


def add_fact(agent: str, tag: str, fact: str, confidence: float = 1.0, db_path=None) -> int:
    """Store a fact. Returns the new row ID."""
    conn = connect(db_path)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    # Check if near-duplicate exists (same agent + tag + first 60 chars of fact)
    prefix = fact[:60]
    existing = conn.execute(
        "SELECT id, reinforced FROM agent_memory WHERE agent=? AND tag=? AND fact LIKE ?",
        (agent, tag, prefix + "%")
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE agent_memory SET reinforced = reinforced + 1, ts = ? WHERE id = ?",
            (ts, existing["id"])
        )
        conn.commit()
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO agent_memory (ts, agent, tag, fact, confidence) VALUES (?, ?, ?, ?, ?)",
        (ts, agent, tag, fact, confidence)
    )
    conn.commit()
    return cur.lastrowid


def search(term: str, limit: int = 10, db_path=None) -> list[dict]:
    """Keyword search over facts. Returns list of dicts."""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT * FROM agent_memory WHERE fact LIKE ? OR tag LIKE ? ORDER BY ts DESC LIMIT ?",
        (f"%{term}%", f"%{term}%", limit)
    ).fetchall()
    return [dict(r) for r in rows]


def recall(tag: str, limit: int = 5, db_path=None) -> list[dict]:
    """Retrieve facts by exact tag, most reinforced first."""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT * FROM agent_memory WHERE tag=? ORDER BY reinforced DESC, confidence DESC LIMIT ?",
        (tag, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def list_recent(limit: int = 20, db_path=None) -> list[dict]:
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT * FROM agent_memory ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def facts_for_prompt(tag: str, db_path=None) -> str:
    """Return a compact string of recalled facts suitable for injecting into an LLM prompt."""
    facts = recall(tag, limit=5, db_path=db_path)
    if not facts:
        return ""
    lines = [f"- {f['fact']}" for f in facts]
    return f"[Memory: {tag}]\n" + "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 1:
        rows = list_recent(20)
        if not rows:
            print("No facts stored yet.")
        else:
            print(f"{'ID':>4}  {'TS':<20}  {'AGENT':<15}  {'TAG':<20}  FACT")
            print("-" * 90)
            for r in rows:
                print(f"{r['id']:>4}  {r['ts']:<20}  {r['agent']:<15}  {r['tag']:<20}  {r['fact'][:50]}")
    elif sys.argv[1] == "search" and len(sys.argv) >= 3:
        term = " ".join(sys.argv[2:])
        rows = search(term)
        for r in rows:
            print(f"[{r['tag']}] {r['agent']}: {r['fact']}")
    elif sys.argv[1] == "add" and len(sys.argv) >= 5:
        agent, tag, fact = sys.argv[2], sys.argv[3], " ".join(sys.argv[4:])
        rid = add_fact(agent, tag, fact)
        print(f"Stored fact #{rid}")
    else:
        print("Usage: python3 agent_memory.py [search <term>] [add <agent> <tag> <fact>]")
