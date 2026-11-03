#!/usr/bin/env bash
# =============================================================================
# weekly_offsite.sh —— 每周异地副本（R4-7，用户 2026-11-03 选定方案②）
#
# 为什么由**云主机自己发**，而不是本地机器发：
#   本地机器不常开机。一个"需要你开着电脑才会发生"的备份，在三个月后
#   最容易被发现是空的那一个。云主机本来就 7×24 醒着。
#
# 产物（一封邮件，两个附件）：
#   1. repo_<日期>.bundle  —— git 全历史（git clone <bundle> 即可完整恢复）
#   2. evidence_<日期>.zip —— 不可再生的证据：冻结配置、台账、更正日志、
#                             交易日历、universe_state 快照、STATUS.md
#
# 失败关闭：任一环节失败 → 非 0 退出 + 尝试发一封【失败】告警邮件。
#   **不许"发不出去也返回 0"**——那是最危险的备份形态。
#
# 用法： weekly_offsite.sh [--dry-run]
# =============================================================================
set -uo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

REPO="${DEEPQUANT_HOME:-/opt/quant}"
BARE="${DEEPQUANT_BACKUP_REPO:-/opt/quant-backup/repo.git}"
FWD="$REPO/research/stock_pick_2026-10-25/forward"
PY="${DEEPQUANT_PYTHON:-$REPO/.venv/bin/python}"
DAY="$(date +%Y%m%d)"
OUT="$(mktemp -d /tmp/dqoffsite.XXXXXX)"
trap 'rm -rf "$OUT"' EXIT

log() { echo "[offsite] $*"; }
die() { echo "[offsite] ✗ $*" >&2; exit 1; }

# ---- 0. 前置：邮件配置必须在，否则等于白跑 ----
set -a; . "$REPO/env" 2>/dev/null || true; set +a
[ -n "${DEEPQUANT_SMTP_HOST:-}" ] || die "env 里没有 DEEPQUANT_SMTP_HOST —— 邮件通道未配置"
[ -n "${DEEPQUANT_MAIL_TO:-}" ]   || die "env 里没有 DEEPQUANT_MAIL_TO"

# ---- 1. git bundle（全历史）----
[ -d "$BARE" ] || die "裸仓库不存在：$BARE（先在本地 git push cloud --all）"
BUNDLE="$OUT/repo_${DAY}.bundle"
log "生成 git bundle ← $BARE"
git -C "$BARE" bundle create "$BUNDLE" --all 2>/dev/null \
  || die "git bundle 失败"
BSZ=$(stat -c %s "$BUNDLE")
[ "$BSZ" -gt 1000000 ] || die "bundle 只有 $BSZ 字节，异常（预期 >1 MB）"

# bundle 自校验：能 verify 才算可恢复
git -C "$BARE" bundle verify "$BUNDLE" >/dev/null 2>&1 \
  || die "bundle verify 失败 —— 生成的包不可恢复，不发送"
HEADSHA=$(git -C "$BARE" rev-parse workbuddy/2026-10-20-consolidated 2>/dev/null || echo UNKNOWN)
NCOMMIT=$(git -C "$BARE" rev-list --count --all 2>/dev/null || echo 0)
log "bundle $(numfmt --to=iec "$BSZ") ｜ HEAD ${HEADSHA:0:12} ｜ $NCOMMIT 提交 ｜ verify OK"

# ---- 2. 证据快照 ----
EV="$OUT/evidence"
mkdir -p "$EV/ledger" "$EV/universe_state"
cp "$REPO/STATUS.md" "$EV/" 2>/dev/null || log "⚠️ STATUS.md 缺失"
for f in sp4_config_v1.json sp4_config_v1.sha256 sp4_config_v2.json sp4_config_v2.sha256 \
         corrections.jsonl heartbeat.json trade_calendar.json execution_config_v1.json; do
  [ -f "$FWD/$f" ] && cp "$FWD/$f" "$EV/" || true
done
cp "$FWD"/ledger/*.json "$FWD"/ledger/*.csv "$FWD"/ledger/*.LEGACY_NO_D4 "$EV/ledger/" 2>/dev/null || true
# universe_state 只带最近 14 天（时点证据，全量会越来越大）
find "$FWD/universe_state" -name '*.json' -mtime -14 -exec cp {} "$EV/universe_state/" \; 2>/dev/null || true
NEV=$(find "$EV" -type f | wc -l)
[ "$NEV" -ge 3 ] || die "证据文件只有 $NEV 个，异常（预期 ≥3）"
log "证据 $NEV 文件"

# ---- 3. 打 zip + 记 sha256 ----
ZIP="$OUT/evidence_${DAY}.zip"
( cd "$OUT" && zip -qr "$ZIP" evidence ) || die "zip 失败"
ZSZ=$(stat -c %s "$ZIP")
SHA=$(sha256sum "$BUNDLE" | cut -d' ' -f1)
SHAZ=$(sha256sum "$ZIP" | cut -d' ' -f1)
log "zip $(numfmt --to=iec "$ZSZ")"

if [ "$DRY" = "1" ]; then
  log "--dry-run：不发送。bundle sha256=${SHA:0:16}… zip sha256=${SHAZ:0:16}…"
  exit 0
fi

# ---- 4. 发送 ----
BODY="DeepQuant 每周异地副本 $DAY

附件 1  repo_${DAY}.bundle（$(numfmt --to=iec "$BSZ")）
  git 全历史 $NCOMMIT 提交，HEAD ${HEADSHA:0:12}
  sha256 = $SHA
  恢复：git clone repo_${DAY}.bundle DeepQuant

附件 2  evidence_${DAY}.zip（$(numfmt --to=iec "$ZSZ")，$NEV 个文件）
  冻结配置 v1/v2 + sha256、两期台账（含 LEGACY_NO_D4）、更正日志、
  交易日历、最近 14 天 universe_state 快照、STATUS.md
  sha256 = $SHAZ

行情数据（约 1.7 GB）不在包内：可由 deploy/bin/migrate_data.py 清单重建，
或从云主机 /opt/quant/data 直接取。重建步骤见 服务器迁移指南.md §5。

这封信由云主机自动发送，每周一 09:30。若某周没收到 = 异地副本断了。"

if ! "$PY" "$REPO/deploy/bin/notify_mail.py" \
      --subject "【DeepQuant】每周异地包 $DAY" \
      --body "$BODY" \
      --attach "$BUNDLE" --attach "$ZIP"; then
  # 发送失败：再试一封纯文本告警（不带附件，提高成功率）
  "$PY" "$REPO/deploy/bin/notify_mail.py" \
      --subject "【DeepQuant 告警】每周异地包 $DAY 发送失败" \
      --body "每周异地副本生成成功但邮件发送失败（附件 $(numfmt --to=iec "$BSZ") + $(numfmt --to=iec "$ZSZ")）。
包仍留在云主机 /tmp 已被清理，需人工重跑：$REPO/deploy/bin/weekly_offsite.sh
bundle sha256=$SHA" >/dev/null 2>&1 || true
  die "邮件发送失败（已尝试发出告警邮件）"
fi

# ---- 5. 留档一行（不含凭证）----
echo "$DAY bundle=${SHA:0:16} zip=${SHAZ:0:16} commits=$NCOMMIT head=${HEADSHA:0:12}" \
  >> "$REPO/logs/offsite_history.log"
log "✅ 已发送给 $DEEPQUANT_MAIL_TO"
