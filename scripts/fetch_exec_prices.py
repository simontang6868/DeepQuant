#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""EXEC 层落地：抓取"真不复权"日线，独立存放于 data/exec/。

背景（docs/PRICE_CALIBER_SPEC_2026-10-20.md）：
  data/raw/ 里的 *.raw.csv 是"摄入源副本"，其内容是 qfq（ADJ），**不是真不复权**。
  历史上先装加性 qfq、2026-10-20 起装乘性 qfq，两者都是 ADJ。
  因此交易模拟层（成交价/现金/费用/一手成本）一直缺 EXEC 数据。

本脚本做什么：
  从腾讯 K 线接口取"不复权"日线（kline/kline，不带 fq 参数），
  落 data/exec/<code>.exec.csv，并按三条哨兵自检：
    S1 末日期 >= --end（默认 2026-10-16）
    S2 价格全 > 0，且日期严格递增、无重复
    S3 基准一致性：不复权起点 / ADJ 起点 应 >= 1（<1 说明股本基准反了，标 flag）

用法：
  python scripts/fetch_exec_prices.py                      # 用 data/pool_manifest.json 的池
  python scripts/fetch_exec_prices.py --codes 000001.SZ --out data/exec_dev
  python scripts/fetch_exec_prices.py --end 2026-10-16 --workers 6

注意：
  - 腾讯不复权序列只有 2~3 位小数。对 EXEC 用途（成交价水平/一手成本/现金约束）**是合适的**，
    因为 A 股报价最小变动单位就是 0.01 元；但**不要**用它累乘日收益去做波动率估计
    （那是 PRICE_RET 的活，且 2 位小数的量化噪声会高估波动）。
  - 输出与网络返回顺序无关（按日期排序后写），同一输入两次运行字节应相同。
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

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
PAGE_BARS = 640
MAX_PAGES = 16
DEFAULT_START = "2016-01-01"
DEFAULT_END = "2026-10-16"

HEADER = ["date", "open", "high", "low", "close", "volume"]


def to_tencent_symbol(code: str) -> str:
    """000001.SZ -> sz000001 ; 600519.SH -> sh600519"""
    num, _, suffix = code.partition(".")
    suffix = suffix.upper()
    if suffix == "SH":
        return "sh" + num
    if suffix == "SZ":
        return "sz" + num
    raise ValueError(f"unsupported exchange suffix: {code}")


def _get_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    return json.loads(raw.decode("utf-8", "ignore"))


def fetch_raw_daily(sym: str, start: str, end: str) -> list[list[str]]:
    """分页回翻取不复权日线。返回按日期升序的 [date, open, close, high, low, volume]。

    腾讯返回的每行格式为 [date, open, close, high, low, volume, ...]（注意 open/close/high/low 顺序）。

    分页要点：单页最多 640 根，需用 end 递减回翻。**不要用"本页无新数据"作为终止条件**——
    接口偶发返回整页重复数据，会导致序列被截断（实测 002165 第一次只取到 1920 行而非 2556 行）。
    正确做法：每页都无脑回退到本页首日的前一天，连续 2 页无进展或首日已 <= start 才停。
    """
    rows: dict[str, list[str]] = {}
    cur_end = end
    stall = 0
    for _ in range(MAX_PAGES):
        url = f"{KLINE_URL}?param={sym},day,{start},{cur_end},{PAGE_BARS}"
        payload = _get_json(url)
        node = (payload.get("data") or {}).get(sym) or {}
        batch = node.get("day") or []
        if not batch:
            break
        new = 0
        for row in batch:
            d = str(row[0])
            if d not in rows:
                rows[d] = row
                new += 1
        stall = stall + 1 if new == 0 else 0
        if stall >= 2:
            break
        first = str(batch[0][0])
        if first <= start:
            break
        nxt = dt.date.fromisoformat(first) - dt.timedelta(days=1)
        cur_end = nxt.isoformat()
    return [rows[k] for k in sorted(rows)]


def probe_gap(sym: str, start: str, first_seen: str) -> str | None:
    """窗口 [start, first_seen) 内是否还有数据？有则说明刚才的分页被截断了。

    真实长期停牌也会造成缺口，但它不会在缺口窗口里返回数据——
    所以在缺口里探到数据 = 分页漏取，必须标出来重取。
    """
    prev = dt.date.fromisoformat(first_seen) - dt.timedelta(days=1)
    if prev.isoformat() < start:
        return None
    url = f"{KLINE_URL}?param={sym},day,{start},{prev.isoformat()},{PAGE_BARS}"
    try:
        payload = _get_json(url)
        node = (payload.get("data") or {}).get(sym) or {}
        batch = node.get("day") or []
        return str(batch[0][0]) if batch else None
    except Exception:  # noqa: BLE001 - 探测失败不阻断主流程
        return None


def read_adj_series(code: str, adj_dir: str) -> dict[str, float] | None:
    """读 ADJ 源（data/qfq_mult/listed/<code>.qfq.csv）为 {date: close}。"""
    path = os.path.join(adj_dir, f"{code}.qfq.csv")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return None
    header = [h.strip().lower() for h in rows[0]]
    di = header.index("date") if "date" in header else 0
    ci = header.index("close") if "close" in header else 2
    out: dict[str, float] = {}
    for r in rows[1:]:
        try:
            out[r[di]] = float(r[ci])
        except (IndexError, ValueError):
            continue
    return out or None


def write_exec_csv(path: str, rows: list[list[str]]) -> int:
    """写 <code>.exec.csv（LF 行尾、按日期升序、统一列序）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADER)
        n = 0
        for r in rows:
            # 腾讯列序: date, open, close, high, low, volume
            w.writerow([r[0], r[1], r[3], r[4], r[2], r[5] if len(r) > 5 else ""])
            n += 1
    os.replace(tmp, path)
    return n


def read_exec_csv(path: str) -> list[list[str]]:
    """读 <code>.exec.csv，返回与网络行同构的 [date, open, close, high, low, volume]。"""
    with open(path, encoding="utf-8") as f:
        r = list(csv.reader(f))
    if len(r) < 2:
        return []
    hdr = [h.strip().lower() for h in r[0]]
    idx = {name: hdr.index(name) for name in HEADER if name in hdr}
    out = []
    for row in r[1:]:
        if not row:
            continue
        out.append(
            [
                row[idx["date"]],
                row[idx["open"]],
                row[idx["close"]],
                row[idx["high"]],
                row[idx["low"]],
                row[idx["volume"]] if "volume" in idx and idx["volume"] < len(row) else "",
            ]
        )
    return out


def validate_rows(code: str, rows: list[list[str]], args, do_probe: bool = True) -> dict:
    """对一只的 EXEC 序列跑全部哨兵。纯计算 + 可选联网探缺口，不写盘。"""
    sym = to_tencent_symbol(code)
    rec: dict = {"code": code, "symbol": sym, "ok": False, "flags": []}
    if not rows:
        rec["error"] = "no_data"
        return rec

    rec["rows"] = len(rows)
    rec["start"] = rows[0][0]
    rec["end"] = rows[-1][0]

    # S2: 价格为正 + 日期严格递增
    dates = [r[0] for r in rows]
    rec["monotonic"] = all(dates[i] < dates[i + 1] for i in range(len(dates) - 1))
    rec["unique_dates"] = len(set(dates)) == len(dates)
    bad = 0
    for r in rows:
        try:
            if min(float(r[1]), float(r[2]), float(r[3]), float(r[4])) <= 0:
                bad += 1
        except (TypeError, ValueError):
            bad += 1
    rec["nonpositive_rows"] = bad
    if bad:
        rec["flags"].append("nonpositive_price")
    if not rec["monotonic"] or not rec["unique_dates"]:
        rec["flags"].append("bad_date_order")

    # S1: 末日期
    if rec["end"] < args.end:
        rec["flags"].append(f"stale_end:{rec['end']}")

    # S1b: 分页截断探测（起点明显晚于请求起点时才探，省请求）。
    #      真实长期停牌会造成缺口但缺口里无数据；缺口里探到数据 = 分页漏取。
    gap_days = (dt.date.fromisoformat(rec["start"]) - dt.date.fromisoformat(args.start)).days
    if do_probe and gap_days > 20:
        earlier = probe_gap(sym, args.start, rec["start"])
        if earlier:
            rec["flags"].append(f"truncated_before:{earlier}")

    # S3: 基准一致性 —— 必须在**共同日期**上比，否则两条序列起止不同会算出假比值。
    #     前复权锚定最新价，故末日必然 ADJ==EXEC（无信息量）；有信息量的是**最早共同日**。
    adj = read_adj_series(code, args.adj_dir)
    if adj:
        exec_close = {r[0]: float(r[2]) for r in rows}
        common = sorted(set(exec_close) & set(adj))
        rec["adj_only_days"] = len(set(adj) - set(exec_close))
        rec["exec_only_days"] = len(set(exec_close) - set(adj))
        rec["common_days"] = len(common)
        if not common:
            rec["flags"].append("no_overlap_with_adj")
        else:
            first = common[0]
            rec["first_common_date"] = first
            rec["first_common_exec_close"] = exec_close[first]
            rec["first_common_adj_close"] = adj[first]
            adj_close = adj[first]
            if adj_close > 0:
                ratio = exec_close[first] / adj_close
                rec["basis_ratio"] = round(ratio, 4)
                if ratio < 1.0:
                    # 反了：ADJ 起点高于真不复权 => 隐含"股息贡献为负"，数学上不可能。
                    # 这是硬失败——它破坏"ADJ 是 EXEC 的下界"这个前提。
                    rec["flags"].append("basis_inverted")
                elif ratio > args.max_ratio:
                    # 高比值不构成风险：说明该股十年间送转多、ADJ 远低于真价，
                    # 用它算"买得起"偏保守。只作提示，不判失败。
                    rec.setdefault("notes", []).append(f"basis_ratio_high:{ratio:.2f}")
            last = common[-1]
            rec["last_common_date"] = last
            rec["last_ratio"] = round(exec_close[last] / adj[last], 6) if adj[last] else None

            # S3b: 日收益一致性 —— 真不复权与 ADJ 的日收益只应在除权日不同。
            #      ADJ 日收益系统性低于 EXEC（比值 >1 单调收敛）说明 ADJ 调整方向反了。
            exec_ret = []
            adj_ret = []
            for i in range(1, len(common)):
                a, b = common[i - 1], common[i]
                if exec_close[a] > 0 and adj[a] > 0:
                    exec_ret.append(exec_close[b] / exec_close[a] - 1)
                    adj_ret.append(adj[b] / adj[a] - 1)
            if exec_ret:
                diffs = sorted(abs(x - y) for x, y in zip(exec_ret, adj_ret))
                rec["ret_median_absdiff"] = round(diffs[len(diffs) // 2], 6)
                rec["ret_bigdiff_days"] = sum(1 for d in diffs if d > 0.005)
                rec["ret_bigdiff_pct"] = round(rec["ret_bigdiff_days"] / len(diffs) * 100, 2)
        # 覆盖差异只作信息，不作失败判据（长期停牌会造成真实缺口）
        adj_start = sorted(adj)[0] if adj else rec["start"]
        if rec["start"] != adj_start:
            rec.setdefault("notes", []).append(f"exec_start={rec['start']} adj_start={adj_start}")

    rec["ok"] = not rec["flags"]
    return rec


def process_one(code: str, args) -> dict:
    out_path = os.path.join(args.out, f"{code}.exec.csv")
    try:
        rows = fetch_raw_daily(to_tencent_symbol(code), args.start, args.end)
    except Exception as exc:  # noqa: BLE001 - 网络异常逐只记录，不中断整体
        return {"code": code, "symbol": to_tencent_symbol(code), "ok": False, "flags": [], "error": f"{type(exc).__name__}: {exc}"}
    if not rows:
        return {"code": code, "symbol": to_tencent_symbol(code), "ok": False, "flags": [], "error": "no_data"}
    write_exec_csv(out_path, rows)
    return validate_rows(code, rows, args, do_probe=True)


def load_codes(args) -> list[str]:
    if args.codes:
        return [c.strip().upper() for c in args.codes.split(",") if c.strip()]
    with open(args.pool_manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    codes = [s["code"] for s in manifest.get("symbols", []) if s.get("code")]
    return sorted(codes)


def _flag_counts(results: list[dict]) -> dict[str, int]:
    """按 flag 主名（去掉 `:值` 后缀）计数，便于一眼看出问题分布。"""
    counts: dict[str, int] = {}
    for r in results:
        if r.get("error"):
            counts[r["error"].split(":")[0]] = counts.get(r["error"].split(":")[0], 0) + 1
        for f in r.get("flags") or []:
            key = f.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="抓取真不复权日线（EXEC 层）")
    ap.add_argument("--codes", default=None, help="逗号分隔；不传则读 --pool-manifest")
    ap.add_argument("--pool-manifest", default="data/pool_manifest.json")
    ap.add_argument("--adj-dir", default="data/qfq_mult/listed", help="ADJ 源目录（用于基准哨兵）")
    ap.add_argument("--out", default="data/exec")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-ratio", type=float, default=6.0, help="不复权/ADJ 起点比值上界，超过视为异常")
    ap.add_argument("--report", default=None, help="自检报告 JSON 路径，默认 <out>/_exec_fetch_report.json")
    ap.add_argument("--sleep", type=float, default=0.25, help="每只之间的抖动间隔（秒）")
    ap.add_argument(
        "--verify-only",
        action="store_true",
        help="不联网：只读 --out 下已有的 *.exec.csv 跑全部哨兵并出报告（可在任何终端复现）",
    )
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    codes = load_codes(args)
    if not codes:
        print("no codes to fetch", file=sys.stderr)
        return 2
    print(f"[exec] {len(codes)} 只 -> {args.out}  区间 {args.start} ~ {args.end}")

    results: list[dict] = []
    t0 = time.time()
    if args.verify_only:
        print("[exec] verify-only 模式：不联网，只校验已有文件")
        for code in codes:
            path = os.path.join(args.out, f"{code}.exec.csv")
            if not os.path.exists(path):
                results.append({"code": code, "ok": False, "flags": ["file_missing"]})
                continue
            results.append(validate_rows(code, read_exec_csv(path), args, do_probe=False))
    else:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {}
            for i, code in enumerate(codes):
                futures[pool.submit(process_one, code, args)] = code
                time.sleep(args.sleep / max(1, args.workers))
            for fut in as_completed(futures):
                rec = fut.result()
                results.append(rec)
                mark = "OK " if rec.get("ok") else "FLAG"
                extra = ""
                if rec.get("flags"):
                    extra = " " + ",".join(rec["flags"])
                print(
                    f"  [{mark}] {rec['code']:<12} rows={rec.get('rows','-'):>5} "
                    f"end={rec.get('end','-'):<10} ratio={rec.get('basis_ratio','-'):<7}{extra}"
                )

    results.sort(key=lambda r: r["code"])
    ok = [r for r in results if r.get("ok")]
    flagged = [r for r in results if not r.get("ok")]
    report = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "out_dir": args.out,
        "start": args.start,
        "end": args.end,
        "n_requested": len(codes),
        "n_ok": len(ok),
        "n_flagged": len(flagged),
        "flag_counts": _flag_counts(results),
        "flagged": [{"code": r["code"], "flags": r.get("flags"), "error": r.get("error")} for r in flagged],
        "elapsed_sec": round(time.time() - t0, 1),
        "results": results,
    }
    report_path = args.report or os.path.join(args.out, "_exec_fetch_report.json")
    with open(report_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(f"\n[exec] ok={len(ok)} flagged={len(flagged)}  elapsed={report['elapsed_sec']}s")
    print(f"[exec] report -> {report_path}")
    return 0 if not flagged else 1


if __name__ == "__main__":
    raise SystemExit(main())
