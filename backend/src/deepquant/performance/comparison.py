"""回测结果的元数据比较基础（不涉及数据库或前端）。

用于判断两个结果是否在「同一口径」下可比，并列出差异。
本阶段不实现完整比较界面，只提供基于元数据的判定：

- 差异维度（决定是否「公平可比」的关键配置）：
  标的、数据集摘要、市场规则、费用/滑点配置、成交价规则、信号滞后、回测区间；
- 策略名称/版本/参数属于**合法的比较维度**（例如比较不同参数），
  不因此判定为不可比；
- 关键配置不一致时，**不得**直接判定为可公平比较，须在 ``reasons`` 中说明。
"""

from __future__ import annotations

import pandas as pd

from deepquant.backtest.models import BacktestResult

# 决定「是否公平可比」的关键字段
CRITICAL_KEYS: tuple[str, ...] = (
    "symbol",
    "dataset_digest",
    "market_rules",
    "fees",
    "slippage",
    "exec_price_rule",
    "signal_lag",
    "period",
)


def _iso(value: pd.Timestamp | None) -> str | None:
    return None if value is None else pd.Timestamp(value).isoformat()


def result_profile(result: BacktestResult) -> dict:
    """提取用于比较的元数据画像。"""
    config = result.config
    rules = config.market_rules
    limit = rules.price_limit
    return {
        "symbol": config.symbol,
        "period": {"start": _iso(result.period_start), "end": _iso(result.period_end)},
        "dataset_digest": (result.dataset or {}).get("digest"),
        "market_rules": {
            "name": rules.name,
            "lot_size": rules.lot_size,
            "t_plus_one": bool(rules.t_plus_one),
            "price_limit": (
                None if limit is None else {"rate": limit.rate, "tick_size": limit.tick_size}
            ),
        },
        "fees": {
            "commission_rate": config.commission_rate,
            "min_commission": config.min_commission,
            "stamp_duty_rate": config.stamp_duty_rate,
            "transfer_fee_rate": config.transfer_fee_rate,
            "fee_source": config.fee_source,
        },
        "slippage": {"buy": config.slippage_buy, "sell": config.slippage_sell},
        "exec_price_rule": config.exec_price_rule.value,
        "signal_lag": config.signal_lag,
        "strategy": {
            "name": (result.strategy or {}).get("name"),
            "version": (result.strategy or {}).get("version"),
            "params": (result.strategy or {}).get("params"),
        },
    }


def compare_metadata(result_a: BacktestResult, result_b: BacktestResult) -> dict:
    """比较两个结果的元数据，返回差异与「是否公平可比」。

    ``fairly_comparable`` 仅在以上关键字段全部一致时为 True；
    策略名称/版本/参数不一致不阻断可比性（它们是比较的目标维度）。
    """
    profile_a = result_profile(result_a)
    profile_b = result_profile(result_b)

    identical: dict[str, object] = {}
    differences: dict[str, tuple] = {}
    for key in profile_a:
        value_a, value_b = profile_a[key], profile_b[key]
        if value_a == value_b:
            identical[key] = value_a
        else:
            differences[key] = (value_a, value_b)

    reasons: list[str] = []
    for key in CRITICAL_KEYS:
        if key in differences:
            reasons.append(f"关键配置不一致：{key}")

    strategy_changed = "strategy" in differences
    if strategy_changed:
        reasons.append("策略配置不同（属于合法比较维度，不阻断可比性）")

    return {
        "fairly_comparable": not any(key in differences for key in CRITICAL_KEYS),
        "identical": identical,
        "differences": differences,
        "reasons": reasons,
        "strategy_differs": strategy_changed,
    }
