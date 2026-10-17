# -*- coding: utf-8 -*-
"""
B15 —— 8 年区间（20170101-20241231）完整 30 组结果的汇总与诊断。

只读 `b8_out/step75_research_summary.json`，不重新下载、不重跑回测、不调参（R2/R11）。
所有数字由脚本从落盘 JSON 现算，可复现。

用法：
    D:/DeepQuant power by txm/venv/Scripts/python.exe b15_summarize.py [summary_json_path]
"""
from __future__ import annotations

import hashlib
import json
import statistics as st
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "b8_out" / "step75_research_summary.json"


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt(x: float, nd: int = 4) -> str:
    return f"{x * 100:+.{nd - 2}f}%"


def main() -> int:
    path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT
    d = load(path)
    res = d["results"]

    print(f"# B15 汇总源：{path}")
    print(f"# 文件 sha256：{sha256_of(path)}")
    print(f"# run_at={d['run_at']}  source={d['data_source']}")
    print(f"# range={d['request_range']}  design={d['design']}")
    print(f"# stocks={len(d['stocks'])}  skipped={len(d['skipped'])}  results={len(res)}")
    for s in d["skipped"]:
        print(f"#   skipped: {s['code']} {s['name']} reason={s['reason']} codes={s['error_codes']}")
    print()

    # ---- 逐组派生：成交密度 ----
    print("## 逐组明细（含成交密度）")
    hdr = (
        f"{'symbol':<11}{'name':<7}{'strategy':<15}{'fee':<26}"
        f"{'nWin':>5}{'sel':>4}{'oos':>4}{'trd':>5}{'trd/win':>9}"
        f"{'pos%':>7}{'medRet':>10}{'medMDD':>9}{'fees':>10}"
    )
    print(hdr)
    rows = []
    for r in sorted(res, key=lambda r: (r["symbol"], r["strategy"], r["fee_source"])):
        n_sel = r["n_sel_ok"]
        dens = r["total_trades"] / n_sel if n_sel else float("nan")
        rows.append(
            {
                "symbol": r["symbol"],
                "name": r["name"],
                "strategy": r["strategy"],
                "fee": r["fee_source"],
                "n_win": r["n_windows"],
                "n_sel": n_sel,
                "n_oos": r["n_oos_ok"],
                "trades": r["total_trades"],
                "density": dens,
                "pos": r["positive_ratio"],
                "med": r["median_ret"],
                "mdd": r["median_mdd"],
                "fees": r["total_fees"],
                "trans": r.get("transition_rate"),
            }
        )
        print(
            f"{r['symbol']:<11}{r['name']:<7}{r['strategy']:<15}{r['fee_source']:<26}"
            f"{r['n_windows']:>5}{n_sel:>4}{r['n_oos_ok']:>4}{r['total_trades']:>5}{dens:>9.2f}"
            f"{r['positive_ratio'] * 100:>7.1f}{r['median_ret']:>10.6f}"
            f"{r['median_mdd']:>9.4f}{r['total_fees']:>10.2f}"
        )
    print()

    # ---- 按 策略 × 费率 汇总（跨 5 只标的）----
    print("## 按 策略 × 费率档 汇总（n=5 只标的）")
    print(f"{'strategy':<15}{'fee':<26}{'>0':>4}{'=0':>4}{'<0':>4}{'中位数的中位数':>16}{'均值':>12}{'min':>11}{'max':>11}")
    for strat in ("ma_cross", "mean_reversion"):
        for fee in ("simulation_placeholder", "broker_reference_common_2024", "research_scenario_high_cost"):
            g = [r for r in rows if r["strategy"] == strat and r["fee"] == fee]
            meds = [r["med"] for r in g]
            pos = sum(1 for m in meds if m > 0)
            zer = sum(1 for m in meds if m == 0)
            neg = sum(1 for m in meds if m < 0)
            print(
                f"{strat:<15}{fee:<26}{pos:>4}{zer:>4}{neg:>4}"
                f"{st.median(meds):>16.6f}{st.fmean(meds):>12.6f}{min(meds):>11.6f}{max(meds):>11.6f}"
            )
    print()

    # ---- 按策略合并（15 组）----
    print("## 按策略合并（3 费率 × 5 只 = 15 组）")
    for strat in ("ma_cross", "mean_reversion"):
        g = [r for r in rows if r["strategy"] == strat]
        meds = [r["med"] for r in g]
        print(
            f"{strat:<15} n={len(g):>3}  中位数的中位数={st.median(meds):+.6f}  "
            f">0:{sum(1 for m in meds if m > 0)}  =0:{sum(1 for m in meds if m == 0)}  "
            f"<0:{sum(1 for m in meds if m < 0)}  成交密度中位={st.median([r['density'] for r in g]):.2f} 笔/窗口"
        )
    print()

    # ---- 全 30 组符号分布 ----
    meds30 = [r["med"] for r in rows]
    print("## 全 30 组中位收益符号分布")
    print(
        f"  >0: {sum(1 for m in meds30 if m > 0)}   =0: {sum(1 for m in meds30 if m == 0)}   "
        f"<0: {sum(1 for m in meds30 if m < 0)}   合并中位数={st.median(meds30):+.6f}"
    )
    print()

    # ---- 费率单调性（诊断用，不择优 R11）----
    print("## 费率档单调性检查（只披露，禁止择优 R11）")
    for strat in ("ma_cross", "mean_reversion"):
        for sym, nm in sorted({(r["symbol"], r["name"]) for r in rows}):
            seq = [
                next(r for r in rows if r["symbol"] == sym and r["strategy"] == strat and r["fee"] == f)["med"]
                for f in ("simulation_placeholder", "broker_reference_common_2024", "research_scenario_high_cost")
            ]
            mono = "单调↓" if seq[0] >= seq[1] >= seq[2] else "非单调"
            print(f"  {strat:<15}{nm:<7} sim={seq[0]:+.6f}  ref={seq[1]:+.6f}  high={seq[2]:+.6f}  {mono}")
    print()

    # ---- 参数漂移 ----
    print("## 参数稳定性（transition_rate = 相邻窗口换参数的比例）")
    for strat in ("ma_cross", "mean_reversion"):
        tr = [r["trans"] for r in rows if r["strategy"] == strat and r["trans"] is not None]
        print(f"  {strat:<15} 中位={st.median(tr):.4f}  min={min(tr):.4f}  max={max(tr):.4f}")
    print()

    # ---- 参与 vs 选中 ----
    print("## 选中成功 ≠ 实际成交（关键诊断）")
    print(f"{'symbol':<11}{'strategy':<15}{'fee':<26}{'n_sel':>6}{'trades':>8}{'零成交窗口下界':>14}")
    for r in rows:
        zero_lb = max(0, r["n_sel"] - r["trades"])
        print(
            f"{r['symbol']:<11}{r['strategy']:<15}{r['fee']:<26}{r['n_sel']:>6}{r['trades']:>8}{zero_lb:>14}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
