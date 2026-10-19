"""A 股「每年买入上一年涨幅冠军」回测。

口径见 docs/CHAMPION_PREREG_2026-10-20.md（先冻结后跑，跑完不得改参数）。
美股版本跑不了 —— DeepQuant 只有 Shiller 宏观面板，没有美股个股行情。
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QDIR = ROOT / "data" / "qfq_mult"
BENCH = ROOT / "data" / "benchmark" / "hs300_daily.csv"

START_YEAR = 2017          # 需要 2016 全年涨幅做第一次选择
END_YEAR = 2025            # 9 个完整年
MIN_TRADING_DAYS = 200     # 排序期资格
LIMIT_UP = 0.098           # 开盘涨幅 >= 此值标注「可能买不进」

POOLS = {
    "P_L": ("在市（csi300 当日成分，有幸存者偏差）", QDIR / "listed"),
    "P_F": ("并集（在市 + 2017 后退市全量）", None),
}


@dataclass
class Symbol:
    code: str
    bars: list[tuple[str, float, float]] = field(default_factory=list)  # (date, open, close)


def load_csv(path: Path) -> list[tuple[str, float, float]]:
    out = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out.append((row["date"], float(row["open"]), float(row["close"])))
            except (ValueError, KeyError):
                continue
    return out


def load_pool(dirs: list[Path]) -> list[Symbol]:
    syms = []
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.qfq.csv")):
            bars = load_csv(p)
            if bars:
                syms.append(Symbol(p.name.split(".")[0] + "." + p.name.split(".")[1], bars))
    return syms


def year_bars(bars, year: int):
    """该自然年的 (日期, 开, 收)；不足 MIN_TRADING_DAYS 返回 None。"""
    rows = [b for b in bars if b[0][:4] == str(year)]
    return rows if len(rows) >= MIN_TRADING_DAYS else None


def prev_close(bars, year: int) -> float | None:
    """第 year 年之前最后一个收盘价（用于算第 year 年涨幅的分母）。"""
    prev = [b for b in bars if b[0][:4] < str(year)]
    return prev[-1][2] if prev else None


def run(pool_name: str, syms: list[Symbol]) -> dict:
    years = list(range(START_YEAR, END_YEAR + 1))

    # 预计算：每只股票每年的（首日开, 末日收, 年末涨幅, 上年末收）
    info: dict[str, dict[int, dict]] = {}
    for s in syms:
        per = {}
        for y in years:
            cur = year_bars(s.bars, y)
            if not cur:
                continue
            base = prev_close(s.bars, y)
            if base is None or base <= 0:
                continue
            per[y] = {
                "open": cur[0][1],
                "close": cur[-1][2],
                "prev_close": base,
                "gain": cur[-1][2] / base - 1.0,      # 第 y 年全年涨幅
                "n_days": len(cur),
            }
        if per:
            info[s.code] = per

    results = {}
    for rank in (1, 2, 3):                            # 预先定死三档
        nav, rows, mdd_peak, mdd = 1.0, [], 1.0, 0.0
        for y in years:
            sort_year = y - 1
            cands = []
            for code, per in info.items():
                if sort_year in per and y in per:
                    cands.append((per[sort_year]["gain"], code))
            cands.sort(key=lambda t: (-t[0], t[1]))   # 降序，并列按代码
            if len(cands) < rank:
                rows.append({"year": y, "code": None, "note": f"候选不足 {rank} 个"})
                continue

            gain_prev, code = cands[rank - 1]
            py = info[code][y]
            hold = py["close"] / py["open"] - 1.0     # 首日开盘买 -> 末日收盘卖
            nav *= (1.0 + hold)
            mdd_peak = max(mdd_peak, nav)
            mdd = max(mdd, 1.0 - nav / mdd_peak)
            rows.append({
                "year": y, "code": code, "prev_gain": gain_prev,
                "hold": hold, "nav": nav,
                "open_gap": py["open"] / py["prev_close"] - 1.0,
                "n_days": py["n_days"],
                "limit_up_risk": py["open"] / py["prev_close"] - 1.0 >= LIMIT_UP,
            })

        n_years = len([r for r in rows if r.get("code")])
        total = nav - 1.0
        cagr = nav ** (1.0 / n_years) - 1.0 if n_years and nav > 0 else None
        results[f"top{rank}"] = {
            "nav": nav, "total_return": total, "cagr": cagr,
            "max_drawdown": mdd, "years": rows,
        }
    return {"pool": pool_name, "n_symbols": len(info), "results": results}


def bench() -> dict:
    bars = load_csv(BENCH)
    nav, rows, peak, mdd = 1.0, [], 1.0, 0.0
    for y in range(START_YEAR, END_YEAR + 1):
        cur = [b for b in bars if b[0][:4] == str(y)]
        if not cur:
            continue
        hold = cur[-1][2] / cur[0][1] - 1.0
        nav *= (1.0 + hold)
        peak = max(peak, nav)
        mdd = max(mdd, 1.0 - nav / peak)
        rows.append({"year": y, "hold": hold, "nav": nav})
    n = len(rows)
    return {"nav": nav, "cagr": nav ** (1 / n) - 1.0 if n else None,
            "max_drawdown": mdd, "years": rows}


def main() -> None:
    listed = load_pool([QDIR / "listed"])
    delisted = load_pool([QDIR / "delist"])
    print(f"[POOL] 在市 {len(listed)}  退市 {len(delisted)}", file=sys.stderr)

    out = {"prereg": "docs/CHAMPION_PREREG_2026-10-20.md",
           "start_year": START_YEAR, "end_year": END_YEAR}

    for name, (desc, d) in POOLS.items():
        syms = listed if name == "P_L" else listed + delisted
        r = run(desc, syms)
        out[name] = r
        print(f"\n=== {name}  {desc}  ({r['n_symbols']} 只可用)")
        for rank in ("top1", "top2", "top3"):
            rr = r["results"][rank]
            cagr = f"{rr['cagr']*100:+.2f}%" if rr["cagr"] is not None else "n/a"
            print(f"  {rank}: 净值 {rr['nav']:.2f}  总收益 {rr['total_return']*100:+.1f}%  "
                  f"年化 {cagr}  最大回撤 {rr['max_drawdown']*100:.1f}%")

    out["benchmark"] = bench()
    b = out["benchmark"]
    print(f"\n=== 沪深300 同期: 净值 {b['nav']:.2f}  年化 {b['cagr']*100:+.2f}%  "
          f"最大回撤 {b['max_drawdown']*100:.1f}%")

    for name in POOLS:
        print(f"\n--- {name} top1 逐年")
        for row in out[name]["results"]["top1"]["years"]:
            if not row.get("code"):
                print(f"  {row['year']}: {row['note']}")
                continue
            flag = "  ⚠开盘涨停" if row["limit_up_risk"] else ""
            print(f"  {row['year']}: {row['code']}  上年涨 {row['prev_gain']*100:+7.1f}%  "
                  f"本年 {row['hold']*100:+7.1f}%  净值 {row['nav']:7.2f}{flag}")

    import json
    tgt = ROOT / "docs" / "CHAMPION_BACKTEST_2026-10-20.json"
    tgt.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[OUT] {tgt.name}")


if __name__ == "__main__":
    main()
