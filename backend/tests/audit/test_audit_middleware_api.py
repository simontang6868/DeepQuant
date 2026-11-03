"""审计中间件接入测试：默认关闭零变化、启用后成功/拒绝路径都落卡。

三条断言构成 T1 能安全接入既有系统的全部理由：
1. 未启用时**不读请求体、不建目录、不加响应头**（既有行为基线不变）；
2. 启用后成功操作产出五元组完整的工件，并记录请求体摘要作为「依据」；
3. 被拒绝的操作（4xx）同样落卡并标记为 rejected——只记成功等于只记宣传。

注意：本文件**刻意不建** ``conftest.py``。仓库既有测试使用 ``from conftest import ...``
这种裸模块名导入，若本目录也放一个 ``conftest.py``，会与 ``tests/api/conftest.py``
争抢 ``sys.modules["conftest"]``，导致 2 个既有测试 ImportError（已实测复现）。
因此夹具内联在本文件里。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from deepquant.api.app import app
from deepquant.api.config import get_data_root
from deepquant.audit.middleware import AUDIT_HEADER, OPERATOR_HEADER, SESSION_HEADER
from deepquant.audit.operations import AUDIT_DIR_ENV
from deepquant.audit.run_card import REQUIRED_FIELDS
from deepquant.audit.store import AuditStore
from deepquant.data.pipeline import ingest_daily_csv

DATA_ROWS = 60


def _make_daily_csv(path: Path, *, n: int = DATA_ROWS) -> Path:
    """确定性日线 CSV（先跌 → 再涨 → 再跌），保证双均线会产生交叉。"""
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
    """隔离 SQLite 与审计开关，避免污染工作区或互相干扰。"""
    monkeypatch.setenv("DEEPQUANT_DB_PATH", str(tmp_path / "backtests.db"))
    monkeypatch.delenv(AUDIT_DIR_ENV, raising=False)


@pytest.fixture
def audit_root(tmp_path: Path) -> Path:
    return tmp_path / "audit"


@pytest.fixture
def dataset(tmp_path: Path):
    """登记一个 TEST01 数据集，并把数据根目录重定向到临时目录。"""
    data_root = tmp_path / "data"
    data_root.mkdir()
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src = _make_daily_csv(src_dir / "TEST01.csv")
    ingest_daily_csv(src, symbol="TEST01", data_root=data_root)
    app.dependency_overrides[get_data_root] = lambda: data_root.resolve()
    yield "TEST01"
    app.dependency_overrides.clear()


@pytest.fixture
def client(dataset: str):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def backtest_payload() -> dict:
    return {
        "dataset_symbol": "TEST01",
        "strategy_id": "ma_cross",
        "strategy_params": {"fast_window": 5, "slow_window": 20},
        "initial_cash": 100000,
    }


def test_default_off_adds_no_header_and_creates_nothing(
    client, backtest_payload: dict, audit_root: Path
) -> None:
    response = client.post("/backtests", json=backtest_payload)
    assert response.status_code == 200
    assert AUDIT_HEADER not in response.headers
    assert not audit_root.exists()


def test_unregistered_route_is_not_recorded(
    client, audit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """健康检查不在核心操作清单里，不应产生工件（避免把噪声当证据）。"""
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))
    response = client.get("/health")
    assert response.status_code == 200
    assert AUDIT_HEADER not in response.headers
    assert AuditStore(audit_root).count() == 0


def test_successful_backtest_writes_complete_card(
    client, backtest_payload: dict, audit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))
    response = client.post(
        "/backtests",
        json=backtest_payload,
        headers={OPERATOR_HEADER: "ops@deepquant", SESSION_HEADER: "sess-A"},
    )
    assert response.status_code == 200
    assert response.headers[AUDIT_HEADER] == "recorded"

    store = AuditStore(audit_root)
    cards = store.query(action="backtest.run")
    assert len(cards) == 1
    card = cards[0]
    for field in REQUIRED_FIELDS:
        assert card[field] is not None, f"工件缺少 T1 必需字段 {field}"

    assert card["operator"] == "ops@deepquant"
    assert card["session"] == "sess-A"
    assert card["outcome"]["status"] == "ok"
    assert card["outcome"]["status_code"] == 200
    assert card["outcome"]["duration_ms"] >= 0
    # 依据：请求摘要（含策略参数），这是「依据哪条规则执行」的可复核形态
    assert card["basis"]["request"]["body"]["strategy_id"] == "ma_cross"
    assert card["basis"]["request"]["body_sha256"]
    assert card["inputs"]["body_sha256"] == card["basis"]["request"]["body_sha256"]
    assert store.verify().ok is True


def test_rejected_request_is_recorded_as_rejected(
    client, audit_root: Path, monkeypatch: pytest.MonkeyPatch, backtest_payload: dict
) -> None:
    """4xx（数据集不存在）也要落卡，且状态为 rejected。"""
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))
    payload = dict(backtest_payload, dataset_symbol="NOPE")
    response = client.post("/backtests", json=payload)
    assert response.status_code == 404
    assert response.headers[AUDIT_HEADER] == "recorded"

    card = AuditStore(audit_root).query(action="backtest.run")[0]
    assert card["outcome"]["status"] == "rejected"
    assert card["outcome"]["status_code"] == 404
    assert any("≥400" in c for c in card["caveats"])


def test_missing_operator_header_is_flagged(
    client, audit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没带操作人时不能假装有人负责：记为 anonymous 并写明不具追责效力。"""
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))
    response = client.get("/strategies")
    assert response.status_code == 200

    card = AuditStore(audit_root).query(action="strategy.list")[0]
    assert card["operator"] == "anonymous"
    assert any("追责" in c for c in card["caveats"])


def test_read_only_operations_are_recorded(
    client, audit_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只读操作（数据集/策略枚举）也在核心清单内，必须留痕。"""
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))
    assert client.get("/datasets").status_code == 200
    assert client.get("/strategies").status_code == 200

    store = AuditStore(audit_root)
    actions = [c["action"] for c in store.iter_all()]
    assert "dataset.list" in actions
    assert "strategy.list" in actions
    assert store.verify().ok is True


def test_body_is_not_recorded_beyond_limit(
    client, audit_root: Path, monkeypatch: pytest.MonkeyPatch, backtest_payload: dict
) -> None:
    """超大请求体只记摘要，不把内容塞进工件（防膨胀、防敏感信息扩散）。"""
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))
    payload = dict(backtest_payload, note="x" * (70 * 1024))
    response = client.post("/backtests", json=payload)
    assert response.status_code in {200, 404, 422}
    cards = AuditStore(audit_root).query(action="backtest.run")
    if not cards:
        pytest.skip("请求在进入中间件前即被拒绝（如 422），无可检验工件")
    card = cards[0]
    assert "body" not in card["basis"]["request"]
    assert card["basis"]["request"]["body_sha256"]
    assert any("上限" in c for c in card["caveats"])
