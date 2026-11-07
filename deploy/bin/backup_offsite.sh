#!/usr/bin/env bash
# 机外备份：把「不可重建」的东西打包送出本机。
#
# 只备份证据与配置，**不备份行情数据**：
#   - 台账 / 观测表 / 配置 / 交易日历 / 更正日志 —— 不可重建，必须异地一份
#   - data/qfq_all、data/exec_all —— 可由行情源重建，备份它们只会挤占空间与带宽
#
# 传输目标（按优先级取**第一个已配置**的通道，都不配就**失败退出**，不静默跳过）：
#   DEEPQUANT_BACKUP_EMAIL  收件邮箱（走 notify_mail.py，SMTP 配置见 env）
#   DEEPQUANT_BACKUP_RSYNC  形如  user@host:/srv/deepquant-backup
#   DEEPQUANT_BACKUP_RCLONE 形如  myremote:deepquant-backup
#
# 为什么"不配就失败"：备份脚本最危险的形态是"配错了但每次都成功退出"。
# 三个月后要恢复时才发现目录是空的，而那时没人记得它从哪天开始没备份。
set -uo pipefail

REPO="${DEEPQUANT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# 显式落到仓库内：从 /root 之类不可进入的目录启动时，find 结束会报
# "Failed to restore initial working directory"——无害但会被误读成错误。
cd "$REPO" || exit 70
FWD="$REPO/research/stock_pick_2026-10-25/forward"
VAR="$REPO/var/backup"
KEEP_DAYS="${DEEPQUANT_BACKUP_KEEP_DAYS:-30}"
TS="$(date +%Y%m%dT%H%M%S)"
OUT="$VAR/deepquant_evidence_$TS.tar.gz"

mkdir -p "$VAR"

# ---- 1. 打包（只打证据）----
INCLUDE=()
for p in "$FWD/ledger" "$FWD/observations.csv" "$FWD/corrections.jsonl" \
         "$FWD/shadow_reconcile" "$FWD/account_snapshot.json" \
         "$FWD/universe_state_mirror.json" \
         "$FWD/sp4_config_v1.json" "$FWD/sp4_config_v1.sha256" \
         "$FWD/sp4_config_v2.json" "$FWD/sp4_config_v2.sha256" \
         "$FWD/execution_config_v1.json" "$FWD/trade_calendar.json" \
         "$FWD/heartbeat.json" "$REPO/STATUS.md"; do
  [ -e "$p" ] && INCLUDE+=("$p")
done
if [ "${#INCLUDE[@]}" -eq 0 ]; then
  echo "[backup] 没有可备份的证据文件——先确认 forward/ 是否就位" >&2
  exit 66
fi

# ⚠️ 路径里有空格（`D:/DeepQuant power by txm/...`），所以**不能**用
#    `tar ... $(printf ...)`：命令替换会按空格切词，路径被劈成两半，tar 返回 2。
#    改用 -T 清单文件，一行一个路径。
# ⚠️ 三个坑，都是 Windows 上暴露的，Linux 上不会出现但这样写两边都对：
#   1. 命令替换 `$(printf ...)` 会按空格切词，而仓库路径含空格 ⇒ 改用清单文件；
#   2. `sys.stdout` 在 Windows 把 \n 翻成 \r\n ⇒ 用 Python 直接写文件并指定 newline；
#   3. **GNU tar 会把 `-f/-T/-C` 里带冒号的参数当成远程主机**
#      （"Cannot connect to D:"）⇒ 清单与输出都用相对路径，不用盘符。
LIST_REL="var/.backup_list.$TS"
python3 - "$REPO" "$REPO/$LIST_REL" "${INCLUDE[@]}" <<'PYEOF'
import os, sys

repo, out = sys.argv[1], sys.argv[2]
with open(out, "w", encoding="utf-8", newline="\n") as f:
    for p in sys.argv[3:]:
        f.write(os.path.relpath(p, repo).replace("\\", "/") + "\n")
PYEOF

OUT_REL="var/backup/deepquant_evidence_$TS.tar.gz"
( cd "$REPO" && tar --force-local -czf "$OUT_REL" -T "$LIST_REL" )
rc=$?
OUT="$REPO/$OUT_REL"

# ---- 2. 自检：归档里必须真的有条目（tar 成功但空包是可能的）----
# `-t` 也要用相对路径：`tar -tf D:/...` 同样会被当成远程主机。
n_in_tar="$( cd "$REPO" && tar --force-local -tzf "$OUT_REL" | grep -vc '/$' )"
n_src="$(printf '%s\n' "${INCLUDE[@]}" | while read -r p; do
          if [ -d "$p" ]; then find "$p" -type f | wc -l; else echo 1; fi
        done | awk '{s+=$1} END {print s}')"
if [ "$n_in_tar" -lt "$n_src" ]; then
  echo "[backup] 归档条目数 $n_in_tar < 源文件数 $n_src —— 备份不完整" >&2
  exit 68
fi
echo "[backup] 打包完成 $OUT（$n_in_tar 个文件，$(du -h "$OUT" | cut -f1)）"

# ---- 3. 送出本机 ----
sent=0
if [ -n "${DEEPQUANT_BACKUP_EMAIL:-}" ]; then
  echo "[backup] 邮件 → $DEEPQUANT_BACKUP_EMAIL"
  if python3 "$REPO/deploy/bin/notify_mail.py" \
        --subject "DeepQuant 证据备份 $TS（$(hostname)）" \
        --body-file <(printf '归档：%s\n文件数：%s\n时间：%s\n主机：%s\n\n本邮件由 backup_offsite.sh 自动发送；行情数据不入邮件（可由行情源重建）。\n' \
            "$OUT" "$n_in_tar" "$TS" "$(hostname)") \
        --attach "$OUT"; then
    sent=1
  else
    echo "[backup] ⚠️ 邮件通道失败 rc=$?（配置缺失或 SMTP 拒绝）"
  fi
elif [ -n "${DEEPQUANT_BACKUP_RSYNC:-}" ]; then
  echo "[backup] rsync → $DEEPQUANT_BACKUP_RSYNC"
  rsync -az --timeout=300 "$OUT" "$DEEPQUANT_BACKUP_RSYNC/" && sent=1
elif [ -n "${DEEPQUANT_BACKUP_RCLONE:-}" ]; then
  echo "[backup] rclone → $DEEPQUANT_BACKUP_RCLONE"
  rclone copy --timeout 5m "$OUT" "$DEEPQUANT_BACKUP_RCLONE" && sent=1
fi

if [ "$sent" -ne 1 ]; then
  echo "[backup] ✗ 没有任何机外目标可用（DEEPQUANT_BACKUP_RSYNC / _RCLONE 都没配或传输失败）" >&2
  echo "[backup]   本地副本保留在 $OUT，但**本机磁盘损坏时这份也没了**" >&2
  exit 69
fi

# ---- 4. 本地保留最近 N 天（机外那份不删）----
find "$VAR" -name 'deepquant_evidence_*.tar.gz' -mtime +"$KEEP_DAYS" -delete
echo "[backup] 完成；本地保留 ${KEEP_DAYS} 天"
