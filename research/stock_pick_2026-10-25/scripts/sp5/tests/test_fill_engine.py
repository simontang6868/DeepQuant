"""SP-5 撮合引擎测试：验证「判据本身」，不验证数字。

覆盖四件容易写错的事：
1. 限价档分板块、分制度期（创业板 2020-08-24 前后不同）；
2. **买卖方向不对称**：一字涨停买不进但卖得出，一字跌停卖不出但买得进；
3. 停牌（volume 0 / 无记录）双向都不成交；
4. 顺延搜索只跳到「该方向可成交」的第一天。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fill_engine as fe  # noqa: E402


# ---------------------------------------------------------------- 限价档
@pytest.mark.parametrize(
    "code,date,is_st,sh_mode,expect",
    [
        ("600519", "20150105", False, "base", 0.10),
        ("000001", "20150105", False, "base", 0.10),
        ("000001", "20150105", True, "base", 0.05),      # 深市主板 ST
        ("300001", "20200821", False, "base", 0.10),     # 创业板注册制前
        ("300001", "20200824", False, "base", 0.20),     # 创业板注册制后
        ("300001", "20200824", True, "base", 0.20),      # 创业板 ST 仍 20%
        ("688981", "20210105", False, "base", 0.20),     # 科创板
        ("830799", "20210105", False, "base", 0.30),     # 北交所
        ("600001", "20150105", True, "base", 0.10),      # 沪市 ST 未知 → base 当 10%
        ("600001", "20150105", True, "sh5", 0.05),       # 沪市 ST 双界上界
    ],
)
def test_limit_pct(code, date, is_st, sh_mode, expect):
    assert fe.limit_pct(code, date, is_st, sh_mode) == pytest.approx(expect)


# ---------------------------------------------------------------- 盘面构造
DATES = ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109"]
EXEC = [
    # date, open, high, low, close, volume
    ("20240102", 10.0, 10.0, 10.0, 10.00, 1000),
    ("20240103", 11.0, 11.0, 11.0, 11.00, 500),      # 一字涨停 +10%
    ("20240104", 9.90, 9.90, 9.90, 9.90, 400),       # 一字跌停 −10%
    ("20240105", 10.2, 10.5, 9.50, 10.20, 900),      # 正常
    ("20240108", 10.2, 10.2, 10.2, 10.20, 0),        # 停牌（volume 0）
    ("20240109", 10.3, 10.6, 10.1, 10.40, 800),      # 正常
]


@pytest.fixture()
def bar(tmp_path: Path) -> fe.Bars:
    ex = tmp_path / "000001.SZ.exec.csv"
    q = tmp_path / "000001.SZ.qfq.csv"
    pd.DataFrame(
        [{"date": d, "open": o, "high": h, "low": lo, "close": c, "volume": v}
         for d, o, h, lo, c, v in EXEC]
    ).to_csv(ex, index=False)
    pd.DataFrame({"date": [r[0] for r in EXEC], "close": [r[4] for r in EXEC]}).to_csv(
        q, index=False)
    return fe.Bars("000001", str(ex), str(q))


def test_one_way_limit_up_asymmetric(bar):
    assert fe.judge(bar, "20240103", False, "buy")[0] == "FAIL_LIMIT_UP"
    assert fe.judge(bar, "20240103", False, "sell")[0] == "FILLED"   # 涨停能卖


def test_one_way_limit_down_asymmetric(bar):
    assert fe.judge(bar, "20240104", False, "sell")[0] == "FAIL_LIMIT_DOWN"
    assert fe.judge(bar, "20240104", False, "buy")[0] == "FILLED"    # 跌停能买


def test_halt_blocks_both_sides(bar):
    assert fe.judge(bar, "20240108", False, "buy")[0] == "HALT"
    assert fe.judge(bar, "20240108", False, "sell")[0] == "HALT"


def test_no_bar_blocks_both_sides(bar):
    assert fe.judge(bar, "20300101", False, "buy")[0] == "NO_BAR"


def test_normal_day_fills(bar):
    assert fe.judge(bar, "20240105", False, "buy")[0] == "FILLED"


def test_deferred_skips_to_next_tradable(bar):
    cal = DATES
    pos = {d: i for i, d in enumerate(cal)}
    # 买：20240103 一字涨停 → 顺延到 20240104（跌停可买）
    d, tag = fe.deferred(bar, "20240103", cal, pos, False, "buy")
    assert (d, tag) == ("20240104", "DEFERRED")
    # 卖：20240104 一字跌停 → 顺延到 20240105
    d2, _ = fe.deferred(bar, "20240104", cal, pos, False, "sell")
    assert d2 == "20240105"


def test_deferred_gives_up_after_window(bar):
    cal = ["20240103", "20240104"]
    pos = {d: i for i, d in enumerate(cal)}
    d, tag = fe.deferred(bar, "20240103", cal, pos, False, "buy")  # 窗口内只剩 20240104
    assert d == "20240104"
    # 窗口被压成 0 天 → 必须返回 None，不得无限找下去
    d2, tag2 = fe.deferred(bar, "20240108", ["20240108"], {"20240108": 0}, False, "buy")
    assert d2 is None and tag2 == "NO_DEFER"


# ---------------------------------------------------------------- 空槽永久化（真 bug 回归）
def test_empty_slot_is_refilled_next_month():
    """买入失败留下现金槽后，下个月必须**重新下单**。

    历史 bug：买单按 `zip(sells, buys)` 配对，空槽会让 buys 比 sells 多，
    多出的买单被静默丢弃 → 空槽永久化 → 组合逐步退化成现金（实测曾出现 65% 现金、
    年化被凭空压低 4pp）。此测试钉住修复。
    """
    import numpy as np
    import p02c_st_effect as p

    cal = ["20240131", "20240201", "20240229", "20240301"]
    cal_pos = {d: i for i, d in enumerate(cal)}
    months = ["20240131", "20240229"]
    buy = ["20240201", "20240301"]
    nxt = ["20240301", "20240301"]

    dates = np.array(["20240201", "20240301"])
    # A 在建仓日 1 不可买、建仓日 2 可买；B~E 一直可买
    tabs = {"A": (dates, np.array([False, True]), np.array([True, True]))}
    px = {"A": np.array([10, 10, 10, 12], float)}
    for k in "BCDE":
        tabs[k] = (dates, np.array([True, True]), np.array([True, True]))
        px[k] = np.array([10, 10, 10, 10], float)

    holds = [["A", "B", "C", "D", "E"], ["A", "B", "C", "D", "E"]]
    _, cash, _ = p.nav_tier([holds], months, buy, nxt, cal_pos, px, tabs, "strict")
    # 第 1 个月 1/5 现金，第 2 个月应全部买上 → 均值 0.1；若空槽永久化则为 0.2
    assert abs(cash - 0.1) < 1e-9, f"空槽未被补买，cash={cash}"
