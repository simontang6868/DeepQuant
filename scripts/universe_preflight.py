"""股票池资金充足性预检（只读，不改变任何既有行为）。

为什么需要它
------------
`docs/OOS_VERDICT_2026-10-19.md` §5 发现：`initial_cash=100_000` 与 A 股 100 股整手规则相互作用，
使 **600519.SH 从 2021 年起一手成本 13.25 万 > 10 万，结构上无法建仓**。
后果不是报错，而是**静默的**：
  - walk-forward 的训练期里所有候选都零成交 → `min_trades=2` 不满足 → *选择失败*；
  - 该标的在样本外**整段消失**，而汇总表仍写"6 只池"；
  - 更隐蔽的是方向：茅台买不起的那段，如果它本来会亏，池子其实是**被有利地**缩小了。

引擎行为是对的（不足一手就拒单，符合 A 股规则）。缺的是**跑研究之前的一句声明**。
本脚本把这句话变成一条命令。

用法
----
    # 查默认 6 只池在 10 万本金下逐年能否建仓
    python scripts/universe_preflight.py

    # 换资金 / 换池 / 换数据目录
    python scripts/universe_preflight.py --cash 300000 --universe data/step75/universe/csi300_50.json
    python scripts/universe_preflight.py --data-dir data/step75_ext --cash 500000

    # CI 用：有任何标的不胜任则返回非 0
    python scripts/universe_preflight.py --strict

判据
----
`一手成本 = 该年最低收盘价 × lot_size`。只要**年内任一时点**买得起（最低价买得起），
就算该年可建仓 —— 这是最宽松的判据，因此被判"不可建仓"的年份是**确定无疑**买不起的。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]

DEFAULT_POOL = ["600519.SH", "000001.SZ", "601318.SH", "300750.SZ", "000858.SZ", "600900.SH"]


def load_pool(args) -> list[str]:
    if args.codes:
        return [c.strip() for c in args.codes.split(",") if c.strip()]
    if args.universe:
        payload = json.loads((REPO / args.universe).read_text(encoding="utf-8"))
        return [s["code"] for s in payload["stocks"]]
    return list(DEFAULT_POOL)


def one_lot_by_year(path: Path, lot_size: int) -> dict[int, float]:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"])
    df["y"] = df["date"].dt.year
    return {int(y): float(g["close"].min()) * lot_size for y, g in df.groupby("y")}


def main() -> int:
    ap = argparse.ArgumentParser(description="股票池资金充足性预检（只读）")
    ap.add_argument("--data-dir", default="data/step75_ext", help="行情 CSV 目录（默认延伸数据）")
    ap.add_argument("--cash", type=float, default=100_000.0, help="初始资金（默认 10 万，与 research.py 一致）")
    ap.add_argument("--lot-size", type=int, default=100, help="整手股数（A 股默认 100）")
    ap.add_argument("--codes", default="", help="逗号分隔的标的代码，覆盖默认池")
    ap.add_argument("--universe", default="", help="universe JSON（含 stocks[].code），覆盖默认池")
    ap.add_argument("--strict", action="store_true", help="任一标的存在不可建仓年份则返回 1")
    args = ap.parse_args()

    pool = load_pool(args)
    data_dir = REPO / args.data_dir
    print(f"资金 {args.cash:,.0f} / 整手 {args.lot_size} / 数据 {args.data_dir} / 池 {len(pool)} 只\n")

    header_printed = False
    years: list[int] = []
    verdicts: dict[str, dict] = {}
    rows: list[tuple[str, dict[int, float], list[int]]] = []
    for code in pool:
        p = data_dir / f"{code}.qfq.csv"
        if not p.exists():
            print(f"[MISS ] {code} 数据缺失：{p}")
            verdicts[code] = {"missing": True}
            continue
        yearly = one_lot_by_year(p, args.lot_size)
        bad = sorted(y for y, c in yearly.items() if c > args.cash)
        years = sorted(set(years) | set(yearly))   # 并集：不同标的上市年份不同，必须对齐
        rows.append((code, yearly, bad))
        verdicts[code] = {"one_lot_by_year": yearly, "unaffordable_years": bad}

    if rows:
        print(f"{'code':11s} " + " ".join(f"{y:>8d}" for y in years) + "   首次不可建仓")
        print("-" * (11 + 9 * len(years) + 14))
        for code, yearly, bad in rows:
            cells = []
            for y in years:
                cells.append("       —" if y not in yearly else f"{yearly[y] / 1000:>8.1f}k")
            print(f"{code:11s} " + " ".join(cells) + f"   {bad[0] if bad else '—'}")

    print("\n（单位 k = 千元；数字是该年**最低**收盘价买一手所需金额，最宽松口径）")

    broken = {c: v for c, v in verdicts.items() if v.get("unaffordable_years") or v.get("missing")}
    if not broken:
        print("\n[OK] 全部标的在全部年份可建仓。")
        return 0
    print(f"\n[WARN] {len(broken)}/{len(pool)} 只存在不可建仓年份 —— 这些年份里它们不构成有效样本：")
    for c, v in broken.items():
        if v.get("missing"):
            print(f"  {c}: 数据缺失")
        else:
            print(f"  {c}: {', '.join(map(str, v['unaffordable_years']))}")
    print("\n声明要求：任何引用这些年份的结论都必须写明「池规模在此时段实际为 N-k 只」，"
          "否则等于把'买不起'当成'没信号'。")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
