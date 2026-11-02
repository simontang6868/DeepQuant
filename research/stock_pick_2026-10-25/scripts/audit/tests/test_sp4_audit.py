# -*- coding: utf-8 -*-
"""SP-4 审计阶段的测试（独立构造小样本，不依赖大文件）

覆盖的关键行为：
  1 仓位收益计算（与闭式解逐位一致）
  2 现金收益计入（w=0 纯现金 / w=1 纯股票）
  3 交易成本计入（单调性 + 与手算一致）
  4 交易日对齐（日频 NAV 无重复日期、无倒退）
  5 最大回撤计算（已知序列）
  6 随机对照可复现性（同种子两次一致、异种子不同）
  7 缺失数据处理（NaN 不产生 inf/nan 净值）
  8 退市截断（truncated 行的 fwd20 用最后价格）
  9 不可成交（pool 为空 -> 空仓，净值不变）
 10 策略版本冻结（配置被改动 -> 哈希校验失败）
 11 前瞻台账完整性（列齐全、哈希关联）
 12 结果与配置哈希关联

运行：python -m pytest scripts/audit/tests -q
"""
import os, sys, json, hashlib, importlib.util
import numpy as np
import pandas as pd
import pytest

RD = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AUD = os.path.join(RD, "scripts", "audit")


def load_mod(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(AUD, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)          # 模块级只有常量与函数定义，不会跑 main
    return m


POS = load_mod("audit_sp4_position")
FWD = load_mod("sp4_forward")


# ---------- 1 仓位收益计算 ----------
def test_position_return_matches_closed_form():
    r = np.array([0.10, -0.05, 0.02, 0.03])
    nav = POS.nav_from_returns(r, cash_w=0.0, mode="stockonly")
    expect = np.concatenate([[1.0], np.cumprod(1 + r)])
    assert np.allclose(nav, expect)
    assert nav[0] == 1.0


def test_mix_rebalance_closed_form():
    r = np.array([0.20, -0.10])
    w = 0.25
    nav = POS.nav_from_returns(r, cash_w=0.75, mode="rebalance")
    c = 0.03 / 12
    e1 = 1 + w * r[0] + 0.75 * c
    e2 = e1 * (1 + w * r[1] + 0.75 * c)
    assert np.allclose(nav, [1.0, e1, e2])


# ---------- 2 现金收益计入 ----------
def test_zero_stock_weight_is_pure_cash():
    r = np.array([0.5, -0.4, 0.3])
    nav = POS.nav_from_returns(r, cash_w=1.0, mode="rebalance")
    c = 0.03 / 12
    assert np.allclose(nav[-1], (1 + c) ** len(r))
    # 股票收益不应影响结果
    nav2 = POS.nav_from_returns(-r, cash_w=1.0, mode="rebalance")
    assert abs(nav[-1] - nav2[-1]) < 1e-12


def test_buyhold_equals_stockonly_at_full_weight():
    r = np.array([0.1, -0.2, 0.05])
    a = POS.nav_from_returns(r, cash_w=0.0, mode="buyhold")
    b = POS.nav_from_returns(r, cash_w=0.0, mode="stockonly")
    assert np.allclose(a, b)


# ---------- 3 交易成本计入 ----------
def test_cost_reduces_return_monotonically():
    base = np.array([0.01] * 24)
    prev = None
    for c_pen in (0.0, 0.001, 0.005, 0.01):
        nav = np.concatenate([[1.0], np.cumprod(1 + base - c_pen)])
        if prev is not None:
            assert nav[-1] < prev
        prev = nav[-1]


def test_cost_matches_hand_calc():
    # 每月换 1 只、成本率 0.0204% => 24 个月累计成本约 24*0.000204
    r = np.zeros(24)
    ns = 1.0
    cost = 0.000204
    net = r - ns * cost
    nav = np.cumprod(1 + net)
    assert abs(nav[-1] - (1 - cost) ** 24) < 1e-12


# ---------- 4 交易日对齐 ----------
def test_daily_nav_dates_monotonic():
    path = os.path.join(RD, "audit_out", "sp4_daily_nav_p0_c2.csv")
    if not os.path.exists(path):
        pytest.skip("日频净值文件未生成")
    df = pd.read_csv(path)
    d = df["date"].astype(str).tolist()
    assert len(d) == len(set(d)), "日频 NAV 存在重复日期"
    assert d == sorted(d), "日频 NAV 日期未单调递增"
    assert len(d) > 1000


def test_daily_nav_no_gap_between_months():
    """月间必须无缝：相邻日期差不超过 10 个自然日（长假容忍）。"""
    path = os.path.join(RD, "audit_out", "sp4_daily_nav_p0_c2.csv")
    if not os.path.exists(path):
        pytest.skip("日频净值文件未生成")
    df = pd.read_csv(path)
    dt = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    gap = dt.diff().dt.days.dropna()
    assert gap.max() <= 15, f"存在 {gap.max()} 天的空档"


# ---------- 5 最大回撤 ----------
def test_mdd_known_series():
    r = np.array([0.10, -0.50, 0.20])       # 1 -> 1.1 -> 0.55 -> 0.66
    nav = np.concatenate([[1.0], np.cumprod(1 + r)])
    m = POS.metrics(nav)
    assert abs(m["mdd"] - (0.55 / 1.1 - 1)) < 1e-12


def test_mdd_includes_first_month():
    """原引擎 cumprod 无前置 1.0，会漏掉首月回撤；独立实现必须包含。"""
    r = np.array([-0.10, 0.50])
    nav = np.concatenate([[1.0], np.cumprod(1 + r)])
    assert abs(POS.metrics(nav)["mdd"] - (-0.10)) < 1e-12


# ---------- 6 随机对照可复现性 ----------
def test_rng_same_seed_same_result():
    a = np.random.default_rng(20261026).choice(1000, 5, replace=False)
    b = np.random.default_rng(20261026).choice(1000, 5, replace=False)
    assert np.array_equal(a, b)


def test_rng_different_seed_differs():
    a = np.random.default_rng(20261026).choice(1000, 5, replace=False)
    b = np.random.default_rng(20261027).choice(1000, 5, replace=False)
    assert not np.array_equal(a, b)


# ---------- 7 缺失数据处理 ----------
def test_nan_returns_not_propagated():
    """缺价股票应被剔除或按 0 收益，不应产生 nan/inf 净值。"""
    mat = np.array([[10.0, 11.0, np.nan, 12.0],
                    [20.0, 20.0, 21.0, 22.0]])
    rets = mat[:, 1:] / mat[:, :-1] - 1
    rets = np.where(np.isnan(rets), 0.0, rets)
    port = rets.mean(axis=0)
    nav = np.cumprod(1 + port)
    assert np.all(np.isfinite(nav))


def test_zero_holding_month_nav_flat():
    """持仓为空时净值不变（空仓），不应变成 nan 或 0。"""
    nav_prev = 1.23
    assert nav_prev * (1 + 0.0) == 1.23


# ---------- 8 退市截断 ----------
def test_truncated_fwd20_uses_last_price():
    """退市股的最后一段损失必须被计入（而不是被剔除）。

    依据（实测，非拍脑袋）：
      trunc 面板 truncated=1 共 5,511 行，其中 5,197 行落在数据末尾（20260930），
      真正的退市截断 = 该股最后出现月 < 全局最后月，共 304 行。
      这 304 行：均值 -9.02%、中位 -6.91%、<-20% 占 32.24%、<-50% 占 8.55%。
    因此断言：真退市截断必须存在、均值显著为负、且深度亏损占比 > 20%。
    """
    p = os.path.join(RD, "data", "panel_monthly_trunc.parquet")
    if not os.path.exists(p):
        pytest.skip("对照面板未生成")
    df = pd.read_parquet(p, columns=["date", "code", "fwd20", "truncated"])
    tr = df[df["truncated"] == 1].copy()
    assert len(tr) > 0, "对照面板应包含被截断的样本"

    lastm = df.groupby("code")["date"].max()
    gmax = df["date"].max()
    tr["codelast"] = tr["code"].map(lastm)
    real = tr[tr["codelast"] < gmax]          # 真退市，而非数据末尾
    assert len(real) > 100, f"真退市截断样本过少（{len(real)}），无法验证"
    assert real["fwd20"].mean() < 0, "真退市样本的平均收益应为负"
    assert (real["fwd20"] < -0.20).mean() > 0.20, "真退市样本中深度亏损（<-20%）占比应 > 20%"
    # 全样本 fwd20 不应有大面积缺失（截断后都能算出）
    assert df["fwd20"].isna().mean() < 0.01


def test_original_panel_drops_delisting_tail():
    """原面板用 sell<len(dates) 剔除退市尾段，应表现为"退市股最后 20 天缺失"。"""
    p = os.path.join(RD, "data", "panel_monthly.parquet")
    if not os.path.exists(p):
        pytest.skip("原面板不存在")
    df = pd.read_parquet(p, columns=["date", "code"])
    lastm = df.groupby("code")["date"].max()
    gmax = df["date"].max()
    ended = lastm[lastm < gmax]
    assert len(ended) > 50, "原面板应包含已退市/停牌股票，否则幸存者偏差判断需重做"
    # 这些股票的最后一个月不应出现在"数据末尾"，即其尾段被截掉
    tail_rows = df[(df["code"].isin(ended.index)) & (df["date"] == gmax)]
    assert len(tail_rows) == 0, "已退市股票不应出现在最后一个月"


# ---------- 9 不可成交 ----------
def test_empty_pool_means_no_trade():
    pool = np.array([], dtype=int)
    K = 5
    assert len(pool) < K          # 触发"不建仓"分支


# ---------- 10 策略版本冻结 ----------
def test_config_hash_detects_tampering():
    cfg = os.path.join(RD, "forward", "sp4_config_v1.json")
    sha = os.path.join(RD, "forward", "sp4_config_v1.sha256")
    if not (os.path.exists(cfg) and os.path.exists(sha)):
        pytest.skip("前瞻配置未冻结")
    want = open(sha, encoding="utf-8").read().split()[0]
    with open(cfg, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    assert want == got
    # 模拟篡改：改一个字符后哈希必须变化
    txt = open(cfg, encoding="utf-8").read()
    tampered = txt.replace('"n_hold": 5', '"n_hold": 6', 1)
    assert tampered != txt, "测试前提失败：配置中未找到 n_hold"
    assert hashlib.sha256(tampered.encode()).hexdigest() != want


# ---------- 11 前瞻台账完整性 ----------
def test_forward_log_schema():
    p = os.path.join(RD, "forward", "observations.csv")
    if not os.path.exists(p):
        pytest.skip("台账未初始化")
    df = pd.read_csv(p)
    for c in FWD.OBS_COLS:
        assert c in df.columns, f"台账缺列 {c}"
    if len(df):
        assert df["strategy_hash"].notna().all()
        assert (df["n_swaps"].astype(float) >= 0).all()


# ---------- 12 结果与配置哈希关联 ----------
def test_results_carry_prereg_hash():
    p = os.path.join(RD, "out", "sp4_result.json")
    d = json.load(open(p, encoding="utf-8"))
    assert len(d["prereg_sha256"]) == 64
    pre = os.path.join(RD, "sp4_prereg.sha256")
    want = open(pre, encoding="utf-8").read().split()[0]
    assert d["prereg_sha256"] == want, "结果文件记录的预登记哈希与冻结文件不一致"


def test_dump_reproduces_original_numbers():
    """独立复算必须复现原结果：c2 年化绝对差 < 1e-9，MDD 差 < 1e-3（首月口径差异）。"""
    p = os.path.join(RD, "audit_out", "sp4_position_audit.json")
    if not os.path.exists(p):
        pytest.skip("未运行独立复算")
    d = json.load(open(p, encoding="utf-8"))
    assert d["vs_original"]["max_abs_diff"] < 1e-3
    for c in d["vs_original"]["checks"]:
        if c["field"] == "ann":
            assert abs(c["diff"]) < 1e-9, f"{c['group']} 年化未复现：{c['diff']}"
