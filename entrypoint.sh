#!/bin/sh
set -eu

cd /app

# 从 config.json 的 env 区块加载运行参数（如 GITHUB_TOKEN 等）
# 优先级：已设置的环境变量 > config.json 中的 env 区块
if [ -f /app/config.json ]; then
  eval "$(python - <<'PY'
import json, os, shlex
try:
    with open('/app/config.json', encoding='utf-8') as f:
        cfg = json.load(f)
    env_block = cfg.get('env') or {}
    for key, value in env_block.items():
        if value is None or os.environ.get(key) is not None:
            continue
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        print(f'export {key}={shlex.quote(str(value))}')
except Exception as e:
    print(f'echo "[entrypoint] 读取 config.json env 区块失败: {e}"')
PY
)"
fi

INTERVAL_SECONDS="${RUN_INTERVAL_SECONDS:-10800}"
LOG_PREFIX="[entrypoint]"

run_cycle() {
  cd /app
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

  # ===== 推送结果到独立分支（避免与 main 代码分支冲突）=====
  # 默认推送到 results 分支，可用环境变量 GIT_RESULT_BRANCH 修改
  GIT_RESULT_BRANCH="${GIT_RESULT_BRANCH:-results}"

  # 开关：未显式启用则跳过推送
  if [ "${GIT_PUSH_ENABLED:-false}" != "true" ]; then
    echo "$LOG_PREFIX Git push disabled (set GIT_PUSH_ENABLED=true to enable); skipping upload"
    return 0
  fi

  if [ -n "${REPO_URL:-}" ]; then
    remote_url="$REPO_URL"
  elif [ -n "${GITHUB_REPOSITORY:-}" ]; then
    remote_url="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
  else
    echo "$LOG_PREFIX No GitHub token or repo URL configured; skipping upload"
    return 0
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "$LOG_PREFIX Git is not installed in this container; skipping upload"
    return 0
  fi

  # 独立结果仓库：只包含输出文件，绝不触碰 /app 代码文件
  RESULTS_DIR="/app/results-repo"
  mkdir -p "$RESULTS_DIR"
  cd "$RESULTS_DIR"

  # 初始化结果仓库（仅首次）
  if [ ! -d .git ]; then
    echo "$LOG_PREFIX Initializing results repository in $RESULTS_DIR"
    git init >/dev/null 2>&1 || true
  fi
  # 确保 remote 存在（每次运行都执行，防止重建容器后 remote 丢失）
  git remote add origin "$remote_url" >/dev/null 2>&1 || git remote set-url origin "$remote_url"
  git config user.name "${GIT_USER_NAME:-GitHub Action}"
  git config user.email "${GIT_USER_EMAIL:-action@github.com}"
  git config --global --add safe.directory "$RESULTS_DIR" >/dev/null 2>&1 || true

  # 确保在结果分支上（-B：不存在则创建，存在则切换）
  if [ "$(git branch --show-current 2>/dev/null)" != "$GIT_RESULT_BRANCH" ]; then
    git checkout -B "$GIT_RESULT_BRANCH" >/dev/null 2>&1 || true
  fi

  # 以远端为基准对齐（reset --hard 彻底避免 fetch first 冲突）
  git fetch origin "$GIT_RESULT_BRANCH" >/dev/null 2>&1 || true
  if git rev-parse --verify "origin/$GIT_RESULT_BRANCH" >/dev/null 2>&1; then
    git reset --hard "origin/$GIT_RESULT_BRANCH" >/dev/null 2>&1 || true
  fi

  # 同步最新输出文件（必须在 reset 之后，用新结果覆盖旧结果）
  for file in IPlist.txt Senflare.txt IPlist-Pro.txt Senflare-Pro.txt Ranking.txt Cache.json IPtest.log; do
    if [ -f "/app/$file" ]; then
      cp -f "/app/$file" "$RESULTS_DIR/" 2>/dev/null || true
    fi
  done

  # 提交并推送
  git add -A >/dev/null 2>&1 || true
  if git diff --cached --quiet 2>/dev/null; then
    echo "$LOG_PREFIX No changes to commit; results up to date"
    return 0
  fi

  UTC_TIME=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
  git commit -m "${COMMIT_MESSAGE:-Update IP results} ${UTC_TIME}" >/dev/null 2>&1 || {
    echo "$LOG_PREFIX Commit failed"
    return 0
  }

  echo "$LOG_PREFIX Pushing results to branch '$GIT_RESULT_BRANCH'"
  git push origin "HEAD:$GIT_RESULT_BRANCH" 2>/dev/null \
    || git push -f origin "HEAD:$GIT_RESULT_BRANCH" 2>/dev/null \
    || echo "$LOG_PREFIX Push to '$GIT_RESULT_BRANCH' failed"
}

while true; do
  echo "$LOG_PREFIX Starting cycle; next run in ${INTERVAL_SECONDS}s"
  run_cycle
  echo "$LOG_PREFIX Sleeping for ${INTERVAL_SECONDS}s"
  sleep "$INTERVAL_SECONDS"
done