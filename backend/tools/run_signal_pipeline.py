"""端到端信号链路冒烟脚本（纯加法 / 不依赖真实行情）。

把整条研究链路串起来跑一遍，验证「生成信号 → 组合 → 评价 → 持久化 → 读回校验」
五个环节在当前代码库下能端到端打通：

    合成 pd.Series 信号（多个源）
      → events_from_signals 转结构化 SignalEvent
      → ensemble.combine_events 合成一条信号 + 事件列表
      → ensemble_eval.evaluate_ensemble 评价（仅评价，不选择）
      → signal_store.save_signal_events 落库（临时库）
      → signal_store.list_signal_events 读回
      → 校验无损往返

诚实性 / 红线约定：
- 本脚本使用的行情与信号 **全部是确定性合成数据**，仅用于冒烟，
  不代表任何真实市场行情（红线 R3：不把合成行情标为真实）。
  返回值里带 ``synthetic: True`` 明确标注。
- ``evaluate_ensemble`` 使用未来收益做评价，但本脚本 **不据此挑选任何参数**
  （不调 `weights` / `method` / `min_sources` 的择优），守 R2 / R11。
- 本脚本不改动任何既有模块，也不触碰回测引擎 `backtest/engine.py`。

用法：
    python tools/run_signal_pipeline.py                 # 用临时库，跑完即清
    python tools/run_signal_pipeline.py --db smoke.db   # 落指定库
    python tools/run_signal_pipeline.py --method weighted --n-days 120
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
from pathlib import Path

import pandas as pd

from deepquant.analysis.ensemble_eval import evaluate_ensemble
from deepquant.persistence.signal_store import (
    list_signal_events,
    save_signal_events,
)
from deepquant.strategies.ensemble import combine_events
from deepquant.strategies.signals import SignalEvent, events_from_signals


def _synthetic_calendar(n_days: int) -> pd.DatetimeIndex:
    """确定性交易日历（工作日，长度由入参给定，不写死）。"""
    return pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=n_days))


def _synthetic_signals(
    dates: pd.DatetimeIndex, *, seed: int
) -> dict[str, pd.Series]:
    """生成确定性的合成策略信号（取值 ∈ {-1, 0, 1}）。

    这是 **合成数据**，用于冒烟，不代表真实行情（R3）。
    """
    rng = random.Random(seed)
    n = len(dates)

    def _one_source() -> pd.Series:
        values: list[int] = []
        for _ in range(n):
            roll = rng.random()
            if roll < 0.18:
                values.append(1)  # BUY
            elif roll < 0.36:
                values.append(-1)  # SELL
            else:
                values.append(0)  # HOLD
        return pd.Series(values, index=dates, dtype="int8")

    return {
        "ma_cross": _one_source(),
        "mean_reversion": _one_source(),
        "momentum": _one_source(),
    }


def _synthetic_close(dates: pd.DatetimeIndex, *, seed: int) -> pd.Series:
    """生成确定性的合成收盘价（随机游走，仅用于冒烟，非真实行情，R3）。"""
    rng = random.Random(seed + 909)
    price = 100.0
    values: list[float] = []
    for _ in range(len(dates)):
        price *= 1.0 + rng.uniform(-0.02, 0.02)
        values.append(price)
    return pd.Series(values, index=dates, dtype="float64")


def _canonical_key(event: SignalEvent) -> tuple:
    """把 SignalEvent 映射为可比较的规范化元组，用于无损往返校验。"""
    return (
        event.date.isoformat(),
        int(event.direction),
        float(event.strength),
        event.source,
        None if event.valid_until is None else event.valid_until.isoformat(),
        json.dumps(event.metadata, sort_keys=True, ensure_ascii=False),
    )


def _verify_roundtrip(
    original: list[SignalEvent], readback: list[SignalEvent]
) -> bool:
    """校验读回的事件与原始事件在内容上一一对应（忽略 id / 存储顺序）。"""
    if len(original) != len(readback):
        return False
    a = sorted(original, key=_canonical_key)
    b = sorted(readback, key=_canonical_key)
    return all(
        _canonical_key(o) == _canonical_key(r) for o, r in zip(a, b)
    )


def run_signal_pipeline(
    *,
    n_days: int = 60,
    seed: int = 42,
    method: str = "majority",
    db_path=None,
) -> dict:
    """跑一遍完整信号链路，返回冒烟摘要。

    Args:
        n_days: 合成交易日历长度。
        seed: 随机种子，保证冒烟可复现。
        method: 组合方法（透传给 combine_events / evaluate_ensemble）。
        db_path: 持久化目标库路径；None 时使用临时库（函数返回后删除）。

    Returns:
        冒烟摘要字典，含 ``synthetic`` 标志、各环节计数与 ``readback_ok``。
    """
    if n_days < 10:
        raise ValueError(f"n_days 至少 10，收到 {n_days}")

    dates = _synthetic_calendar(n_days)
    signals = _synthetic_signals(dates, seed=seed)
    close = _synthetic_close(dates, seed=seed)

    # 1) 合成 SignalEvent 列表
    inputs = {
        name: events_from_signals(series, source=name)
        for name, series in signals.items()
    }

    # 2) ensemble.combine —— 合成一条信号 + 事件列表
    result = combine_events(inputs, dates, method=method)

    # 3) ensemble_eval.evaluate —— 仅评价，不选择（R2 / R11）
    eval_report = evaluate_ensemble(signals, close, method=method, horizon=5)

    # 4) signal_store 存（落临时库）
    saved_batch = save_signal_events(result.events, db_path=db_path)

    # 5) 读回校验
    readback = list_signal_events(batch_id=saved_batch, db_path=db_path)
    readback_ok = _verify_roundtrip(result.events, readback)

    return {
        "synthetic": True,
        "n_days": n_days,
        "seed": seed,
        "method": method,
        "n_sources": len(signals),
        "n_combined_events": len(result.events),
        "n_actionable_combined": int(result.n_actionable),
        "ensemble_coverage": eval_report.ensemble_coverage,
        "saved_batch_id": saved_batch,
        "n_saved": len(result.events),
        "n_readback": len(readback),
        "readback_ok": readback_ok,
        "evaluation_available": eval_report.ensemble is not None,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DeepQuant 信号链路端到端冒烟脚本（合成数据，非真实行情）"
    )
    parser.add_argument("--n-days", type=int, default=60, help="合成交易日数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（可复现）")
    parser.add_argument(
        "--method",
        default="majority",
        choices=("majority", "weighted", "unanimous"),
        help="组合方法",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="持久化库路径；缺省使用临时库（跑完即删）",
    )
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    db_path = Path(args.db) if args.db else None
    tmp = None
    if db_path is None:
        tmp = tempfile.TemporaryDirectory(prefix="deepquant_smoke_")
        db_path = Path(tmp.name) / "smoke.db"

    try:
        summary = run_signal_pipeline(
            n_days=args.n_days,
            seed=args.seed,
            method=args.method,
            db_path=db_path,
        )
    finally:
        if tmp is not None:
            tmp.cleanup()

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if summary.get("readback_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
