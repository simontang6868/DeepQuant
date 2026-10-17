"""akshare 适配器测试：双源 fallback、字段映射、失败行为。

设计约束：
- **全程 monkeypatch 注入假 akshare，不依赖网络。** 真实网络可达性受出口环境影响
  （实测：东财源 ``push2his.eastmoney.com`` 被云端 proxy 拦截，腾讯源可用），
  把网络塞进单测只会得到一条随机抽风的测试，既不稳定也掩盖真正的问题。
- 只测适配器自身的逻辑：源选择、字段映射、参数校验、失败传播、溯源信息。
"""

import sys

import pandas as pd
import pytest

from deepquant.data.loaders import akshare_loader
from deepquant.data.loaders.akshare_loader import (
    AkshareAdapterError,
    AkshareNetworkError,
    AkshareNotInstalledError,
    AkshareSchemaError,
    AdjustStatus,
    FetchConfig,
    _to_tencent_symbol,
    fetch_daily_a_share,
)

# 东财源原始形态：中文列名、乱序、数值是字符串
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

# 腾讯源原始形态：英文列名、乱序，且带东财没有的 turnover 列
TX_RAW = pd.DataFrame(
    {
        "date": ["2024-01-03", "2024-01-02"],
        "open": [10.2, 10.0],
        "close": [10.4, 10.2],
        "high": [10.6, 10.5],
        "low": [10.0, 9.8],
        "volume": [1200, 1000],
        "turnover": [0.0012, 0.0010],
        "amount": [1.2345e7, 1.0e7],
    }
)

EM_EMPTY = EM_RAW.iloc[0:0]
TX_EMPTY = TX_RAW.iloc[0:0]


class FakeAk:
    """假 akshare 模块。

    按需让某个源抛异常，并如实记录每次调用收到的参数，便于断言
    「腾讯源确实拿到了带市场前缀的 symbol」这类细节。
    """

    def __init__(self, em=EM_RAW, tx=TX_RAW, em_exc=None, tx_exc=None):
        self._em = em
        self._tx = tx
        self._em_exc = em_exc
        self._tx_exc = tx_exc
        self.calls: list[tuple[str, dict]] = []

    def stock_zh_a_hist(self, **kwargs):
        self.calls.append(("eastmoney", kwargs))
        if self._em_exc is not None:
            raise self._em_exc
        return self._em

    def stock_zh_a_hist_tx(self, **kwargs):
        self.calls.append(("tencent", kwargs))
        if self._tx_exc is not None:
            raise self._tx_exc
        return self._tx


@pytest.fixture
def install_ak(monkeypatch):
    """把假 akshare 模块注入适配器（替换掉延迟导入点）。"""

    def _install(fake):
        monkeypatch.setattr(akshare_loader, "_require_akshare", lambda: fake)
        return fake

    return _install


# --------------------------------------------------------------------------
# 单源：字段映射与规范化
# --------------------------------------------------------------------------


def test_eastmoney_source_maps_chinese_columns(install_ak):
    install_ak(FakeAk())
    df, cfg = fetch_daily_a_share(
        "000001", "20240101", "20240301", source="eastmoney"
    )
    assert list(df.columns) == [
        "date", "source_symbol", "open", "close", "high", "low", "volume", "amount",
    ]
    assert df["date"].is_monotonic_increasing
    assert df["close"].tolist() == [10.2, 10.4]
    assert df["volume"].tolist() == [1000, 1200]
    assert cfg.used_source == "eastmoney"


def test_tencent_source_maps_english_columns(install_ak):
    install_ak(FakeAk())
    df, cfg = fetch_daily_a_share("000001", "20240101", "20240301", source="tencent")
    # 英文列名已是统一命名；turnover 不属于统一字段，保留但不参与必需列
    for col in ("date", "open", "close", "high", "low", "volume", "amount", "source_symbol"):
        assert col in df.columns
    assert df["date"].is_monotonic_increasing
    assert df["close"].tolist() == [10.2, 10.4]
    assert df["source_symbol"].tolist() == ["000001", "000001"]
    assert cfg.used_source == "tencent"


def test_tencent_source_receives_market_prefixed_symbol(install_ak):
    fake = install_ak(FakeAk())
    fetch_daily_a_share("600519", "20240101", "20240301", source="tencent")
    sources = dict(fake.calls)
    assert sources["tencent"]["symbol"] == "sh600519"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("000001", "sz000001"),
        ("300750", "sz300750"),
        ("600519", "sh600519"),
        ("688981", "sh688981"),
        ("430047", "bj430047"),
        ("830799", "bj830799"),
    ],
)
def test_tencent_symbol_prefix_mapping(code, expected):
    assert _to_tencent_symbol(code) == expected


def test_tencent_symbol_unknown_prefix_raises():
    # 推断不出市场就明确报错，绝不猜一个市场去拉错数据
    with pytest.raises(AkshareAdapterError):
        _to_tencent_symbol("990001")


# --------------------------------------------------------------------------
# 双源 fallback 行为
# --------------------------------------------------------------------------


def test_auto_uses_eastmoney_when_available(install_ak):
    fake = install_ak(FakeAk())
    _, cfg = fetch_daily_a_share("000001", "20240101", "20240301")
    assert cfg.used_source == "eastmoney"
    # 东财成功时不应该再去打扰腾讯源
    assert [name for name, _ in fake.calls] == ["eastmoney"]


def test_auto_falls_back_to_tencent_when_eastmoney_blocked(install_ak):
    # 复现本机真实故障：东财源被出口 proxy 拦截
    blocked = FakeAk(em_exc=ConnectionError("Unable to connect to proxy"))
    fake = install_ak(blocked)
    df, cfg = fetch_daily_a_share("000001", "20240101", "20240301")

    assert cfg.used_source == "tencent"
    assert len(df) == 2
    assert [name for name, _ in fake.calls] == ["eastmoney", "tencent"]


def test_all_sources_fail_raises_network_error(install_ak):
    fake = install_ak(
        FakeAk(
            em_exc=ConnectionError("Unable to connect to proxy"),
            tx_exc=TimeoutError("tencent timeout"),
        )
    )
    with pytest.raises(AkshareNetworkError) as excinfo:
        fetch_daily_a_share("000001", "20240101", "20240301")
    msg = str(excinfo.value)
    # 两个源的失败原因都要留下（不删失败记录，便于后续诊断）
    assert "eastmoney" in msg and "tencent" in msg
    assert len(fake.calls) == 2


def test_explicit_source_does_not_fallback(install_ak):
    # 显式指定东财源时被拦，就该直接失败，不偷偷换源
    install_ak(FakeAk(em_exc=ConnectionError("Unable to connect to proxy")))
    with pytest.raises(AkshareNetworkError):
        fetch_daily_a_share("000001", "20240101", "20240301", source="eastmoney")


def test_schema_error_propagates_without_fallback(install_ak):
    # 上游字段变化属于需要人工确认的事，不做 fallback，直接抛
    broken = EM_RAW.rename(columns={"开盘": "开盘价"})
    install_ak(FakeAk(em=broken))
    with pytest.raises(AkshareSchemaError) as excinfo:
        fetch_daily_a_share("000001", "20240101", "20240301", source="eastmoney")
    assert "开盘" in str(excinfo.value)


def test_tencent_schema_error_propagates(install_ak):
    broken = TX_RAW.rename(columns={"close": "closing_price"})
    install_ak(FakeAk(tx=broken))
    with pytest.raises(AkshareSchemaError):
        fetch_daily_a_share("000001", "20240101", "20240301", source="tencent")


# --------------------------------------------------------------------------
# 空结果 / 参数校验 / 依赖检查
# --------------------------------------------------------------------------


def test_empty_result_returns_empty_frame_and_records_source(install_ak):
    # 空结果不算错误（区间内确实无数据），但必须带上正确的列和来源记录
    install_ak(FakeAk(em=EM_EMPTY))
    df, cfg = fetch_daily_a_share("000001", "20240101", "20240301", source="eastmoney")
    assert len(df) == 0
    for col in ("date", "open", "high", "low", "close", "volume", "amount", "source_symbol"):
        assert col in df.columns
    assert cfg.used_source == "eastmoney"


def test_none_result_returns_empty_frame(install_ak):
    install_ak(FakeAk(tx=None))
    df, _ = fetch_daily_a_share("000001", "20240101", "20240301", source="tencent")
    assert len(df) == 0


@pytest.mark.parametrize("bad", ["2024-01-01", "202401", "202401011", "abcdefgh"])
def test_invalid_date_format_raises(install_ak, bad):
    install_ak(FakeAk())
    with pytest.raises(AkshareAdapterError):
        fetch_daily_a_share("000001", bad, "20240301", source="tencent")


@pytest.mark.parametrize("bad", ["00001", "0000011", "A00001", ""])
def test_invalid_symbol_code_raises(install_ak, bad):
    install_ak(FakeAk())
    with pytest.raises(AkshareAdapterError):
        fetch_daily_a_share(bad, "20240101", "20240301", source="tencent")


def test_invalid_adjust_raises(install_ak):
    install_ak(FakeAk())
    with pytest.raises(AkshareAdapterError):
        fetch_daily_a_share("000001", "20240101", "20240301", adjust="weekly")


def test_invalid_source_raises(install_ak):
    install_ak(FakeAk())
    with pytest.raises(AkshareAdapterError):
        fetch_daily_a_share("000001", "20240101", "20240301", source="sina")


def test_akshare_not_installed_raises(monkeypatch, install_ak):
    # sys.modules 里置 None 会让 import akshare 抛 ImportError
    monkeypatch.setitem(sys.modules, "akshare", None)
    with pytest.raises(AkshareNotInstalledError):
        fetch_daily_a_share("000001", "20240101", "20240301")


# --------------------------------------------------------------------------
# 溯源信息（红线 R3：真实数据必须可追溯来源）
# --------------------------------------------------------------------------


def test_metadata_records_actual_source(install_ak):
    install_ak(FakeAk(em_exc=ConnectionError("blocked")))
    _, cfg = fetch_daily_a_share("000001", "20240101", "20240301")
    meta = cfg.to_metadata()

    assert meta["used_source"] == "tencent"
    assert meta["source"] == "auto"
    assert meta["adapter"] == "akshare.stock_zh_a_hist_tx"
    assert meta["symbol_code"] == "000001"
    assert meta["adjust_status"] == "qfq"


def test_metadata_records_eastmoney_adapter(install_ak):
    install_ak(FakeAk())
    _, cfg = fetch_daily_a_share("000001", "20240101", "20240301")
    assert cfg.to_metadata()["adapter"] == "akshare.stock_zh_a_hist"


def test_metadata_of_unused_config_marks_no_source():
    # 还没成功取到数据时，used_source 为空且不假装来自某个源
    cfg = FetchConfig("000001", "20240101", "20240301", AdjustStatus.QFQ)
    assert cfg.used_source == ""
    assert cfg.to_metadata()["adapter"] == "akshare.<none>"
