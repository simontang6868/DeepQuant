"""API 测试公共夹具：临时数据根目录 + 确定性日线数据。

原则：
- 全部使用临时目录，不依赖用户真实行情文件，不访问网络；
- 数据集通过既有 ingest_daily_csv 管线生成，元数据与数据文件均为真实产物；
- 价格序列先跌后涨再跌，保证双均线策略会产生金叉与死叉（便于检验成交与绩效）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from deepquant.api.app import app
from deepquant.api.config import get_data_root
from deepquant.data.pipeline import IngestResult, ingest_daily_csv

DATA_ROWS = 60


def make_daily_csv(path: Path, *, n: int = DATA_ROWS) -> Path:
    """生成确定性日线 CSV（15 天下跌 → 30 天上涨 → 15 天下跌）。"""
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


def ingest_dataset(data_root: Path, tmp_path: Path, symbol: str, *, n: int = DATA_ROWS) -> IngestResult:
    """通过正规导入管线登记一个数据集（源 CSV 放在数据根目录之外）。"""
    src_dir = tmp_path / f"src_{symbol}"
    src_dir.mkdir(exist_ok=True)
    src = make_daily_csv(src_dir / f"{symbol}.csv", n=n)
    return ingest_daily_csv(src, symbol=symbol, data_root=data_root)


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """已 resolve 的临时数据根目录（存在但为空）。"""
    root = tmp_path / "data"
    root.mkdir()
    return root.resolve()


@pytest.fixture
def make_client():
    """按给定数据根目录构造 TestClient（测试结束自动清理依赖覆盖）。"""
    clients: list[TestClient] = []

    def _make(root: Path) -> TestClient:
        app.dependency_overrides[get_data_root] = lambda: root
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    app.dependency_overrides.clear()
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client, data_root) -> TestClient:
    """指向空数据根目录的客户端。"""
    return make_client(data_root)


@pytest.fixture
def client_with_dataset(client, data_root, tmp_path) -> TestClient:
    """指向已登记 TEST01 数据集的客户端。"""
    ingest_dataset(data_root, tmp_path, "TEST01")
    return client


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将持久化数据库重定向到临时文件，避免污染工作区或互相干扰。"""
    db = tmp_path / "backtests.db"
    monkeypatch.setenv("DEEPQUANT_DB_PATH", str(db))
    return db


@pytest.fixture
def valid_backtest_payload() -> dict:
    """一份合法的回测请求体。"""
    return {
        "dataset_symbol": "TEST01",
        "strategy_id": "ma_cross",
        "strategy_params": {"fast_window": 5, "slow_window": 20},
        "initial_cash": 100000,
    }
