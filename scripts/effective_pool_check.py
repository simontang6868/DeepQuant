"""有效池闸门的对账脚本：预测 vs 实测。

闸门若不能预见现实，就只是装饰。本脚本把「跑之前」的资金充足性预测，
与 `docs/OOS_RESULTS_2026-10-19.json`（v1，加性口径的实际运行）里记录的
`sel_ok` 逐窗对照，得出混淆矩阵。

注意：
- 这里只做**对账**，不重跑回测、不改任何既有结果文件；
- v1 的 `sel_ok=False` 有两种成因：资金不足（本闸门预测的那类）与
  训练期成交次数 < min_trades（策略本身不满足）。闸门只对前者负责，
  因此 FN（预测买得起但实际选参失败）需要按成因此拆开看。

用法：
    python scripts/effective_pool_check.py
    python scripts/effective_pool_check.py --spec docs/oos_recalc_prereg_v1.1_2026-10-19.json \
        --result docs/OOS_RESULTS_RECALC_2026-10-19.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.research.effective_pool import evaluate_pool  # noqa: E402
from deepquant.walkforward import WalkForwardConfig, generate_windows  # noqa: E402

CELL = "ma_cross|simulation_placeholder"
CUT = pd.Timestamp("2024-12-31")
"""IS 截止日：`oos_start > CUT` 的窗口算纯样本外窗口（与 oos_2026_run.py 一致）。"""


def load_frame(source_dir: Path, code: str) -> pd.DataFrame:
    df = pd.read_csv(source_dir / f"{code}.qfq.csv")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="有效池闸门预测 vs 实测对账")
    ap.add_argument("--spec", default="docs/oos_prereg_2026-10-19.json")
    ap.add_argument("--result", default="docs/OOS_RESULTS_2026-10-19.json")
    ap.add_argument("--out", default="docs/EFFECTIVE_POOL_GATE_2026-10-19.json")
    args = ap.parse_args()

    spec = json.loads((ROOT / args.spec).read_text(encoding="utf-8"))
    res = json.loads((ROOT / args.result).read_text(encoding="utf-8"))
    design = spec["design"]
    source_dir = ROOT / spec["data"]["source_dir"]
    codes = [x["code"] for x in spec["data"]["pool"]]

    frames = {}
    for c in codes:
        p = source_dir / f"{c}.qfq.csv"
        if p.exists():
            frames[c] = load_frame(source_dir, c)
    missing = [c for c in codes if c not in frames]

    wcfg = WalkForwardConfig(
        train_window=design["train_periods"], oos_window=design["oos_window"],
        step=design["step"], min_train=design["train_periods"], min_oos=design["oos_window"],
    )

    # 窗口逐标的分开生成：300750 是 2018 年上市，自有窗口数与 600519 不同，
    # 拿一套窗口套全池会去取不属于该标的的日期（实测直接抛「没有可用价格」）
    per_symbol_windows = {
        c: generate_windows(pd.DatetimeIndex(frames[c]["date"]), wcfg) for c in frames
    }

    gates = {}
    for basis in ("strict", "lenient"):
        g = evaluate_pool(frames, per_symbol_windows, cash=design["initial_cash"],
                          basis=basis, nominal_n=len(codes), cut=CUT)
        gates[basis] = g.as_dict()

    # ---- 实测：v1 结果里每个 (symbol, window_index) 的 sel_ok ----
    actual = {}
    for w in res["e1_windows"][CELL]:
        actual[(w["symbol"], w["window_index"])] = {
            "sel_ok": bool(w["sel_ok"]),
            "valid": bool(w["valid"]),
            "trades": w["trades"],
            "bucket": w["bucket"],
        }

    def confusion(gate_d: dict, only_bucket: str | None = None) -> dict:
        tp = fp = fn = tn = 0
        fn_rows, fp_rows = [], []
        for (code, wi), a in sorted(actual.items()):
            if only_bucket and a["bucket"] != only_bucket:
                continue
            sym = gate_d["per_symbol"].get(code)
            if sym is None:
                continue
            blocked = any(b["code"] == code and b["window_index"] == wi
                          for b in gate_d["blocked"])
            pred_ok = not blocked
            if pred_ok and a["sel_ok"]:
                tn += 1
            elif pred_ok and not a["sel_ok"]:
                fn += 1
                fn_rows.append({"code": code, "window_index": wi,
                                "bucket": a["bucket"], "trades": a["trades"]})
            elif not pred_ok and not a["sel_ok"]:
                tp += 1
            else:
                fp += 1
                fp_rows.append({"code": code, "window_index": wi, "bucket": a["bucket"]})
        return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "precision": round(tp / (tp + fp), 4) if tp + fp else None,
                "recall": round(tp / (tp + fn), 4) if tp + fn else None,
                "fn_rows": fn_rows, "fp_rows": fp_rows}

    out = {
        "spec": args.spec,
        "result": args.result,
        "cell": CELL,
        "cash": design["initial_cash"],
        "cut": str(CUT.date()),
        "n_windows": {c: len(v) for c, v in per_symbol_windows.items()},
        "nominal_n": len(codes),
        "missing_files": missing,
        "gates": gates,
        "gate_passed": {
            "strict": {"g2": gates["strict"]["g2_passed"], "g3": gates["strict"]["g3_passed"]},
            "lenient": {"g2": gates["lenient"]["g2_passed"], "g3": gates["lenient"]["g3_passed"]},
        },
        "confusion": {b: {"all_windows": confusion(gates[b]),
                          "oos_windows_only": confusion(gates[b], only_bucket="OOS")}
                      for b in ("strict", "lenient")},
    }
    for basis in ("strict", "lenient"):
        g = gates[basis]
        print(f"\n== {basis} 判据 ==")
        print(f"  名义 {g['nominal_n']} 只 / 可建仓 {g['n_effective']} 只 "
              f"→ 样本外缺席 {g['predicted_absent']}")
        print(f"  G2(所有窗口都买得起)={g['g2_passed']}  G3(无样本外缺席)={g['g3_passed']}"
              f"  最差窗口覆盖 {g['worst_window_coverage']:.0%}")
        for c, v in g["per_symbol"].items():
            print(f"   {c} 训练段可负担 {v['n_train_affordable']}/{v['n_windows']} "
                  f"样本外窗口可建仓 {v['n_oos_affordable']}/{v['n_oos_windows']} "
                  f"最贵一手 {v['one_lot_max_overall']:,.0f}"
                  f"{'  ← 样本外缺席' if v['absent'] else ''}")
        for tag in ("all_windows", "oos_windows_only"):
            cf = out["confusion"][basis][tag]
            print(f"  [{tag}] TP={cf['tp']} FP={cf['fp']} FN={cf['fn']} TN={cf['tn']} "
                  f"precision={cf['precision']} recall={cf['recall']}")
            if cf["fp_rows"]:
                print(f"     FP（预测阻断但实际选参成功）{cf['fp_rows']}")

    (ROOT / args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
