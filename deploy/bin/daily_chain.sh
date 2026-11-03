#!/usr/bin/env bash
# DeepQuant 日更链（云主机 systemd timer 调用）
#
# 设计原则（每条都对应一个已发生的故障）：
#  1. **每一步都检查退出码**，不用 `set -e` 直接退出——那样拿不到"哪一步失败了"。
#  2. 数据两源比对**不一致就不决策**。拿着两源打架的价格选股，等于用未经验证的输入
#     产出不可复现的结论。
#  3. qfq 重建**不直接覆盖**：先写 stage → 与现行版本逐位校验 → 只在 PASS 时原子替换。
#     重建脚本一旦中途失败，直接覆盖会留下半成品，而半成品在截面里表现为"某天某些股票消失"。
#  4. 无论成败都写心跳。心跳是"这台机器今天跑过"的唯一证据；只在成功时写，
#     故障当天就没有心跳——那和"没跑"分不开。
#  5. 决策只在窗口内跑（`is_due.py`），非窗口日不碰台账。
#
# 用法：
#   DEEPQUANT_HOME=/opt/deepquant DEEPQUANT_PYTHON=/opt/deepquant/.venv/bin/python \
#     deploy/bin/daily_chain.sh [--dry-run] [--skip-update]
#
set -uo pipefail

REPO="${DEEPQUANT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PY="${DEEPQUANT_PYTHON:-$REPO/.venv/bin/python}"
SP5="$REPO/research/stock_pick_2026-10-25/scripts/sp5"
FWD="$REPO/research/stock_pick_2026-10-25/forward"
VAR="$REPO/var"
LOG_DIR="$VAR/log"
HB="$FWD/heartbeat.json"

DRY=0
SKIP_UPDATE=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --skip-update) SKIP_UPDATE=1 ;;
    *) echo "未知参数：$a"; exit 64 ;;
  esac
done

mkdir -p "$LOG_DIR" "$VAR"
TS="$(date +%Y%m%dT%H%M%S)"
LOG="$LOG_DIR/daily_$(date +%Y%m%d).log"
exec > >(tee -a "$LOG") 2>&1

echo "=== daily_chain $TS | repo=$REPO | dry_run=$DRY ==="
command -v "$PY" >/dev/null 2>&1 || { echo "[fatal] 解释器不存在：$PY"; exit 70; }

run() {  # run <名字> <命令...>
  local name="$1"; shift
  if [ "$DRY" = "1" ]; then echo "[dry] $name :: $*"; return 0; fi
  echo "--- [$name] $*"
  "$@"
  local rc=$?
  echo "--- [$name] rc=$rc"
  return $rc
}

stage_ok=1
stage_note=""
decision=""
mode="CHAIN_OK"

# ---------------- 1. 增量更新（exec_all） ----------------
upd_rc=0
if [ "$SKIP_UPDATE" = "1" ]; then
  echo "[update] 已跳过（--skip-update）"
else
  run update "$PY" "$REPO/scripts/update_exec_incremental.py" --apply \
      --report "$VAR/update_$(date +%Y%m%d).json"
  upd_rc=$?
  # 0=干净；1=有 flag（两源打架）；2=有 ERROR
  if [ "$upd_rc" = "1" ]; then
    stage_ok=0; stage_note="两源比对有 flag 的股票 >0"
    echo "[update] ⚠️ 有 flag：两源不一致。**不做决策**（不自行择一源）。"
  elif [ "$upd_rc" != "0" ]; then
    stage_ok=0; stage_note="增量更新失败 rc=$upd_rc"
  fi
fi

# ---------------- 1.5 universe_state 每日快照（R4-5，时点证据） ----------------
# 失败只告警不中断：决策用「最新可用快照 ≤ 决策日」语义，缺今天的还有昨天的；
# 但快照缺失会记进心跳 note，17:40 状态邮件可见。
if [ "$SKIP_UPDATE" = "1" ]; then
  echo "[universe] 已跳过（--skip-update）"
else
  if run universe_snap "$PY" "$SP5/p17_universe_filter.py" --fetch; then
    SNAP="$REPO/research/stock_pick_2026-10-25/forward/universe_state/$(date +%Y%m%d).json"
    if [ -f "$SNAP" ]; then
      sha256sum "$SNAP" >> "$REPO/research/stock_pick_2026-10-25/forward/universe_state/SHA256S.txt"
      echo "[universe] 快照已存档并记 sha256：$(basename "$SNAP")"
    else
      echo "[universe] ⚠️ --fetch 成功但未见今日快照文件（检查 p17 输出路径）"
    fi
  else
    echo "[universe] ⚠️ 快照抓取失败（不中断链路；决策将沿用最近可用快照）"
  fi
fi

# ---------------- 2. 是否决策窗口 ----------------
due_rc=1
due_line=""
if [ "$stage_ok" = "1" ]; then
  if [ "$DRY" = "1" ]; then
    echo "[dry] is_due :: $PY $REPO/deploy/bin/is_due.py"
    due_line="DUE (dry-run)"
  else
    due_line="$("$PY" "$REPO/deploy/bin/is_due.py")"
    due_rc=$?
    echo "[is_due] $due_line"
  fi
else
  echo "[is_due] 跳过：上游失败"
fi

# ---------------- 3. 决策（含 qfq 重建） ----------------
if [ "$stage_ok" = "1" ] && [ "${due_line:0:3}" = "DUE" ]; then
  decision="$(echo "$due_line" | awk '{print $2}')"
  STAGE="$REPO/data/qfq_stage"
  echo "[qfq] 决策日 $decision ⇒ 重建 ADJ 到 stage（不覆盖现行版本）"
  if run qfq_rebuild "$PY" "$REPO/scripts/rebuild_adj_from_exec.py" \
        --div-source merge --out "$STAGE"; then
    if run qfq_verify "$PY" "$REPO/scripts/verify_qfq_rebuild.py" \
          --old "$REPO/data/qfq_all" --new "$STAGE" \
          --json "$VAR/qfq_verify_$(date +%Y%m%d).json"; then
      if [ "$DRY" = "0" ]; then
        PREV="$REPO/data/qfq_all_prev_$(date +%Y%m%d)"
        rm -rf "$PREV"
        mv "$REPO/data/qfq_all" "$PREV" && mv "$STAGE" "$REPO/data/qfq_all"
        echo "[qfq] 已原子替换；上一版保留在 $PREV"
      fi
      if run decision "$PY" "$SP5/p13_forward.py" --if-due; then
        mode="DECIDED"
      else
        stage_ok=0; stage_note="决策阶段失败（见日志）"; mode="ABORT_DECISION"
      fi
    else
      stage_ok=0; stage_note="qfq 重建校验未通过，拒绝替换与决策"; mode="ABORT_QFQ_VERIFY"
    fi
  else
    stage_ok=0; stage_note="qfq 重建失败"; mode="ABORT_QFQ_REBUILD"
  fi
elif [ "$stage_ok" = "1" ]; then
  case "${due_line:0:8}" in
    NOT_DUE*) mode="NOT_DUE" ;;
    ABORT*)   stage_ok=0; stage_note="$due_line"; mode="ABORT_WINDOW" ;;
  esac
fi

# ---------------- 4. 心跳（无条件写） ----------------
# dry-run 必须与真跑区分开：一个写着 DECIDED 的 dry-run 日志，
# 事后看与真决策完全一样——这正是"看起来跑过了"的经典来源。
if [ "$DRY" = "1" ]; then mode="DRY_RUN"; decision=""; fi
if [ "$DRY" = "0" ]; then
  "$PY" - "$HB" "$mode" "$decision" "$stage_ok" "$stage_note" <<'PYEOF'
import json, sys, time, pathlib
hb, mode, decision, ok, note = sys.argv[1:6]
p = pathlib.Path(hb)
payload = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "decision": decision or None,
    "mode": mode,
    "ok": ok == "1",
    "stage_note": note,
    "source": "daily_chain",
    "duration_s": None,
}
if p.exists():
    try:
        old = json.loads(p.read_text(encoding="utf-8"))
        if old.get("source") == "p13_forward" and old.get("mode") not in (None, "NOT_DUE"):
            # 保留决策阶段自己写的心跳字段（那是决策的证据，不能被链路覆盖掉）
            old.update({k: v for k, v in payload.items() if v is not None})
            payload = old
    except Exception:
        pass
p.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
             encoding="utf-8", newline="\n")
print(f"[heartbeat] {mode} ok={payload['ok']} decision={payload['decision']}")
PYEOF
fi

# ---------------- 4b. 外部心跳（独立于本机的存活信号） ----------------
# 机内 heartbeat.json 只能证明"这台机器认为它跑过了"。机器整机宕机/断电时，
# 没有任何进程会去报告"我没跑"——所以必须有**外部**在等这个信号。
if [ "$DRY" = "0" ]; then
  if [ -n "${DEEPQUANT_HEARTBEAT_PING_URL:-}" ]; then
    # 成功才 ping；失败时 ping /fail 让外部立刻告警（而不是等超时）
    if [ "$stage_ok" = "1" ]; then PING="$DEEPQUANT_HEARTBEAT_PING_URL"; else PING="$DEEPQUANT_HEARTBEAT_PING_URL/fail"; fi
    if curl -fsS -m 10 -o /dev/null "$PING"; then
      echo "[alert] 外部心跳已上报"
    else
      echo "[alert] ⚠️ 外部心跳上报失败（网络/URL 问题，需人工确认）"
    fi
  else
    echo "[alert] ⚠️ 未配置 DEEPQUANT_HEARTBEAT_PING_URL：本机整机故障将无人知晓"
  fi
fi

# ---------------- 5. 机外备份 ----------------
if [ "$DRY" = "0" ] && [ -n "${DEEPQUANT_BACKUP_CMD:-}" ]; then
  echo "--- [backup] $DEEPQUANT_BACKUP_CMD"
  eval "$DEEPQUANT_BACKUP_CMD"
  [ $? -ne 0 ] && echo "[backup] ⚠️ 备份失败（不改变本次链路结论，但必须人工处理）"
fi

echo "=== daily_chain 结束 mode=$mode ok=$stage_ok $stage_note ==="

# ---------------- 6. 峰值内存记录（R3-6） ----------------
# systemd oneshot 的 cgroup 在服务退出后即销毁，memory.peak 只能在运行中读。
# 读到自己 cgroup 的峰值（字节），追加到 CSV；连续 10 个交易日后汇总统计。
# 手工运行（非 systemd）时 cgroup 路径不存在 → 跳过并说明，不伪造数据。
MEMPEAK="/sys/fs/cgroup/system.slice/deepquant-daily.service/memory.peak"
if [ -r "$MEMPEAK" ]; then
  echo "$(date +%Y%m%dT%H%M%S),$(cat "$MEMPEAK"),$mode" >> "$LOG_DIR/mem_peak.csv"
  echo "[mem] 本次峰值 $(awk -F, 'END{printf "%.0f MB", $2/1048576}' "$LOG_DIR/mem_peak.csv")"
else
  echo "[mem] 非 systemd 运行，cgroup memory.peak 不可读（不计入统计）"
fi

[ "$stage_ok" = "1" ] || exit 3
exit 0
