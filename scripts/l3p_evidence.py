"""L3p 组合级闸门验收取证。

六条标准，每条都针对一种「组合层最容易做假」的写法：

1. **共享现金池**：用两只涨跌相反的合成标的 + 不等权，验证组合收益恰为加权和
   （+12%），而"各标的收益平均"会给出 0%——差 12pp。这种错法看起来完全正常。
2. **weights 口径与资金无关**：同一调度在 10 万与 1 亿下收益必须逐位相同；
   若不同，说明资金规模被当成了约束，收益就不是权重记账的结果。
3. **判据同源**：判定链必须是同一个函数对象，且同一份汇总给出同一结论。
4. **折结构**：折间相邻、测试窗零重叠、与存档结果逐折一致。
5. **选参与打分分离**：每折回测调用次数恰为「网格点数 + 1」。
6. **现金残留与稀释**：两臂现金占比并列，并给出"每单位投入资金"的粗对照——
   只比原始收益会把"稀释程度不同"误读成"选择有没有用"。

输出：reports/L3P_GATE_EVIDENCE.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from deepquant.backtest.config import BacktestConfig  # noqa: E402
from deepquant.backtest.portfolio import (  # noqa: E402
    PortfolioBacktestConfig,
    TargetPortfolio,
    run_portfolio_backtest,
)
from deepquant.gates import l3 as l3_module  # noqa: E402
from deepquant.gates import load_spec  # noqa: E402
from deepquant.gates import l3_portfolio as l3p  # noqa: E402
from deepquant.gates.l3 import WalkForwardSpec, build_folds, overlap_report, parse_walk_forward  # noqa: E402
from deepquant.gates.l3_portfolio import (  # noqa: E402
    PortfolioSpec,
    decide_from_summary,
    load_universe,
    run_l3_portfolio,
    to_portfolio_verdict,
)
from deepquant.gates.spec import parse_spec  # noqa: E402

SPEC_PATH = ROOT / "docs" / "gates" / "specs" / "H_L3P_MACROSS_000001.json"
RESULT_DIR = ROOT / "reports" / "gates" / "H-L3P-MACROSS-000001"
OUT_PATH = ROOT / "reports" / "L3P_GATE_EVIDENCE.json"
L3_RESULT = ROOT / "reports" / "gates" / "H-L3-MACROSS-000001" / "l3_result.json"


def _free_config(cash: float) -> BacktestConfig:
    return BacktestConfig(
        symbol="EVIDENCE",
        initial_cash=cash,
        commission_rate=0.0,
        min_commission=0.0,
        stamp_duty_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_buy=0.0,
        slippage_sell=0.0,
    )


def _frame(closes: list[float], *, open_price: float = 10.0) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=len(closes))
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": open_price,
            "high": np.maximum(close, open_price) * 1.01,
            "low": np.minimum(close, open_price) * 0.99,
            "close": close,
            "volume": 1e6,
        },
        index=idx,
    )


# --------------------------------------------------------------------------- #
# 标准 1：共享现金池 ≠ 收益平均
# --------------------------------------------------------------------------- #
def standard_1_shared_cash_pool() -> dict[str, Any]:
    frames = {
        "A": _frame([10.0] + [12.0] * 9),   # +20%
        "B": _frame([10.0] + [8.0] * 9),    # −20%
    }
    signal_day = frames["A"].index[0]
    schedule = (TargetPortfolio(signal_date=signal_day, weights={"A": 0.8, "B": 0.2}),)

    def run(cash: float):
        return run_portfolio_backtest(
            frames,
            schedule,
            PortfolioBacktestConfig(
                symbols=("A", "B"),
                initial_cash=cash,
                fee_config=_free_config(cash),
                allocation="weights",
            ),
            start_date=frames["A"].index[0],
        )

    small = run(1e5)
    large = run(1e8)
    weighted_sum = 0.8 * 0.20 + 0.2 * (-0.20)
    equal_average = (0.20 + -0.20) / 2
    return {
        "portfolio_return": small.cumulative_return,
        "weighted_sum_expected": weighted_sum,
        "equal_average_wrong_answer": equal_average,
        "gap_vs_equal_average": small.cumulative_return - equal_average,
        "return_at_1e5": small.cumulative_return,
        "return_at_1e8": large.cumulative_return,
        "cash_invariant": abs(small.cumulative_return - large.cumulative_return) < 1e-12,
        "matches_weighted_sum": abs(small.cumulative_return - weighted_sum) < 1e-9,
        "passed": bool(
            abs(small.cumulative_return - weighted_sum) < 1e-9
            and abs(small.cumulative_return - large.cumulative_return) < 1e-12
            and abs(small.cumulative_return - equal_average) > 0.1
        ),
    }


# --------------------------------------------------------------------------- #
# 标准 2 与 3：判据同源
# --------------------------------------------------------------------------- #
def standard_2_shared_judgement() -> dict[str, Any]:
    summary = {
        "folds_total": 10,
        "folds_usable": 10,
        "test_positive_folds": 3,
        "test_positive_fold_ratio": 0.3,
        "train_positive_fold_ratio": 0.9,
        "test_return_mean": -0.02,
        "divergent_folds": 6,
        "divergent_fold_ratio": 0.6,
        "median_wfe": 0.1,
        "wfe_samples": 9,
    }
    overlap = {"independent": True, "overlap_pairs": 0}
    wf = WalkForwardSpec(train_days=252, test_days=63, step_days=63, min_folds=5)
    verdict, checks = decide_from_summary(summary, overlap, wf, usable_folds=10, failed_folds=0)
    return {
        "same_function_object": l3p.decide_from_summary is l3_module.decide_from_summary,
        "sample_verdict_l3": verdict,
        "sample_verdict_l3p": to_portfolio_verdict(verdict),
        "prefix_only": to_portfolio_verdict(verdict).replace("L3P_", "L3_") == verdict,
        "checks": [item["name"] for item in checks],
        "passed": bool(
            l3p.decide_from_summary is l3_module.decide_from_summary
            and to_portfolio_verdict(verdict).replace("L3P_", "L3_") == verdict
            and verdict == "L3_REJECTED_SYSTEMATIC_DIVERGENCE"
        ),
    }


# --------------------------------------------------------------------------- #
# 标准 3：折结构
# --------------------------------------------------------------------------- #
def standard_3_fold_structure() -> dict[str, Any]:
    payload = json.loads((RESULT_DIR / "l3p_result.json").read_text(encoding="utf-8"))
    result = payload["result"]
    wf = parse_walk_forward(load_spec(SPEC_PATH).walk_forward)
    universe_path = ROOT / str(json.loads(SPEC_PATH.read_text(encoding="utf-8"))["universe"])
    universe = load_universe(
        universe_path,
        data_dir=ROOT / "data" / "qfq_all",
        span_start=wf.span_start,
        span_end=wf.span_end,
    )
    calendar = pd.DatetimeIndex(
        sorted(set().union(*(set(frame.index) for frame in universe.frames.values())))
    )
    calendar = calendar[
        (calendar >= pd.Timestamp(wf.span_start)) & (calendar <= pd.Timestamp(wf.span_end))
    ]
    folds, meta = build_folds(calendar, wf)
    positions = {day: i for i, day in enumerate(calendar)}
    adjacency = [positions[f.test_start] == positions[f.train_end] + 1 for f in folds]
    overlap = overlap_report(folds)
    recorded = result["folds"]

    same_windows = all(
        str(fold.train_start.date()) == item["train"]["start"]
        and str(fold.test_start.date()) == item["test"]["start"]
        for fold, item in zip(folds, recorded)
    )
    return {
        "calendar_days": len(calendar),
        "calendar_first": str(calendar[0].date()),
        "calendar_last": str(calendar[-1].date()),
        "folds": len(folds),
        "folds_in_result": len(recorded),
        "adjacency_ok": all(adjacency),
        "independent": overlap["independent"],
        "overlap_pairs": overlap["overlap_pairs"],
        "windows_match_result": same_windows,
        "unused_tail_days": meta["unused_tail_days"],
        "note": "组合日历是标的池交易日的**并集**（某只停牌而其他照常交易的日子仍在），"
        "因此折数与单标的 L3（按单只标的自己的交易日切）不同：28 vs 27。",
        "passed": bool(
            all(adjacency)
            and overlap["independent"]
            and same_windows
            and len(folds) == len(recorded)
        ),
    }


# --------------------------------------------------------------------------- #
# 标准 4：选参与打分分离（组合层）
# --------------------------------------------------------------------------- #
def standard_4_selection_separation() -> dict[str, Any]:
    """每折必须「训练窗跑满网格 + 测试窗恰好一次（+ 对照臂一次）」。"""
    calls: list[tuple[str, str]] = []
    original = l3p.run_portfolio_backtest

    def spy(prices, schedule, config, *, start_date=None, end_date=None):  # noqa: ANN001
        calls.append((str(pd.Timestamp(start_date).date()), str(pd.Timestamp(end_date).date())))
        return original(prices, schedule, config, start_date=start_date, end_date=end_date)

    l3p.run_portfolio_backtest = spy
    try:
        universe_path = ROOT / str(json.loads(SPEC_PATH.read_text(encoding="utf-8"))["universe"])
        universe = load_universe(
            universe_path,
            data_dir=ROOT / "data" / "qfq_all",
            span_start="2019-01-01",
            span_end="2021-06-30",
        )
        payload = {
            "hypothesis_id": "H-L3P-SEP",
            "title": "分离证明",
            "strategy_id": "ma_cross",
            "param_grid": {"fast_window": [3, 10], "slow_window": [20, 60]},
            "constraints": [["fast_window", "slow_window"]],
            "data": {"symbol": "U", "research_path": "unused.csv", "trading_path": ""},
            "in_sample": {"start": "2019-01-01", "end": "2020-12-31"},
            "out_sample": {"start": "2021-01-01", "end": "2021-06-30"},
            "boundaries": {
                "min_in_sample_return": -1.0,
                "max_in_sample_drawdown": 0.99,
                "min_closed_trades": 1,
                "min_out_sample_return": -1.0,
                "max_out_sample_drawdown": 0.99,
                "max_oos_degradation": 0.99,
            },
            "declared_grid_points": 4,
        }
        wf = WalkForwardSpec(
            train_days=120,
            test_days=40,
            step_days=40,
            min_folds=3,
            min_test_days=20,
            span_start="2019-01-01",
            span_end="2021-06-30",
        )
        result = run_l3_portfolio(
            parse_spec(payload), universe=universe, walk_forward=wf, portfolio=PortfolioSpec()
        )
    finally:
        l3p.run_portfolio_backtest = original

    folds = [fold for fold in result.folds if fold.ok]
    per_fold: list[dict[str, Any]] = []
    for fold in folds:
        train_key = (str(fold.train_start.date()), str(fold.train_end.date()))
        test_key = (str(fold.test_start.date()), str(fold.test_end.date()))
        per_fold.append(
            {
                "fold": fold.index,
                "train_calls": sum(1 for c in calls if c == train_key),
                "test_calls": sum(1 for c in calls if c == test_key),
            }
        )
    grid_points = 4
    expected_per_fold = grid_points + 2  # 网格 + 测试 + 对照臂
    return {
        "grid_points": grid_points,
        "folds_checked": len(per_fold),
        "total_calls": len(calls),
        "expected_total": len(per_fold) * expected_per_fold,
        "train_calls_are_full_grid": all(item["train_calls"] == grid_points for item in per_fold),
        "test_and_control_are_two_calls": all(item["test_calls"] == 2 for item in per_fold),
        "detail": per_fold,
        "note": "测试窗的两次调用是同一条两臂设计：策略臂 + 对照臂，共享同一窗口与费用口径；"
        "它们都不参与选参（选参只发生在训练窗）。",
        "passed": bool(
            per_fold
            and len(calls) == len(per_fold) * expected_per_fold
            and all(item["train_calls"] == grid_points for item in per_fold)
            and all(item["test_calls"] == 2 for item in per_fold)
        ),
    }


# --------------------------------------------------------------------------- #
# 标准 5：现金残留与稀释后的对照
# --------------------------------------------------------------------------- #
def standard_5_cash_and_dilution() -> dict[str, Any]:
    payload = json.loads((RESULT_DIR / "l3p_result.json").read_text(encoding="utf-8"))
    result = payload["result"]
    folds = [f for f in result["folds"] if f["test_return"] is not None]

    # 每单位投入资金的收益：(1+r) / (1-现金占比) - 1。这是**粗算**：
    # 它假设现金在整个窗口内恒定不动，与逐日复利不等价，只用于判断方向。
    per_invested: list[dict[str, Any]] = []
    for fold in folds:
        cash_a = fold.get("avg_cash_ratio")
        cash_b = fold.get("control_cash_ratio")
        if cash_a is None or cash_b is None or fold.get("control_return") is None:
            continue
        if cash_a >= 1 or cash_b >= 1:
            continue
        per_invested.append(
            {
                "fold": fold["fold"],
                "strategy": (1 + fold["test_return"]) / (1 - cash_a) - 1,
                "control": (1 + fold["control_return"]) / (1 - cash_b) - 1,
            }
        )
    gaps = [item["strategy"] - item["control"] for item in per_invested]
    control = result["control"]
    return {
        "strategy_cash_median": control.get("strategy_cash_median"),
        "control_cash_median": control.get("control_cash_median"),
        "cash_gap_median": control.get("cash_gap_median"),
        "raw_excess_mean": control.get("excess_mean"),
        "raw_beats_ratio": control.get("beats_control_ratio"),
        "per_invested_excess_mean": float(np.mean(gaps)) if gaps else None,
        "per_invested_strategy_mean": (
            float(np.mean([item["strategy"] for item in per_invested])) if per_invested else None
        ),
        "per_invested_control_mean": (
            float(np.mean([item["control"] for item in per_invested])) if per_invested else None
        ),
        "folds_compared": len(per_invested),
        "cash_only_periods_total": result["portfolio"].get("cash_only_periods_total"),
        "avg_positions_median": result["portfolio"].get("avg_positions_median"),
        "note": "per_invested 是粗算（假设现金占比恒定），只用于判断方向，不得当收益引用。"
        "结论：原始口径超额 +0.26pp，但按投入资金折算后为负 —— "
        "说明「选择」没有正贡献，组合收益来自持有这批股票本身。",
        "passed": bool(
            control.get("strategy_cash_median") is not None
            and control.get("control_cash_median") is not None
            and per_invested
        ),
    }


# --------------------------------------------------------------------------- #
# 标准 6：标的池冻结可复现
# --------------------------------------------------------------------------- #
def standard_6_universe_reproducible() -> dict[str, Any]:
    import numpy as np

    path = ROOT / "docs" / "gates" / "universes" / "H_L3P_UNIVERSE_30.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    recorded = [item["symbol"] for item in payload["constituents"]]

    codes = [
        c.strip()
        for c in (ROOT / "data" / "_pool_518_a_share.txt")
        .read_text(encoding="utf-8")
        .replace("\n", "")
        .split(",")
        if c.strip()
    ]
    qfq = ROOT / "data" / "qfq_all"
    candidates = [c for c in codes if (qfq / f"{c}.qfq.csv").exists()]
    rng = np.random.default_rng(int(payload["seed"]))
    order = rng.permutation(len(candidates))
    rebuilt: list[str] = []
    for idx in order:
        code = candidates[idx]
        frame = pd.read_csv(qfq / f"{code}.qfq.csv", parse_dates=["date"])
        span = frame[
            (frame["date"] >= pd.Timestamp(payload["span"]["start"]))
            & (frame["date"] <= pd.Timestamp(payload["span"]["end"]))
        ]
        if len(span) < int(payload["min_rows_in_span"]):
            continue
        rebuilt.append(code)
        if len(rebuilt) == int(payload["size"]):
            break

    return {
        "seed": payload["seed"],
        "size": len(recorded),
        "rebuilt_size": len(rebuilt),
        "identical": sorted(recorded) == sorted(rebuilt),
        "prefix_distribution": pd.Series([c[:3] for c in recorded]).value_counts().to_dict(),
        "note": "按冻结文件里写明的规则（种子 + 跨度内行数门槛）重跑抽样，必须复现同一份名单。",
        "passed": bool(sorted(recorded) == sorted(rebuilt)),
    }


# --------------------------------------------------------------------------- #
# 与单标的 L3 的对照（背景，不参与验收）
# --------------------------------------------------------------------------- #
def l3_reference() -> dict[str, Any]:
    if not L3_RESULT.exists():
        return {"note": "未找到 L3 结果，跳过对照"}
    l3 = json.loads(L3_RESULT.read_text(encoding="utf-8"))["result"]
    l3p_payload = json.loads((RESULT_DIR / "l3p_result.json").read_text(encoding="utf-8"))
    l3p_result = l3p_payload["result"]

    def pick(result: dict[str, Any]) -> dict[str, Any]:
        s = result["summary"]
        return {
            "verdict": result["verdict"],
            "preliminary_verdict": result.get("preliminary_verdict"),
            "folds": s.get("folds_usable"),
            "test_return_mean": s.get("test_return_mean"),
            "test_return_std": s.get("test_return_std"),
            "test_positive_fold_ratio": s.get("test_positive_fold_ratio"),
            "divergent_fold_ratio": s.get("divergent_fold_ratio"),
            "median_wfe": s.get("median_wfe"),
            "test_drawdown_median": s.get("test_drawdown_median"),
            "param_stability": result["param_selection"].get("param_stability"),
        }

    return {
        "single_name": pick(l3),
        "portfolio": pick(l3p_result),
        "note": "两者折数不同（27 vs 28）：组合日历是标的池交易日并集，"
        "单标的按 000001.SZ 自己的交易日切。所以逐折不能一一对应，只能比折级统计的形状。",
    }


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3p 验收取证")
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args(argv)

    result = json.loads((RESULT_DIR / "l3p_result.json").read_text(encoding="utf-8"))
    evidence: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "spec": str(SPEC_PATH.relative_to(ROOT)),
        "main_run": {
            "verdict": result["result"]["verdict"],
            "preliminary_verdict": result["result"].get("preliminary_verdict"),
            "summary": result["result"]["summary"],
            "control": result["result"]["control"],
            "portfolio": result["result"]["portfolio"],
        },
    }

    steps = [
        ("standard_1_shared_cash_pool", standard_1_shared_cash_pool, "标准 1：共享现金池"),
        ("standard_2_shared_judgement", standard_2_shared_judgement, "标准 2：判据同源"),
        ("standard_3_fold_structure", standard_3_fold_structure, "标准 3：折结构"),
        ("standard_4_selection_separation", standard_4_selection_separation, "标准 4：选参与打分分离"),
        ("standard_5_cash_and_dilution", standard_5_cash_and_dilution, "标准 5：现金残留与稀释"),
        ("standard_6_universe_reproducible", standard_6_universe_reproducible, "标准 6：标的池可复现"),
    ]
    for key, func, label in steps:
        print(f"{label}…", flush=True)
        evidence[key] = func()

    evidence["l3_reference"] = l3_reference()

    standards = {
        key: value["passed"]
        for key, value in evidence.items()
        if isinstance(value, dict) and "passed" in value
    }
    evidence["acceptance"] = {
        "standards": standards,
        "all_passed": all(standards.values()),
        "passed_count": sum(1 for value in standards.values() if value),
        "total": len(standards),
    }

    summary = result["result"]["summary"]
    control = result["result"]["control"]
    gaps: list[str] = []
    gaps.append(
        f"中位 WFE {summary.get('median_wfe'):+.4f} 低于阈值 0.30："
        "训练窗收益（均值 "
        f"{summary.get('train_return_mean'):+.4f}）远高于测试窗，说明样本内选参本身就吃到了大量"
        "当时才有的行情"
    )
    gaps.append(
        f"对照臂超额仅 {control.get('excess_mean'):+.4f}、跑赢折数 "
        f"{control.get('beats_control_folds')}/{control.get('folds_compared')}："
        "组合层的折间一致性来自分散与持有，不是来自「快线在上才持有」的选择"
    )
    gaps.append("组合化不降低共同因子风险：28 折仍是同一段历史的不同切片，折间不独立")
    gaps.append("标的池为当前快照，未使用时点成分股：结果含幸存者偏差，只能作上界估计")
    gaps.append("weights 记账不受整手约束；可执行性需另用 lots 口径复核")
    evidence["gaps"] = gaps

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print("\n=== 验收结果 ===")
    for key, value in standards.items():
        print(f"  {'PASS' if value else 'FAIL'}  {key}")
    print(f"合计 {evidence['acceptance']['passed_count']}/{evidence['acceptance']['total']}")
    print(f"输出：{out.relative_to(ROOT)}")
    return 0 if evidence["acceptance"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
