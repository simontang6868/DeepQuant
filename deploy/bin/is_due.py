#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""今天是不是决策窗口？（日更链用来决定"要不要跑重活"）

输出一行 ``DUE <YYYYMMDD>`` / ``NOT_DUE <YYYYMMDD> <原因>`` / ``ABORT <原因>``，
退出码 0/1/2。**不写任何文件**，可安全地反复调用。

## 为什么不直接问 p13_forward

``p13_forward --if-due`` 会走完整流程（建截面 ~60 s），用它做"要不要跑"的判据
等于每次都先付一次最贵的代价。这里只做日历 + 数据末日两个查询。

## 判定表

| 数据末日 | 距月末 | 台账 | 结果 |
| --- | --- | --- | --- |
| 覆盖月末 | ≤3 天 | 无 | ``DUE``（跑，模式 LATE_RUN 或 FORWARD） |
| 覆盖月末 | >3 天 | 无 | ``NOT_DUE``（当日为月末时由 p13 判 FORWARD，不走这条） |
| 覆盖月末 | 任意 | 有 | ``NOT_DUE``（本期已落盘） |
| 未覆盖月末 | — | — | ``ABORT``（数据没更新完，是真问题） |
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SP5 = REPO / "research" / "stock_pick_2026-10-25" / "scripts" / "sp5"
sys.path.insert(0, str(SP5))
sys.path.insert(0, str(SP5.parent))


def main() -> int:
    import p13_forward as pf
    import trade_calendar as tcal

    today = pf._today_str()
    try:
        off = tcal.load(today=today)
    except tcal.CalendarUnavailable as e:
        print(f"ABORT 交易日历不可用：{e}")
        return 2

    try:
        data_last = pf.data_last_date()
    except pf.DataStaleError as e:
        print(f"ABORT 数据末日取不到：{e}")
        return 2

    me, why, err = pf.resolve_decision_day(today, off, data_last, check_window=False)
    if me is None:
        print(f"ABORT {why}")
        return 2

    ledger = REPO / "research" / "stock_pick_2026-10-25" / "forward" / "ledger" / f"{me}.json"
    if ledger.exists():
        print(f"NOT_DUE {me} 台账已存在（{ledger.name}）")
        return 1

    gap = (pf._parse(today) - pf._parse(me)).days
    if gap > pf.MAX_LATE_RUN_DAYS:
        print(f"ABORT {me} 无台账且距月末 {gap} 天 > {pf.MAX_LATE_RUN_DAYS} ⇒ 已漏跑，无法补记")
        return 2

    print(f"DUE {me} 距月末 {gap} 天；数据末日 {data_last}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
