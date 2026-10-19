"""长区间结论的口径重算：把凡是建立在加性前复权上的"多年涨跌"重算一遍。

为什么必须重算
--------------
加性前复权 `qfq_t = raw_t − 未来累计分红_t`，在多年跨度上累计分红可以**超过**起始价，
把历史价压到趋零甚至为负。于是「2017 年买入持有到 2024 年赚多少」这个问题，
在两种口径下能差一个数量级：600519 加性 +3260.7% / 乘性 +385.6%（差 2875pp）。

这不是精度问题，而是**结论会翻号**：某只票在乘性口径下是小幅上涨，在加性口径下可能是暴涨。
任何引用"某池多年收益"的文档，都必须先过这张表。

本脚本只做价格数学：
  - 不跑回测、不看策略、不产生任何关于"策略有效"的判断；
  - 所有区间用两口径的**共同交易日**（inner join），保证不是拿长度不同的序列比涨跌。

用法：
    python scripts/long_horizon_caliber_recalc.py
    python scripts/long_horizon_caliber_recalc.py --pools 6,50
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

PAIRS = {
    "pool6": {
        "label": "6 只主池",
        "additive": ROOT / "data" / "step75",
        # `data/step75` 的加性行情只到 2024-12-31；延伸到 2026-10 的加性在 step75_ext。
        # 不分开的话，"全段"会被 inner join 截回 2024-12-31，两个窗口算出同一组数（踩过）。
        "additive_ext": ROOT / "data" / "step75_ext",
        "mult": ROOT / "data" / "qfq_mult" / "step75",
        "mult_fallback": ROOT / "data" / "qfq_mult" / "listed",
        "universe": None,
    },
    "pool50": {
        "label": "50 只扩样池（csi300_50）",
        "additive": ROOT / "data" / "stage0" / "listed",
        "additive_ext": None,
        "mult": ROOT / "data" / "qfq_mult" / "listed",
        "mult_fallback": None,
        "universe": ROOT / "data" / "step75" / "universe" / "csi300_50.json",
    },
}

WINDOWS = [
    ("is_2017_2024", "2017-01-03", "2024-12-31"),
    ("full_2017_2026", "2017-01-03", "2026-10-16"),
]


def read_close(p: Path) -> pd.Series | None:
    if not p.exists():
        return None
    df = pd.read_csv(p)
    if "close" not in df.columns or "date" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    df = df[df["close"] > 0]
    if df.empty:
        return None
    return df.set_index("date")["close"]


def bh(a: pd.Series, b: pd.Series, start: str, end: str) -> dict | None:
    """两口径在**共同交易日**上的买入持有收益。"""
    j = pd.concat([a.rename("add"), b.rename("mul")], axis=1, join="inner").sort_index()
    j = j.loc[(j.index >= start) & (j.index <= end)]
    if len(j) < 2:
        return None
    ra = float(j["add"].iloc[-1] / j["add"].iloc[0] - 1.0)
    rm = float(j["mul"].iloc[-1] / j["mul"].iloc[0] - 1.0)
    return {"n": int(len(j)), "additive": ra, "mult": rm,
            "delta_pp": round((ra - rm) * 100, 1),
            "sign_flip": bool((ra > 0) != (rm > 0))}


def resolve_mult(pair: dict, code: str) -> pd.Series | None:
    for key in ("mult", "mult_fallback"):
        d = pair.get(key)
        if d is None:
            continue
        s = read_close(Path(d) / f"{code}.qfq.csv")
        if s is not None:
            return s
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="长区间收益的口径重算")
    ap.add_argument("--pools", default="6,50")
    ap.add_argument("--out", default="docs/LONG_HORIZON_CALIBER_RECALC_2026-10-19.json")
    args = ap.parse_args()

    wanted = [f"pool{p.strip()}" for p in args.pools.split(",") if f"pool{p.strip()}" in PAIRS]
    out = {"generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
           "windows": [{"id": w[0], "start": w[1], "end": w[2]} for w in WINDOWS],
           "pools": {}}

    for key in wanted:
        pair = PAIRS[key]
        codes = None
        if pair["universe"]:
            u = json.loads(Path(pair["universe"]).read_text(encoding="utf-8"))
            codes = [s["code"] for s in u["stocks"]]
        else:
            codes = sorted(p.stem.replace(".qfq", "")
                           for p in Path(pair["additive"]).glob("*.qfq.csv"))

        rows, skipped = [], []
        for c in codes:
            a = read_close(Path(pair["additive"]) / f"{c}.qfq.csv")
            a_ext = (read_close(Path(pair["additive_ext"]) / f"{c}.qfq.csv")
                     if pair.get("additive_ext") else None)
            m = resolve_mult(pair, c)
            if a is None or m is None:
                skipped.append({"code": c, "reason": "缺加性" if a is None else "缺乘性"})
                continue
            rec = {"code": c}
            for wid, s, e in WINDOWS:
                # 窗口超出加性主文件的覆盖范围时，改用已延伸的加性文件
                src = a
                if a_ext is not None and pd.Timestamp(e) > a.index.max():
                    src = a_ext
                rec[wid] = bh(src, m, s, e)
            rows.append(rec)

        stats = {}
        for wid, _, _ in WINDOWS:
            add = [r[wid]["additive"] for r in rows if r[wid]]
            mul = [r[wid]["mult"] for r in rows if r[wid]]
            flip = [r["code"] for r in rows if r[wid] and r[wid]["sign_flip"]]
            if not add:
                continue
            stats[wid] = {
                "n": len(add),
                "additive_median": round(float(np.median(add)), 4),
                "mult_median": round(float(np.median(mul)), 4),
                "delta_median_pp": round(float(np.median(add) - np.median(mul)) * 100, 1),
                "additive_p10_p90": [round(float(np.percentile(add, 10)), 4),
                                     round(float(np.percentile(add, 90)), 4)],
                "mult_p10_p90": [round(float(np.percentile(mul, 10)), 4),
                                 round(float(np.percentile(mul, 90)), 4)],
                "n_sign_flip": len(flip),
                "sign_flip_codes": flip,
                "max_delta_pp": round(max((r[wid]["delta_pp"] for r in rows if r[wid])), 1),
            }

        out["pools"][key] = {
            "label": pair["label"],
            "n_codes": len(codes),
            "n_usable": len(rows),
            "skipped": skipped,
            "stats": stats,
            "rows": [
                {
                    "code": r["code"],
                    **{wid: (None if r[wid] is None else {
                        "additive": round(r[wid]["additive"], 4),
                        "mult": round(r[wid]["mult"], 4),
                        "delta_pp": r[wid]["delta_pp"],
                        "sign_flip": r[wid]["sign_flip"],
                    }) for wid, _, _ in WINDOWS},
                }
                for r in rows
            ],
        }

        p = out["pools"][key]
        print(f"\n== {pair['label']}：可用 {p['n_usable']}/{p['n_codes']}，跳过 {len(skipped)} ==")
        for wid, s, e in WINDOWS:
            st = stats.get(wid)
            if not st:
                print(f"  {wid}: 无数据")
                continue
            print(f"  {wid} ({s}~{e}, n={st['n']})")
            print(f"    加性中位 {st['additive_median']:+.2%}  乘性中位 {st['mult_median']:+.2%}  "
                  f"差 {st['delta_median_pp']:+.1f}pp")
            print(f"    加性 p10~p90 {st['additive_p10_p90'][0]:+.1%}~{st['additive_p10_p90'][1]:+.1%}"
                  f" | 乘性 {st['mult_p10_p90'][0]:+.1%}~{st['mult_p10_p90'][1]:+.1%}")
            print(f"    符号翻转 {st['n_sign_flip']} 只（{st['sign_flip_codes'][:8]}）"
                  f" 最大单只差 {st['max_delta_pp']:+.0f}pp")

    (ROOT / args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
