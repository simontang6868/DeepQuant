"""heartbeat_alert 每日状态邮件（死信开关）的行为测试。

钉死四条：
1. 默认（未设 DEEPQUANT_MAIL_STATUS）不发邮件，行为与旧版一致；
2. 开启后：正常心跳发【正常】邮件、告警条件发【告警】邮件；
3. **正常结论 + 邮件发送失败 ⇒ rc=5**——否则人会以为每天有信；
4. 告警结论 + 邮件发送失败不吞告警（rc 仍非 0）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
_SPEC = _REPO / "deploy" / "bin" / "heartbeat_alert.py"
_spec = importlib.util.spec_from_file_location("heartbeat_alert", _SPEC)
ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ha)

SMTP_ENV = {
    "DEEPQUANT_SMTP_HOST": "smtp.qq.com",
    "DEEPQUANT_SMTP_PORT": "465",
    "DEEPQUANT_SMTP_USER": "sender@qq.com",
    "DEEPQUANT_SMTP_PASS": "authcode",
    "DEEPQUANT_MAIL_TO": "user@qq.com",
}


def _write_hb(tmp_path: Path, ok: bool = True, day: str = "20261103") -> Path:
    hb = tmp_path / "heartbeat.json"
    hb.write_text(json.dumps({
        "ts": f"{day[:4]}-{day[4:6]}-{day[6:8]}T17:30:00",
        "mode": "CHAIN_OK" if ok else "ABORT_TEST",
        "ok": ok,
        "decision": None,
    }, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return hb


def _run(monkeypatch, tmp_path, ok, sent_ok, mail_on, today="20261103", day="20261103"):
    calls = []

    def fake_send(msg, cfg):
        calls.append(msg["Subject"])
        return sent_ok

    monkeypatch.setattr(ha, "_mail_status", lambda cfg, s, b: (calls.append(s), sent_ok)[1])
    monkeypatch.setattr(ha, "_is_trading_day", lambda day_: True)
    if mail_on:
        for k, v in {**SMTP_ENV, "DEEPQUANT_MAIL_STATUS": "1"}.items():
            monkeypatch.setenv(k, v)
    else:
        monkeypatch.delenv("DEEPQUANT_MAIL_STATUS", raising=False)
    hb = _write_hb(tmp_path, ok=ok, day=day)
    rc = ha.main(["--today", today, "--hb", str(hb)])
    return rc, calls


def test_mail_disabled_by_default(monkeypatch, tmp_path):
    rc, calls = _run(monkeypatch, tmp_path, ok=True, sent_ok=True, mail_on=False)
    assert rc == 0 and calls == []


def test_ok_day_sends_status_mail(monkeypatch, tmp_path):
    rc, calls = _run(monkeypatch, tmp_path, ok=True, sent_ok=True, mail_on=True)
    assert rc == 0 and len(calls) == 1 and "正常" in calls[0]


def test_alert_day_sends_alert_mail(monkeypatch, tmp_path):
    rc, calls = _run(monkeypatch, tmp_path, ok=False, sent_ok=True, mail_on=True)
    assert rc == 1 and len(calls) == 1 and "告警" in calls[0]


def test_ok_but_mail_fails_is_rc5(monkeypatch, tmp_path):
    """正常结论 + 发不出邮件 = rc 5。否则人会以为每天有信。"""
    rc, calls = _run(monkeypatch, tmp_path, ok=True, sent_ok=False, mail_on=True)
    assert rc == 5 and len(calls) == 1


def test_alert_plus_mail_fail_still_alerts(monkeypatch, tmp_path):
    rc, calls = _run(monkeypatch, tmp_path, ok=False, sent_ok=False, mail_on=True)
    assert rc == 1 and len(calls) == 1 and "告警" in calls[0]


def test_missing_heartbeat_sends_alert_mail(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ha, "_mail_status",
                        lambda cfg, s, b: (calls.append(s), True)[1])
    monkeypatch.setattr(ha, "_is_trading_day", lambda day_: True)
    for k, v in {**SMTP_ENV, "DEEPQUANT_MAIL_STATUS": "1"}.items():
        monkeypatch.setenv(k, v)
    rc = ha.main(["--today", "20261103", "--hb", str(tmp_path / "nope.json")])
    assert rc == 1 and "告警" in calls[0]
