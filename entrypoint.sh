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
        # 跳过注释键（config.example.json 中以 // 开头的键，如 "// LOG_LEVEL"）
        if key.startswith('//') or value is None or os.environ.get(key):
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

  # ===== 推送结果到 GitHub（独立分支，避免与 main 代码分支冲突）=====
  # 推送逻辑已拆分为独立脚本 push_results.sh，可以单独手动执行：
  #   sh /app/push_results.sh        # 手动推送（忽略 GIT_PUSH_ENABLED 开关）
  #   sh /app/push_results.sh -n     # dry-run：只检查不推送
  #   sh /app/push_results.sh -h     # 查看全部参数
  PUSH_SCRIPT="/app/push_results.sh"
  if [ ! -f "$PUSH_SCRIPT" ]; then
    PUSH_SCRIPT="$(dirname "$0")/push_results.sh"
  fi

  if [ ! -f "$PUSH_SCRIPT" ]; then
    echo "$LOG_PREFIX push_results.sh not found; skipping upload"
    return 0
  fi

  # --auto：遵循 GIT_PUSH_ENABLED 开关；--force：推送失败时强制推送兜底
  echo "$LOG_PREFIX Running push_results.sh (auto mode)"
  if ! sh "$PUSH_SCRIPT" --auto --force; then
    echo "$LOG_PREFIX push_results.sh exited with an error; continuing after the interval"
  fi
}

while true; do
  echo "$LOG_PREFIX Starting cycle; next run in ${INTERVAL_SECONDS}s"
  run_cycle
  echo "$LOG_PREFIX Sleeping for ${INTERVAL_SECONDS}s"
  sleep "$INTERVAL_SECONDS"
done