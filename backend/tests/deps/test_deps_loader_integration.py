"""加载器接入集成测试：真实调用路径上切换是否留痕、锁定是否生效。

与 ``test_deps_chain.py`` 的区别：这里走的是 **``fetch_daily_a_share`` 真实入口**，
用假 akshare 模块替代网络。这样验证的是"装配对不对"，而不是"链本身对不对"——
两者都要有，因为最常见的故障是链条写对了但没人接上去。

隔离方式：每个用例把 ``DEEPQUANT_DEPS_DIR`` 指到临时目录并清空运行期缓存，
避免用例之间共享"上次成功通道"状态（那会让事件断言依赖执行顺序）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from deepquant.data.loaders import akshare_loader
from deepquant.data.loaders.akshare_loader import (
    AkshareNetworkError,
    AkshareSchemaError,
    fetch_daily_a_share,
)
from deepquant.deps.market_data import reset_runtime

EM_RAW = pd.DataFrame(
    {
        "日期": ["2024-01-03", "2024-01-02"],
        "股票代码": ["000001", "000001"],
        "开盘": ["10.2", "10.0"],
        "收盘": ["10.4", "10.2"],
        "最高": ["10.6", "10.5"],
        "最低": ["10.0", "9.8"],
        "成交量": ["1200", "1000"],
        "成交额": ["12345.0", "10000.0"],
    }
)

TX_RAW = pd.DataFrame(
    {
        "date": ["2024-01-03", "2024-01-02"],
        "open": [10.2, 10.0],
        "close": [10.4, 10.2],
        "high": [10.6, 10.5],
        "low": [10.0, 9.8],
        "volume": [1200, 1000],
        "amount": [1.2345e7, 1.0e7],
    }
)


class FakeAk:
    def __init__(self, em=None, tx=None, em_exc=None, tx_exc=None):
        self._em = EM_RAW if em is None else em
        self._tx = TX_RAW if tx is None else tx
        self._em_exc = em_exc
        self._tx_exc = tx_exc
        self.calls: list[str] = []

    def stock_zh_a_hist(self, **kwargs):
        self.calls.append("eastmoney")
        if self._em_exc is not None:
            raise self._em_exc
        return self._em

    def stock_zh_a_hist_hist_tx(self, **kwargs):  # pragma: no cover - 防御性
        raise AssertionError("不应调用")

    def stock_zh_a_hist_tx(self, **kwargs):
        self.calls.append("tencent")
        if self._tx_exc is not None:
            raise self._tx_exc
        return self._tx


@pytest.fixture(autouse=True)
def isolated_deps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "deps"
    monkeypatch.setenv("DEEPQUANT_DEPS_DIR", str(root))
    monkeypatch.delenv("DEEPQUANT_DEP_LOCK", raising=False)
    reset_runtime()
    yield root
    reset_runtime()


@pytest.fixture
def install_ak(monkeypatch: pytest.MonkeyPatch):
    def _install(fake: FakeAk) -> FakeAk:
        monkeypatch.setattr(akshare_loader, "_require_akshare", lambda: fake)
        return fake

    return _install


def _events(root: Path) -> list[dict]:
    import json

    path = root / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _samples(root: Path) -> list[dict]:
    import json

    files = sorted((root / "latency").glob("latency-*.jsonl"))
    out: list[dict] = []
    for path in files:
        out.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return out


# --------------------------------------------------------------------------- #
# 切换留痕
# --------------------------------------------------------------------------- #
def test_primary_ok_records_latency_but_no_event(install_ak, isolated_deps: Path) -> None:
    install_ak(FakeAk())
    _, cfg = fetch_daily_a_share("000001", "20240101", "20240301")

    assert cfg.used_source == "eastmoney"
    assert _events(isolated_deps) == [], "没发生切换就不该有事件"
    samples = _samples(isolated_deps)
    assert len(samples) == 1 and samples[0]["channel"] == "eastmoney"


def test_fallback_records_switch_event_with_attempts(install_ak, isolated_deps: Path) -> None:
    install_ak(FakeAk(em_exc=ConnectionError("Unable to connect to proxy")))
    df, cfg = fetch_daily_a_share("000001", "20240101", "20240301")

    assert cfg.used_source == "tencent"
    assert len(df) == 2, "业务无感知中断：调用方照常拿到数据"

    events = _events(isolated_deps)
    assert len(events) == 1
    event = events[0]
    assert (event["src"], event["dst"]) == ("eastmoney", "tencent")
    assert event["direction"] == "failover"
    assert [a["channel"] for a in event["attempts"]] == ["eastmoney", "tencent"]
    assert event["attempts"][0]["ok"] is False
    error_text = (event["attempts"][0]["error"] or "").lower()
    assert "proxy" in error_text or "connectionerror" in error_text


def test_recovery_records_recover_event(install_ak, isolated_deps: Path) -> None:
    install_ak(FakeAk(em_exc=ConnectionError("blocked")))
    fetch_daily_a_share("000001", "20240101", "20240301")

    install_ak(FakeAk())  # 主源恢复
    _, cfg = fetch_daily_a_share("000001", "20240101", "20240301")

    assert cfg.used_source == "eastmoney"
    directions = [e["direction"] for e in _events(isolated_deps)]
    assert directions == ["failover", "recover"]


def test_failed_call_latency_is_recorded(install_ak, isolated_deps: Path) -> None:
    """失败调用的耗时也要记：只记成功会系统性低估延迟。"""
    install_ak(FakeAk(em_exc=TimeoutError("timeout")))
    fetch_daily_a_share("000001", "20240101", "20240301")

    by_channel = {s["channel"]: s for s in _samples(isolated_deps)}
    assert by_channel["eastmoney"]["ok"] is False
    assert by_channel["tencent"]["ok"] is True


# --------------------------------------------------------------------------- #
# 锁定
# --------------------------------------------------------------------------- #
def test_env_lock_pins_channel(install_ak, isolated_deps: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """人工锁定：只用指定通道，即使主源可用也不去试它。"""
    monkeypatch.setenv("DEEPQUANT_DEP_LOCK", "market_data=tencent")
    reset_runtime()
    fake = install_ak(FakeAk())

    _, cfg = fetch_daily_a_share("000001", "20240101", "20240301")
    assert cfg.used_source == "tencent"
    assert fake.calls == ["tencent"]
    assert _events(isolated_deps) == [], "锁定时不发生切换，不应产出切换事件"


def test_explicit_source_is_pinned(install_ak, isolated_deps: Path) -> None:
    fake = install_ak(FakeAk(em_exc=ConnectionError("blocked")))
    with pytest.raises(AkshareNetworkError):
        fetch_daily_a_share("000001", "20240101", "20240301", source="eastmoney")
    assert fake.calls == ["eastmoney"], "显式指定源时不得偷偷换源"


# --------------------------------------------------------------------------- #
# 失败语义
# --------------------------------------------------------------------------- #
def test_all_fail_reports_every_channel(install_ak, isolated_deps: Path) -> None:
    install_ak(
        FakeAk(
            em_exc=ConnectionError("Unable to connect to proxy"),
            tx_exc=TimeoutError("tencent timeout"),
        )
    )
    with pytest.raises(AkshareNetworkError) as excinfo:
        fetch_daily_a_share("000001", "20240101", "20240301")

    message = str(excinfo.value)
    assert "eastmoney" in message and "tencent" in message
    assert _events(isolated_deps) == [], "全部失败不是切换，不应伪造切换事件"


def test_schema_error_does_not_fallback(install_ak, isolated_deps: Path) -> None:
    """字段变更属于需人工确认的问题：不换源，也不留切换事件。"""
    broken = EM_RAW.rename(columns={"开盘": "开盘价"})
    fake = install_ak(FakeAk(em=broken))

    with pytest.raises(AkshareSchemaError):
        fetch_daily_a_share("000001", "20240101", "20240301")

    assert fake.calls == ["eastmoney"]
    assert _events(isolated_deps) == []
