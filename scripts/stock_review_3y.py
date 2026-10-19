"""近三年个股体检：总回报 vs 纯股价涨幅，双口径并列。

为什么必须分两个口径
--------------------
「这只票近三年涨了多少」有两句不同的话，混在一起就会得出错误印象：

1. **总回报**（乘性前复权 `qfq_mult`）：假设分红在除权日按收盘价再买入。
   这是**持有者的真实财富变化**，也是与沪深300 价格指数对比时唯一自洽的口径之一
   （注意：项目里的 hs300 是**价格指数不含股息**，见 `docs/` 基准说明）。
2. **纯股价涨幅**（不复权 `raw`）：就是行情软件 K 线上那根线的变化。
   高分红股除权日会向下跳空，所以这个数字**系统性低于**总回报。

两者之差 ≈ 分红/送转的贡献，对煤炭、建筑这类高股息股可能是几十个百分点，必须摊开。

口径（先定死，后计算）
----------------------
- 窗口：近三年 = 起点 `2023-10-16`（若非交易日取其后首个交易日）→ 终点 `2026-10-16`（数据末端）
- 收益基于**收盘价**，不含盘中
- 年化按自然日：`(1+total)^(365/days) - 1`
- 最大回撤：收盘价口径的 peak-to-trough（**不是**盘中，会低估回撤）
- 波动率：日收益样本标准差（ddof=1）× √252
- 分年度：自然年切分，起始年从窗口起点算起，末年为 YTD

不做
----
- 不做预测、不给买卖建议
- 不补数据：缺一天就少一天，如实记交易日数
- 不计交易费用、税费、滑点

运行
----
    python scripts/stock_review_3y.py --codes 601699.SH,002165.SZ,601668.SH
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QFQ_DIR = ROOT / "data" / "qfq_mult" / "listed"
BENCH = ROOT / "data" / "benchmark" / "hs300_daily.csv"
NAMES = {
    "601699.SH": "潞安环能",
    "002165.SZ": "红宝丽",
    "601668.SH": "中国建筑",
}
START_DEFAULT = "2023-10-16"
END = "2026-10-16"
PAGE = 640
TX_RAW = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={sym},day,{beg},{end},{n}"


def tx_symbol(code: str) -> str:
    num, mkt = code.split(".")
    return f"{mkt.lower()}{num}"


def _get(url: str, tries: int = 4) -> dict:
    last: Exception | None = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"{type(last).__name__}: {last}")


def fetch_raw(code: str, beg: str, end: str) -> list[tuple[str, float]]:
    """不复权收盘序列（回翻分页）。"""
    sym = tx_symbol(code)
    rows: dict[str, float] = {}
    cursor = end
    for _ in range(12):
        payload = _get(TX_RAW.format(sym=sym, beg=beg, end=cursor, n=PAGE))
        data = payload.get("data", {}).get(sym)
        batch = data.get("day") if isinstance(data, dict) else None
        if not batch:
            break
        for r in batch:
            rows[r[0]] = float(r[2])
        first = batch[0][0]
        if first <= beg:
            break
        cursor = (datetime.strptime(first, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return [(d, rows[d]) for d in sorted(rows)]


def read_qfq(path: Path) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append((r["date"], float(r["close"])))
    return out


def read_bench() -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    with BENCH.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append((r["date"], float(r["close"])))
    return out


def slice_window(series: list[tuple[str, float]], start: str, end: str):
    win = [(d, c) for d, c in series if start <= d <= end]
    return win


def metrics(win: list[tuple[str, float]]) -> dict:
    """给定窗口的收盘序列，算总收益/年化/回撤/波动。"""
    n = len(win)
    if n < 2:
        return {"n_days": n, "error": "窗口内交易日不足 2 天"}
    first_d, first_c = win[0]
    last_d, last_c = win[-1]
    total = last_c / first_c - 1.0
    days = (datetime.strptime(last_d, "%Y-%m-%d") - datetime.strptime(first_d, "%Y-%m-%d")).days
    years = days / 365.0
    ann = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else float("nan")

    # 最大回撤（收盘价口径）
    peak = win[0][1]
    mdd = 0.0
    mdd_peak_d = mdd_trough_d = first_d
    cur_peak_d = first_d
    for d, c in win:
        if c > peak:
            peak, cur_peak_d = c, d
        dd = c / peak - 1.0
        if dd < mdd:
            mdd, mdd_peak_d, mdd_trough_d = dd, cur_peak_d, d

    # 日收益波动
    rets = [win[i][1] / win[i - 1][1] - 1.0 for i in range(1, n)]
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        vol = math.sqrt(var) * math.sqrt(252)
    else:
        vol = float("nan")

    return {
        "n_days": n,
        "start_date": first_d, "end_date": last_d,
        "start_close": round(first_c, 4), "end_close": round(last_c, 4),
        "total_return": round(total, 6),
        "annualized": round(ann, 6),
        "max_drawdown": round(mdd, 6),
        "mdd_peak_date": mdd_peak_d, "mdd_trough_date": mdd_trough_d,
        "ann_volatility": round(vol, 6),
        "calmar": round(ann / abs(mdd), 4) if mdd < 0 else None,
        "calendar_years": yearly(win),
    }


def yearly(win: list[tuple[str, float]]) -> dict:
    """自然年收益；首年从窗口起点算，末年为 YTD。"""
    if not win:
        return {}
    out: dict[str, float] = {}
    years = sorted({d[:4] for d, _ in win})
    prev_close = None
    for y in years:
        seg = [(d, c) for d, c in win if d[:4] == y]
        if not seg:
            continue
        base = prev_close if prev_close is not None else seg[0][1]
        out[y] = round(seg[-1][1] / base - 1.0, 6)
        prev_close = seg[-1][1]
    return out


def one(code: str, start: str) -> dict:
    name = NAMES.get(code, "")
    rec: dict = {"code": code, "name": name}

    qfq_path = QFQ_DIR / f"{code}.qfq.csv"
    if not qfq_path.exists():
        return {**rec, "error": f"缺乘性前复权文件 {qfq_path.name}"}
    qfq = read_qfq(qfq_path)
    win_q = slice_window(qfq, start, END)
    if not win_q:
        return {**rec, "error": "窗口内无数据"}
    start_eff = win_q[0][0]
    rec["total_return_basis"] = metrics(win_q)  # 含分红再投资

    try:
        raw = fetch_raw(code, start_eff, END)
        win_r = slice_window(raw, start_eff, END)
        rec["price_only_basis"] = metrics(win_r) if len(win_r) >= 2 else {"n_days": len(win_r), "error": "不复权窗口不足"}
    except Exception as exc:  # noqa: BLE001
        rec["price_only_basis"] = {"error": f"不复权取数失败：{type(exc).__name__}: {exc}"[:160]}

    tb = rec["total_return_basis"]
    pb = rec.get("price_only_basis", {})
    if "total_return" in tb and "total_return" in pb:
        rec["dividend_contribution_pp"] = round((tb["total_return"] - pb["total_return"]) * 100, 2)

    # 口径一致性哨兵：理论上 qfq_start <= raw_start（前复权只会把历史价下调）。
    # 若反了，说明腾讯「不复权」序列与 hfq 的股本基准不一致，纯股价口径仅可作参考。
    if "start_close" in tb and "start_close" in pb:
        ratio = pb["start_close"] / tb["start_close"]
        rec["price_basis_ratio_start"] = round(ratio, 4)
        if ratio < 1.0:
            rec["price_basis_warning"] = (
                f"不复权起点({pb['start_close']}) < 乘性前复权起点({tb['start_close']})，"
                f"方向上不可能（前复权只会下调历史价）。腾讯不复权序列与 hfq 基准不一致，"
                f"纯股价口径与分红贡献对这只票仅作参考，不可用于结论。"
            )
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="601699.SH,002165.SZ,601668.SH")
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--out", default="docs/STOCK_REVIEW_3Y_2026-10-20.json")
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    with ThreadPoolExecutor(max_workers=len(codes)) as pool:
        futs = {pool.submit(one, c, args.start): c for c in codes}
        results = [f.result() for f in futs]

    bench = read_bench()
    win_b = slice_window(bench, args.start, END)
    bench_m = metrics(win_b)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": {"start_requested": args.start, "end": END, "start_effective": win_b[0][0] if win_b else None},
        "basis_note": (
            "total_return_basis = 乘性前复权（分红按除权日收盘价再投资），是持有者财富变化；"
            "price_only_basis = 不复权，即 K 线涨跌幅，高分红股会被除权跳空系统性压低。"
            "基准 hs300 为价格指数，不含股息。"
        ),
        "benchmark_hs300": bench_m,
        "stocks": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"窗口 {args.start} → {END}（基准首个交易日 {win_b[0][0] if win_b else '?'}）\n")
    print(f"{'标的':<12}{'总回报':>9}{'年化':>9}{'最大回撤':>10}{'波动率':>9}{'纯股价':>9}{'分红贡献':>10}")
    print("-" * 70)
    b = bench_m
    print(f"{'沪深300':<12}{b['total_return']:>8.1%}{b['annualized']:>9.2%}{b['max_drawdown']:>10.1%}"
          f"{b['ann_volatility']:>9.1%}{'—':>9}{'—':>10}")
    for r in results:
        tb, pb = r["total_return_basis"], r.get("price_only_basis", {})
        label = f"{r['name'] or r['code']}"
        price = f"{pb['total_return']:.1%}" if "total_return" in pb else "—"
        div = f"{r.get('dividend_contribution_pp')}pp" if "dividend_contribution_pp" in r else "—"
        print(f"{label:<12}{tb['total_return']:>8.1%}{tb['annualized']:>9.2%}{tb['max_drawdown']:>10.1%}"
              f"{tb['ann_volatility']:>9.1%}{price:>9}{div:>10}")
    print(f"\n-> {out.name}")


if __name__ == "__main__":
    main()
