"""阶段 0 结论的资金混淆审计：Δmedian 里有多少是「买不起」造成的。

问题
----
阶段 0（v1.2）判定 S1：A 组（在市 289 只）窗口收益中位 −1.32%，
B 组（退市 229 只）−2.99%，Δmedian = −1.67pp，解读为「幸存者偏差真实存在」。

但这个 Δ 是在 `initial_cash = 100_000` 下测出来的。已知：
固定资金 + A 股 100 股整手 ⇒ 一手成本 > 资金 时买单必然被拒 ⇒
训练期零成交 ⇒ 选参失败 ⇒ 该标的该窗口**整段消失**。

于是 Δ 有可能不是「幸存者偏差」，而是「**A 组里有更多贵到买不起的标的被删了**」，
或反之。两者是竞争性解释，必须分开。

本脚本做什么（只读，不重跑回测）
--------------------------------
1. 用 `effective_pool` 逐标的、逐窗口重建资金闸门，得到被阻断的 (code, window)；
2. 与 v1.2 结果里真实的 `selection_success=False` 对照（闸门能否预见现实）；
3. **核心**：看被删掉的标的在「没被删的窗口」上收益处于组内什么位置
   —— 如果被删的是组内表现特别好的标的，删它会让 A 组中位被低估，
   则真实偏差 ≥ 测得的 −1.67pp（结论偏保守，可放心）；
4. 给反事实边界：把缺席窗口按乐观/中性/悲观三种假设还回去，Δ 的取值区间。

用法
----
    python scripts/stage0_capital_confound.py
    python scripts/stage0_capital_confound.py --result docs/STAGE0_RESULTS_V12_2026-10-19.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.research.effective_pool import evaluate_pool  # noqa: E402
from deepquant.walkforward import WalkForwardConfig, generate_windows  # noqa: E402

# 与 scripts/stage0_survivorship_probe.py 逐项一致（R2/R11：不另发明参数）
TRAIN = 150
OOS = 75
STEP = 75
INITIAL_CASH = 100_000.0
START = "2017-01-01"
END = "2024-12-31"
LAYOUT = "mult"

MULT_DIRS = {
    "A_listed": "data/qfq_mult/listed",
    "B_delisted": "data/qfq_mult/delist",
}


def load_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df[(df["date"] >= START) & (df["date"] <= END)].reset_index(drop=True)


def desc(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    a = np.array(vals, dtype=float)
    return {
        "n": int(a.size),
        "median": round(float(np.median(a)), 6),
        "mean": round(float(a.mean()), 6),
        "p25": round(float(np.percentile(a, 25)), 6),
        "p75": round(float(np.percentile(a, 75)), 6),
        "worst": round(float(a.min()), 6),
        "best": round(float(a.max()), 6),
        "loss_ratio": round(float((a < 0).sum() / a.size), 4),
    }


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def mannwhitney(x: list[float], y: list[float]) -> dict:
    """Mann-Whitney U（tie 校正正态近似），与探针/预登记同一写法。"""
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return {"error": "empty group"}
    allv = np.array(x + y, dtype=float)
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(allv.size, dtype=float)
    ranks[order] = np.arange(1, allv.size + 1, dtype=float)
    s = pd.Series(allv)
    for _v, idx in s.groupby(s).groups.items():
        if len(idx) > 1:
            m = float(np.mean(ranks[list(idx)]))
            ranks[list(idx)] = m
    r1 = float(ranks[:n1].sum())
    u1 = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    n = n1 + n2
    tie = float(sum((t ** 3 - t) for t in s.value_counts() if t > 1))
    var = n1 * n2 / 12.0 * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if var <= 0:
        return {"error": "zero variance"}
    z = (u1 - mu) / math.sqrt(var)
    p_lower = _phi(z)
    p_upper = 1.0 - p_lower
    return {
        "n_B": n1, "n_A": n2, "U_B": round(u1, 3), "expected_U": round(mu, 3),
        "z": round(z, 4), "p_one_sided_B_less": round(p_lower, 8),
        "p_two_sided": round(min(1.0, 2 * min(p_lower, p_upper)), 8),
        "rank_biserial_B_minus_A": round(2 * u1 / (n1 * n2) - 1, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="阶段 0 资金混淆审计")
    ap.add_argument("--result", default="docs/STAGE0_RESULTS_V12_2026-10-19.json")
    ap.add_argument("--out", default="docs/STAGE0_CAPITAL_CONFOUND_2026-10-19.json")
    args = ap.parse_args()

    res = json.loads((ROOT / args.result).read_text(encoding="utf-8"))
    wins = res["windows"]

    # 只分析真正进入结果的标的
    codes_by_group: dict[str, list[str]] = {}
    for w in wins:
        codes_by_group.setdefault(w["group"], set()).add(w["code"])
    codes_by_group = {g: sorted(v) for g, v in codes_by_group.items()}

    frames: dict[str, pd.DataFrame] = {}
    skipped: list[dict] = []
    for g, codes in codes_by_group.items():
        d = ROOT / MULT_DIRS.get(g, "")
        for c in codes:
            p = d / f"{c}.qfq.csv"
            if not p.exists():
                skipped.append({"code": c, "group": g, "reason": "乘性文件缺失"})
                continue
            df = load_frame(p)
            if len(df) < 400:
                skipped.append({"code": c, "group": g, "reason": f"区间内仅 {len(df)} 行"})
                continue
            frames[c] = df

    wcfg = WalkForwardConfig(train_window=TRAIN, oos_window=OOS, step=STEP,
                             min_train=TRAIN, min_oos=OOS)
    per_symbol_windows = {
        c: generate_windows(pd.DatetimeIndex(f["date"]), wcfg) for c, f in frames.items()
    }

    gates = {}
    for basis in ("strict", "lenient"):
        g = evaluate_pool(frames, per_symbol_windows, cash=INITIAL_CASH, basis=basis,
                          nominal_n=sum(len(v) for v in codes_by_group.values()),
                          cut=pd.Timestamp(END))
        gates[basis] = g.as_dict()

    # ---------------- 实测：真实 selection_success ----------------
    actual: dict[tuple[str, str]] = {}
    for w in wins:
        actual[(w["code"], str(w["oos_start"]))] = {
            "selection_success": bool(w["selection_success"]),
            "oos_success": bool(w["oos_success"]),
            "is_complete": bool(w["is_complete"]),
            "ret": w["ret"],
            "trades": w["trades"],
        }

    # 闸门预测 ↔ 实测对照（按 oos_start 匹配）
    def confusion(basis: str) -> dict:
        g = gates[basis]
        tp = fp = fn = tn = 0
        fn_rows: list[dict] = []
        fp_rows: list[dict] = []
        for (code, os_), a in sorted(actual.items()):
            hit = [b for b in g["blocked"]
                   if b["code"] == code and str(b.get("train_start")) <= os_]
            # 闸门按窗口索引，这里退化为「该标的训练段是否被阻断过」
            blocked_any = any(b["code"] == code for b in g["blocked"])
            pred_ok = not blocked_any
            if pred_ok and a["selection_success"]:
                tn += 1
            elif pred_ok and not a["selection_success"]:
                fn += 1
                fn_rows.append({"code": code, "oos_start": os_, "trades": a["trades"]})
            elif not pred_ok and not a["selection_success"]:
                tp += 1
            else:
                fp += 1
                fp_rows.append({"code": code, "oos_start": os_})
        return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                "recall": round(tp / (tp + fn), 4) if tp + fn else None,
                "n_fn": len(fn_rows), "fn_rows": fn_rows[:10], "fp_rows": fp_rows[:10]}

    # ---------------- 核心：被删标的在未删窗口上的位置 ----------------
    # 收集每只标的的有效收益
    by_code: dict[str, list[float]] = {}
    group_of: dict[str, str] = {}
    for w in wins:
        if (w["selection_success"] and w["oos_success"] and w["is_complete"]
                and w["ret"] is not None):
            by_code.setdefault(w["code"], []).append(float(w["ret"]))
            group_of[w["code"]] = w["group"]

    blocked_codes = sorted({b["code"] for b in gates["strict"]["blocked"]}
                           | {b["code"] for b in gates["lenient"]["blocked"]})

    profile = []
    for c in blocked_codes:
        rets = by_code.get(c, [])
        gname = group_of.get(c, "?")
        peers = [v for code, vals in by_code.items()
                 if group_of.get(code) == gname for v in vals]
        if not rets or not peers:
            continue
        pe = float(np.mean(np.array(peers) >= np.median(rets))) if rets else None
        profile.append({
            "code": c,
            "group": gname,
            "n_valid_windows": len(rets),
            "n_blocked_strict": sum(1 for b in gates["strict"]["blocked"] if b["code"] == c),
            "n_blocked_lenient": sum(1 for b in gates["lenient"]["blocked"] if b["code"] == c),
            "one_lot_max": gates["strict"]["per_symbol"][c]["one_lot_max_overall"],
            "median_ret": round(float(np.median(rets)), 6),
            "peer_median": round(float(np.median(peers)), 6),
            "peer_quantile_of_own_median": round(pe, 4) if pe is not None else None,
        })

    # ---------------- 反事实边界 ----------------
    # 对每个「闸门预测被阻断 且 实测 selection_success=False」的窗口，
    # 我们不知道它本可以产生的收益，按三种假设插回该组收益列表。
    def counterfactual(basis: str, mode: str) -> dict:
        g = gates[basis]
        blocked_pairs = {(b["code"], b["window_index"]) for b in g["blocked"]}
        A: list[float] = []
        B: list[float] = []
        for w in wins:
            if not (w["selection_success"] and w["oos_success"] and w["is_complete"]):
                continue
            if w["ret"] is None:
                continue
            (A if w["group"] == "A_listed" else B).append(float(w["ret"]))
        refs = {"A": np.array(A), "B": np.array(B)}

        n_imputed = 0
        for code, wi in sorted(blocked_pairs):
            gname = group_of.get(code)
            if gname is None:
                continue
            ref = refs["A" if gname == "A_listed" else "B"]
            if mode == "optimistic":
                v = float(np.percentile(ref, 90))
            elif mode == "pessimistic":
                v = float(np.percentile(ref, 10))
            else:  # neutral：该标的自身成功窗口的中位
                own = by_code.get(code, [])
                if not own:
                    continue
                v = float(np.median(own))
            (A if gname == "A_listed" else B).append(v)
            n_imputed += 1

        mA = float(np.median(A)) if A else float("nan")
        mB = float(np.median(B)) if B else float("nan")
        return {"mode": mode, "n_imputed": n_imputed, "n_A": len(A), "n_B": len(B),
                "median_A": round(mA, 6), "median_B": round(mB, 6),
                "delta_pp": round((mB - mA) * 100, 4)}

    base_A = [float(w["ret"]) for w in wins
              if w["group"] == "A_listed" and w["selection_success"]
              and w["oos_success"] and w["is_complete"] and w["ret"] is not None]
    base_B = [float(w["ret"]) for w in wins
              if w["group"] == "B_delisted" and w["selection_success"]
              and w["oos_success"] and w["is_complete"] and w["ret"] is not None]

    baseline = {
        "median_A": round(float(np.median(base_A)), 6),
        "median_B": round(float(np.median(base_B)), 6),
        "n_A": len(base_A), "n_B": len(base_B),
        "delta_pp": round((float(np.median(base_B)) - float(np.median(base_A))) * 100, 4),
        "mannwhitney": mannwhitney(base_B, base_A),
    }

    payload = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "purpose": (
            "检验阶段 0 的 Δmedian 是否被「固定 10 万本金买不起一手」混淆："
            "若被删标的在组内属极端值，则 Δ 有偏"
        ),
        "source_result": args.result,
        "params": {"train": TRAIN, "oos": OOS, "step": STEP,
                   "initial_cash": INITIAL_CASH, "start": START, "end": END,
                   "layout": LAYOUT},
        "counts": {
            "n_windows_total": len(wins),
            "n_symbols_in_result": sum(len(v) for v in codes_by_group.values()),
            "n_symbols_loadable": len(frames),
            "n_skipped": len(skipped),
        },
        "gates": gates,
        "confusion_vs_actual": {b: confusion(b) for b in ("strict", "lenient")},
        "blocked_symbol_profile": profile,
        "baseline": baseline,
        "counterfactual": {
            "optimistic": counterfactual("strict", "optimistic"),
            "neutral": counterfactual("strict", "neutral"),
            "pessimistic": counterfactual("strict", "pessimistic"),
        },
        "skipped": skipped,
    }

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    # ---------------- 控制台摘要 ----------------
    print(f"[LOAD] 结果窗口 {len(wins)}；可加载标的 {len(frames)}；跳过 {len(skipped)}")
    for b in ("strict", "lenient"):
        g = gates[b]
        print(f"\n[GATE {b}] G2={g['g2_passed']} G3={g['g3_passed']} "
              f"缺席 {g['predicted_absent']} 无数据 {list(g['missing'])[:3]}")
        print(f"        训练段被阻断明细 {len(g['blocked'])} 条；最差窗口覆盖 "
              f"{g['worst_window_coverage']:.1%}")
        cf = confusion(b)
        print(f"        对账 TP={cf['tp']} FP={cf['fp']} FN={cf['fn']} TN={cf['tn']} "
              f"precision={cf['precision']} recall={cf['recall']}")

    print(f"\n[BASELINE] A 中位 {baseline['median_A']:.6f} (n={baseline['n_A']})；"
          f"B 中位 {baseline['median_B']:.6f} (n={baseline['n_B']})；"
          f"Δ={baseline['delta_pp']:+.4f}pp")
    print(f"           MWU p_one={baseline['mannwhitney'].get('p_one_sided_B_less')} "
          f"rb={baseline['mannwhitney'].get('rank_biserial_B_minus_A')}")

    if profile:
        print("\n[被资金闸门阻断的标的：在成功窗口上的收益位置]")
        for p in profile:
            print(f"  {p['code']:12s} {p['group']:11s} 阻断(strict)={p['n_blocked_strict']:2d} "
                  f"最贵一手={p['one_lot_max']:>12,.0f}  "
                  f"自身中位={p['median_ret']:+.4f} 同组中位={p['peer_median']:+.4f} "
                  f"分位={p['peer_quantile_of_own_median']}")

    print("\n[反事实边界：把缺席窗口按假设插回后]")
    for k, v in payload["counterfactual"].items():
        print(f"  {k:12s} 插回 {v['n_imputed']:3d} 窗 → A 中位 {v['median_A']:+.6f} "
              f"B 中位 {v['median_B']:+.6f}  Δ={v['delta_pp']:+.4f}pp")

    print(f"\n[OUT] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
