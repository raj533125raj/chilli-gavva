#!/usr/bin/env python3
"""scripts/make_office_fixture.py — Synthetic office snapshot generator for Binny Mills.

Generates deterministic, synthetic office snapshots matching the schema of office_protocol.py.
Safe for public repositories: fake names only, no secrets, no money amounts.
Every record is explicitly marked with `"synthetic": true`.

Supports sizes:
  - small: 6 teams, 30 agents
  - medium: 15 teams, 400 agents
  - full: 40 teams, 3500 main agents + sub agents up to 6000 total

Never writes into web/data. Output defaults to /tmp.
"""

import argparse
import datetime
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path so office_protocol can be imported read-only
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import office_protocol

# Preset size configurations:
# (num_teams, num_main_agents, target_total_agents)
SIZE_CONFIGS = {
    "small": {
        "teams": 6,
        "main_agents": 24,       # 24 main + 6 captains = 30 agents (0 sub agents)
        "sub_agents": 0,
        "sub_rooms_per_team": 2,
        "approvals": 6,
    },
    "medium": {
        "teams": 15,
        "main_agents": 250,      # 250 main + 15 captains = 265; 135 sub agents = 400 total
        "sub_agents": 135,
        "sub_rooms_per_team": 3,
        "approvals": 15,
    },
    "full": {
        "teams": 40,
        "main_agents": 3500,     # exactly 3500 main agents
        "sub_agents": 2460,      # 3500 main + 40 captains + 2460 sub agents = 6000 total agents
        "sub_rooms_per_team": 5,
        "approvals": 40,
    },
}

HONEST_STATUSES = office_protocol.HONEST_STATUSES

FAKE_DEPARTMENTS = [
    ("Alpha", "Strategic Planning and Architecture"),
    ("Beta", "Data Pipeline Ingestion and Validation"),
    ("Gamma", "Algorithmic Analysis and Pattern Engine"),
    ("Delta", "Content Structuring and Knowledge Index"),
    ("Epsilon", "Code Review and Verification Services"),
    ("Zeta", "Interface Design and Interaction Specs"),
    ("Eta", "Automated QA and Integration Probes"),
    ("Theta", "Performance Profiling and Latency Guard"),
    ("Iota", "Audit Trails and Protocol Compliance"),
    ("Kappa", "Task Dispatch and Schedule Coordination"),
    ("Lambda", "Event Stream Processing and Queues"),
    ("Mu", "Semantic Search and Document Retrieval"),
    ("Nu", "Localization and International Text Pack"),
    ("Xi", "Schema Validation and Type Enforcers"),
    ("Omicron", "Resource Accounting and Budget Tracking"),
    ("Pi", "Continuous Delivery and Artifact Staging"),
    ("Rho", "Cache Invalidation and Storage Maintenance"),
    ("Sigma", "Model Evaluation and Benchmark Telemetry"),
    ("Tau", "Security Sandboxing and Memory Isolation"),
    ("Upsilon", "Incident Mitigation and Self-Healing"),
    ("Phi", "Workforce Operations and Roster Planning"),
    ("Chi", "System Telemetry and Metrics Aggregation"),
    ("Psi", "Workflow Automation and Cron Supervision"),
    ("Omega", "Executive Synthesis and Weekly Projection"),
    ("Apex", "High-Throughput Batch Processing"),
    ("Beacon", "Distributed Node Health and Heartbeats"),
    ("Cipher", "Cryptographic Signatures and Key Vault"),
    ("Dynamo", "High-Frequency Transaction Balancing"),
    ("Echo", "Real-Time Broadcast and Notification Hub"),
    ("Falcon", "Rapid Prototyping and Spike Evaluation"),
    ("Glacier", "Cold Archive Storage and Retrieval"),
    ("Horizon", "Long-Term Trend Analysis and Modeling"),
    ("Impulse", "Signal Detection and Event Routing"),
    ("Jupiter", "Large Model Coordination and Sharding"),
    ("Krypton", "Container Orchestration and Isolation"),
    ("Lunar", "Nightly Batch Reconciliation and Rollup"),
    ("Matrix", "Vector Indexing and Nearest Neighbor Search"),
    ("Nebula", "Cloud Edge Routing and DNS Failover"),
    ("Orion", "Multi-Region Synchronization and Sync"),
    ("Polaris", "North Star Metric Guard and Verification"),
]

FAKE_FIRST_NAMES = [
    "Aarav", "Aditi", "Alex", "Amara", "Arjun", "Astra", "Bo", "Chen",
    "Devi", "Elena", "Farhan", "Gita", "Hana", "Indira", "Jaya", "Kiran",
    "Lars", "Meera", "Nikhil", "Noor", "Omar", "Pooja", "Quinn", "Rahul",
    "Ravi", "Sanjay", "Sita", "Tara", "Uma", "Vikram", "Wei", "Yuki",
    "Zara", "Aiden", "Blair", "Cyrus", "Daria", "Ezra", "Felix", "Gwen",
]

FAKE_ROLES = [
    "Planner", "Analyst", "Coder", "Reviewer", "Verifier", "Auditor",
    "Indexer", "Validator", "Profiler", "Dispatcher", "Engineer", "Scout",
]

FAKE_MODELS = [
    "SynthLLM-Small-v1",
    "SynthLLM-Medium-v2",
    "SynthLLM-Large-v3",
    "SynthLLM-Reasoning-v1",
    "SynthLocal-7B-Quant",
]

FAKE_ACTIONS = [
    "Processing synthetic pipeline batch",
    "Evaluating test vector coverage",
    "Compiling protocol snapshot",
    "Validating synthetic schema nodes",
    "Running deterministic simulations",
    "Refactoring virtual module interface",
    "Auditing synthetic event logs",
    "Generating fixture mock data",
    "Calibrating benchmark parameters",
    "Awaiting scheduled dispatch window",
]


def _format_iso(dt: datetime.datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _distribute_counts(total: int, bins: int) -> List[int]:
    """Evenly distribute `total` across `bins` bins."""
    base = total // bins
    remainder = total % bins
    return [base + (1 if i < remainder else 0) for i in range(bins)]


def generate_agent_record(
    agent_id: str,
    name: str,
    level: str,
    status: str,
    rng: random.Random,
    base_time: datetime.datetime,
) -> Dict[str, Any]:
    """Build a deterministic synthetic agent record."""
    # Progress: 0-100 for working/thinking/just finished, None for idle/paused/etc.
    progress = None
    action = ""
    next_shift = None

    if status in ("working", "thinking"):
        progress = rng.randint(10, 95)
        action = rng.choice(FAKE_ACTIONS)
    elif status == "just finished":
        progress = 100
        action = "Task recently completed"
    elif status == "needs you":
        progress = rng.choice([None, rng.randint(20, 80)])
        action = "Needs owner assistance with task parameters"
    elif status == "waiting":
        progress = 0
        action = "Task queued waiting for slot"
    elif status == "off duty with next shift":
        progress = None
        # Next shift between 1 and 12 hours ahead
        shift_dt = base_time + datetime.timedelta(hours=rng.randint(1, 12))
        next_shift = _format_iso(shift_dt)
        action = f"Off duty, next shift at {next_shift}"
    elif status == "idle":
        action = "Idle and waiting for input"
    elif status == "warning":
        action = "Recovery in progress"
    elif status == "error":
        action = "Last task failed"
    elif status == "paused":
        action = "Paused"
    elif status == "not built yet":
        action = "Not built yet"

    last_seen_dt = base_time - datetime.timedelta(minutes=rng.randint(1, 120))
    last_seen = _format_iso(last_seen_dt)
    model = rng.choice(FAKE_MODELS)

    record = {
        "id": agent_id,
        "name": name,
        "level": level,
        "status": status,
        "current_action": action,
        "progress": progress,
        "model": model,
        "last_seen": last_seen,
        "synthetic": True,
    }
    if next_shift:
        record["next_shift"] = next_shift

    return record


def generate_office_fixture(
    size: str = "full",
    seed: int = 42,
    base_time: Optional[datetime.datetime] = None,
    office_id: str = "binny-mills-synthetic-office",
    validate: bool = True,
) -> Dict[str, Any]:
    """Generate a full synthetic office snapshot fixture."""
    if size not in SIZE_CONFIGS:
        raise ValueError(f"Unknown size '{size}'. Available sizes: {list(SIZE_CONFIGS.keys())}")

    config = SIZE_CONFIGS[size]
    rng = random.Random(seed)
    base_dt = base_time or datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
    if base_dt.tzinfo is None:
        base_dt = base_dt.replace(tzinfo=datetime.timezone.utc)

    num_teams = config["teams"]
    total_main_agents = config["main_agents"]
    total_sub_agents = config["sub_agents"]
    sub_rooms_per_team = config["sub_rooms_per_team"]
    num_approvals = config["approvals"]

    main_per_team = _distribute_counts(total_main_agents, num_teams)
    sub_per_team = _distribute_counts(total_sub_agents, num_teams)

    # Status distribution pool: ensure every honest status is represented
    # Statuses: 'working', 'thinking', 'just finished', 'needs you', 'waiting',
    #           'off duty with next shift', 'idle', 'warning', 'error', 'paused', 'not built yet'
    status_list = list(HONEST_STATUSES)

    teams: List[Dict[str, Any]] = []
    all_agent_statuses: List[str] = []

    global_agent_id_counter = 1

    for t_idx in range(num_teams):
        dept_name, dept_role = FAKE_DEPARTMENTS[t_idx % len(FAKE_DEPARTMENTS)]
        team_id = f"cube-{dept_name.lower()}-{t_idx+1:02d}"
        team_name = f"Synthetic {dept_name} Cube"

        # Rotate team statuses: mostly 'on', occasionally 'off' or 'archived'
        if t_idx == 3:
            team_status = "off"
        elif t_idx == 7 and num_teams > 10:
            team_status = "archived"
        else:
            team_status = "on"

        # Captain
        capt_status = status_list[(t_idx) % len(status_list)]
        capt_name = f"Captain {FAKE_FIRST_NAMES[t_idx % len(FAKE_FIRST_NAMES)]} {dept_name}"
        capt_id = f"synth-capt-{dept_name.lower()}-{t_idx+1:02d}"
        captain = generate_agent_record(
            agent_id=capt_id,
            name=capt_name,
            level="captain",
            status=capt_status,
            rng=rng,
            base_time=base_dt,
        )
        all_agent_statuses.append(capt_status)

        # Sub rooms
        sub_rooms: List[Dict[str, Any]] = []
        n_sub_agents = sub_per_team[t_idx]
        sub_agents_per_room = _distribute_counts(n_sub_agents, sub_rooms_per_team)

        for r_idx in range(sub_rooms_per_team):
            room_name = f"{dept_name} Room {chr(65 + r_idx)}"
            room_agents: List[Dict[str, Any]] = []
            for s_idx in range(sub_agents_per_room[r_idx]):
                s_status = status_list[(global_agent_id_counter + s_idx) % len(status_list)]
                first = FAKE_FIRST_NAMES[(global_agent_id_counter + s_idx) % len(FAKE_FIRST_NAMES)]
                role = FAKE_ROLES[(s_idx) % len(FAKE_ROLES)]
                s_id = f"synth-sub-{dept_name.lower()}-{t_idx+1:02d}-r{r_idx+1:02d}-{s_idx+1:03d}"
                s_rec = generate_agent_record(
                    agent_id=s_id,
                    name=f"Sub {first} {role}",
                    level="sub",
                    status=s_status,
                    rng=rng,
                    base_time=base_dt,
                )
                room_agents.append(s_rec)
                all_agent_statuses.append(s_status)

            sub_rooms.append({
                "name": room_name,
                "agents": room_agents,
                "synthetic": True,
            })

        # Main agents
        main_agents: List[Dict[str, Any]] = []
        n_main_agents = main_per_team[t_idx]
        for m_idx in range(n_main_agents):
            m_status = status_list[(global_agent_id_counter + m_idx) % len(status_list)]
            first = FAKE_FIRST_NAMES[(global_agent_id_counter + m_idx) % len(FAKE_FIRST_NAMES)]
            role = FAKE_ROLES[(m_idx) % len(FAKE_ROLES)]
            m_id = f"synth-agent-{dept_name.lower()}-{t_idx+1:02d}-{m_idx+1:04d}"
            m_rec = generate_agent_record(
                agent_id=m_id,
                name=f"{first} {role}",
                level="main",
                status=m_status,
                rng=rng,
                base_time=base_dt,
            )
            main_agents.append(m_rec)
            all_agent_statuses.append(m_status)

        global_agent_id_counter += n_main_agents + n_sub_agents

        team_record = {
            "id": team_id,
            "name": team_name,
            "role": dept_role,
            "status": team_status,
            "captain": captain,
            "sub_rooms": sub_rooms,
            "agents": main_agents,
            "synthetic": True,
        }
        teams.append(team_record)

    # Approvals Queue: mix of waited hours around the 24h rule
    # (e.g. 1.5h, 12.0h, 23.5h, 23.9h, 24.1h, 25.0h, 48.0h)
    approvals: List[Dict[str, Any]] = []
    waited_hour_samples = [
        0.5, 2.0, 6.0, 12.5, 18.0, 22.0, 23.5, 23.9, 24.0, 24.2, 25.5, 30.0, 48.0
    ]
    for a_idx in range(num_approvals):
        app_id = 1000 + a_idx
        waited_h = waited_hour_samples[a_idx % len(waited_hour_samples)]
        created_dt = base_dt - datetime.timedelta(hours=waited_h)
        due_dt = created_dt + datetime.timedelta(hours=24)

        score = 60 + (a_idx * 7) % 40
        topic_dept = FAKE_DEPARTMENTS[a_idx % len(FAKE_DEPARTMENTS)][0]
        app_title = f"Synthetic Proposal {app_id} - {topic_dept} Optimization"

        approvals.append({
            "id": app_id,
            "title": app_title,
            "score": score,
            "waited_hours": round(waited_h, 1),
            "auto_decision_due": _format_iso(due_dt),
            "synthetic": True,
        })

    # Counters calculation
    running_count = sum(1 for s in all_agent_statuses if s in ("working", "thinking"))
    needs_you_agents = sum(1 for s in all_agent_statuses if s == "needs you")
    total_agents_count = len(all_agent_statuses)

    counters = {
        "agents": total_agents_count,
        "running": running_count,
        "needs_you": needs_you_agents + len(approvals),
        "drafts_waiting": len(approvals),
        "products_ready": 42 if size == "full" else (12 if size == "medium" else 3),
        "products_needs_fix": 3 if size == "full" else (1 if size == "medium" else 0),
        "minutes_used": 1420 if size == "full" else 350,
        "minutes_limit": 5000,
        "ai_providers_up": 3,
        "ai_providers_total": 3,
        "synthetic": True,
    }

    snapshot = {
        "version": office_protocol.PROTOCOL_VERSION,
        "office_id": office_id,
        "revision": 1,
        "generated_at": _format_iso(base_dt),
        "teams": teams,
        "counters": counters,
        "approvals": approvals,
        "synthetic": True,
    }

    # Strict schema validation using office_protocol (read-only import)
    if validate:
        office_protocol.validate_snapshot(snapshot)

    return snapshot


def write_fixture_file(snapshot: Dict[str, Any], output_path: str) -> str:
    """Write synthetic office snapshot to disk, ensuring target is safe."""
    norm_path = os.path.abspath(output_path)
    # Refuse writing into web/data as required
    if "web/data" in norm_path or norm_path.endswith("web/data"):
        raise ValueError(f"Writing into web/data is strictly prohibited: {output_path}")

    parent_dir = os.path.dirname(norm_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    with open(norm_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)

    return norm_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic office snapshots in office_protocol schema."
    )
    parser.add_argument(
        "--size",
        choices=["small", "medium", "full"],
        default="full",
        help="Office size preset: small (6 teams, 30 agents), medium (15 teams, 400 agents), full (40 teams, 3500 main agents + sub agents up to 6000 total). Default: full.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic random seed. Default: 42.",
    )
    parser.add_argument(
        "--out-dir",
        default="/tmp",
        help="Directory to write fixture JSON. Default: /tmp. (Never web/data)",
    )
    parser.add_argument(
        "--filename",
        default=None,
        help="Output filename. Defaults to synthetic_office_<size>.json.",
    )
    parser.add_argument(
        "--office-id",
        default="binny-mills-synthetic-office",
        help="Office identifier. Default: binny-mills-synthetic-office.",
    )

    args = parser.parse_args()

    filename = args.filename or f"synthetic_office_{args.size}.json"
    out_path = os.path.join(args.out_dir, filename)

    print(f"Generating synthetic office fixture: size={args.size}, seed={args.seed}...")
    snapshot = generate_office_fixture(
        size=args.size,
        seed=args.seed,
        office_id=args.office_id,
        validate=True,
    )

    written_path = write_fixture_file(snapshot, out_path)
    print(f"Successfully generated and validated fixture written to: {written_path}")
    print(f"Teams: {len(snapshot['teams'])}, Total agents: {snapshot['counters']['agents']}")


if __name__ == "__main__":
    main()
