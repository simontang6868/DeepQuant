"""G1「连续 10 个交易日零漏跑」只读统计。

⚠️ **只统计已发生的运行，不生成任何证据**：
  数据来源只有云主机 `journalctl -u deepquant-daily` 与状态邮件记录，
  脚本不写台账、不补心跳、不 ping。拿不到日志 ⇒ **FAIL**，不是「0/10 继续观察」。

一个交易日算「跑过」的条件（两条都要）：
  ① 日志有 `=== daily_chain 结束 mode=... ok=1 ===`
  ② 同一天有 `[heartbeat] ... ok=True`

用法：
    python scripts/g1_progress.py                      # 从云主机拉日志
    python scripts/g1_progress.py --journal-file F     # 离线（测试/审查复算）
    python scripts/g1_progress.py --today 20261130     # 指定判定日
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FWD = REPO / "research" / "stock_pick_2026-10-25" / "forward"
HOST = "root@110.40.190.225"
KEY = REPO / "var" / "inbox" / "dq_deploy_ed25519"
NEED = 10
START = "20261103"          # R4 起算日（首次真实日更全绿）

_RE_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T")
_RE_CHAIN = re.compile(r"=== daily_chain 结束 mode=\S+ ok=1")
_RE_HB = re.compile(r"\[heartbeat\].*ok=True")


def official_days() -> list[str]:
    payload = json.loads((FWD / "trade_calendar.json").read_text(encoding="utf-8"))
    return [str(x) for x in payload["dates"]]


def parse_journal(text: str) -> tuple[set[str], set[str]]:
    """从 `journalctl --output=short-iso` 文本里抽出「链路 ok」与「心跳 ok」的日期。"""
    chain: set[str] = set()
    hb: set[str] = set()
    for line in text.splitlines():
        m = _RE_DAY.match(line)
        if not m:
            continue
        day = "".join(m.groups())
        if _RE_CHAIN.search(line):
            chain.add(day)
        if _RE_HB.search(line):
            hb.add(day)
    return chain, hb


def effective_upto(today: str, now: dt.datetime, trading: list[str]) -> tuple[str, str]:
    """当日 17:10 未到时，不把今天算「漏跑」——只是还没到执行时点。"""
    d = dt.date(int(today[:4]), int(today[4:6]), int(today[6:]))
    cutoff = dt.datetime.combine(d, dt.time(17, 10))
    if today in trading and now < cutoff:
        prev = [x for x in trading if x < today]
        return (prev[-1] if prev else ""), "今日 17:10 未到，不计入"
    return today, ""


def count_streak(trading: list[str], good: set[str], upto: str,
                 *, start: str = START) -> tuple[int, list[str], str | None]:
    """从 upto 往前数**连续**交易日，遇到第一个没跑过的日子就停。

    返回 (连续数, 构成该连续段的日期, 第一个断点的日期)。
    起点 `start` 之前的日子不算（R4 起算日之前没有日更链）。
    """
    days = [d for d in trading if start <= d <= upto]
    streak: list[str] = []
    for d in reversed(days):
        if d not in good:
            streak.reverse()               # 断点返回也要按时间升序，否则顺序不一致
            return len(streak), streak, d
        streak.append(d)
    streak.reverse()
    return len(streak), streak, None


def fetch_journal(since: str = "2026-11-01", timeout: int = 45) -> str:
    cmd = ["ssh", "-i", str(KEY), "-o", "StrictHostKeyChecking=no",
           "-o", f"ConnectTimeout={timeout}", HOST,
           f"journalctl -u deepquant-daily --since {since} --output=short-iso --no-pager"]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
    if cp.returncode != 0:
        raise RuntimeError(f"取日志失败 rc={cp.returncode}: {cp.stderr[-200:]}")
    return cp.stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="G1 连续交易日零漏跑统计（只读）")
    ap.add_argument("--today", default=dt.date.today().strftime("%Y%m%d"))
    ap.add_argument("--journal-file", default=None, help="离线复算用；不给则 ssh 拉云端日志")
    ap.add_argument("--start", default=START)
    args = ap.parse_args(argv)

    try:
        trading = official_days()
    except Exception as e:  # noqa: BLE001
        print(f"[G1] FAIL 交易日历不可用：{e}")
        return 2

    try:
        text = (Path(args.journal_file).read_text(encoding="utf-8")
                if args.journal_file else fetch_journal())
    except Exception as e:  # noqa: BLE001
        print(f"[G1] FAIL 拿不到运行日志：{type(e).__name__}: {e}")
        return 2
    if not text.strip():
        print("[G1] FAIL 日志为空——不得把「没数据」当成「0 天继续观察」")
        return 2

    chain, hb = parse_journal(text)
    good = chain & hb
    upto, note = effective_upto(args.today, dt.datetime.now(), trading)
    if not upto:
        print("[G1] FAIL 起算日之前没有交易日可判")
        return 2
    k, days, broken = count_streak(trading, good, upto, start=args.start)

    print(f"[G1] 判定日 {args.today}｜有效截至 {upto}｜{note or '今日已过 17:10'}")
    print(f"      链路 ok 的日子 {len(chain)} 天：{sorted(chain)}")
    print(f"      心跳 ok 的日子 {len(hb)} 天：{sorted(hb)}")
    print(f"      两者皆 ok {len(good)} 天")
    print(f"      **G1 = {min(k, NEED)}/{NEED}**｜连续段 {days}")
    if broken:
        print(f"      断点：{broken}（当天没跑或心跳缺失 ⇒ 连续计数从这里断）")
        print("      ⚠️ 断点之后即使又跑了，也要**重新起算**，不能跨断点累加。")
    if k >= NEED:
        print("[G1] PASS 连续 10 个交易日零漏跑")
    else:
        rest = [d for d in trading if d > upto][:max(0, NEED - k)]
        print(f"[G1] 未通过；还须连续 {NEED - k} 个交易日"
              f"（按交易日历：{rest}）")
    print("[G1] 只读统计：未写台账、未补心跳、未 ping。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
