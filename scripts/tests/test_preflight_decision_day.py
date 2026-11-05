"""`scripts/preflight_decision_day.py` 的判据测试。

重点是**失败必须真红**：日历被改、数据末日没覆盖月末，都要 rc=1。
"检查器全绿"若只是因为判据写宽了，比没有检查器更危险。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import preflight_decision_day as pf  # noqa: E402

CAL = pf.FWD / "trade_calendar.json"


def official() -> list[str]:
    return [str(x) for x in json.loads(CAL.read_text(encoding="utf-8"))["dates"]]


def test_timer_covers_trading_monday():
    ok, why = pf.timer_covers("20261130", official())
    assert ok and "周一" in why


def test_timer_does_not_cover_weekend():
    ok, why = pf.timer_covers("20261128", official())   # 周六
    assert not ok and "周末" in why


def test_timer_does_not_cover_holiday():
    """周一但不是交易日 ⇒ timer 会触发，但 is_due 不会 DUE；不能算覆盖。"""
    ok, _ = pf.timer_covers("20261005", official())     # 国庆假期里的周一
    assert not ok


def test_calendar_digest_matches_payload():
    payload = json.loads(CAL.read_text(encoding="utf-8"))
    assert pf.calendar_digest(payload) == payload["sha256_of_dates"]


def test_tampered_calendar_is_rejected(tmp_path):
    """真改行为：改最后一个交易日但不动 sha 字段 ⇒ 自校验必须抓到。"""
    payload = json.loads(CAL.read_text(encoding="utf-8"))
    payload["dates"][-1] = "20261230"
    bad = tmp_path / "cal.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert pf.main(["--calendar", str(bad)]) == 1


def test_data_last_not_covering_month_end_is_fail():
    assert pf.main(["--today", "20261130", "--data-last", "20261127"]) == 1


def test_nominal_preflight_passes_with_one_pending():
    """11-30 预检：静态项全 PASS，快照时点必须 PENDING（不能提前当 PASS）。"""
    rows, n_bad = pf.check(pf.argparse.Namespace(
        today="20261130", data_last=None, calendar=None))
    assert n_bad == 0
    assert any(st == pf.PEND for _, st, _ in rows)
    assert all(st != pf.BAD for _, st, _ in rows)


def test_next_day_late_run_still_resolves_to_1130():
    """12-01 补跑仍应指向 11-30（gap=1 ≤ 决策窗口），不是 12-01。"""
    rows, n_bad = pf.check(pf.argparse.Namespace(
        today="20261201", data_last="20261130", calendar=None))
    assert n_bad == 0
    assert any("20261130" in why for _, _, why in rows)
