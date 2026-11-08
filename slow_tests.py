"""slow_tests.py — Runs a given list of test modules with timing (subprocess, per-module timeout).

Prints the 15 slowest tests and modules over 20s, writes a markdown report.
Used by Claude / developers to keep CI under the timeout.
Never runs in CI by itself.

CLI:
  python3 slow_tests.py [modules...] [--timeout SECONDS] [--report PATH] [--threshold-s SECONDS]
"""

import argparse
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple


def normalize_module_name(mod: str) -> str:
    """Normalize file path or python module syntax to a dotted module name."""
    mod = mod.strip()
    if mod.endswith(".py"):
        mod = mod[:-3]
    mod = mod.replace("/", ".").replace("\\", ".")
    if mod.startswith("."):
        mod = mod.lstrip(".")
    return mod


def run_test_module(
    module: str,
    timeout: float = 60.0,
    cwd: Optional[str] = None,
    python_bin: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a single test module via subprocess with unittest -v and measure duration.

    Returns dict:
      {
        "module": module,
        "duration": float (seconds),
        "status": "ok" | "fail" | "timeout" | "error",
        "returncode": int,
        "test_count": int,
        "timed_tests": list of {"name": str, "duration": float, "status": str},
        "output": str,
      }
    """
    python_cmd = python_bin or sys.executable
    cmd = [python_cmd, "-m", "unittest", "-v", module]
    start_time = time.perf_counter()

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        duration = round(time.perf_counter() - start_time, 3)
        output = proc.stdout or ""
        returncode = proc.returncode
        status = "ok" if returncode == 0 else "fail"
    except subprocess.TimeoutExpired as exc:
        duration = round(time.perf_counter() - start_time, 3)
        output = (exc.stdout or "") if hasattr(exc, "stdout") and exc.stdout else f"Timed out after {timeout}s"
        returncode = -1
        status = "timeout"
    except Exception as exc:
        duration = round(time.perf_counter() - start_time, 3)
        output = str(exc)
        returncode = -2
        status = "error"

    timed_tests, test_count = parse_unittest_output(output, duration)

    return {
        "module": module,
        "duration": duration,
        "status": status,
        "returncode": returncode,
        "test_count": test_count,
        "timed_tests": timed_tests,
        "output": output,
    }


def parse_unittest_output(output: str, total_module_duration: float) -> Tuple[List[Dict[str, Any]], int]:
    """Parse unittest verbose output into individual tests and approximate durations if available."""
    timed_tests: List[Dict[str, Any]] = []
    lines = output.splitlines()

    # Pattern for unittest -v output:
    # test_name (module.Class) ... ok
    # or test_name (module.Class.test_name) ... ok
    # or with custom runner timing: test_name (module.Class) ... ok in 0.12s
    test_pattern = re.compile(r"^(\S+)\s+\(([^)]+)\)\s+\.\.\.\s+([A-Za-z0-9_-]+)(?:\s+in\s+([0-9.]+)\s*s)?")
    ran_pattern = re.compile(r"^Ran\s+(\d+)\s+tests?\s+in\s+([0-9.]+)s")

    parsed_tests = []
    reported_total = None

    for line in lines:
        line_clean = line.strip()
        m_ran = ran_pattern.match(line_clean)
        if m_ran:
            reported_total = float(m_ran.group(2))
            continue

        m_test = test_pattern.match(line_clean)
        if m_test:
            test_fn = m_test.group(1)
            test_cls = m_test.group(2)
            res_status = m_test.group(3)
            dur_str = m_test.group(4)
            dur = float(dur_str) if dur_str else None
            if test_cls.endswith(f".{test_fn}"):
                full_name = test_cls
            else:
                full_name = f"{test_cls}.{test_fn}"
            parsed_tests.append({
                "name": full_name,
                "status": res_status,
                "duration": dur,
            })

    total_parsed = len(parsed_tests)
    effective_total = reported_total if reported_total is not None else total_module_duration

    # If individual durations were not logged by custom runner, distribute or estimate evenly
    for item in parsed_tests:
        if item["duration"] is None:
            est_dur = round(effective_total / max(1, total_parsed), 3)
            item["duration"] = est_dur
        timed_tests.append(item)

    return timed_tests, total_parsed


def run_all_test_modules(
    modules: List[str],
    timeout_per_module: float = 60.0,
    cwd: Optional[str] = None,
    python_bin: Optional[str] = None,
) -> Dict[str, Any]:
    """Run all specified test modules, aggregate slowest modules and tests."""
    results = []
    all_individual_tests: List[Dict[str, Any]] = []

    for mod in modules:
        norm_mod = normalize_module_name(mod)
        if not norm_mod:
            continue
        res = run_test_module(norm_mod, timeout=timeout_per_module, cwd=cwd, python_bin=python_bin)
        results.append(res)
        for t in res.get("timed_tests", []):
            all_individual_tests.append({
                "module": res["module"],
                "name": t["name"],
                "duration": t["duration"],
                "status": t["status"],
            })

    # Sort slowest modules
    slow_modules = sorted(results, key=lambda x: x["duration"], reverse=True)

    # Sort slowest tests
    slow_tests = sorted(all_individual_tests, key=lambda x: x["duration"], reverse=True)
    top_15_tests = slow_tests[:15]

    # Modules exceeding 20s
    over_20s_modules = [m for m in results if m["duration"] >= 20.0]

    return {
        "modules_run": len(results),
        "module_results": results,
        "slowest_modules": slow_modules,
        "slowest_15_tests": top_15_tests,
        "modules_over_20s": over_20s_modules,
    }


def generate_markdown_report(report_data: Dict[str, Any], threshold_s: float = 20.0) -> str:
    """Generate Markdown report of slowest tests and slow modules."""
    lines = [
        "# Test Suite Execution & Latency Report",
        "",
        f"- **Total modules executed**: {report_data.get('modules_run', 0)}",
        f"- **Modules exceeding {threshold_s}s**: {len(report_data.get('modules_over_20s', []))}",
        "",
        "## Top 15 Slowest Tests",
        "",
        "| Rank | Test | Module | Duration (s) | Status |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    slowest_tests = report_data.get("slowest_15_tests", [])
    if slowest_tests:
        for idx, t in enumerate(slowest_tests, start=1):
            lines.append(f"| {idx} | `{t['name']}` | `{t['module']}` | {t['duration']:.3f}s | {t['status']} |")
    else:
        lines.append("| - | No tests recorded | - | - | - |")

    lines.extend([
        "",
        f"## Modules Exceeding {threshold_s}s",
        "",
        "| Module | Duration (s) | Tests | Status |",
        "| :--- | :--- | :--- | :--- |",
    ])

    modules_over = [m for m in report_data.get("module_results", []) if m["duration"] >= threshold_s]
    if modules_over:
        for m in sorted(modules_over, key=lambda x: x["duration"], reverse=True):
            lines.append(f"| `{m['module']}` | {m['duration']:.3f}s | {m.get('test_count', 0)} | {m['status']} |")
    else:
        lines.append(f"| None | < {threshold_s}s | - | All OK |")

    lines.extend([
        "",
        "## All Module Durations",
        "",
        "| Module | Duration (s) | Tests | Status |",
        "| :--- | :--- | :--- | :--- |",
    ])

    for m in report_data.get("slowest_modules", []):
        lines.append(f"| `{m['module']}` | {m['duration']:.3f}s | {m.get('test_count', 0)} | {m['status']} |")

    lines.append("")
    return "\n".join(lines)


def format_cli_summary(report_data: Dict[str, Any], threshold_s: float = 20.0) -> str:
    """Format human-readable CLI summary printing top 15 tests and >20s modules."""
    lines = [
        "==================================================",
        "TEST RUN TIME SUMMARY",
        "==================================================",
        f"Total modules tested: {report_data.get('modules_run', 0)}",
        "",
        "--- TOP 15 SLOWEST TESTS ---",
    ]

    slowest_tests = report_data.get("slowest_15_tests", [])
    if slowest_tests:
        for idx, t in enumerate(slowest_tests, start=1):
            lines.append(f"{idx:2d}. {t['duration']:6.2f}s - {t['name']} ({t['module']}) [{t['status']}]")
    else:
        lines.append("No individual test details available.")

    lines.extend([
        "",
        f"--- MODULES OVER {threshold_s}s ---",
    ])

    modules_over = [m for m in report_data.get("module_results", []) if m["duration"] >= threshold_s]
    if modules_over:
        for m in sorted(modules_over, key=lambda x: x["duration"], reverse=True):
            lines.append(f"• {m['duration']:6.2f}s - {m['module']} ({m.get('test_count', 0)} tests) [{m['status']}]")
    else:
        lines.append(f"None (all modules completed under {threshold_s}s).")

    lines.append("==================================================")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run test modules with per-module timing & timeout.")
    parser.add_argument("modules", nargs="*", help="Test modules or test file paths to run.")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-module timeout in seconds (default: 60).")
    parser.add_argument("--report", default=None, help="Path to write Markdown report.")
    parser.add_argument("--threshold", type=float, default=20.0, help="Threshold in seconds for slow modules (default: 20).")
    args = parser.parse_args()

    modules = args.modules
    if not modules:
        # Default fallback: check tests/ directory
        test_dir = os.path.join(os.path.dirname(__file__), "tests")
        if os.path.isdir(test_dir):
            modules = [
                os.path.join("tests", f)
                for f in sorted(os.listdir(test_dir))
                if f.startswith("test_") and f.endswith(".py")
            ][:5]  # Sample first 5 if none provided
        else:
            modules = []

    report_data = run_all_test_modules(modules, timeout_per_module=args.timeout)

    print(format_cli_summary(report_data, threshold_s=args.threshold))

    if args.report:
        md_text = generate_markdown_report(report_data, threshold_s=args.threshold)
        report_path = os.path.abspath(args.report)
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        # Deterministic atomic write
        tmp_path = report_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(md_text)
        os.replace(tmp_path, report_path)
        print(f"Markdown report written to: {args.report}")


if __name__ == "__main__":
    main()
