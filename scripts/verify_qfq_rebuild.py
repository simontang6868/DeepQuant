#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""复权重建的验收闸门：新旧 `qfq` 目录逐日逐位比对。

为什么需要独立脚本而不是「看一眼」：
  重建 `qfq_all` 是**研究底座**的替换。历史 L3z–L3af 的全部结论都建立在这批序列上。
  如果重算让某只股票的历史涨停/除权被轻微改变，结论的可比性就失效了。
  所以提升前必须回答两个问题，且要机械可答：
    Q1 重叠区间内，新序列是否**逐位等于**旧序列？
    Q2 旧序列有、新序列没有的日期有多少？（数据丢失是最危险的静默失败）

判据（四档，不允许合并成一档）：
  ``BITWISE``    max|diff| == 0.0            —— 最严，实测可达（同脚本同口径必然 0）
  ``STORAGE``    max|diff| <= 1 个末位        —— 只能由「6 位小数的独立舍入」解释
  ``REANCHORED`` 比值 old/new 在各交易日均恒定（相对极差 <= 1e-5），**且**
                 新增区间内确有除权事件        —— 前复权锚点移动到新末端的必然结果
  ``FAIL``       以上都不满足                  —— 口径变了，禁止提升

⚠️ ``REANCHORED`` 不是「放宽」，它是前复权的一条**代数性质**：
   ADJ(t) = EXEC(t) × Π_{s>t} f_s。若在末端新增一个事件 s0（旧区间里没有），
   则对所有 t < s0：ADJ_new(t) = ADJ_old(t) × f_s0，比值严格恒定、收益率完全相消。
   因此这一档**必须同时**满足两个条件才认：
     (a) 比值恒定 —— 排除「漏事件 / 单位错 / 分段错」这类会改变比值结构的错误；
     (b) 新增区间确有除权事件 —— 排除「无理由的整体缩放」。
   任一不满足即落回 ``FAIL``。

⚠️ 容差的推导（不是拍脑袋的 1e-6）：
  产物写盘保留 6 位小数 ⇒ 末位 = 1e-6。
  **单个数**被舍入的误差上界是 0.5 个末位。但这里比的是**两个各自舍入过的数**，
  两者反向舍入的最大差是 **1 个末位 = 1e-6**，不是 0.5e-6。
  （第一版把界写成 0.5e-6，`12.5 + 0.5e-6` 实测差 5.00000000514e-07 > 5e-07 而被误判 FAIL
   —— 半末位在 float64 下本来就不可精确表示。）
  另加相对 1e-9 余量吸收浮点表示误差（1e-15 量级，远小于余量）。

用法：
  python scripts/verify_qfq_rebuild.py --old data/qfq_all --new data/qfq_all_next
  python scripts/verify_qfq_rebuild.py ... --json out.json --allow-new-days 12
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

#: qfq 产物写盘保留 6 位小数 ⇒ 1 个末位 = 1e-6。
#: 两个**各自舍入过**的数最大差 1 个末位（不是半末位，见模块文档）。
#: 参考 MEMORY 铁律「容差按产物存储精度定」：先算 0.5e-6/|值|，再决定用哪个界。
STORE_ULP = 1e-6

#: 浮点表示余量：float64 在 1e-6 量级的绝对误差约 1e-22，1e-9 相对余量足够且有量级冗余。
FP_SLACK = 1e-9

#: 参与比对的列。volume 也要比——它是科创板「股 vs 手」混用的唯一暴露点。
COLS = ("open", "close", "high", "low", "volume")

BITWISE = "BITWISE"
STORAGE = "STORAGE"
REANCHORED = "REANCHORED"
FAIL = "FAIL"

#: 判「比值恒定」的相对极差上限。取值依据同 STORE_ULP：ADJ 存 6 位小数，
#: 两次独立舍入后比值本身有 ~1e-6 的相对抖动，故给 1e-5 有余量但不至于放过结构性差异。
RATIO_SPREAD_MAX = 1e-5


def load_series(path: str) -> dict[str, dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def compare_one(code: str, old_dir: str, new_dir: str,
                new_segment_events: list[str] | None = None) -> dict:
    name = f"{code}.qfq.csv"
    old_p, new_p = os.path.join(old_dir, name), os.path.join(new_dir, name)
    res: dict = {"code": code, "status": None, "old_exists": os.path.exists(old_p),
                 "new_exists": os.path.exists(new_p)}
    if not res["old_exists"]:
        res["status"] = "OLD_MISSING"      # 新目录多出标的 —— 属池子变更，须单独审
        return res
    if not res["new_exists"]:
        res["status"] = "NEW_MISSING"      # 重建漏掉 —— 严重
        return res

    old, new = load_series(old_p), load_series(new_p)
    common = sorted(set(old) & set(new))
    only_new = sorted(set(new) - set(old))
    only_old = sorted(set(old) - set(new))

    maxd = {c: 0.0 for c in COLS}
    bad_rows: list[str] = []
    ratios: list[float] = []
    for d in common:
        for c in COLS:
            try:
                dd = abs(float(old[d][c]) - float(new[d][c]))
            except (KeyError, ValueError):
                res["status"] = "BAD_FIELD"
                res["bad_field"] = f"{d}:{c}"
                return res
            if dd > maxd[c]:
                maxd[c] = dd
            if dd > 0:
                bad_rows.append(f"{d}:{c}")
        # 只用 close 算比值：它是判断「整体等比缩放」的充分统计量
        oc, nc = float(old[d]["close"]), float(new[d]["close"])
        if nc > 0 and oc > 0:
            ratios.append(oc / nc)
    worst = max(maxd.values()) if maxd else 0.0

    ratio_spread = (max(ratios) - min(ratios)) if len(ratios) >= 2 else 0.0
    has_new_event = bool(new_segment_events)

    if only_old:
        status = "LOST_DAYS"               # 优先级最高：先报丢数据
    elif worst == 0.0:
        status = BITWISE
    elif worst <= STORE_ULP * (1.0 + FP_SLACK):
        status = STORAGE
    elif ratio_spread <= RATIO_SPREAD_MAX and has_new_event:
        # 整体等比缩放 + 新增区间确有除权事件 ⇒ 锚点移动，不是口径错误
        status = REANCHORED
    else:
        status = FAIL

    res.update({
        "status": status,
        "n_common": len(common),
        "n_only_new": len(only_new),
        "n_only_old": len(only_old),
        "max_abs_diff": {c: maxd[c] for c in COLS},
        "worst": worst,
        "n_diff_cells": len(bad_rows),
        "ratio_spread": ratio_spread,
        "reanchor_factor": (sum(ratios) / len(ratios)) if ratios else None,
        "new_segment_has_event": has_new_event,
        "new_segment_events": (new_segment_events or [])[:6],
        "diff_sample": bad_rows[:5],
        "new_range": (only_new[0], only_new[-1]) if only_new else None,
        "lost_sample": only_old[:5],
        "old_last": max(old) if old else None,
        "new_last": max(new) if new else None,
    })
    return res


def _new_segment_events(report_path: str | None, old_dir: str, new_dir: str) -> dict[str, list[str]]:
    """每只股票在「旧序列末日期之后」新增的除权事件日期。

    没有 report 就无法佐证锚点移动，``REANCHORED`` 一律不认（全部落回 FAIL）——
    这是有意的：宁可报错，也不接受无证据的缩放解释。
    """
    if not report_path or not os.path.exists(report_path):
        return {}
    with open(report_path, encoding="utf-8") as f:
        rep = json.load(f)
    out: dict[str, list[str]] = {}
    for r in rep.get("results", []):
        code = r.get("code")
        if not code:
            continue
        p = os.path.join(old_dir, f"{code}.qfq.csv")
        if not os.path.exists(p):
            continue
        old_last = None
        with open(p, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                old_last = row["date"]
        if not old_last:
            continue
        seg = [e["ex_date"] for e in (r.get("events") or []) if e.get("ex_date", "") >= old_last]
        if seg:
            out[code] = seg
    return out


def build(old_dir: str, new_dir: str, *, allow_new_days: int | None = None,
          report_path: str | None = None) -> dict:
    if not os.path.isdir(old_dir):
        raise FileNotFoundError(old_dir)
    if not os.path.isdir(new_dir):
        raise FileNotFoundError(new_dir)

    def codes_of(d: str) -> set[str]:
        return {f[: -len(".qfq.csv")] for f in os.listdir(d) if f.endswith(".qfq.csv")}

    old_codes, new_codes = codes_of(old_dir), codes_of(new_dir)
    ev_map = _new_segment_events(report_path, old_dir, new_dir)
    # 以旧集合为准逐只比；新多出的标的单独列（池子变更须人工审，不能混进 PASS）
    rows = [compare_one(c, old_dir, new_dir, ev_map.get(c)) for c in sorted(old_codes)]

    from collections import Counter
    status_counts = Counter(r["status"] for r in rows)

    # ---- 闸门 ----
    violations: list[dict] = []
    for r in rows:
        if r["status"] in (FAIL, "LOST_DAYS", "NEW_MISSING", "BAD_FIELD"):
            violations.append({"code": r["code"], "status": r["status"],
                               "detail": {k: r.get(k) for k in
                                          ("worst", "n_only_old", "lost_sample", "bad_field")}})

    extra = sorted(new_codes - old_codes)
    if extra:
        violations.append({"code": f"<{len(extra)} 只新标的>", "status": "EXTRA_SYMBOLS",
                           "detail": {"sample": extra[:10]}})

    # 新增交易日必须有限且一致：全池延长同样多的天数是健康的，
    # 若有股票延长 10 天、另一些延长 0 天，说明增量更新没覆盖全。
    ok_status = (BITWISE, STORAGE, REANCHORED)
    new_days = Counter(r["n_only_new"] for r in rows if r["status"] in ok_status)
    if allow_new_days is not None:
        for r in rows:
            if r["status"] in ok_status and r["n_only_new"] > allow_new_days:
                violations.append({"code": r["code"], "status": "TOO_MANY_NEW_DAYS",
                                   "detail": {"n_only_new": r["n_only_new"],
                                              "allow": allow_new_days}})

    return {
        "old_dir": old_dir,
        "new_dir": new_dir,
        "n_old_symbols": len(old_codes),
        "n_new_symbols": len(new_codes),
        "n_compared": len(rows),
        "status_counts": dict(status_counts),
        "store_ulp": STORE_ULP,
        "distinct_new_days": dict(new_days),
        "worst_overall": max((r.get("worst") or 0.0) for r in rows) if rows else 0.0,
        "total_common_rows": sum(r.get("n_common") or 0 for r in rows),
        "violations": violations,
        # 三种 PASS 必须分开报，不能合并成一个「通过」：
        #   BITWISE  —— 逐位相同，最干净
        #   ROUNDING —— 只有个别点落在 1 个末位内（存储精度）
        #   REANCHOR —— 有股票整体等比缩放（锚点移动），需读者知道价格水平变了
        "verdict": _verdict(rows, status_counts, violations),
        "rows": rows,
    }


def _verdict(rows: list[dict], status_counts, violations: list[dict]) -> str:
    if violations:
        return "BLOCKED_REBUILD_MISMATCH"
    if status_counts.get(BITWISE) == len(rows):
        return "PASS_BITWISE"
    if status_counts.get(REANCHORED):
        return "PASS_WITH_REANCHOR"
    return "PASS_WITH_ROUNDING"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="复权重建验收：新旧目录逐日逐位比对")
    ap.add_argument("--old", default="data/qfq_all")
    ap.add_argument("--new", default="data/qfq_all_next")
    ap.add_argument("--json", default="data/qfq_rebuild_verify.json")
    ap.add_argument("--allow-new-days", type=int, default=None,
                    help="单只最多允许新增多少交易日（不给则不设上限）")
    ap.add_argument("--report", default=None,
                    help="重建报告 JSON；用于佐证「新增区间确有除权事件」。不给则 REANCHORED 一律不认")
    a = ap.parse_args(argv)

    try:
        res = build(a.old, a.new, allow_new_days=a.allow_new_days, report_path=a.report)
    except FileNotFoundError as e:
        print(f"[abort] 目录不存在：{e}")
        return 2

    print(f"[verify] {res['n_old_symbols']} 旧 / {res['n_new_symbols']} 新 / 比对 {res['n_compared']}")
    print(f"[verify] 状态分布: {res['status_counts']}")
    print(f"[verify] 全池最大 |diff| = {res['worst_overall']:.3e}（存储 1 末位 {res['store_ulp']:.1e}）")
    print(f"[verify] 新增天数分布: {res['distinct_new_days']}")
    print(f"[verify] 重叠行合计: {res['total_common_rows']:,}")
    if res["violations"]:
        print(f"[verify] ❌ 违规 {len(res['violations'])} 条，前 5：")
        for v in res["violations"][:5]:
            print(f"        {v['code']}  {v['status']}  {v['detail']}")
    print(f"[verify] 判定: {res['verdict']}")

    with open(a.json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"[verify] -> {a.json}")
    return 0 if res["verdict"].startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
