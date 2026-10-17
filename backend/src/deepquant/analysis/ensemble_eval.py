"""ensemble 信号评价接入（Step 8.5'）—— **只评价，不选择**。

把 `strategies/ensemble.py` 合成出的信号，接到 `analysis/signal_metrics.py` 的
评价指标上，得到「合成信号 + 各源信号」并列的评估报告。

红线圈定（务必阅读，本模块的合法性完全依赖于此）：

- 本模块**使用未来收益**来评估信号预测能力。这对「评价」是必需的，
  但**绝不得用于参数选择**。用本报告去挑 `weights` / `method` / `min_sources`
  属于拿样本外收益选参，直接违反 **R2**（不用 OOS 数据选参）与
  **R11**（不对候选参数按 OOS 收益排序后择优）。
- **本模块刻意不提供任何择优 / 搜索 / 优化 / 拟合入口**。
  没有 `best_*`、没有 `select_*`、没有网格搜索、没有排序择优。
  各源与合成信号并列展示仅为**描述性对比**，不构成推荐。
- 组合不承诺 alpha：多个无 alpha 的源组合起来依然无 alpha。
  合成信号命中率高于单源，**不能**作为「应该组合」的证据。

诚实性约定：
- 末尾 `horizon` 个交易日的信号无法评估（无未来数据），一律从统计中剔除
  并在 `excluded_tail` 报告数量，不静默丢弃、不用 0 填补。
- 命中率不含费用、滑点、涨跌停、T+1 与整手约束，也不模拟撮合。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

from deepquant.analysis.signal_metrics import SignalEvalReport, evaluate_signals
from deepquant.strategies.ensemble import MAJORITY, combine_series

__all__ = ["EnsembleEvalReport", "evaluate_ensemble"]


@dataclass(frozen=True)
class EnsembleEvalReport:
    """ensemble 并列评估报告（不含任何撮合 / 费用假设）。

    Attributes:
        ensemble: 合成信号自身的评估报告。
        per_source: 各输入源信号的评估报告（键 = 源名称）。
            `evaluate_sources=False` 时为空字典。
        method: 使用的组合方法。
        horizon: 评估前瞻期（交易日）。
        ensemble_coverage: 合成信号中可操作（非 HOLD）天数的占比。
        notes: 红线与诚实性说明。
    """

    ensemble: SignalEvalReport
    per_source: dict[str, SignalEvalReport] = field(default_factory=dict)
    method: str = MAJORITY
    horizon: int = 5
    ensemble_coverage: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def n_sources(self) -> int:
        """参与评价的源数量。"""
        return len(self.per_source)

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "horizon": self.horizon,
            "ensemble_coverage": self.ensemble_coverage,
            "ensemble": self.ensemble.to_dict(),
            "per_source": {name: rep.to_dict() for name, rep in self.per_source.items()},
            "notes": list(self.notes),
        }


def evaluate_ensemble(
    signals: Mapping[str, pd.Series],
    close: pd.Series,
    *,
    method: str = MAJORITY,
    weights: Mapping[str, float] | None = None,
    min_sources: int = 1,
    horizon: int = 5,
    evaluate_sources: bool = True,
) -> EnsembleEvalReport:
    """组合多个源信号，并评价「合成信号 + 各源信号」。

    这是 `combine_series` 与 `evaluate_signals` 的**唯一合法接线点**：
    组合层不评价、评价层不组合，本模块只做一次并列评价。

    Args:
        signals: `{源名称: 信号序列}`，取值 ∈ {-1, 0, 1}，各序列索引必须一致。
        close: 收盘价序列，索引必须与每个信号序列**完全一致**。
        method: 组合方法，透传 `combine_series`。
        weights: 权重，透传 `combine_series`。**由调用方给定，本函数不拟合。**
        min_sources: 当日最少参与源数，透传 `combine_series`。
        horizon: 评估前瞻期（交易日）。
        evaluate_sources: 是否同时评价各输入源（并列对比用）。

    Raises:
        TypeError: `close` 不是 pd.Series、`evaluate_sources` 不是 bool。
        ValueError: `close` 为空、`signals` 非法、索引与 close 不一致、
            `horizon` < 1 等。

    注意：
        本函数返回的是**评价结果**，不是选参依据。请勿据此挑选
        `weights` / `method` / `min_sources`（违反 R2 / R11）。
    """
    if not isinstance(close, pd.Series):
        raise TypeError(f"close 必须是 pd.Series，收到 {type(close)!r}")
    if len(close) == 0:
        raise ValueError("close 为空，无法评价")
    if not isinstance(evaluate_sources, bool):
        raise TypeError(
            f"evaluate_sources 必须是 bool，收到 {type(evaluate_sources)!r}"
        )
    if not isinstance(signals, Mapping) or not signals:
        raise ValueError("signals 必须是非空 Mapping[str, pd.Series]")

    # 先校验与 close 的索引一致性：拒绝隐式对齐，避免把不同日历的行情拼在一起。
    for name, series in signals.items():
        if not isinstance(series, pd.Series):
            raise TypeError(f"源 {name!r} 的值必须是 pd.Series，收到 {type(series)!r}")
        if not series.index.equals(close.index):
            raise ValueError(
                f"源 {name!r} 的索引与 close 不一致：拒绝隐式对齐，请先统一交易日历"
            )

    combined = combine_series(
        signals,
        weights=weights,
        method=method,
        min_sources=min_sources,
    )
    ensemble_report = evaluate_signals(
        combined.signals,
        close,
        source=combined.source,
        horizon=horizon,
    )

    per_source: dict[str, SignalEvalReport] = {}
    if evaluate_sources:
        for name, series in signals.items():
            per_source[name] = evaluate_signals(
                series,
                close,
                source=name,
                horizon=horizon,
            )

    notes = [
        f"本评估使用未来 {horizon} 个交易日收益，仅用于评价信号预测能力，"
        "不得用于参数选择（红线 R2 / R11）。",
        "严禁用本报告挑选 weights / method / min_sources："
        "那属于用样本外收益选参，违反 R2 / R11。",
        "各源与合成信号并列展示仅为描述性对比，不构成排序择优；"
        "本模块不提供任何择优 / 搜索 / 优化入口。",
        "组合不承诺 alpha：多个无 alpha 的源组合起来仍然无 alpha；"
        "合成信号命中率高不等于应该组合。",
        "命中率不含费用、滑点、涨跌停、T+1 与整手约束，也不模拟撮合。",
        f"合成信号末尾 {ensemble_report.excluded_tail} 个信号因前瞻期不足未能评估，"
        "已从统计中剔除（未用 0 填补）。",
    ]

    return EnsembleEvalReport(
        ensemble=ensemble_report,
        per_source=per_source,
        method=method,
        horizon=horizon,
        ensemble_coverage=combined.coverage,
        notes=notes,
    )
