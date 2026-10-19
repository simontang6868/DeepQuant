"""按冻结的《资金档预登记》解出资金档位。

规则只用价格，不看任何收益数字 —— 这是本脚本唯一要守住的东西。
若规则依赖收益，"选档"就变成了"挑一个让结论好看的档"。

用法：
    python scripts/capital_tier_resolve.py
    python scripts/capital_tier_resolve.py --check-only   # 只校验冻结指纹，不计算
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.research.effective_pool import evaluate_pool  # noqa: E402
from deepquant.walkforward import WalkForwardConfig, generate_windows  # noqa: E402

SPEC = ROOT / "docs" / "capital_tier_prereg_2026-10-19.json"
SHA = ROOT / "docs" / "CAPITAL_TIER_PREREG_2026-10-19.sha256"


def verify() -> dict:
    spec_bytes = SPEC.read_bytes()
    actual = hashlib.sha256(spec_bytes).hexdigest()
    recorded = None
    for line in SHA.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        h, _, name = line.partition("  ")
        if name.strip().endswith(SPEC.name):
            recorded = h.strip()
    if actual != recorded:
        raise SystemExit(
            f"规格已被修改：实际 {actual[:16]}… 冻结 {str(recorded)[:16]}…\n"
            "冻结失效：改过的规则不能再用来定档。"
        )
    return {"spec_sha256": actual, "json": json.loads(spec_bytes)}


def feasible(gate_d: dict) -> bool:
    """该档下是否 6 只都在全部样本外窗口可建仓（且没有无数据的标的）。"""
    if gate_d["missing"] or gate_d["predicted_absent"]:
        return False
    return all(v["clean"] for v in gate_d["per_symbol"].values())


def main() -> int:
    ap = argparse.ArgumentParser(description="按冻结预登记解资金档")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--out", default="docs/CAPITAL_TIER_TABLE_2026-10-19.json")
    args = ap.parse_args()

    fr = verify()
    spec = fr["json"]
    print(f"[FREEZE-OK] {fr['spec_sha256'][:16]}…  {spec['spec_id']} v{spec['version']}")
    if args.check_only:
        return 0

    design = spec["data"]["walkforward"]
    cut = pd.Timestamp(spec["data"]["oos_cut"])
    source_dir = ROOT / spec["data"]["source_dir"]
    codes = [x["code"] for x in spec["data"]["pool"]]
    df = design

    frames, windows = {}, {}
    for c in codes:
        p = source_dir / f"{c}.qfq.csv"
        f = pd.read_csv(p)
        f["date"] = pd.to_datetime(f["date"])
        f = f.sort_values("date").reset_index(drop=True)
        frames[c] = f
        windows[c] = generate_windows(
            pd.DatetimeIndex(f["date"]),
            WalkForwardConfig(train_window=df["train_periods"], oos_window=df["oos_window"],
                              step=df["step"], min_train=df["train_periods"],
                              min_oos=df["oos_window"]),
        )

    n = len(codes)
    rows = []
    for tier in spec["ladder"]:
        row = {"cash": tier}
        for pol, budget in (("independent_account", tier),
                            ("equal_weight_portfolio", tier / n)):
            gate = evaluate_pool(frames, windows, cash=budget, basis="strict", cut=cut,
                                 nominal_n=n, expected_codes=codes)
            gd = gate.as_dict()
            row[pol] = {
                "budget_per_symbol": round(budget, 2),
                "deployable_windows_by_symbol": {
                    c: f"{v['n_oos_affordable']}/{v['n_oos_windows']}"
                    for c, v in gd["per_symbol"].items()
                },
                "n_absent": len(gd["predicted_absent"]),
                "absent": gd["predicted_absent"],
                "worst_one_lot": max(v["one_lot_max_overall"] for v in gd["per_symbol"].values()),
                "feasible": feasible(gd),
            }
        rows.append(row)
        a, b = row["independent_account"], row["equal_weight_portfolio"]
        print(f"  {tier/10000:>5.0f} 万 | 独立账户 {'✓' if a['feasible'] else '✗'} "
              f"缺席 {a['n_absent']} | 等权(预算 {b['budget_per_symbol']:>9,.0f}) "
              f"{'✓' if b['feasible'] else '✗'} 缺席 {b['n_absent']}")

    picked = {}
    for pol in ("independent_account", "equal_weight_portfolio"):
        hit = [r["cash"] for r in rows if r[pol]["feasible"]]
        picked[pol] = hit[0] if hit else None

    # 预登记预测逐条核对（跑之前写死的四条）
    preds = spec["predictions_before_computation"]
    check = {
        "P1_independent_tier": {
            "predicted": preds["P1_independent_tier"]["value"], "actual": picked["independent_account"],
            "held": picked["independent_account"] == preds["P1_independent_tier"]["value"],
        },
        "P2_equal_weight_tier": {
            "predicted": preds["P2_equal_weight_tier"]["value"], "actual": picked["equal_weight_portfolio"],
            "held": picked["equal_weight_portfolio"] == preds["P2_equal_weight_tier"]["value"],
        },
        "P3_at_200k_still_infeasible": {
            "predicted": preds["P3_at_200k_still_infeasible"]["value"],
            "actual": not next(r for r in rows if r["cash"] == 200000)["independent_account"]["feasible"],
            "held": (not next(r for r in rows if r["cash"] == 200000)["independent_account"]["feasible"])
                    == preds["P3_at_200k_still_infeasible"]["value"],
        },
        "P4_at_30k_effective_pool_is_6": {
            "predicted": preds["P4_at_30k_effective_pool_is_6"]["value"],
            "actual": n - next(r for r in rows if r["cash"] == 300000)["independent_account"]["n_absent"],
            "held": (n - next(r for r in rows if r["cash"] == 300000)["independent_account"]["n_absent"])
                    == preds["P4_at_30k_effective_pool_is_6"]["value"],
        },
    }
    n_held = sum(1 for v in check.values() if v["held"])

    out = {
        "spec": spec["spec_id"], "spec_version": spec["version"],
        "spec_sha256": fr["spec_sha256"],
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "cash_ladder": spec["ladder"],
        "n_symbols": n,
        "rows": rows,
        "picked": picked,
        "prediction_check": {"checks": check, "n_held": n_held, "n_total": len(check)},
        "note": "规则只用价格与整手成本；收益数字不参与选档。",
    }
    (ROOT / args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n独立账户档 = {picked['independent_account']:,.0f} 元"
          if picked["independent_account"] else "\n独立账户档 = 无解")
    print(f"等权组合档 = {picked['equal_weight_portfolio']:,.0f} 元"
          if picked["equal_weight_portfolio"] else "等权组合档 = 无解")
    print(f"预测命中 {n_held}/{len(check)}")
    for k, v in check.items():
        print(f"  [{'OK ' if v['held'] else 'MISS'}] {k}: 预测 {v['predicted']} 实际 {v['actual']}")
    print(f"\n[SAVED] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
