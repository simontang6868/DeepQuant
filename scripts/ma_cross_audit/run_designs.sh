#!/usr/bin/env bash
# MA-CROSS 独立复核：v2 / v3 双版全量重跑（不改任何既有代码，只通过环境变量切换口径）
# v2 = DEEPQUANT_PRICE_LIMIT=off   （原始主结果口径：不模拟涨跌停）
# v3 = DEEPQUANT_PRICE_LIMIT=board （修复口径：按板块 10%/20% 模拟）
set -u
ROOT="D:/DeepQuant power by txm/DeepQuant-main"
AUDIT="$ROOT/ma_cross_audit"
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

export DEEPQUANT_UNIVERSE="$AUDIT/universe48.json"
export PYTHONPATH="$ROOT/backend/src"
export DEEPQUANT_START=20170101
export DEEPQUANT_END=20241231

run_one() {
  local tag="$1" mode="$2"
  export DEEPQUANT_DATA_ROOT="$AUDIT/data_${tag}"
  export DEEPQUANT_PRICE_LIMIT="$mode"
  export DEEPQUANT_W1_OUT="$AUDIT/runs/${tag}/windows"
  mkdir -p "$AUDIT/runs/${tag}"
  echo "=== [$(date +%H:%M:%S)] START ${tag} (price_limit=${mode}) ==="
  "$PY" -u "$ROOT/data/step75/research.py" > "$AUDIT/runs/${tag}/run.log" 2>&1
  local rc=$?
  echo "=== [$(date +%H:%M:%S)] END ${tag} rc=${rc} ==="
  if [ -f "$DEEPQUANT_DATA_ROOT/step75_research_summary.json" ]; then
    cp "$DEEPQUANT_DATA_ROOT/step75_research_summary.json" "$AUDIT/runs/${tag}/"
    echo "[saved] $AUDIT/runs/${tag}/step75_research_summary.json"
  else
    echo "[MISS ] ${tag} 未产出 summary"
  fi
  tail -4 "$AUDIT/runs/${tag}/run.log"
}

run_one v2 off
run_one v3 board
echo "=== ALL DONE $(date +%H:%M:%S) ==="
