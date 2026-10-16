"""Step 7.5 端到端多标的研究 — v2（腾讯接口）。"""
import sys, os, json, hashlib, traceback
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/workspace/DeepQuant/backend/src")

import pandas as pd
import numpy as np
import akshare as ak

from deepquant.data.validation.checks_extended import validate_daily_extended
from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.fee_presets import (
    FEE_PRESET_SIMULATION, FEE_PRESET_REFERENCE, FEE_PRESET_HIGH_COST,
)
from deepquant.strategies.ma_cross import MaCrossStrategy
from deepquant.strategies.mean_reversion import MeanReversionStrategy
from deepquant.walkforward.parameter_selection import GridSearchConfig, run_walkforward_v2
from deepquant.walkforward.sensitivity import run_sensitivity_and_risk_audit

STOCKS = [
    ("sh600519", "600519.SH", "贵州茅台", "白酒/消费"),
    ("sz000001", "000001.SZ", "平安银行", "银行/金融"),
    ("sh601318", "601318.SH", "中国平安", "保险/金融"),
    ("sz300750", "300750.SZ", "宁德时代", "新能源/制造"),
    ("sz000858", "000858.SZ", "五粮液", "白酒/消费"),
    ("sh600900", "600900.SH", "长江电力", "公用事业"),
]

DATA_ROOT = Path("/workspace/DeepQuant/data/step75")
DATA_ROOT.mkdir(parents=True, exist_ok=True)
START, END = "20180101", "20241231"
INITIAL_CASH = 100000.0

MA_GRID = GridSearchConfig(
    fast_candidates=(3, 5, 10, 20), slow_candidates=(15, 25, 40, 60),
    min_train_periods=150, min_oos_periods=50, min_trades=None, max_combinations=50,
)
MR_GRID = GridSearchConfig(
    fast_candidates=(10, 20, 30, 50), slow_candidates=(15, 20, 25),   # ×10 → threshold 1.5/2.0/2.5
    min_train_periods=150, min_oos_periods=50, min_trades=None, max_combinations=50,
)

def ma_factory(p):
    return MaCrossStrategy(fast_window=int(p["fast_window"]), slow_window=int(p["slow_window"]))
def mr_factory(p):
    return MeanReversionStrategy(lookback=int(p["fast_window"]), threshold=float(p["slow_window"]) / 10.0)

STRATEGIES = [
    ("ma_cross", MA_GRID, ma_factory),
    ("mean_reversion", MR_GRID, mr_factory),
]
PRESETS = [FEE_PRESET_SIMULATION, FEE_PRESET_REFERENCE, FEE_PRESET_HIGH_COST]

# --- 下载 ---
download_results = []
for tencent_code, full_code, name, industry in STOCKS:
    csv_path = DATA_ROOT / f"{full_code}.qfq.csv"
    if csv_path.exists() and csv_path.stat().st_size > 500:
        df = pd.read_csv(csv_path)
        print(f"[CACHED] {full_code} {name} {len(df)} rows", flush=True)
    else:
        try:
            df = ak.stock_zh_a_hist_tx(symbol=tencent_code, start_date=START, end_date=END, adjust="qfq")
            df.to_csv(csv_path, index=False)
            print(f"[FETCH ] {full_code} {name} {len(df)} rows via tx", flush=True)
        except Exception as exc:
            print(f"[FAIL  ] {full_code} {name}: {exc}", flush=True)
            df = None

    if df is None:
        download_results.append({"code": full_code, "ok": False, "n_rows": 0})
        continue

    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    report_ext = validate_daily_extended(df, adjust_status="qfq")
    download_results.append({
        "code": full_code, "name": name, "industry": industry,
        "sha256": sha, "n_rows": len(df),
        "date_min": str(df["date"].min().date()), "date_max": str(df["date"].max().date()),
        "ok": True,
        "warnings": [{"code": i.code, "msg": i.message[:200]} for i in report_ext.issues if i.level == "warning"],
    })

# --- Walk-Forward ---
all_results = []
for dr in download_results:
    if not dr["ok"] or dr["n_rows"] < 400:
        continue
    full_code = dr["code"]
    csv_path = DATA_ROOT / f"{full_code}.qfq.csv"
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df_wf = df.copy().set_index("date")[["open", "high", "low", "close", "volume"]]
    df_wf.index.name = None

    for strat_name, grid, factory in STRATEGIES:
        for preset in PRESETS:
            try:
                base_cfg = BacktestConfig(symbol=full_code, initial_cash=INITIAL_CASH)
                cfg = preset.apply(base_cfg)
                specs, wf_results, summary = run_walkforward_v2(df_wf, cfg, strategy_factory=factory, grid_config=grid)
                audit = run_sensitivity_and_risk_audit(wf_results, grid_config=grid)
                all_results.append({
                    "symbol": full_code, "name": dr["name"], "industry": dr["industry"],
                    "strategy": strat_name, "fee_source": preset.fee_source,
                    "n_windows": len(specs),
                    "n_sel_ok": summary.n_selection_success,
                    "n_oos_ok": summary.n_oos_success,
                    "positive_ratio": summary.positive_ratio,
                    "median_ret": summary.median_cumulative_return,
                    "median_mdd": summary.median_max_drawdown_ratio,
                    "total_trades": summary.total_oos_trades,
                    "total_fees": summary.total_oos_fees,
                    "param_dist": summary.param_distribution,
                    "transition_rate": (
                        audit.param_stability.n_window_transitions / audit.param_stability.n_pairs
                        if audit.param_stability.n_pairs > 0 else None
                    ),
                    "risk_summary": audit.risk.summary_text,
                    "risk_rules": [{"id": r.rule_id, "level": r.level} for r in audit.risk.rules],
                })
                print(f"  {strat_name:15s} {preset.fee_source[:28]:28s} | "
                      f"n={len(specs):2d} pos={summary.positive_ratio:4.0%} "
                      f"med={summary.median_cumulative_return:+.4f} "
                      f"mdd={summary.median_max_drawdown_ratio:.4f} "
                      f"risk={audit.risk.summary_text[:50]}", flush=True)
            except Exception as exc:
                all_results.append({"symbol": full_code, "strategy": strat_name,
                                    "fee_source": preset.fee_source, "error": str(exc)})
                print(f"  {strat_name:15s} {preset.fee_source[:28]:28s} FAIL: {exc}", flush=True)

out = {
    "run_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    "data_source": "akshare.stock_zh_a_hist_tx (tencent kline, qfq)",
    "stocks": download_results,
    "results": all_results,
}
out_path = DATA_ROOT / "step75_research_summary.json"
out_path.write_text(json.dumps(out, indent=2, default=str))
print(f"\n[SAVED] {out_path}")

# --- 终端汇总 ---
from collections import defaultdict
agg = defaultdict(lambda: {"pos": [], "ret": [], "mdd": [], "fees": []})
for r in all_results:
    if "error" in r or r.get("positive_ratio") is None: continue
    k = (r["strategy"], r["fee_source"])
    agg[k]["pos"].append(r["positive_ratio"])
    agg[k]["ret"].append(r["median_ret"])
    agg[k]["mdd"].append(r["median_mdd"])
    agg[k]["fees"].append(r["total_fees"])

print("\nMULTI-STOCK SUMMARY")
print(f"{'STRAT':15} {'FEE':28} {'N':>3} {'POS%':>8} {'RET_MED':>10} {'MDD_MED':>10} {'FEE_MED':>10}")
print("-"*80)
for (s, f), v in sorted(agg.items()):
    n = len(v["pos"])
    if n == 0: continue
    print(f"{s:15} {f:28} {n:>3} {np.mean(v['pos']):>7.1%} {np.mean(v['ret']):>+10.4f} "
          f"{np.mean(v['mdd']):>10.4f} {np.median(v['fees']):>10.2f}")

print("\nPER-STOCK (SIM)")
for r in sorted([x for x in all_results if x.get("fee_source") == "simulation_placeholder"],
                key=lambda x: (x["symbol"], x["strategy"])):
    if "error" in r: continue
    print(f"{r['symbol']:10} {r.get('name',''):6} {r['strategy']:15} "
          f"pos={r['positive_ratio']:.0%} med={r['median_ret']:+.4f} "
          f"mdd={r['median_mdd']:.4f} risk={r['risk_summary'][:40]}")

print("\nFEE SENSITIVITY (000001 ma_cross)")
for src in ["simulation_placeholder", "broker_reference_common_2024", "research_scenario_high_cost"]:
    for r in all_results:
        if r.get("symbol") == "000001.SZ" and r.get("strategy") == "ma_cross" and r.get("fee_source") == src:
            print(f"  {src[:28]:28s}: pos={r['positive_ratio']:.0%} med={r['median_ret']:+.4f} fees={r['total_fees']:.2f}")
            break

print("\nDone OK")
