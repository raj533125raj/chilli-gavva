"""Run the GitHub suite in one isolated checkout without owner state or packages."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CLOCKS = ('2026-11-23T19:45:00+00:00', '2026-11-23T01:15:00+00:00',
          '2026-11-23T07:00:00+00:00')


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd)


def private_state(name):
    return (Path(name).name.startswith('agent_data.db') or name in
            ('products.tar.gz.enc', 'web/data/status.json') or name.startswith('.scheduler_locks/'))


def sync_checkout(work):
    work = Path(work).resolve()
    if work == ROOT or ROOT in work.parents:
        raise ValueError('Parity workspace must be outside the repository')
    work.mkdir(parents=True, exist_ok=True)
    marker = work / 'source.json'
    if marker.exists() and json.loads(marker.read_text()) != str(ROOT):
        raise ValueError('Workspace belongs to another source')
    repo = work / 'repo'
    if repo.exists():
        origin = git('remote', 'get-url', 'origin', cwd=repo).decode().strip()
        if Path(origin).resolve() != ROOT:
            raise ValueError('Refusing to overwrite an unrelated checkout')
    else:
        subprocess.run(['git', 'clone', '--local', '--no-hardlinks', '--quiet', str(ROOT), str(repo)], check=True)
    marker.write_text(json.dumps(str(ROOT)))
    git('fetch', '--quiet', str(ROOT), 'HEAD', cwd=repo)
    git('update-ref', 'HEAD', git('rev-parse', 'HEAD').decode().strip(), cwd=repo)
    git('read-tree', 'HEAD', cwd=repo)
    names = git('ls-files', '-z').decode().split('\0')
    names += git('ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    for name in names:
        if not name or name.startswith(('.jobs/', '.git/')):
            continue
        dest = repo / name
        if private_state(name):
            result = subprocess.run(['git', 'show', 'HEAD:' + name], cwd=ROOT, capture_output=True)
            if result.returncode == 0:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(result.stdout)
        elif (ROOT / name).is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)
        elif dest.is_file():
            dest.unlink()
    return repo


def sensitive_modules(repo):
    """Discover clock/calendar users throughout the test tree, including helpers."""
    modules = []
    for path in sorted((Path(repo) / 'tests').glob('test_*.py')):
        tree = ast.parse(path.read_text())
        if any(isinstance(n, ast.Call) and (
            isinstance(n.func, ast.Attribute) and n.func.attr in
            {'now', 'utcnow', 'today', 'time', 'strftime', 'localtime', 'gmtime', 'timestamp', 'process_time', 'perf_counter'} or
            isinstance(n.func, ast.Attribute) and re.search(r'(today|now|clock|daily|morning|due)', n.func.attr) or
            isinstance(n.func, ast.Name) and re.search(r'(today|now|clock|daily|morning|due)', n.func.id)
        ) for n in ast.walk(tree)) or path.name == 'test_cxaa_clock_safety.py':
            modules.append('tests.' + path.stem)
    return modules


def clean_env(work, python):
    home, tmp = Path(work) / 'home', Path(work) / 'tmp'
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    return dict(HOME=str(home), TMPDIR=str(tmp), PATH=os.pathsep.join(
        [str(python.parent), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin']),
        TZ='UTC', CHILLI_TEST='1', GITHUB_ACTIONS='true', CI='true',
        PYTHONNOUSERSITE='1', PYTHONUNBUFFERED='1', PYTHONHASHSEED='0', CHILLI_TEST_JOBS='4', LANG='en_US.UTF-8')


def run_checked(cmd, env, cwd, log):
    with Path(log).open('w') as stream:
        result = subprocess.run(['env', '-i', *[k+'='+v for k,v in env.items()], *cmd],
                                cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
    return result.returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default='/private/tmp/chilli-ci-parity')
    parser.add_argument('--modules', nargs='+', help='Focused unittest modules; always includes the three guards')
    args = parser.parse_args(argv)
    work = Path(args.workspace).resolve()
    try:
        repo = sync_checkout(work)
        venv = work / 'venv'
        python = venv / 'bin/python3'
        if not python.exists():
            subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
        workflow = (ROOT / '.github/workflows/agent.yml').read_text()
        pillow = re.search(r'Pillow==[0-9.]+', workflow).group()
        subprocess.run([str(python), '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', pillow], check=True)
        hosted = work / 'hostedtoolcache/Python/3.11/x64/bin/python3'
        hosted.parent.mkdir(parents=True, exist_ok=True)
        if not hosted.is_symlink():
            hosted.symlink_to(python)
        shutil.copy2(venv / 'pyvenv.cfg', hosted.parent.parent / 'pyvenv.cfg')
        library = hosted.parent.parent / 'lib'
        if not library.exists():
            library.symlink_to(venv / 'lib', target_is_directory=True)
        env = clean_env(work, hosted)
        env["PYTHONPATH"] = os.pathsep.join((str(repo), str(repo / "tests")))
        subprocess.run([str(hosted), '-c', 'import PIL, mockups, sys; assert mockups.available(); assert "hostedtoolcache" in sys.executable; print(sys.executable, "Pillow", PIL.__version__)'],
                       cwd=repo, env=env, check=True)
        subprocess.run([str(hosted), "tools/ci_real_packs.py", str(work / "real-products")],
                       cwd=repo, env=env, check=True)
        if args.modules:
            modules = list(dict.fromkeys(args.modules + ['tests.test_r22_markdown_currency', 'tests.test_secret_guard', 'tests.test_autofix_control_copy']))
            log = work / 'focused.log'
            ok = run_checked([str(hosted), '-m', 'unittest', *modules], env, repo, log)
            subprocess.run([str(hosted), "tools/ci_real_packs.py", "--check", str(work / "real-products")],
                           cwd=repo, env=env, check=True)
            print('\n'.join(log.read_text().splitlines()[-18:]), flush=True)
            print('CI PARITY FOCUSED: ' + ('PASS' if ok else 'FAIL'), flush=True)
            return 0 if ok else 1
        ok = run_checked([str(hosted), 'tools/run_tests_chunked.py', '--jobs', '4'], env, repo, work / 'full.log')
        print('\n'.join((work / 'full.log').read_text().splitlines()[-18:]), flush=True)
        modules = sensitive_modules(repo)
        for moment in CLOCKS:
            log = work / ('clock-' + moment[11:16].replace(':', '') + '.log')
            passed = run_checked([str(hosted), 'tools/ci_clock.py', *modules], dict(env, CI_PARITY_NOW=moment), repo, log)
            print(f'UTC {moment[11:16]}: {len(modules)} clock-sensitive modules; ' +
                  '\n'.join(log.read_text().splitlines()[-5:]), flush=True)
            if not passed:
                print('Details: ' + str(log), flush=True)
            ok = passed and ok
        subprocess.run([str(hosted), "tools/ci_real_packs.py", "--check", str(work / "real-products")],
                       cwd=repo, env=env, check=True)
        print('CI PARITY: ' + ('PASS' if ok else 'FAIL'), flush=True)
        return 0 if ok else 1
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print('Parity setup failed: ' + str(exc), file=sys.stderr)
        print('CI PARITY: FAIL')
        return 1


if __name__ == '__main__':
    sys.exit(main())
