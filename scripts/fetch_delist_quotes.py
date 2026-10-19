"""构建时点无关股票池（PIT 阶段 0 的数据准备）：把**已退市**的股票补进来。

为什么只补退市股
----------------
幸存者偏差的主体是"**活到今天的才进池**"。现存 A 股（5,575 只）本来就都在，
缺的是那些曾经上市、后来退市的（370 只，其中 2017 年后退市 267 只）。
所以最小可行方案是只补这 267 只，而不是重下全市场。

实测可行性（`probe_out/delist_rate.json`）：
- 退市名单：`ak.stock_info_sh_delist` / `stock_info_sz_delist`，免费，含上市日与退市日；
- 退市股行情：腾讯源对 **2017 年后退市**的抽样 **24/24 可得**；
- 早年退市（2000s 那批）取不到 —— 本脚本会如实记为 `no_quote`，不做任何填充。

输出
----
- ``data/delist/*.qfq.csv`` —— 行情（**不入库**，与 `data/step75` 同策略）
- ``docs/DELIST_UNIVERSE_2026-10-19.json`` —— 清单与逐只状态（**入库**，小文件）

用法
----
    python scripts/fetch_delist_quotes.py              # 全量 267 只（约 5-10 分钟）
    python scripts/fetch_delist_quotes.py --limit 20   # 只跑前 20 只（试跑）
    python scripts/fetch_delist_quotes.py --since 2020-01-01
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "delist"
MANIFEST = ROOT / "docs" / "DELIST_UNIVERSE_2026-10-19.json"
START_DATE = "20160101"


def load_delist_list() -> list[dict]:
    import akshare as ak
    import pandas as pd

    sh = ak.stock_info_sh_delist(symbol="全部")
    sz = ak.stock_info_sz_delist(symbol="终止上市公司")
    sh.columns = ["code", "name", "list_date", "delist_date"]
    sz.columns = ["code", "name", "list_date", "delist_date"]
    rows = []
    for df, mkt in ((sh, "sh"), (sz, "sz")):
        for _, r in df.iterrows():
            rows.append({
                "code": str(r["code"]).zfill(6),
                "name": str(r["name"]),
                "market": mkt,
                "list_date": str(pd.to_datetime(r["list_date"], errors="coerce").date())
                if pd.notna(pd.to_datetime(r["list_date"], errors="coerce")) else None,
                "delist_date": str(pd.to_datetime(r["delist_date"], errors="coerce").date())
                if pd.notna(pd.to_datetime(r["delist_date"], errors="coerce")) else None,
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2017-01-01", help="只取该日期后退市的")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（试跑用）")
    ap.add_argument("--end", default="20261016")
    args = ap.parse_args()

    import akshare as ak
    import pandas as pd

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_delist_list()
    print(f"[LIST] 退市股 {len(rows)} 只")

    picked = [r for r in rows if r["delist_date"] and r["delist_date"] >= args.since]
    picked.sort(key=lambda r: r["delist_date"])
    if args.limit:
        picked = picked[: args.limit]
    print(f"[LIST] {args.since} 后退市 {len(picked)} 只，开始下载")

    ok = empty = fail = 0
    for i, r in enumerate(picked, 1):
        sym = r["market"] + r["code"]
        path = OUT_DIR / f"{r['code']}.{r['market'].upper()}.qfq.csv"
        status, n_rows, span = "pending", 0, None
        if path.exists() and path.stat().st_size > 0:
            try:
                n_rows = len(pd.read_csv(path))
                status = "cached"
            except Exception:
                status = "pending"
        if status == "pending":
            try:
                df = ak.stock_zh_a_hist_tx(
                    symbol=sym, start_date=START_DATE, end_date=args.end, adjust="qfq"
                )
                n = 0 if df is None else len(df)
                if n:
                    df.to_csv(path, index=False)
                    n_rows = n
                    span = f"{df.iloc[0, 0]}~{df.iloc[-1, 0]}"
                    status = "ok"
                else:
                    status = "no_quote"
            except Exception as exc:  # noqa: BLE001
                status = f"error:{type(exc).__name__}"
        r["status"] = status
        r["n_rows"] = n_rows
        r["span"] = span
        if status in ("ok", "cached"):
            ok += 1
        elif status == "no_quote":
            empty += 1
        else:
            fail += 1
        if i % 25 == 0 or i == len(picked):
            print(f"  [{i}/{len(picked)}] ok={ok} no_quote={empty} fail={fail}", flush=True)

    payload = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "purpose": (
            "PIT 阶段 0：把已退市股票补进池，构造时点无关池（每个时点只用当时已上市且未退市的标的）。"
            "仅补 2017 年后退市的，早年退市股行情源不可得（见 no_quote）。"
        ),
        "source": "akshare stock_info_sh_delist / stock_info_sz_delist + stock_zh_a_hist_tx(qfq)",
        "since": args.since,
        "counts": {"listed_total": len(rows), "picked": len(picked),
                   "ok": ok, "no_quote": empty, "fail": fail},
        "note": (
            "行情文件在 data/delist/（不入库）。本清单入库，供复现时按其重建。"
            "no_quote 的标的不做任何价格填充——宁可缺口显式化，不要伪造数据。"
        ),
        "symbols": picked,
    }
    MANIFEST.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OUT] {MANIFEST}   ok={ok} no_quote={empty} fail={fail}")


if __name__ == "__main__":
    main()
