"""真实网络链路冒烟测试（默认跳过，需 DEEPQUANT_LIVE 环境变量开启）。

设计约束（与 test_akshare_loader.py 的 32 条 mock 测试互补）：
- 那 32 条全程 monkeypatch 注入假 akshare，只测**适配器逻辑**，不碰真实网络；
  本文件只补「真实链路到底通不通」这一环，且**默认不跑**——
  外网可达性受出口环境影响（实测：东财源被 proxy 拦截、腾讯源可用），
  塞进普通 CI 只会得到一条随机抽风的测试，既不稳定也掩盖真问题。
- 用环境变量 gate：只有显式 `DEEPQUANT_LIVE=1 pytest -m network` 才真正执行。
- 真实行情来自 akshare 公开接口（公开真实数据），`fetch_daily_a_share` 已在
  `FetchConfig.used_source` 记录实际生效的数据源，保证可溯源（红线 R3）。
- 本文件不把真实网络测试的通过当作 CI 门槛；不改任何既有 mock 测试（R7）。
"""

import os

import pandas as pd
import pytest

from deepquant.data.loaders.akshare_loader import fetch_daily_a_share


@pytest.mark.network
@pytest.mark.skipif(
    not os.environ.get("DEEPQUANT_LIVE"),
    reason="未设 DEEPQUANT_LIVE，跳过真实网络",
)
def test_live_fetch_000001_columns_dates_and_source():
    """真实拉取 000001（平安银行）一段日线，校验结构与溯源信息。"""
    df, cfg = fetch_daily_a_share("000001", "20240101", "20240301")

    assert isinstance(df, pd.DataFrame)
    assert not df.empty, "真实链路应返回非空 DataFrame"

    # 标准化后的列（来自 csv_loader.REQUIRED_COLUMNS 约定）
    for col in ["date", "open", "high", "low", "close", "volume"]:
        assert col in df.columns, f"缺少列 {col}"

    # date 单调递增（交易日升序）
    dates = pd.to_datetime(df["date"])
    assert dates.is_monotonic_increasing, "date 应单调递增"

    # close 全为正（真实有效行情不应出现非正收盘价）
    assert (df["close"] > 0).all(), "close 应全为正"

    # 溯源信息：必须记录实际生效的数据源（东财 / 腾讯），不能静默
    assert cfg.used_source in ("eastmoney", "tencent"), (
        f"used_source 应记录真实源，收到 {cfg.used_source!r}"
    )
