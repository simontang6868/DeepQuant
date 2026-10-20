#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第二分红源：新浪 F10「分红配送」页，专治**退市股**（东财 `RPT_SHAREBONUS_DET` 不含退市标的）。

## 为什么需要它（这是本轮最重要的数据缺陷）

宽池 549 只里 **249 只最终退市**。东财分红接口对这 249 只**全部返回空**，
而 `rebuild_adj_from_exec.py` 的 A4 判据有一条豁免：
「零分红事件 → ADJ ≡ EXEC，A4 不适用」。于是这 249 只**从未被涨跌停判据检查过**，
ADJ 实际就是**未复权原价**——每次 10送10/10转10 都会在 ADJ 里留下 −50% 的假跌幅。
后果：反转策略的多头腿（买"跌得多"的）会系统性地把这些假输家选进来。

本脚本抓新浪 F10 分红表（含送股/转增/派息/**除权除息日**），落 `_div_cache_sina/<code>.div.json`，
并与东财源在**在市标的**上做交叉核验（同一事件两源是否一致）。

用法：
  python scripts/fetch_dividends_sina.py                     # 全部 549 只
  python scripts/fetch_dividends_sina.py --codes 000616.SZ
  python scripts/fetch_dividends_sina.py --verify-only       # 只做两源一致性核验
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

SINA_URL = "https://money.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{num}.phtml"


def to_num(code: str) -> str:
    return code.split(".")[0]


def fetch_page(num: str, timeout: int = 30) -> str:
    req = urllib.request.Request(SINA_URL.format(num=num), headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    try:
        return raw.decode("gbk", "ignore")
    except Exception:  # noqa: BLE001
        return raw.decode("utf-8", "ignore")


CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)


def _txt(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return s.replace("&nbsp;", " ").strip()


def parse_events(html: str) -> list[dict]:
    """解析 `sharebonus_1` 表 → [{ex_date, div10, bonus10, transfer10, announce_date, progress}]。"""
    i = html.find('id="sharebonus_1"')
    if i < 0:
        return []
    seg = html[i:]
    end = seg.find("</table>")
    if end > 0:
        seg = seg[:end]
    out = []
    for row in ROW.findall(seg):
        cells = [_txt(c) for c in CELL.findall(row)]
        if len(cells) < 6:
            continue
        ann, bonus, transfer, div, prog, ex = cells[0], cells[1], cells[2], cells[3], cells[4], cells[5]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", ann):
            continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", ex):
            continue  # 未实施 / 无除权日（"--"）

        def num(x: str) -> float:
            try:
                return float(x)
            except ValueError:
                return 0.0

        b, t, d = num(bonus), num(transfer), num(div)
        out.append(
            {
                "ex_date": ex,
                "announce_date": ann,
                "div10": d,
                "bonus10": b,
                "transfer10": t,
                "progress": prog,
            }
        )
    out.sort(key=lambda e: e["ex_date"])
    return out


def process_one(code: str, args) -> dict:
    path = os.path.join(args.out, f"{code}.div.json")
    if args.verify_only:
        if not os.path.exists(path):
            return {"code": code, "ok": False, "error": "missing"}
        rows = json.load(open(path, encoding="utf-8"))
        return {"code": code, "ok": True, "n": len(rows)}
    try:
        html = fetch_page(to_num(code))
    except Exception as exc:  # noqa: BLE001
        return {"code": code, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    rows = parse_events(html)
    if not rows and "sharebonus_1" not in html:
        return {"code": code, "ok": False, "error": "no_table"}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)
    return {"code": code, "ok": True, "n": len(rows)}


def load_codes(args) -> list[str]:
    if args.codes:
        return [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    with open(args.code_file, encoding="utf-8") as f:
        return sorted(c.strip() for c in f.read().split(",") if c.strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="新浪 F10 分红配送（第二源，覆盖退市股）")
    ap.add_argument("--codes", default=None)
    ap.add_argument("--code-file", default="data/_codes_broad_549.txt")
    ap.add_argument("--out", default="data/exec/_div_cache_sina")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--report", default="data/exec/_div_sina_report.json")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    codes = load_codes(args)
    print(f"[sina] {len(codes)} 只 -> {args.out}")
    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = [pool.submit(process_one, c, args) for c in codes]
        for fut in as_completed(futs):
            rec = fut.result()
            results.append(rec)
            mark = "OK " if rec.get("ok") else "ERR"
            print(f"  [{mark}] {rec['code']:<12} {rec.get('n', rec.get('error'))}")
            time.sleep(0.02)
    results.sort(key=lambda r: r["code"])
    bad = [r for r in results if not r.get("ok")]
    n_zero = sum(1 for r in results if r.get("ok") and r.get("n") == 0)
    report = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "out": args.out,
        "n": len(results),
        "n_ok": len(results) - len(bad),
        "n_err": len(bad),
        "n_zero_events": n_zero,
        "elapsed_sec": round(time.time() - t0, 1),
        "errors": bad,
        "counts": {r["code"]: r.get("n") for r in results if r.get("ok")},
    }
    with open(args.report, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[sina] ok={report['n_ok']} err={report['n_err']} zero_event={n_zero} elapsed={report['elapsed_sec']}s")
    print(f"[sina] report -> {args.report}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
