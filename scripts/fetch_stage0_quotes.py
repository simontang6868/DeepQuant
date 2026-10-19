"""阶段 0a：下载 50 只 csi300 样本的 qfq 行情，与 262 只退市股凑成时点无关池。

为什么需要这个脚本
------------------
`docs/PIT_EXPANSION_PROJECT_2026-10-19.md` 阶段 0 的前提是「池里既有在市股、也有退市股」，
但实测本地只有 6 只手工池 + 1 只 600809 的 qfq 文件，50 只扩样池里 **47 只根本没有行情**。
没有这 47 只，任何"补了退市股就能修幸存者偏差"的说法都是无据的。

口径
----
- 起始日 2016-01-01，与 `scripts/fetch_delist_quotes.py` 完全一致（否则两组不可比）
- 结束日 2026-10-16，与现有数据末端一致
- 前复权 qfq，腾讯源 `stock_zh_a_hist_tx`，与 `data/step75` / `data/delist` 同源
- 输出 `data/stage0/listed/<code>.qfq.csv`（**不入库**，与 `data/delist` 同策略）

不做什么
--------
- 不做任何价格填充。取不到就是取不到，显式记录在 manifest 里。
- 不覆盖已存在的文件（断点续传），避免把别处已验证的文件换掉。
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "data" / "step75" / "universe" / "csi300_50.json"
OUT_DIR = ROOT / "data" / "stage0" / "listed"
MANIFEST = ROOT / "docs" / "STAGE0_LISTED_QUOTES_2026-10-19.json"
START_DATE = "20160101"
END_DATE = "20261016"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（试跑用）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在文件")
    ap.add_argument("--pool", default=str(POOL), help="池 json（默认 csi300_50）")
    ap.add_argument("--manifest", default=str(MANIFEST), help="清单输出路径")
    ap.add_argument("--out-dir", default=str(OUT_DIR), help="行情输出目录")
    args = ap.parse_args()

    import akshare as ak
    import pandas as pd

    pool_path = Path(args.pool)
    out_dir = Path(args.out_dir)
    manifest_path = Path(args.manifest)
    payload = json.loads(pool_path.read_text(encoding="utf-8"))
    stocks = payload["stocks"]
    if args.limit:
        stocks = stocks[: args.limit]
    print(f"[POOL] {pool_path.name} 名单 {len(stocks)} 只，开始补齐行情", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for i, s in enumerate(stocks, 1):
        code = s["code"]
        sym = s["tencent_symbol"]
        path = out_dir / f"{code}.qfq.csv"
        status, n_rows, span = "pending", 0, None

        if path.exists() and path.stat().st_size > 500 and not args.force:
            try:
                df = pd.read_csv(path)
                status, n_rows = "cached", len(df)
                span = [str(df["date"].min()), str(df["date"].max())]
            except Exception as exc:  # noqa: BLE001
                print(f"[BAD   ] {code} 缓存损坏 {exc}", flush=True)
                status = "corrupt"
        if status == "pending":
            try:
                df = ak.stock_zh_a_hist_tx(
                    symbol=sym, start_date=START_DATE, end_date=END_DATE, adjust="qfq"
                )
                if df is None or len(df) == 0:
                    status = "no_quote"
                else:
                    df.to_csv(path, index=False)
                    status, n_rows = "downloaded", len(df)
                    span = [str(df["date"].min()), str(df["date"].max())]
            except Exception as exc:  # noqa: BLE001
                status = "fail"
                print(f"[FAIL  ] {code} {s.get('name','')}: {exc}", flush=True)

        rows.append({
            "code": code,
            "name": s.get("name", ""),
            "board": s.get("board", ""),
            "status": status,
            "n_rows": n_rows,
            "span": span,
            "file": str(path.relative_to(ROOT)).replace("\\", "/") if status in ("cached", "downloaded") else None,
        })
        if i % 10 == 0 or status in ("fail", "no_quote", "corrupt"):
            print(f"[{i:>3}/{len(stocks)}] {code} {status} {n_rows}", flush=True)

    usable = [r for r in rows if r["status"] in ("cached", "downloaded")]
    # pool 可能是相对路径，resolve() 后再求相对，否则 relative_to 会抛 ValueError
    try:
        pool_rel = pool_path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        pool_rel = str(pool_path).replace("\\", "/")
    manifest = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "purpose": "阶段 0a：补齐 csi300 样本的在市股行情，与 262 只退市股组成时点无关池",
        "source": f"akshare stock_zh_a_hist_tx(qfq), start={START_DATE}, end={END_DATE}",
        "pool_file": pool_rel,
        "counts": {
            "pool_size": len(rows),
            "usable": len(usable),
            "downloaded": sum(1 for r in rows if r["status"] == "downloaded"),
            "cached": sum(1 for r in rows if r["status"] == "cached"),
            "no_quote": sum(1 for r in rows if r["status"] == "no_quote"),
            "fail": sum(1 for r in rows if r["status"] == "fail"),
            "total_rows": sum(r["n_rows"] or 0 for r in usable),
        },
        "note": "行情文件在 data/stage0/listed/（不入库）。本清单入库，供复现时按其重建。取不到的不做任何填充。",
        "symbols": rows,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[DONE] usable={len(usable)}/{len(rows)}  rows={manifest['counts']['total_rows']}  -> {manifest_path.name}", flush=True)


if __name__ == "__main__":
    main()
