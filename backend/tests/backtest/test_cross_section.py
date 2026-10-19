"""Gate 1 候选集与动量信号测试（B-3 第一阶段）。

对应 ``docs/GATE1_PREREGISTRATION.md`` v1.1：
- 第 3 节 P1~P3：信号日可定价、窗口覆盖率 ≥90%
- 第 4 节：``close[t-5] / close[t-64] - 1``
- 第 13 节 C3-7：未来价格不得影响历史信号（前缀不变性）

本文件**不产生任何研究结论**：全部用构造数据或单只真实数据验证实现，
不跑组合回测、不算策略绩效。Gate 1 的判定要等用户授权后单独执行。
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from deepquant.backtest.cross_section import (
    REASON_AFTER_LAST_ROW,
    REASON_ENDPOINT_MISSING,
    REASON_INSUFFICIENT_COVERAGE,
    REASON_INSUFFICIENT_HISTORY,
    REASON_NO_SIGNAL_ROW,
    REASON_NONPOSITIVE_PRICE,
    REASON_NOT_YET_LISTED,
    REASON_OK,
    REASON_SIGNAL_NOT_IN_CALENDAR,
    REASON_ZERO_AMOUNT,
    CrossSectionConfig,
    CrossSectionPanel,
    build_scores_by_date,
    momentum_scores,
    trading_calendar,
)
from deepquant.backtest.portfolio import equal_weight_targets

# 真实行情样本不在仓库内（b50_v3 是本机历史产物）。默认值保留原作者的绝对路径，
# 以便在该机上零配置运行；换机器用 DEEPQUANT_REAL_600809 指过去，指不到就按
# skipif 跳过——绝不允许"因为路径不存在，所以这条真实数据回归悄悄不跑了"却不留痕。
_DEFAULT_REAL_600809 = r"D:/DeepQuant power by txm/b50_v3/600809.SH.qfq.csv"
REAL_600809 = Path(os.environ.get("DEEPQUANT_REAL_600809", _DEFAULT_REAL_600809))

N_DAYS = 70          # 信号日 = 第 69 个位置；窗口 [5, 64]
SIGNAL_POS = 69


# ===================================================================== #
# 构造工具
# ===================================================================== #


def make_dates(n: int = N_DAYS, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


def make_frame(
    closes,
    *,
    dates=None,
    opens=None,
    amounts=None,
) -> pd.DataFrame:
    closes = [float(c) for c in closes]
    dates = pd.DatetimeIndex(dates) if dates is not None else make_dates(len(closes))
    n = len(closes)
    opens = [float(o) for o in opens] if opens is not None else list(closes)
    amounts = [float(a) for a in amounts] if amounts is not None else [1e8] * n
    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": [max(c, o) for c, o in zip(closes, opens)],
            "low": [min(c, o) for c, o in zip(closes, opens)],
            "close": closes,
            "volume": [1_000_000] * n,
            "amount": amounts,
        }
    )


def trending(n: int = N_DAYS, base: float = 10.0, step: float = 0.5) -> list[float]:
    """确定性价格序列：base, base+step, base+2*step, ..."""
    return [base + i * step for i in range(n)]


def signal_date(frames: dict[str, pd.DataFrame]) -> pd.Timestamp:
    cal = trading_calendar(frames)
    return cal[SIGNAL_POS]


# ===================================================================== #
# 一、动量计算
# ===================================================================== #


def test_momentum_matches_pre_registered_formula():
    """score = close[t-5] / close[t-64] - 1（预登记第 4 节，字面）。"""
    closes = trending()
    frame = make_frame(closes)
    cal = trading_calendar({"X": frame})
    panel = CrossSectionPanel({"X": frame}, calendar=make_dates())
    cfg = panel.config

    scores = panel.scores(cal[SIGNAL_POS])
    expected = closes[SIGNAL_POS - cfg.skip] / closes[SIGNAL_POS - cfg.start_offset] - 1.0
    assert scores["X"] == pytest.approx(expected)


def test_default_offsets_match_pre_registration():
    """默认参数必须就是预登记的 60/5 → 起点偏移 64、窗口 60 位置、最少 54 有效日。"""
    cfg = CrossSectionConfig()
    assert (cfg.lookback, cfg.skip) == (60, 5)
    assert cfg.start_offset == 64
    assert cfg.window_length == 60
    assert cfg.min_valid_days == 54


def test_last_five_days_do_not_affect_score():
    """skip=5：最近 5 个交易日的价格变化不得影响分数（避开短期反转噪声）。"""
    base = trending()
    panel_a = CrossSectionPanel({"X": make_frame(base)})
    cal = trading_calendar({"X": make_frame(base)})
    d = cal[SIGNAL_POS]

    mutated = list(base)
    for i in range(SIGNAL_POS - 4, SIGNAL_POS + 1):   # 后 5 天大幅改动
        mutated[i] = mutated[i] * 3
    panel_b = CrossSectionPanel({"X": make_frame(mutated)})

    assert panel_a.scores(d)["X"] == pytest.approx(panel_b.scores(d)["X"])


def test_changing_window_endpoint_changes_score():
    """改动 t-5 的价格必须改变分数——否则 skip 把整个窗口吃掉了。"""
    base = trending()
    cal = make_dates()
    d = cal[SIGNAL_POS]
    panel_a = CrossSectionPanel({"X": make_frame(base)})

    mutated = list(base)
    mutated[SIGNAL_POS - 5] = mutated[SIGNAL_POS - 5] * 2
    panel_b = CrossSectionPanel({"X": make_frame(mutated)})

    assert panel_a.scores(d)["X"] != pytest.approx(panel_b.scores(d)["X"])


# ===================================================================== #
# 二、P1 / P2：信号日必须可定价、可成交
# ===================================================================== #


def test_missing_signal_row_is_excluded_with_reason():
    """信号日当天缺行（停牌/数据缺口）——数据在信号日之后仍在继续。"""
    n = N_DAYS + 10
    frame = make_frame(trending(n))
    d = make_dates(n)[SIGNAL_POS]
    frame = frame[frame["date"] != d].reset_index(drop=True)   # 中间挖掉信号日

    panel = CrossSectionPanel({"X": frame}, calendar=make_dates(n))
    assert panel.scores(d)["X"] is None
    assert panel.reasons(d)["X"] == REASON_NO_SIGNAL_ROW
    # 注意：若删的是最后一行，会被判成 after_last_available_row，
    # 那是「区间结束」而不是「当天缺行」，两者不同（见下一条测试）。


def test_nonpositive_signal_price_is_excluded():
    """P2：信号日价格非正 → 不可参与排序（复权缺陷导致，不是「便宜」）。"""
    base = trending()
    base[SIGNAL_POS] = -3.77          # 与真实 600809.SH 的缺陷同量级
    panel = CrossSectionPanel({"X": make_frame(base)})
    d = make_dates()[SIGNAL_POS]

    assert panel.scores(d)["X"] is None
    assert panel.reasons(d)["X"] == REASON_NONPOSITIVE_PRICE


def test_zero_amount_is_excluded():
    amounts = [1e8] * N_DAYS
    amounts[SIGNAL_POS] = 0.0
    panel = CrossSectionPanel({"X": make_frame(trending(), amounts=amounts)})
    d = make_dates()[SIGNAL_POS]

    assert panel.scores(d)["X"] is None
    assert panel.reasons(d)["X"] == REASON_ZERO_AMOUNT


def test_missing_amount_column_raises_unless_explicitly_disabled():
    """缺 amount 列必须报错，不能静默跳过 P2 的成交额检查。"""
    frame = make_frame(trending()).drop(columns=["amount"])
    with pytest.raises(ValueError, match="缺少 amount"):
        CrossSectionPanel({"X": frame})

    panel = CrossSectionPanel({"X": frame}, config=CrossSectionConfig(require_positive_amount=False))
    assert panel.scores(make_dates()[SIGNAL_POS])["X"] is not None


# ===================================================================== #
# 三、P3：窗口覆盖率
# ===================================================================== #


def _drop_positions(frame: pd.DataFrame, positions: list[int], n: int = N_DAYS) -> pd.DataFrame:
    dates = make_dates(n)
    drop = {dates[i] for i in positions}
    return frame[~frame["date"].isin(drop)].reset_index(drop=True)


def test_coverage_exactly_54_passes():
    """窗口 60 位置，删 6 个非端点日 → 有效 54（恰好达线）→ 保留。"""
    frame = make_frame(trending())
    mids = [10, 15, 20, 25, 30, 35]      # 都在 [5, 64] 内且不是端点
    frame = _drop_positions(frame, mids)

    panel = CrossSectionPanel({"X": frame}, calendar=make_dates())
    assert panel.config.min_valid_days == 54
    assert panel.reasons(make_dates()[SIGNAL_POS])["X"] == REASON_OK


def test_coverage_53_is_excluded_with_count_in_reason():
    """删 7 个 → 有效 53（差 1 天）→ 剔除，且原因里带具体数字。"""
    frame = make_frame(trending())
    mids = [10, 15, 20, 25, 30, 35, 40]
    frame = _drop_positions(frame, mids)

    panel = CrossSectionPanel({"X": frame}, calendar=make_dates())
    d = make_dates()[SIGNAL_POS]
    assert panel.scores(d)["X"] is None
    reason = panel.reasons(d)["X"]
    assert reason.startswith(REASON_INSUFFICIENT_COVERAGE)
    assert "53/60" in reason, "必须报出实际有效天数，否则无法判断离阈值差多少"


def test_missing_window_endpoint_is_reported_separately():
    """端点缺失是「算不出来」，与「覆盖率不够」是两回事，不能混成一个原因。"""
    frame = make_frame(trending())
    frame = _drop_positions(frame, [SIGNAL_POS - 5])   # 删 t-5

    panel = CrossSectionPanel({"X": frame}, calendar=make_dates())
    d = make_dates()[SIGNAL_POS]
    assert panel.scores(d)["X"] is None
    assert panel.reasons(d)["X"] == REASON_ENDPOINT_MISSING


def test_insufficient_history_before_window_fits():
    """数据不足 65 个交易日 → 窗口起点为负 → 明确报「历史不足」。"""
    frame = make_frame(trending(30))
    panel = CrossSectionPanel({"X": frame}, calendar=make_dates(30))
    d = make_dates(30)[-1]
    assert panel.scores(d)["X"] is None
    assert panel.reasons(d)["X"] == REASON_INSUFFICIENT_HISTORY


def test_signal_date_outside_calendar():
    frame = make_frame(trending())
    panel = CrossSectionPanel({"X": frame}, calendar=make_dates())
    assert panel.reasons(pd.Timestamp("2024-01-06"))["X"] == REASON_SIGNAL_NOT_IN_CALENDAR


# ===================================================================== #
# 四、C3-7：未来价格不得影响历史信号（预登记第 13 节）
# ===================================================================== #


def test_future_prices_do_not_change_past_signal():
    """把信号日之后的价格整体改成垃圾值，此前算出的分数必须一字不差。"""
    base = trending(N_DAYS + 20)
    cal = make_dates(N_DAYS + 20)
    d = cal[SIGNAL_POS]

    clean = CrossSectionPanel({"X": make_frame(base)})

    polluted = list(base)
    for i in range(SIGNAL_POS + 1, len(polluted)):
        polluted[i] = polluted[i] * 50 if i % 2 else 0.01
    dirty = CrossSectionPanel({"X": make_frame(polluted)})

    assert clean.scores(d)["X"] == pytest.approx(dirty.scores(d)["X"])
    assert clean.reasons(d)["X"] == dirty.reasons(d)["X"] == REASON_OK


def test_truncating_future_keeps_same_score():
    """只保留到信号日的行情，分数与全样本一致——信号只依赖历史。"""
    full = trending(N_DAYS + 30)
    cal = make_dates(N_DAYS + 30)
    d = cal[SIGNAL_POS]

    panel_full = CrossSectionPanel({"X": make_frame(full, dates=cal)})
    panel_trunc = CrossSectionPanel({"X": make_frame(full[: SIGNAL_POS + 1], dates=cal[: SIGNAL_POS + 1])})

    assert panel_full.scores(d)["X"] == pytest.approx(panel_trunc.scores(d)["X"])


# ===================================================================== #
# 五、日历与面板行为
# ===================================================================== #


def test_calendar_is_union_not_intersection():
    """日历用并集：不能因为某只股票晚上市就把整段样本期砍掉。"""
    d1 = make_dates(70)
    d2 = make_dates(70)[20:]     # 晚上市 20 天
    cal = trading_calendar({
        "A": make_frame(trending(70), dates=d1),
        "B": make_frame(trending(50), dates=d2),
    })
    assert len(cal) == 70
    assert cal[0] == d1[0]


def test_never_listed_symbol_is_excluded_but_kept_in_reasons():
    """晚上市的股票在早期信号日应被剔除，且**仍出现在 reasons 里**（不被静默抹掉）。"""
    n = 100
    d_all = make_dates(n)
    panel = CrossSectionPanel({
        "EARLY": make_frame(trending(n), dates=d_all),
        "LATE": make_frame(trending(n - 80), dates=d_all[80:]),   # 信号日(index 69)之后才上市
    }, calendar=d_all)
    d = d_all[SIGNAL_POS]

    assert panel.scores(d)["EARLY"] is not None
    assert panel.scores(d)["LATE"] is None
    assert "LATE" in panel.reasons(d)
    # 「也许未上市」必须与「停牌」分开报：前者是池子构建问题，后者是交易约束问题
    assert panel.reasons(d)["LATE"] == REASON_NOT_YET_LISTED
    assert set(panel.reasons(d)) == {"EARLY", "LATE"}


def test_late_listed_symbol_is_excluded_for_coverage_not_listing():
    """已上市但历史不够一个窗口 → 报覆盖率不足，不能混成「未上市」。"""
    n = 100
    d_all = make_dates(n)
    panel = CrossSectionPanel({
        "EARLY": make_frame(trending(n), dates=d_all),
        "LATE": make_frame(trending(len(d_all[60:])), dates=d_all[60:]),   # 首行 60 < 信号日 69
    }, calendar=d_all)
    d = d_all[SIGNAL_POS]

    reason = panel.reasons(d)["LATE"]
    assert reason.startswith(REASON_INSUFFICIENT_COVERAGE)


def test_symbol_after_its_last_row_is_reported_separately():
    """末行之后（退市/数据截止）同样不该混成「停牌」。"""
    d_full = make_dates()
    early_exit = make_dates(70)[:60]      # 只到第 59 个位置就没数据了
    panel = CrossSectionPanel({
        "FULL": make_frame(trending(70), dates=d_full),
        "EXITED": make_frame(trending(60), dates=early_exit),
    }, calendar=d_full)
    d = d_full[SIGNAL_POS]

    assert panel.reasons(d)["EXITED"] == REASON_AFTER_LAST_ROW
    assert panel.reasons(d)["FULL"] == REASON_OK


def test_mid_history_gap_is_reported_as_missing_row():
    """中间缺一天（停牌/数据缺口）才是真正的 ``no_row_on_signal_date``。"""
    n = N_DAYS + 10
    frame = make_frame(trending(n))
    frame = _drop_positions(frame, [SIGNAL_POS], n)
    panel = CrossSectionPanel({"X": frame}, calendar=make_dates(n))
    d = make_dates(n)[SIGNAL_POS]

    assert panel.reasons(d)["X"] == REASON_NO_SIGNAL_ROW


def test_exclusion_summary_is_aggregatable():
    n = N_DAYS + 10
    frame = make_frame(trending(n))
    panel = CrossSectionPanel({
        "GOOD": frame,
        "BAD": _drop_positions(frame, [SIGNAL_POS], n),   # 信号日无行（区间未结束）
    }, calendar=make_dates(n))
    dates = make_dates(n)
    summary = panel.exclusion_summary([dates[SIGNAL_POS]])

    key = str(dates[SIGNAL_POS].date())
    assert summary[key][REASON_OK] == 1
    assert summary[key][REASON_NO_SIGNAL_ROW] == 1
    assert sum(summary[key].values()) == 2


def test_config_rejects_skip_not_smaller_than_lookback():
    with pytest.raises(ValueError, match="skip"):
        CrossSectionConfig(lookback=10, skip=10)
    with pytest.raises(ValueError, match="lookback"):
        CrossSectionConfig(lookback=1)
    with pytest.raises(ValueError, match="min_valid_ratio"):
        CrossSectionConfig(min_valid_ratio=0.0)


def test_scores_feed_equal_weight_targets_and_none_is_not_selected():
    """下游只认可用标的：None 不填 0，不会让缺数据的股票排到中间被选中。"""
    scores = {"A": 0.30, "B": None, "C": 0.10, "D": float("nan")}
    weights = equal_weight_targets(scores, top_fraction=0.5, min_assets=1)
    assert set(weights) == {"A"}          # 2 个可用里取 ceil(2*0.5)=1


def test_batch_scores_match_single_calls():
    panel_dates = make_dates()
    prices = {"X": make_frame(trending()), "Y": make_frame(trending(base=20.0))}
    batch = build_scores_by_date(prices, list(panel_dates[65:70]))
    for d in panel_dates[65:70]:
        assert batch[d] == momentum_scores(prices, d)


# ===================================================================== #
# 六、真实数据回归（600809.SH 的复权缺陷）
# ===================================================================== #


@pytest.mark.skipif(not REAL_600809.exists(), reason="真实行情文件不在本机")
def test_real_600809_negative_prices_are_excluded_by_rule():
    """用真实数据确认：减法复权造成的负价被 P2 机械剔除，而不是被当成低价股。

    这条不是「针对 600809 的特殊处理」——规则对全部标的一致，
    只是这只股票恰好留下了负价证据。
    """
    df = pd.read_csv(REAL_600809, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    neg = df[(df[["open", "close", "high", "low"]] <= 0).any(axis=1)]
    assert len(neg) > 100, "真实数据应含大量非正价（已知缺陷），否则本测试失去意义"

    panel = CrossSectionPanel({"600809.SH": df})

    # 污染区间内：至少有一天被剔除，且原因指向价格
    bad_day = neg["date"].iloc[len(neg) // 2]
    reasons = panel.reasons(pd.Timestamp(bad_day))
    assert reasons["600809.SH"] != REASON_OK

    # 污染结束两年后（2019-01-04 之后）应可用
    cal = panel.calendar
    later = cal[cal >= pd.Timestamp("2021-06-01")]
    if len(later) > 70:
        d = later[65]
        score, reason = panel.evaluate(d)["600809.SH"]
        assert reason == REASON_OK, f"{d.date()} 仍被剔除：{reason}"
        assert score is not None and -1.0 < score < 5.0
