#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全 A 行情（`data/exec_all/`）三步验收 + as-of 宇宙 manifest。

**未完成这三步之前，`data/exec_all/` 不得用于任何实验。**
判据（照抄 `docs/PIT_DECISION_2026-10-21.md` §5）：

  V1 覆盖率：落盘只数 / 5,223，并记录失败清单
  V2 重叠段逐行交叉验证：与 `data/exec_long/` 的 549 只逐行比对（这是我抓出 002916 截断的唯一手段）
  V3 as-of manifest：每只的（首日=上市、末日=退市/序列终点），并按年统计可投资只数

用法：
  python scripts/verify_exec_all.py
"""

from __future__ import annotations

import csv
import glob
import json
import os
from collections import Counter

import pandas as pd


def read_exec(path: str) -> dict[str, tuple]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return {}
    hdr = [h.strip().lower() for h in rows[0]]
    idx = {h: hdr.index(h) for h in ("date", "open", "high", "low", "close", "volume") if h in hdr}
    out = {}
    for r in rows[1:]:
        if not r:
            continue
        try:
            out[r[idx["date"]]] = tuple(float(r[idx[k]]) if r[idx[k]] else float("nan")
                                       for k in ("open", "high", "low", "close", "volume"))
        except (IndexError, ValueError):
            continue
    return out


def main() -> int:
    src = "data/_all_a_v2_codes.json" if os.path.exists("data/_all_a_v2_codes.json") else "data/_all_a_codes.json"
    if src.endswith("_v2_codes.json"):
        want = set(json.load(open(src, encoding="utf-8")))
    else:
        want = {c["code"] for c in json.load(open(src, encoding="utf-8"))}
    print(f"[V0] 目标清单：{src}（{len(want)} 只）")
    have = {os.path.basename(p).replace(".exec.csv", "") for p in glob.glob("data/exec_all/*.exec.csv")}
    missing = sorted(want - have)
    print(f"[V1] 目标 {len(want)} 只 | 已落盘 {len(have)} 只 | 缺失 {len(missing)} 只")
    if missing:
        print(f"     缺失样例：{missing[:15]}")

    # ---- V2 重叠段交叉验证（与 exec_long 的 549 只）----
    ref_codes = [os.path.basename(p).replace(".exec.csv", "") for p in sorted(glob.glob("data/exec_long/*.exec.csv"))]
    bad, checked, missing_ref, extra = [], 0, [], []
    for code in ref_codes:
        p_new = f"data/exec_all/{code}.exec.csv"
        if not os.path.exists(p_new):
            # 缺文件属 **V1 覆盖问题**，不是 V2 一致性问题；单独记账，不让 V2 判死
            missing_ref.append(code)
            continue
        a = read_exec(f"data/exec_long/{code}.exec.csv")
        b = read_exec(p_new)
        if not a or not b:
            bad.append({"code": code, "reason": "empty_side"})
            continue

        # ⚠️ V2 自纠（2026-10-21）：旧版只比对 `set(a) & set(b)` 的**交集数值**，
        #    对"日期缺失"完全无感。变异测试实测：把 exec_all 砍掉最后 200 行，
        #    旧版仍判 PASS（common=4940、数值差异=0）——这正是 002916 截断能存活两轮的原因。
        #    "逐行交叉验证"必须同时验两条：①交集内数值一致 ②区间内日期不缺。
        lo = max(min(a), min(b))
        hi = min(max(a), max(b))
        if lo > hi:
            bad.append({"code": code, "reason": "no_overlap_window"})
            continue
        da = {d for d in a if lo <= d <= hi}
        db = {d for d in b if lo <= d <= hi}
        common = sorted(da & db)
        if len(common) < 50:
            bad.append({"code": code, "reason": f"too_few_common:{len(common)}"})
            continue

        diffs = 0
        for d in common:
            x, y = a[d], b[d]
            if any(abs(u - v) > 1e-9 for u, v in zip(x, y)):
                diffs += 1
        missing_dates = sorted(da - db)      # exec_all 少了（严重）
        extra_dates = sorted(db - da)        # exec_all 多了（可能是 ref 截断）
        checked += 1
        if diffs:
            bad.append({"code": code, "reason": f"value_diff:{diffs}/{len(common)}"})
        elif missing_dates:
            bad.append({"code": code, "reason": f"missing_dates:{len(missing_dates)}",
                        "sample": missing_dates[:3], "window": [lo, hi]})
        elif extra_dates:
            extra.append({"code": code, "reason": f"extra_dates:{len(extra_dates)}",
                          "sample": extra_dates[:3]})
    print(f"[V2] 交叉验证 {checked} 只（与 exec_long）| 数值/日期不一致 {len(bad)} 只 | "
          f"缺失文件 {len(missing_ref)} 只（归 V1）| exec_all 多出日期 {len(extra)} 只")
    print("[V2] 口径：①区间内交集数值 5 字段全比对 ②区间内日期集合双向比对"
          "（变异测试已证实可检出 200 天截断）")
    for r in bad[:10]:
        print(f"     [BAD] {r['code']} {r['reason']}")

    # ---- V3 as-of manifest ----
    recs = []
    for code in sorted(have):
        p = f"data/exec_all/{code}.exec.csv"
        rows = read_exec(p)
        if not rows:
            continue
        ds = sorted(rows)
        recs.append({"code": code, "first": ds[0], "last": ds[-1], "n_days": len(ds)})
    by_year: dict[str, int] = {}
    years = sorted({r["first"][:4] for r in recs} | {"2026"})
    for y in years:
        d0, d1 = f"{y}-01-01", f"{y}-12-31"
        n = sum(1 for r in recs if r["first"] <= d1 and r["last"] >= d0)
        by_year[y] = n
    print(f"[V3] as-of manifest：{len(recs)} 只；按年可投资只数（当年有交易）：")
    for y, n in by_year.items():
        print(f"     {y}: {n}")

    listed_before = Counter(r["first"][:4] for r in recs)
    print(f"[V3] 上市年份分布（前 8）：{listed_before.most_common(8)}")

    out = {
        "n_target": len(want), "n_have": len(have), "n_missing": len(missing), "missing": missing,
        "v2_checked": checked, "v2_bad": bad, "v2_missing_ref": missing_ref,
        "v2_extra": extra[:20],
        "n_manifest": len(recs),
        "investable_by_year": by_year,
        "listing_year_counts": dict(listed_before),
        "manifest_sample": recs[:5],
    }
    path = "data/exec_all/_acceptance.json"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    # 完整 manifest（供后续实验使用）
    with open("data/exec_all/_asof_manifest.json", "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(recs, ensure_ascii=False) + "\n")
    print(f"[V3] -> {path} 与 data/exec_all/_asof_manifest.json")
    ok = (len(bad) == 0)
    print(f"[验收] {'PASS' if ok else 'FAIL'}（V2 必须 0 不一致）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
