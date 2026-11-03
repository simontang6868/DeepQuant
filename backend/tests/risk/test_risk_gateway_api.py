"""网关接入的真实请求测试：拒绝带原因码、审计工件可检索、无旁路。

三条是 T2 验收标准里最容易被"看起来做了"糊弄过去的：

1. **每次拒绝均有原因码** —— 断言响应体的 ``code`` 是原因码表里的值，
   而不是泛泛的 ``backtest_rejected``；
2. **可在工件中检索** —— 断言 T1 工件里带 ``reason_code``，且能按原因码查回来；
3. **无绕过路径** —— 用路由清单对账（``bypass_report``），而不是靠"我们装了中间件"。

注意本文件**不建 conftest.py**：仓库既有测试用 ``from conftest import ...`` 裸导入，
两个 conftest 会抢 ``sys.modules["conftest"]``（已实测导致既有测试 ImportError）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from deepquant.api.app import app, create_app
from deepquant.api.config import get_data_root
from deepquant.audit.operations import AUDIT_DIR_ENV
from deepquant.audit.store import AuditStore
from deepquant.data.pipeline import ingest_daily_csv
from deepquant.risk.bypass import bypass_report
from deepquant.risk.gateway import MODE_ENFORCE, MODE_OBSERVE, RISK_MODE_ENV
from deepquant.risk.middleware import RISK_HEADER
from deepquant.risk.reason_codes import REASON_CODES

DATA_ROWS = 60
OPERATOR = "ops@deepquant"


def _make_daily_csv(path: Path, *, n: int = DATA_ROWS) -> Path:
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    closes: list[float] = []
    for i in range(n):
        if i < 15:
            closes.append(10.0 - 0.1 * i)
        elif i < 45:
            closes.append(8.6 + 0.1 * (i - 14))
        else:
            closes.append(11.6 - 0.1 * (i - 44))

    lines = ["date,open,high,low,close,volume"]
    for d, c in zip(dates, closes):
        open_, high, low, volume = c - 0.05, c + 0.1, c - 0.1, 10000
        lines.append(f"{d:%Y-%m-%d},{open_:.2f},{high:.2f},{low:.2f},{c:.2f},{volume}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPQUANT_DB_PATH", str(tmp_path / "backtests.db"))
    monkeypatch.delenv(RISK_MODE_ENV, raising=False)
    monkeypatch.delenv(AUDIT_DIR_ENV, raising=False)


@pytest.fixture
def audit_root(tmp_path: Path) -> Path:
    return tmp_path / "audit"


@pytest.fixture
def dataset(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    ingest_daily_csv(_make_daily_csv(src_dir / "TEST01.csv"), symbol="TEST01", data_root=data_root)
    app.dependency_overrides[get_data_root] = lambda: data_root.resolve()
    yield "TEST01"
    app.dependency_overrides.clear()


@pytest.fixture
def client(dataset: str):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def payload() -> dict:
    return {
        "dataset_symbol": "TEST01",
        "strategy_id": "ma_cross",
        "strategy_params": {"fast_window": 5, "slow_window": 20},
        "initial_cash": 100000,
    }


def _assert_reason(response, expected_code: str) -> None:
    body = response.json()
    assert "error" in body, body
    code = body["error"]["code"]
    assert code == expected_code, body
    assert code in REASON_CODES, f"响应里的原因码 {code!r} 不在原因码表中"
    assert body["error"]["details"]["checkpoint"]
    assert body["error"]["details"]["category"]


# --------------------------------------------------------------------------- #
# off：默认行为零变化
# --------------------------------------------------------------------------- #
def test_off_mode_is_transparent(client, payload: dict) -> None:
    """默认关闭：不带操作人也能正常回测，且没有风控响应头。"""
    response = client.post("/backtests", json=payload)
    assert response.status_code == 200
    assert RISK_HEADER not in response.headers


# --------------------------------------------------------------------------- #
# enforce：四类拒绝各自可复现
# --------------------------------------------------------------------------- #
def test_enforce_rejects_missing_identity(client, payload: dict, monkeypatch) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    response = client.post("/backtests", json=payload)
    assert response.status_code == 403
    assert response.headers[RISK_HEADER] == "rejected"
    _assert_reason(response, "identity_missing")


def test_enforce_rejects_unknown_dataset(client, payload: dict, monkeypatch) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    response = client.post(
        "/backtests",
        json=dict(payload, dataset_symbol="NOPE"),
        headers={"X-Operator": OPERATOR},
    )
    assert response.status_code == 409
    _assert_reason(response, "state_dataset_unavailable")


def test_enforce_rejects_cash_out_of_range(client, payload: dict, monkeypatch) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    response = client.post(
        "/backtests",
        json=dict(payload, initial_cash=1e12),
        headers={"X-Operator": OPERATOR},
    )
    assert response.status_code == 422
    _assert_reason(response, "limit_cash_range")


def test_enforce_rejects_missing_field(client, payload: dict, monkeypatch) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    body = {k: v for k, v in payload.items() if k != "strategy_id"}
    response = client.post("/backtests", json=body, headers={"X-Operator": OPERATOR})
    assert response.status_code == 422
    _assert_reason(response, "field_missing")


def test_enforce_rejects_unregistered_strategy(client, payload: dict, monkeypatch) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    response = client.post(
        "/backtests",
        json=dict(payload, strategy_id="no_such_strategy"),
        headers={"X-Operator": OPERATOR},
    )
    assert response.status_code == 409
    _assert_reason(response, "state_strategy_unavailable")


def test_enforce_allows_valid_request(client, payload: dict, monkeypatch) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    response = client.post("/backtests", json=payload, headers={"X-Operator": OPERATOR})
    assert response.status_code == 200, response.text
    assert response.headers[RISK_HEADER] == "pass"


# --------------------------------------------------------------------------- #
# observe：不拦，但记录"本应拒绝"
# --------------------------------------------------------------------------- #
def test_observe_mode_passes_but_marks_would_reject(client, payload: dict, monkeypatch) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, MODE_OBSERVE)
    response = client.post("/backtests", json=payload)
    assert response.status_code == 200, "观察期不拦截"
    assert response.headers[RISK_HEADER] == "would-reject"


# --------------------------------------------------------------------------- #
# 审计联动：拒绝也要落卡，且原因码可检索
# --------------------------------------------------------------------------- #
def test_rejection_is_recorded_in_audit_with_reason_code(
    client, payload: dict, monkeypatch, audit_root: Path
) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))
    response = client.post("/backtests", json=payload)
    assert response.status_code == 403

    store = AuditStore(audit_root)
    cards = store.query(action="backtest.run", reason_code="identity_missing")
    assert len(cards) == 1, "被网关拒绝的请求也必须留下工件，并带原因码"
    card = cards[0]
    assert card["outcome"]["status"] == "rejected"
    assert card["outcome"]["reason_code"] == "identity_missing"
    assert card["outcome"]["risk"]["checkpoint"] == "identity"
    assert card["outcome"]["risk"]["mode"] == MODE_ENFORCE
    assert store.verify().ok is True


def test_observe_mode_records_would_reject_in_audit(
    client, payload: dict, monkeypatch, audit_root: Path
) -> None:
    monkeypatch.setenv(RISK_MODE_ENV, MODE_OBSERVE)
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))
    assert client.post("/backtests", json=payload).status_code == 200

    card = AuditStore(audit_root).query(action="backtest.run")[0]
    assert card["outcome"]["risk"]["would_reject"] is True
    assert card["outcome"]["reason_code"] == "identity_missing"
    assert any("观察期" in c for c in card["caveats"])


def test_allowed_request_has_no_reason_code(
    client, payload: dict, monkeypatch, audit_root: Path
) -> None:
    """通过的请求不应带 reason_code——否则"有原因码"就不能代表"被拒绝过"。"""
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))
    assert client.post("/backtests", json=payload, headers={"X-Operator": OPERATOR}).status_code == 200

    card = AuditStore(audit_root).query(action="backtest.run")[0]
    assert card["outcome"]["status"] == "ok"
    assert "reason_code" not in card["outcome"]
    assert card["outcome"]["risk"]["reason_code"] is None


# --------------------------------------------------------------------------- #
# 旁路与装配
# --------------------------------------------------------------------------- #
def test_no_bypass_in_real_app() -> None:
    report = bypass_report(create_app())
    assert report["ok"] is True, report
    assert report["uncovered_mutating"] == []
    assert report["dangling_mappings"] == []
    assert report["missing_middleware"] == []
    assert report["n_mutating_routes"] >= 3


def test_audit_middleware_is_outer_than_risk_gate() -> None:
    """审计必须在网关外层，否则被拒绝的请求不会留下工件。"""
    names = [item.cls.__name__ for item in create_app().user_middleware]
    assert names.index("AuditMiddleware") < names.index("RiskGateMiddleware")


def test_invalid_mode_returns_503_not_silent_pass(client, payload: dict, monkeypatch) -> None:
    """非法配置不得放行：整体拒绝并给出原因码，而不是"配错了就当没开"。"""
    monkeypatch.setenv(RISK_MODE_ENV, "semi")
    response = client.post("/backtests", json=payload)
    assert response.status_code == 503
    _assert_reason(response, "gateway_internal_error")


def test_health_is_not_gated(monkeypatch) -> None:
    """健康检查不参与风控：否则网关一配错，探活先挂，反而看不出真实状态。"""
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    with TestClient(app) as c:
        response = c.get("/health")
    assert response.status_code == 200
    assert RISK_HEADER not in response.headers
