"""审计乘性前复权（`data/qfq_mult/**`）的拼接完整性。

背景
----
`scripts/repair_qfq_mult.py` 用腾讯 hfq 序列做乘性前复权：

    qfq_mult_t = hfq_t × (raw_anchor / hfq_anchor)

hfq 是按 640 根/段**回翻式分页**抓取后拼接的。一旦某一段错位、重复或因子错乱，
拼接出的序列会出现**凭空的日收益率**——价格全为正，**非正价闸门拦不住**，
只看价格曲线也看不出来，只有收益率序列会暴露。

本脚本不比较「乘性与加性的水平值之比」：加性口径在除权日附近本来就分段偏移，
比值不恒定是它的**固有性质**，不能当作乘性出错的证据（这是第一版判据的错误）。
正确的诊断对象是**收益率序列本身**。

判据（全部限定在研究区间 2017-01-01 ~ 2024-12-31 内）
--------------------------------------------------
1. **非正价**：`close <= 0` 的交易日数 —— 修复的目标就是让它为 0；
2. **超涨跌停**：A 股主板日涨跌幅上限 10%（创业板/科创板 20%），取 21% 作宽松上限；
   单日 |收益率| > 21% 只可能是复权拼接错误或数据源脏数据；
3. **与加性口径的收益率分叉**：在加性口径**未失真**的日子（加性价 > 0 且远离 0）
   两口径日收益率应当一致，分叉日即为可疑拼接点。

输出 json + 控制台摘要。**只读**，不修改任何行情文件。

用法
----
    python scripts/audit_qfq_mult_drift.py
    python scripts/audit_qfq_mult_drift.py --top 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

STUDY_START = pd.Timestamp("2017-01-01")
STUDY_END = pd.Timestamp("2024-12-31")

# 加性口径源目录 → 乘性口径目录
PAIRS = [
    ("data/stage0/listed", "data/qfq_mult/listed"),
    ("data/delist", "data/qfq_mult/delist"),
    ("data/step75", "data/qfq_mult/step75"),
]

LIMIT = 0.21
"""A 股单日涨跌幅宽松上限（科创/创业 20%，留 1pp 给四舍五入）。"""

NEAR_ZERO = 1.0
"""判定「加性口径已失真」的阈值：价格低于 1 元时，加性偏移会主导数值。"""

FORK_TOL = 0.01
"""两口径日收益率差异超过 1pp 即记为分叉。"""


def load_close(path: Path) -> pd.Series:
    df = pd.read_csv(path)
    if "date" not in df.columns or "close" not in df.columns:
        raise KeyError(f"{path}: 缺少 date/close 列")
    s = df[["date", "close"]].copy()
    s["date"] = pd.to_datetime(s["date"], errors="coerce")
    s["close"] = pd.to_numeric(s["close"], errors="coerce")
    s = s.dropna(subset=["date"]).sort_values("date")
    return s.set_index("date")["close"]


def audit_one(add: pd.Series, mul: pd.Series) -> dict:
    j = pd.concat([add.rename("add"), mul.rename("mul")], axis=1, join="inner").dropna()
    j = j[(j.index >= STUDY_START) & (j.index <= STUDY_END)]
    if len(j) < 3:
        return {"n": int(len(j)), "ok": None, "reason": "研究区间内样本不足"}

    m = j["mul"]
    a = j["add"]
    n_nonpos_mul = int((m <= 0).sum())
    n_nonpos_add = int((a <= 0).sum())

    rm = m.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    ra = a.pct_change().replace([np.inf, -np.inf], np.nan).dropna()

    n_over_limit = int((rm.abs() > LIMIT).sum())
    worst_date = None
    worst_ret = None
    if n_over_limit:
        idx = rm.abs().idxmax()
        worst_date = str(idx.date())
        worst_ret = round(float(rm.loc[idx]), 6)

    # 分叉：只看加性口径未失真的日子（加性价 > NEAR_ZERO）
    clean = j[j["add"] > NEAR_ZERO].index
    rm2 = rm.reindex(clean).dropna()
    ra2 = ra.reindex(clean).dropna()
    common = rm2.index.intersection(ra2.index)
    d = (rm2.loc[common] - ra2.loc[common]).abs()
    n_fork = int((d > FORK_TOL).sum())
    fork_dates = [str(x.date()) for x in d[d > FORK_TOL].index[:8]]

    return {
        "n": int(len(j)),
        "span": [str(j.index[0].date()), str(j.index[-1].date())],
        "nonpos_mul": n_nonpos_mul,
        "nonpos_add": n_nonpos_add,
        "n_over_price_limit": n_over_limit,
        "worst_over_limit_date": worst_date,
        "worst_over_limit_ret": worst_ret,
        "clean_days": int(len(common)),
        "n_ret_fork": n_fork,
        "ret_fork_dates": fork_dates,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="乘性前复权拼接完整性审计")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", default="docs/QFQ_MULT_DRIFT_AUDIT_2026-10-19.json")
    args = ap.parse_args()

    rows: list[dict] = []
    for add_dir, mul_dir in PAIRS:
        add_root, mul_root = ROOT / add_dir, ROOT / mul_dir
        if not add_root.exists() or not mul_root.exists():
            continue
        group = Path(mul_dir).name
        for mp in sorted(mul_root.glob("*.qfq.csv")):
            ap_ = add_root / mp.name
            code = mp.name.split(".")[0] + "." + mp.name.split(".")[1]
            if not ap_.exists():
                rows.append({"group": group, "code": code, "status": "no_pair"})
                continue
            try:
                res = audit_one(load_close(ap_), load_close(mp))
            except Exception as exc:  # noqa: BLE001
                rows.append({"group": group, "code": code, "status": "read_failed",
                             "reason": str(exc)[:160]})
                continue
            rows.append({"group": group, "code": code, "status": "ok", **res})

    ok = [r for r in rows if r.get("status") == "ok"]
    nonpos = [r for r in ok if r.get("nonpos_mul", 0) > 0]
    over = [r for r in ok if r.get("n_over_price_limit", 0) > 0]
    fork = [r for r in ok if r.get("n_ret_fork", 0) > 0]

    by_group: dict[str, dict] = {}
    for g in sorted({r["group"] for r in rows}):
        sub = [r for r in ok if r["group"] == g]
        by_group[g] = {
            "n_symbols": len(sub),
            "n_nonpos": len([r for r in sub if r.get("nonpos_mul", 0) > 0]),
            "n_over_limit": len([r for r in sub if r.get("n_over_price_limit", 0) > 0]),
            "n_ret_fork": len([r for r in sub if r.get("n_ret_fork", 0) > 0]),
            "n_days": int(sum(r["n"] for r in sub)),
        }

    payload = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "purpose": (
            "检查 repair_qfq_mult.py 的 hfq 分页拼接是否引入虚假日收益率。"
            "诊断对象是收益率序列，不是价格水平（加性口径分段偏移属固有性质，不构成证据）"
        ),
        "study_window": {"start": str(STUDY_START.date()), "end": str(STUDY_END.date())},
        "criteria": {
            "price_limit": LIMIT,
            "near_zero_add_threshold": NEAR_ZERO,
            "ret_fork_tol": FORK_TOL,
        },
        "counts": {
            "n_scanned": len(rows),
            "n_ok": len(ok),
            "n_days": int(sum(r["n"] for r in ok)),
            "n_symbols_nonpos": len(nonpos),
            "n_symbols_over_limit": len(over),
            "n_symbols_ret_fork": len(fork),
        },
        "by_group": by_group,
        "worst_over_limit": sorted(
            [r for r in ok if r.get("n_over_price_limit", 0) > 0],
            key=lambda r: -abs(r.get("worst_over_limit_ret") or 0))[:args.top],
        "symbols": rows,
    }

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    c = payload["counts"]
    print(f"[SCAN] 扫描 {c['n_scanned']} 只，可比 {c['n_ok']} 只，研究区间内 {c['n_days']} 个交易日")
    print(f"[1] 乘性口径非正价标的：{c['n_symbols_nonpos']} 只")
    print(f"[2] 单日收益超涨跌停上限({LIMIT:.0%})的标的：{c['n_symbols_over_limit']} 只")
    print(f"[3] 与加性口径收益率分叉的标的：{c['n_symbols_ret_fork']} 只")
    print()
    for g, v in by_group.items():
        print(f"  {g:8s} 标的 {v['n_symbols']:4d}  天数 {v['n_days']:7d}  "
              f"非正价 {v['n_nonpos']:3d}  超限 {v['n_over_limit']:3d}  分叉 {v['n_ret_fork']:3d}")
    if payload["worst_over_limit"]:
        print("\n[超限最严重的标的]")
        for r in payload["worst_over_limit"]:
            print(f"  {r['code']:12s} {r['group']:8s} 超限 {r['n_over_price_limit']:3d} 天；"
                  f"最大 {r['worst_over_limit_ret']:+.2%} @ {r['worst_over_limit_date']}")
    print(f"\n[OUT] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
