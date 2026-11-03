#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Phase 1.4：exec_all 增量更新 + 双源比对。

为什么增量而不是全量重写
----------------------
`fetch_exec_prices.py` 每次从 `--start` 全量重抓并**重写整个文件**。
对 5,472 只 × 10 年历史来说，这既有网络代价，也让「一次失败的抓取」有机会
覆盖掉本来完好的历史。本脚本只做两件事：**追加新行**，且**只有两个独立来源
对同一天的收盘价与成交量都吻合时才追加**。

双源口径（这是本脚本存在的核心理由）
----------------------------------
| 来源 | 接口 | 价格 | 成交量单位 |
| --- | --- | --- | --- |
| 腾讯 | `appstock/app/kline/kline`（不复权） | 元，3 位小数 | **手**（100 股） |
| 新浪 | `CN_MarketDataService.getKLineData`（不复权） | 元，3 位小数 | **股** |

⇒ 成交量必须换算后才能比：`腾讯volume × 100 ≈ 新浪volume`。

⚠️ **但「腾讯=手」不是全市场统一的。** 2026-11-02 全量实测（5,472 只）发现：
**科创板（688xxx）的腾讯 volume 已经是「股」**，比值 1.0；主板/创业板比值 0.01。
所以单位必须**逐只从数据自身判定**，不能按板块前缀硬编码，也不能假定全市场一致。

本项目 `data/exec_all` 因此是**单位混用**的：科创板存的是股，其余存的是手。
这一条在本阶段之前没有任何文档记录；`volume × 100 × close` 的算法对 688 会放大 100 倍。
本脚本不改历史单位（保持与既有行一致），只**把判定的单位写进报告**，供下游修正。

这条换算不是可选项：本项目历史上出现过 volume 单位错误（见 MEMORY 铁律），
把「手」当「股」会让成交额差 100 倍，而**价格比对完全正常**，只看价格查不出来。

写入纪律
--------
- 默认 **dry-run**，必须显式 `--apply` 才写；
- 只**追加**，不重写历史：新内容 = 原文件字节 + 已核验的新行；
- 原子替换（写 `.tmp` 再 `os.replace`），中途失败不会留下半截文件；
- 任何一个来源缺失、或两源不一致 ⇒ **该行不写入**，只记 flagged，绝不折中取一个。

用法
----
    python scripts/update_exec_incremental.py --limit 20                 # 试算（不写）
    python scripts/update_exec_incremental.py --codes 600000.SH --end 2026-10-30
    python scripts/update_exec_incremental.py --limit 20 --apply         # 小批写入
    python scripts/update_exec_incremental.py --apply                    # 全量写入

退出码：0 全绿；1 有 flagged（仍需人工看）；2 有写入错误。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

EXEC_DIR = "data/exec_all"
HEADER = ["date", "open", "high", "low", "close", "volume"]

TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
SINA_URL = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
            "?symbol={sym}&scale=240&ma=no&datalen={n}")

#: 收盘价容差。两个来源都给 3 位小数，按最小变动单位 0.01 元的一半再留一点余量。
CLOSE_TOL = 0.011
#: 成交量相对容差。换算后（手→股）两源通常差 <1e-6，1% 只用来挡真正的错价/错股。
VOL_REL_TOL = 0.01


def to_symbol(code: str) -> str:
    """600000.SH -> sh600000；000001.SZ -> sz000001。"""
    num, _, mkt = code.partition(".")
    return ("sh" if mkt.upper() == "SH" else "sz") + num


def _get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def fetch_tencent(sym: str, start: str, end: str) -> dict[str, dict]:
    """返回 {date: {open,high,low,close,volume_hand}}。

    列序是 [date, open, close, high, low, volume] —— **注意 close 在 high/low 之前**，
    这是腾讯接口的坑，也是 `fetch_exec_prices.write_exec_csv` 里做重排的原因。
    """
    url = f"{TENCENT_URL}?param={sym},day,{start},{end},640"
    payload = json.loads(_get(url))
    node = (payload.get("data") or {}).get(sym) or {}
    out: dict[str, dict] = {}
    for r in (node.get("day") or []):
        try:
            out[str(r[0])] = {
                "open": float(r[1]), "close": float(r[2]),
                "high": float(r[3]), "low": float(r[4]),
                "volume": float(r[5]),
            }
        except (IndexError, TypeError, ValueError):
            continue
    return out


def fetch_sina(sym: str, n: int = 120) -> dict[str, dict]:
    """返回 {date: {open,high,low,close,volume_share}}。成交量单位是**股**。"""
    url = SINA_URL.format(sym=sym, n=n)
    raw = _get(url)
    if not raw.strip() or raw.strip() == "null":
        return {}
    rows = json.loads(raw)
    out: dict[str, dict] = {}
    for r in rows:
        try:
            out[str(r["day"])[:10]] = {
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _infer_unit(ten_vol: float, sina_vol: float) -> str | None:
    """判定腾讯的 volume 是「手」还是「股」。

    * 等值 ⇒ ``share``（科创板实测如此）
    * 100 倍 ⇒ ``hand``（主板/创业板实测如此）
    * 都不满足 ⇒ ``None``（不是单位问题，是数据对不上）

    为什么让它判定而不是按代码前缀硬编码：前缀规则是外部约定，会变；
    而「两源是否吻合」是每只股票每天都能就地验证的事实。
    """
    if sina_vol <= 0:
        return None
    if abs(ten_vol - sina_vol) / sina_vol <= VOL_REL_TOL:
        return "share"
    if abs(ten_vol * 100.0 - sina_vol) / sina_vol <= VOL_REL_TOL:
        return "hand"
    return None


def read_last(path: str) -> tuple[str, int]:
    """返回 (最后一行的日期, 行数)。空文件返回 ("", 0)。"""
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            txt = fh.read()
    except FileNotFoundError:
        return "", 0
    lines = [x for x in txt.split("\n") if x.strip()]
    if len(lines) < 2:
        return "", len(lines)
    return lines[-1].split(",")[0], len(lines) - 1


def check_one(code: str, exec_dir: str, end: str, write: bool) -> dict:
    path = os.path.join(exec_dir, f"{code}.exec.csv")
    last, n_have = read_last(path)
    rec = {"code": code, "last_before": last, "n_before": n_have,
           "appended": 0, "status": "", "flags": []}
    if not last:
        rec["status"] = "NO_LOCAL_FILE"
        return rec
    if last >= end:
        rec["status"] = "UP_TO_DATE"
        return rec

    sym = to_symbol(code)
    start = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()
    try:
        ten = fetch_tencent(sym, start, end)
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "TENCENT_ERROR"
        rec["flags"].append(f"{type(exc).__name__}: {exc}")
        return rec
    new_dates = sorted(d for d in ten if d > last)
    if not new_dates:
        # 停牌/退市/尚未发布，都表现为「没有新行」。**不**把它当错误，但也**不**当成已更新。
        rec["status"] = "NO_NEW_ROWS"
        return rec

    try:
        sin = fetch_sina(sym, n=max(60, len(new_dates) + 20))
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "SINA_ERROR"
        rec["flags"].append(f"{type(exc).__name__}: {exc}")
        return rec

    good: list[str] = []
    units: dict[str, str] = {}
    for d in new_dates:
        a = ten[d]
        b = sin.get(d)
        if b is None:
            rec["flags"].append(f"{d}: 新浪缺该日")
            continue
        if abs(a["close"] - b["close"]) > CLOSE_TOL:
            rec["flags"].append(f"{d}: 收盘价不符 腾讯{a['close']:.3f} vs 新浪{b['close']:.3f}")
            continue
        if abs(a["high"] - b["high"]) > CLOSE_TOL:
            rec["flags"].append(f"{d}: 最高价不符 {a['high']:.3f} vs {b['high']:.3f}")
            continue
        # ⚠️ 单位由数据判定，不按板块硬编码（见模块文档：科创板是「股」不是「手」）
        u = _infer_unit(a["volume"], b["volume"])
        if u is None:
            rec["flags"].append(
                f"{d}: 成交量不符 腾讯{a['volume']:.0f} vs 新浪{b['volume']:.0f}"
                f"（既非等值也非 100 倍关系）")
            continue
        units[d] = u
        good.append(d)

    if good:
        # 同一只股票的所有新行必须是同一种单位，混用说明中间有数据源切换或错股
        distinct = set(units.values())
        if len(distinct) > 1:
            rec["flags"].append(f"单位在窗口内不一致：{sorted(distinct)} ⇒ 全部不写")
            good = []
        else:
            rec["volume_unit"] = distinct.pop()

    if not good:
        rec["status"] = "NO_AGREEMENT"
        return rec

    rec["appended"] = len(good)
    rec["last_after"] = good[-1]
    rec["status"] = "READY" if not rec["flags"] else "READY_WITH_FLAGS"

    if write:
        rows = []
        for d in good:
            a = ten[d]
            # 与既有文件同列序：date,open,high,low,close,volume（volume 仍是「手」）
            rows.append(f"{d},{a['open']:.3f},{a['high']:.3f},{a['low']:.3f},"
                        f"{a['close']:.3f},{a['volume']:.3f}\n")
        with open(path, "rb") as fh:
            old = fh.read()
        sep = b"" if old.endswith(b"\n") or not old else b"\n"
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(old)
            fh.write(sep)
            fh.write("".join(rows).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        rec["written"] = True
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exec-dir", default=EXEC_DIR)
    ap.add_argument("--codes", default=None, help="逗号分隔；不传则全目录")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 只（试算用）")
    ap.add_argument("--end", default=None, help="目标末日 YYYY-MM-DD，默认今天")
    ap.add_argument("--apply", action="store_true", help="真正写入；不加则只试算")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    end = args.end or dt.date.today().isoformat()
    if args.codes:
        codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    else:
        codes = sorted(f[:-len(".exec.csv")] for f in os.listdir(args.exec_dir)
                       if f.endswith(".exec.csv"))
    if args.limit:
        codes = codes[: args.limit]

    t0 = time.time()
    print(f"[update] {len(codes)} 只｜目标末日 {end}｜{'APPLY' if args.apply else 'DRY-RUN'}")
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(check_one, c, args.exec_dir, end, args.apply): c for c in codes}
        done = 0
        for f in as_completed(futs):
            results.append(f.result())
            done += 1
            if done % 200 == 0:
                print(f"  ... {done}/{len(codes)}")

    results.sort(key=lambda r: r["code"])
    from collections import Counter
    status = Counter(r["status"] for r in results)
    n_app = sum(r["appended"] for r in results)
    flagged = [r for r in results if r["flags"]]
    print(f"\n[status] {dict(status)}")
    print(f"[rows] 可追加 {n_app} 行｜有 flag 的股票 {len(flagged)} 只｜耗时 {time.time()-t0:.1f}s")

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": end, "apply": args.apply, "n_codes": len(codes),
        "status": dict(status), "rows_appended": n_app,
        "n_flagged_codes": len(flagged),
        "close_tol": CLOSE_TOL, "vol_rel_tol": VOL_REL_TOL,
        "volume_unit_note": ("单位逐只由两源比对判定（share=腾讯已是股；hand=腾讯是手）。"
                             "实测科创板=share、主板创业板=hand ⇒ exec_all 历史上是单位混用"),
        "volume_unit_counts": dict(Counter(r.get("volume_unit") for r in results
                                          if r.get("volume_unit"))),
        "results": results,
    }
    out = args.report or os.path.join(args.exec_dir, "_incremental_report.json")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(f"[report] {out}")

    if any(r["status"].endswith("_ERROR") for r in results):
        return 2
    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
