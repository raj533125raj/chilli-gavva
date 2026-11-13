#!/usr/bin/env python3
"""tools/run_tests_chunked.py — Run all tests in chunks with per-chunk timeout and summary.

Splits tests into chunks, runs each chunk in a subprocess with a timeout,
collects timing per test module, identifies slowest 10 modules, outputs
FAIL/ERROR lines for run_failure compatibility, and exits with code 0 on pass
or non-zero on test failures/timeouts.
"""
import argparse
import glob
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHUNK_SIZE = 20
DEFAULT_TIMEOUT_PER_CHUNK = 480  # 8 minutes per chunk (a GitHub chunk took 217 s on 2026-11-13)


def discover_test_modules(tests_dir=None):
    """Find and return a sorted list of test module names under tests_dir.

    Returns:
        list of str: e.g. ['tests.test_approvals_ledger', 'tests.test_auto_approve', ...]
    """
    base = Path(tests_dir) if tests_dir else (ROOT / "tests")
    if not base.is_dir():
        return []
    pattern = str(base / "test_*.py")
    files = sorted(glob.glob(pattern))
    modules = []
    for f in files:
        stem = Path(f).stem
        # Use module dot notation matching python3 -m unittest invocation
        modules.append(f"tests.{stem}")
    return modules


def chunk_list(items, chunk_size):
    """Split items into list of lists of max length chunk_size."""
    if chunk_size <= 0:
        chunk_size = 1
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def parse_unittest_output(output):
    """Extract counts (run, failures, errors, skipped) and failed test names from unittest output.

    Returns:
        dict: {
            "run": int,
            "failures": int,
            "errors": int,
            "skipped": int,
            "failing_tests": list[str] # formatted as method (module.Class) or line
        }
    """
    res = {
        "run": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "failing_tests": [],
    }

    # Match: Ran 12 tests in 0.34s
    ran_m = re.search(r"Ran\s+(\d+)\s+tests?", output)
    if ran_m:
        res["run"] = int(ran_m.group(1))

    # Match FAILED (failures=X, errors=Y, skipped=Z)
    fail_m = re.search(r"FAILED\s*\(([^)]+)\)", output)
    if fail_m:
        details = fail_m.group(1)
        for part in details.split(","):
            part = part.strip()
            if part.startswith("failures="):
                res["failures"] = int(part.split("=")[1])
            elif part.startswith("errors="):
                res["errors"] = int(part.split("=")[1])
            elif part.startswith("skipped="):
                res["skipped"] = int(part.split("=")[1])

    # Match OK (skipped=Z)
    ok_m = re.search(r"OK\s*\(([^)]+)\)", output)
    if ok_m:
        details = ok_m.group(1)
        for part in details.split(","):
            part = part.strip()
            if part.startswith("skipped="):
                res["skipped"] = int(part.split("=")[1])

    # Match FAIL/ERROR lines: FAIL: test_foo (tests.test_bar.BarTest)
    for match in re.finditer(r"^(FAIL|ERROR):\s+(test_\w+)\s+\(([\w.]+)\)", output, re.MULTILINE):
        kind, method, loc = match.groups()
        res["failing_tests"].append(f"{kind}: {method} ({loc})")

    return res


def run_single_module(module, timeout=30, env=None, runner=subprocess.run):
    """Run a single test module to measure its execution time accurately if needed."""
    t0 = time.time()
    cmd = [sys.executable, "-m", "unittest", module]
    try:
        res = runner(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        elapsed = time.time() - t0
        return (res.returncode, elapsed, res.stdout or "")
    except subprocess.TimeoutExpired:
        return (-1, timeout, f"TIMEOUT after {timeout}s")
    except OSError as e:
        return (-1, 0.0, str(e))


def run_chunk(
    modules,
    timeout=DEFAULT_TIMEOUT_PER_CHUNK,
    env=None,
    runner=subprocess.run,
    log_func=print,
):
    """Run a chunk of modules via python3 -m unittest.

    Returns:
        dict: {
            "modules": list[str],
            "returncode": int,
            "elapsed": float,
            "timed_out": bool,
            "output": str,
            "parsed": dict
        }
    """
    t0 = time.time()
    cmd = [sys.executable, "-m", "unittest"] + modules
    timed_out = False
    output = ""
    rc = 0

    try:
        res = runner(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        elapsed = round(time.time() - t0, 3)
        rc = res.returncode
        output = res.stdout or ""
    except subprocess.TimeoutExpired as e:
        timed_out = True
        rc = 124
        elapsed = round(time.time() - t0, 3)
        output = (e.stdout or "") if hasattr(e, "stdout") and e.stdout else f"Chunk timed out after {timeout}s"
    except OSError as e:
        rc = 1
        elapsed = round(time.time() - t0, 3)
        output = f"OSError: {e}"

    parsed = parse_unittest_output(output)
    if timed_out and not parsed["failing_tests"]:
        # Synthesize a failure for run_failure to pick up
        first_mod = modules[0] if modules else "test_chunk"
        synth = f"ERROR: test_chunk_timeout ({first_mod}.TimeoutError)"
        parsed["failing_tests"].append(synth)
        parsed["errors"] += 1

    return {
        "modules": modules,
        "returncode": rc,
        "elapsed": elapsed,
        "timed_out": timed_out,
        "output": output,
        "parsed": parsed,
    }


def format_summary(
    total_tests,
    total_failures,
    total_errors,
    total_skipped,
    total_time,
    chunk_timings,
    all_failing_tests,
):
    """Format the final chunked runner summary string."""
    lines = []
    lines.append("=" * 70)
    lines.append("CHUNKED TEST RUNNER SUMMARY")
    lines.append("=" * 70)
    lines.append(
        f"Total tests run: {total_tests} | "
        f"Failures: {total_failures} | "
        f"Errors: {total_errors} | "
        f"Skipped: {total_skipped} | "
        f"Time: {total_time:.2f}s"
    )
    lines.append("-" * 70)

    # Slowest 10 modules
    sorted_timings = sorted(chunk_timings.items(), key=lambda x: x[1], reverse=True)
    slowest_10 = sorted_timings[:10]
    lines.append("Slowest modules / chunks (top 10):")
    for name, dur in slowest_10:
        lines.append(f"  {dur:6.2f}s - {name}")
    lines.append("-" * 70)

    # Failing tests list
    if all_failing_tests:
        lines.append(f"Failing tests ({len(all_failing_tests)}):")
        for fail_line in all_failing_tests:
            lines.append(f"  {fail_line}")
        lines.append("-" * 70)
        lines.append("RESULT: FAILED")
    else:
        lines.append("RESULT: PASSED")
    lines.append("=" * 70)

    return "\n".join(lines)


def run_all_chunked(
    tests_dir=None,
    chunk_size=DEFAULT_CHUNK_SIZE,
    timeout_per_chunk=DEFAULT_TIMEOUT_PER_CHUNK,
    env=None,
    runner=subprocess.run,
    log_func=print,
):
    """Discover all tests, run in chunks, print per-chunk status and final summary.

    Returns:
        int: exit code (0 if all passed, non-zero if any failed or timed out)
    """
    run_env = dict(os.environ if env is None else env)
    run_env.setdefault("CHILLI_TEST", "1")
    # Same import paths as "python3 -m unittest discover -s tests": the repo root AND the tests
    # folder (several tests import shared helpers such as test_office_controls by plain name).
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    t_dir = os.path.abspath(tests_dir) if tests_dir else os.path.join(repo_root, "tests")
    extra = [repo_root, t_dir]
    old_pp = run_env.get("PYTHONPATH", "")
    run_env["PYTHONPATH"] = os.pathsep.join(extra + ([old_pp] if old_pp else []))

    modules = discover_test_modules(tests_dir)
    if not modules:
        log_func("No tests found to run.")
        return 0

    chunks = chunk_list(modules, chunk_size)
    total_chunks = len(chunks)
    log_func(
        f"[run_tests_chunked] Discovered {len(modules)} test modules across "
        f"{total_chunks} chunks (chunk_size={chunk_size}, timeout={timeout_per_chunk}s/chunk)."
    )

    total_tests = 0
    total_failures = 0
    total_errors = 0
    total_skipped = 0
    all_failing_tests = []
    chunk_timings = {}

    start_total = time.time()
    any_failed = False

    for idx, chk in enumerate(chunks, 1):
        chunk_name = f"chunk_{idx:02d}_{chk[0].split('.')[-1]}"
        log_func(f"[Chunk {idx}/{total_chunks}] Running {len(chk)} modules...")

        res = run_chunk(
            chk,
            timeout=timeout_per_chunk,
            env=run_env,
            runner=runner,
            log_func=log_func,
        )

        chunk_timings[chunk_name] = res["elapsed"]
        parsed = res["parsed"]
        total_tests += parsed["run"]
        total_failures += parsed["failures"]
        total_errors += parsed["errors"]
        total_skipped += parsed["skipped"]

        if res["returncode"] != 0 or res["timed_out"] or parsed["failures"] > 0 or parsed["errors"] > 0:
            any_failed = True
            log_func(
                f"[Chunk {idx}/{total_chunks}] FAILED in {res['elapsed']:.2f}s "
                f"(rc={res['returncode']}, timed_out={res['timed_out']}, "
                f"failures={parsed['failures']}, errors={parsed['errors']})"
            )
            # Print chunk output if there was a problem so details are visible in CI
            if res["output"]:
                for line in res["output"].splitlines():
                    log_func(line)
        else:
            log_func(
                f"[Chunk {idx}/{total_chunks}] OK in {res['elapsed']:.2f}s "
                f"({parsed['run']} tests, {parsed['skipped']} skipped)"
            )

        if parsed["failing_tests"]:
            all_failing_tests.extend(parsed["failing_tests"])

    total_elapsed = time.time() - start_total
    summary_text = format_summary(
        total_tests=total_tests,
        total_failures=total_failures,
        total_errors=total_errors,
        total_skipped=total_skipped,
        total_time=total_elapsed,
        chunk_timings=chunk_timings,
        all_failing_tests=all_failing_tests,
    )
    log_func(summary_text)

    # For run_failure compatibility: print explicit FAIL: and ERROR: lines so regex picks them up
    if all_failing_tests:
        for f in all_failing_tests:
            log_func(f)

    return 1 if (any_failed or all_failing_tests) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chilli Gavva chunked test runner")
    parser.add_argument(
        "--tests-dir",
        default=None,
        help="Path to tests directory (default: repo tests/)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Number of test modules per chunk (default: {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_PER_CHUNK,
        help=f"Per-chunk timeout in seconds (default: {DEFAULT_TIMEOUT_PER_CHUNK})",
    )
    args = parser.parse_args(argv)

    return run_all_chunked(
        tests_dir=args.tests_dir,
        chunk_size=args.chunk_size,
        timeout_per_chunk=args.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
