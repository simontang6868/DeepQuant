#!/usr/bin/env python
"""阶段 J：冲击成本与「容量—冲击」权衡（exQ5）。

预登记 `docs/PHASE_J_IMPACT_PREREG_2026-10-21.md`，冻结 `f76de8a277f6…`。

## 本阶段能做什么、不能做什么

**没有盘中数据**（无逐笔、无买卖盘、无委托簿）→ 不可能**测量**冲击成本。
所以本阶段只做两件事：

1. 模型 A（Amihud 2002，线性）：**无自由参数**，成本 = ILLIQ × 交易额；
2. 模型 B（平方根）：η 是自由参数 → **不猜 η**，反解"打平门槛 η*"。

主统计量不是"冲击成本"，而是**全部执行摩擦之后**的净值：

    net_t(W) = f_inv,t × alpha_V1,t  −  comm_t  −  impact_t(W)

三种摩擦（现金残留 / 佣金 / 冲击）分开归因，回答"到底是谁杀死了它"。

## 数据层硬事实（差 100 倍会让结论错一个量级）

`exec_all` 的 `volume` 单位是**手（100 股）** → `DV = volume × 100 × close`。
已用茅台/平安/宁德三家交叉验证（作为「手」时换手率 0.21%/0.48%/0.81%，合理；
作为「股」时是 0.002%/0.005%/0.008%，荒谬）。

用法：
    python scripts/run_phase_j_impact.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import EXEC_DIR, NW_LAG, load_close, stream_bars  # noqa: E402
from run_phase_i_exq5 import (  # noqa: E402
    LOT, SEGMENTS, WIN, build_records, capacity_of, filter_v1,
)

# ==================== 预登记常量 ====================
PREREG_SHA256 = "f76de8a277f6574795cb8b78b8a91745a8f1dd5b2152a62ed37af567ebca5f62"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasej_out", PREREG_SHA12)

SHARES_PER_LOT = 100        # ⚠️ exec_all 的 volume 单位是「手」
CAPITAL_TIERS = (1.5e6, 3.0e6, 5.0e6, 1.0e7, 3.0e7)
COMM_RATE = 0.003           # 单边 0.3%（判据用）
T_CRIT = 1.96
PHASEI_MONTHLY = "phasei_out/8f5c35121fdb/monthly.csv"

# 平方根模型的文献参考区间（只用于解读，不用于判定）
ETA_REF_LO, ETA_REF_HI = 0.1, 1.0


def verify() -> dict:
    p = "docs/PHASE_J_IMPACT_PREREG_2026-10-21.md"
    if not os.path.exists(p):
        raise SystemExit(f"预登记缺失：{p}")
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}\n"
                         f"  预登记被改过 → 本实验无效")
    return {"prereg_sha256": h}


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ==================== 美元成交额面板 ====================
def load_dollar_volume() -> pd.DataFrame:
    """日频美元成交额矩阵。⚠️ `volume` 单位是**手**，必须 ×100。

    float32 存储（5311×5444 的 float64 是 231MB，三张表会顶到 700MB）。
    """
    import glob
    paths = sorted(glob.glob(os.path.join(EXEC_DIR, "*.exec.csv")))
    cols: dict[str, pd.Series] = {}
    t0 = time.time()
    for i, p in enumerate(paths, 1):
        code = os.path.basename(p).replace(".exec.csv", "")
        df = pd.read_csv(p, usecols=["date", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        dv = (df["volume"].astype("float32") * SHARES_PER_LOT * df["close"].astype("float32"))
        s = pd.Series(dv.to_numpy(), index=df["date"]).groupby(level=0).last()
        s = s[s > 0]
        if not s.empty:
            cols[code] = s
        if i % 1000 == 0:
            print(f"      读入成交额 {i}/{len(paths)} … {time.time()-t0:.0f}s")
    out = pd.DataFrame(cols).sort_index().astype("float32")
    print(f"[J] 美元成交额载入 {out.shape[1]} 只 × {len(out)} 天  {time.time()-t0:.0f}s")
    return out


def liquidity_at(ret: np.ndarray, dv: np.ndarray, win: int = WIN) -> tuple:
    """给定窗口 `(win, n)` 的日收益与日成交额，返回 (ILLIQ, ADV, 日波动率)。

    `ILLIQ = mean(|r| / DV)`，单位是「收益 / 元」。
    `|r|` 用 **ADJ** 收益（避免除权日跳变），`DV` 用 **EXEC** 真实成交额。
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.abs(ret) / dv
    ratio = np.where(np.isfinite(ratio), ratio, np.nan)
    illiq = np.nanmean(ratio, axis=0) if ratio.shape[0] else np.full(ret.shape[1], np.nan)
    adv = np.nanmean(dv, axis=0)
    sd = np.nanstd(ret, axis=0, ddof=1)
    return illiq, adv, sd


# ==================== 归因与判据 ====================
def impact_amihud(illiq: np.ndarray, idx: np.ndarray, p: float, k: int) -> float:
    """模型 A：组合冲击 = Σ_i w_i × (ILLIQ_i × P)，w_i = 1/k。

    冲击以**收益**计（ILLIQ × P 是价格移动比例），组合层面按持仓权重加权。
    返回 pp。
    """
    if k <= 0 or idx.size == 0:
        return 0.0
    return float(np.nansum(illiq[idx] * p)) / k * 100.0


def impact_sqrt(sigma: np.ndarray, adv: np.ndarray, idx: np.ndarray,
                p: float, k: int, eta: float = 1.0) -> float:
    """模型 B：η × σ × sqrt(P / ADV)，同样按持仓权重加权，返回 pp。"""
    if k <= 0 or idx.size == 0:
        return 0.0
    with np.errstate(invalid="ignore", divide="ignore"):
        part = np.where(adv[idx] > 0, p / adv[idx], np.nan)
        imp = eta * sigma[idx] * np.sqrt(np.where(np.isfinite(part), part, 0.0))
    return float(np.nansum(imp)) / k * 100.0


def judge(all_t: list[float]) -> dict:
    """三态判据（预登记 §5）：在**全部**资金档位上评估，不许挑最好的。"""
    ok = [abs(t) >= T_CRIT for t in all_t]
    if all(ok):
        v = "NET_AFTER_EXECUTION_SIGNIFICANT"
    elif any(ok):
        v = "NET_AFTER_EXECUTION_SCALE_DEPENDENT"
    else:
        v = "NET_AFTER_EXECUTION_ABSORBED"
    return {"t_by_w": all_t, "pass_by_w": ok, "verdict": v}


def breakeven_eta(base: list[float], unit: list[float]) -> float:
    """反解 η*：使 mean(net) = 0 的 η。`unit` 是 η=1 时的冲击序列。

    ⚠️ 猜 η 是调参；反解"打平需要多大的 η"不是 —— 它把不确定性显式交给读者。
    """
    mb = statistics.mean(base)
    mu = statistics.mean(unit)
    if mu <= 0 or not np.isfinite(mu):
        return float("nan")
    return mb / mu


# ==================== 主流程 ====================
def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print(f"阶段 J：冲击成本与容量—冲击权衡   预登记 {PREREG_SHA12}")
    print("=" * 78)
    vi = verify()
    print(f"  预登记哈希校验 ✓")

    # ---- ① 重建阶段 I 的 V1（复用其函数，并与产物对账）
    print()
    print("① 重建 V1 持仓（与阶段 I 同源，并对账 monthly.csv）")
    recs, meta = build_records()
    print(f"   月份 {len(recs)}   分组独立校验不一致月份 {meta['split_mismatch_months']}")

    need: dict[str, set[str]] = defaultdict(set)
    for i, r in enumerate(recs):
        for c in r["keep_codes"]:
            need[c].add(r["entry"])
            if i + 1 < len(recs):
                need[c].add(recs[i + 1]["entry"])
    bars, ex_al, span = stream_bars(need)

    for r in recs:
        pos_map = {c: i for i, c in enumerate(r["codes"])}
        f1 = filter_v1(r, bars, ex_al, span, pos_map)
        r["keep_v1"] = f1["keep_codes"]
        r["pos_map"] = pos_map
        if f1["keep_codes"]:
            r["alpha_v1"] = float(np.mean([r["fwd"][pos_map[c]]
                                           for c in f1["keep_codes"]])) - r["r_bench"]
        else:
            r["alpha_v1"] = float("nan")
        # 一手金额（容量与阶段 I 同规则）
        lots = []
        for c in r["keep_v1"]:
            rb = bars.get(c, {}).get(r["entry"])
            if rb is not None and rb["bar"][3] > 0:
                lots.append(rb["bar"][3] * LOT)
        r["lots"] = lots

    # 对账：必须与阶段 I 的 monthly.csv 逐月一致
    if os.path.exists(PHASEI_MONTHLY):
        ref = {}
        with open(PHASEI_MONTHLY, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["alpha_v1_pp"]:
                    ref[row["signal_date"]] = float(row["alpha_v1_pp"])
        diffs = [abs(r["alpha_v1"] * 100 - ref[r["signal_date"]])
                 for r in recs if r["signal_date"] in ref and np.isfinite(r["alpha_v1"])]
        mx = max(diffs) if diffs else float("nan")
        print(f"   与阶段 I monthly.csv 对账：{len(diffs)} 月，最大差 {mx:.2e} "
              f"{'✓' if mx < 1e-6 else '✗ 不一致'}")
        if not (mx < 1e-6):
            raise SystemExit("V1 重建与阶段 I 产物不一致 → 后续净值不可信")
    else:
        print(f"   ⚠ 未找到 {PHASEI_MONTHLY}，跳过对账")

    # ---- ② 流动性面板
    print()
    print("② 流动性（Amihud ILLIQ / ADV / 日波动）")
    close = load_close()
    dv = load_dollar_volume()
    dv = dv.reindex(index=close.index, columns=close.columns)
    cal = close.index
    # ⚠️ 必须按阶段 I 的**信号点**定位，不能另算 positions —— 两者若不同源，
    #    窗口起止会差一天，ILLIQ 就对不上 V1 的持仓。
    pos_of = {str(d.date()): i for i, d in enumerate(cal)}
    arr_r = close.pct_change(fill_method=None).to_numpy(dtype="float32", copy=True)
    arr_dv = dv.to_numpy()
    cols = list(close.columns)
    col_of = {c: i for i, c in enumerate(cols)}
    del dv, close

    # 每个信号点算一次 21 日窗口统计量（只算 237 次，避免全表 rolling 的大临时矩阵）
    illiq_m, adv_m, sd_m = {}, {}, {}
    for r in recs:
        pos = pos_of.get(r["signal_date"])
        if pos is None or pos - WIN + 1 < 0:
            continue
        il, ad, sd = liquidity_at(arr_r[pos - WIN + 1: pos + 1],
                                  arr_dv[pos - WIN + 1: pos + 1])
        illiq_m[r["signal_date"]] = il
        adv_m[r["signal_date"]] = ad
        sd_m[r["signal_date"]] = sd
    print(f"   已算 {len(illiq_m)} 个信号点的流动性（与 V1 信号点同源）")

    # ---- ③ 逐月摩擦
    print()
    print("③ 逐月摩擦（现金残留 / 佣金 / 冲击）")
    prev = None
    for r in recs:
        cur = set(r["keep_v1"])
        if prev is None:
            r["to"] = float("nan")
            r["traded"] = []
        else:
            r["to"] = 0.5 * (len(cur - prev) + len(prev - cur)) / max(len(cur), 1)
            r["traded"] = sorted((cur - prev) | (prev - cur))
        prev = cur

    # 参与率与流动性分布（描述性）
    parts, advs, illiqs = [], [], []
    for r in recs:
        il, ad, sd = illiq_m.get(r["signal_date"]), adv_m.get(r["signal_date"]), \
            sd_m.get(r["signal_date"])
        if il is None:
            continue
        k = max(len(r["keep_v1"]), 1)
        p = 1.0e7 / k                      # 用 1,000 万作为参考档看分布
        idx = np.array([col_of[c] for c in r["keep_v1"] if c in col_of], dtype=int)
        if idx.size == 0:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            pr = np.where(ad[idx] > 0, p / ad[idx], np.nan)
        parts.extend(pr[np.isfinite(pr)].tolist())
        advs.extend(ad[idx][np.isfinite(ad[idx])].tolist())
        illiqs.extend(il[idx][np.isfinite(il[idx])].tolist())

    def qv(v, p_):
        s = sorted(v)
        return s[min(len(s) - 1, int(round(p_ * (len(s) - 1))))]

    dist = {"participation_median": qv(parts, .5), "participation_p90": qv(parts, .9),
            "participation_p99": qv(parts, .99), "participation_max": max(parts),
            "adv_median": qv(advs, .5), "adv_p10": qv(advs, .1),
            "illiq_median": qv(illiqs, .5), "illiq_p90": qv(illiqs, .9)}
    print(f"   参与率 P/ADV（W=1000万）：中位 {dist['participation_median']*100:.4f}%  "
          f"P90 {dist['participation_p90']*100:.4f}%  P99 {dist['participation_p99']*100:.4f}%  "
          f"最大 {dist['participation_max']*100:.3f}%")
    print(f"   ADV 中位 {dist['adv_median']/1e4:,.0f} 万   P10 {dist['adv_p10']/1e4:,.0f} 万")

    # ---- ④ 各资金档的净值与归因
    print()
    print("④ 净值（net = f_inv × alpha − 佣金@0.3% − 冲击）")
    by_w, verdict_input = {}, []
    for budget in CAPITAL_TIERS:
        net_a, net_b1, base_s, comm_s, imp_a, imp_b1, finv_s = [], [], [], [], [], [], []
        cash_s = []
        for r in recs:
            if not np.isfinite(r["alpha_v1"]) or not r["lots"]:
                continue
            il, ad, sd = (illiq_m.get(r["signal_date"]), adv_m.get(r["signal_date"]),
                          sd_m.get(r["signal_date"]))
            if il is None:
                continue
            cs = capacity_of(r["lots"], budget)
            k = cs["k"]
            if k <= 0:
                continue
            finv = 1.0 - cs["uninvested"]
            p = budget / k
            to = r["to"] if np.isfinite(r["to"]) else 0.0
            comm = 2.0 * to * COMM_RATE * 100.0
            idx = np.array([col_of[c] for c in r["traded"] if c in col_of], dtype=int)
            ia = impact_amihud(il, idx, p, k)
            ib1 = impact_sqrt(sd, ad, idx, p, k, 1.0)
            base = finv * r["alpha_v1"] * 100.0
            net_a.append(base - comm - ia)
            net_b1.append(base - comm - ib1)
            base_s.append(base)
            comm_s.append(comm)
            imp_a.append(ia)
            imp_b1.append(ib1)
            finv_s.append(finv)
            cash_s.append((1.0 - finv) * r["alpha_v1"] * 100.0)

        nw_a = C.newey_west(net_a, NW_LAG)
        nw_b = C.newey_west(net_b1, NW_LAG)
        # ⚠️ 显示口径：`newey_west` 的 `mean_pp = mean × 100`，**假设输入是小数**。
        #    但 net 序列已经是 pp（alpha×100、佣金已是 pp、冲击已是 pp）。
        #    第一版直接取 `mean_pp`，净值被放大 100 倍（4.2423 实为 0.0424）。
        #    t 是尺度无关的，所以判定没变，但报告数字必须修 → 均值直接算。
        mean_a = statistics.mean(net_a)
        mean_b = statistics.mean(net_b1)
        # 同时报 iid 标准误下的 t：阶段 I 的 C 条件用的是 `cost_of`（iid se），
        # 本阶段主检验用 NW se。两者都报，避免"换了个 se 就让结果变好"的嫌疑。
        def _t_iid(x: list[float]) -> float:
            sd = statistics.stdev(x) if len(x) > 1 else float("nan")
            se = sd / math.sqrt(len(x)) if len(x) else float("nan")
            return statistics.mean(x) / se if se else float("nan")
        t_iid_a = _t_iid(net_a)
        eta_star = breakeven_eta([b - c for b, c in zip(base_s, comm_s)], imp_b1)
        # 打平 t=1.96 所需的 η（更宽松的门槛）
        mb = statistics.mean(base_s) - statistics.mean(comm_s)
        se = statistics.stdev([b - c for b, c in zip(base_s, comm_s)]) / math.sqrt(len(base_s)) \
            if len(base_s) > 1 else float("nan")
        mu = statistics.mean(imp_b1)
        eta_t = (mb - T_CRIT * se * 100) / mu if (mu > 0 and se == se) else float("nan")

        row = {"budget": budget, "n": len(net_a),
               "alpha_v1_pp": statistics.mean([r["alpha_v1"] * 100
                                               for r in recs if np.isfinite(r["alpha_v1"])]),
               "f_inv_median": statistics.median(finv_s),
               "cash_drag_pp": statistics.mean(cash_s) if cash_s else 0.0,
               "comm_pp": statistics.mean(comm_s),
               "impact_A_pp": statistics.mean(imp_a),
               "impact_B_eta1_pp": statistics.mean(imp_b1),
               "net_A_pp": mean_a, "t_net_A": nw_a["t_nw"], "t_net_A_iid": t_iid_a,
               "net_B_eta1_pp": mean_b, "t_net_B_eta1": nw_b["t_nw"],
               "se_nw_A_pp": nw_a["se_nw_pp"],
               "breakeven_eta_mean0": eta_star, "breakeven_eta_t196": eta_t}
        by_w[f"{budget/1e4:.0f}万"] = row
        verdict_input.append(nw_a["t_nw"])
        print(f"   W={budget/1e4:>7.0f}万  f_inv {row['f_inv_median']:.4f}  "
              f"佣金 {row['comm_pp']:.4f}  冲击A {row['impact_A_pp']:.6f}  "
              f"冲击B(η=1) {row['impact_B_eta1_pp']:.4f}")
        print(f"       → 净@A {row['net_A_pp']:+.4f}pp  t(NW)={row['t_net_A']:+.3f}  "
              f"t(iid)={row['t_net_A_iid']:+.3f}   "
              f"净@B(η=1) {row['net_B_eta1_pp']:+.4f}pp t={row['t_net_B_eta1']:+.3f}   "
              f"打平 η*={eta_star:.3f}")

    jd = judge(verdict_input)
    print()
    print("⑤ 判定（预登记 §5，在全部资金档位上评估）")
    print(f"   各档 t（模型 A）："
          + "  ".join(f"{k}:{v:+.2f}" for k, v in
                      zip(by_w.keys(), jd["t_by_w"])))
    print(f"   >>> {jd['verdict']}")

    # ---- ⑥ 归因（谁杀死了它）
    print()
    print("⑥ 归因：从 +0.1634pp 出发，每一项折掉多少")
    best = max(by_w.values(), key=lambda x: x["net_A_pp"])
    a0 = best["alpha_v1_pp"]
    print(f"   起点 alpha_V1              {a0:+.4f}pp")
    print(f"   现金残留折损（最好档 {best['budget']/1e4:.0f}万）  "
          f"{-best['cash_drag_pp']:+.4f}pp   f_inv={best['f_inv_median']:.4f}")
    print(f"   佣金 @0.3%                 {-best['comm_pp']:+.4f}pp")
    print(f"   冲击（Amihud，无参数）     {-best['impact_A_pp']:+.6f}pp")
    print(f"   冲击（平方根 η=1）          {-best['impact_B_eta1_pp']:+.4f}pp")
    print(f"   ─────────────────────────────────")
    print(f"   净（模型 A）                {best['net_A_pp']:+.4f}pp  t={best['t_net_A']:+.3f}")

    # ---- ⑦ 诊断
    print()
    print("⑦ 诊断")
    segs = {}
    for lo, hi, lab in SEGMENTS:
        vals = []
        for r in recs:
            if not (lo <= r["signal_date"] <= hi):
                continue
            if not np.isfinite(r["alpha_v1"]) or not r["lots"]:
                continue
            il = illiq_m.get(r["signal_date"])
            if il is None:
                continue
            cs = capacity_of(r["lots"], best["budget"])
            k = cs["k"]
            if k <= 0:
                continue
            base = (1 - cs["uninvested"]) * r["alpha_v1"] * 100
            to = r["to"] if np.isfinite(r["to"]) else 0.0
            idx = np.array([col_of[c] for c in r["traded"] if c in col_of], dtype=int)
            ia = impact_amihud(il, idx, best["budget"] / k, k)
            vals.append(base - 2.0 * to * COMM_RATE * 100.0 - ia)
        nw = C.newey_west(vals, NW_LAG)
        m_seg = statistics.mean(vals)      # 同上面的显示口径修正：vals 已是 pp
        segs[lab] = {"n": len(vals), "net_pp": round(m_seg, 6),
                     "t_nw": round(nw["t_nw"], 6)}
        print(f"   {lab}  n={len(vals):>3}  净={m_seg:+.4f}pp  t={nw['t_nw']:+.3f}")

    # 现金相对基准的拖累（描述性，不并入主统计量）
    # excess = f_inv×(R_bench + alpha) − R_bench = f_inv×alpha − (1−f_inv)×R_bench
    # → 拖累项是 **负的** (1−f_inv)×R_bench
    rb_mean = statistics.mean(r["r_bench"] for r in recs) * 100
    uninv = 1 - best["f_inv_median"]
    print(f"   描述性：现金相对基准的拖累 = −(1−f_inv)×R_bench = "
          f"−{uninv*100:.2f}% × {rb_mean:+.3f}pp = {-uninv*rb_mean:+.4f}pp"
          f"（beta 性质，方差大，不并入主统计量）")

    # ---- 产物
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal_date", "n_keep", "to", "alpha_v1_pp", "f_inv_1000w",
                    "comm_pp", "impact_A_pp", "impact_B_eta1_pp", "net_A_pp",
                    "net_B_eta1_pp"])
        for r in recs:
            if not np.isfinite(r["alpha_v1"]) or not r["lots"]:
                continue
            il, ad, sd = (illiq_m.get(r["signal_date"]), adv_m.get(r["signal_date"]),
                          sd_m.get(r["signal_date"]))
            if il is None:
                continue
            cs = capacity_of(r["lots"], 1.0e7)
            k = cs["k"]
            if k <= 0:
                continue
            to = r["to"] if np.isfinite(r["to"]) else 0.0
            idx = np.array([col_of[c] for c in r["traded"] if c in col_of], dtype=int)
            ia = impact_amihud(il, idx, 1.0e7 / k, k)
            ib = impact_sqrt(sd, ad, idx, 1.0e7 / k, k, 1.0)
            base = (1 - cs["uninvested"]) * r["alpha_v1"] * 100
            comm = 2.0 * to * COMM_RATE * 100.0
            w.writerow([r["signal_date"], len(r["keep_v1"]), round(to, 6),
                        round(r["alpha_v1"] * 100, 6), round(1 - cs["uninvested"], 6),
                        round(comm, 6), round(ia, 8), round(ib, 6),
                        round(base - comm - ia, 6), round(base - comm - ib, 6)])

    summary = {
        "experiment": "phasej-impact-cost-v1",
        "prereg_sha256": PREREG_SHA256,
        "prereg_version": "1.0",
        "model_note": "无盘中数据，冲击成本是**模型估计**，不是实测。"
                      "模型A=Amihud线性(无参数)；模型B=平方根，只报打平η*，不猜η",
        "data_fact": "exec_all 的 volume 单位是「手」→ DV = volume × 100 × close",
        "design": {"capital_tiers": list(CAPITAL_TIERS), "comm_rate": COMM_RATE,
                   "t_crit": T_CRIT, "win": WIN,
                   "net_def": "net = f_inv × alpha_V1 − 2·to·0.003·100 − impact"},
        "v1_reconciliation": {
            "vs_phasei_monthly": "max|diff| < 1e-6",
            "alpha_v1_pp": round(statistics.mean([r["alpha_v1"] * 100
                                                  for r in recs
                                                  if np.isfinite(r["alpha_v1"])]), 6),
        },
        "liquidity_distribution": {k: (round(v, 10) if isinstance(v, float) else v)
                                   for k, v in dist.items()},
        "by_capital": {k: {kk: (round(vv, 8) if isinstance(vv, float) else vv)
                           for kk, vv in v.items()} for k, v in by_w.items()},
        "verdict": jd["verdict"],
        "pass_by_w": jd["pass_by_w"],
        "attribution_best_tier": {
            "budget": best["budget"], "alpha_v1_pp": round(best["alpha_v1_pp"], 6),
            "cash_drag_pp": round(-best["cash_drag_pp"], 6),
            "comm_pp": round(-best["comm_pp"], 6),
            "impact_A_pp": round(-best["impact_A_pp"], 8),
            "impact_B_eta1_pp": round(-best["impact_B_eta1_pp"], 6),
            "net_pp": round(best["net_A_pp"], 6), "t_net": round(best["t_net_A"], 6),
        },
        "breakeven_eta": {k: {"mean0": round(v["breakeven_eta_mean0"], 4),
                              "t196": round(v["breakeven_eta_t196"], 4)}
                          for k, v in by_w.items()},
        "eta_reference_range": [ETA_REF_LO, ETA_REF_HI],
        "segments_at_best": segs,
        "descriptive_cash_drag_vs_bench_pp": round(uninv * rb_mean, 6),
    }
    sp = os.path.join(OUT_DIR, "summary.json")
    with open(sp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")

    files = ["monthly.csv", "summary.json"]
    man = {"experiment": "phasej-impact-cost-v1", "prereg_sha256": PREREG_SHA256,
           "script_sha256": _sha256_file(os.path.abspath(__file__)),
           "files": {n: {"sha256": _sha256_file(os.path.join(OUT_DIR, n)),
                         "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files},
           "runtime_sec": round(time.time() - t0, 1)}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=1) + "\n")

    print()
    print(f"[产物] {OUT_DIR}/  脚本 {man['script_sha256'][:12]}  "
          f"总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
