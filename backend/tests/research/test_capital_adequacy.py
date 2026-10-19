"""`research.capital_adequacy` 的契约测试。

背景：`docs/OOS_VERDICT_2026-10-19.md` §5 发现 10 万本金买不起一手茅台（2021 年起一手 13.25 万），
导致该标的在 walk-forward 里静默消失。本模块把"这笔钱在这个池子上是否成立"变成可计算的函数。

这里钉住四件事：
1. **判据边界**：`strict` 用窗口最高价、`lenient` 用最低价，恰好等于现金时算买得起；
2. **金额可复算**：`raise_cash` 的上取整公式与 `both` 的锚点公式，数字对不上就是报告对不上；
3. **默认行为不变**：`as_is` 必须原样返回资金与标的，一个都不许动；
4. **失败显式**：空池、缺列、全非正价都要抛错，不许静默返回"0 只可建仓"。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.research.capital_adequacy import (
    CapitalPlan,
    SymbolCost,
    _anchor_value,
    plan_as_is,
    plan_both,
    plan_drop_symbols,
    plan_raise_cash,
    resolve,
    symbol_cost,
)


def _sc(code: str, lo: float, hi: float, lot: int = 100) -> SymbolCost:
    """直接构造成本对象（价格按每股给，一手 = ×lot）。"""
    return SymbolCost(code=code, rows=10, price_min=lo, price_max=hi, lot_size=lot)


# --------------------------------------------------------------------------- #
# 1. symbol_cost：从价格序列提取
# --------------------------------------------------------------------------- #


def test_symbol_cost_reads_min_and_max():
    frame = pd.DataFrame({"close": [10.0, 12.5, 8.0]})
    sc = symbol_cost("600000.SH", frame)
    assert sc.one_lot_min == pytest.approx(800.0)
    assert sc.one_lot_max == pytest.approx(1250.0)
    assert sc.rows == 3


def test_symbol_cost_ignores_nonpositive_prices():
    """qfq 负价是已知瑕疵，不能拿来算成本，也不能因此判"买得起"。"""
    frame = pd.DataFrame({"close": [10.0, -2.07, 0.0, 20.0]})
    sc = symbol_cost("300750.SZ", frame)
    assert sc.one_lot_min == pytest.approx(1000.0)
    assert sc.one_lot_max == pytest.approx(2000.0)
    assert sc.rows == 2


def test_symbol_cost_raises_on_missing_column():
    with pytest.raises(KeyError, match="close"):
        symbol_cost("600000.SH", pd.DataFrame({"px": [1.0]}))


def test_symbol_cost_raises_when_no_positive_price():
    """全非正 → 抛错。静默返回"成本 0"会让买不起的标的看起来免费。"""
    with pytest.raises(ValueError, match="正价格"):
        symbol_cost("600000.SH", pd.DataFrame({"close": [-1.0, 0.0]}))


def test_symbol_cost_respects_lot_size():
    sc = symbol_cost("688981.SH", pd.DataFrame({"close": [50.0]}), lot_size=200)
    assert sc.one_lot_max == pytest.approx(10_000.0)


# --------------------------------------------------------------------------- #
# 2. 判据边界：strict 用最高价，lenient 用最低价
# --------------------------------------------------------------------------- #


def test_strict_and_lenient_disagree_on_same_symbol():
    """区间内价格跨度大时，两档判据给出相反答案 —— 这正是断点年份差一年的原因。"""
    sc = _sc("600519.SH", lo=700.0, hi=1_500.0)   # 一手 7 万 ~ 15 万
    assert sc.affordable(100_000, basis="strict") is False   # 1,500×100=15万 > 10万
    assert sc.affordable(100_000, basis="lenient") is True   # 700×100=7万 ≤ 10万


def test_affordable_is_inclusive_at_exact_boundary():
    """恰好等于现金时算买得起 —— 否则"刚好够"会被判成买不起，凭空缩池。"""
    sc = _sc("600000.SH", lo=1_000.0, hi=1_000.0)
    assert sc.affordable(100_000.0) is True


def test_affordable_defaults_to_strict():
    sc = _sc("600519.SH", lo=700.0, hi=1_500.0)
    assert sc.affordable(100_000) is False


# --------------------------------------------------------------------------- #
# 3. as_is：不改任何东西（这是"对照组"的全部意义）
# --------------------------------------------------------------------------- #


def test_as_is_keeps_cash_and_universe_verbatim():
    costs = {"600519.SH": _sc("600519.SH", 1_000, 1_500), "000001.SZ": _sc("000001.SZ", 10, 12)}
    p = plan_as_is(costs, 100_000)
    assert p.mode == "as_is"
    assert p.initial_cash == 100_000
    assert p.universe == ("600519.SH", "000001.SZ")
    assert p.dropped == {}
    assert p.n_effective == 2


def test_as_is_does_not_filter_even_when_unaffordable():
    """对照组必须保留"买不起"的标的，否则就失去了对照的作用。"""
    costs = {"600519.SH": _sc("600519.SH", 1_000, 1_500)}
    p = plan_as_is(costs, 100_000)
    assert p.universe == ("600519.SH",)


# --------------------------------------------------------------------------- #
# 4. raise_cash：金额公式与保底
# --------------------------------------------------------------------------- #


def test_raise_cash_matches_frozen_number():
    """茅台 2021 一手 132,506 元 → ×1.1=145,756.6 → 上取整 150,000。

    这个数字出现在 `docs/CAPITAL_POLICY_AB_2026-10-19.md` 的 raise_cash 口径里，
    公式一改报告就对不上，所以逐位钉住。
    """
    costs = {"600519.SH": _sc("600519.SH", 1_325.06, 1_325.06)}
    p = plan_raise_cash(costs, 100_000)
    assert p.mode == "raise_cash"
    assert p.initial_cash == 150_000.0


def test_raise_cash_covers_most_expensive_symbol():
    costs = {"A": _sc("A", 10, 20), "B": _sc("B", 30, 90)}
    p = plan_raise_cash(costs, 50_000, safety_factor=1.0, round_to=1_000.0)
    assert p.initial_cash == 9_000.0        # 90×100=9,000
    assert all(s.affordable(p.initial_cash) for s in p.costs.values())


def test_raise_cash_keeps_all_symbols():
    costs = {"A": _sc("A", 10, 20), "B": _sc("B", 30, 90)}
    p = plan_raise_cash(costs, 50_000)
    assert p.universe == ("A", "B")
    assert p.dropped == {}


def test_raise_cash_raises_on_empty_pool():
    with pytest.raises(ValueError, match="空标的"):
        plan_raise_cash({}, 100_000)


def test_raise_cash_note_records_the_arithmetic():
    """note 必须能让人不复算就看懂资金怎么来的。"""
    costs = {"600519.SH": _sc("600519.SH", 1_325.06, 1_325.06)}
    p = plan_raise_cash(costs, 100_000)
    assert "132,506" in p.note
    assert "150,000" in p.note


# --------------------------------------------------------------------------- #
# 5. drop_symbols：剔除与原因
# --------------------------------------------------------------------------- #


def test_drop_symbols_removes_only_unaffordable():
    costs = {
        "600519.SH": _sc("600519.SH", 1_000, 1_500),   # 一手 15 万，超
        "000001.SZ": _sc("000001.SZ", 10, 12),         # 一手 1,200，可
    }
    p = plan_drop_symbols(costs, 100_000)
    assert p.universe == ("000001.SZ",)
    assert list(p.dropped) == ["600519.SH"]
    assert "150,000" in p.dropped["600519.SH"]


def test_drop_symbols_basis_changes_result():
    """同一只标的，lenient 判据下留下、strict 判据下剔除。"""
    costs = {"600519.SH": _sc("600519.SH", 700, 1_500)}
    assert plan_drop_symbols(costs, 100_000, basis="strict").universe == ()
    assert plan_drop_symbols(costs, 100_000, basis="lenient").universe == ("600519.SH",)


def test_drop_symbols_keeps_cash_unchanged():
    costs = {"600519.SH": _sc("600519.SH", 1_000, 1_500)}
    p = plan_drop_symbols(costs, 100_000)
    assert p.initial_cash == 100_000


def test_drop_symbols_note_counts_input_and_output():
    costs = {"A": _sc("A", 10, 20), "B": _sc("B", 1_000, 1_500)}
    p = plan_drop_symbols(costs, 100_000)
    assert "2 只输入" in p.note and "剔除 1 只" in p.note


# --------------------------------------------------------------------------- #
# 6. both：锚点 + 保底
# --------------------------------------------------------------------------- #


def test_both_never_drops_below_base_cash():
    """保底是硬约束：锚点算出的资金低于基准时必须取基准。

    否则"资金变少"这个与本题无关的变量会混进来，让更多标的买不起，方向恰好相反。
    """
    costs = {"A": _sc("A", 1, 2), "B": _sc("B", 1, 3), "C": _sc("C", 1, 4)}
    p = plan_both(costs, 100_000, anchor="median")
    assert p.initial_cash == 100_000
    assert "保底" in p.note


def test_both_raises_cash_when_anchor_exceeds_base():
    costs = {"A": _sc("A", 100, 200), "B": _sc("B", 300, 400), "C": _sc("C", 500, 600)}
    p = plan_both(costs, 10_000, anchor="max", safety_factor=1.0, round_to=1_000.0)
    assert p.initial_cash == 60_000.0      # 600×100
    assert p.universe == ("A", "B", "C")


def test_both_drops_outlier_above_anchor():
    """p75 锚点只剔真正的离群者，不把整池拉高到最贵那只的水平。"""
    costs = {
        "A": _sc("A", 10, 20), "B": _sc("B", 20, 30),
        "C": _sc("C", 30, 40), "D": _sc("D", 1_000, 1_500),
    }
    p = plan_both(costs, 10_000, anchor="p75", safety_factor=1.0, round_to=1_000.0)
    assert "D" in p.dropped
    assert p.initial_cash < 150_000


def test_both_equals_drop_symbols_when_anchor_below_base():
    """6 只池的真实情形：锚点 4 万 < 保底 10 万 → both 与 drop_symbols 等价。"""
    costs = {
        "600519.SH": _sc("600519.SH", 1_000, 1_500),
        "000001.SZ": _sc("000001.SZ", 10, 12),
        "601318.SH": _sc("601318.SH", 40, 55),
    }
    both = plan_both(costs, 100_000)
    drop = plan_drop_symbols(costs, 100_000)
    assert both.initial_cash == drop.initial_cash
    assert both.universe == drop.universe


def test_both_raises_on_empty_pool():
    with pytest.raises(ValueError, match="空标的"):
        plan_both({}, 100_000)


# --------------------------------------------------------------------------- #
# 7. 锚点
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "anchor,expected",
    [("min", 1.0), ("median", 3.0), ("max", 7.0)],
)
def test_anchor_value_basic(anchor, expected):
    assert _anchor_value([1.0, 3.0, 7.0], anchor) == pytest.approx(expected)


def test_anchor_p75_interpolates():
    """p75 用线性插值：4 个点时落在第 3、4 个之间。"""
    assert _anchor_value([1.0, 2.0, 3.0, 4.0], "p75") == pytest.approx(3.25)


def test_anchor_rejects_unknown_name():
    with pytest.raises(ValueError, match="未知锚点"):
        _anchor_value([1.0], "p90")


def test_anchor_rejects_empty_sequence():
    with pytest.raises(ValueError, match="空序列"):
        _anchor_value([], "median")


# --------------------------------------------------------------------------- #
# 8. resolve：统一入口
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["as_is", "raise_cash", "drop_symbols", "both"])
def test_resolve_dispatches_all_modes(mode):
    costs = {"A": _sc("A", 10, 20)}
    plan = resolve(mode, costs, 100_000)
    assert isinstance(plan, CapitalPlan)
    assert plan.mode == mode


def test_resolve_rejects_unknown_mode():
    with pytest.raises(ValueError, match="未知口径"):
        resolve("raise_money", {"A": _sc("A", 1, 2)}, 100_000)


# --------------------------------------------------------------------------- #
# 9. as_dict：报告消费的字段必须齐
# --------------------------------------------------------------------------- #


def test_as_dict_carries_both_lot_costs_and_dropped_reasons():
    costs = {
        "600519.SH": _sc("600519.SH", 1_000, 1_500),
        "000001.SZ": _sc("000001.SZ", 10, 12),
    }
    d = plan_drop_symbols(costs, 100_000).as_dict()
    assert d["mode"] == "drop_symbols"
    assert d["n_effective"] == 1
    assert d["one_lot_max_by_symbol"]["600519.SH"] == pytest.approx(150_000.0)
    assert d["one_lot_min_by_symbol"]["600519.SH"] == pytest.approx(100_000.0)
    assert "600519.SH" in d["dropped"]


def test_price_basis_is_recorded_but_not_validated():
    """价格口径由调用方声明；模块不校验，但必须原样带出（qfq 是成本下界这件事要能追溯）。"""
    costs = {"A": _sc("A", 1, 2)}
    plan = resolve("as_is", costs, 100_000, price_basis="qfq")
    assert plan.price_basis == "qfq"
    assert plan.as_dict()["price_basis"] == "qfq"
