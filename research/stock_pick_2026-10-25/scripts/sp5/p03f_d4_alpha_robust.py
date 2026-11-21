# -*- coding: utf-8 -*-
"""p03f 的稳健性核查（**看到结果之后才写的**，不参与预登记判定）。

只回答一个问题：主结果 +0.88pp/月 会不会是「持仓从当月面板消失、被其余股票平均」
这条规则造出来的？做法是三种处理并列，看符号是否稳定：

  A 基线     ：消失的持仓剔除、其余取均值（= build_decisions 原逻辑）
  B 悲观     ：消失的持仓按 **−20%** 计（退市/长停的真实损失下界假设）
  C 剔除月份 ：只要有持仓消失，该月整月剔除（不给任何替代值）

另外输出：消失事件的频率、消失个股在可用月份里的平均 fwd（若它们本来就很差，
说明 A 的确高估，B 才是下界）。

⚠️ 本脚本不改任何既有产物；只读预登记产物 + 重算。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RD = HERE.parents[1]
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import p03d_d4_strict as p03d  # noqa: E402
import run_phase_c as C  # noqa: E402
import fill_engine as fe  # noqa: E402

OUT = RD / "d4_alpha_out" / "a1801ece1ad7"
MODERN = "20160101"
BAD_FILL = -0.20


def nw(x, months, lo=None):
    if lo:
        x = [v for v, m in zip(x, months) if m >= lo]
    r = C.newey_west(list(map(float, x)), C.NW_LAG)
    return float(r["mean"]) * 100, float(r["t_nw"]), 2.8016 * float(r["se_nw"]) * 100


def actual_fwd(code: str, me: str, cache: dict) -> tuple[float, bool]:
    """用该股自身 K 线算 ME 当月的 fwd20（与 build_panel 同定义：close[pos+21]/close[pos+1]-1）。

    返回 (r, truncated)。truncated=True 表示该股在 pos+21 之前就没有数据了
    （退市/长停），此时用**最后一个可得价**计算 ⇒ 低估损失（与 ffill 同向）。
    """
    if code not in cache:
        b = None
        try:
            b = fe.load_bars(code)
        except Exception:  # noqa: BLE001
            b = None
        cache[code] = b
    b = cache[code]
    if b is None:
        return float("nan"), True
    pos = int(np.searchsorted(b.d, me, side="right")) - 1
    if pos < 0:
        return float("nan"), True
    buy, sell = pos + 1, pos + 21
    if buy >= len(b.d):
        return float("nan"), True
    if sell < len(b.d):
        return float(b.qc[sell] / b.qc[buy] - 1.0), False
    return float(b.qc[-1] / b.qc[buy] - 1.0), True


def main() -> int:
    panel = pd.read_parquet(p03d.PANEL)
    by, months, paths, ns_list, monthly, _ = p03d.build_decisions(panel, 200, "d4")
    bench = np.asarray([float(np.mean(by[d]["fwd"])) for d in months], float)

    miss_months = 0
    miss_names = Counter()
    total_slots = 0
    a_rows, b_rows, d_rows, e_rows, f_rows, g_rows = [], [], [], [], [], []
    c_keep = []
    bars_cache: dict = {}
    n_trunc = 0
    n_trunc_resume = 0
    n_trunc_terminal = 0
    for p, holds in enumerate(paths):
        a, b, dd, e, f, gg, keep = [], [], [], [], [], [], []
        for t, d in enumerate(months):
            cm = by[d]["cmap"]
            present = [c for c in holds[t] if c in cm]
            gone = [c for c in holds[t] if c not in cm]
            total_slots += len(holds[t])
            if gone:
                miss_months += 1
                for c in gone:
                    miss_names[c] += 1
            n = ns_list[p][t]
            pv = [by[d]["fwd"][cm[c]] for c in present]
            gross_present = float(np.mean(pv)) if pv else 0.0
            cost = n * 2 * p03d.SIDE_COST
            # A：原逻辑（剔除消失、其余平均——等于把权重重新分配给幸存者）
            a.append(gross_present - cost)
            # B：消失按 BAD_FILL 计（5 槽等权，下界）
            b.append(((sum(pv) + BAD_FILL * len(gone)) / p03d.K if pv or gone else 0.0) - cost)
            # D：消失按"同池等权"计（中性）
            dd.append(((sum(pv) + bench[t] * len(gone)) / p03d.K if pv or gone else 0.0) - cost)
            # E：消失按 0 收益计（= 日频 NAV 里 ffill 冻结价格的语义）
            e.append(((sum(pv)) / p03d.K if pv or gone else 0.0) - cost)
            # F：消失的持仓用**该股自身 K 线**算真实 fwd20（数据说话，不用假设）
            # G：同上，但"截断"（退市/长停到期末）一律记 −100%（硬下界）
            fv, gv = [], []
            for c in gone:
                rr, tr = actual_fwd(c, d, bars_cache)
                if rr != rr:          # NaN ⇒ 无行情，退回基准
                    rr = bench[t]
                n_trunc += int(tr)
                if tr:
                    bb = bars_cache.get(c)
                    if bb is not None and len(bb.d) and str(bb.d[-1]) > d:
                        n_trunc_resume += 1
                    else:
                        n_trunc_terminal += 1
                fv.append(rr)
                gv.append(-1.0 if tr else rr)
            f.append(((sum(pv) + sum(fv)) / p03d.K if pv or gone else 0.0) - cost)
            gg.append(((sum(pv) + sum(gv)) / p03d.K if pv or gone else 0.0) - cost)
            keep.append(not gone)
        c_keep.append(np.asarray(keep, bool))
        a_rows.append(np.asarray(a) - bench)
        b_rows.append(np.asarray(b) - bench)
        d_rows.append(np.asarray(dd) - bench)
        e_rows.append(np.asarray(e) - bench)
        f_rows.append(np.asarray(f) - bench)
        g_rows.append(np.asarray(gg) - bench)

    a_rows, b_rows = np.vstack(a_rows), np.vstack(b_rows)
    d_rows, e_rows = np.vstack(d_rows), np.vstack(e_rows)
    f_rows, g_rows = np.vstack(f_rows), np.vstack(g_rows)
    ea, eb = a_rows.mean(axis=0), b_rows.mean(axis=0)
    ed, ee = d_rows.mean(axis=0), e_rows.mean(axis=0)
    ef, eg = f_rows.mean(axis=0), g_rows.mean(axis=0)

    # C：各路径的缺失月份不同 ⇒ 只能逐路径取均值，再报跨路径分布（不做 NW）
    c_means, c_dropped = [], []
    for p in range(a_rows.shape[0]):
        keep = c_keep[p]
        c_means.append(float(np.mean(a_rows[p][keep])))
        c_dropped.append(int((~keep).sum()))

    res = {"n_path_slots": total_slots, "n_missing_events": miss_months,
           "missing_rate_per_slot": miss_months / total_slots,
           "n_months_with_missing_any_path": int((~np.vstack(c_keep).all(axis=0)).sum()),
           "top_missing_codes": miss_names.most_common(5),
           "n_truncated_events": n_trunc,
           "truncated_share": n_trunc / miss_months if miss_months else 0.0,
           "truncated_resumed_later": n_trunc_resume,
           "truncated_terminal": n_trunc_terminal,
           "A_baseline_drop_and_reweight": {}, "B_gone_gets_-20%": {},
           "D_gone_gets_bench": {}, "E_gone_gets_0%": {}, "F_gone_actual_from_bars": {}, "G_truncated_-100%": {},
           "C_drop_months": {}}
    for seg, lo in (("full", None), ("modern_2016", MODERN)):
        for key, series in (("A_baseline_drop_and_reweight", ea), ("B_gone_gets_-20%", eb),
                            ("D_gone_gets_bench", ed), ("E_gone_gets_0%", ee),
                            ("F_gone_actual_from_bars", ef), ("G_truncated_-100%", eg)):
            res[key][seg] = dict(zip(("mean_pp", "t_nw", "mde_pp"), nw(series, months, lo)))
        sel = (np.ones(len(months), bool) if lo is None
               else np.asarray([m >= lo for m in months], bool))
        vals = []
        for p in range(a_rows.shape[0]):
            keep = c_keep[p] & sel
            vals.append(float(np.mean(a_rows[p][keep])))
        res["C_drop_months"][seg] = {
            "mean_pp": float(np.median(vals)) * 100,
            "p05_pp": float(np.percentile(vals, 5)) * 100,
            "p95_pp": float(np.percentile(vals, 95)) * 100,
            "share_positive": float(np.mean(np.asarray(vals) > 0)),
            "median_dropped_months": float(np.median(c_dropped))
            if seg == "full" else float(np.median([
                int((~c_keep[p] & sel).sum()) for p in range(a_rows.shape[0])])),
            "note": "逐路径中位数，无 NW t（各路径可用月份不同，无法对齐）"}

    # F 的逐年均值（看是否由个别年份驱动）
    by_year = {}
    for y in sorted({m[:4] for m in months}):
        idx = [i for i, m in enumerate(months) if m[:4] == y]
        by_year[y] = {"n": len(idx), "mean_pp": float(np.mean(ef[idx])) * 100}
    res["F_by_year"] = by_year

    (OUT / "robustness.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8", newline="\n")
    print(f"[缺失] 槽位 {total_slots}｜消失事件 {miss_months}｜"
          f"占比 {miss_months/total_slots*100:.4f}%｜涉及月份(任一 pat) "
          f"{res['n_months_with_missing_any_path']}/{len(months)}")
    print(f"[缺失个股] {miss_names.most_common(5)}")
    print(f"[截断拆分] 后续恢复交易 {n_trunc_resume}｜终止(末日=当月) {n_trunc_terminal}")
    print("[F 逐年] " + "  ".join(f"{y}:{v['mean_pp']:+.2f}" for y, v in res["F_by_year"].items()))
    for k in ("A_baseline_drop_and_reweight", "B_gone_gets_-20%", "D_gone_gets_bench",
              "E_gone_gets_0%", "F_gone_actual_from_bars", "G_truncated_-100%", "C_drop_months"):
        for seg in ("full", "modern_2016"):
            v = res[k][seg]
            if "t_nw" in v:
                print(f"  {k:<18} {seg:<11} {v['mean_pp']:+.4f}pp  t={v['t_nw']:+.2f}  MDE={v['mde_pp']:.4f}")
            else:
                print(f"  {k:<18} {seg:<11} 中位 {v['mean_pp']:+.4f}pp "
                      f"[{v['p05_pp']:+.3f}, {v['p95_pp']:+.3f}] 正占比 {v['share_positive']*100:.1f}% "
                      f"剔除月份中位 {v['median_dropped_months']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
