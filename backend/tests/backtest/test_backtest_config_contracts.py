"""backtest/config.py 契约与不变量测试（A16#9，本地不提 PR）。

补既有 test_backtest/test_config.py 未覆盖的 3 行守卫（coverage 实测 backtest/config.py 76 stmts / 3 miss，
缺口为 BacktestConfig.__post_init__ 的三处输入校验守卫 lines 120/135/139）：
- exec_price_rule 必须是 ExecutionPrice 枚举实例，否则 ValueError（且消息点名字段）；
- fee_source 必须是非空字符串，否则 ValueError；
- assumptions 必须是「字符串组成的元组」，否则 ValueError。

全部为语义断言：校验具体被违反的不变量与抛出的稳定原因，不靠「只断言不报错」。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.backtest.config import (
    BacktestConfig,
    ExecutionPrice,
    FEE_SOURCE_SIMULATION,
)


def _valid_kwargs(**overrides):
    base = dict(
        symbol="600519.SH",
        initial_cash=100000.0,
        exec_price_rule=ExecutionPrice.NEXT_OPEN,
        fee_source=FEE_SOURCE_SIMULATION,
        assumptions=("a", "b"),
    )
    base.update(overrides)
    return base


# ---------- 1. exec_price_rule 必须是 ExecutionPrice 枚举 ----------
def test_exec_price_rule_rejects_non_enum():
    """exec_price_rule 不是 ExecutionPrice 实例 → ValueError，且消息点名字段。"""
    with pytest.raises(ValueError) as exc:
        BacktestConfig(**_valid_kwargs(exec_price_rule="next_open"))  # 字符串而非枚举
    assert "exec_price_rule" in str(exc.value)


def test_exec_price_rule_accepts_only_enum_member():
    """合法构造必须以 ExecutionPrice 枚举成员传入（目前仅 NEXT_OPEN 存在）。"""
    cfg = BacktestConfig(**_valid_kwargs(exec_price_rule=ExecutionPrice.NEXT_OPEN))
    assert cfg.exec_price_rule is ExecutionPrice.NEXT_OPEN


# ---------- 2. fee_source 必须是非空字符串 ----------
@pytest.mark.parametrize("bad_fee", ["", "   ", 123, None])
def test_fee_source_rejects_non_nonempty_str(bad_fee):
    """fee_source 为非字符串或空/空白字符串 → ValueError。"""
    with pytest.raises(ValueError) as exc:
        BacktestConfig(**_valid_kwargs(fee_source=bad_fee))
    assert "fee_source" in str(exc.value)


# ---------- 3. assumptions 必须是字符串组成的元组 ----------
@pytest.mark.parametrize(
    "bad_assumptions",
    [
        ["not", "a", "tuple"],  # 非元组
        ("ok", 123),            # 元组但含非字符串
        "just a string",        # 字符串而非元组
    ],
)
def test_assumptions_rejects_non_str_tuple(bad_assumptions):
    """assumptions 不是「字符串组成的元组」→ ValueError。"""
    with pytest.raises(ValueError) as exc:
        BacktestConfig(**_valid_kwargs(assumptions=bad_assumptions))
    assert "assumptions" in str(exc.value)


# ---------- 4. 合法输入（默认占位参数）必须成功构造 ----------
def test_valid_config_constructs_with_defaults():
    """合法输入（默认占位参数）必须成功构造，且 fee_source 标注为 simulation_placeholder。"""
    cfg = BacktestConfig(symbol="600519.SH", initial_cash=100000.0)
    assert cfg.fee_source == FEE_SOURCE_SIMULATION
    assert cfg.exec_price_rule is ExecutionPrice.NEXT_OPEN
    assert isinstance(cfg.assumptions, tuple) and all(
        isinstance(a, str) for a in cfg.assumptions
    )


# ---------- 5. require_positive 公共契约（有限非零正数） ----------
@pytest.mark.parametrize("bad", [0, -1, 0.0, "x", None, True, float("nan")])
def test_require_positive_rejects_invalid(bad):
    """require_positive 拒绝非数/非有限/非正/布尔，抛出 ValueError。"""
    with pytest.raises(ValueError):
        from deepquant.backtest.config import require_positive

        require_positive(bad, "x")


def test_require_positive_accepts_positive():
    """require_positive 接受有限正数并返回 float。"""
    from deepquant.backtest.config import require_positive

    assert require_positive(5, "x") == 5.0
    assert require_positive(0.3, "x") == 0.3


# ---------- 6. require_naive_timestamp 拒绝 NaT ----------
def test_require_naive_timestamp_rejects_nat():
    """pd.NaT 必须被拒绝（「不能为 NaT」契约）。"""
    from deepquant.backtest.config import require_naive_timestamp

    with pytest.raises(ValueError) as exc:
        require_naive_timestamp(pd.NaT, "start_date")
    assert "NaT" in str(exc.value)
