"""`effective_pool` 的自测（合成数据，不依赖任何行情文件）。

为什么放在 scripts/ 而不是 backend/tests/：
`backend/tests/**` 属 A 线所有者文件，B 线不改。这里用可执行的断言脚本承担同一职责，
失败时返回非 0，可被 CI 直接调用。

    python scripts/selftest_effective_pool.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.research.effective_pool import (  # noqa: E402
    PoolGateError, Window, coerce_window, enforce, evaluate_pool,
)

CASES: list[tuple[str, callable]] = []


def case(fn):
    CASES.append((fn.__name__, fn))
    return fn


def make_frame(prices: list[float], start: str = "2020-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(prices), freq="B")
    return pd.DataFrame({
        "date": dates,
        "close": prices,
        "open": prices,
        "high": prices,
        "low": prices,
        "volume": [1e6] * len(prices),
    })


def win(i: int, t0: str, t1: str, o0: str, o1: str) -> Window:
    return Window(i, pd.Timestamp(t0), pd.Timestamp(t1), pd.Timestamp(o0), pd.Timestamp(o1))


@case
def test_coerce_accepts_tuple_and_object():
    w = coerce_window((3, "2020-01-01", "2020-02-01", "2020-02-02", "2020-03-01"))
    assert w.window_index == 3 and w.oos_start == pd.Timestamp("2020-02-02")

    class FakeSpec:
        window_index = 7
        train_start = pd.Timestamp("2020-01-01")
        train_end = pd.Timestamp("2020-02-01")
        oos_start = pd.Timestamp("2020-02-02")
        oos_end = pd.Timestamp("2020-03-01")
        valid = True

    assert coerce_window(FakeSpec()).window_index == 7, "应能直接吃 WindowSpec 形状的对象"


@case
def test_affordable_symbol_is_clean():
    f = {"AAA": make_frame([10.0] * 40)}
    w = [win(0, "2020-01-01", "2020-01-31", "2020-02-01", "2020-02-29")]
    g = evaluate_pool(f, w, cash=5000.0, cut="2020-01-15")
    assert g.g3_passed and g.g2_passed
    assert g.per_symbol["AAA"].clean
    assert g.effective_universe() == ("AAA",)


@case
def test_expensive_symbol_is_absent_in_oos_window():
    """一手 1000×100=10 万 > 资金 5 万，且该标的只出现在样本外窗口 → 缺席。"""
    f = {"AAA": make_frame([1000.0] * 40), "BBB": make_frame([10.0] * 40)}
    w = [win(0, "2020-01-01", "2020-01-31", "2020-02-01", "2020-02-29")]
    g = evaluate_pool(f, w, cash=50_000.0, cut="2020-01-15")
    assert not g.g3_passed
    assert g.predicted_absent == ("AAA",)
    assert g.per_symbol["AAA"].one_lot_max_overall == 100_000.0
    assert "全部" in g.per_symbol["AAA"].reason


@case
def test_cut_separates_is_and_oos_windows():
    """同一只标的：IS 窗口买得起、OOS 窗口买不起 → 不算缺席，算部分阻断。"""
    f = {"AAA": make_frame([50.0] * 20 + [1000.0] * 20)}
    w = [win(0, "2020-01-01", "2020-01-10", "2020-01-13", "2020-01-17"),
         win(1, "2020-01-20", "2020-01-28", "2020-01-29", "2020-02-05")]
    g = evaluate_pool(f, w, cash=50_000.0, cut="2020-01-18")
    v = g.per_symbol["AAA"]
    assert v.n_oos_windows == 1, "只有 w1 是样本外窗口"
    assert v.n_oos_affordable == 0 and v.absent, "样本外一个都买不起 → 缺席"
    assert g.g2_passed is False, "g2 走严格读法：有任一窗口买不起就不通过"


@case
def test_partial_blocking_is_not_absent():
    """价格在窗口之间变化：一个窗口买不起、另一个买得起 → 部分阻断而非缺席。"""
    f = {"AAA": make_frame([50.0] * 10 + [1000.0] * 10 + [50.0] * 10)}
    w = [win(0, "2020-01-01", "2020-01-06", "2020-01-07", "2020-01-10"),
         win(1, "2020-01-16", "2020-01-21", "2020-01-22", "2020-01-27")]
    g = evaluate_pool(f, w, cash=50_000.0, cut=None)
    v = g.per_symbol["AAA"]
    assert v.n_oos_windows == 2 and v.n_oos_affordable == 1
    assert v.partially_blocked and not v.absent
    assert g.g3_passed, "有窗口能建仓就不算结构性缺席"


@case
def test_missing_codes_count_against_g3():
    f = {"AAA": make_frame([10.0] * 40)}
    w = [win(0, "2020-01-01", "2020-01-31", "2020-02-01", "2020-02-29")]
    g = evaluate_pool(f, w, cash=5000.0, expected_codes=["AAA", "ZZZ"], nominal_n=2, cut=None)
    assert g.missing == ("ZZZ",)
    assert not g.g3_passed, "名义池里有、但没数据 → 不能算通过"
    assert "ZZZ" in str(PoolGateError(g)), "错误信息里必须点名缺哪只"


@case
def test_per_symbol_windows_mapping():
    """逐标的分窗：两只标的窗口数不同，不应互相污染。"""
    f = {"AAA": make_frame([10.0] * 60, "2020-01-01"),
         "BBB": make_frame([10.0] * 20, "2020-03-02")}
    w = {"AAA": [win(0, "2020-01-01", "2020-01-31", "2020-02-01", "2020-02-29"),
                 win(1, "2020-03-02", "2020-04-01", "2020-04-02", "2020-04-30")],
         "BBB": [win(0, "2020-03-02", "2020-04-01", "2020-04-02", "2020-04-30")]}
    g = evaluate_pool(f, w, cash=5000.0, cut=None)
    assert g.per_symbol["AAA"].n_windows == 2
    assert g.per_symbol["BBB"].n_windows == 1


@case
def test_enforce_block_raises_warn_does_not():
    f = {"AAA": make_frame([1000.0] * 40)}
    w = [win(0, "2020-01-01", "2020-01-31", "2020-02-01", "2020-02-29")]
    g = evaluate_pool(f, w, cash=50_000.0, cut=None)
    try:
        enforce(g, on_fail="block", block_on="absent")
    except PoolGateError:
        pass
    else:
        raise AssertionError("on_fail=block 时应当抛 PoolGateError")
    assert enforce(g, on_fail="warn") is g, "warn 必须原样返回，不能抛"
    assert enforce(g, on_fail="block", block_on="none") is g, "block_on=none 不该拦"


@case
def test_non_positive_prices_are_ignored():
    """qfq 负价是已知瑕疵，不能拿它算出"买得起"。"""
    f = {"AAA": make_frame([-5.0, 10.0, 10.0, 10.0] + [10.0] * 36)}
    w = [win(0, "2020-01-01", "2020-01-31", "2020-02-01", "2020-02-29")]
    g = evaluate_pool(f, w, cash=2000.0, cut=None)
    assert g.per_symbol["AAA"].one_lot_max_overall == 1000.0, "负价应被剔除，不参与判断"


def main() -> int:
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception:
            failed += 1
            print(f"[FAIL] {name}")
            traceback.print_exc()
    print(f"\n{len(CASES) - failed}/{len(CASES)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
