"""`append_lots`（SP5-LOTAPP-1）的判据测试。

规则原文见 `research/stock_pick_2026-10-25/SP5_LOTAPP_PREREG.md` §2。
这里锁的是**不变量**，不是某个价格下的具体手数——手数会随价格变，不变量不会：

  I1 资金守恒：投入 + 残金 == 每只预算 × 只数
  I2 退出条件：残金 < 池内最小一手金额
  I3 只数不变：lots 的长度 == 价格个数（不加只、不减只）
  I4 上限：任一标的不超过等权目标的 1.3 倍
  I5 整手：手数全为非负整数，且实际投入 == 手数 × 一手金额
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dryrun_decision_day import append_lots  # noqa: E402

#: 2026-10-30 的真实四只收盘价（一手 1,570 / 941 / 541 / 777 元）
REAL = [15.70, 9.41, 5.41, 7.77]


def test_invariants_on_real_prices():
    per = 4000.0
    r = append_lots(REAL, per)
    n = len(REAL)
    invested = sum(l * 100 * p for l, p in zip(r["lots"], REAL))
    assert r["invested"] == pytest.approx(invested)
    # I1
    assert r["invested"] + r["cash"] == pytest.approx(per * n)
    # I2
    assert r["cash"] < min(100 * p for p in REAL)
    # I3
    assert len(r["lots"]) == n
    # I4
    assert all(l * 100 * p <= 1.3 * per + 1e-9 for l, p in zip(r["lots"], REAL))
    # I5
    assert all(isinstance(l, int) and l >= 0 for l in r["lots"])


def test_append_beats_naive_floor():
    """追加规则必须严格优于各自取整（否则规则没起作用）。"""
    per = 4000.0
    naive = sum(int(per // (100 * p)) * 100 * p for p in REAL)
    r = append_lots(REAL, per)
    assert r["invested"] > naive
    assert r["adds"] >= 1


def test_cap_can_bind_at_small_budget():
    """5 万档（每只 2,000）会触 1.3× 上限——预登记 P3 在这一档不成立，测试锁住事实。"""
    per = 2000.0
    r = append_lots(REAL, per)
    assert r["cap_blocked"] >= 1
    assert all(l * 100 * p <= 1.3 * per + 1e-9 for l, p in zip(r["lots"], REAL))


def test_residual_below_min_lot_always():
    """对一批预算都要满足 I2：残金 < 最小一手。用真实价格扫一遍。"""
    for per in (1500.0, 2000.0, 3000.0, 4000.0, 6000.0, 8000.0, 20000.0):
        r = append_lots(REAL, per)
        assert r["cash"] < min(100 * p for p in REAL), f"per={per}"
        assert r["cash"] >= 0


def test_single_stock_pool_no_add():
    """只有一只时，"追加"意味着加仓同一只——仍须遵守上限与残金规则。"""
    r = append_lots([15.70], 4000.0)
    assert r["lots"] == [2]
    assert r["cash"] == pytest.approx(4000.0 - 2 * 1570.0)


def test_equal_prices_deterministic():
    """同价同缺口时必须确定性（不能因 set/dict 顺序漂移）。"""
    p = [10.0, 10.0, 10.0]
    a = append_lots(p, 3000.0)
    b = append_lots(p, 3000.0)
    assert a == b
    assert sum(a["lots"]) * 1000.0 + a["cash"] == pytest.approx(9000.0)


def test_cap_multiplier_is_honoured():
    """把上限放宽到 2.0 时投入应不少于 1.3 的情形（上限确实在生效）。"""
    per = 4000.0
    tight = append_lots(REAL, per, cap_mult=1.3)
    loose = append_lots(REAL, per, cap_mult=2.0)
    assert loose["invested"] >= tight["invested"]


def test_overshoot_is_reported():
    """追加会让部分标的超过等权目标，overshoot 必须如实计数（不得谎报 0）。"""
    r = append_lots(REAL, 4000.0)
    manual = sum(1 for l, p in zip(r["lots"], REAL) if l * 100 * p > 4000.0)
    assert r["overshoot"] == manual
