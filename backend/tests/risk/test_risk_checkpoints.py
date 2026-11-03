"""四类检查点测试：每类都要覆盖「允许 / 拒绝 / 无法校验」三条路径。

其中「无法校验」最容易被写成"先放过"：依赖数据拿不到时默认放行，看起来业务更
顺畅，实际上把风险留在了无人知晓的地方。本文件用 ``checkpoint_unverifiable``
钉住 fail-closed 语义。
"""

from __future__ import annotations

import pytest

from deepquant.risk.checkpoints import (
    REQUIRED_FIELDS_BY_OPERATION,
    FieldCheckpoint,
    IdentityCheckpoint,
    LimitCheckpoint,
    OperationContext,
    RiskLimits,
    StateCheckpoint,
    category_coverage,
    default_checkpoints,
)
from deepquant.risk.reason_codes import REQUIRED_CATEGORIES


def _ctx(operation: str = "backtest.run", **kwargs) -> OperationContext:
    base = dict(
        operation=operation,
        method="POST",
        path="/backtests",
        operator="ops@example",
        payload={
            "dataset_symbol": "TEST01",
            "strategy_id": "ma_cross",
            "strategy_params": {"fast_window": 5, "slow_window": 20},
            "initial_cash": 100000,
        },
        body_bytes=120,
    )
    base.update(kwargs)
    return OperationContext(**base)


# --------------------------------------------------------------------------- #
# 1. 身份
# --------------------------------------------------------------------------- #
def test_identity_allows_known_operator() -> None:
    cp = IdentityCheckpoint(allowlist=["ops@example"])
    assert cp.check(_ctx()).allowed is True


def test_identity_rejects_missing_operator() -> None:
    cp = IdentityCheckpoint()
    decision = cp.check(_ctx(operator=None))
    assert decision.allowed is False
    assert decision.code == "identity_missing"
    assert decision.category == "identity"


def test_identity_rejects_blank_operator() -> None:
    """空白字符串等同于没提供——不能因为"字段存在"就当成已核实。"""
    cp = IdentityCheckpoint()
    assert cp.check(_ctx(operator="   ")).code == "identity_missing"


def test_identity_rejects_operator_not_in_allowlist() -> None:
    cp = IdentityCheckpoint(allowlist=["alice", "bob"])
    decision = cp.check(_ctx(operator="mallory"))
    assert decision.code == "identity_not_allowed"
    assert decision.details["operator"] == "mallory"
    # 详情里不得出现完整允许名单（否则等于把白名单泄露给被拒绝者）
    assert "allowlist" not in decision.details


def test_identity_without_allowlist_only_checks_presence() -> None:
    cp = IdentityCheckpoint()
    assert cp.check(_ctx(operator="anyone")).allowed is True


# --------------------------------------------------------------------------- #
# 2. 限额
# --------------------------------------------------------------------------- #
def test_limit_allows_normal_request() -> None:
    assert LimitCheckpoint().check(_ctx()).allowed is True


def test_limit_rejects_oversized_body() -> None:
    decision = LimitCheckpoint().check(_ctx(body_bytes=10 * 1024 * 1024))
    assert decision.code == "limit_body_too_large"
    assert decision.status_code == 413


def test_limit_rejects_cash_out_of_range() -> None:
    limits = RiskLimits(min_initial_cash=1_000, max_initial_cash=1_000_000_000)
    high = LimitCheckpoint(limits).check(_ctx(payload={"initial_cash": 1e12}))
    low = LimitCheckpoint(limits).check(_ctx(payload={"initial_cash": 1.0}))
    assert high.code == "limit_cash_range"
    assert low.code == "limit_cash_range"


def test_limit_ignores_non_numeric_cash() -> None:
    """类型错误交给 schema，不在限额里替它判断——否则会报出误导性的原因码。"""
    decision = LimitCheckpoint().check(_ctx(payload={"initial_cash": "很多钱"}))
    assert decision.allowed is True


def test_limit_rejects_excessive_date_span() -> None:
    decision = LimitCheckpoint().check(
        _ctx(payload={"start_date": "1990-01-01", "end_date": "2026-01-01"})
    )
    assert decision.code == "limit_date_span"
    assert decision.details["span_days"] > 365 * 30


def test_limit_ignores_unparsable_dates() -> None:
    """解析不了就不臆造跨度：不能把"看不懂"当成"超限"。"""
    decision = LimitCheckpoint().check(
        _ctx(payload={"start_date": "not-a-date", "end_date": "2026-01-01"})
    )
    assert decision.allowed is True


def test_limit_date_span_within_bound() -> None:
    decision = LimitCheckpoint().check(
        _ctx(payload={"start_date": "2020-01-01", "end_date": "2021-01-01"})
    )
    assert decision.allowed is True


# --------------------------------------------------------------------------- #
# 3. 状态
# --------------------------------------------------------------------------- #
def _state(**kwargs) -> StateCheckpoint:
    return StateCheckpoint(
        dataset_lookup=kwargs.get("dataset_lookup", lambda s: {"symbol": s}),
        strategy_lookup=kwargs.get("strategy_lookup", lambda: {"ma_cross": object()}),
        run_lookup=kwargs.get("run_lookup", lambda r: {"run_id": r}),
    )


def test_state_allows_known_dataset_and_strategy() -> None:
    assert _state().check(_ctx()).allowed is True


def test_state_rejects_unknown_dataset() -> None:
    cp = _state(dataset_lookup=lambda s: None)
    decision = cp.check(_ctx())
    assert decision.code == "state_dataset_unavailable"
    assert decision.details["symbol"] == "TEST01"


def test_state_rejects_unregistered_strategy() -> None:
    cp = _state(strategy_lookup=lambda: {"mean_reversion": object()})
    decision = cp.check(_ctx())
    assert decision.code == "state_strategy_unavailable"
    assert "ma_cross" not in decision.details["available"]


def test_state_rejects_unknown_run_id() -> None:
    cp = _state(run_lookup=lambda r: None)
    decision = cp.check(_ctx("backtest.compare", payload={"run_ids": ["r1", "r2"]}))
    assert decision.code == "state_run_missing"


def test_state_checks_every_portfolio_symbol() -> None:
    seen: list[str] = []

    def lookup(symbol: str):
        seen.append(symbol)
        return None if symbol == "BAD" else {"symbol": symbol}

    cp = _state(dataset_lookup=lookup)
    decision = cp.check(_ctx("portfolio.backtest", payload={"dataset_symbols": ["A", "BAD", "C"]}))
    assert decision.code == "state_dataset_unavailable"
    assert seen == ["A", "BAD"], "遇到第一个不可用标的就应停止，不做无意义的后续查询"


def test_state_lookup_failure_is_unverifiable_not_allowed() -> None:
    """查不了 ≠ 不存在，也绝不等于通过。"""

    def boom(symbol: str):
        raise OSError("磁盘不可读")

    decision = _state(dataset_lookup=boom).check(_ctx())
    assert decision.allowed is False
    assert decision.code == "checkpoint_unverifiable"
    assert decision.unverifiable is True
    assert decision.status_code == 503


def test_state_without_lookup_is_unverifiable() -> None:
    """未注入查询能力时不得放行：那等于"没检查"却显示"通过"。"""
    cp = StateCheckpoint(dataset_lookup=None, strategy_lookup=None, run_lookup=None)
    assert cp.check(_ctx()).code == "checkpoint_unverifiable"


def test_state_skips_missing_field() -> None:
    """字段缺失由字段检查点负责，状态检查不重复报同一个问题。"""
    cp = _state()
    decision = cp.check(_ctx(payload={"strategy_id": "ma_cross"}))
    assert decision.allowed is True


# --------------------------------------------------------------------------- #
# 4. 字段
# --------------------------------------------------------------------------- #
def test_field_allows_complete_payload() -> None:
    assert FieldCheckpoint().check(_ctx()).allowed is True


def test_field_rejects_missing_key() -> None:
    payload = {"strategy_id": "ma_cross", "strategy_params": {}, "initial_cash": 1}
    decision = FieldCheckpoint().check(_ctx(payload=payload))
    assert decision.code == "field_missing"
    assert decision.details["missing"] == ["dataset_symbol"]


def test_field_rejects_empty_values() -> None:
    payload = {
        "dataset_symbol": "",
        "strategy_id": "ma_cross",
        "strategy_params": {},
        "initial_cash": 1,
    }
    decision = FieldCheckpoint().check(_ctx(payload=payload))
    assert decision.code == "field_empty"
    assert decision.details["empty"] == ["dataset_symbol"]


def test_field_allows_empty_strategy_params_dict() -> None:
    """``strategy_params={}`` 表示"全部用默认参数"，不是缺失。

    这是"误拒正常业务"的具体入口：把空字典判成空值，会让所有用默认参数的
    合法回测被拒，而调用方只会以为系统坏了。
    """
    payload = {
        "dataset_symbol": "TEST01",
        "strategy_id": "ma_cross",
        "strategy_params": {},
        "initial_cash": 1,
    }
    assert FieldCheckpoint().check(_ctx(payload=payload)).allowed is True


def test_field_rejects_empty_body_for_operation_with_required_fields() -> None:
    decision = FieldCheckpoint().check(_ctx(payload=None))
    assert decision.code == "field_missing"
    assert "dataset_symbol" in decision.details["missing"]


def test_field_allows_read_only_operation_without_body() -> None:
    assert FieldCheckpoint().check(_ctx("dataset.list", payload=None)).allowed is True


def test_required_fields_table_covers_all_mutating_operations() -> None:
    """核心可变更操作必须在字段表里出现（可以是空元组，但不能漏登记）。"""
    from deepquant.audit.middleware import OPERATION_ROUTES

    mapped = {op for _, _, op in OPERATION_ROUTES}
    assert mapped <= set(REQUIRED_FIELDS_BY_OPERATION), (
        f"以下操作未登记必需字段：{mapped - set(REQUIRED_FIELDS_BY_OPERATION)}"
    )


# --------------------------------------------------------------------------- #
# 装配
# --------------------------------------------------------------------------- #
def test_default_checkpoints_cover_four_categories() -> None:
    report = category_coverage(default_checkpoints())
    assert report["missing"] == []
    assert report["ratio"] == 1.0
    assert set(report["present"]) >= set(REQUIRED_CATEGORIES)


def test_checkpoint_order_is_identity_limit_state_field() -> None:
    """顺序影响报错质量：缺字段的请求不应先被报成"状态异常"。"""
    names = [c.name for c in default_checkpoints()]
    assert names == ["identity", "limit", "state", "field"]
