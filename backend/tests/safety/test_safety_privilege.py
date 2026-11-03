"""最小权限：默认拒绝变更型操作、必须显式授予、无认证的前提如实标注。"""

from __future__ import annotations

import pytest

from deepquant.safety.least_privilege import (
    MODE_MINIMAL,
    MODE_OPEN,
    PERMISSION_MODE_ENV,
    PrivilegeConfigError,
    audit_matrix,
    check,
    mode,
    permission_summary,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PERMISSION_MODE_ENV, raising=False)


MUTATING = ("mutating", "reads_dataset")
READ_ONLY = ("read_only", "reads_db")


def test_default_mode_is_open() -> None:
    assert mode() == MODE_OPEN


def test_unknown_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PERMISSION_MODE_ENV, "strict")
    with pytest.raises(PrivilegeConfigError):
        mode()


def test_open_mode_allows_anonymous() -> None:
    """默认保持既有行为：引入权限机制不该在第一天挡住所有调用方。"""
    assert check("backtest.run", MUTATING, "anonymous").allowed is True


def test_minimal_requires_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PERMISSION_MODE_ENV, MODE_MINIMAL)
    decision = check("backtest.run", MUTATING, "anonymous", granted=frozenset({"alice"}))
    assert decision.allowed is False
    assert decision.code == "privilege_unidentified"


def test_minimal_denies_mutating_without_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PERMISSION_MODE_ENV, MODE_MINIMAL)
    decision = check("backtest.run", MUTATING, "alice", granted=None)
    assert decision.allowed is False
    assert decision.code == "privileged_operation_denied"
    assert decision.status_code == 403


def test_minimal_allows_mutating_with_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PERMISSION_MODE_ENV, MODE_MINIMAL)
    assert check("backtest.run", MUTATING, "alice", granted=frozenset({"alice"})).allowed is True


def test_minimal_denies_unlisted_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PERMISSION_MODE_ENV, MODE_MINIMAL)
    decision = check("backtest.run", MUTATING, "bob", granted=frozenset({"alice"}))
    assert decision.allowed is False and decision.code == "privileged_operation_denied"


def test_minimal_wildcard_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PERMISSION_MODE_ENV, MODE_MINIMAL)
    assert check("backtest.run", MUTATING, "anyone", granted=frozenset({"*"})).allowed is True


def test_minimal_allows_identified_operator_for_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只读能力不需要逐人授予：否则每加一个查询接口都要改一次名单。"""
    monkeypatch.setenv("DEEPQUANT_PERMISSION_MODE", MODE_MINIMAL)
    assert check("dataset.list", READ_ONLY, "alice", granted=None).allowed is True


def test_empty_operator_treated_as_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPQUANT_PERMISSION_MODE", MODE_MINIMAL)
    assert check("backtest.run", MUTATING, "   ", granted=frozenset({"alice"})).code == (
        "privilege_unidentified"
    )


def test_audit_matrix_marks_mutating_capabilities() -> None:
    class Cap:
        def __init__(self, cap_id: str, tags: tuple[str, ...]) -> None:
            self.cap_id = cap_id
            self.risk_tags = tags

    matrix = audit_matrix([Cap("a.run", MUTATING), Cap("b.list", READ_ONLY)])
    assert matrix["n_capabilities"] == 2 and matrix["n_mutating"] == 1
    rows = {r["capability"]: r for r in matrix["rows"]}
    assert rows["a.run"]["minimal_mode"] == "requires_explicit_grant"
    assert rows["b.list"]["minimal_mode"] == "identified_operator"


def test_matrix_states_the_no_auth_caveat() -> None:
    """这条注释是模块最重要的输出：没有认证，权限只是防误操作。"""
    matrix = audit_matrix([])
    assert "无认证" in str(matrix["caveat"])


def test_permission_summary_reports_caveat() -> None:
    summary = permission_summary(grants={"backtest.run": frozenset({"alice"})})
    assert summary["n_grants"] == 1
    assert "防误操作" in str(summary["caveat"])
