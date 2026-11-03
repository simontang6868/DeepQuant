"""能力准入策略：模式、禁用、授权、版本兼容。

这一层的错误方向不对称：**误拦**会让线上调用失败（立刻被发现），**漏拦**会让
"以为关了其实没关"持续存在（几乎不会被发现）。因此这里的测试更关注漏拦：
配置写错必须报错、观察期判定必须与强制期一致、``off`` 必须真的不介入。
"""

from __future__ import annotations

import pytest

from deepquant.capabilities import policy
from deepquant.capabilities.policy import (
    DISABLED_ENV,
    GRANTS_ENV,
    MODE_ENV,
    CapabilityConfigError,
    CapabilityDenied,
    check_version,
    disabled,
    evaluate,
    grants,
    guard,
    mode,
    policy_snapshot,
)
from deepquant.capabilities.registry import get


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (MODE_ENV, DISABLED_ENV, GRANTS_ENV):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- #
# 模式
# --------------------------------------------------------------------------- #


def test_default_mode_is_off() -> None:
    assert mode() == policy.MODE_OFF


@pytest.mark.parametrize("value", ["observe", "enforce", "ENFORCE", " observe "])
def test_valid_modes(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(MODE_ENV, value)
    assert mode() == value.strip().lower()


@pytest.mark.parametrize("value", ["on", "true", "1", "strict"])
def test_invalid_mode_raises(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """拼错模式必须报错：静默当成 off 就等于"以为开了其实没开"。"""
    monkeypatch.setenv(MODE_ENV, value)
    with pytest.raises(CapabilityConfigError, match="非法"):
        mode()


# --------------------------------------------------------------------------- #
# 禁用清单
# --------------------------------------------------------------------------- #


def test_no_disabled_by_default() -> None:
    assert disabled() == frozenset()


def test_disable_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLED_ENV, "backtest.run")
    assert disabled() == {"backtest.run"}


def test_disable_multiple_and_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLED_ENV, "backtest.*, strategy.list")
    # 通配在解析期展开为具体 id：这样"关了哪几个"永远是确定的
    assert disabled() == {"backtest.run", "backtest.compare", "strategy.list"}


def test_disable_unknown_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLED_ENV, "backtest.runm")  # 拼错一个字母
    with pytest.raises(CapabilityConfigError, match="不存在"):
        disabled()


def test_disable_wildcard_matching_nothing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLED_ENV, "ordres.*")
    with pytest.raises(CapabilityConfigError, match="未匹配"):
        disabled()


# --------------------------------------------------------------------------- #
# 授权表
# --------------------------------------------------------------------------- #


def test_no_grants_by_default() -> None:
    assert grants() == {}


def test_grants_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=alice,bob; portfolio.backtest=*")
    table = grants()
    assert table["backtest.run"] == {"alice", "bob"}
    assert table["portfolio.backtest"] == {"*"}


def test_grants_unknown_cap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=alice")
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=alice;nope.cap=bob")
    with pytest.raises(CapabilityConfigError, match="不存在"):
        grants()


def test_grants_empty_list_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """空名单是"没人能用"，与"忘了填"不可区分，必须显式写 ``*``。"""
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=")
    with pytest.raises(CapabilityConfigError, match="为空"):
        grants()


def test_grants_duplicate_entry_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=alice;backtest.run=bob")
    with pytest.raises(CapabilityConfigError, match="重复"):
        grants()


def test_grants_missing_equals_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRANTS_ENV, "backtest.run alice")
    with pytest.raises(CapabilityConfigError, match="缺少"):
        grants()


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #


def _cap():
    cap = get("backtest.run")
    assert cap is not None
    return cap


def test_evaluate_allows_by_default() -> None:
    assert evaluate(_cap(), "alice").allowed is True


def test_evaluate_blocks_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLED_ENV, "backtest.run")
    decision = evaluate(_cap(), "alice")
    assert decision.allowed is False
    assert decision.code == "capability_disabled"
    assert decision.status_code == 403


def test_evaluate_blocks_unlisted_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=alice")
    decision = evaluate(_cap(), "bob")
    assert decision.allowed is False
    assert decision.code == "capability_not_permitted"


def test_evaluate_allows_listed_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=alice")
    assert evaluate(_cap(), "alice").allowed is True


def test_evaluate_wildcard_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=*")
    assert evaluate(_cap(), "whoever").allowed is True


def test_disabled_beats_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    """先看"是否开放"再看"谁被授权"：授权一个已关闭的能力没有意义。"""
    monkeypatch.setenv(DISABLED_ENV, "backtest.run")
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=*")
    assert evaluate(_cap(), "alice").code == "capability_disabled"


# --------------------------------------------------------------------------- #
# 版本兼容
# --------------------------------------------------------------------------- #


def test_version_unpinned_is_not_checked() -> None:
    assert check_version(_cap(), None) is None
    assert check_version(_cap(), "") is None


def test_version_same_major_passes() -> None:
    assert check_version(_cap(), "1.9.9") is None


def test_version_cross_major_rejected() -> None:
    decision = check_version(_cap(), "2.0.0")
    assert decision is not None and decision.allowed is False
    assert decision.code == "capability_version_unsupported"
    assert decision.status_code == 409


# --------------------------------------------------------------------------- #
# 服务层闸门
# --------------------------------------------------------------------------- #


def test_guard_off_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLED_ENV, "audit.replay")
    # off 下连配置都不读：关着的系统不该因为配置写错而拒绝业务
    assert guard("audit.replay", "alice").allowed is True


def test_guard_enforce_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    monkeypatch.setenv(DISABLED_ENV, "audit.replay")
    with pytest.raises(CapabilityDenied) as excinfo:
        guard("audit.replay", "alice")
    assert excinfo.value.code == "capability_disabled"
    assert excinfo.value.status_code == 403


def test_guard_observe_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODE_ENV, "observe")
    monkeypatch.setenv(DISABLED_ENV, "audit.replay")
    decision = guard("audit.replay", "alice")
    assert decision.allowed is False and decision.code == "capability_disabled"


def test_guard_unknown_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    with pytest.raises(CapabilityDenied) as excinfo:
        guard("ghost.cap", "alice")
    assert excinfo.value.code == "capability_unknown"


def test_guard_enforce_allows_when_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    assert guard("audit.replay", "alice").allowed is True


# --------------------------------------------------------------------------- #
# 快照与自检
# --------------------------------------------------------------------------- #


def test_policy_snapshot_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODE_ENV, "nonsense")
    snapshot = policy_snapshot()
    assert snapshot["error"] and "非法" in str(snapshot["error"])


def test_policy_snapshot_ok() -> None:
    snapshot = policy_snapshot()
    assert snapshot["error"] is None
    assert snapshot["mode"] == "off"
    assert snapshot["n_capabilities"] == 6


def test_assert_config_valid_passes() -> None:
    policy.assert_config_valid()


def test_assert_config_valid_catches_bad_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODE_ENV, "strict")
    with pytest.raises(CapabilityConfigError):
        policy.assert_config_valid()
