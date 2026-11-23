"""Test-only process clock injection; no host clock or network changes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main(modules):
    if os.environ.get('CHILLI_TEST') != '1' or not os.environ.get('CI_PARITY_NOW'):
        raise SystemExit('Clock injection requires CHILLI_TEST=1 and CI_PARITY_NOW')
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix='parity-clock-') as temp:
        Path(temp, 'sitecustomize.py').write_text(
            'import os, datetime\n'
            'if os.environ.get("CHILLI_TEST") == "1" and os.environ.get("CI_PARITY_NOW"):\n'
            '    from tools.robot_sim_world import install_clock\n'
            '    install_clock(datetime.datetime.fromisoformat(os.environ["CI_PARITY_NOW"]).timestamp())\n')
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([temp, str(root), str(root / 'tests')]))
        from concurrent.futures import ThreadPoolExecutor
        def run(module):
            result = subprocess.run([sys.executable, '-m', 'unittest', module], cwd=root, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900)
            return module, result
        ok = True
        count = failures = errors = skipped = 0
        from tools.run_tests_chunked import parse_unittest_output
        with ThreadPoolExecutor(max_workers=4) as pool:
            for module, result in pool.map(run, modules):
                print(module + ': ' + ('OK' if result.returncode == 0 else 'FAIL'), flush=True)
                stats = parse_unittest_output(result.stdout)
                count += stats['run']; failures += stats['failures']
                errors += stats['errors']; skipped += stats['skipped']
                if result.returncode:
                    print(result.stdout, flush=True)
                    ok = False
        print(f'Ran {count} tests; failures={failures}; errors={errors}; skipped={skipped}', flush=True)
        print('OK' if ok else 'FAILED', flush=True)
        return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
