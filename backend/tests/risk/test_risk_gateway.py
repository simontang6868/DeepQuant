"""网关测试：三模式语义、fail-closed、首个拒绝即停、响应体契约。

最关键的一条是 **observe 模式下的判定必须与 enforce 完全一致**。如果观察期为了
"不拦"而顺手把判定也放宽了，那么观察期采到的 would_reject 计数就是假的，
灰度上线就退化成"拍脑袋上线"。
"""

from __future__ import annotations

import pytest

from deepquant.risk.checkpoints import (
    Checkpoint,
    OperationContext,
    RiskLimits,
    allow,
    reject as reject_decision,
)
from deepquant.risk.gateway import (
    MODE_ENFORCE,
    MODE_OBSERVE,
    MODE_OFF,
    RISK_ALLOWLIST_ENV,
    RISK_MODE_ENV,
    RiskConfigurationError,
    RiskGateway,
    operator_allowlist,
    risk_mode,
)
from deepquant.risk.reason_codes import CATEGORY_INTERNAL


def _ctx(**kwargs) -> OperationContext:
    base = dict(
        operation="backtest.run",
        method="POST",
        path="/backtests",
        operator="ops@example",
        payload={
            "dataset_symbol": "TEST01",
            "strategy_id": "ma_cross",
            "strategy_params": {"fast_window": 5, "slow_window": 20},
            "initial_cash": 100000,
        },
        body_bytes=100,
    )
    base.update(kwargs)
    return OperationContext(**base)


def _gateway() -> RiskGateway:
    return RiskGateway.build(
        dataset_lookup=lambda s: {"symbol": s},
        strategy_lookup=lambda: {"ma_cross": object()},
        run_lookup=lambda r: {"run_id": r},
    )


# --------------------------------------------------------------------------- #
# 模式
# --------------------------------------------------------------------------- #
def test_off_mode_skips_all_checks() -> None:
    result = _gateway().evaluate(_ctx(), mode=MODE_OFF)
    assert result.allowed is True
    assert result.would_reject is False
    assert result.decisions == ()


def test_observe_mode_allows_but_records_would_reject() -> None:
    result = _gateway().evaluate(_ctx(operator=None), mode=MODE_OBSERVE)
    assert result.allowed is True, "观察期不拦截"
    assert result.would_reject is True, "但必须记录「本应拒绝」"
    assert result.reason_code == "identity_missing"


def test_enforce_mode_blocks() -> None:
    result = _gateway().evaluate(_ctx(operator=None), mode=MODE_ENFORCE)
    assert result.allowed is False
    assert result.status_code == 403
    assert result.reason_code == "identity_missing"


def test_judgement_is_identical_across_observe_and_enforce() -> None:
    """同一请求在两种模式下必须得到同一个原因码与同一个检查点。"""
    observed = _gateway().evaluate(_ctx(operator=None), mode=MODE_OBSERVE)
    enforced = _gateway().evaluate(_ctx(operator=None), mode=MODE_ENFORCE)
    assert observed.reason_code == enforced.reason_code
    assert observed.rejection.checkpoint == enforced.rejection.checkpoint  # type: ignore[union-attr]
    assert observed.to_audit_dict()["details"] == enforced.to_audit_dict()["details"]


def test_unknown_mode_raises() -> None:
    with pytest.raises(RiskConfigurationError):
        _gateway().evaluate(_ctx(), mode="semi")


# --------------------------------------------------------------------------- #
# 执行语义
# --------------------------------------------------------------------------- #
def test_stops_at_first_denial() -> None:
    """首个拒绝即停：后续检查点不执行，工件里只留到这一处。"""
    result = _gateway().evaluate(_ctx(operator=None), mode=MODE_ENFORCE)
    assert len(result.decisions) == 1
    assert result.decisions[0].checkpoint == "identity"


def test_all_checkpoints_run_when_allowed() -> None:
    result = _gateway().evaluate(_ctx(), mode=MODE_ENFORCE)
    assert result.allowed is True
    assert [d.checkpoint for d in result.decisions] == ["identity", "limit", "state", "field"]


def test_checkpoint_exception_is_fail_closed() -> None:
    """检查点自身抛异常时必须拒绝，不能"跳过这一项继续"。"""

    class Broken(Checkpoint):
        name = "broken"
        category = CATEGORY_INTERNAL

        def applies_to(self, operation: str) -> bool:
            return True

        def check(self, ctx):  # noqa: ANN001
            raise RuntimeError("配置读不到")

    gateway = RiskGateway([Broken()])
    result = gateway.evaluate(_ctx(), mode=MODE_ENFORCE)
    assert result.allowed is False
    assert result.reason_code == "gateway_internal_error"
    assert result.rejection is not None
    assert "RuntimeError" in result.rejection.message


def test_checkpoint_not_applicable_is_skipped() -> None:
    class OnlyPortfolio(Checkpoint):
        name = "only_portfolio"
        category = "state"

        def applies_to(self, operation: str) -> bool:
            return operation == "portfolio.backtest"

        def check(self, ctx):  # noqa: ANN001
            return reject_decision("only_portfolio", "state", "state_dataset_unavailable")

    result = RiskGateway([OnlyPortfolio()]).evaluate(_ctx(), mode=MODE_ENFORCE)
    assert result.allowed is True
    assert result.decisions == ()


def test_allow_decision_carries_no_code() -> None:
    decision = allow("x", "identity")
    assert decision.code is None
    assert decision.status_code == 200


# --------------------------------------------------------------------------- #
# 响应体与工件摘要
# --------------------------------------------------------------------------- #
def test_response_body_matches_api_error_contract() -> None:
    """错误体结构必须与 api.errors 的既有契约一致（code/message/details）。"""
    result = _gateway().evaluate(_ctx(operator=None), mode=MODE_ENFORCE)
    body = result.to_response_body()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) >= {"code", "message", "details"}
    assert error["code"] == "identity_missing"
    assert error["details"]["checkpoint"] == "identity"
    assert error["details"]["mode"] == MODE_ENFORCE


def test_audit_dict_is_flat_and_searchable() -> None:
    result = _gateway().evaluate(_ctx(operator=None), mode=MODE_ENFORCE)
    audit = result.to_audit_dict()
    assert audit["reason_code"] == "identity_missing"
    assert audit["checkpoint"] == "identity"
    assert audit["mode"] == MODE_ENFORCE
    assert audit["allowed"] is False
    assert isinstance(audit["n_checked"], int)


# --------------------------------------------------------------------------- #
# 环境变量
# --------------------------------------------------------------------------- #
def test_mode_defaults_to_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RISK_MODE_ENV, raising=False)
    assert risk_mode() == MODE_OFF


@pytest.mark.parametrize("raw,expected", [("observe", MODE_OBSERVE), ("ENFORCE", MODE_ENFORCE), (" off ", MODE_OFF)])
def test_mode_parsing(raw: str, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, raw)
    assert risk_mode() == expected


def test_invalid_mode_in_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法配置不得静默降级为 off —— 那会让"以为开了"变成"其实没开"。"""
    monkeypatch.setenv(RISK_MODE_ENV, "yes")
    with pytest.raises(RiskConfigurationError):
        risk_mode()


def test_allowlist_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RISK_ALLOWLIST_ENV, raising=False)
    assert operator_allowlist() is None
    monkeypatch.setenv(RISK_ALLOWLIST_ENV, " alice , bob ,, ")
    assert operator_allowlist() == frozenset({"alice", "bob"})
    monkeypatch.setenv(RISK_ALLOWLIST_ENV, "  ,  ")
    assert operator_allowlist() is None


def test_limits_are_loose_enough_for_normal_research() -> None:
    """限额只拦明显误输入：默认上限应远高于正常研究请求。"""
    limits = RiskLimits()
    assert limits.max_initial_cash >= 100_000_000
    assert limits.max_body_bytes >= 1024 * 1024
    assert limits.max_date_span_days >= 365 * 20
