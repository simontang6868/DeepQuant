"""量化「加性前复权」对收益率的影响：原口径 vs 乘性口径逐只对照。

口径定义
--------
- 原口径（additive qfq）：`data/step75/`、`data/stage0/listed/`、`data/delist/` 里的 csv
  由腾讯 / akshare 直接给出，`qfq_t = raw_t - 未来累计分红_t`
- 新口径（multiplicative qfq）：`data/qfq_mult/**`，由 `scripts/repair_qfq_mult.py` 生成，
  `qfq_mult_t = hfq_t * (raw_anchor / hfq_anchor)`

报什么
------
逐只：行数、最低收盘（原 / 新）、最大单日绝对收益（原 / 新）、
      |日收益|>50% 的天数（原 / 新）、2017-01-01~2024-12-31 买入持有收益（原 / 新）。
聚合：各数据集的分布，以及「原口径把哪些标的算错到无法使用」的名单。

区间固定为 2017-01-01~2024-12-31，与 `b50_v3` 历史结论同区间，便于对照。
本脚本只读，不改任何数据。

运行
----
    python scripts/audit_qfq_artifact.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "QFQ_ARTIFACT_AUDIT_2026-10-19.json"
START, END = "2017-01-01", "2024-12-31"

DATASETS = {
    "step75": ("data/step75", "data/qfq_mult/step75"),
    "listed": ("data/stage0/listed", "data/qfq_mult/listed"),
    "delist": ("data/delist", "data/qfq_mult/delist"),
}


def load(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return None
    if "date" not in df.columns or len(df) == 0:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    for c in ("open", "high", "low", "close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def metrics(df: pd.DataFrame | None) -> dict | None:
    if df is None:
        return None
    d = df[(df["date"] >= START) & (df["date"] <= END)]
    if len(d) < 2:
        return {"rows": len(d)}
    close = d["close"].astype(float)
    ret = close.pct_change()
    finite = ret.replace([np.inf, -np.inf], np.nan).dropna()
    bh = float(close.iloc[-1] / close.iloc[0] - 1) if close.iloc[0] > 0 else None
    return {
        "rows": int(len(d)),
        "min_close": round(float(close.min()), 4),
        "max_abs_ret": round(float(finite.abs().max()), 4) if len(finite) else None,
        "n_ret_gt_50": int((finite.abs() > 0.5).sum()),
        "n_ret_gt_100": int((finite.abs() > 1.0).sum()),
        "buy_hold": round(bh, 4) if bh is not None else None,
    }


def main() -> None:
    out: dict = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                 "range": [START, END], "datasets": {}}
    for name, (old_dir, new_dir) in DATASETS.items():
        od, nd = ROOT / old_dir, ROOT / new_dir
        files = sorted(p.name.replace(".qfq.csv", "") for p in od.glob("*.qfq.csv"))
        rows = []
        for code in files:
            o = metrics(load(od / f"{code}.qfq.csv"))
            n = metrics(load(nd / f"{code}.qfq.csv"))
            rows.append({"code": code, "old": o, "new": n})

        def count(pred) -> int:
            return sum(1 for r in rows if pred(r))

        summary = {
            "n_files": len(rows),
            "old_nonpositive": count(lambda r: (r["old"] or {}).get("min_close") is not None
                                     and r["old"]["min_close"] <= 0),
            "new_nonpositive": count(lambda r: (r["new"] or {}).get("min_close") is not None
                                     and r["new"]["min_close"] <= 0),
            "old_abs_ret_gt_50": count(lambda r: ((r["old"] or {}).get("max_abs_ret") or 0) > 0.5),
            "new_abs_ret_gt_50": count(lambda r: ((r["new"] or {}).get("max_abs_ret") or 0) > 0.5),
            "missing_new": count(lambda r: r["new"] is None),
        }
        # 口径差最大的一批（按买入持有收益差排序）
        diffs = [
            (r["code"], r["old"]["buy_hold"], r["new"]["buy_hold"],
             round((r["old"]["buy_hold"] or 0) - (r["new"]["buy_hold"] or 0), 4))
            for r in rows
            if (r["old"] or {}).get("buy_hold") is not None and (r["new"] or {}).get("buy_hold") is not None
        ]
        diffs.sort(key=lambda x: -abs(x[3]))
        summary["bh_pairs"] = len(diffs)
        summary["bh_abs_diff_median"] = round(float(np.median([abs(d[3]) for d in diffs])), 4) if diffs else None
        summary["bh_abs_diff_p90"] = round(float(np.percentile([abs(d[3]) for d in diffs], 90)), 4) if diffs else None
        summary["worst_bh_gap"] = [{"code": c, "old": o, "new": n, "gap": g} for c, o, n, g in diffs[:8]]
        out["datasets"][name] = {"summary": summary, "symbols": rows}
        print(f"[{name:6s}] files={summary['n_files']} old<=0:{summary['old_nonpositive']} "
              f"new<=0:{summary['new_nonpositive']} old|r|>50%:{summary['old_abs_ret_gt_50']} "
              f"new|r|>50%:{summary['new_abs_ret_gt_50']} bhΔ中位={summary['bh_abs_diff_median']}")

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OUT] {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
