"""Fail-safe full-suite decision from Git ancestry and a narrow state-file allowlist."""
import argparse
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def state_only(path):
    return (path in {'agent_data.db.enc','products.tar.gz.enc','web/data/status.json','README.md','.scheduler_locks'}
            or path.startswith('.scheduler_locks/') or path.startswith('docs/') and path.endswith('.md'))


def plan(repo=ROOT,baseline='',event='',reason='',force='no'):
    def git(*args):
        return subprocess.run(['git',*args],cwd=repo,capture_output=True,check=False)
    result=git('rev-parse','HEAD')
    head=result.stdout.decode().strip() if result.returncode==0 else ''
    if force.strip().lower() in {'yes','true','1'}:return head,True
    eligible=event=='schedule' or event=='workflow_dispatch' and reason in {'doorbell','manual'}
    if not eligible or not re.fullmatch('[0-9a-f]{40}',baseline) or not head:return head,True
    if git('merge-base','--is-ancestor',baseline,head).returncode!=0:return head,True
    diff=git('diff','--name-only','--no-renames','-z',baseline,head)
    if diff.returncode!=0:return head,True
    paths=diff.stdout.decode('utf-8',errors='replace').split('\0')
    return head,any(not state_only(path) for path in paths if path)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',default=str(ROOT))
    parser.add_argument('--baseline-file',required=True)
    parser.add_argument('--event',default='')
    parser.add_argument('--reason',default='')
    parser.add_argument('--force',default='no')
    args=parser.parse_args(argv)
    try:baseline=Path(args.baseline_file).read_text().strip()
    except OSError:baseline=''
    try:head,run=plan(args.repo,baseline,args.event,args.reason,args.force)
    except (OSError,ValueError):head,run='',True
    output=f'head={head}\nrun={str(run).lower()}\n'
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as handle:handle.write(output)
    print(output,end='')
    print('Full unit suite required: '+str(run).lower())
    return 0


if __name__=='__main__':
    raise SystemExit(main())
