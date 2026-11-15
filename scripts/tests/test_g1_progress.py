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


# ============================================================================
# R8 §1 / D3 严格口径（2026-11-15 追加；上方历史口径测试原文一律不改）
# ============================================================================

import json  # noqa: E402


def _state(chain, hb, *, hb_ok=True, sr_ok=True, hb_day="2026-11-13T17:14:24",
           errors=None, old_machine_days=()):
    """构造 D3 离线 state。独立构造，不调用被测函数。"""
    return {
        "host": "/opt/quant",
        "chain_days": sorted(set(chain) | set(old_machine_days)),
        "hb_days": sorted(set(hb) | set(old_machine_days)),
        "heartbeat": {"ts": hb_day, "ok": hb_ok, "mode": "NOT_DUE",
                      "stages": ["update=0:关键"]},
        "stage_results": {"ts": hb_day, "ok": sr_ok, "stage_note": "",
                          "stages": ["update=0:关键"]},
        "errors": list(errors or []),
    }


D3_DAYS = ["20261112", "20261113"]


def test_d3_start_and_decision_day_are_frozen_by_calendar():
    assert g.START == "20261112" and g.DECISION_DAY == "20261125"
    # 独立算术：D 起第 10 个交易日必须恰为 11-25（不按星期几猜）
    assert g.decision_day(g.official_days()) == "20261125"


def test_d3_start_cannot_be_moved_back(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(_state(D3_DAYS, D3_DAYS)), encoding="utf-8")
    assert g.main(["--state-file", str(f), "--today", "20261115",
                   "--start", "20261103"]) == 2


def test_d3_conditional_decision_day(tmp_path, capsys):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(_state(D3_DAYS, D3_DAYS)), encoding="utf-8")
    assert g.main(["--state-file", str(f), "--today", "20261115"]) == 0
    out = capsys.readouterr().out
    assert "若此后全绿，则 2026-11-25 收盘后判" in out
    assert "G1 = 2/10" in out
    assert "D3 严格口径" in out
    assert "LEGACY" not in out


def test_d3_red_day_resets_and_gives_no_new_date(tmp_path, capsys):
    # 11-13 心跳行缺失 ⇒ 红日 ⇒ 不给判定日
    f = tmp_path / "s.json"
    f.write_text(json.dumps(_state(D3_DAYS, ["20261112"])), encoding="utf-8")
    assert g.main(["--state-file", str(f), "--today", "20261115"]) == 0
    out = capsys.readouterr().out
    assert "2026-11-25 收盘后判" not in out
    assert "不预先给新日期" in out


def test_d3_artifact_failure_marks_day_red(tmp_path, capsys):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(_state(D3_DAYS, D3_DAYS, sr_ok=False)), encoding="utf-8")
    assert g.main(["--state-file", str(f), "--today", "20261115"]) == 0
    out = capsys.readouterr().out
    assert "产物校验不通过" in out and "该日判红" in out
    assert "2026-11-25 收盘后判" not in out


def test_d3_missing_artifact_fails_closed(tmp_path):
    f = tmp_path / "s.json"
    s = _state(D3_DAYS, D3_DAYS)
    s["errors"] = ["stage_results.json:FileNotFoundError"]
    f.write_text(json.dumps(s), encoding="utf-8")
    assert g.main(["--state-file", str(f), "--today", "20261115"]) == 2


def test_d3_bad_artifact_ts_fails_closed(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(_state(D3_DAYS, D3_DAYS, hb_day="not-a-date")),
                 encoding="utf-8")
    assert g.main(["--state-file", str(f), "--today", "20261115"]) == 2


def test_d3_never_merges_old_machine_days():
    # 旧机 11-03~11-11 即使出现在日志里，也**不得**进入严格口径的全绿集合
    old = ["20261103", "20261104", "20261105", "20261106",
           "20261109", "20261110", "20261111"]
    good, _ = g.build_days(_state(D3_DAYS, D3_DAYS, old_machine_days=old),
                           g.official_days(), g.START)
    assert all(d not in good for d in old)
    assert sorted(good) == D3_DAYS


def test_d3_state_has_no_days_fails_closed(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(_state([], [])), encoding="utf-8")
    assert g.main(["--state-file", str(f), "--today", "20261115"]) == 2


def test_legacy_offline_mode_is_explicitly_labelled(tmp_path, capsys):
    f = tmp_path / "j.log"
    f.write_text(JOURNAL, encoding="utf-8")
    assert g.main(["--journal-file", str(f), "--today", "20261105"]) == 0
    assert "LEGACY_HISTORICAL_ONLY" in capsys.readouterr().out
