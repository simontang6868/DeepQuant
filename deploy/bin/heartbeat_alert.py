#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""机内告警 + 每日状态邮件：每个交易日在收盘流程之后检查心跳是否新鲜。

## 它解决什么、不解决什么

**解决**：机器醒着、但日更链没跑成（数据源挂了、脚本报错、磁盘满）。
**不解决**：整机宕机 / 断电 / 网络断开——那时没有任何进程会去说"我没跑"。
后者只能靠**外部**监控在等我们的出站信号，见 ``daily_chain.sh`` 的 4b 段。

## 每日状态邮件（用户选定的"死信开关"方案）

设 ``DEEPQUANT_MAIL_STATUS=1`` 且 SMTP 配置齐全时，本脚本**每天发一封状态邮件**
（正常/告警都在标题里）。死信逻辑：**当天没收到邮件 = 出了问题**——要么日更链没跑，
要么邮件通道挂了（后者也会以 rc=5 暴露在 systemd 里）。它弱于 healthchecks.io
（要靠人注意到"没来信"），但不需要用户去配置任何外部服务。

## 判定（按顺序，第一条命中即返回）

1. 心跳文件不存在 → CRITICAL（从未成功跑过，或被人删了）
2. 今天是**非交易日** → OK（不要求非交易日有心跳；但会检查上一交易日有没有）
3. 心跳日期不是今天 → CRITICAL（今天没跑）
4. ``ok == false`` → CRITICAL（跑了但失败，带 mode）
5. 其余 → OK

⚠️ 第 2 条之后仍要检查上一交易日：只在交易日检查会让"周五漏跑、周一正常"
看起来毫无问题——而漏的那期永远补不回来。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
SP5 = REPO / "research" / "stock_pick_2026-10-25" / "scripts" / "sp5"
HB = REPO / "research" / "stock_pick_2026-10-25" / "forward" / "heartbeat.json"
sys.path.insert(0, str(SP5))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _mail_channel():
    """邮件通道（可选）。返回 (cfg, None) 或 (None, 原因)。"""
    if os.environ.get("DEEPQUANT_MAIL_STATUS", "").strip() != "1":
        return None, "DEEPQUANT_MAIL_STATUS 未开启"
    import notify_mail
    cfg = notify_mail.read_config(dict(os.environ))
    miss = notify_mail.missing_keys(cfg)
    if miss:
        return None, f"SMTP 配置缺失：{', '.join(miss)}"
    return cfg, None


def _mail_status(cfg, subject: str, body: str) -> bool:
    """发状态邮件。失败只返回 False，由调用方决定退出码——邮件挂了不改变检查结论。"""
    import notify_mail
    try:
        msg = notify_mail.build_message(cfg, subject, body, [])
        notify_mail.send(msg, cfg)
        print(f"[mail] 状态邮件已发送：{subject}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[mail] ✗ 状态邮件发送失败：{type(e).__name__}: {e}", file=sys.stderr)
        return False


def _load_hb() -> dict | None:
    if not HB.exists():
        return None
    try:
        return json.loads(HB.read_text(encoding="utf-8"))
    except Exception:
        return {"_broken": True}


def _is_trading_day(day: str) -> bool | None:
    """None 表示日历不可用（此时不当成非交易日——那会让告警静默）。"""
    try:
        import trade_calendar as tcal
        days = tcal.load(today=day)
        return day in set(days)
    except Exception:
        return None


def _notify(text: str, webhook: str | None) -> bool:
    """把告警送出去。**没有 webhook 就不算送出**——只在 stderr 打印。

    stderr 会进 journald，但没人会天天翻 journal。这一点必须显式暴露：
    返回值 False 时脚本以非零退出，让 systemd 的 OnFailure 或外部监控接住。
    """
    print(f"[ALERT] {text}", file=sys.stderr)
    if not webhook:
        return False
    try:
        body = json.dumps({"text": text, "source": "deepquant-heartbeat"},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(webhook, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as e:
        print(f"[ALERT] 通知发送失败：{type(e).__name__}: {e}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", default=None, help="覆盖今天（测试用）")
    ap.add_argument("--max-age-hours", type=float, default=20.0)
    ap.add_argument("--webhook", default=os.environ.get("DEEPQUANT_ALERT_WEBHOOK"))
    ap.add_argument("--hb", default=None, help="心跳文件路径（默认 forward/heartbeat.json）")
    args = ap.parse_args(argv)

    today = args.today or time.strftime("%Y%m%d")
    global HB
    if args.hb:
        HB = pathlib.Path(args.hb)
    hb = _load_hb()
    mail_cfg, mail_why = _mail_channel()
    if mail_cfg is None:
        print(f"[mail] 状态邮件未启用（{mail_why}）")

    def finish(subject: str, body: str, rc: int) -> int:
        """收口：发状态邮件（每日一封，缺信=没跑），再按检查结论退出。"""
        if mail_cfg is not None:
            # 邮件发送失败不改变 rc 的告警语义：告警时本来就非 0；
            # 但"一切正常却发不出邮件"必须非 0——否则人会以为每天有信。
            ok_mail = _mail_status(mail_cfg, subject, body)
            if rc == 0 and not ok_mail:
                rc = 5
        return rc

    if hb is None:
        return finish("【DeepQuant 告警】心跳文件不存在",
                      "心跳文件不存在：日更链从未成功运行，或文件被删除。", 1)
    if hb.get("_broken"):
        return finish("【DeepQuant 告警】心跳文件不可解析",
                      "心跳文件不可解析：可能被截断（磁盘满/写入中断）。", 1)

    ts = str(hb.get("ts") or "")
    hb_day = ts[:10].replace("-", "")
    trading = _is_trading_day(today)

    if trading is False:
        # 非交易日：不要求今天有心跳，但仍要说清楚最后一条是哪天的。
        print(f"[ok] 今天 {today} 非交易日；最后心跳 {ts}（mode={hb.get('mode')}）")
        return finish(f"【DeepQuant 正常】{today} 非交易日（最后心跳 {ts}）",
                      f"今天 {today} 非交易日；最后心跳 {ts}，mode={hb.get('mode')}。", 0)
    if trading is None:
        print("[warn] 交易日历不可用，无法判断今天是否交易日；按交易日处理")

    if hb_day != today:
        return finish("【DeepQuant 告警】今天没有心跳",
                      f"今天 {today} 没有心跳；最后一条是 {ts}（mode={hb.get('mode')}）。", 1)

    try:
        t = dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        age_h = (dt.datetime.now() - t).total_seconds() / 3600.0
    except Exception:
        age_h = None
    if age_h is not None and age_h > args.max_age_hours:
        return finish("【DeepQuant 告警】心跳过旧",
                      f"心跳过旧：{age_h:.1f} 小时前（上限 {args.max_age_hours}）。", 1)

    if hb.get("ok") is not True:
        return finish("【DeepQuant 告警】日更链失败",
                      f"日更链失败：mode={hb.get('mode')} note={hb.get('stage_note') or hb.get('reason')}。", 1)

    print(f"[ok] 心跳正常：{ts} mode={hb.get('mode')} decision={hb.get('decision')}")
    return finish(f"【DeepQuant 正常】{today} 日更链正常",
                  f"心跳 {ts}，mode={hb.get('mode')}，decision={hb.get('decision')}。", 0)


if __name__ == "__main__":
    sys.exit(main())
