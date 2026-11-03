# -*- coding: utf-8 -*-
"""MA-CROSS 独立复核 / P0-1(b)：搭建审计工作区（只读复制，不动任何既有产物）。

做四件事：
  1. 从**恢复的原版** `../b50_dist/step75_research_summary.json` 读出实际进入结果的 48 只代码
     与被数据闸门拦下的 2 只（用于证明池子边界）；
  2. 生成 `universe48.json`（**把池固定为这 48 只**，避免 akshare 重取导致池变化，
     从而让"复现"与"换池"两件事分开）；
  3. 把 b50_dist 里 48 个 `{code}.qfq.csv` 复制到 data_v2 / data_v3 两个独立目录
     （research.py 会把 summary 写进 DATA_ROOT，两个目录避免互相覆盖）；
  4. 产出 `MA_CROSS_INPUT_MANIFEST.csv`（每个输入文件的 sha256 / 行数 / 区间）。

不修改：research.py、universe/csi300_50.json、data/step75/ 下任何文件。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
OUTSIDE = r"D:/DeepQuant power by txm"          # b50_* 所在（仓库外）
AUDIT = os.path.join(ROOT, "ma_cross_audit")
REPORTS = os.path.join(ROOT, "reports")
UNIVERSE_SRC = os.path.join(ROOT, "data/step75/universe/csi300_50.json")
SRC_SUMMARY = os.path.join(OUTSIDE, "b50_dist/step75_research_summary.json")


def sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main() -> int:
    j = json.load(open(SRC_SUMMARY, encoding="utf-8"))
    codes = sorted({r["symbol"] for r in j["results"]})
    skipped = [s["code"] for s in (j.get("skipped") or [])]
    assert len(codes) == 48, f"期望 48 只，实际 {len(codes)}"

    uni = json.load(open(UNIVERSE_SRC, encoding="utf-8"))
    by_code = {s["code"]: s for s in uni["stocks"]}
    missing = [c for c in codes if c not in by_code]
    assert not missing, f"universe 缺少 {missing}"

    os.makedirs(AUDIT, exist_ok=True)
    uni48 = {
        "meta": {
            "purpose": "MA-CROSS 独立复核：把池固定为 b50_dist 实际进入结果的 48 只",
            "derived_from": "data/step75/universe/csi300_50.json",
            "derived_from_sha256": sha(UNIVERSE_SRC),
            "evidence_summary": "b50_dist/step75_research_summary.json",
            "evidence_summary_sha256": sha(SRC_SUMMARY),
            "n_results_symbols": len(codes),
            "gated_out": skipped,
            "gated_out_reason": "data_quality_gate: invalid_price（原始运行记录）",
            "note": ("固定 48 只是为了让「复现 v2」与「更换股票池」两件事分离；"
                     "若放开，research.py 会重新抓取被闸门拦下的 2 只，池子可能变成 50 只，"
                     "复现就失去意义。"),
            "original_meta": uni.get("meta", {}),
        },
        "stocks": [by_code[c] for c in codes],
    }
    p_uni = os.path.join(AUDIT, "universe48.json")
    with open(p_uni, "w", encoding="utf-8", newline="\n") as f:
        json.dump(uni48, f, ensure_ascii=False, indent=2)

    manifest = []
    for tag in ("data_v2", "data_v3"):
        d = os.path.join(AUDIT, tag)
        os.makedirs(d, exist_ok=True)
        for c in codes:
            src = os.path.join(OUTSIDE, "b50_dist", f"{c}.qfq.csv")
            dst = os.path.join(d, f"{c}.qfq.csv")
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
            manifest.append({
                "tag": tag, "code": c, "src": f"b50_dist/{c}.qfq.csv",
                "sha256": sha(dst), "size": os.path.getsize(dst),
                "src_sha256": sha(src),
            })

    mp = os.path.join(REPORTS, "MA_CROSS_INPUT_MANIFEST.csv")
    with open(mp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys()), lineterminator="\n")
        w.writeheader(); w.writerows(manifest)

    same = sum(1 for m in manifest if m["sha256"] == m["src_sha256"])
    print(f"[ok] universe48.json → {len(codes)} 只（闸门剔除 {skipped}）")
    print(f"[ok] 数据复制 {len(manifest)} 份（v2/v3 各 {len(codes)}），与源一致 {same}/{len(manifest)}")
    print(f"[ok] MA_CROSS_INPUT_MANIFEST.csv")
    print(f"[paths] AUDIT={AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
