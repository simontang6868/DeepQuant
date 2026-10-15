#!/usr/bin/env bash
# DeepQuant 多终端同步脚本（Linux / macOS / WSL）。
#
# 用法：
#   scripts/git-sync.sh "提交说明"      # 省略说明时使用时间戳
#
# 行为：git add -A → 有改动则 commit → git pull --rebase --autostash → git push
# 安全约定：绝不 force push；rebase 出现冲突时停止，需人工解决后再运行。
set -euo pipefail

# 切到仓库根目录（脚本位于 scripts/ 下）
cd "$(dirname "$0")/.."

msg="${1:-sync: $(date '+%Y-%m-%d %H:%M:%S')}"

git add -A

if git diff --cached --quiet; then
  echo "[git-sync] 无变更需要提交"
else
  git commit -m "$msg"
fi

git pull --rebase --autostash
git push

branch="$(git rev-parse --abbrev-ref HEAD)"
echo "[git-sync] 已同步到 origin/${branch}"
