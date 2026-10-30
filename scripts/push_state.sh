#!/usr/bin/env bash
# Used only by CI. Never force-push or replace another writer's state.
set -e -o pipefail
if [ "$#" = 0 ]; then
  echo "No saved paths supplied."
  exit 1
fi
base=$(git rev-parse HEAD~1)
for attempt in 1 2; do
  if git push -q origin HEAD:main; then
    echo "State saved."
    exit 0
  fi
  if [ "$attempt" = 2 ]; then
    echo "Could not save state after a retry."
    exit 1
  fi
  echo "Push refused; checking whether main moved."
  # Use an explicit refspec: FETCH_HEAD alone must not leave origin/main stale.
  git fetch -q origin +refs/heads/main:refs/remotes/origin/main
  remote=$(git rev-parse origin/main)
  if [ "$remote" = "$base" ]; then
    echo "Main did not move; check workflow write permissions or branch rules."
    exit 1
  fi
  if ! git merge-base --is-ancestor "$base" origin/main; then
    echo "Main history changed or is incomplete; refusing to replay state."
    exit 1
  fi
  if ! git diff --quiet "$base" origin/main -- "$@"; then
    echo "Someone else saved newer state while this run worked; not overwriting it."
    exit 1
  fi
  if ! git rebase -q origin/main; then
    git rebase --abort || true
    echo "Could not replay this run's save on the new main."
    exit 1
  fi
done
