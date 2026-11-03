# -*- coding: utf-8 -*-
"""MA-CROSS 独立复核 / §九：11 项关键行为测试。

原则：**证明行为正确，而不只是"程序能跑"**。
部分测试刻意检查"不支持"的事实（如连续账户），失败即缺陷证据，不得为变绿而改被测代码。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os

import numpy as np
import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import round_money
from deepquant.backtest.engine import run_backtest
from deepquant.backtest.models import OrderStatus
from deepquant.market.ashare import BLOCK_LIMIT_UP
from deepquant.market.rules import Side, a_share_rules
from deepquant.performance.benchmarks import buy_and_hold
from deepquant.strategies.base import Signal, Strategy
from deepquant.strategies.ma_cross import MaCrossStrategy
from deepquant.walkforward.parameter_selection import GridSearchConfig, run_walkforward_v2

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
OUTSIDE = r"D:/DeepQuant power by txm"
AUDIT = os.path.join(ROOT, "ma_cross_audit")
REPORTS = os.path.join(ROOT, "reports")
B50_DIST = os.path.join(OUTSIDE, "b50_dist/step75_research_summary.json")

DATES = pd.to_datetime([
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
    "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11",
])


class FixedStrategy(Strategy):
    """按预置映射给信号；未列出的日期 HOLD。"""
    name = "fixed_test"
    version = "0.0.0"

    def __init__(self, signal_map: dict):
        self._map = {pd.Timestamp(k): int(v) for k, v in signal_map.items()}

    @property
    def params(self) -> dict:
        return {"signals": {str(k): int(v) for k, v in self._map.items()}}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        s = pd.Series(int(Signal.HOLD), index=data.index, dtype="int64")
        for ts, v in self._map.items():
            if ts in s.index:
                s.loc[ts] = v
        return s


def make_df(closes, opens=None, volume=1_000_000.0):
    closes = list(map(float, closes))
    opens = list(map(float, opens)) if opens is not None else closes
    n = len(closes)
    return pd.DataFrame({
        "open": opens, "high": [max(o, c) for o, c in zip(opens, closes)],
        "low": [min(o, c) for o, c in zip(opens, closes)], "close": closes,
        "volume": [volume] * n,
    }, index=DATES[:n])


def cfg(**kw):
    base = dict(symbol="600000.SH", initial_cash=100000.0)
    base.update(kw)
    return BacktestConfig(**base)


# ---------------------------------------------------------------- 1 可重复
def test_01_same_input_same_output():
    df = make_df([10, 10.2, 10.4, 10.6, 10.8, 11.0])
    strat = FixedStrategy({DATES[0]: Signal.BUY})
    r1 = run_backtest(df, strat, cfg())
    r2 = run_backtest(df, strat, cfg())
    assert r1.performance.final_equity == r2.performance.final_equity
    assert len(r1.fills) == len(r2.fills)
    assert [f.price for f in r1.fills] == [f.price for f in r2.fills]


# ---------------------------------------------------------------- 2 窗口边界
def test_02_window_boundaries_are_correct():
    n = 420
    idx = pd.bdate_range("2020-01-01", periods=n)
    px = np.linspace(10.0, 20.0, n)
    df = pd.DataFrame({"open": px, "high": px * 1.01, "low": px * 0.99,
                       "close": px, "volume": 1e6}, index=idx)
    grid = GridSearchConfig(fast_candidates=(3, 5), slow_candidates=(10, 20),
                            min_train_periods=150, min_oos_periods=75,
                            min_trades=1, max_combinations=10)
    _, wfs, _ = run_walkforward_v2(
        df, cfg(), strategy_factory=lambda p: MaCrossStrategy(int(p["fast_window"]),
                                                             int(p["slow_window"])),
        grid_config=grid)
    assert wfs, "未产生任何窗口"
    for w in wfs:
        assert w.train_periods == 150
        assert w.oos_periods == 75
        assert w.train_start < w.train_end < w.oos_start <= w.oos_end
        assert (w.oos_start - w.train_end).days >= 1
    steps = {(wfs[i + 1].oos_start - wfs[i].oos_start).days for i in range(len(wfs) - 1)}
    assert steps, "窗口不足两个，无法校验步长"
    assert all(60 <= s <= 110 for s in steps), f"步长异常: {steps}"


# ---------------------------------------------------------------- 3 无未来函数
def test_03_no_lookahead_in_signals():
    n = 200
    rng = np.random.default_rng(7)
    px = 10 * np.cumprod(1 + rng.normal(0, 0.02, n))
    idx = pd.bdate_range("2021-01-04", periods=n)
    full = pd.DataFrame({"open": px, "high": px * 1.01, "low": px * 0.99,
                         "close": px, "volume": 1e6}, index=idx)
    cut = 150
    trunc = full.iloc[:cut].copy()
    s_full = MaCrossStrategy(5, 20).generate_signals(full)
    s_trunc = MaCrossStrategy(5, 20).generate_signals(trunc)
    assert (s_full.iloc[:cut].to_numpy() == s_trunc.to_numpy()).all(), \
        "截断未来数据改变了历史信号 → 存在未来函数"


# ---------------------------------------------------------------- 4 涨停不可成交
def test_04_limit_up_order_not_filled():
    # 第 2 天开在 +10% 且全天一价 → 一字涨停，买不进
    df = make_df([10.0, 11.0, 11.2, 11.4, 11.6, 11.8],
                 opens=[10.0, 11.0, 11.0, 11.2, 11.4, 11.6])
    df.loc[df.index[1], "high"] = 11.0
    df.loc[df.index[1], "low"] = 11.0
    strat = FixedStrategy({DATES[0]: Signal.BUY})
    res = run_backtest(df, strat, cfg(market_rules=a_share_rules(price_limit_rate=0.10)))
    buys = [f for f in res.fills if f.side == Side.BUY]
    assert not buys, "一字涨停日仍成交 → 涨跌停约束失效"
    assert any(o.status == OrderStatus.REJECTED and o.reason == BLOCK_LIMIT_UP
               for o in res.orders), \
        f"未成交订单没有记录 BLOCK_LIMIT_UP 原因: {[(o.status, o.reason) for o in res.orders]}"


# ---------------------------------------------------------------- 5 成交价与日期一致
def test_05_fill_price_matches_execution_date():
    df = make_df([10.0, 10.5, 10.8, 11.0, 11.2, 11.5])
    strat = FixedStrategy({DATES[0]: Signal.BUY})
    res = run_backtest(df, strat, cfg())
    assert len(res.fills) == 1
    f = res.fills[0]
    assert f.fill_date == df.index[1], "T 日信号应在 T+1 成交"
    expected = round_money(df["open"].iloc[1] * (1 + cfg().slippage_buy))
    assert f.price == pytest.approx(expected, abs=1e-9), \
        f"成交价与 T+1 开盘价+滑点（两位取整）不一致: {f.price} vs {expected}"
    assert f.price >= df["open"].iloc[1], "买入价低于开盘价（滑点方向错误）"


# ---------------------------------------------------------------- 6 费用只扣一次
def test_06_fees_not_double_counted():
    df = make_df([10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    strat = FixedStrategy({DATES[0]: Signal.BUY})
    c = cfg()
    res = run_backtest(df, strat, c)
    f = res.fills[0]
    notional = f.quantity * f.price
    comm = round_money(max(notional * c.commission_rate, c.min_commission))
    transfer = round_money(notional * c.transfer_fee_rate)
    # ① 分项之和必须等于总额（不得重复计入）
    assert f.fees.total == pytest.approx(f.fees.commission + f.fees.stamp_duty + f.fees.transfer_fee,
                                         abs=1e-9), "费用分项之和 ≠ 总额"
    # ② 买入无印花
    assert f.fees.stamp_duty == 0.0, "买入不应计印花税"
    # ③ 佣金按"成交额 × 费率"或最低佣金，且只计一次（差一份即为重复扣除）
    assert f.fees.commission == pytest.approx(comm, abs=0.01), \
        f"佣金计算异常: {f.fees.commission} vs {comm}"
    assert f.fees.transfer_fee == pytest.approx(transfer, abs=0.01), \
        f"过户费计算异常: {f.fees.transfer_fee} vs {transfer}"


# ---------------------------------------------------------------- 7 基准同窗口
def test_07_benchmark_uses_same_window():
    df = make_df([10.0, 10.5, 11.0, 10.8, 11.2, 11.5])
    r = buy_and_hold(list(df.index), list(df["open"]), list(df["close"]), cfg())
    assert r.summary.start_date == df.index[0]
    assert r.summary.end_date == df.index[-1]
    assert r.liquidated is False, "默认期末不应强制平仓"


# ---------------------------------------------------------------- 8 配对按同窗口逐条
def test_08_paired_difference_is_window_aligned():
    files = glob.glob(os.path.join(AUDIT, "runs", "v2", "windows", "*", "windows.csv"))
    if not files:
        pytest.skip("v2 逐窗明细尚未生成（需先跑 run_designs.sh）")
    df = pd.read_csv(files[0], usecols=["strategy_cumulative_return",
                                        "benchmark_cumulative_return", "excess_return"])
    d = df.dropna()
    assert len(d) > 0
    diff = (d["strategy_cumulative_return"] - d["benchmark_cumulative_return"]
            - d["excess_return"]).abs().max()
    assert diff < 1e-9, f"excess_return 不是同窗口配对差（最大偏差 {diff}）"


# ---------------------------------------------------------------- 9 连续账户：不支持
def test_09_continuous_account_not_supported():
    """引擎不支持初始持仓 → 连续账户无法实现（这是事实，不是断言它能实现）。"""
    fields = set(BacktestConfig.__dataclass_fields__)
    assert "initial_positions" not in fields and "portfolio" not in fields, \
        "引擎新增了初始持仓字段，请同步更新 P0-5 差距分析"
    # 单次回测内账本恒等式必须成立（连续账户的第一块砖）
    df = make_df([10.0, 10.5, 11.0, 10.8, 11.2, 11.5])
    res = run_backtest(df, FixedStrategy({DATES[0]: Signal.BUY}), cfg())
    for st in res.account_states:
        if st.total_equity is None:
            continue
        assert st.total_equity == pytest.approx(st.cash + (st.market_value or 0.0), rel=1e-9)


# ---------------------------------------------------------------- 10 缺失行情不填充
def test_10_missing_quotes_are_not_silently_filled():
    df = make_df([10.0, 10.5, 11.0, 10.8, 11.2, 11.5])
    df = df.drop(index=df.index[2])                 # 人为制造缺失交易日
    res = run_backtest(df, FixedStrategy({DATES[0]: Signal.BUY}), cfg())
    got = {st.date for st in res.account_states}
    assert got == set(df.index), "账本出现了行情中不存在的日期（疑似插值/填充）"
    for st in res.account_states:
        has_pos = st.position.quantity > 0
        if has_pos and (st.close is None or not np.isfinite(st.close)):
            assert st.market_value is None, "有持仓且收盘缺失时，市值不得记为 0"
        if not has_pos:
            assert st.market_value == 0.0 and st.total_equity == st.cash, \
                "无持仓时应满足 market_value=0 且 total_equity=cash"


# ---------------------------------------------------------------- 11 原产物不可覆盖
def test_11_original_artifacts_untouched():
    b20 = json.load(open(os.path.join(OUTSIDE, "b20_out/b20_window_benchmark.json"),
                         encoding="utf-8"))
    recorded = b20["source"]["summary_sha256"]
    actual = hashlib.sha256(open(B50_DIST, "rb").read()).hexdigest()
    assert actual == recorded, "原始 v2 主结果已被改动（哈希与 B20 记录不符）"
    # 原始文件 mtime 必须早于本次审计开始（证明未被重写）
    mtime = os.path.getmtime(B50_DIST)
    assert mtime < os.path.getmtime(os.path.join(AUDIT, "universe48.json")), \
        "原始 v2 主结果在本次审计期间被重写（mtime 晚于审计工作区创建）"
    # 审计脚本不得向仓库外目录写入（静态扫描"写"语句的目标）
    offenders = []
    for p in glob.glob(os.path.join(ROOT, "scripts", "ma_cross_audit", "*.py")):
        for line in open(p, encoding="utf-8"):
            writes = any(k in line for k in (".write_text(", "to_csv(", "json.dump(", "shutil.copy2("))
            if not writes:
                continue
            if ("OUTSIDE" in line or "BACKUP" in line) and "AUDIT" not in line and "REPORTS" not in line:
                # copy2 允许：源是仓库外，目标是 AUDIT
                if "copy2" in line and "AUDIT" in line:
                    continue
                offenders.append((os.path.basename(p), line.strip()[:80]))
    assert not offenders, f"发现向仓库外写入: {offenders[:3]}"
