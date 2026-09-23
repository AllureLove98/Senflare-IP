#!/bin/sh
# =====================================================================
# Senflare-IP 结果推送脚本（GitHub）
# ---------------------------------------------------------------------
# 把 IPtest.py 生成的输出文件提交并推送到 GitHub 的独立结果分支。
# 全程在一个独立的本地仓库目录里操作 git，只包含输出文件，
# **绝不触碰 main 代码分支 / 代码文件**。
#
# 使用场景：
#   1) 自动：容器内由 entrypoint.sh 每轮采集完成后调用（--auto 模式，遵循
#      GIT_PUSH_ENABLED 开关）。
#   2) 手动：随时执行，用于补推结果、换分支推送、排查推送问题。
#      手动模式默认忽略 GIT_PUSH_ENABLED 开关（因为手动执行本身就是明确意图）。
#
# 用法：
#   sh /app/push_results.sh [选项]
#
# 选项：
#   -n, --dry-run          只检查并打印将要做的操作，不执行任何 git 写操作
#   -f, --force            推送失败时允许强制推送（覆盖远端结果分支）
#   -b, --branch <名称>    目标结果分支（默认 GIT_RESULT_BRANCH，否则 results）
#   -m, --message <文本>   提交信息前缀（默认 COMMIT_MESSAGE，自动追加 UTC 时间）
#   -r, --repo-url <URL>   直接指定远端仓库地址（优先于 GITHUB_REPOSITORY 拼接）
#       --app-dir <目录>   结果文件所在目录（默认脚本所在目录）
#       --results-dir <目录> 本地结果仓库目录（默认 <app-dir>/results-repo）
#       --files "<列表>"   要同步的文件（空格分隔，支持通配符，覆盖默认列表）
#       --auto             自动模式：遵循 GIT_PUSH_ENABLED 开关（entrypoint.sh 用）
#       --no-config        不读取 config.json 的 env 区块
#   -h, --help             显示本帮助
#
# 示例：
#   sh /app/push_results.sh -n                      # 只检查，不推送
#   sh /app/push_results.sh                         # 手动推送结果
#   sh /app/push_results.sh -b results-test         # 推到另一个结果分支
#   sh /app/push_results.sh -f -m "手动补推"        # 允许强推 + 自定义提交信息
#
# 退出码：0 成功/跳过/无变更  1 执行失败  2 参数错误
# =====================================================================

LOG_PREFIX="[push_results]"
log() { echo "$LOG_PREFIX $*"; }
warn() { echo "$LOG_PREFIX $*" >&2; }

usage() {
  # 打印文件顶部的注释块（shebang 之后、第一个非注释行之前的全部内容）
  awk 'NR > 1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "$0"
}

# 隐藏 URL 中的凭据，避免令牌被打印到日志
mask_url() {
  printf '%s' "$1" | sed -E 's#(//[^/@]*):[^@]+@#\1:***@#'
}

# ── 解析命令行参数 ──────────────────────────────────────────────
DRY_RUN=0
FORCE=0
AUTO_MODE=0
NO_CONFIG=0
BRANCH_OPT=""
MESSAGE_OPT=""
REPO_URL_OPT=""
APP_DIR_OPT=""
RESULTS_DIR_OPT=""
FILES_OPT=""

while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run)       DRY_RUN=1 ;;
    -f|--force)         FORCE=1 ;;
    --auto)             AUTO_MODE=1 ;;
    --no-config)        NO_CONFIG=1 ;;
    -b|--branch)        BRANCH_OPT="${2:-}"; shift ;;
    -m|--message)       MESSAGE_OPT="${2:-}"; shift ;;
    -r|--repo-url)      REPO_URL_OPT="${2:-}"; shift ;;
    --app-dir)          APP_DIR_OPT="${2:-}"; shift ;;
    --results-dir)      RESULTS_DIR_OPT="${2:-}"; shift ;;
    --files)            FILES_OPT="${2:-}"; shift ;;
    -h|--help)          usage; exit 0 ;;
    *)                  warn "未知选项: $1"; usage; exit 2 ;;
  esac
  shift
done

# ── 定位目录（容器内 /app，宿主机则是脚本所在目录）────────────────
# 统一转成绝对路径：后面会 cd 到结果仓库目录，相对路径会失效
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR="${APP_DIR_OPT:-${APP_DIR:-$SCRIPT_DIR}}"
if [ -d "$APP_DIR" ]; then
  APP_DIR=$(CDPATH= cd -- "$APP_DIR" && pwd) || true
fi

RESULTS_DIR="${RESULTS_DIR_OPT:-${RESULTS_DIR:-$APP_DIR/results-repo}}"
if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$RESULTS_DIR" || { warn "无法创建结果仓库目录: $RESULTS_DIR"; exit 1; }
fi
if [ -d "$RESULTS_DIR" ]; then
  RESULTS_DIR=$(CDPATH= cd -- "$RESULTS_DIR" && pwd) || true
fi

# 默认同步的输出文件（支持通配符；不存在的文件自动跳过）
DEFAULT_FILES="IPlist.txt Senflare.txt IPlist-Pro.txt Senflare-Pro.txt Ranking.txt Region-All.txt Region-*.txt Cache.json IPtest.log"
RESULT_FILES="${FILES_OPT:-${RESULT_FILES:-$DEFAULT_FILES}}"

# ── 加载 config.json 的 env 区块（已设置的环境变量优先）──────────
load_config_env() {
  config_file="$1"
  [ -f "$config_file" ] || return 0
  PYTHON_BIN=$(command -v python || command -v python3 || true)
  if [ -z "$PYTHON_BIN" ]; then
    warn "未找到 python，跳过读取 $config_file 的 env 区块"
    return 0
  fi
  eval "$(CONFIG_FILE="$config_file" "$PYTHON_BIN" - <<'PY'
import json, os, shlex
path = os.environ.get('CONFIG_FILE', 'config.json')
try:
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
    env_block = cfg.get('env') or {}
except Exception as e:
    print(f'echo "[push_results] 读取 {path} 的 env 区块失败: {e}"')
    raise SystemExit(0)

for key, value in env_block.items():
    # 跳过注释键（config.example.json 中以 // 开头的键）与已设置的变量
    if key.startswith('//') or value is None or os.environ.get(key):
        continue
    if isinstance(value, bool):
        value = 'true' if value else 'false'
    print(f'export {key}={shlex.quote(str(value))}')
PY
)"
}

if [ "$NO_CONFIG" -eq 0 ]; then
  load_config_env "$APP_DIR/config.json"
fi

# ── 计算最终参数 ────────────────────────────────────────────────
BRANCH="${BRANCH_OPT:-${GIT_RESULT_BRANCH:-results}}"
MESSAGE_BASE="${MESSAGE_OPT:-${COMMIT_MESSAGE:-Update IP results}}"

if [ "$AUTO_MODE" -eq 1 ]; then
  if [ "${GIT_PUSH_ENABLED:-false}" != "true" ]; then
    log "自动模式：GIT_PUSH_ENABLED 未开启，跳过推送（手动执行本脚本可忽略该开关）"
    exit 0
  fi
fi

if [ -n "$REPO_URL_OPT" ]; then
  REMOTE_URL="$REPO_URL_OPT"
elif [ -n "${REPO_URL:-}" ]; then
  REMOTE_URL="$REPO_URL"
elif [ -n "${GITHUB_REPOSITORY:-}" ]; then
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    REMOTE_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
  else
    REMOTE_URL="https://github.com/${GITHUB_REPOSITORY}.git"
  fi
else
  warn "未配置推送目标：请设置 GITHUB_REPOSITORY（或 GITHUB_TOKEN / REPO_URL），或使用 -r 指定仓库地址"
  exit 1
fi

log "模式: $([ "$AUTO_MODE" -eq 1 ] && echo 自动 || echo 手动)$([ "$DRY_RUN" -eq 1 ] && echo ' + dry-run')"
log "结果目录: $APP_DIR"
log "结果仓库: $RESULTS_DIR"
log "远端仓库: $(mask_url "$REMOTE_URL")"
log "目标分支: $BRANCH"

# 收集待同步文件（通配符展开后只保留真实存在的文件）
# 注意：必须返回**绝对路径**，因为后续会 cd 到结果仓库目录再复制
collect_files() {
  src_dir=$(CDPATH= cd -- "$1" 2>/dev/null && pwd) || return 0
  out=""
  for name in $RESULT_FILES; do
    for f in "$src_dir"/$name; do
      if [ -f "$f" ]; then
        out="$out $f"
      fi
    done
  done
  printf '%s' "$out"
}

PENDING_FILES=$(collect_files "$APP_DIR")
if [ -z "$PENDING_FILES" ]; then
  warn "在 $APP_DIR 下没有找到任何结果文件，无需推送"
  exit 0
fi

# ── dry-run：只检查与展示 ───────────────────────────────────────
if [ "$DRY_RUN" -eq 1 ]; then
  log "将同步以下文件:"
  for f in $PENDING_FILES; do
    log "  $(basename "$f")"
  done
  if command -v git >/dev/null 2>&1; then
    log "测试远端连通性（git ls-remote）..."
    if git ls-remote --heads "$REMOTE_URL" "$BRANCH" >/dev/null 2>&1; then
      log "远端可访问，分支 '$BRANCH' 存在（或仓库可读）"
    else
      warn "无法访问远端分支 '$BRANCH'（令牌/网络/分支不存在？仅提示，dry-run 不视为失败）"
    fi
  else
    warn "未安装 git"
  fi
  log "dry-run 结束，未做任何修改"
  exit 0
fi

# ── 前置检查 ────────────────────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
  warn "未安装 git，无法推送"
  exit 1
fi

# ── 初始化/对齐本地结果仓库 ─────────────────────────────────────
mkdir -p "$RESULTS_DIR" || exit 1
cd "$RESULTS_DIR" || exit 1

if [ ! -d .git ]; then
  log "初始化结果仓库: $RESULTS_DIR"
  git init >/dev/null 2>&1 || { warn "git init 失败"; exit 1; }
fi

# remote 每次都对：防止重建容器后 remote 丢失或地址变化
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL" >/dev/null 2>&1 || true
else
  git remote add origin "$REMOTE_URL" >/dev/null 2>&1 || true
fi

git config user.name "${GIT_USER_NAME:-GitHub Action}"
git config user.email "${GIT_USER_EMAIL:-action@github.com}"
git config --global --add safe.directory "$RESULTS_DIR" >/dev/null 2>&1 || true

# 确保在结果分支上（-B：不存在则创建，存在则切换）
if [ "$(git branch --show-current 2>/dev/null)" != "$BRANCH" ]; then
  git checkout -B "$BRANCH" >/dev/null 2>&1 || true
fi

# 以远端为基准对齐（reset --hard 彻底避免 fetch first 冲突）
git fetch origin "$BRANCH" >/dev/null 2>&1 || true
if git rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1; then
  git reset --hard "origin/$BRANCH" >/dev/null 2>&1 || true
fi

# ── 同步最新结果文件（必须在 reset 之后，用新结果覆盖旧结果）────
for f in $PENDING_FILES; do
  cp -f "$f" "$RESULTS_DIR/" 2>/dev/null || warn "复制失败: $f"
done

# ── 提交 ────────────────────────────────────────────────────────
git add -A >/dev/null 2>&1 || true
if git diff --cached --quiet 2>/dev/null; then
  log "结果无变化，无需提交（已是最新）"
  exit 0
fi

log "本次变更:"
git diff --cached --name-only | sed "s/^/$LOG_PREFIX   /"

UTC_TIME=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
COMMIT_MSG="${MESSAGE_BASE} ${UTC_TIME}"
if ! git commit -m "$COMMIT_MSG" >/dev/null 2>&1; then
  warn "提交失败"
  exit 1
fi
log "已提交: $COMMIT_MSG"

# ── 推送 ────────────────────────────────────────────────────────
log "推送到 '$BRANCH' ..."
if git push origin "HEAD:$BRANCH"; then
  log "推送成功: $(mask_url "$REMOTE_URL") -> $BRANCH"
  exit 0
fi

if [ "$FORCE" -eq 1 ]; then
  warn "普通推送失败，尝试强制推送..."
  if git push -f origin "HEAD:$BRANCH"; then
    log "强制推送成功: $BRANCH"
    exit 0
  fi
fi

warn "推送失败（可加 -f/--force 强制推送，或检查令牌权限与网络）"
exit 1
