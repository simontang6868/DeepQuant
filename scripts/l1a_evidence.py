"""L1a 闸门验收取证：每条否决路径都要有一次真实触发，不允许只写"实现了"。

七类用例
--------
1. ``real_hypothesis``：真实数据 + 真实策略（ma_cross / 000001.SZ），给出完整判定与数字；
2. ``data_reject``：OHLC 结构被破坏 → 数据否决；
3. ``leak_static``：``rolling(center=True)`` → 静态检测命中；
4. ``leak_dynamic``：无静态特征但用全样本统计量 → 动态截断检验命中；
5. ``coverage_reject``：网格有点跑失败 → 覆盖率不足否决；
6. ``inconclusive``：样本外区间没有数据 → 指标不可计算 → 无结论（不是通过）；
7. ``pass_branch``：合成上升趋势 + 宽松边界 → 走通"通过"分支（**机制自检，不是策略结论**）。

另附契约摘要稳定性检查：同一契约两次摘要一致，改边界必变。
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend" / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from deepquant.gates import (  # noqa: E402
    INCONCLUSIVE,
    PASS,
    REJECTED_BOUNDS,
    REJECTED_COVERAGE,
    REJECTED_DATA,
    REJECTED_LEAKAGE,
    REJECTED_SPEC,
    load_spec,
    run_l1a,
)
from deepquant.gates.spec import parse_spec  # noqa: E402
from deepquant.strategies.base import Signal, Strategy  # noqa: E402

OUT = ROOT / "reports" / "L1A_GATE_EVIDENCE.json"
SPEC = ROOT / "docs" / "gates" / "specs" / "H_L1A_MACROSS_000001.json"


# --------------------------------------------------------------------------- #
# 测试用策略
# --------------------------------------------------------------------------- #
class CenterRollingStrategy(Strategy):
    """故意用居中窗口：窗口里含未来值。静态扫描必须抓到。"""

    name = "leaky_center"

    @property
    def params(self) -> dict:
        return {"window": 20}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        ma = data["close"].rolling(20, center=True).mean()
        signals = pd.Series(Signal.HOLD, index=data.index, dtype=int)
        signals[data["close"] > ma] = Signal.BUY
        signals[data["close"] < ma] = Signal.SELL
        return signals


class FullSampleZScoreStrategy(Strategy):
    """没有任何静态特征，但用**全样本**均值/标准差——只有动态截断能抓到。"""

    name = "leaky_fullsample"

    @property
    def params(self) -> dict:
        return {"threshold": 1.0}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        mean = data["close"].mean()
        std = data["close"].std()
        z = (data["close"] - mean) / (std if std else 1.0)
        signals = pd.Series(Signal.HOLD, index=data.index, dtype=int)
        signals[z > 1.0] = Signal.BUY
        signals[z < -1.0] = Signal.SELL
        return signals


# --------------------------------------------------------------------------- #
# 数据构造
# --------------------------------------------------------------------------- #
def _frame(prices: np.ndarray, start: str = "2020-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(prices))
    frame = pd.DataFrame(
        {
            "date": dates,
            "close": prices,
        }
    )
    frame["open"] = frame["close"].shift(1).fillna(frame["close"])
    frame["high"] = frame[["open", "close"]].max(axis=1) * 1.002
    frame["low"] = frame[["open", "close"]].min(axis=1) * 0.998
    frame["volume"] = 50_000
    return frame[["date", "open", "high", "low", "close", "volume"]]


def _trend_frame(rows: int = 640, *, drift: float = 0.0012, wave: float = 0.06) -> pd.DataFrame:
    """确定性上升趋势 + 正弦波动：均线会交叉，且两个窗口都上涨。"""
    index = np.arange(rows)
    prices = 10.0 * np.exp(drift * index) * (1 + wave * np.sin(index / 18.0))
    return _frame(prices)


def _spec(payload: dict):
    return parse_spec(payload)


BASE_SPEC = {
    "hypothesis_id": "H-L1A-UNIT",
    "title": "单元用例",
    "strategy_id": "ma_cross",
    "param_grid": {"fast_window": [5, 10], "slow_window": [20, 40]},
    "constraints": [["fast_window", "slow_window"]],
    "data": {"symbol": "UNIT", "research_path": "n/a"},
    "in_sample": {"start": "2020-01-01", "end": "2021-12-31"},
    "out_sample": {"start": "2022-01-01", "end": "2022-12-31"},
    "boundaries": {
        "min_in_sample_return": 0.10,
        "max_in_sample_drawdown": 0.30,
        "min_closed_trades": 2,
        "min_out_sample_return": 0.0,
        "max_out_sample_drawdown": 0.30,
        "max_oos_degradation": 0.5,
    },
    "declared_grid_points": 4,
}


def _case(payload: dict, *, data=None, factory=None) -> dict:
    # 直接把原始字典交给闸门：契约不合法时要看到 REJECTED_SPEC，而不是在这里先抛异常
    result = run_l1a(payload, data=data, factory=factory) if factory else run_l1a(payload, data=data)
    return {
        "verdict": result.verdict,
        "stages": [stage.to_dict() for stage in result.stages],
        "bound_checks": [check.to_dict() for check in result.bound_checks],
        "honesty": result.honesty,
        "notes": result.notes,
        "leakage": result.leakage,
        "data": {
            "blocking": result.data.get("blocking_failures"),
            "undetermined": result.data.get("undetermined"),
        },
        "sweep": {
            "declared": result.sweep.get("declared_points"),
            "executed": result.sweep.get("executed_points"),
            "failed": result.sweep.get("failed_points"),
            "coverage": result.sweep.get("coverage"),
            "oos_evaluations": result.sweep.get("oos_evaluations"),
            "best": (result.sweep.get("best") or {}).get("params"),
            "plateau": result.sweep.get("plateau_ratio"),
        },
    }


def main() -> int:
    started = time.perf_counter()
    cases: dict[str, dict] = {}

    # ---- 1. 真实假设 ----
    real_spec = load_spec(SPEC)
    real = run_l1a(real_spec)
    cases["real_hypothesis"] = {
        "verdict": real.verdict,
        "spec_digest": real.spec.get("spec_digest"),
        "stages": [stage.to_dict() for stage in real.stages],
        "metrics": {name: metric.to_dict() for name, metric in real.metrics.items()},
        "bound_checks": [check.to_dict() for check in real.bound_checks],
        "sweep": {
            "declared": real.sweep.get("declared_points"),
            "executed": real.sweep.get("executed_points"),
            "coverage": real.sweep.get("coverage"),
            "best": (real.sweep.get("best") or {}).get("params"),
            "plateau": real.sweep.get("plateau_ratio"),
            "oos_evaluations": real.sweep.get("oos_evaluations"),
        },
        "data_checks": [
            {"code": check["code"], "passed": check["passed"], "detail": check["detail"]}
            for check in real.data.get("checks", [])
        ],
        "leakage": real.leakage,
        "notes": real.notes,
    }

    # ---- 2. 数据否决 ----
    broken = _trend_frame()
    broken.loc[5, "high"] = broken.loc[5, "low"] * 0.5  # high 低于 low
    cases["data_reject"] = _case(BASE_SPEC, data=broken)

    # ---- 3. 静态泄漏 ----
    cases["leak_static"] = _case(
        {**BASE_SPEC, "strategy_id": "leaky_center"},
        data=_trend_frame(),
        factory=lambda sid, params: CenterRollingStrategy(),
    )

    # ---- 4. 动态泄漏 ----
    cases["leak_dynamic"] = _case(
        {**BASE_SPEC, "strategy_id": "leaky_fullsample"},
        data=_trend_frame(),
        factory=lambda sid, params: FullSampleZScoreStrategy(),
    )

    # ---- 5. 覆盖不足 ----
    def flaky_factory(strategy_id: str, params: dict):
        if params.get("fast_window") == 10 and params.get("slow_window") == 40:
            raise ValueError("故意让一个参数点在构造阶段失败，用于验证覆盖率分母")
        from deepquant.api.registry import build_strategy

        return build_strategy(strategy_id, dict(params))

    cases["coverage_reject"] = _case(BASE_SPEC, data=_trend_frame(), factory=flaky_factory)

    # ---- 6. 无结论（样本外无数据）----
    cases["inconclusive"] = _case(
        {
            **BASE_SPEC,
            "out_sample": {"start": "2035-01-01", "end": "2035-12-31"},
            # 样本内门槛全部放宽以确保"没有任何明确失败项"，这样结论只能由
            # 样本外不可计算决定 → INCONCLUSIVE（不通过也不否决）
            "boundaries": {
                "min_in_sample_return": -1.0,
                "max_in_sample_drawdown": 0.99,
                "min_closed_trades": 1,
                "min_out_sample_return": 0.0,
                "max_out_sample_drawdown": 0.99,
                "max_oos_degradation": 0.9,
            },
        },
        data=_trend_frame(),
    )

    # ---- 7. 通过分支（机制自检）----
    cases["pass_branch"] = _case(
        {
            **BASE_SPEC,
            "boundaries": {
                "min_in_sample_return": 0.05,
                "max_in_sample_drawdown": 0.60,
                "min_closed_trades": 1,
                "min_out_sample_return": 0.0,
                "max_out_sample_drawdown": 0.60,
                "max_oos_degradation": 0.9,
            },
        },
        data=_trend_frame(),
    )

    # ---- 8. 契约否决 ----
    broken_spec = dict(BASE_SPEC)
    broken_spec.pop("boundaries")
    cases["spec_reject"] = _case(broken_spec, data=_trend_frame())

    # ---- 9. 摘要稳定性 ----
    digest_a = _spec(BASE_SPEC).digest()
    digest_b = _spec(BASE_SPEC).digest()
    changed = {**BASE_SPEC, "boundaries": {**BASE_SPEC["boundaries"], "max_in_sample_drawdown": 0.29}}
    digest_c = _spec(changed).digest()
    digest_case = {
        "same_spec_twice_equal": digest_a == digest_b,
        "digest_after_boundary_change_differs": digest_a != digest_c,
        "digest": digest_a,
    }

    expectations = {
        "real_hypothesis": REJECTED_BOUNDS,
        "data_reject": REJECTED_DATA,
        "leak_static": REJECTED_LEAKAGE,
        "leak_dynamic": REJECTED_LEAKAGE,
        "coverage_reject": REJECTED_COVERAGE,
        "inconclusive": INCONCLUSIVE,
        "pass_branch": PASS,
        "spec_reject": REJECTED_SPEC,
    }
    matched = {
        name: cases[name]["verdict"] == expected for name, expected in expectations.items()
    }

    # 三条附加判据
    static_hits = [item["code"] for item in cases["leak_static"]["leakage"].get("static") or []]
    dynamic_hits = [item["code"] for item in cases["leak_dynamic"]["leakage"].get("dynamic") or []]
    honesty_ok = all(
        item["status"] == "not_computable" and item["value"] is None
        for item in cases["inconclusive"]["honesty"]
    )
    extra = {
        "static_detects_center_rolling": "center_rolling" in static_hits,
        "dynamic_detects_fullsample_only": bool(dynamic_hits)
        and not cases["leak_dynamic"]["leakage"].get("static"),
        "inconclusive_metrics_all_null": honesty_ok and bool(cases["inconclusive"]["honesty"]),
        "oos_evaluated_once": (real.sweep.get("oos_evaluations") == 1),
        "coverage_denominator_uses_declared_points": (
            cases["coverage_reject"]["sweep"]["declared"] == 4
            and cases["coverage_reject"]["sweep"]["executed"] == 3
        ),
        "digest_stable": digest_case["same_spec_twice_equal"]
        and digest_case["digest_after_boundary_change_differs"],
    }

    passed = all(matched.values()) and all(extra.values())
    payload = {
        "task": "阶段三 L1a 向量化粗筛闸门",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_s": round(time.perf_counter() - started, 2),
        "cases": cases,
        "expectations": expectations,
        "verdict_matched": matched,
        "extra_checks": extra,
        "digest_case": digest_case,
        "verdict": "L1A_GATE_READY" if passed else "L1A_GATE_NOT_READY",
        "notes": [
            "real_hypothesis 用的是真实行情（000001.SZ，qfq 研究层 + exec 交易层跨源抽检），"
            "结论只对该假设/该区间成立，不能外推为「ma_cross 全局无效」。",
            "pass_branch 用合成上升趋势 + 买入持有策略，只为验证「通过」分支可达，"
            "不构成任何策略结论。",
            "边界值来自契约文件（跑前写定）；本文件所有判定均可用 scripts/l1a_gate.py 复现。",
        ],
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"{payload['verdict']}  ({payload['elapsed_s']}s)")
    for name, ok in matched.items():
        print(f"  {'✓' if ok else '✗'} {name:22s} → {cases[name]['verdict']}")
    for name, ok in extra.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"证据：{OUT}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
