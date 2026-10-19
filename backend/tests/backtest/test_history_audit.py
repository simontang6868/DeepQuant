"""`scripts/history_audit.py` 的少量单元测试。

只测纯函数：这个脚本是只读分析工具，没有控制流值得测；值得钉死的是
**口径**——尤其「非正价按四价判还是按 close 判」，两种口径会得出不同的行数。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "history_audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("history_audit_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ha = _load()


def _row(date: str, o: float, c: float, h: float, low: float) -> dict[str, str]:
    return {"date": date, "open": str(o), "close": str(c), "high": str(h), "low": str(low)}


def test_nonpositive_uses_four_prices_not_close_only():
    """`000568.SZ` 2017-01-16 的真实形态：close=0.75（正）但 low=-0.01。

    若只用 close，这一行会被判成正常——预登记缺陷 1 记的是「行」，必须同口径。
    """
    rows = [_row("2017-01-16", 0.76, 0.75, 0.80, -0.01)]
    assert ha.nonpositive_rows(rows) == ["2017-01-16"]


def test_nonpositive_ignores_clean_rows_and_bad_values():
    rows = [
        _row("2017-01-03", 10.0, 10.2, 10.5, 9.9),
        {"date": "2017-01-04", "open": "", "close": "10.1", "high": "10.2", "low": "10.0"},
        _row("2017-01-05", 0.0, 10.0, 10.0, 10.0),
    ]
    assert ha.nonpositive_rows(rows) == ["2017-01-05"]


def test_lot_cost_respects_window():
    rows = [
        _row("2020-12-31", 1.0, 100.0, 100.0, 100.0),
        _row("2021-01-04", 1.0, 10.0, 10.0, 10.0),
        _row("2024-12-31", 1.0, 20.0, 20.0, 20.0),
        _row("2025-01-02", 1.0, 999.0, 999.0, 999.0),
    ]
    lo, hi = ha.lot_cost(rows, "2021-01-01", "2024-12-31")
    assert (lo, hi) == (1000.0, 2000.0), "窗口外的 100 元与 999 元都不得进入"


def test_fee_ordering_puts_broker_reference_first():
    """D1 的机械化复现：`simulation_placeholder` 不是最低成本档。"""
    r = ha.check_fee_ordering()
    assert r["cheapest"] == "broker_reference_common_2024"
    assert r["simulation_is_cheapest"] is False
    tiers = r["tiers"]
    assert tiers["broker_reference_common_2024"]["round_trip_cost"] < \
        tiers["simulation_placeholder"]["round_trip_cost"]


@pytest.mark.skipif(
    not ha.HIST_SUMMARY.exists() or not ha.V3_ROOT.exists(),
    reason=(
        "审计对象是本机历史产物（b50_dist / b50_v3），未入库；"
        "干净环境无对象可审。需要时用 DEEPQUANT_HIST_SUMMARY / "
        "DEEPQUANT_HIST_V3_ROOT 指向产物目录后再跑。"
    ),
)
def test_audit_does_not_compute_returns():
    """审计脚本的自我约束：输出里必须显式声明「不产生收益数字」。"""
    report = ha.run()
    assert report["will_compute_returns"] is False
    assert report["D5_pool_split"]["in_v3_not_in_v2"] == ["000568.SZ", "600809.SH"]
    assert report["D2_price_limit"]["mentions_price_limit"] is False
