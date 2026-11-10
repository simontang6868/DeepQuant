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

# R7-5：11-27 月末 DRYRUN 必须覆盖本修复（跨日推进 / REV_CONFLICT / rc 闸门 / 迁移幂等）
if [ "$DRY" = "1" ]; then
  echo "--- [r7_selfcheck] ECR-P0-SNAPSHOT-1 DRYRUN 覆盖"
  "$PY" "$REPO/scripts/r7_dryrun_check.py" || { echo "[r7] ✗ 自检失败"; exit 71; }
fi

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
STAGES=""
STAGE_RESULTS="$FWD/stage_results.json"

# ECR-P0-SNAPSHOT-1 / R7-3：关键阶段（行情、宇宙快照、决策、账户快照、
# SHADOW、备份）任一非零 ⇒ ok=false ⇒ 17:40 告警 ⇒ 该日不计 G1。
# 忽略项必须逐条写理由并落进 stage_note，避免「没检出＝没问题」。
record_stage() {  # <名字> <rc> <关键|忽略> [忽略理由]
  local name="$1" rc="$2" kind="$3" why="${4:-}"
  STAGES="${STAGES:+$STAGES;}$name=$rc:$kind"
  if [ "$rc" = "0" ]; then return 0; fi
  if [ "$kind" = "关键" ]; then
    if [ "$DRY" = "1" ]; then
      # DRYRUN 里 run() 不执行任何命令，也不产文件；模拟出的「缺产物」
      # 不是真失败，只记 note，不计入 ok（否则 11-27 DRYRUN 会永远 exit 3）。
      stage_note="${stage_note:+$stage_note; }${name}_DRY_RC=$rc"
      echo "[stage] DRYRUN 模拟 $name rc=$rc（不计入 ok）"
    else
      stage_ok=0
      stage_note="${stage_note:+$stage_note; }${name}_RC=$rc"
      echo "[stage] ✗ 关键阶段 $name 失败 rc=$rc ⇒ ok=false、须告警"
    fi
  else
    stage_note="${stage_note:+$stage_note; }${name}_IGNORED=$rc($why)"
    echo "[stage] ⚠ 忽略阶段 $name rc=$rc：${why}（已写 note）"
  fi
}

# ---------------- 1. 增量更新（exec_all） ----------------
upd_rc=0
if [ "$SKIP_UPDATE" = "1" ]; then
  echo "[update] 已跳过（--skip-update）"
else
  run update "$PY" "$REPO/scripts/update_exec_incremental.py" --apply \
      --report "$VAR/update_$(date +%Y%m%d).json"
  upd_rc=$?
  record_stage update "$upd_rc" 关键
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
      record_stage universe_snap 0 关键
      sha256sum "$SNAP" >> "$REPO/research/stock_pick_2026-10-25/forward/universe_state/SHA256S.txt"
      echo "[universe] 快照已存档并记 sha256：$(basename "$SNAP")"
    else
      # rc=0 却没有产物 ⇒ 同样判失败（「没检出≠没问题」）
      record_stage universe_snap 1 关键
      echo "[universe] ⚠️ --fetch 成功但未见今日快照文件（R7-3：按失败处理）"
    fi
  else
    record_stage universe_snap 1 关键
    echo "[universe] ⚠️ 快照抓取失败（R7-3：ok=false、该日不计 G1）"
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
    record_stage is_due "$due_rc" 关键
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

# ---------------- 3a. 仿真账户快照（可选；无凭证时完全跳过） ----------------
# DQ_GM_TOKEN / DQ_GM_ACCOUNT_ID 由 EnvironmentFile 注入（env 文件 600、不进 git）。
# 没配凭证就一步都不跑——否则每天都会产生一次"缺配置"的假告警。
# 适配器只读 get_cash/get_positions，**绝不调用任何 order_* 函数**。
#
# ⚠️ 时点口径：本链路 17:10 跑，而掘金沪深账户 18:00 才结算。
# 因此这里取的是「收盘后、结算前」的账户状态。当前不向仿真账户报单，
# 无当日未结委托，故与 17:10 的对账自洽；**将来若允许报单，必须重评时点**。
if [ "$DRY" = "0" ] && [ -n "${DQ_GM_TOKEN:-}" ]; then
  # gmtrade 官方只发布 cp36~cp310 的 wheel（无 sdist、无 cp311/cp312）。
  # 主 venv 是 3.12、系统 python3 是 3.11，直接 pip 会 "No matching distribution"，
  # 强行按 cp311 改名加载 C 扩展会段错误（实测 rc=139）。所以单独放一个 3.10 venv。
  PAPER_PY="${DQ_GM_PYTHON:-$PY}"
  if [ -x "$REPO/.venv-gm/bin/python" ]; then
    PAPER_PY="$REPO/.venv-gm/bin/python"
  fi
  # gmtrade 的 C SDK 会往 /tmp/goldminer 写日志；目录被 tmpfiles 清掉后，
  # 由运行用户自己重建即可（它自己就是属主），否则 journal 每天一条 ERROR。
  mkdir -p /tmp/goldminer 2>/dev/null || true
  run paper_snapshot "$PAPER_PY" "$REPO/scripts/paper_account_adapter.py" \
      --date "${TS:0:8}"
  pa_rc=$?
  record_stage paper_snapshot "$pa_rc" 关键
  if [ "$pa_rc" -ne 0 ]; then
    echo "[paper] ⚠️ 快照 rc=$pa_rc（R7-3：ok=false、告警；对账将退回 SELF_CHECK_ONLY，不计 G2）"
  fi
fi

# ---------------- 3b. SHADOW 每日对账（只读，绝不发单） ----------------
# 2.9-a 的"生成不发送"机制早已 PASS，但此前只被测试调用、日更链一次没跑过，
# 所以 G2 的「连续 20 个交易日」一直是 0 天。这里把它接进来。
#
# ⚠️ 三条纪律：
#  1. **ECR-P0-SNAPSHOT-1 收紧**：对账非零 rc 现在会置 stage_ok=0、心跳 ok=false、
#     17:40 告警、该日不计 G1（R7-3）。旧口径「失败只告警不阻断」已作废。
#  2. **不把"没账户"当故障**：LIVE_ENABLED=false 且无仿真账户时，脚本每天如实
#     产出 SELF_CHECK_ONLY、counts_toward_g2=false；接入管道≠G2 开始计数。
#  3. **不静默吞掉异常**：非零退出必须落在日志与心跳里，否则"没检出=没问题"。
if [ "$DRY" = "0" ]; then
  run shadow_reconcile "$PY" "$REPO/scripts/shadow_daily_reconcile.py" \
      --date "${TS:0:8}"
  sh_rc=$?
  record_stage shadow_reconcile "$sh_rc" 关键
  if [ "$sh_rc" -ne 0 ]; then
    echo "[shadow] ⚠️ 对账 rc=$sh_rc（R7-3：ok=false、告警，必须人工核对）"
  fi
fi

# ---------------- 4. 心跳（无条件写） ----------------
# dry-run 必须与真跑区分开：一个写着 DECIDED 的 dry-run 日志，
# 事后看与真决策完全一样——这正是"看起来跑过了"的经典来源。
if [ "$DRY" = "1" ]; then mode="DRY_RUN"; decision=""; fi
if [ "$DRY" = "0" ]; then
  # R7-3：最终 ok 由独立闸门判定（唯一真源，可单测）；shell 的 record_stage 只采集。
  if ! "$PY" "$REPO/scripts/stage_rc_gate.py" --check "$STAGES"; then
    stage_ok=0
    echo "[stage] ✗ 闸门判定失败 ⇒ ok=false、17:40 须告警、该日不计 G1"
  fi
  "$PY" - "$HB" "$STAGE_RESULTS" "$mode" "$decision" "$stage_ok" "$stage_note" "$STAGES" <<'PYEOF'
import json, sys, time, pathlib
hb, sr, mode, decision, ok, note, stages = sys.argv[1:8]
p = pathlib.Path(hb)
stage_list = [x for x in stages.split(";") if x]
payload = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "decision": decision or None,
    "mode": mode,
    "ok": ok == "1",
    "stage_note": note,
    "stages": stage_list,
    "stage_results_path": str(sr),
    "source": "daily_chain",
    "duration_s": None,
}
# R7-3：阶段结果单独落盘，供 17:40 告警/审计读取（不只依赖 ok 字段）
pathlib.Path(sr).write_text(json.dumps({
    "ts": payload["ts"], "mode": mode, "ok": payload["ok"],
    "stages": stage_list, "stage_note": note,
}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
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
      record_stage heartbeat_ping 0 忽略 "外部存活信号"
      echo "[alert] 外部心跳已上报"
    else
      # 忽略理由：网络/外部服务问题，不改变本机已完成的链路事实；但必须写 note
      record_stage heartbeat_ping 1 忽略 "网络或外部服务问题，不影响本机链路事实"
      echo "[alert] ⚠️ 外部心跳上报失败（网络/URL 问题，需人工确认；已写 note）"
    fi
  else
    echo "[alert] ⚠️ 未配置 DEEPQUANT_HEARTBEAT_PING_URL：本机整机故障将无人知晓"
  fi
fi

# ---------------- 5. 机外备份 ----------------
if [ "$DRY" = "0" ] && [ -n "${DEEPQUANT_BACKUP_CMD:-}" ]; then
  echo "--- [backup] $DEEPQUANT_BACKUP_CMD"
  eval "$DEEPQUANT_BACKUP_CMD"
  brc=$?
  record_stage backup "$brc" 关键
  [ "$brc" -ne 0 ] && echo "[backup] ⚠️ 备份失败（R7-3：ok=false、该日不计 G1，必须人工处理）"
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
  # R7-3 忽略项（理由）：mem_peak 是纯诊断项，不影响证据链；
  # 且它运行在心跳写入之后，故不进入 STAGES/stage_results.json，只在此说明。
  echo "[mem] 非 systemd 运行，cgroup memory.peak 不可读（不计入统计）"
fi

[ "$stage_ok" = "1" ] || exit 3
exit 0
