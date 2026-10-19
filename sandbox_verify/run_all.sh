#!/usr/bin/env bash
# 一次性跑完 §4 的 #6 #7 与 #2~#5 #8（#1 的 pytest 单独跑，太慢）。
# 用法：在 DeepQuant-main/ 下  bash sandbox_verify/run_all.sh
#      服务未起时 api_checks 会报连接失败，其余两项照常跑。
set -u
cd "$(dirname "$0")/.." || exit 1
PY="${PYTHON:-python}"
export PYTHONPATH=backend/src
DATA="data/processed/600519.SH.daily.parquet"
BASE="${BASE:-http://127.0.0.1:8123}"
rc=0

echo "### #6 P0-1 generate_windows"
"$PY" sandbox_verify/p0_1_windows.py --data "$DATA" || rc=1
echo
echo "### #7 P0-2 price_jump"
"$PY" sandbox_verify/p0_2_price_jump.py --data "$DATA" || rc=1
echo
echo "### #2~#5 #8 HTTP 自检（base=$BASE）"
"$PY" sandbox_verify/api_checks.py --base "$BASE" || rc=1
echo
echo "### 汇总：$([ $rc -eq 0 ] && echo 'ALL PASS' || echo 'HAS FAILURE')"
exit $rc
