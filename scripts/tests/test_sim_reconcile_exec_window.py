"""U16-EXEC-2 验收第 9 条：created_at 时间窗（独立测试，不改既有对账用例）。"""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import sim_reconcile as sr  # noqa: E402


def test_exec_timestamps_on_time():
    want = "2026-12-01T09:35:00+08:00"
    rows = [{"created_at": "2026-12-01T09:35:12+08:00"},
            {"created_at": "2026-12-01T09:34:58+08:00"}]
    ok, problems = sr.check_exec_timestamps(rows, intended=want, window_min=10)
    assert ok and not problems


def test_exec_timestamps_out_of_window():
    rows = [{"created_at": "2026-12-01T09:46:00+08:00"}]  # 11min > 10
    ok, problems = sr.check_exec_timestamps(rows, window_min=10)
    assert not ok and any("SKEW" in p for p in problems)


def test_exec_timestamps_missing_fails_closed():
    ok, problems = sr.check_exec_timestamps([{"status": "3"}], window_min=10)
    assert not ok and any("MISSING" in p for p in problems)


def test_exec_timestamps_empty_fails_closed():
    ok, problems = sr.check_exec_timestamps([], window_min=10)
    assert not ok and problems == ["created_at:NO_INPUTS"]


def test_reconcile_window_injects_hard_fail():
    truth, local = sr.selfcheck_fixture()
    # 给外部回报一个超窗 created_at
    truth["execution_reports"][0]["created_at"] = "2026-12-01T10:00:00+08:00"
    res = sr.reconcile(truth, local, exec_window_min=10)
    assert res["decision"] == "FAIL"
    assert any("created_at_window" in json_str(d) for d in res["differences"]
               for json_str in [lambda x: str(x)])


def test_reconcile_window_zero_keeps_legacy():
    truth, local = sr.selfcheck_fixture()
    res = sr.reconcile(truth, local, exec_window_min=0)
    assert res["decision"] != "FAIL"
