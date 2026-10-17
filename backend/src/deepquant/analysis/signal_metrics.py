"""信号层评价指标（Step 8.3'）—— 纯研究层，**不参与参数选择**。

红线合规声明（务必阅读）：

- 本模块**使用未来收益**（`forward_returns`）来评估信号的预测能力。
  这对「评价」是必需的，但**绝不得用于参数选择** ——
  任何把本模块输出用于「挑出最优参数 / 最优策略」的用法都属于数据泄漏，
  直接违反红线 R2（不用 OOS 数据选参）与 R11（不对候选参数按 OOS 排序后择优）。
- 本模块只读，不修改输入，不依赖全局状态（纯函数）。
- 「命中率」高**不等于**策略盈利：本模块不计算费用、滑点、涨跌停、
  T+1 与整手约束，也不模拟撮合。命中率只能说明方向性预测能力。

诚实性约定：
- 末尾 `horizon` 个交易日的信号**无法评估**（没有足够的未来数据），
  一律从统计中剔除并在 `excluded_tail` 中报告数量，不静默丢弃、不用 0 填补。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from deepquant.strategies.base import Signal


def forward_returns(close: pd.Series, horizon: int) -> pd.Series:
    """未来 `horizon` 个交易日的简单收益率（用于评估，禁止用于选参）。

    末尾 horizon 个交易日为 NaN（无未来数据），不填补。
    """
    if horizon < 1:
        raise ValueError(f"horizon 必须 >= 1，收到 {horizon}")
    return close.shift(-horizon) / close - 1.0


def signal_density(signals: pd.Series) -> float:
    """可成交信号（非 HOLD）占总交易日的比例。"""
    if len(signals) == 0:
        return 0.0
    actionable = int((signals.astype(int) != int(Signal.HOLD)).sum())
    return actionable / len(signals)


def signal_gaps(signals: pd.Series) -> list[int]:
    """相邻可成交信号之间的交易日间隔（不含信号日自身）。"""
    positions = [i for i, v in enumerate(signals.astype(int).tolist()) if v != int(Signal.HOLD)]
    if len(positions) < 2:
        return []
    return [b - a - 1 for a, b in zip(positions, positions[1:])]


@dataclass(frozen=True)
class SignalEvalReport:
    """信号层评估报告（不含任何撮合 / 费用假设）。"""

    source: str
    horizon: int
    n_days: int
    n_signals: int
    n_buy: int
    n_sell: int
    density: float
    n_evaluated: int
    excluded_tail: int
    buy_hit_rate: float | None
    sell_hit_rate: float | None
    overall_hit_rate: float | None
    mean_return_after_buy: float | None
    mean_return_after_sell: float | None
    mean_gap_days: float | None
    max_gap_days: int | None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "horizon": self.horizon,
            "n_days": self.n_days,
            "n_signals": self.n_signals,
            "n_buy": self.n_buy,
            "n_sell": self.n_sell,
            "density": self.density,
            "n_evaluated": self.n_evaluated,
            "excluded_tail": self.excluded_tail,
            "buy_hit_rate": self.buy_hit_rate,
            "sell_hit_rate": self.sell_hit_rate,
            "overall_hit_rate": self.overall_hit_rate,
            "mean_return_after_buy": self.mean_return_after_buy,
            "mean_return_after_sell": self.mean_return_after_sell,
            "mean_gap_days": self.mean_gap_days,
            "max_gap_days": self.max_gap_days,
            "notes": list(self.notes),
        }


def evaluate_signals(
    signals: pd.Series,
    close: pd.Series,
    *,
    source: str,
    horizon: int = 5,
) -> SignalEvalReport:
    """评估信号序列的方向性预测能力。

    Args:
        signals: 与 close 同索引的信号序列，取值 ∈ {-1, 0, 1}。
        close: 收盘价序列。
        source: 策略标识。
        horizon: 评估前瞻期（交易日）。
    """
    if horizon < 1:
        raise ValueError(f"horizon 必须 >= 1，收到 {horizon}")
    if len(signals) == 0:
        raise ValueError("signals 为空，无法评估")
    if not signals.index.equals(close.index):
        raise ValueError("signals 与 close 索引必须一致（禁止依赖 pandas 自动对齐）")

    sig = signals.astype(int)
    fwd = forward_returns(close, horizon)

    n_days = len(sig)
    buy_mask = sig == int(Signal.BUY)
    sell_mask = sig == int(Signal.SELL)
    n_buy = int(buy_mask.sum())
    n_sell = int(sell_mask.sum())

    # 可评估 = 信号非 HOLD 且未来收益不为 NaN
    evaluable = (sig != int(Signal.HOLD)) & fwd.notna()
    n_evaluated = int(evaluable.sum())

    # 末尾因 horizon 不足而被剔除的信号数（不静默丢弃）
    tail_mask = (sig != int(Signal.HOLD)) & fwd.isna()
    excluded_tail = int(tail_mask.sum())

    def _hit_rate(mask: pd.Series, expected_positive: bool) -> float | None:
        sub = mask & evaluable
        n = int(sub.sum())
        if n == 0:
            return None
        ret = fwd[sub]
        hits = int((ret > 0).sum()) if expected_positive else int((ret < 0).sum())
        return hits / n

    buy_hit = _hit_rate(buy_mask, expected_positive=True)
    sell_hit = _hit_rate(sell_mask, expected_positive=False)

    total_hits = 0
    if buy_hit is not None:
        total_hits += round(buy_hit * int((buy_mask & evaluable).sum()))
    if sell_hit is not None:
        total_hits += round(sell_hit * int((sell_mask & evaluable).sum()))
    overall = (total_hits / n_evaluated) if n_evaluated > 0 else None

    def _mean(mask: pd.Series) -> float | None:
        sub = mask & evaluable
        if int(sub.sum()) == 0:
            return None
        return float(fwd[sub].mean())

    gaps = signal_gaps(sig)

    notes = [
        f"本评估使用未来 {horizon} 个交易日收益，仅用于评价信号预测能力，"
        "不得用于参数选择（红线 R2 / R11）。",
        "命中率不含费用、滑点、涨跌停、T+1 与整手约束，也不模拟撮合；"
        "命中率高不等于策略盈利。",
        f"末尾 {excluded_tail} 个信号因前瞻期不足未能评估，已从统计中剔除（未用 0 填补）。",
    ]

    return SignalEvalReport(
        source=source,
        horizon=horizon,
        n_days=n_days,
        n_signals=n_buy + n_sell,
        n_buy=n_buy,
        n_sell=n_sell,
        density=signal_density(sig),
        n_evaluated=n_evaluated,
        excluded_tail=excluded_tail,
        buy_hit_rate=buy_hit,
        sell_hit_rate=sell_hit,
        overall_hit_rate=overall,
        mean_return_after_buy=_mean(buy_mask),
        mean_return_after_sell=_mean(sell_mask),
        mean_gap_days=(sum(gaps) / len(gaps)) if gaps else None,
        max_gap_days=max(gaps) if gaps else None,
        notes=notes,
    )
