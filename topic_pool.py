"""topic_pool.py — Auto-seed research tasks when the queue runs dry.

Maintains a rotating pool of fishery/aquaculture topics. When the worker
detects an idle queue, it calls seed_if_idle() which enqueues 1-2 research
tasks so agents always have something to work on.

Topics are marked as queued in agent_memory to avoid repeats within 7 days.

Usage (called by worker_daemon.py loop, or manually):
    python3 topic_pool.py                    # seed if idle
    python3 topic_pool.py --status           # show pool + last-queued
    python3 topic_pool.py --force            # seed regardless of queue depth
"""
import datetime
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TOPICS = [
    # Aquaculture tools
    "free pond water quality calculator for tilapia farmers",
    "FCR feed conversion ratio tracker for shrimp farms",
    "fish stocking density calculator for small ponds",
    "dissolved oxygen management in aquaculture ponds",
    "shrimp post-larvae survival rate benchmarks",
    # Market & regulation
    "global shrimp price trends and supply chain outlook",
    "India aquaculture export regulations 2024-2025",
    "vannamei vs monodon shrimp farming cost comparison",
    "EU seafood import standards and labelling requirements",
    "sustainable aquaculture certification guides (ASC, BAP)",
    # Technology
    "IoT sensors for pond monitoring in small-scale farms",
    "probiotics use in shrimp aquaculture feed",
    "aeration systems comparison for fish ponds",
    "biofloc technology introduction for beginners",
    "tilapia cage culture in reservoirs pros and cons",
    # News angles
    "trending fishery regulations or sustainability news this week",
    "climate impact on ocean fish stocks and fishermen livelihoods",
    "artisanal fishing communities technology adoption stories",
    "mangrove conservation and coastal fishing communities",
    "fish meal alternatives in aquaculture feed industry",
]

IDLE_THRESHOLD = 2       # enqueue when pending tasks < this
TOPICS_PER_SEED = 2      # how many to add when idle
COOLDOWN_DAYS = 7        # don't repeat a topic within this many days


def _recent_topics(db_path=None):
    """Return set of topics enqueued in the last COOLDOWN_DAYS from agent_memory."""
    try:
        import agent_memory as am
        conn = am._connect(db_path)
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=COOLDOWN_DAYS)).isoformat()
        rows = conn.execute(
            "SELECT content FROM agent_memory WHERE tag='topic_seeded' AND created_at > ?",
            (cutoff,)
        ).fetchall()
        conn.close()
        return {r[0].lower() for r in rows}
    except Exception:
        return set()


def _mark_queued(topic, db_path=None):
    try:
        import agent_memory as am
        am.add_fact("scheduler", "topic_seeded", topic, confidence=1.0, db_path=db_path)
    except Exception:
        pass


def _pending_count(db_path=None):
    try:
        import workforce
        conn = workforce.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='pending'"
        ).fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 999


def pick_topics(n=TOPICS_PER_SEED, db_path=None):
    """Return up to n topics not recently used."""
    recent = _recent_topics(db_path)
    available = [t for t in TOPICS if t.lower() not in recent]
    if not available:
        # All topics used — reset and start over
        available = list(TOPICS)
    import random
    random.shuffle(available)
    return available[:n]


def seed_if_idle(db_path=None, force=False):
    """Enqueue research tasks if queue is nearly empty. Returns list of queued topics."""
    pending = _pending_count(db_path)
    if not force and pending >= IDLE_THRESHOLD:
        return []

    topics = pick_topics(db_path=db_path)
    queued = []
    try:
        import workforce
        for topic in topics:
            workforce.enqueue("research", {"topic": topic}, priority=3, db_path=db_path)
            _mark_queued(topic, db_path)
            queued.append(topic)
    except Exception as e:
        pass
    return queued


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Chilli Gavva topic pool")
    parser.add_argument("--status", action="store_true", help="Show pool status")
    parser.add_argument("--force", action="store_true", help="Seed regardless of queue depth")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    if args.status:
        recent = _recent_topics(args.db)
        pending = _pending_count(args.db)
        available = [t for t in TOPICS if t.lower() not in recent]
        print(f"\nQueue pending: {pending}  |  Topics available: {len(available)}/{len(TOPICS)}")
        print(f"Idle threshold: {IDLE_THRESHOLD}  |  Cooldown: {COOLDOWN_DAYS} days\n")
        if recent:
            print("Recent (cooldown):")
            for t in sorted(recent):
                print(f"  • {t}")
        return

    queued = seed_if_idle(db_path=args.db, force=args.force)
    if queued:
        print(f"Seeded {len(queued)} research task(s):")
        for t in queued:
            print(f"  • {t}")
    else:
        pending = _pending_count(args.db)
        print(f"No seed needed (pending={pending} >= {IDLE_THRESHOLD})")


if __name__ == "__main__":
    main()
