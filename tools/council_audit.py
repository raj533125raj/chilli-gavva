"""Read-only counts of blocked approvals; legacy fallback decisions remain unclassified."""
import sqlite3
from contextlib import closing
from pathlib import Path


def counts(db_path):
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'product_deliveries' not in tables:
            return {}
        rows = conn.execute("SELECT d.approval_id,d.status FROM product_deliveries d JOIN approvals a ON a.id=d.approval_id "
                            "WHERE a.kind='public_draft' AND (d.reason LIKE '%Council did not pass%' "
                            "OR d.status IN ('waiting_ai','council_rejected','council_improving'))").fetchall()
        result = {}
        for aid, status in rows:
            # Old council logs did not save whether an AI answer or heuristics made the decision.
            # A NEEDS YOU row cannot retrospectively prove a genuine model rejection.
            reason = {'waiting_ai': 'waiting for AI', 'council_rejected': 'council rejected after improvement',
                      'council_improving': 'improvement waiting'}.get(status, 'legacy outcome lacks AI evidence')
            result[reason] = result.get(reason, 0) + 1
        return result


if __name__ == '__main__':
    import json
    import sys
    print(json.dumps(counts(sys.argv[1]), sort_keys=True))
