"""能力闸门中间件：装配顺序、执行前拒绝、留痕、观察期、版本与弃用。

最重要的一条断言是「拒绝发生在业务之前」：判据不是"返回非 200"，而是
**同一个请求在关闸时返回 403、在开闸时返回 422**。请求体本身非法，若闸门在
业务之后，用户会先看到 422；先看到 403 才能证明业务没被执行。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deepquant.api.config import get_data_root
from deepquant.audit.store import AuditStore
from deepquant.capabilities.policy import (
    DEPRECATED_HEADER,
    DISABLED_ENV,
    GRANTS_ENV,
    MODE_ENV,
    RESPONSE_ID_HEADER,
    RESPONSE_VERSION_HEADER,
    VERSION_HEADER,
    WOULD_REJECT_HEADER,
)
from deepquant.capabilities.middleware import (
    CAPABILITY_HEADER,
    UNREGISTERED_ACTION,
)

_BAD_BODY = {"initial_cash": 100000}  # 缺 dataset_symbol/strategy_id/strategy_params


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (MODE_ENV, DISABLED_ENV, GRANTS_ENV, "DEEPQUANT_AUDIT_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPQUANT_RISK_MODE", "off")  # 风控不参与，隔离本层行为


def _client(tmp_path: Path) -> TestClient:
    from deepquant.api.app import create_app

    app = create_app()
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    app.dependency_overrides[get_data_root] = lambda: data_root.resolve()
    return TestClient(app)


# --------------------------------------------------------------------------- #
# 默认关闭：零行为变化
# --------------------------------------------------------------------------- #


def test_off_mode_adds_no_headers(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/strategies")
    assert response.status_code == 200
    assert RESPONSE_ID_HEADER not in response.headers
    assert CAPABILITY_HEADER not in response.headers


def test_off_mode_does_not_block_disabled_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """off 下即使写了禁用清单也不介入（也不因其非法而拒绝业务）。"""
    monkeypatch.setenv(DISABLED_ENV, "backtest.run")
    response = _client(tmp_path).post("/backtests", json=_BAD_BODY)
    assert response.status_code == 422  # 走到业务校验，而不是 403


# --------------------------------------------------------------------------- #
# enforce：执行前拒绝
# --------------------------------------------------------------------------- #


def test_enforce_disabled_capability_rejected_before_business(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    monkeypatch.setenv(DISABLED_ENV, "backtest.run")
    response = _client(tmp_path).post("/backtests", json=_BAD_BODY)

    # 关键判据：请求体本身非法（开闸会是 422），但闸门挡住了业务 → 403
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "capability_disabled"
    assert body["error"]["details"]["capability"] == "backtest.run"
    assert response.headers[CAPABILITY_HEADER] == "rejected"


def test_enforce_unlisted_operator_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=alice")
    response = _client(tmp_path).post("/backtests", json=_BAD_BODY, headers={"X-Operator": "bob"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_not_permitted"


def test_enforce_listed_operator_reaches_business(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    monkeypatch.setenv(GRANTS_ENV, "backtest.run=alice")
    response = _client(tmp_path).post("/backtests", json=_BAD_BODY, headers={"X-Operator": "alice"})
    assert response.status_code == 422  # 授权通过，回到业务校验


def test_enforce_version_mismatch_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    response = _client(tmp_path).post(
        "/backtests", json=_BAD_BODY, headers={VERSION_HEADER: "2.0.0"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "capability_version_unsupported"


def test_enforce_same_major_version_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    response = _client(tmp_path).post(
        "/backtests", json=_BAD_BODY, headers={VERSION_HEADER: "1.7.3"}
    )
    assert response.status_code == 422


def test_enforce_unregistered_mutating_route_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新接口忘了登记能力 → 拒绝（"必经契约校验"的运行时一半）。"""
    monkeypatch.setenv(MODE_ENV, "enforce")
    from deepquant.api.app import create_app

    app = create_app()
    app.dependency_overrides[get_data_root] = lambda: (tmp_path / "data").resolve()

    @app.post("/ghost-action")
    def ghost_action() -> dict[str, str]:
        return {"ran": "yes"}

    response = TestClient(app).post("/ghost-action", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_unregistered"


def test_enforce_exempt_route_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    response = _client(tmp_path).get("/health")
    assert response.status_code == 200
    # 未登记能力 → 如实不加能力头，而不是编一个
    assert RESPONSE_ID_HEADER not in response.headers


def test_invalid_mode_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模式拼错 → 503 拒绝，不静默当成 off。"""
    monkeypatch.setenv(MODE_ENV, "strict")
    response = _client(tmp_path).get("/strategies")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capability_internal_error"


def test_invalid_disabled_list_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    monkeypatch.setenv(DISABLED_ENV, "backtest.runm")  # 拼错
    response = _client(tmp_path).get("/strategies")
    assert response.status_code == 503


# --------------------------------------------------------------------------- #
# 观察期：判定与强制期一致，只是不拦
# --------------------------------------------------------------------------- #


def test_observe_does_not_block_but_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "observe")
    monkeypatch.setenv(DISABLED_ENV, "backtest.run")
    response = _client(tmp_path).post("/backtests", json=_BAD_BODY)
    assert response.status_code == 422  # 业务照常执行
    assert response.headers[CAPABILITY_HEADER] == "would-reject"
    assert response.headers[WOULD_REJECT_HEADER] == "capability_disabled"


def test_observe_on_exempt_route_adds_no_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "observe")
    response = _client(tmp_path).get("/health")
    assert response.status_code == 200
    assert CAPABILITY_HEADER not in response.headers


# --------------------------------------------------------------------------- #
# 成功路径的响应头
# --------------------------------------------------------------------------- #


def test_success_carries_capability_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MODE_ENV, "enforce")
    response = _client(tmp_path).get("/strategies")
    assert response.status_code == 200
    assert response.headers[CAPABILITY_HEADER] == "pass"
    assert response.headers[RESPONSE_ID_HEADER] == "strategy.list"
    assert response.headers[RESPONSE_VERSION_HEADER] == "1.0.0"


def test_deprecated_capability_signals_successor() -> None:
    """弃用信息必须能被机器读到，调用方据此排期迁移。"""
    from starlette.responses import Response

    from deepquant.capabilities.middleware import _apply_success_headers
    from deepquant.capabilities.policy import Decision
    from deepquant.capabilities.registry import get

    cap = get("strategy.list")
    assert cap is not None
    import dataclasses

    deprecated = dataclasses.replace(cap, deprecated=True, successor="strategy.list.v2")
    response = Response()
    _apply_success_headers(response, deprecated, Decision("strategy.list", True), "enforce")
    assert DEPRECATED_HEADER in response.headers
    assert "successor=strategy.list.v2" in response.headers[DEPRECATED_HEADER]


# --------------------------------------------------------------------------- #
# 与 T1 工件联动
# --------------------------------------------------------------------------- #


def test_rejection_is_recorded_in_audit_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("DEEPQUANT_AUDIT_DIR", str(audit_dir))
    monkeypatch.setenv(MODE_ENV, "enforce")
    monkeypatch.setenv(DISABLED_ENV, "backtest.run")

    response = _client(tmp_path).post("/backtests", json=_BAD_BODY, headers={"X-Operator": "alice"})
    assert response.status_code == 403

    store = AuditStore(audit_dir)
    cards = store.query(action="backtest.run")
    assert len(cards) == 1
    outcome = cards[0]["outcome"]
    assert outcome["status"] == "rejected"
    assert outcome["reason_code"] == "capability_disabled"
    assert outcome["capability"]["capability"] == "backtest.run"

    # 可按原因码等值检索——这是 T2/T4 共同要求的"可检索"
    assert len(store.query(reason_code="capability_disabled")) == 1


def test_success_card_carries_capability_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("DEEPQUANT_AUDIT_DIR", str(audit_dir))
    monkeypatch.setenv(MODE_ENV, "enforce")

    assert _client(tmp_path).get("/strategies").status_code == 200
    cards = AuditStore(audit_dir).query(action="strategy.list")
    assert cards[0]["outcome"]["capability"]["allowed"] is True


def test_unregistered_rejection_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未登记路由不会被审计中间件落卡，闸门必须自己补一张——否则审计留下空洞。"""
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("DEEPQUANT_AUDIT_DIR", str(audit_dir))
    monkeypatch.setenv(MODE_ENV, "enforce")

    from deepquant.api.app import create_app

    app = create_app()
    app.dependency_overrides[get_data_root] = lambda: (tmp_path / "data").resolve()

    @app.post("/ghost-action")
    def ghost_action() -> dict[str, str]:
        return {"ran": "yes"}

    assert TestClient(app).post("/ghost-action", json={}).status_code == 403

    store = AuditStore(audit_dir)
    cards = store.query(action=UNREGISTERED_ACTION)
    assert len(cards) == 1
    assert cards[0]["outcome"]["reason_code"] == "capability_unregistered"


# --------------------------------------------------------------------------- #
# 装配顺序
# --------------------------------------------------------------------------- #


def test_middleware_order_audit_outermost() -> None:
    """由内到外必须是 风控 → 能力 → 审计。

    反过来（能力在审计外层）时，能力层的 403 不会经过审计中间件，
    工件里会留下一片"没有记录"的空白——而缺口和"没人操作过"一模一样。
    """
    from deepquant.api.app import create_app

    names = [m.cls.__name__ for m in create_app().user_middleware]
    # T5 加入后是四层；顺序断言仍以"审计最外层、能力在风控外层"为核心约束
    assert names == [
        "AuditMiddleware",
        "SafetyMiddleware",
        "CapabilityGateMiddleware",
        "RiskGateMiddleware",
        "BuildHeaderMiddleware",
    ]
