"""池规模曲线：给定资金，历史每一年的"有效标的数"到底是多少。

为什么需要
----------
历史研究里"6 只池""50 只池"是**名义**规模。实际能下单的标的数取决于每只标的一手成本
与资金约束的关系，而这个关系**逐年变化**（股价涨、送转、上市/退市都会改）。

`docs/OOS_VERDICT_2026-10-19.md` 已证明 600519 自 2021 年起一手 13 万 > 10 万，
使"6 只池"在 2021 年后实际只有 4 只。本脚本把这一只的孤立发现推广成**曲线**：
所有池、所有年份、所有标的，逐年统计可建仓数量。

两种判据，必须分开看
--------------------
1. ``whole_cash``：一手 ≤ 整笔资金 —— 该标的能否**单独开一个账户**。
   这是"独立回测"场景下的判据（本仓库的 walk-forward 就是每只标的一个账户）。
2. ``equal_weight``：一手 ≤ 资金 / N —— 该标的能否**在等权组合里占一份**。
   这是真实组合场景下的判据，比 1 严格得多（N 越大越严）。

两者结论不同是正常的，不是矛盾。报告里必须写清用的是哪一个。

输出
----
``docs/POOL_SIZE_CURVE_2026-10-19.json``
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LOT_SIZE = 100
DEFAULT_POOLS = {
    # step75_ext 目录里有 7 个 csv（含 600809），但**研究池只 6 只**。
    # 目录文件数 ≠ 池规模，不显式给清单就会把"有数据 7"误读成"7 只池"。
    "step75_6": (
        ROOT / "data" / "step75_ext",
        ["600519.SH", "000001.SZ", "601318.SH", "300750.SZ", "000858.SZ", "600900.SH"],
    ),
    "b50_v3": (Path(r"D:/DeepQuant power by txm/b50_v3"), None),
}


def yearly_curve(csv: Path, cash: float, lot: int) -> dict[int, dict]:
    df = pd.read_csv(csv, usecols=["date", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df[df["close"] > 0]
    df["y"] = df["date"].dt.year
    out: dict[int, dict] = {}
    for y, g in df.groupby("y"):
        lo = float(g["close"].min()) * lot
        hi = float(g["close"].max()) * lot
        out[int(y)] = {
            "rows": int(len(g)),
            "one_lot_min": round(lo, 2),
            "one_lot_max": round(hi, 2),
            "affordable_whole": hi <= cash,
            "affordable_whole_lenient": lo <= cash,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="池规模曲线")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--lot", type=int, default=LOT_SIZE)
    ap.add_argument("--out", default=str(ROOT / "docs" / "POOL_SIZE_CURVE_2026-10-19.json"))
    ap.add_argument("--pool", action="append", default=None,
                    help="name=dir 形式，可重复；默认内置两个池")
    args = ap.parse_args()

    pools = DEFAULT_POOLS
    if args.pool:
        pools = {}
        for item in args.pool:
            name, _, d = item.partition("=")
            pools[name] = (Path(d), None)

    payload: dict = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "cash": args.cash,
        "lot_size": args.lot,
        "judgement": (
            "affordable_whole = 窗口内最高收盘×手数 ≤ 整笔资金（独立账户能否建仓，strict）；"
            "affordable_equal = 同一门槛但按等权预算 cash/N 重算（在组合里能否占一份）；"
            "affordable_whole_lenient = 用最低收盘判（曾经有机会买）。"
            "本文件 strict 为主判据。"
        ),
        "pools": {},
    }

    for name, (d, only) in pools.items():
        d = Path(d)
        if only:
            files = [d / f"{c}.qfq.csv" for c in only]
            files = [f for f in files if f.exists()]
            missing = [c for c in only if not (d / f"{c}.qfq.csv").exists()]
            if missing:
                print(f"[WARN] {name} 缺失 {missing}")
        else:
            files = sorted(Path(d).glob("*.qfq.csv"))
        if not files:
            print(f"[SKIP] {name}: {d} 无 csv")
            continue
        n = len(files)
        per_symbol: dict[str, dict] = {}
        for f in files:
            code = f.name.replace(".qfq.csv", "")
            per_symbol[code] = yearly_curve(f, args.cash, args.lot)

        years = sorted({y for v in per_symbol.values() for y in v})
        curve: list[dict] = []
        for y in years:
            have = [c for c, v in per_symbol.items() if y in v]
            whole = [c for c in have if per_symbol[c][y]["affordable_whole"]]
            budget = args.cash / n
            equal = [
                c for c in have
                if per_symbol[c][y]["one_lot_max"] <= budget
            ]
            lenient = [c for c in have if per_symbol[c][y]["affordable_whole_lenient"]]
            curve.append({
                "year": y,
                "n_with_data": len(have),
                "n_whole": len(whole),
                "n_equal": len(equal),
                "n_lenient": len(lenient),
                "equal_budget": round(budget, 2),
                "blocked_whole": sorted(set(have) - set(whole)),
            })
            print(f"{name} {y}  有数据 {len(have):>2}  整笔可建 {len(whole):>2}  "
                  f"等权可建 {len(equal):>2}  (预算 {budget:>8,.0f})")

        first_blocked = {}
        for c, v in per_symbol.items():
            bad = sorted(y for y, r in v.items() if not r["affordable_whole"])
            if bad:
                first_blocked[c] = {"first_blocked_year": bad[0], "blocked_years": bad}

        payload["pools"][name] = {
            "dir": str(d).replace("\\", "/"),
            "n_files": n,
            "curve": curve,
            "first_blocked_by_symbol": first_blocked,
        }
        print(f"[{name}] {n} 只，{len(curve)} 年，被整笔资金阻断的标的 {len(first_blocked)} 只\n")

    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OUT] {out}")


if __name__ == "__main__":
    main()
