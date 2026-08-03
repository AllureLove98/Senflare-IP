#!/bin/sh
set -eu

cd /app

INTERVAL_SECONDS="${RUN_INTERVAL_SECONDS:-10800}"
LOG_PREFIX="[entrypoint]"

run_cycle() {
  echo "$LOG_PREFIX Running IPtest.py"
  if ! python /app/IPtest.py; then
    echo "$LOG_PREFIX IPtest.py exited with an error; continuing after the interval"
  fi

  echo "$LOG_PREFIX Checking generated output files"
  for file in IPlist.txt Senflare.txt IPlist-Pro.txt Senflare-Pro.txt Ranking.txt Cache.json IPtest.log; do
    if [ -f "$file" ]; then
      echo "$LOG_PREFIX found $file"
    fi
  done

  # 若挂载了 /app/output 目录，则同步结果文件到宿主机（方便查看，不影响 git push）
  if [ -d /app/output ]; then
    echo "$LOG_PREFIX Syncing output files to /app/output"
    cp -f IPlist.txt Senflare.txt IPlist-Pro.txt Senflare-Pro.txt Ranking.txt Cache.json IPtest.log /app/output/ 2>/dev/null || true
  fi

  if [ "${GIT_PUSH_ENABLED:-false}" != "true" ]; then
    echo "$LOG_PREFIX Git push disabled; skipping upload"
    return 0
  fi

  if [ -z "${GITHUB_TOKEN:-}" ] && [ -z "${REPO_URL:-}" ]; then
    echo "$LOG_PREFIX No GitHub token or repo URL configured; skipping upload"
    return 0
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "$LOG_PREFIX Git is not installed in this container; skipping upload"
    return 0
  fi

  if [ ! -d .git ]; then
    echo "$LOG_PREFIX No git repository found; initializing one in /app"
    git init >/dev/null 2>&1 || true
  fi

  if [ ! -d .git ]; then
    echo "$LOG_PREFIX Git repository still unavailable; skipping upload"
    return 0
  fi

  git config --global --add safe.directory /app || true

  git config user.name "${GIT_USER_NAME:-GitHub Action}"
  git config user.email "${GIT_USER_EMAIL:-action@github.com}"

  if [ -n "${REPO_URL:-}" ]; then
    git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REPO_URL"
    git remote set-url origin "$REPO_URL"
    echo "$LOG_PREFIX Using configured remote URL"
  elif [ -n "${GITHUB_REPOSITORY:-}" ]; then
    remote_url="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
    git remote get-url origin >/dev/null 2>&1 || git remote add origin "$remote_url"
    git remote set-url origin "$remote_url"
    echo "$LOG_PREFIX Configured GitHub remote for ${GITHUB_REPOSITORY}"
  fi

  git add IPlist.txt Senflare.txt IPlist-Pro.txt Senflare-Pro.txt Ranking.txt Cache.json IPtest.log 2>/dev/null || true

  if git diff --cached --quiet; then
    echo "$LOG_PREFIX No changes to commit"
    return 0
  fi

  UTC_TIME=$(date -u '+%Y-%m-%d %H:%M:%S UTC')

  if git rev-parse --verify HEAD >/dev/null 2>&1; then
    git commit -m "${COMMIT_MESSAGE:-Update IP results} ${UTC_TIME}"
  else
    git commit -m "${COMMIT_MESSAGE:-Initial IP results} ${UTC_TIME}"
  fi

  git push origin HEAD:${GITHUB_REF_NAME:-main}
}

while true; do
  echo "$LOG_PREFIX Starting cycle; next run in ${INTERVAL_SECONDS}s"
  run_cycle
  echo "$LOG_PREFIX Sleeping for ${INTERVAL_SECONDS}s"
  sleep "$INTERVAL_SECONDS"
done