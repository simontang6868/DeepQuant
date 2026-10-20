"""P2-1：财报 PIT 抽验——"最新公告日期"是否等于首次披露日？

背景
----
路线图 P2-1 要求：抽样 ≥20 只 × ≥8 个报告期，核对财报"最新公告日期"是否等于首次披露日。
若否，则任何用该字段做 PIT 的因子都会**前视偏差**（用一年后才修订的信息算历史）。

本脚本做什么
------------
1. 直连东财数据接口 `RPT_LICO_FN_CPD`，取每期的 `NOTICE_DATE`（公告日）与 `UPDATE_DATE`（记录更新日）。
2. 对 20 只 × 8 期逐对比较：
   - `NOTICE_DATE` 是否落在法定披露窗口内（验证它=真实首次披露）；
   - `UPDATE_DATE` 是否 = `NOTICE_DATE`（若系统性晚约一年 → akshare 字段不可作 PIT）。
3. 复现 akshare `stock_yjbb_em` 的字段映射，量化其把 `UPDATE_DATE` 当"最新公告日期"的偏差。

不做什么
--------
- 不改任何数据；只读。
- 不下结论说"财报不可用"，只回答"**哪个字段**可用、哪个不可用"。

运行
----
    python scripts/p2_1_financial_pit_check.py
    python scripts/p2_1_financial_pit_check.py --limit 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "P2_1_FINANCIAL_PIT_2026-10-20.json"

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}

# 8 个报告期（2022–2024）
PERIODS = ["20221231", "20230331", "20230630", "20230930",
           "20231231", "20240331", "20240630", "20240930"]

# 法定披露截止（次年/当年 月-日）
STATUTORY = {
    "1231": ("0430", +1),  # 年报：次年 4/30
    "0331": ("0430", 0),   # 一季报：当年 4/30
    "0630": ("0831", 0),   # 半年报：当年 8/31
    "0930": ("1031", 0),   # 三季报：当年 10/31
}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))


def fetch_period(period: str, codes: list[str]) -> list[dict]:
    """取某一期、指定代码集合的记录（IN 过滤一次拿全）。"""
    rd = f"{period[:4]}-{period[4:6]}-{period[6:]}"
    inlist = ",".join(f'"{c}"' for c in codes)
    params = {
        "sortColumns": "UPDATE_DATE,SECURITY_CODE", "sortTypes": "-1,-1",
        "pageSize": "500", "pageNumber": "1", "reportName": "RPT_LICO_FN_CPD",
        "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,REPORTDATE,NOTICE_DATE,UPDATE_DATE",
        "filter": f"(REPORTDATE='{rd}')(SECURITY_CODE in ({inlist}))",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    d = _get(url)
    return (d.get("result") or {}).get("data") or []


def statutory_deadline(period: str) -> date:
    mmdd, add_year = STATUTORY[period[4:]]
    y = int(period[:4]) + add_year
    return date(y, int(mmdd[:2]), int(mmdd[2:]))


def _d(v) -> date | None:
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="抽样只数（默认 20）")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    pool = json.loads((ROOT / "data" / "pool_manifest.json").read_text(encoding="utf-8"))
    codes = [s["code"].split(".")[0] for s in pool["symbols"]][: args.limit]
    names = {s["code"].split(".")[0]: s.get("name") for s in pool["symbols"]}
    print(f"[POOL] 抽样 {len(codes)} 只 × {len(PERIODS)} 期", flush=True)

    pairs: list[dict] = []
    for p in PERIODS:
        rows = fetch_period(p, codes)
        dl = statutory_deadline(p)
        for r in rows:
            nd, ud = _d(r.get("NOTICE_DATE")), _d(r.get("UPDATE_DATE"))
            if nd is None:
                continue
            pairs.append({
                "code": r["SECURITY_CODE"], "name": r.get("SECURITY_NAME_ABBR"),
                "period": p,
                "notice_date": nd.isoformat(), "update_date": ud.isoformat() if ud else None,
                "statutory_deadline": dl.isoformat(),
                "notice_within_deadline": nd <= dl,
                "update_within_deadline": (ud <= dl) if ud else None,
                "update_minus_notice_days": (ud - nd).days if ud else None,
                "same": (ud == nd) if ud else None,
            })
        print(f"[{p}] 取到 {len(rows)} 条", flush=True)

    n = len(pairs)
    same = sum(1 for x in pairs if x["same"])
    late = [x["update_minus_notice_days"] for x in pairs if x["update_minus_notice_days"] is not None]
    notice_ok = sum(1 for x in pairs if x["notice_within_deadline"])
    update_ok = sum(1 for x in pairs if x["update_within_deadline"])

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "periods": PERIODS,
        "n_stocks": args.limit,
        "n_pairs": n,
        # NOTICE_DATE：真实公告日
        "notice_within_statutory": notice_ok,
        "notice_within_statutory_pct": round(notice_ok / n * 100, 2) if n else None,
        # UPDATE_DATE：akshare 当作"最新公告日期"的字段
        "update_equals_notice": same,
        "update_equals_notice_pct": round(same / n * 100, 2) if n else None,
        "update_within_statutory": update_ok,
        "update_within_statutory_pct": round(update_ok / n * 100, 2) if n else None,
        "update_minus_notice_days": {
            "min": min(late) if late else None,
            "median": statistics.median(late) if late else None,
            "max": max(late) if late else None,
            "mean": round(statistics.mean(late), 1) if late else None,
        } if late else None,
        "verdict": None,
        "pairs": pairs,
    }

    # 判定：若 UPDATE_DATE 与 NOTICE_DATE 大面积不等、且 UPDATE_DATE 系统性越过法定窗口，
    # 则该字段（akshare 的"最新公告日期"）不可作 PIT；NOTICE_DATE 可用（若其绝大多数落在窗口内）。
    if n and summary["update_equals_notice_pct"] is not None:
        if summary["update_equals_notice_pct"] < 50 and summary["notice_within_statutory_pct"] >= 90:
            summary["verdict"] = "FAIL_as_exposed__USE_NOTICE_DATE"
            summary["verdict_zh"] = ("akshare「最新公告日期」(=UPDATE_DATE) 与真实首次披露 NOTICE_DATE 大面积不等，"
                                     "不可作 PIT；原始字段 NOTICE_DATE 可用（绝大多数落在法定窗口内）。")
        else:
            summary["verdict"] = "NEEDS_REVIEW"
            summary["verdict_zh"] = "结果不满足预期模式，需人工复核。"

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n=== 汇总 ===")
    print(f"样本对: {n}（{args.limit} 只 × {len(PERIODS)} 期）")
    print(f"NOTICE_DATE 落在法定窗口内: {notice_ok}/{n}（{summary['notice_within_statutory_pct']}%）")
    print(f"UPDATE_DATE == NOTICE_DATE: {same}/{n}（{summary['update_equals_notice_pct']}%）")
    print(f"UPDATE_DATE 落在法定窗口内: {update_ok}/{n}（{summary['update_within_statutory_pct']}%）")
    print(f"UPDATE−NOTICE 天数: {summary['update_minus_notice_days']}")
    print(f"判定: {summary['verdict']}")
    print(f"[FILE] {args.out}")


if __name__ == "__main__":
    main()
