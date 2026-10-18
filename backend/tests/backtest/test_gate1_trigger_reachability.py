"""`scripts/gate1_trigger_reachability.py` 的契约测试。

这个脚本不产出收益，但它产出**判据层的结论**（「某条触发项能不能触发」）。
所以测试的重点不是覆盖率，而是三件事：

1. 它真的没有碰回测：不 import `run_portfolio_backtest`，报告里也没有任何收益字段。
2. 「OOS 只依赖近端窗口」这条结论是可证伪的：扰动远端不变 → 证明无长程依赖；
   扰动近端**必须**发生变化 → 证明这个比较不是恒真（否则测试是自欺）。
3. 算术口径取最不利侧：单只预算用 OOS 内**最大**持仓数，不能用最小值虚高预算。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "gate1_trigger_reachability.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("gate1_trigger_probe_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


# --------------------------------------------------------------------------- #
# 合成行情（不依赖磁盘数据，测试可在任何机器上跑）
# --------------------------------------------------------------------------- #


def _make_prices(n_symbols: int = 8, start: str = "2018-01-01", end: str = "2024-12-31"):
    """确定性随机漫步：同一 seed 得到同一份行情，不含任何真实标的信息。"""
    dates = pd.bdate_range(start, end)
    rng = np.random.default_rng(20261018)
    codes = [f"60{i:04d}.SH" for i in range(n_symbols - 2)] + ["300001.SZ", "688001.SH"]
    prices = {}
    for i, code in enumerate(codes):
        drift = 0.0004 * (i + 1)
        rets = rng.normal(drift, 0.012, size=len(dates))
        close = 10.0 * np.exp(np.cumsum(rets))
        prices[code] = pd.DataFrame({
            "date": dates,
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "amount": np.full(len(dates), 1e8),
        })
    return prices


@pytest.fixture(scope="module")
def prices():
    return _make_prices()


@pytest.fixture(scope="module")
def calendar(prices):
    return probe.trading_calendar(prices)


# --------------------------------------------------------------------------- #
# 1. 它没有碰回测
# --------------------------------------------------------------------------- #


def test_source_never_calls_backtest_engine():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "run_portfolio_backtest(" not in src, (
        "本脚本必须只做选股层面的比较；一旦调用组合回测就会产出收益数字"
    )


def test_report_contains_no_return_fields():
    report = probe.build_report(_make_prices(6), data_root=Path("synthetic"))
    assert report["will_compute_returns"] is False
    blob = json.dumps(report, ensure_ascii=False)
    for forbidden in ("mean_d", "t_stat", "r_strategy", "r_benchmark", "win_ratio"):
        assert forbidden not in blob, f"报告里出现了收益字段 {forbidden}"


# --------------------------------------------------------------------------- #
# 2. 双创判定按前缀，不按名称/行业
# --------------------------------------------------------------------------- #


def test_gem_symbols_by_code_prefix(prices):
    gem = probe.gem_symbols(prices)
    assert gem == ["300001.SZ", "688001.SH"]
    assert all(not c.startswith(("600", "000")) for c in gem)


# --------------------------------------------------------------------------- #
# 3. 「OOS 只依赖近端窗口」必须既成立又可证伪
# --------------------------------------------------------------------------- #


def test_oos_targets_ignore_distant_history(prices, calendar):
    """扰动 2020-06-30 之前的全部价格 → OOS 目标一个都不该变。

    这是 T1 结构性结论的证据：OOS 决策只看 ``lookback+skip`` 个交易日的近端数据，
    所以「IS 期把某些标的移出候选池」无法穿透到 OOS 判据。
    """
    base = probe.build_targets(prices, calendar)
    keys = probe.oos_keys(base)
    assert keys, "合成数据应能覆盖 OOS 区间"

    perturbed = probe.build_targets(probe.perturb_before(prices, "2020-06-30", 1.10), calendar)
    assert probe.changed_months(base, perturbed, keys) == []


def test_near_window_perturbation_does_change_targets(prices, calendar):
    """正向对照：扰动**窗口内**价格必须改变目标。

    没有这条，「远端扰动无变化」可能只是因为比较函数本身恒返回空——那时上面那条
    测试会永远绿，等于没测。

    注意不能只用一个统一的乘数：同一乘数作用在整段前缀上，会在动量比值里约掉
    （这也是第一版这条测试红掉的原因）。必须**逐标的给不同乘数**，才构成跨截面重排。
    """
    base = probe.build_targets(prices, calendar)
    keys = probe.oos_keys(base)

    cut = pd.Timestamp("2024-11-01")
    scaled = {}
    for i, (code, df) in enumerate(sorted(prices.items())):
        v = df.copy()
        mask = v["date"] >= cut
        factor = 1.0 + 0.08 * i
        for col in ("open", "high", "low", "close"):
            v.loc[mask, col] = v.loc[mask, col] * factor
        scaled[code] = v

    perturbed = probe.build_targets(scaled, calendar)
    assert probe.changed_months(base, perturbed, keys), (
        "扰动近端价格后目标应发生变化；若不变化，说明比较逻辑没有真的在比较"
    )


def test_changed_months_reports_both_sides():
    d1, d2 = pd.Timestamp("2021-01-29"), pd.Timestamp("2021-02-26")
    base = {d1: {"A": 0.5, "B": 0.5}, d2: {"A": 1.0}}
    other = {d1: {"A": 0.5, "C": 0.5}, d2: {"A": 1.0}}
    diff = probe.changed_months(base, other, [d1, d2])
    assert len(diff) == 1
    assert diff[0]["only_in_base"] == ["B"]
    assert diff[0]["only_in_variant"] == ["C"]


# --------------------------------------------------------------------------- #
# 4. 标的可达性：没进过目标 = 不可能翻转符号
# --------------------------------------------------------------------------- #


def test_symbol_never_held_cannot_flip_sign():
    keys = [pd.Timestamp("2021-01-29"), pd.Timestamp("2021-02-26")]
    targets = {keys[0]: {"600000.SH": 1.0}, keys[1]: {"600000.SH": 1.0}}
    info = probe.symbol_reachability(targets, "600809.SH")
    assert info["held_months"] == 0
    assert info["can_flip_sign"] is False


def test_symbol_held_reports_window():
    keys = [pd.Timestamp("2021-01-29"), pd.Timestamp("2021-02-26")]
    targets = {keys[0]: {"600809.SH": 1.0}, keys[1]: {"600809.SH": 0.5, "600000.SH": 0.5}}
    info = probe.symbol_reachability(targets, "600809.SH")
    assert info["held_months"] == 2
    assert info["first_held"] == "2021-01-29"
    assert info["can_flip_sign"] is True


# --------------------------------------------------------------------------- #
# 5. 1 手可负担性：预算必须取最不利侧
# --------------------------------------------------------------------------- #


def _flat_frame(close: float) -> pd.DataFrame:
    dates = pd.bdate_range("2021-01-04", "2021-12-31")
    return pd.DataFrame({
        "date": dates,
        "open": close, "high": close, "low": close, "close": close, "amount": 1e8,
    })


def test_lot_affordability_uses_smallest_budget():
    frame = _flat_frame(1_000.0)          # 1 手 = 100,000 元
    ok = probe.lot_affordability(
        frame, initial_cash=1_000_000.0, n_names=10, start="2021-01-01", end="2024-12-31"
    )
    assert ok["budget_per_name"] == pytest.approx(100_000.0)
    assert ok["affordable_always"] is True

    tight = probe.lot_affordability(
        frame, initial_cash=1_000_000.0, n_names=20, start="2021-01-01", end="2024-12-31"
    )
    assert tight["budget_per_name"] == pytest.approx(50_000.0)
    assert tight["affordable_never"] is True


def test_lot_affordability_empty_window_is_reported_not_crashed():
    frame = _flat_frame(10.0)
    out = probe.lot_affordability(
        frame, initial_cash=1_000_000.0, n_names=10, start="2019-01-01", end="2019-12-31"
    )
    assert out["ok"] is False


# --------------------------------------------------------------------------- #
# 6. 成本档：只记录参数差，不推测收益
# --------------------------------------------------------------------------- #


def test_fee_delta_flags_the_three_changed_fields():
    delta = probe.fee_preset_delta(probe.gate1_run.MAIN_FEE, probe.gate1_run.HIGH_FEE)
    assert delta["changed"] == ["commission_rate", "slippage_buy", "slippage_sell"]
    # 印花税/过户费是市场统一规则，两档必须相同；不同就说明预设被改坏了
    assert "stamp_duty_rate" in delta["same"]
    assert "transfer_fee_rate" in delta["same"]
    assert delta["high"]["slippage_buy"] > delta["main"]["slippage_buy"]


def test_cost_tier_is_marked_as_requiring_a_run():
    report = probe.build_report(_make_prices(6), data_root=Path("synthetic"))
    assert report["T3_cost_tier"]["requires_run"] is True


# --------------------------------------------------------------------------- #
# 7. 落盘
# --------------------------------------------------------------------------- #


def test_write_report_writes_utf8_json(tmp_path):
    report = probe.build_report(_make_prices(6), data_root=Path("synthetic"))
    path = probe.write_report(report, tmp_path)
    assert path.name == "gate1_trigger_reachability.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["mode"] == "trigger-reachability"
    assert loaded["will_compute_returns"] is False
