"""阶段 C 执行脚本的单元测试。

测的是**规则语义与算法正确性**，不是市场结果：
配对逻辑、NW 估计量、Holm 校正、可成交性实现一致性、四档判据边界。

核心方法：**每个关键函数都与一份独立实现交叉验证**。
本项目已发生过三次"检查器自己写错、通过率是假的"（PC2 三次、机 V2 一次），
所以这里不允许"只有一份实现 + 自己断言自己"。

运行：
    cd "D:/DeepQuant power by txm/DeepQuant-main"
    PYTHONPATH=scripts python -m pytest scripts/tests/test_phase_c.py -q
"""
from __future__ import annotations

import math
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_phase_c import (  # noqa: E402
    ECON_MIN_PP, K_FIXED, MIN_LISTED_DAYS, MIN_N_MAIN, MIN_NW_EFF, T_CRIT,
    build_mask, cost_of, flags_fast, holm, judge_pool_effect, newey_west, pair_diff,
    pool_518, stream_bars,
)


# ============================================================ NW 估计量

def _nw_ref(x, lag):
    """独立实现：numpy 切片点积，与脚本里的 Python 循环不共享代码路径。"""
    x = np.asarray(x, dtype=float)
    n = len(x)
    m = float(x.mean())
    e = x - m
    s = float(e @ e) / n
    for j in range(1, lag + 1):
        s += 2.0 * (1.0 - j / (lag + 1)) * float(e[j:] @ e[:-j]) / n
    se = math.sqrt(s / n)
    return m, se, (m / se if se > 0 else float("nan"))


def test_newey_west_matches_independent_implementation():
    rng = np.random.default_rng(20261021)
    x = list(rng.normal(0.005, 0.03, size=237))
    got = newey_west(x, lag=3)
    m, se, t = _nw_ref(x, 3)
    assert got["n"] == 237
    assert abs(got["mean"] - m) < 1e-12
    assert abs(got["se_nw"] - se) < 1e-12
    assert abs(got["t_nw"] - t) < 1e-10


def test_newey_west_lag0_equals_population_t():
    """lag=0 时 S=γ0，se 用**总体**标准差（不是样本），须与解析式逐位一致。"""
    x = [0.01, -0.02, 0.03, 0.00, 0.015, -0.01]
    got = newey_west(x, lag=0)
    n = len(x)
    m = sum(x) / n
    sd_pop = math.sqrt(sum((v - m) ** 2 for v in x) / n)
    assert abs(got["se_nw"] - sd_pop / math.sqrt(n)) < 1e-15
    assert abs(got["t_nw"] - m / (sd_pop / math.sqrt(n))) < 1e-12


def _bartlett_s(x, lag=3):
    """独立计算 Bartlett 加权和（用于搜负方差样本，与脚本实现不共享代码）。"""
    n = len(x)
    m = sum(x) / n
    e = [v - m for v in x]
    s = sum(v * v for v in e) / n
    for j in range(1, lag + 1):
        s += 2.0 * (1.0 - j / (lag + 1)) * sum(e[t] * e[t - j] for t in range(j, n)) / n
    return s


def test_newey_west_negative_variance_falls_back_and_flags():
    """有限样本下 Bartlett 和可能为负。必须退回 γ0 **并打标**，不能静默给出 se<0。

    ⚠️ 构造陷阱记录：严格交替序列 `[1,-1]*n` 在 Bartlett 权重下
       **奇数 lag 恰好为 0、偶数 lag 为正**（数学恒等式），根本触发不了负分支。
       第一版测试就卡在这里——是测试样本构造错了，不是代码错。
       故改用随机小样本搜索，并在找不到时如实 skip。
    """
    rng = np.random.default_rng(7)
    found = None
    for _ in range(20000):
        n = int(rng.integers(6, 10))
        x = [float(v) for v in rng.normal(0, 1, size=n)]
        if _bartlett_s(x, 3) <= 0:
            found = x
            break
    if found is None:
        pytest.skip("随机搜索未找到负方差样本；该分支未被覆盖（如实记录，不伪造）")
    got = newey_west(found, lag=3)
    assert got["variance_fell_back_to_g0"] is True
    assert got["se_nw"] > 0
    # 退回 γ0 后 se = sqrt(γ0 / n)
    assert got["se_nw"] == pytest.approx(math.sqrt(_gamma0(found) / len(found)))


def _gamma0(x):
    n = len(x)
    m = sum(x) / n
    return sum((v - m) ** 2 for v in x) / n


def test_newey_west_constant_series_gives_nan_not_crash():
    got = newey_west([0.01] * 50, lag=3)
    assert got["se_nw"] == 0.0
    assert math.isnan(got["t_nw"])


# ============================================================ Holm

def test_holm_textbook_case():
    # p = 0.01, 0.02, 0.03, 0.04；m=4 → 阈值 0.0125 / 0.01667 / 0.025 / 0.05
    p = {"a": 0.01, "b": 0.02, "c": 0.03, "d": 0.04}
    r = holm(p, alpha=0.05)
    assert r["a"]["reject"] is True
    assert abs(r["a"]["threshold"] - 0.0125) < 1e-12
    # b: p=0.02 > 0.01667 → 不拒绝，且后续全部不拒绝（Holm 的单调性）
    assert r["b"]["reject"] is False
    assert r["c"]["reject"] is False
    assert r["d"]["reject"] is False
    assert r["b"]["rank"] == 2 and r["d"]["rank"] == 4


def test_holm_all_reject_when_all_tiny():
    p = {"a": 0.0001, "b": 0.0002, "c": 0.0003, "d": 0.0004}
    r = holm(p, alpha=0.05)
    assert all(v["reject"] for v in r.values())


def test_holm_handles_nan():
    r = holm({"a": 0.01, "b": float("nan")}, alpha=0.05)
    assert r["a"]["reject"] is True
    assert r["b"]["reject"] is False and r["b"]["p"] is None


# ============================================================ 配对

def test_pair_diff_uses_intersection_only_and_doubles():
    a = [{"signal_date": "2020-01-31", "d": 0.001},
         {"signal_date": "2020-02-28", "d": 0.002},
         {"signal_date": "2020-03-31", "d": 0.003}]
    b = [{"signal_date": "2020-02-28", "d": 0.005},
         {"signal_date": "2020-03-31", "d": 0.004},
         {"signal_date": "2020-04-30", "d": 0.009}]
    common, delta = pair_diff(a, b, "d")
    assert common == ["2020-02-28", "2020-03-31"]
    # Δ = 2*(d_b - d_a)
    assert delta[0] == pytest.approx(2 * (0.005 - 0.002))
    assert delta[1] == pytest.approx(2 * (0.004 - 0.003))


def test_pair_diff_drops_nan_months():
    a = [{"signal_date": "2020-01-31", "d": float("nan")},
         {"signal_date": "2020-02-28", "d": 0.002}]
    b = [{"signal_date": "2020-01-31", "d": 0.001},
         {"signal_date": "2020-02-28", "d": 0.003}]
    common, delta = pair_diff(a, b, "d")
    assert common == ["2020-02-28"]
    assert len(delta) == 1


# ============================================================ 辅助检验方向

def test_aux_defs_direction_consistent_with_label():
    """label 写的是 `spread(TO) − spread(FROM)`，所以元组顺序必须是 (id, FROM, TO)。

    第一版把它写成 (id, hi, lo) 并把 hi 传成第一个元素，导致 S3~S5 三个比较的
    **符号与标签完全相反**：S3 标签说 spread(D)−spread(B)，实际算的是 spread(B)−spread(D)，
    报 +0.57（正），而真相是 D 比 B 低 0.57。
    这种错误会让整段结论读反，且数值看起来"很合理"，肉眼极难发现。
    这里用正则从 label 文本里反解方向，自动比对元组顺序。
    """
    from run_phase_c import AUX_DEFS
    pat = re.compile(r"spread\((\w+)\)\s*[−\-]\s*spread\((\w+)\)")
    assert len(AUX_DEFS) == 4
    for sid, frm, to, label in AUX_DEFS:
        m = pat.search(label)
        assert m, f"{sid} 的 label 无法解析方向：{label}"
        assert (m.group(1), m.group(2)) == (to, frm), (
            f"{sid} 元组顺序 (from={frm}, to={to}) 与 label「{label}」不一致")
    # S1（主检验）不在辅助列表里，避免重复计数
    assert "S1" not in {d[0] for d in AUX_DEFS}


def test_pair_diff_sign_follows_argument_order():
    """Δ = 2×(d_第二个参数 − d_第一个参数)。方向约定必须单向，不能靠记性。"""
    a = [{"signal_date": "2020-01-31", "d": 0.0094}]
    b = [{"signal_date": "2020-01-31", "d": 0.0066}]
    _, dl = pair_diff(a, b, "d")
    assert dl[0] < 0 and dl[0] == pytest.approx(2 * (0.0066 - 0.0094))
    _, dl2 = pair_diff(b, a, "d")
    assert dl2[0] == pytest.approx(-dl[0])


# ============================================================ 成本口径

def test_cost_is_monthly_not_annualized():
    """用 Gate 6 的实测值钉死成本口径。

    Gate 6 主设定：换手 0.8029/月、mean_d = 0.7084pp、sd = 2.4866pp、n = 237
    → 月成本 0.1606pp、净 0.5479pp、t_net = 3.392（Gate 6 报告原值）。

    若误把预登记 §7 的 `2×to×12×费率`（**年化**）当月度扣，
    月成本会变成 1.93pp，净收益变 -1.27pp、t_net = -7.96。
    第一版就是这么错的，这条测试防止再次发生。
    """
    r = cost_of(0.8028945332579936, 0.0070844569, 0.0248657394, 237, 0.001)
    assert r["cost_pp_month"] == pytest.approx(0.1606, abs=1e-3)
    assert r["net_pp_month"] == pytest.approx(0.5479, abs=1e-3)
    assert r["t_net"] == pytest.approx(3.392, abs=5e-3)
    # 年化是月度的 12 倍，且单独字段
    assert r["cost_pp_year"] == pytest.approx(r["cost_pp_month"] * 12)


def test_cost_scales_linearly_with_rate():
    a = cost_of(0.8, 0.007, 0.025, 237, 0.001)
    b = cost_of(0.8, 0.007, 0.025, 237, 0.003)
    assert b["cost_pp_month"] == pytest.approx(3 * a["cost_pp_month"])


# ============================================================ 四档判据

def _nw(n, t, mean_pp, eff=None):
    return {"n": n, "n_eff": n - 3 if eff is None else eff, "t_nw": t,
            "mean_pp": mean_pp, "mean": mean_pp / 100.0}


def test_judge_confirmed():
    v = judge_pool_effect(_nw(237, 2.5, 0.40), [])
    assert v["verdict"] == "POOL_EFFECT_CONFIRMED"


def test_judge_not_detected():
    v = judge_pool_effect(_nw(237, 1.2, 0.90), [])
    assert v["verdict"] == "POOL_EFFECT_NOT_DETECTED"


def test_judge_gap_tier_declared_before_run():
    """n 够、t 够、但效应小于经济门槛 —— 预登记三档都不匹配，走补充第四档。"""
    v = judge_pool_effect(_nw(237, 2.5, 0.10), [])
    assert v["verdict"] == "POOL_EFFECT_SIGNIFICANT_BUT_SMALL"
    assert "note" in v


def test_judge_inconclusive_small_n():
    v = judge_pool_effect(_nw(MIN_N_MAIN - 1, 5.0, 1.0), [])
    assert v["verdict"] == "INCONCLUSIVE"


def test_judge_inconclusive_short_nw_eff():
    v = judge_pool_effect(_nw(237, 5.0, 1.0, eff=MIN_NW_EFF - 1), [])
    assert v["verdict"] == "INCONCLUSIVE"


def test_judge_boundaries_are_inclusive():
    """恰好在门槛上：|t|=1.96、|mean|=0.30pp 应算 CONFIRMED（判据写的是 ≥）。"""
    v = judge_pool_effect(_nw(150, T_CRIT, ECON_MIN_PP), [])
    assert v["verdict"] == "POOL_EFFECT_CONFIRMED"


# ============================================================ 可成交性

def _rec(o, h, l, c, prev_close=10.0, idx=100, prev_date="2020-01-06"):
    return {"bar": (o, h, l, c, 1e6), "prev": (prev_close,) * 4 + (1e6,),
            "prev_date": prev_date, "idx": idx}


def test_flags_plain_up_move_is_tradable():
    # 主板 +9.0%：不到 10%-0.5% → 可成交
    f = flags_fast(_rec(10.5, 11.0, 10.4, 10.9), "600000.SH", "2020-01-07", set())
    assert f["tradable"] is True and f["reason"] == "ok"


def test_flags_limit_up_detected_with_tolerance():
    # +9.8%：明确落在 10%-0.5% 容差之内 → 判涨停
    # ⚠️ 不要用恰好 +9.5%：`10.95/10 - 1 = 0.09499999999999997` 差一个 ulp 就跨过阈值，
    #    那是浮点噪声，不是语义边界。第一版测试就踩了这个坑（代码对、预期错）。
    f = flags_fast(_rec(10.7, 11.0, 10.6, 10.98), "600000.SH", "2020-01-07", set())
    assert f["tradable"] is False and f["reason"] == "limit_up" and f["dir"] == "up"


def test_flags_one_word_board():
    f = flags_fast(_rec(11.0, 11.0, 11.0, 11.0), "600000.SH", "2020-01-07", set())
    assert f["tradable"] is False
    assert f["reason"] == "one_word_board" and f["dir"] == "up"


def test_flags_gem_limit_switches_at_2020_08_24():
    """创业板 +9.8%：2020-08-24 之前是涨停，之后不是。**写死常数会让前后一半判错。**"""
    rec = _rec(10.7, 11.0, 10.6, 10.98, prev_date="2020-08-21")
    before = flags_fast(rec, "300001.SZ", "2020-08-21", set())
    after = flags_fast(dict(rec, prev_date="2021-06-01"), "300001.SZ", "2021-06-02", set())
    assert before["tradable"] is False and before["limit"] == 0.10
    assert after["tradable"] is True and after["limit"] == 0.20


def test_flags_star_board_is_20pct_from_day_one():
    rec = _rec(10.7, 11.0, 10.6, 10.95)
    f = flags_fast(rec, "688001.SH", "2020-01-07", set())
    assert f["tradable"] is True and f["limit"] == 0.20


def test_flags_warmup_exempts_first_5_days():
    f = flags_fast(_rec(11.0, 11.0, 11.0, 11.0, prev_close=10.0, idx=3),
                   "600000.SH", "2020-01-07", set())
    assert f["tradable"] is True and f["reason"] == "exempt"


def test_flags_resume_gap_exempts():
    f = flags_fast(_rec(11.0, 11.0, 11.0, 11.0, prev_date="2019-12-01"),
                   "600000.SH", "2020-01-07", set())
    assert f["tradable"] is True and f["reason"] == "exempt"


def test_flags_ex_dividend_day_exempts():
    """除权日价格本就要跳，不豁免会把它当成涨停/跌停。"""
    f = flags_fast(_rec(9.0, 9.0, 9.0, 9.0), "600000.SH", "2020-01-07", {"2020-01-07"})
    assert f["tradable"] is True and f["reason"] == "exempt"


def test_flags_no_bar():
    assert flags_fast(None, "600000.SH", "2020-01-07", set())["reason"] == "no_bar"


def test_flags_no_prev():
    rec = {"bar": (10, 10, 10, 10, 1e6), "prev": None, "prev_date": None, "idx": 0}
    assert flags_fast(rec, "600000.SH", "2020-01-07", set())["reason"] == "no_prev"


# ============================================================ 掩码

def _close_df():
    idx = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"])
    return pd.DataFrame({
        "A": [10.0, 10.1, 10.2, 10.3, 10.4],
        "B": [np.nan, np.nan, 5.0, 5.1, 5.2],
        "C": [np.nan] * 5,
        "D": [0.0, 0.0, 3.0, 3.0, 3.0],     # 0 价必须被当成缺失
    }, index=idx)


def test_build_mask_zero_means_no_listing_requirement():
    m = build_mask(_close_df(), 0)
    assert m.iloc[0].tolist() == [True, False, False, False]
    assert m.iloc[2].tolist() == [True, True, False, True]


def test_build_mask_252_uses_cumulative_valid_days():
    m = build_mask(_close_df(), 3)
    # 第 3 天（idx=2）：A 有 3 个有效日 → 在；B 只有 1 个 → 不在
    assert m.iloc[2].tolist() == [True, False, False, False]
    # 第 5 天（idx=4）：B 有 3 个有效日 → 在
    assert m.iloc[4].tolist() == [True, True, False, True]


def test_build_mask_never_uses_future_rows():
    """as-of 语义：第 t 行的掩码只能由 <= t 的数据决定。"""
    df = _close_df()
    m = build_mask(df, 2)
    # 第 3 天 B 只有 1 个有效日 → 必为 False（若误用全样本 cumsum 会变成 True）
    assert bool(m.iloc[2]["B"]) is False
    assert MIN_LISTED_DAYS == 252


# ============================================================ 池与流式实现

def test_pool_518_has_no_b_share():
    p = pool_518()
    assert len(p) == 518
    assert not [c for c in p if c.split(".")[0].startswith(("200", "900"))]


def test_reason_of_distinguishes_after_last_data_from_suspension():
    """`no_bar` 必须拆成两种：晚于最后行情日 = 序列终止；区间内缺失 = 真停牌。

    合并成同一个桶，就是"把退市藏进停牌里"——D-1 事故（东财空表当成无公司行为）
    的同构错误。本项目的铁律是：**"文件不存在"与"确认无事件"必须分开**。
    """
    from run_phase_c import _reason_of
    span = {"600000.SH": {"first": "2005-01-04", "last": "2020-06-30", "n_days": 3000}}
    f = {"tradable": False, "reason": "no_bar"}
    assert _reason_of(f, "600000.SH", "2020-12-31", span) == "after_last_data"
    assert _reason_of(f, "600000.SH", "2019-03-01", span) == "no_bar"
    # 没有该股 span 时**不得臆断**为退市——未知就是未知
    assert _reason_of(f, "UNKNOWN.SH", "2020-12-31", span) == "no_bar"
    # 其他 reason 原样返回
    assert _reason_of({"reason": "limit_up"}, "600000.SH", "2019-03-01", span) == "limit_up"


def test_stream_bars_matches_reference_on_real_data(tmp_path=None):
    """流式实现与 `TradabilityIndex` 在真实数据上必须逐点一致。"""
    exec_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "exec_all")
    code = "600000.SH"
    path = os.path.join(exec_dir, f"{code}.exec.csv")
    if not os.path.exists(path):
        pytest.skip("无本地 EXEC 数据")

    dates = [ln.split(",")[0] for ln in open(path, encoding="utf-8").read().splitlines()[1:]]
    pick = dates[2500:2505] + dates[3000:3005] + dates[4000:4005]
    bars, ex_al, span = stream_bars({code: set(pick)})
    assert span[code]["n_days"] > 4000
    assert span[code]["first"] < span[code]["last"]

    from asof_universe import TradabilityIndex
    ref = TradabilityIndex(exec_dir)
    for d in pick:
        a = flags_fast(bars[code].get(d), code, d, ex_al.get(code, set()))
        b = ref.flags(code, d)
        assert a["tradable"] == b["tradable"], f"{d} tradable 不一致：{a} vs {b}"
        if not a["tradable"]:
            assert a["reason"] == b["reason"], f"{d} reason 不一致"
        if a.get("dir"):
            assert a["dir"] == b.get("dir")
