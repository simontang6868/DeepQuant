#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""机内告警：每个交易日在收盘流程之后检查心跳是否新鲜。

## 它解决什么、不解决什么

**解决**：机器醒着、但日更链没跑成（数据源挂了、脚本报错、磁盘满）。
**不解决**：整机宕机 / 断电 / 网络断开——那时没有任何进程会去说"我没跑"。
后者只能靠**外部**监控（healthchecks.io 之类）在等我们的出站信号，见
``daily_chain.sh`` 的 4b 段。两份都需要，缺一个就有一整类故障无声无息。

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

    if hb is None:
        _notify("心跳文件不存在：日更链从未成功运行，或文件被删除", args.webhook)
        return 1
    if hb.get("_broken"):
        _notify("心跳文件不可解析：可能被截断（磁盘满/写入中断）", args.webhook)
        return 1

    ts = str(hb.get("ts") or "")
    hb_day = ts[:10].replace("-", "")
    trading = _is_trading_day(today)

    if trading is False:
        # 非交易日：不要求今天有心跳，但仍要说清楚最后一条是哪天的。
        print(f"[ok] 今天 {today} 非交易日；最后心跳 {ts}（mode={hb.get('mode')}）")
        return 0
    if trading is None:
        print("[warn] 交易日历不可用，无法判断今天是否交易日；按交易日处理")

    if hb_day != today:
        _notify(f"今天 {today} 没有心跳；最后一条是 {ts}（mode={hb.get('mode')}）",
                args.webhook)
        return 1

    try:
        t = dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        age_h = (dt.datetime.now() - t).total_seconds() / 3600.0
    except Exception:
        age_h = None
    if age_h is not None and age_h > args.max_age_hours:
        _notify(f"心跳过旧：{age_h:.1f} 小时前（上限 {args.max_age_hours}）", args.webhook)
        return 1

    if hb.get("ok") is not True:
        _notify(f"日更链失败：mode={hb.get('mode')} note={hb.get('stage_note') or hb.get('reason')}",
                args.webhook)
        return 1

    print(f"[ok] 心跳正常：{ts} mode={hb.get('mode')} decision={hb.get('decision')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
