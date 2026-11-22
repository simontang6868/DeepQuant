#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""施工单 v2 P1-1：全部预登记日期过交易日历（不靠“应该没问题”）。

输出：
  research/stock_pick_2026-10-25/forward/trading_day_check_<YYYYMMDD>.json
  reports/TRADING_DAY_CHECK_<date>.md
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAL_REL = "research/stock_pick_2026-10-25/forward/trade_calendar.json"
FW = ROOT / "research/stock_pick_2026-10-25/forward"
CHECK_DATE = "20261122"

FILES = [
    "research/stock_pick_2026-10-25/forward/paper_authorization.json",
    "docs/PROBE_EXEC_PREREG_2026-11-20.md",
    "docs/PROBE_MIN_ORDER_ACCEPTANCE_2026-11-22.md",
    "docs/U16_EXEC_PREREG_2026-11-16.md",
    "docs/PROBE_EXEC_CHECKLIST_2026-11-23_TO_25.md",
]

EXEC_SEMANTICS = ("执行日", "买入日", "卖出日", "去污", "调仓日", "决策日", "前置自检",
                  "主演练", "旁证", "台账", "DRYRUN", "PREFLIGHT", "PREDICT", "读价")

RE_DASH = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")
RE_COMPACT = re.compile(r"\b(20\d{6})\b")


def _semantic(line: str) -> str:
    for kw in EXEC_SEMANTICS:
        if kw in line:
            return kw
    low = line.lower()
    if "not_after" in low or "到期" in line:
        return "授权到期"
    if "issued_at" in low or "签发" in line:
        return "签发/冻结"
    if "冻结" in line or "生成" in line or "更新" in line:
        return "文档日期"
    return "其他引用"


def load_calendar() -> set[str]:
    data = json.loads((ROOT / CAL_REL).read_text(encoding="utf-8"))
    return set(data["dates"])


def prev_trading_day(d: dt.date, cal: set[str]) -> str:
    x = d
    for _ in range(15):
        x -= dt.timedelta(days=1)
        if x.strftime("%Y%m%d") in cal:
            return x.isoformat()
    return "NOT_FOUND"


def main() -> int:
    cal = load_calendar()
    rows: list[dict] = []
    for rel in FILES:
        p = ROOT / rel
        if not p.is_file():
            rows.append({"所在文件": rel, "该处语义": "FILE_MISSING", "字面日期": "-",
                         "是否交易日": "N/A", "上一交易日": "-", "冲突": "MISSING_FILE",
                         "处置": "如实登记，不伪造核对"})
            continue
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            found = []
            for m in RE_DASH.finditer(line):
                found.append((f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
                              m.group(0)))
            for m in RE_COMPACT.finditer(line):
                s = m.group(1)
                try:
                    d = dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
                    found.append((d.isoformat(), s))
                except ValueError:
                    continue
            for iso, literal in found:
                d = dt.date.fromisoformat(iso)
                key = d.strftime("%Y%m%d")
                is_td = key in cal
                sem = _semantic(line)
                conflict = ""
                disposition = "无需处置"
                if not is_td and sem in EXEC_SEMANTICS:
                    conflict = "非交易日却作为执行/产物日"
                    disposition = "按上一交易日 " + prev_trading_day(d, cal) + " 校正，保留字面值"
                if iso == "2026-11-21":
                    conflict = "P-1 字面日期为周六；上一交易日 2026-11-20（收盘 9.400）"
                    disposition = "脚本已按 11-20 取前收，产物同时记字面与实际"
                rows.append({
                    "所在文件": f"{rel}:{lineno}",
                    "该处语义": sem,
                    "字面日期": literal,
                    "是否交易日": "是" if is_td else "否",
                    "上一交易日": prev_trading_day(d, cal),
                    "冲突": conflict,
                    "处置": disposition,
                })

    n_conf = sum(1 for r in rows if r["冲突"])
    out = {"generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
           "calendar": CAL_REL, "n_rows": len(rows), "n_conflicts": n_conf,
           "rule": "字面日期逐条与 exec_all 交易日历核对；执行语义非交易日必须校正，冻结文档日期不判冲突",
           "rows": rows}
    jp = FW / f"trading_day_check_{CHECK_DATE}.json"
    jp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                  encoding="utf-8", newline="\n")

    md = ["# 预登记日期交易日历核对表（施工单 v2 P1-1）", "",
          f"> 日历：`{CAL_REL}`｜核对行数 {len(rows)}｜冲突 {n_conf}", "",
          "| 所在文件 | 该处语义 | 字面日期 | 是否交易日 | 上一交易日 | 冲突 | 处置 |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append("| " + " | ".join(str(r[k]).replace("|", "\\|") for k in
                                    ("所在文件", "该处语义", "字面日期", "是否交易日",
                                     "上一交易日", "冲突", "处置")) + " |")
    mp = ROOT / f"reports/TRADING_DAY_CHECK_{CHECK_DATE[:4]}-{CHECK_DATE[4:6]}-{CHECK_DATE[6:]}.md"
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"json": str(jp.relative_to(ROOT)), "md": str(mp.relative_to(ROOT)),
                      "n_rows": len(rows), "n_conflicts": n_conf}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
