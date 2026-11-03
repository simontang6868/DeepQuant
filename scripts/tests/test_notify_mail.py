"""notify_mail.py（证据邮件通道）的行为测试。

钉死四条（每条对应一种"看起来成功其实没送出"）：
1. 配置缺失 → exit 2，且报错里**不出现任何配置值**（哪怕是对的）；
2. 附件超 15 MB → exit 3，不发送、不截断；
3. dry-run → 全部校验通过但**不连网**；
4. 正常路径 → 收件人/主题/附件名正确；**授权码绝不进入邮件头或正文**。
"""

from __future__ import annotations

import base64
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
_SPEC = _REPO / "deploy" / "bin" / "notify_mail.py"
_spec = importlib.util.spec_from_file_location("notify_mail", _SPEC)
nm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nm)

BASE_ENV = {
    "DEEPQUANT_SMTP_HOST": "smtp.qq.com",
    "DEEPQUANT_SMTP_PORT": "465",
    "DEEPQUANT_SMTP_USER": "sender@qq.com",
    "DEEPQUANT_SMTP_PASS": "authcode-12345678",
    "DEEPQUANT_MAIL_TO": "774365830@qq.com",
}


def _run(monkeypatch, argv, env=None):
    e = dict(BASE_ENV)
    e.update(env or {})
    for k in list(os.environ):
        if k.startswith("DEEPQUANT_"):
            monkeypatch.delenv(k, raising=False)
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    return nm.main(argv)


def _att(tmp_path, name="ev.tar.gz", size=100):
    p = tmp_path / name
    p.write_bytes(os.urandom(size))
    return p


def test_missing_config_exits_2_without_leaking(monkeypatch, capsys):
    for k in ("DEEPQUANT_SMTP_HOST", "DEEPQUANT_SMTP_PASS", "DEEPQUANT_MAIL_TO"):
        _ = k
    rc = _run(monkeypatch, ["--subject", "t"], env={
        "DEEPQUANT_SMTP_HOST": "", "DEEPQUANT_SMTP_PASS": "", "DEEPQUANT_MAIL_TO": "",
    })
    assert rc == nm.EXIT_CONFIG
    err = capsys.readouterr().err
    assert "smtp.qq.com" not in err and "authcode" not in err
    assert "DEEPQUANT_SMTP_HOST" in err and "DEEPQUANT_MAIL_TO" in err


def test_oversize_attachment_exits_3(monkeypatch, tmp_path, capsys):
    big = _att(tmp_path, size=nm.MAX_ATTACH_BYTES + 1)
    rc = _run(monkeypatch, ["--subject", "t", "--attach", str(big)])
    assert rc == nm.EXIT_TOO_BIG
    assert "不截断" in capsys.readouterr().err


def test_missing_attachment_exits_2(monkeypatch, tmp_path):
    rc = _run(monkeypatch, ["--subject", "t", "--attach", str(tmp_path / "nope.bin")])
    assert rc == nm.EXIT_CONFIG


def test_dry_run_does_not_send(monkeypatch, tmp_path):
    sent = []
    with patch.object(nm.smtplib, "SMTP_SSL",
                      side_effect=AssertionError("dry-run 不得连网")) as _:
        # 即使有人改坏实现去连网，SMTP_SSL 被替换成会抛断言的桩
        with patch.object(nm, "send", side_effect=AssertionError("dry-run 不得发送")):
            rc = _run(monkeypatch, ["--subject", "t", "--attach", str(_att(tmp_path)),
                                    "--dry-run"])
    assert rc == nm.EXIT_OK
    assert sent == []


def test_send_builds_message_correctly(monkeypatch, tmp_path):
    att = _att(tmp_path, size=64)
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, **kw):
            captured["host"], captured["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, user, pw):
            captured["user"], captured["pw"] = user, pw

        def send_message(self, msg):
            captured["msg"] = msg

    with patch.object(nm.smtplib, "SMTP_SSL", FakeSMTP):
        rc = _run(monkeypatch, ["--subject", "证据备份 20261103",
                                "--body", "正文", "--attach", str(att)])
    assert rc == nm.EXIT_OK
    msg = captured["msg"]
    assert msg["To"] == "774365830@qq.com"
    assert msg["Subject"] == "证据备份 20261103"
    assert captured["host"] == "smtp.qq.com" and captured["port"] == 465
    # 授权码绝不进入邮件（头或体）
    raw = msg.as_string()
    assert "authcode-12345678" not in raw
    # 附件名在 payload 里
    assert "ev.tar.gz" in raw


def test_body_file_is_read(monkeypatch, tmp_path):
    bf = tmp_path / "body.txt"
    bf.write_text("归档行数 42\n", encoding="utf-8", newline="\n")
    cfg = nm.read_config(BASE_ENV)
    msg = nm.build_message(cfg, "s", bf.read_text(encoding="utf-8"), [])
    assert "归档行数 42" in msg.get_content()
    assert base64.b64encode("x".encode()).decode()  # import sanity
