#!/usr/bin/env bash
# DeepQuant 多终端同步脚本（Linux / macOS / WSL）。
#
# 用法：
#   scripts/git-sync.sh "提交说明"      # 省略说明时使用时间戳
#
# 行为：git add -A → 有改动则 commit → git pull --rebase --autostash → git push
# 认证：若本机已安装并登录 gh，则让 git 复用 gh 凭据（无需把令牌写入任何文件）；
#       否则使用本机已配置的凭据助手（如 Git Credential Manager）。
# 安全：绝不 force push；rebase 出现冲突时停止，需人工解决后再运行。
set -euo pipefail

# 切到仓库根目录（脚本位于 scripts/ 下）
cd "$(dirname "$0")/.."

msg="${1:-sync: $(date '+%Y-%m-%d %H:%M:%S')}"

# 认证：优先复用已登录的 gh 凭据（仅本次命令，不持久化配置）
git_auth=()
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  git_auth=(-c "credential.helper=!gh auth git-credential")
fi

git add -A

if git diff --cached --quiet; then
  echo "[git-sync] 无变更需要提交"
else
  git commit -m "$msg"
fi

if ! git "${git_auth[@]}" pull --rebase --autostash; then
  echo "[git-sync] git pull --rebase 失败：如有冲突请手动解决后重试；如为认证失败，请先运行 'gh auth login'" >&2
  exit 1
fi

if ! git "${git_auth[@]}" push; then
  echo "[git-sync] git push 失败：请检查网络与 GitHub 认证（gh auth status）" >&2
  exit 1
fi

echo "[git-sync] 已同步到 origin/$(git rev-parse --abbrev-ref HEAD)"
