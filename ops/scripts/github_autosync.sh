#!/bin/sh
set -eu

PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO_DIR="${REPO_DIR:-/Users/mortenisachsen/Documents/TW_Service_Order_System}"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"
LOCK_DIR="/tmp/com.tomwood.sos-github-autosync.lock"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi

cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "Missing git repo: $REPO_DIR"
  exit 1
fi

cd "$REPO_DIR"

if [ -z "$(git status --porcelain)" ]; then
  exit 0
fi

git add -A

if git diff --cached --quiet; then
  exit 0
fi

STAMP="$(date '+%Y-%m-%d %H:%M:%S %z')"
git commit -m "Auto-sync from Codex $STAMP"

if ! git push "$REMOTE" "$BRANCH"; then
  git pull --rebase "$REMOTE" "$BRANCH"
  git push "$REMOTE" "$BRANCH"
fi
