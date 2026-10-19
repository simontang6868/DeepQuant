"""把沪深300日频数据整理为研究用标准 CSV，并输出覆盖诊断。

支持两种输入：
1. 东财原始 kline 行（``date,open,close,high,low,volume,amount,amplitude``）—— ``csi300_daily_full.raw``
2. westock CLI 输出的 Markdown 表格 —— ``csi300_daily.md``

同时把交易日历一并整理为 ``trade_cal.csv``。

原则：只清洗与对齐，**不做任何插值或填充**。缺失就是缺失。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

DAILY_COLUMNS = ["date", "open", "close", "high", "low", "volume", "amount"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_em_raw(path: Path) -> pd.DataFrame:
    """东财 ``klines`` 原始行：日期,开,收,高,低,成交量,成交额,振幅。"""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3:
            continue
        rows.append(parts[:7])
    df = pd.DataFrame(rows, columns=DAILY_COLUMNS)
    for c in DAILY_COLUMNS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"])


def parse_markdown(path: Path) -> pd.DataFrame:
    """westock CLI 的 Markdown 表格（倒序，最后一行为最早）。"""
    rows = []
    pat = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        rows.append(
            {
                "date": m.group(1),
                "open": m.group(2),
                "close": m.group(3),
                "high": m.group(4),
                "low": m.group(5),
                "volume": m.group(6),
                "amount": m.group(7),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in DAILY_COLUMNS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date"])


def parse_point_sample(path: Path) -> pd.DataFrame:
    """逐点快照采样结果：``date,price,pe,pb,div,quote_date``（无表头）。

    ``date`` 是请求日期，``quote_date`` 是接口实际返回的交易日。
    只保留两者完全一致的记录——不一致说明该请求日非交易日、接口回填了前一日，
    这类记录必须丢弃，不能当作当日价格。
    """
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.strip().split(",")]
        if len(parts) < 6:
            continue
        d, price, _pe, _pb, _div, qd = parts[:6]
        if d != qd:
            continue
        rows.append({"date": d, "close": price})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("open", "high", "low", "volume", "amount"):
        df[c] = pd.NA
    return df.dropna(subset=["date", "close"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--em-raw", default=None, help="东财全历史 klines 原始文件")
    ap.add_argument("--markdown", default=None, help="westock 近 5 年日频 Markdown")
    ap.add_argument(
        "--point-sample",
        action="append",
        default=None,
        help="逐点快照采样文件（可多次指定），用于补齐早期区间",
    )
    ap.add_argument("--calendar", required=True, help="交易日历 CSV")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = []

    frames = []
    if args.em_raw:
        p = Path(args.em_raw)
        if p.exists():
            frames.append(parse_em_raw(p))
            sources.append({"file": p.name, "kind": "eastmoney_kline_raw", "sha256": sha256(p)})
    if args.markdown:
        p = Path(args.markdown)
        if p.exists():
            frames.append(parse_markdown(p))
            sources.append({"file": p.name, "kind": "westock_markdown", "sha256": sha256(p)})
    for ps in args.point_sample or []:
        p = Path(ps)
        if p.exists():
            got = parse_point_sample(p)
            if len(got):
                frames.append(got)
                sources.append(
                    {
                        "file": p.name,
                        "kind": "westock_quote_date_point_sample",
                        "rows": int(len(got)),
                        "sha256": sha256(p),
                        "note": "已丢弃 quote_date != date 的回填记录",
                    }
                )

    if not frames:
        raise SystemExit("没有可用的日频输入文件")

    df = pd.concat(frames, ignore_index=True)
    for c in DAILY_COLUMNS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    df = df[df["close"] > 0]
    df = df[["date"] + DAILY_COLUMNS[1:]]

    dpath = out_dir / "csi300_daily.csv"
    df.to_csv(dpath, index=False, date_format="%Y-%m-%d", float_format="%.4f")

    cal = pd.read_csv(args.calendar)
    cal.columns = [str(c).strip().lstrip("\ufeff") for c in cal.columns]
    col = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
    cal = pd.to_datetime(cal[col], errors="coerce").dropna().sort_values().unique()
    cpath = out_dir / "trade_cal.csv"
    pd.DataFrame({"trade_date": cal}).to_csv(cpath, index=False, date_format="%Y-%m-%d")

    # 覆盖率诊断：日频序列 vs 日历
    cal_idx = pd.DatetimeIndex(cal)
    span = cal_idx[(cal_idx >= df["date"].min()) & (cal_idx <= df["date"].max())]
    prices_idx = pd.DatetimeIndex(df["date"])
    missing = span.difference(prices_idx)
    per_year = df.groupby(df["date"].dt.year).size().to_dict()

    # 识别连续数据段：相邻观测间隔超过 20 个自然日即视为断开。
    # 合并多源数据（如「近 5 年全量 + 早期点采」）必然产生空洞，
    # 必须显式披露，否则下游「250 个观测」可能跨越数年。
    gaps = np.diff(prices_idx.values).astype("timedelta64[D]").astype(int)
    cuts = np.flatnonzero(gaps > 20)
    segments = []
    start = 0
    for c in list(cuts) + [len(prices_idx) - 1]:
        segments.append(
            {
                "start": str(prices_idx[start].date()),
                "end": str(prices_idx[c].date()),
                "rows": int(c - start + 1),
            }
        )
        start = c + 1

    report = {
        "files": {
            "csi300_daily.csv": {
                "rows": int(len(df)),
                "date_min": str(df["date"].min().date()),
                "date_max": str(df["date"].max().date()),
                "sha256": sha256(dpath),
                "sources": sources,
            },
            "trade_cal.csv": {
                "rows": int(len(cal)),
                "date_min": str(cal_idx.min().date()),
                "date_max": str(cal_idx.max().date()),
                "sha256": sha256(cpath),
            },
        },
        "coverage": {
            "calendar_days_in_span": int(len(span)),
            "daily_rows": int(len(df)),
            "missing_trading_days_in_span": int(len(missing)),
            "missing_rate": float(len(missing) / len(span)) if len(span) else None,
            "missing_examples": [str(d.date()) for d in missing[:10]],
            "segments": segments,
            "n_segments": int(len(segments)),
            "rows_per_year": {int(k): int(v) for k, v in per_year.items()},
            "note": (
                "缺失交易日不做任何插值；日频核验时缺失即标记 daily_available=False。"
                "segments 列出连续数据段，段与段之间的空洞不可跨越。"
            ),
        },
    }
    (out_dir / "daily_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
