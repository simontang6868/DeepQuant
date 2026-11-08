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


def _isolate_manifest(monkeypatch, ok=True, diffs=None):
    """C11 依赖真实部署清单；单测里注入受控结果，避免测试随部署状态漂移。"""
    monkeypatch.setattr(pf.dm, "load_manifest",
                        lambda *a, **k: {"commit": "testcommit", "files": {"x.py": "0" * 64}})
    monkeypatch.setattr(pf.dm, "verify_local",
                        lambda rec: (ok, "注入的校验结果", diffs or []))
    monkeypatch.setattr(pf.dm, "verify_remote",
                        lambda rec: (ok, "注入的云端结果", diffs or []))


@pytest.fixture(autouse=True)
def _manifest_isolated(monkeypatch):
    """默认隔离真实部署清单：否则「期望 rc=1」的用例可能只是被陈旧的
    manifest 碰巧判红，成了因错误原因通过的假绿。"""
    _isolate_manifest(monkeypatch)


def test_nominal_preflight_passes_with_one_pending(monkeypatch):
    """11-30 预检：静态项全 PASS，快照时点与云端一致性必须 PENDING（不能提前当 PASS）。"""
    _isolate_manifest(monkeypatch)
    rows, n_bad = pf.check(pf.argparse.Namespace(
        today="20261130", data_last=None, calendar=None, cloud=False))
    assert n_bad == 0
    assert sum(1 for _, st, _ in rows if st == pf.PEND) == 2
    assert all(st != pf.BAD for _, st, _ in rows)


def test_next_day_late_run_still_resolves_to_1130(monkeypatch):
    """12-01 补跑仍应指向 11-30（gap=1 ≤ 决策窗口），不是 12-01。"""
    _isolate_manifest(monkeypatch)
    rows, n_bad = pf.check(pf.argparse.Namespace(
        today="20261201", data_last="20261130", calendar=None, cloud=False))
    assert n_bad == 0
    assert any("20261130" in why for _, _, why in rows)


def test_c11a_deploy_drift_is_fail(monkeypatch):
    """部署文件与 manifest 不一致 ⇒ 决策日预检必须 FAIL。"""
    _isolate_manifest(monkeypatch, ok=False, diffs=["改 scripts/x.py"])
    _, n_bad = pf.check(pf.argparse.Namespace(
        today="20261130", data_last=None, calendar=None, cloud=False))
    assert n_bad >= 1


def test_c11b_cloud_check_is_real(monkeypatch):
    """--cloud 时才实检云端；不一致判 FAIL，离线时不许假装 PASS。"""
    _isolate_manifest(monkeypatch, ok=False, diffs=["云端缺 scripts/y.py"])
    rows, n_bad = pf.check(pf.argparse.Namespace(
        today="20261130", data_last=None, calendar=None, cloud=True))
    assert n_bad >= 1
    assert any(n.startswith("C11b") and st == pf.BAD for n, st, _ in rows)
