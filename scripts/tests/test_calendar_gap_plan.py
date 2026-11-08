"""R6-4 日历缺口预案：缺 2027 不阻塞、拉取失败沿用缓存、缓存坏则失败关闭。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TC = ROOT / "research" / "stock_pick_2026-10-25" / "scripts" / "sp5" / "trade_calendar.py"
spec = importlib.util.spec_from_file_location("tcal", TC)
assert spec and spec.loader
tcal = importlib.util.module_from_spec(spec)
sys.modules["tcal"] = tcal
spec.loader.exec_module(tcal)

REAL_CAL = ROOT / "research" / "stock_pick_2026-10-25" / "forward" / "trade_calendar.json"
DAYS_2026 = [d for d in json.loads(REAL_CAL.read_text(encoding="utf-8"))["dates"]
            if d <= "20261231"]


@pytest.fixture
def env(tmp_path):
    cal = tmp_path / "trade_calendar.json"
    gaps = tmp_path / "calendar_gaps.json"
    tcal.save(DAYS_2026, cal)
    return cal, gaps


def argv_for(cal, gaps, today="20261201"):
    return ["--refresh", "--path", str(cal), "--gaps-path", str(gaps),
            "--today", today]


def test_missing_2027_does_not_block(env):
    cal, gaps = env
    rc, p = tcal.refresh(argv_for(cal, gaps), fetcher=lambda: DAYS_2026)
    assert rc == 0 and p == gaps
    rec = json.loads(gaps.read_text(encoding="utf-8"))
    assert rec["status"] == "OK"
    assert rec["calendar_last"] == "20261231"
    assert "202701" in rec["missing_months"]       # 只记录，不判失败
    assert tcal.last_trading_day("202612", tcal.load(cal)) == "20261231"


def test_fetch_failure_uses_cache_without_blocking(env):
    cal, gaps = env
    before = cal.read_bytes()

    def boom():
        raise tcal.CalendarUnavailable("模拟接口挂了")

    rc, _ = tcal.refresh(argv_for(cal, gaps), fetcher=boom)
    assert rc == 0
    assert cal.read_bytes() == before                 # 绝不写坏/写新日历
    rec = json.loads(gaps.read_text(encoding="utf-8"))
    assert rec["status"] == "FETCH_FAILED_USING_CACHE"
    assert "202612" in rec["note"] and "202701" in rec["missing_months"]


def test_fetch_failure_with_truncated_cache_fails_closed(tmp_path):
    cal = tmp_path / "trade_calendar.json"
    gaps = tmp_path / "calendar_gaps.json"
    truncated = [d for d in DAYS_2026 if d <= "20261127"]   # 止于 11 月月中
    tcal.save(truncated, cal)

    def boom():
        raise tcal.CalendarUnavailable("模拟接口挂了")

    rc, p = tcal.refresh(argv_for(cal, gaps), fetcher=boom)
    assert rc == 1 and p is None
    assert not gaps.exists()                          # 不写「假装成功」的缺口台账


def test_new_calendar_missing_current_month_is_not_written(env):
    """fetch 成功但新日历缺当月 ⇒ 不得 save，沿用缓存（写前校验）。"""
    cal, gaps = env
    before = cal.read_bytes()
    bad = [d for d in DAYS_2026 if d <= "20261127"]
    rc, _ = tcal.refresh(argv_for(cal, gaps), fetcher=lambda: bad)
    assert rc == 0
    assert cal.read_bytes() == before
    assert json.loads(gaps.read_text(encoding="utf-8"))["status"] == "FETCH_FAILED_USING_CACHE"


def test_window_only_needs_2026():
    tcal.assert_window_covered(DAYS_2026, "202612")   # 不抛
    assert tcal.last_trading_day("202612", DAYS_2026) == "20261231"
    assert "202701" in tcal.missing_months(DAYS_2026, "202612")
    with pytest.raises(tcal.CalendarUnavailable):
        tcal.assert_window_covered(DAYS_2026, "202701")


def test_manual_source_is_recorded(tmp_path):
    p = tcal.write_gaps(tmp_path / "gaps.json", status="MANUAL_REQUIRED",
                         days=DAYS_2026, required_ym="202612",
                         note="模拟", source="MANUAL:国务院放假安排+交易所公告")
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert rec["source"].startswith("MANUAL:")


def test_cli_requires_refresh():
    assert tcal._main([]) == 2
