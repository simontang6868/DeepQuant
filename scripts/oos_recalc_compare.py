"""新旧口径逐格 / 逐标的 / 逐窗对照。

只做「比对」，不产生任何新判定：
- 判定直接读两份结果文件里的 `verdict`，不重算、不重判；
- 六条跑前预测在 `docs/oos_recalc_prereg_2026-10-19.json` 里，这里机械核对，
  落空就写 `held=false`，不给人留「事后圆回来」的空间。

用法：
    python scripts/oos_recalc_compare.py
输出：
    docs/OOS_RECALC_COMPARE_2026-10-19.json
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_PATH = ROOT / "docs" / "OOS_RESULTS_2026-10-19.json"
NEW_PATH = ROOT / "docs" / "OOS_RESULTS_RECALC_2026-10-19.json"
SPEC_PATH = ROOT / "docs" / "oos_recalc_prereg_v1.1_2026-10-19.json"
OUT_PATH = ROOT / "docs" / "OOS_RECALC_COMPARE_2026-10-19.json"

PRIMARY = "ma_cross|simulation_placeholder"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def dist_delta(a: dict | None, b: dict | None, key: str) -> dict | None:
    if not a or not b:
        return None
    av, bv = a.get(key), b.get(key)
    if av is None or bv is None:
        return {"old": av, "new": bv, "delta": None}
    return {"old": av, "new": bv, "delta": round(bv - av, 6)}


def cell_compare(old: dict, new: dict) -> dict:
    out = {}
    for cell in sorted(set(old["cells"]) | set(new["cells"])):
        o, n = old["cells"].get(cell), new["cells"].get(cell)
        if o is None or n is None:
            out[cell] = {"error": "某一侧缺失", "old_present": o is not None, "new_present": n is not None}
            continue
        out[cell] = {
            "IS": {
                "n": dist_delta(o["IS"], n["IS"], "n"),
                "median": dist_delta(o["IS"], n["IS"], "median"),
                "p10": dist_delta(o["IS"], n["IS"], "p10"),
                "p90": dist_delta(o["IS"], n["IS"], "p90"),
            },
            "OOS": {
                "n": dist_delta(o["OOS"], n["OOS"], "n"),
                "median": dist_delta(o["OOS"], n["OOS"], "median"),
                "pos_ratio": dist_delta(o["OOS"], n["OOS"], "pos_ratio"),
                "zero_trade_ratio": {
                    "old": o["OOS_zero_trade_ratio"], "new": n["OOS_zero_trade_ratio"],
                    "delta": (None if None in (o["OOS_zero_trade_ratio"], n["OOS_zero_trade_ratio"])
                              else round(n["OOS_zero_trade_ratio"] - o["OOS_zero_trade_ratio"], 4)),
                },
                "total_trades": {"old": o["OOS_total_trades"], "new": n["OOS_total_trades"]},
            },
            "E2": {
                "n_valid": dist_delta(o["E2"], n["E2"], "n_valid"),
                "ret_median": dist_delta(o["E2"], n["E2"], "ret_median"),
                "ret_mean": dist_delta(o["E2"], n["E2"], "ret_mean"),
            },
        }
    return out


def effective_pool(res: dict, cell: str) -> dict:
    """逐标的：名义在池 / 样本外窗口总数 / 有效观测 / 选择失败数。

    v1 只报了 pooled 的 n，看不出茅台贡献了 0 —— 这张表就是为了让缺席可见。
    """
    rows = res["e1_windows"][cell]
    per: dict[str, dict] = {}
    for r in rows:
        if r["bucket"] != "OOS":
            continue
        d = per.setdefault(r["symbol"], {"oos_windows": 0, "valid": 0, "sel_failed": 0, "zero_trade": 0})
        d["oos_windows"] += 1
        if r["valid"]:
            d["valid"] += 1
            if r["trades"] == 0:
                d["zero_trade"] += 1
        elif not r["sel_ok"]:
            d["sel_failed"] += 1
    nominal = [c for c in res["data"]]
    return {
        "n_nominal": len(nominal),
        "n_effective": sum(1 for c in nominal if per.get(c, {}).get("valid", 0) > 0),
        "per_symbol": {c: per.get(c, {"oos_windows": 0, "valid": 0, "sel_failed": 0, "zero_trade": 0})
                       for c in nominal},
        "absent": [c for c in nominal if per.get(c, {}).get("valid", 0) == 0],
    }


def window_join(old: dict, new: dict, cell: str) -> dict:
    """逐窗对照只取**纯样本外**窗口：P6 说的就是那 20 个，把 IS 混进来会让结论跑偏。"""
    def idx(res):
        return {(r["symbol"], r["oos_start"]): r
                for r in res["e1_windows"][cell] if r["bucket"] == "OOS"}
    o, n = idx(old), idx(new)
    common = sorted(set(o) & set(n))
    both_valid = [k for k in common if o[k]["valid"] and n[k]["valid"]]
    diffs, flips, rows = [], 0, []
    for k in both_valid:
        ro, rn = o[k]["ret"], n[k]["ret"]
        d = rn - ro
        diffs.append(abs(d))
        sign_flip = (ro > 0) != (rn > 0)
        flips += int(sign_flip)
        rows.append({
            "symbol": k[0], "oos_start": k[1],
            "old_ret": ro, "new_ret": rn, "delta": round(d, 6), "sign_flip": sign_flip,
        })
    return {
        "n_common_keys": len(common),
        "n_both_valid": len(both_valid),
        "n_only_old_valid": sum(1 for k in common if o[k]["valid"] and not n[k]["valid"]),
        "n_only_new_valid": sum(1 for k in common if n[k]["valid"] and not o[k]["valid"]),
        "n_sign_flip": flips,
        "median_abs_delta": round(statistics.median(diffs), 6) if diffs else None,
        "max_abs_delta": round(max(diffs), 6) if diffs else None,
        "delta_pp": [round(d * 100, 2) for d in diffs],
        "rows": rows,
    }


def prediction_check(spec: dict, old: dict, new: dict, cells: dict, pool_new: dict, join: dict) -> dict:
    """六条预测逐条机械核对。

    `held` 由数字决定，不由我决定；被污染的那几条（跑前已从日志里看到）标 `scoreable=false`，
    照实列 actual 但不计入命中率 —— 看过了还说命中没有意义。
    """
    pred = spec["predicted_before_run"]
    pc = cells[PRIMARY]
    old_oos_n = pc["OOS"]["n"]["old"]
    new_oos_n = pc["OOS"]["n"]["new"]
    d_med = pc["OOS"]["median"]["delta"]
    new_med = pc["OOS"]["median"]["new"]
    moutai = pool_new["per_symbol"]["600519.SH"]

    actual = {
        "P1_gate": {c: d["data_ok"] for c, d in new["data"].items()},
        "P2_moutai_still_absent": moutai,
        "P3_oos_n": {"old": old_oos_n, "new": new_oos_n},
        "P4_median": {"delta": d_med,
                      "delta_pp": None if d_med is None else round(d_med * 100, 3),
                      "new_median": new_med},
        "P5_verdict": {"old": old["verdict"], "new": new["verdict"]},
        "P6_window_flips": {"n_both_valid": join["n_both_valid"], "n_sign_flip": join["n_sign_flip"]},
    }
    held = {
        "P1_gate": all(d["data_ok"] for d in new["data"].values()),
        "P2_moutai_still_absent": moutai["valid"] == 0,
        "P3_oos_n": old_oos_n == 20 and new_oos_n == 25,
        "P4_median": (d_med is not None and abs(d_med) < 0.01 and new_med is not None and new_med < 0),
        "P5_verdict": new["verdict"] == "R4_INCONCLUSIVE",
        "P6_window_flips": 1 <= join["n_sign_flip"] <= 6,
    }

    checks = {}
    for k, v in pred.items():
        if not isinstance(v, dict) or "text" not in v:
            continue
        checks[k] = {
            "predicted": v["text"],
            "contamination": v.get("contamination", "unknown"),
            "scoreable": bool(v.get("scoreable", False)),
            "actual": actual.get(k),
            "held": held.get(k),
        }

    scored = [c for c in checks.values() if c["scoreable"]]
    return {
        "checks": checks,
        "n_scoreable": len(scored),
        "n_held_among_scoreable": sum(1 for c in scored if c["held"]),
        "missed_among_scoreable": [k for k, c in checks.items() if c["scoreable"] and not c["held"]],
        "missed_any": [k for k, c in checks.items() if c["held"] is False],
        "note": "scoreable=false 的条目在冻结前已从日志看到，不计入命中率；missed_any 仍全部列出",
    }


def main() -> None:
    old, new = load(OLD_PATH), load(NEW_PATH)
    spec = load(SPEC_PATH)

    cells = cell_compare(old, new)
    pool = {cell: effective_pool(new, cell) for cell in new["cells"]}
    pool_old = {cell: effective_pool(old, cell) for cell in old["cells"]}
    joins = {cell: window_join(old, new, cell) for cell in new["cells"]}

    pred = prediction_check(spec, old, new, cells, pool[PRIMARY], joins[PRIMARY])

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "what_this_is": "同一批数据、同一套设计、只换价格口径的数值对照；不是第二次样本外检验",
        "sources": {
            "old": {"path": OLD_PATH.name, "spec_sha256": old["freeze"]["spec_sha256"],
                    "data_caliber": old.get("data_caliber"), "verdict": old["verdict"],
                    "generated_at": old["generated_at"]},
            "new": {"path": NEW_PATH.name, "spec_sha256": new["freeze"]["spec_sha256"],
                    "data_caliber": new.get("data_caliber"), "verdict": new["verdict"],
                    "generated_at": new["generated_at"]},
            "recalc_spec": {"path": SPEC_PATH.name, "spec_sha256": new["freeze"]["spec_sha256"]},
        },
        "verdict": {
            "old": old["verdict"], "new": new["verdict"],
            "changed": old["verdict"] != new["verdict"],
            "note": "判定相同不代表策略被验证；两次读的都是同一批已消耗的样本外数据",
        },
        "cells": cells,
        "effective_pool": {"old": pool_old, "new": pool},
        "effective_pool_note": "n_effective = 在纯样本外至少贡献 1 个有效观测的标的数；absent 列出贡献 0 的标的",
        "window_join": joins,
        "prediction_check": pred,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    # ---------------- 控制台摘要
    print(f"{'cell':44s} {'OOS_n':>11} {'OOS_med':>19} {'zeroTr':>13} {'E2_med':>17}")
    for cell in sorted(cells):
        c = cells[cell]
        print(f"{cell:44s} "
              f"{c['OOS']['n']['old']:>4d}->{c['OOS']['n']['new']:<4d} "
              f"{c['OOS']['median']['old']:>+8.4f}->{c['OOS']['median']['new']:<+9.4f} "
              f"{str(c['OOS']['zero_trade_ratio']['old']):>5s}->{str(c['OOS']['zero_trade_ratio']['new']):<6s} "
              f"{str(c['E2']['ret_median']['old']):>8s}->{str(c['E2']['ret_median']['new']):<8s}")
    print(f"\nVERDICT old={old['verdict']} new={new['verdict']}")
    print(f"有效池(主格): nominal={pool[PRIMARY]['n_nominal']} effective={pool[PRIMARY]['n_effective']} "
          f"absent={pool[PRIMARY]['absent']}")
    print(f"逐窗对比(主格): both_valid={joins[PRIMARY]['n_both_valid']} "
          f"符号翻转={joins[PRIMARY]['n_sign_flip']} 中位|差|={joins[PRIMARY]['median_abs_delta']}")
    print(f"\n预测核对：可计分 {pred['n_scoreable']} 条，命中 {pred['n_held_among_scoreable']} 条；"
          f"未命中 {pred['missed_among_scoreable']}")
    for k, v in pred["checks"].items():
        flag = "OK " if v["held"] else "MISS"
        print(f"  [{flag}] {k:24s} scoreable={str(v['scoreable']):5s} contamination={v['contamination']}")
    print(f"\n[SAVED] {OUT_PATH}")


if __name__ == "__main__":
    main()
