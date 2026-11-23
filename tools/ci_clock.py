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
        with ThreadPoolExecutor(max_workers=4) as pool:
            for module, result in pool.map(run, modules):
                print(module + ': ' + ('OK' if result.returncode == 0 else 'FAIL'), flush=True)
                if result.returncode:
                    print(result.stdout, flush=True)
                    ok = False
        return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
