"""L3 Walk-Forward 闸门验收取证。

六条标准，每条都对应一种「会造假」的写法：

1. 窗口结构自证——训练/测试窗严格相邻、测试窗两两不重叠（用真实折逐日核对）。
2. 选参/打分分离——每折回测调用次数必须恰为「网格点数 + 1」，且测试窗那次
   用的参数等于训练窗最优。用调用记录证明，不靠读代码。
3. 判定链可分辨——三种合成市况必须分别落到 PASSED / 系统性背离 / 折间不一致。
   只会说"通过"的闸门等于没有闸门。
4. 终态分类不混用——折数不足是不可判定、数据不足是数据问题、窗口不够是窗口问题。
5. 不构造连续净值——结果结构里不许有净值序列；并给出"若强行复利"的反例数字，
   说明那条路为什么被禁。
6. 前置可追溯——前序终态读自结果文件，缺失记 UNKNOWN，绝不默认通过。

输出：reports/L3_GATE_EVIDENCE.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from deepquant.gates import load_spec  # noqa: E402
from deepquant.gates import sweep as sweep_module  # noqa: E402
from deepquant.gates.l3 import (  # noqa: E402
    VERDICT_DATA,
    VERDICT_DIVERGENCE,
    VERDICT_INCONCLUSIVE,
    VERDICT_INCONSISTENCY,
    VERDICT_PASSED,
    VERDICT_WINDOW,
    WalkForwardSpec,
    build_folds,
    overlap_report,
    parse_walk_forward,
    run_l3,
)
from deepquant.gates.spec import parse_spec  # noqa: E402
from deepquant.strategies.base import Signal, Strategy  # noqa: E402

SPEC_PATH = ROOT / "docs" / "gates" / "specs" / "H_L3_MACROSS_000001.json"
RESULT_DIR = ROOT / "reports" / "gates" / "H-L3-MACROSS-000001"
OUT_PATH = ROOT / "reports" / "L3_GATE_EVIDENCE.json"


# --------------------------------------------------------------------------- #
# 合成市况（用于证明判定链能分辨好坏）
# --------------------------------------------------------------------------- #
def _frame(prices: np.ndarray) -> pd.DataFrame:
    idx = pd.bdate_range("2018-01-01", periods=len(prices))
    close = np.asarray(prices, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]]) * 1.001
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    return pd.DataFrame(
        {"date": idx, "open": open_, "high": high, "low": low, "close": close, "volume": 1e6}
    ).set_index("date")


def _block(n: int, start: float, kind: str, *, up: float = 0.005, amp: float = 0.09, cycle: int = 2) -> np.ndarray:
    out = [start]
    for i in range(1, n):
        if kind == "up":
            out.append(out[-1] * (1 + up))
        else:
            phase = i % (2 * cycle)
            out.append(out[-1] * (1 + (amp if phase < cycle else -amp)))
    return np.array(out)


def _series(pattern: str, *, start: float = 10.0, block_days: int = 20) -> np.ndarray:
    values = list((start * np.cumprod(np.full(20, 0.996))).tolist())
    level = values[-1]
    for char in pattern:
        segment = _block(block_days + 1, level, "up" if char == "U" else "chop")[1:]
        values.extend(segment.tolist())
        level = segment[-1]
    return np.array(values)


class TrendStateStrategy(Strategy):
    """状态型趋势策略：快线在上就持有（每个窗口都会重新建仓）。"""

    name = "trend_state"
    version = "0.0.1"

    def __init__(self, fast_window: int = 3, slow_window: int = 10) -> None:
        self.fast_window = fast_window
        self.slow_window = slow_window

    @property
    def params(self) -> dict:
        return {"fast_window": self.fast_window, "slow_window": self.slow_window}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"]
        fast = close.rolling(self.fast_window, min_periods=self.fast_window).mean()
        slow = close.rolling(self.slow_window, min_periods=self.slow_window).mean()
        out = pd.Series(int(Signal.HOLD), index=close.index, dtype="int8")
        out[fast > slow] = int(Signal.BUY)
        out[fast < slow] = int(Signal.SELL)
        return out


def _factory(strategy_id: str, params: dict) -> Strategy:
    return TrendStateStrategy(**params)


SYNTH_GRID = {"fast_window": [3, 5], "slow_window": [10, 20]}
SYNTH_POINTS = 4
SYNTH_WF = WalkForwardSpec(train_days=40, test_days=20, step_days=20, min_folds=5, min_test_days=10)


def _synth_spec(frame: pd.DataFrame) -> dict:
    return {
        "hypothesis_id": "H-L3-SYNTH",
        "title": "合成用例",
        "strategy_id": "trend_state",
        "param_grid": SYNTH_GRID,
        "constraints": [["fast_window", "slow_window"]],
        "data": {"symbol": "SYN", "research_path": "unused.csv", "trading_path": ""},
        "in_sample": {"start": str(frame.index[0].date()), "end": str(frame.index[-22].date())},
        "out_sample": {"start": str(frame.index[-21].date()), "end": str(frame.index[-1].date())},
        "boundaries": {
            "min_in_sample_return": -1.0,
            "max_in_sample_drawdown": 0.99,
            "min_closed_trades": 1,
            "min_out_sample_return": -1.0,
            "max_out_sample_drawdown": 0.99,
            "max_oos_degradation": 0.99,
        },
        "declared_grid_points": SYNTH_POINTS,
    }


def _synth_run(pattern: str, wf: WalkForwardSpec = SYNTH_WF):
    frame = _frame(_series(pattern))
    return run_l3(parse_spec(_synth_spec(frame)), data=frame, walk_forward=wf, factory=_factory)


# --------------------------------------------------------------------------- #
# 标准 1：窗口结构
# --------------------------------------------------------------------------- #
def standard_1_window_structure() -> dict[str, Any]:
    trading = pd.read_csv(ROOT / "data" / "qfq_all" / "000001.SZ.qfq.csv", parse_dates=["date"])
    index = pd.DatetimeIndex(trading["date"])
    wf = parse_walk_forward(load_spec(SPEC_PATH).walk_forward)
    # 必须切到声明跨度再切窗：否则核对的是"全量数据能切多少折"，
    # 与主跑（按 span 限制）不是同一个东西，数字对不上就是自欺。
    rows_before_span = len(index)
    if wf.span_start:
        index = index[index >= pd.Timestamp(wf.span_start)]
    if wf.span_end:
        index = index[index <= pd.Timestamp(wf.span_end)]
    folds, meta = build_folds(index, wf)
    positions = {day: i for i, day in enumerate(index)}

    adjacency = [
        positions[fold.test_start] == positions[fold.train_end] + 1 for fold in folds
    ]
    covered: set[pd.Timestamp] = set()
    duplicated: list[int] = []
    for fold in folds:
        days = set(index[(index >= fold.test_start) & (index <= fold.test_end)])
        if covered & days:
            duplicated.append(fold.index)
        covered |= days

    report = overlap_report(folds)
    recorded = json.loads((RESULT_DIR / "l3_result.json").read_text(encoding="utf-8"))["result"]
    recorded_folds = [fold["fold"] for fold in recorded["folds"]]
    return {
        "folds": len(folds),
        "folds_in_main_run": len(recorded_folds),
        "folds_match_main_run": len(folds) == len(recorded_folds),
        "rows_before_span_slice": rows_before_span,
        "span_applied": {"start": wf.span_start or str(index[0].date()), "end": wf.span_end or str(index[-1].date())},
        "train_days": wf.train_days,
        "test_days": wf.test_days,
        "step_days": wf.step_days,
        "adjacent_folds": sum(adjacency),
        "adjacency_ok": all(adjacency),
        "duplicated_test_days": duplicated,
        "test_day_overlap_total": len(duplicated),
        "independent": report["independent"],
        "unused_tail_days": meta["unused_tail_days"],
        "first_train_start": str(folds[0].train_start.date()),
        "last_test_end": str(folds[-1].test_end.date()),
        "passed": bool(
            all(adjacency)
            and not duplicated
            and report["independent"]
            and len(folds) == len(recorded_folds)
        ),
    }


# --------------------------------------------------------------------------- #
# 标准 2：选参与打分分离
# --------------------------------------------------------------------------- #
def standard_2_selection_separation() -> dict[str, Any]:
    """用回测调用记录证明：每折「训练窗跑满网格 + 测试窗恰好一次」。"""
    calls: list[tuple[str, str, str]] = []
    original = sweep_module.run_backtest

    def spy(data, strategy, config):  # noqa: ANN001
        calls.append(
            (
                str(config.start_date.date()),
                str(config.end_date.date()),
                json.dumps(strategy.params, sort_keys=True),
            )
        )
        return original(data, strategy, config)

    sweep_module.run_backtest = spy
    try:
        result = _synth_run("UUO" * 7)
    finally:
        sweep_module.run_backtest = original

    folds = [fold for fold in result.folds if fold.ok]
    per_fold: list[dict[str, Any]] = []
    for fold in folds:
        train_key = (str(fold.train_start.date()), str(fold.train_end.date()))
        test_key = (str(fold.test_start.date()), str(fold.test_end.date()))
        train_calls = [item for item in calls if (item[0], item[1]) == train_key]
        test_calls = [item for item in calls if (item[0], item[1]) == test_key]
        per_fold.append(
            {
                "fold": fold.index,
                "train_calls": len(train_calls),
                "distinct_params_in_train": len({item[2] for item in train_calls}),
                "test_calls": len(test_calls),
                "test_used_best_params": bool(
                    test_calls and json.loads(test_calls[0][2]) == fold.best_params
                ),
            }
        )

    return {
        "grid_points": SYNTH_POINTS,
        "folds_checked": len(per_fold),
        "total_backtests": len(calls),
        "expected_total": len(per_fold) * (SYNTH_POINTS + 1),
        "train_calls_all_full_grid": all(item["train_calls"] == SYNTH_POINTS for item in per_fold),
        "train_params_distinct": all(
            item["distinct_params_in_train"] == SYNTH_POINTS for item in per_fold
        ),
        "test_calls_exactly_one": all(item["test_calls"] == 1 for item in per_fold),
        "test_used_train_best": all(item["test_used_best_params"] for item in per_fold),
        "detail": per_fold,
        "passed": bool(
            per_fold
            and len(calls) == len(per_fold) * (SYNTH_POINTS + 1)
            and all(item["train_calls"] == SYNTH_POINTS for item in per_fold)
            and all(item["distinct_params_in_train"] == SYNTH_POINTS for item in per_fold)
            and all(item["test_calls"] == 1 for item in per_fold)
            and all(item["test_used_best_params"] for item in per_fold)
        ),
    }


# --------------------------------------------------------------------------- #
# 标准 3：判定链可分辨
# --------------------------------------------------------------------------- #
def standard_3_discrimination() -> dict[str, Any]:
    cases = {
        # 全程趋势：策略在每个窗口都能吃到趋势 → 应通过
        "全趋势": ("U" * 20, VERDICT_PASSED),
        # 趋势与剧烈震荡交替：训练窗多为趋势（正）、测试窗常落震荡（负）→ 系统性背离
        "趋势震荡交替": ("UUO" * 7, VERDICT_DIVERGENCE),
        # 趋势后长期转震荡：训练也亏 → 折间不一致（不是背离）
        "趋势后转震荡": ("U" * 10 + "O" * 10, VERDICT_INCONSISTENCY),
    }
    results: dict[str, Any] = {}
    for name, (pattern, expected) in cases.items():
        started = time.perf_counter()
        res = _synth_run(pattern)
        summary = res.summary
        results[name] = {
            "expected": expected,
            "actual": res.verdict,
            "matched": res.verdict == expected,
            "folds": summary.get("folds_usable"),
            "test_return_mean": summary.get("test_return_mean"),
            "train_positive_fold_ratio": summary.get("train_positive_fold_ratio"),
            "test_positive_fold_ratio": summary.get("test_positive_fold_ratio"),
            "divergent_folds": summary.get("divergent_folds"),
            "median_wfe": summary.get("median_wfe"),
            "elapsed_seconds": round(time.perf_counter() - started, 2),
        }
    return {
        "cases": results,
        "distinct_verdicts": sorted({item["actual"] for item in results.values()}),
        "passed": all(item["matched"] for item in results.values()),
    }


# --------------------------------------------------------------------------- #
# 标准 4：终态分类
# --------------------------------------------------------------------------- #
def standard_4_taxonomy() -> dict[str, Any]:
    frame = _frame(_series("U" * 20))
    spec = parse_spec(_synth_spec(frame))

    strict = WalkForwardSpec(train_days=40, test_days=20, step_days=20, min_folds=99)
    few_folds = run_l3(spec, data=frame, walk_forward=strict, factory=_factory)

    overlapping = WalkForwardSpec(train_days=40, test_days=20, step_days=10, min_folds=3)
    overlap = run_l3(spec, data=frame, walk_forward=overlapping, factory=_factory)

    narrow = WalkForwardSpec(
        train_days=40,
        test_days=20,
        step_days=20,
        min_folds=3,
        span_start=str(frame.index[-100].date()),
        span_end=str(frame.index[-1].date()),
    )
    too_little_data = run_l3(spec, data=frame, walk_forward=narrow, factory=_factory)

    no_room = WalkForwardSpec(train_days=len(frame) - 20, test_days=60, min_test_days=10)
    no_window = run_l3(spec, data=frame, walk_forward=no_room, factory=_factory)

    payload = _synth_spec(frame)
    payload["param_grid"] = {}
    bad_spec = run_l3(payload, data=frame, walk_forward=SYNTH_WF, factory=_factory)

    taxonomy = {
        "折数不足→不可判定": few_folds.verdict,
        "测试窗重叠→不可判定": overlap.verdict,
        "跨度数据不足→数据问题": too_little_data.verdict,
        "窗口切不出折→窗口问题": no_window.verdict,
        "契约非法→契约问题": bad_spec.verdict,
    }
    expected = {
        "折数不足→不可判定": VERDICT_INCONCLUSIVE,
        "测试窗重叠→不可判定": VERDICT_INCONCLUSIVE,
        "跨度数据不足→数据问题": VERDICT_DATA,
        "窗口切不出折→窗口问题": VERDICT_WINDOW,
        "契约非法→契约问题": "L3_REJECTED_SPEC",
    }
    return {
        "actual": taxonomy,
        "expected": expected,
        "mismatch": {key: (taxonomy[key], expected[key]) for key in taxonomy if taxonomy[key] != expected[key]},
        "note": "折数不足与重叠都不可判定，但两者不能都算通过；数据不足与窗口不足分开报，避免把数据问题读成策略问题。",
        "passed": all(taxonomy[key] == expected[key] for key in taxonomy),
    }


# --------------------------------------------------------------------------- #
# 标准 5：不构造连续净值
# --------------------------------------------------------------------------- #
def standard_5_no_compounded_equity() -> dict[str, Any]:
    payload = json.loads((RESULT_DIR / "l3_result.json").read_text(encoding="utf-8"))
    result = payload["result"]
    blob = json.dumps(result, ensure_ascii=False)
    forbidden = ("equity_curve", "continuous_equity", "compounded_return", "nav")
    present = [key for key in forbidden if key in blob]

    returns = [fold["test_return"] for fold in result["folds"] if fold["test_return"] is not None]
    compounded = float(np.prod([1.0 + value for value in returns]) - 1.0) if returns else None
    arithmetic = float(sum(returns)) if returns else None
    return {
        "forbidden_fields_present": present,
        "has_naive_sum_field": "naive_sum_of_fold_returns" in result["summary"],
        "limitation_mentions_ban": any("不可拼成连续净值" in item for item in result["limitations"]),
        "limitation_mentions_scope": any("只针对参数选择" in item for item in result["limitations"]),
        # 反例：说明"复利相乘"会得到什么，以及它为什么不能当收益——
        # 每折资金重置、起始空仓，折间没有资金连续性，乘积不对应任何真实账户。
        "counterexample_only": {
            "arithmetic_sum": arithmetic,
            "compounded_product": compounded,
            "warning": "仅用于说明该口径为什么被禁，不得引用为收益。",
        },
        "passed": bool(
            not present
            and "naive_sum_of_fold_returns" in result["summary"]
            and any("不可拼成连续净值" in item for item in result["limitations"])
            and any("只针对参数选择" in item for item in result["limitations"])
        ),
    }


# --------------------------------------------------------------------------- #
# 标准 6：前置可追溯
# --------------------------------------------------------------------------- #
def _prior_sources() -> dict[str, tuple[Path, tuple[str, ...]]]:
    return {
        "L1a": (ROOT / "reports" / "gates" / "H-L1A-MACROSS-000001" / "result.json", ("verdict",)),
        "L1b": (ROOT / "reports" / "gates" / "H-L1B-MACROSS-000001" / "l1b_result.json", ("result", "verdict")),
        "L2": (ROOT / "reports" / "gates" / "H-L2-MACROSS-000001" / "l2_result.json", ("result", "verdict")),
    }


def _dig(payload: Any, path: tuple[str, ...]) -> Any:
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def collect_prior() -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for name, (path, keys) in _prior_sources().items():
        exists = path.exists()
        value = None
        if exists:
            try:
                value = _dig(json.loads(path.read_text(encoding="utf-8")), keys)
            except json.JSONDecodeError:
                value = None
        entries[name] = {
            "source": str(path.relative_to(ROOT)),
            "exists": exists,
            "path_in_file": list(keys),
            "value": value or "UNKNOWN",
        }
    recorded = json.loads((RESULT_DIR / "l3_result.json").read_text(encoding="utf-8")).get("prior_verdicts")
    return {
        "sources": entries,
        "recorded_in_result": recorded,
        "matches_recorded": entries.keys() == (recorded or {}).keys()
        and all(entries[name]["value"] == (recorded or {}).get(name) for name in entries),
        "unknown_count": sum(1 for item in entries.values() if item["value"] == "UNKNOWN"),
        "note": "缺失或解析失败一律记 UNKNOWN，绝不默认通过；UNKNOWN 会让终态降为 BLOCKED_PREREQUISITE。",
        "passed": bool(
            recorded
            and entries.keys() == recorded.keys()
            and all(entries[name]["value"] == recorded.get(name) for name in entries)
        ),
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3 验收取证")
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args(argv)

    payload = json.loads((RESULT_DIR / "l3_result.json").read_text(encoding="utf-8"))
    result = payload["result"]
    wf = parse_walk_forward(load_spec(SPEC_PATH).walk_forward)

    evidence: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "spec": str(SPEC_PATH.relative_to(ROOT)),
        "walk_forward_digest": wf.digest,
        "main_run": {
            "verdict": result["verdict"],
            "preliminary_verdict": result.get("preliminary_verdict"),
            "summary": result["summary"],
            "folds": result["summary"].get("folds_usable"),
        },
    }

    print("标准 1：窗口结构…", flush=True)
    evidence["standard_1_window_structure"] = standard_1_window_structure()
    print("标准 2：选参与打分分离…", flush=True)
    evidence["standard_2_selection_separation"] = standard_2_selection_separation()
    print("标准 3：判定链可分辨…", flush=True)
    evidence["standard_3_verdict_discrimination"] = standard_3_discrimination()
    print("标准 4：终态分类…", flush=True)
    evidence["standard_4_verdict_taxonomy"] = standard_4_taxonomy()
    print("标准 5：不构造连续净值…", flush=True)
    evidence["standard_5_no_compounded_equity"] = standard_5_no_compounded_equity()
    print("标准 6：前置可追溯…", flush=True)
    evidence["standard_6_prerequisite_traceability"] = collect_prior()

    standards = {key: value["passed"] for key, value in evidence.items() if isinstance(value, dict) and "passed" in value}
    evidence["acceptance"] = {
        "standards": standards,
        "all_passed": all(standards.values()),
        "passed_count": sum(1 for value in standards.values() if value),
        "total": len(standards),
    }

    gaps: list[str] = []
    summary = result["summary"]
    if summary.get("test_positive_fold_ratio", 1) < wf.min_positive_fold_ratio:
        gaps.append(
            f"正收益折占比 {summary['test_positive_fold_ratio']:.1%} 低于阈值 "
            f"{wf.min_positive_fold_ratio:.0%}：折间一致性不达标"
            "（不属于「样本内外背离」，是没有一致性）"
        )
    gaps.append("折不是真正的前瞻样本外：整段历史在 L1a/L2 阶段已被看过，L3 测的是参数稳健性")
    gaps.append("策略在同一标的上只有一条价格路径，折间并不独立（同一只股票的波动体制有延续性）")
    gaps.append("L1b/L3 未在事件驱动内核上重跑：L3 走的是日线引擎，成交假设与 L1a 一致")
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
