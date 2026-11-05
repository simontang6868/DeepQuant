"""`scripts/g1_progress.py` 的判据测试。

核心是**失败关闭**与「连续」的语义：
  日志为空 / 拿不到日志 ⇒ FAIL，不得显示「0/10 继续观察」；
  中间漏一天就重新起算，不能跨断点累加。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import g1_progress as g  # noqa: E402

DAYS = ["20261103", "20261104", "20261105", "20261106", "20261109",
        "20261110", "20261111", "20261112", "20261113", "20261116"]

JOURNAL = """\
2026-11-03T17:15:13+0800 vm deepquant-daily[1]: === daily_chain 结束 mode=NOT_DUE ok=1  ===
2026-11-03T17:15:11+0800 vm deepquant-daily[1]: [heartbeat] NOT_DUE ok=True decision=None
2026-11-04T17:16:29+0800 vm deepquant-daily[2]: === daily_chain 结束 mode=NOT_DUE ok=1  ===
2026-11-04T17:16:28+0800 vm deepquant-daily[2]: [heartbeat] NOT_DUE ok=True decision=None
"""


def test_parse_extracts_both_chain_and_heartbeat():
    chain, hb = g.parse_journal(JOURNAL)
    assert chain == {"20261103", "20261104"}
    assert hb == {"20261103", "20261104"}


def test_missing_heartbeat_day_is_not_good():
    text = JOURNAL + "2026-11-05T17:14:07+0800 vm deepquant-daily[3]: === daily_chain 结束 mode=NOT_DUE ok=1  ===\n"
    chain, hb = g.parse_journal(text)
    assert "20261105" in chain and "20261105" not in hb
    k, seg, broken = g.count_streak(DAYS, chain & hb, "20261105", start="20261103")
    # 最新交易日 11-05 缺心跳 ⇒ 当前连续段从今天算起就是 0，
    # 不能跳过它去数 03/04（那样会把一次漏跑藏起来）。
    assert k == 0 and broken == "20261105" and seg == []


def test_gap_breaks_streak_and_cannot_be_added_across():
    good = set(DAYS) - {"20261109"}
    k, seg, broken = g.count_streak(DAYS, good, "20261116", start="20261103")
    # 09 漏跑后重新起算：10,11,12,13,16 共 5 天，断点仍是 09。
    assert k == 5 and broken == "20261109"
    assert seg == ["20261110", "20261111", "20261112", "20261113", "20261116"]


def test_full_ten_passes():
    k, seg, broken = g.count_streak(DAYS, set(DAYS), "20261116", start="20261103")
    assert k == 10 and broken is None and len(seg) == 10


def test_before_1710_today_is_not_counted_as_missed():
    upto, note = g.effective_upto("20261105", dt.datetime(2026, 11, 5, 12, 0), DAYS)
    assert upto == "20261104" and "未到" in note


def test_after_1710_today_counts():
    upto, note = g.effective_upto("20261105", dt.datetime(2026, 11, 5, 20, 0), DAYS)
    assert upto == "20261105" and note == ""


def test_empty_journal_fails_closed(tmp_path):
    f = tmp_path / "empty.log"
    f.write_text("", encoding="utf-8")
    assert g.main(["--journal-file", str(f), "--today", "20261105"]) == 2


def test_offline_two_day_run_reports_2_of_10(tmp_path):
    f = tmp_path / "j.log"
    f.write_text(JOURNAL, encoding="utf-8")
    assert g.main(["--journal-file", str(f), "--today", "20261105"]) == 0


def test_calendar_is_required():
    days = g.official_days()
    assert "20261130" in days and "20261116" in days
