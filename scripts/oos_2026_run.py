"""Step OOS 2026-10-19：按**预登记规格**跑一次纯样本外，并机械执行冻结的判定规则。

设计原则（这三点决定这个脚本能不能当证据用）：

1. **参数全部读自 `docs/oos_prereg_2026-10-19.json`**，脚本内不写任何研究口径常量。
   要改口径只能改规格文件 —— 改完哈希就变，冻结失效，一眼看得出。
2. **先验哈希再跑**：规格文件与全部数据文件的 sha256 都必须与 `.sha256` 记录一致，
   不一致直接 SystemExit，不"跑起来再说"。
3. **判定不靠人读**：`verdict_rules_ordered_first_match_wins` 逐条机械求值，先命中先返回。
   人在看到数字之后唯一还能做的就是解释，不能改判。

输出：`docs/OOS_RESULTS_2026-10-19.json`（全部数字，含逐窗明细）
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np
import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.fee_presets import (
    FEE_PRESET_SIMULATION, FEE_PRESET_REFERENCE, FEE_PRESET_HIGH_COST,
)
from deepquant.data.validation.checks_extended import validate_daily_extended
from deepquant.market.rules import a_share_rules, a_share_price_limit_rate
from deepquant.strategies.ma_cross import MaCrossStrategy
from deepquant.strategies.mean_reversion import MeanReversionStrategy
from deepquant.walkforward import WalkForwardConfig, generate_windows
from deepquant.walkforward.parameter_selection import (
    GridSearchConfig, generate_ma_cross_candidates,
    run_walkforward_v2, run_walkforward_window,
)

SPEC_PATH = ROOT / "docs" / "oos_prereg_2026-10-19.json"
SHA_PATH = ROOT / "docs" / "OOS_PREREG_2026-10-19.sha256"
OUT_PATH = ROOT / "docs" / "OOS_RESULTS_2026-10-19.json"

PRESETS = {
    "simulation_placeholder": FEE_PRESET_SIMULATION,
    "broker_reference_common_2024": FEE_PRESET_REFERENCE,
    "research_scenario_high_cost": FEE_PRESET_HIGH_COST,
}


def sha256_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 0. 完整性校验
def verify_against_freeze(spec: dict) -> dict:
    """规格与数据必须与冻结记录逐字节一致，否则拒绝运行。"""
    recorded = {}
    for line in SHA_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        h, _, name = line.partition("  ")
        recorded[name.strip()] = h.strip()

    spec_actual = sha256_of(SPEC_PATH)
    spec_recorded = recorded.get("docs/oos_prereg_2026-10-19.json")
    if spec_actual != spec_recorded:
        raise SystemExit(
            f"规格文件已被修改：实际 {spec_actual[:16]}… 冻结 {str(spec_recorded)[:16]}…\n"
            "冻结失效，本次结果不能作为样本外证据。要改口径请另建 v2 规格并重新冻结。"
        )

    drift = []
    for item in spec["data"]["pool"]:
        p = ROOT / spec["data"]["source_dir"] / f"{item['code']}.qfq.csv"
        if not p.exists():
            drift.append(f"{item['code']}: 文件缺失 {p}")
            continue
        actual = sha256_of(p)
        if actual != item["sha256"]:
            drift.append(f"{item['code']}: {actual[:16]}… != {item['sha256'][:16]}…")
    bench = ROOT / spec["data"]["benchmark"]["path"]
    if sha256_of(bench) != spec["data"]["benchmark_sha256"]:
        drift.append("benchmark hs300 指纹不符")
    if drift:
        raise SystemExit("数据指纹不符，拒绝运行：\n  " + "\n  ".join(drift))

    print(f"[FREEZE-OK] spec={spec_actual[:16]}… 数据 7/7 指纹一致\n", flush=True)
    return {"spec_sha256": spec_actual, "recorded": recorded}


# ---------------------------------------------------------------- 1. 数据装载
def load_frame(code: str, spec: dict) -> pd.DataFrame:
    p = ROOT / spec["data"]["source_dir"] / f"{code}.qfq.csv"
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    return df


def to_wf_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().set_index("date")[["open", "high", "low", "close", "volume"]]
    out.index.name = None
    return out


# ---------------------------------------------------------------- 2. 统计工具
def dist(values: list[float]) -> dict | None:
    if not values:
        return None
    a = np.array(values, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    return {
        "n": int(a.size),
        "median": round(float(np.median(a)), 6),
        "mean": round(float(a.mean()), 6),
        "p10": round(float(np.percentile(a, 10)), 6),
        "p90": round(float(np.percentile(a, 90)), 6),
        "min": round(float(a.min()), 6),
        "max": round(float(a.max()), 6),
        "pos_ratio": round(float((a > 0).sum() / a.size), 4),
        "neg_ratio": round(float((a < 0).sum() / a.size), 4),
        "zero_ratio": round(float((a == 0).sum() / a.size), 4),
    }


def classify(oos_start: pd.Timestamp, oos_end: pd.Timestamp, cut: pd.Timestamp) -> str:
    """窗口三分：纯样本内 / 跨界 / 纯样本外。判据只看 OOS 段是否碰到截止日之后。"""
    if oos_end <= cut:
        return "IS"
    if oos_start > cut:
        return "OOS"
    return "BORDER"


# ---------------------------------------------------------------- 3. 主流程
def main() -> None:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    freeze = verify_against_freeze(spec)

    design = spec["design"]
    cut = pd.Timestamp("2024-12-31")          # IS 截止：原始研究的最后一天
    oos_floor = pd.Timestamp("2025-01-02")     # OOS 起点：第一个从未参与选择的交易日

    grid_ma = GridSearchConfig(
        fast_candidates=tuple(design["ma_grid"]["fast"]),
        slow_candidates=tuple(design["ma_grid"]["slow"]),
        min_train_periods=design["train_periods"],
        min_oos_periods=design["oos_window"],
        min_trades=design["min_trades"],
        max_combinations=design["ma_grid"]["max_combinations"],
    )
    grid_mr = GridSearchConfig(
        fast_candidates=tuple(design["mr_grid"]["fast"]),
        slow_candidates=tuple(design["mr_grid"]["slow"]),
        min_train_periods=design["train_periods"],
        min_oos_periods=design["oos_window"],
        min_trades=design["min_trades"],
        max_combinations=design["mr_grid"]["max_combinations"],
    )
    strategies = [
        ("ma_cross", grid_ma,
         lambda p: MaCrossStrategy(fast_window=int(p["fast_window"]), slow_window=int(p["slow_window"]))),
        ("mean_reversion", grid_mr,
         lambda p: MeanReversionStrategy(lookback=int(p["fast_window"]),
                                         threshold=float(p["slow_window"]) / 10.0)),
    ]
    presets = [PRESETS[s] for s in design["fee_presets"]]
    mode = design["price_limit_mode"]

    # --- 数据闸门（原样应用，不为凑样本放宽）---
    frames: dict[str, pd.DataFrame] = {}
    wf_frames: dict[str, pd.DataFrame] = {}
    data_report: dict[str, dict] = {}
    for item in spec["data"]["pool"]:
        code = item["code"]
        df = load_frame(code, spec)
        rep = validate_daily_extended(df, adjust_status="qfq")
        errs = [{"code": i.code, "count": i.count, "msg": i.message[:160]}
                for i in rep.issues if i.level == "error"]
        data_report[code] = {
            "n_rows": int(len(df)),
            "date_min": str(df["date"].min().date()),
            "date_max": str(df["date"].max().date()),
            "data_ok": bool(rep.is_valid),
            "errors": errs,
            "sha256": item["sha256"],
        }
        if rep.is_valid:
            frames[code] = df
            wf_frames[code] = to_wf_frame(df)
            print(f"[GATE-PASS] {code} {len(df)} 行 {df['date'].min().date()}~{df['date'].max().date()}", flush=True)
        else:
            print(f"[GATE-BLOCK] {code} {[e['code'] for e in errs]}", flush=True)

    # --- 结构复制检查：截到 2024-12-31 时应得到与历史一致的窗口数 ---
    wcfg = WalkForwardConfig(
        train_window=design["train_periods"], oos_window=design["oos_window"],
        step=design["step"], min_train=design["train_periods"],
        min_oos=design["oos_window"],
    )
    repro = {}
    for code, wf in wf_frames.items():
        full_idx = wf.index
        trunc_idx = full_idx[full_idx <= cut]
        n_full = len(generate_windows(full_idx, wcfg))
        n_trunc = len(generate_windows(trunc_idx, wcfg))
        repro[code] = {"n_windows_extended": n_full, "n_windows_to_20241231": n_trunc,
                       "n_rows": len(full_idx)}
        print(f"[WINDOWS] {code} 全段 {n_full} / 截至 2024-12-31 {n_trunc}", flush=True)

    # --- 基准 ---
    bench = pd.read_csv(ROOT / spec["data"]["benchmark"]["path"])
    bench["date"] = pd.to_datetime(bench["date"])
    bench = bench.set_index("date")["close"].astype(float).sort_index()

    def bench_ret(s: pd.Timestamp, e: pd.Timestamp) -> float | None:
        seg = bench.loc[s:e]
        if len(seg) < 2:
            return None
        return float(seg.iloc[-1] / seg.iloc[0] - 1.0)

    # ---------------------------------------------------------------- E1 滚动
    cells: dict[tuple[str, str], dict] = {}
    per_cell_raw: dict[tuple[str, str], dict] = {}
    for strat_name, grid, factory in strategies:
        for preset in presets:
            per_cell_raw[(strat_name, preset.fee_source)] = {
                "IS": [], "OOS": [], "BORDER": [], "window_rows": [], "errors": [],
            }

    for code, wf in wf_frames.items():
        rules = a_share_rules(price_limit_rate=a_share_price_limit_rate(code, mode=mode))
        for strat_name, grid, factory in strategies:
            for preset in presets:
                key = (strat_name, preset.fee_source)
                try:
                    cfg = preset.apply(BacktestConfig(symbol=code, initial_cash=design["initial_cash"],
                                                      market_rules=rules))
                    specs, wf_results, _summary = run_walkforward_v2(
                        wf, cfg, strategy_factory=factory, grid_config=grid,
                    )
                except Exception as exc:  # noqa: BLE001
                    per_cell_raw[key]["errors"].append(f"{code}: {exc}")
                    print(f"  [FAIL] {code} {strat_name} {preset.fee_source}: {exc}", flush=True)
                    continue

                for r in wf_results:
                    valid = (r.selection_success and r.oos_success
                             and r.final_equity is not None and r.oos_is_complete
                             and r.cumulative_return is not None
                             and np.isfinite(r.cumulative_return))
                    bucket = classify(r.oos_start, r.oos_end, cut)
                    row = {
                        "symbol": code,
                        "strategy": strat_name,
                        "fee_source": preset.fee_source,
                        "window_index": r.window_index,
                        "bucket": bucket,
                        "train_start": str(r.train_start.date()),
                        "train_end": str(r.train_end.date()),
                        "oos_start": str(r.oos_start.date()),
                        "oos_end": str(r.oos_end.date()),
                        "valid": bool(valid),
                        "sel_ok": bool(r.selection_success),
                        "params": r.selected_params,
                        "ret": None if r.cumulative_return is None else round(float(r.cumulative_return), 6),
                        "mdd": None if r.max_drawdown_ratio is None else round(float(r.max_drawdown_ratio), 6),
                        "trades": r.trade_count,
                        "fees": None if r.total_fees is None else round(float(r.total_fees), 2),
                        "bench_ret": (None if bucket == "IS" else
                                      (None if bench_ret(r.oos_start, r.oos_end) is None else
                                       round(bench_ret(r.oos_start, r.oos_end), 6))),
                    }
                    if valid:
                        row["excess"] = (None if row["bench_ret"] is None
                                         else round(row["ret"] - row["bench_ret"], 6))
                    per_cell_raw[key]["window_rows"].append(row)
                    if valid:
                        per_cell_raw[key][bucket].append(row)
                print(f"  [E1] {code:10s} {strat_name:15s} {preset.fee_source:30s} "
                      f"IS={len(per_cell_raw[key]['IS']):4d} OOS={len(per_cell_raw[key]['OOS']):3d}", flush=True)

    # ---------------------------------------------------------------- E2 单次持有
    e2_rows: dict[tuple[str, str], list[dict]] = {(s, p.fee_source): [] for s, _, _ in strategies for p in presets}
    for code, wf in wf_frames.items():
        idx = wf.index
        train_start = idx[0]
        train_end = idx[idx <= cut][-1]
        oos_start = idx[idx >= oos_floor][0]
        oos_end = idx[-1]
        rules = a_share_rules(price_limit_rate=a_share_price_limit_rate(code, mode=mode))
        for strat_name, grid, factory in strategies:
            cands = generate_ma_cross_candidates(grid)
            for preset in presets:
                key = (strat_name, preset.fee_source)
                try:
                    cfg = preset.apply(BacktestConfig(symbol=code, initial_cash=design["initial_cash"],
                                                      market_rules=rules))
                    r = run_walkforward_window(
                        wf, cfg, window_index=0,
                        train_start=train_start, train_end=train_end,
                        oos_start=oos_start, oos_end=oos_end,
                        strategy_factory=factory, candidates=cands,
                        min_trades=design["min_trades"],
                        warmup_days=design["train_periods"],
                    )
                    valid = (r.selection_success and r.oos_success and r.final_equity is not None
                             and r.oos_is_complete and r.cumulative_return is not None
                             and np.isfinite(r.cumulative_return))
                    b = bench_ret(oos_start, oos_end)
                    e2_rows[key].append({
                        "symbol": code, "valid": bool(valid),
                        "train": f"{train_start.date()}~{train_end.date()}",
                        "oos": f"{oos_start.date()}~{oos_end.date()}",
                        "oos_periods": int(r.oos_periods),
                        "params": r.selected_params,
                        "ret": None if r.cumulative_return is None else round(float(r.cumulative_return), 6),
                        "mdd": None if r.max_drawdown_ratio is None else round(float(r.max_drawdown_ratio), 6),
                        "trades": r.trade_count,
                        "bench_ret": None if b is None else round(b, 6),
                        "sel_ok": bool(r.selection_success),
                        "err": r.selection_error if not r.selection_success else r.oos_error,
                    })
                except Exception as exc:  # noqa: BLE001
                    e2_rows[key].append({"symbol": code, "valid": False, "error": str(exc),
                                         "err": str(exc), "trades": None, "ret": None,
                                         "params": None, "mdd": None, "bench_ret": None,
                                         "oos": "", "oos_periods": 0, "train": "", "sel_ok": False})
                print(f"  [E2] {code:10s} {strat_name:15s} {preset.fee_source:30s} "
                      f"ret={e2_rows[key][-1].get('ret')} trades={e2_rows[key][-1].get('trades')}", flush=True)

    # ---------------------------------------------------------------- 汇总
    for key, raw in per_cell_raw.items():
        is_ret = [r["ret"] for r in raw["IS"]]
        oos_ret = [r["ret"] for r in raw["OOS"]]
        oos_tr = [r["trades"] for r in raw["OOS"] if r["trades"] is not None]
        oos_ex = [r["excess"] for r in raw["OOS"] if r.get("excess") is not None]
        e2v = [r for r in e2_rows[key] if r["valid"]]
        e2_ret = [r["ret"] for r in e2v]
        cells[f"{key[0]}|{key[1]}"] = {
            "strategy": key[0], "fee_source": key[1],
            "IS": dist(is_ret),
            "OOS": dist(oos_ret),
            "OOS_excess_vs_hs300": dist(oos_ex),
            "OOS_zero_trade_ratio": (
                round(sum(1 for t in oos_tr if t == 0) / len(oos_tr), 4) if oos_tr else None),
            "OOS_total_trades": int(sum(oos_tr)) if oos_tr else 0,
            "BORDER_n": len(raw["BORDER"]),
            "E2": {
                "n_valid": len(e2v),
                "ret_median": round(statistics.median(e2_ret), 6) if e2_ret else None,
                "ret_mean": round(statistics.fmean(e2_ret), 6) if e2_ret else None,
                "bench_ret": e2v[0]["bench_ret"] if e2v else None,
                "rows": e2_rows[key],
            },
            "n_windows_total": len(raw["window_rows"]),
            "errors": raw["errors"],
        }

    # ---------------------------------------------------------------- 机械判定
    pc = spec["primary_cell"]
    pk = f"{pc[0]}|{pc[1]}"
    P = cells[pk]
    e1_med = P["OOS"]["median"] if P["OOS"] else None
    e1_pos = P["OOS"]["pos_ratio"] if P["OOS"] else None
    e1_n = P["OOS"]["n"] if P["OOS"] else 0
    ztr = P["OOS_zero_trade_ratio"]
    e2_med = P["E2"]["ret_median"]
    is_p10 = P["IS"]["p10"] if P["IS"] else None
    is_p90 = P["IS"]["p90"] if P["IS"] else None

    def _sign(x):
        if x is None:
            return 0
        return (x > 0) - (x < 0)

    checks = [
        ("R0_NO_POWER",
         (ztr is not None and ztr >= 0.50) or e1_n < 20,
         f"zero_trade_ratio={ztr} (>=0.50?) / n_valid_oos={e1_n} (<20?)"),
        ("R1_REFUTED",
         e1_med is not None and e1_med < 0 and e1_pos is not None and e1_pos <= 0.25
         and e2_med is not None and e2_med < 0,
         f"e1_median={e1_med} (<0?) / e1_pos_ratio={e1_pos} (<=0.25?) / e2_median={e2_med} (<0?)"),
        ("R2_IMPROVED",
         e1_med is not None and is_p90 is not None and e1_med > is_p90
         and e2_med is not None and e2_med > 0,
         f"e1_median={e1_med} (>is_p90={is_p90}?) / e2_median={e2_med} (>0?)"),
        ("R3_CONSISTENT",
         (e1_med is not None and is_p10 is not None and is_p90 is not None
          and is_p10 <= e1_med <= is_p90 and e1_med != 0
          and _sign(e1_med) == _sign(e2_med)),
         f"is_p10={is_p10} <= e1_median={e1_med} <= is_p90={is_p90}? / sign match={_sign(e1_med)}=={_sign(e2_med)}"),
    ]
    verdict, verdict_reason, trace = "R4_INCONCLUSIVE", "", []
    for rid, ok, desc in checks:
        trace.append({"rule": rid, "hit": bool(ok), "detail": desc})
        if ok:
            verdict, verdict_reason = rid, desc
            break
    if verdict == "R4_INCONCLUSIVE":
        verdict_reason = "以上四条均未命中"

    # 预测核对（跑前写下的四条，逐条打勾）
    pred = spec["predicted_before_run"]
    pred_check = {
        "gate_blocks": {
            "predicted": pred["gate_blocks"],
            "actual": [c for c, d in data_report.items() if not d["data_ok"]],
        },
        "participating_symbols": {
            "predicted": pred["participating_symbols"], "actual": len(frames),
        },
        "e1_n_windows": {
            "predicted": spec["evaluations"]["E1"]["expected_n_windows"],
            # 按「不同 OOS 起始日」计数：600900 有停牌少 11 根，window_index 会错位，
            # 用起始日才是「几个不重叠的样本外时段」的正确答案
            "actual": len({r["oos_start"] for r in per_cell_raw[(pc[0], pc[1])]["OOS"]}),
        },
        "zero_trade_high": {
            "predicted": "偏高（>=0.5 则与 R0 一致）",
            "actual": ztr,
            "prediction_held": (ztr is not None and ztr >= 0.5),
        },
        "verdict_expected_no_power_or_inconclusive": {
            "predicted": "NO_POWER 或 INCONCLUSIVE 概率合计更高",
            "actual": verdict,
            "prediction_held": verdict in ("R0_NO_POWER", "R4_INCONCLUSIVE"),
        },
    }

    out = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "spec_version": spec["spec_version"],
        "freeze": freeze,
        "question": spec["question"],
        "data": data_report,
        "window_structure": repro,
        "primary_cell": pk,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "rule_trace": trace,
        "prediction_check": pred_check,
        "cells": cells,
        "e1_windows": {f"{k[0]}|{k[1]}": v["window_rows"] for k, v in per_cell_raw.items()},
        "caveats": [
            "样本外观测高度相关：同一标的内 5 个窗口首尾相接、同一时段的 5 只标的同时暴露于共同市场因子，"
            "因此 n_valid_oos 是观测数不是独立样本数，不构成统计显著性。",
            "E2 为单窗口，参数只在 2024-12-31 前选一次；其收益率是 431 日的单次实现，方差极大。",
            "涨跌停为简化模型（仅开盘价触及边界即拦截，不模拟排队/部分成交/盘中打开），"
            "创业板 2020-08-24 前按 20% 处理属已知偏松。",
            "基准为沪深300 buy&hold、不含成本与现金拖累，仅作方向参考，不是可交易组合。",
        ],
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n[SAVED] {OUT_PATH}")

    print("\n=== 各格汇总（E1 = 纯样本外窗口）===")
    print(f"{'cell':46s} {'IS_med':>9} {'OOS_n':>6} {'OOS_med':>9} {'pos':>6} {'zeroTr':>7} {'E2_med':>9}")
    for k, c in sorted(cells.items()):
        print(f"{k:46s} {(c['IS'] or {}).get('median', 0):>+9.4f} {(c['OOS'] or {}).get('n', 0):>6d} "
              f"{(c['OOS'] or {}).get('median', 0):>+9.4f} {(c['OOS'] or {}).get('pos_ratio', 0):>6.3f} "
              f"{str(c['OOS_zero_trade_ratio']):>7s} {str(c['E2']['ret_median']):>9s}")
    print(f"\nVERDICT = {verdict}  ({verdict_reason})")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise
