#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把证据归档发到指定邮箱（SMTP over SSL）。

## 用途

``backup_offsite.sh`` 的第三种投递通道。证据归档只有台账 / 配置 / 日历 / 日志摘要，
体积在 MB 以内，适合走邮件；**行情数据不进邮件**（1.6 GB，且可由行情源重建）。

## 三条硬约束（每条都对应一种"看起来成功了其实没送出去"）

1. **配置缺失就失败退出（exit 2），不静默跳过。** 邮件通道最危险的形态是
   "每天都成功退出，但收件箱里没有邮件"——三个月后要恢复时才发现。
2. **附件超限就失败退出（exit 3），不截断。** 截断后的归档能用 tar 打开一部分，
   比打不开更危险：它会让人以为备份完整。
3. **密码永不进日志/邮件正文。** 只以 ``<set>`` / ``<unset>`` 形式出现。

## 配置（写在 /opt/quant/env，chmod 640）

| 变量 | 说明 |
| --- | --- |
| ``DEEPQUANT_SMTP_HOST`` | QQ 邮箱为 ``smtp.qq.com`` |
| ``DEEPQUANT_SMTP_PORT`` | ``465``（SSL）；587 不支持，本脚本只做 SSL |
| ``DEEPQUANT_SMTP_USER`` | 登录账号，同时作为默认发件人 |
| ``DEEPQUANT_SMTP_PASS`` | **授权码**，不是登录密码（QQ 邮箱 → 设置 → 账户 → 开启 SMTP 后生成） |
| ``DEEPQUANT_MAIL_TO`` | 收件人，逗号分隔 |

命令行：

    notify_mail.py --subject "..." --body-file msg.txt [--attach a.tar.gz] [--dry-run]
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import pathlib
import smtplib
import ssl
import sys
from email.message import EmailMessage

#: 单附件上限。QQ 邮箱普通附件上限 50 MB，base64 膨胀约 4/3，
#: 取 45 MB 留余量（2026-11-03 由 15 MB 上调：交付包 ~21 MB 需单封发出）；
#: 超过就失败，**不截断**。
MAX_ATTACH_BYTES = 45 * 1024 * 1024

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_TOO_BIG = 3
EXIT_SEND = 4


def read_config(env: dict[str, str]) -> dict[str, str]:
    """读配置。缺任何一项都返回不完整，由调用方失败退出。"""
    cfg = {
        "host": (env.get("DEEPQUANT_SMTP_HOST") or "").strip(),
        "port": (env.get("DEEPQUANT_SMTP_PORT") or "465").strip(),
        "user": (env.get("DEEPQUANT_SMTP_USER") or "").strip(),
        "password": env.get("DEEPQUANT_SMTP_PASS") or "",
        "to": (env.get("DEEPQUANT_MAIL_TO") or "").strip(),
    }
    cfg["from"] = (env.get("DEEPQUANT_MAIL_FROM") or cfg["user"]).strip()
    return cfg


def missing_keys(cfg: dict[str, str]) -> list[str]:
    need = {"DEEPQUANT_SMTP_HOST": cfg["host"],
            "DEEPQUANT_SMTP_USER": cfg["user"],
            "DEEPQUANT_SMTP_PASS": cfg["password"],
            "DEEPQUANT_MAIL_TO": cfg["to"]}
    return [k for k, v in need.items() if not v]


def build_message(cfg: dict[str, str], subject: str, body: str,
                  attachments: list[pathlib.Path]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(x.strip() for x in cfg["to"].split(",") if x.strip())
    msg["Subject"] = subject
    msg.set_content(body, charset="utf-8")
    for p in attachments:
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype,
                           filename=p.name)
    return msg


#: 附件每 MB 预留的秒数。实测（2026-11-03，QQ→本机，腾讯云内网出口）：
#:   13 MB 附件的 DATA 阶段耗时 55.3 s ⇒ 约 4.3 s/MB。
#:   取 8 s/MB（≈2 倍余量）＋ 60 s 固定开销，再与下限 180 s 取大。
#: 为什么不再写死 120：每周异地包从 13 MB 长到 25 MB 时，120 s 会刚好撞线——
#: 而"刚好撞线"的失败是**间歇性**的，某周成功某周失败，最难查。
SEC_PER_MB = 8.0
MIN_TIMEOUT = 180.0


def timeout_for(total_bytes: int) -> float:
    """按附件总量推算 SMTP 会话超时（秒）。"""
    mb = max(0.0, total_bytes / 1048576.0)
    return max(MIN_TIMEOUT, 60.0 + SEC_PER_MB * mb)


def send(msg: EmailMessage, cfg: dict[str, str], timeout: float = 120.0) -> None:
    """timeout 覆盖整个会话（含 DATA）。

    历史：30 s 时 13 MB 附件会中途断连（SMTPServerDisconnected）；改为 120 s 后
    13 MB 通过。现改为由 :func:`timeout_for` 按附件大小推算，避免包变大后复发。
    """
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), context=ctx,
                          timeout=timeout) as s:
        s.login(cfg["user"], cfg["password"])
        s.send_message(msg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把证据归档发到邮箱")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body", default="")
    ap.add_argument("--body-file")
    ap.add_argument("--attach", action="append", default=[],
                    help="附件路径，可多次")
    ap.add_argument("--dry-run", action="store_true",
                    help="只校验配置与附件大小，不发送")
    args = ap.parse_args(argv)

    env = dict(os.environ)
    cfg = read_config(env)
    miss = missing_keys(cfg)
    if miss:
        # ⚠️ 不打印任何值，只打印键名：哪怕值是对的，日志也不该有它
        print(f"[mail] ✗ 配置不完整，缺少：{', '.join(miss)}", file=sys.stderr)
        print("[mail]   邮件通道不可用。**不要**把它当成'已备份'。", file=sys.stderr)
        return EXIT_CONFIG

    body = args.body
    if args.body_file:
        body = pathlib.Path(args.body_file).read_text(encoding="utf-8")
    if not body:
        body = "(无正文)\n"

    atts: list[pathlib.Path] = []
    total = 0
    for a in args.attach:
        p = pathlib.Path(a)
        if not p.is_file():
            print(f"[mail] ✗ 附件不存在：{p}", file=sys.stderr)
            return EXIT_CONFIG
        total += p.stat().st_size
        atts.append(p)
    if total > MAX_ATTACH_BYTES:
        print(f"[mail] ✗ 附件合计 {total / 1048576:.1f} MB > "
              f"{MAX_ATTACH_BYTES / 1048576:.0f} MB 上限", file=sys.stderr)
        print("[mail]   拒绝发送（不截断）。改为留在本机并另行处理。", file=sys.stderr)
        return EXIT_TOO_BIG

    msg = build_message(cfg, args.subject, body, atts)
    print(f"[mail] 收件人 {msg['To']}｜主题 {args.subject}｜"
          f"附件 {len(atts)} 个 {total / 1048576:.2f} MB")

    if args.dry_run:
        print("[mail] dry-run：只校验，未发送")
        return EXIT_OK

    try:
        to = timeout_for(total)
        if env.get("DEEPQUANT_SMTP_TIMEOUT"):
            to = float(env["DEEPQUANT_SMTP_TIMEOUT"])   # 显式覆盖（排查时用）
        print(f"[mail] SMTP 会话超时 {to:.0f}s（附件 {total / 1048576:.1f} MB）")
        send(msg, cfg, timeout=to)
    except Exception as e:  # noqa: BLE001 - 任何发送失败都必须转成非零退出
        print(f"[mail] ✗ 发送失败：{type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_SEND

    print("[mail] 已发送")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
