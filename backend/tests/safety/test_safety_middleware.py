"""安全中间件与装配：熔断拒绝、5xx 计入、4xx 不计入、排空期拒绝新任务、留痕。

判据同样不是"返回非 200"：4xx 与 5xx 必须被区分开——把用户手抖的错误参数也计入
熔断，几个拼写错误就能把整个功能熔断掉，这比不熔断更糟。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deepquant.api.config import get_data_root
from deepquant.audit.store import AuditStore
from deepquant.safety import breaker
from deepquant.safety.lifecycle import INFLIGHT

_BAD_BODY = {"initial_cash": 100000}  # 缺三个必填字段 → 422


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "safety"
    monkeypatch.setenv("DEEPQUANT_SAFETY_DIR", str(root))
    monkeypatch.setenv("DEEPQUANT_RISK_MODE", "off")
    monkeypatch.delenv("DEEPQUANT_CAPABILITY_MODE", raising=False)
    monkeypatch.delenv("DEEPQUANT_AUDIT_DIR", raising=False)
    monkeypatch.delenv("DEEPQUANT_ALLOW_DIRTY_START", raising=False)
    INFLIGHT.reset()
    yield root
    INFLIGHT.reset()


def _client(tmp_path: Path) -> TestClient:
    from deepquant.api.app import create_app

    app = create_app()
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    app.dependency_overrides[get_data_root] = lambda: data_root.resolve()
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# 放行路径
# --------------------------------------------------------------------------- #


def test_normal_request_passes_with_header(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/strategies")
    assert response.status_code == 200
    assert response.headers["X-DeepQuant-Safety"] == "pass"


def test_4xx_does_not_count_as_breaker_failure(tmp_path: Path) -> None:
    """用户输入错误不是系统故障；把它计入熔断会让几个错参数就熔断整个功能。"""
    client = _client(tmp_path)
    for _ in range(5):
        assert client.post("/backtests", json=_BAD_BODY).status_code == 422
    assert breaker.load("backtest.run").consecutive_failures == 0
    assert breaker.allow("backtest.run").allowed is True


def test_5xx_counts_as_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """系统失败必须计入。用注册过的操作类型构造 500，验证计数链路。"""
    from deepquant.api.app import create_app

    app = create_app()
    app.dependency_overrides[get_data_root] = lambda: (tmp_path / "data").resolve()

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("故意失败")

    monkeypatch.setattr(
        "deepquant.safety.middleware.resolve_operation",
        lambda method, path: "backtest.run" if path == "/boom" else None,
    )

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/boom").status_code == 500
    assert breaker.load("backtest.run").consecutive_failures == 1


# --------------------------------------------------------------------------- #
# 熔断拒绝
# --------------------------------------------------------------------------- #


def test_open_breaker_rejects_with_retry_after(tmp_path: Path) -> None:
    for _ in range(3):
        breaker.record_failure("backtest.run", "模拟失败")

    response = _client(tmp_path).post("/backtests", json=_BAD_BODY)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "breaker_open"
    assert response.headers["X-DeepQuant-Safety"] == "rejected"
    assert int(response.headers["Retry-After"]) > 0


def test_breaker_rejection_skips_business(tmp_path: Path) -> None:
    """熔断打开时业务不应执行：4xx 变 503 就是证据。"""
    for _ in range(3):
        breaker.record_failure("backtest.run", "x")
    assert _client(tmp_path).post("/backtests", json=_BAD_BODY).status_code == 503


def test_one_scope_does_not_block_another(tmp_path: Path) -> None:
    for _ in range(3):
        breaker.record_failure("backtest.run", "x")
    client = _client(tmp_path)
    assert client.post("/backtests", json=_BAD_BODY).status_code == 503
    assert client.get("/strategies").status_code == 200


def test_breaker_rejection_is_audited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("DEEPQUANT_AUDIT_DIR", str(audit_dir))
    for _ in range(3):
        breaker.record_failure("backtest.run", "x")

    response = _client(tmp_path).post("/backtests", json=_BAD_BODY, headers={"X-Operator": "alice"})
    assert response.status_code == 503

    store = AuditStore(audit_dir)
    cards = store.query(reason_code="breaker_open")
    assert len(cards) == 1
    assert cards[0]["outcome"]["safety"]["scope"] == "backtest.run"


# --------------------------------------------------------------------------- #
# 排空期
# --------------------------------------------------------------------------- #


def test_draining_rejects_new_tasks(tmp_path: Path) -> None:
    client = _client(tmp_path)
    INFLIGHT.begin_drain()
    response = client.get("/strategies")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "cleanup_incomplete"


# --------------------------------------------------------------------------- #
# 装配顺序
# --------------------------------------------------------------------------- #


def test_middleware_order() -> None:
    """审计最外层、安全在能力外层——方向反了就会丢工件或漏判。"""
    from deepquant.api.app import create_app

    names = [m.cls.__name__ for m in create_app().user_middleware]
    assert names.index("AuditMiddleware") < names.index("SafetyMiddleware")
    assert names.index("SafetyMiddleware") < names.index("CapabilityGateMiddleware")
    assert names.index("CapabilityGateMiddleware") < names.index("RiskGateMiddleware")


def test_unmapped_route_still_counted_in_flight(tmp_path: Path) -> None:
    """未登记路由不参与熔断，但仍要计入在飞数——否则退出清空会漏掉它们。"""
    client = _client(tmp_path)
    assert client.get("/health").status_code == 200
    assert INFLIGHT.count == 0  # 请求结束后归零


# --------------------------------------------------------------------------- #
# lifespan 启动门
# --------------------------------------------------------------------------- #


def test_lifespan_writes_and_removes_session(tmp_path: Path, monkeypatch) -> None:
    from deepquant.api.app import create_app

    root = tmp_path / "safety_lifecycle"
    monkeypatch.setenv("DEEPQUANT_SAFETY_DIR", str(root))
    app = create_app()
    app.dependency_overrides[get_data_root] = lambda: (tmp_path / "data").resolve()

    with TestClient(app) as client:
        assert client.get("/strategies").status_code == 200
        assert (root / "session.json").exists(), "启动时必须写会话标记，否则崩溃无法检测"

    assert not (root / "session.json").exists(), "正常退出必须删除会话标记"
    assert (root / "cleanup_log.jsonl").exists(), "退出必须留下清理证据"


def test_lifespan_refuses_dirty_start(tmp_path: Path, monkeypatch) -> None:
    from deepquant.api.app import create_app
    from deepquant.safety.lifecycle import DirtyShutdown

    root = tmp_path / "safety_dirty"
    root.mkdir(parents=True)
    (root / "session.json").write_text('{"pid": 1, "started_at": "x"}', encoding="utf-8")
    monkeypatch.setenv("DEEPQUANT_SAFETY_DIR", str(root))

    with pytest.raises(DirtyShutdown):
        with TestClient(create_app()):
            pass
