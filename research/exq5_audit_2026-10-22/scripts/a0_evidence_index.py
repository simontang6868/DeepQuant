#!/usr/bin/env python
"""A0 冻结证据索引（任务 §阶段二）。

只读：对预登记文档、冻结产物、生产脚本、审计产物逐项计算 sha256，
校验「预登记哈希 = 运行目录名前缀」，输出 CSV/JSON 清单。

不修改任何文件；不读取 summary.json 的结论字段（只做哈希）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AUDIT = os.path.join(REPO, "research", "exq5_audit_2026-10-22")
OUT = os.path.join(AUDIT, "out", "a0_evidence")

# (phase, prereg path, expected prefix, product dir, production script, 产物文件名)
PHASES = [
    ("I", "docs/PHASE_I_EXQ5_PREREG_2026-10-21.md", "8f5c35121fdb",
     "phasei_out/8f5c35121fdb", "scripts/run_phase_i_exq5.py",
     ("monthly.csv", "summary.json", "manifest.json")),
    ("J", "docs/PHASE_J_IMPACT_PREREG_2026-10-21.md", "f76de8a277f6",
     "phasej_out/f76de8a277f6", "scripts/run_phase_j_impact.py",
     ("monthly.csv", "summary.json", "manifest.json")),
    ("O", "docs/PHASE_O_FEE_CONVENTION_PREREG_2026-10-21.md", "ac7aa2830ab0",
     "phaseo_out/ac7aa2830ab0", "scripts/run_phase_o_fee_convention.py",
     ("monthly.csv", "summary.json", "manifest.json")),
    ("S", "docs/PHASE_S_EXQ5_MODERN_PREREG_2026-10-22.md", "f098a59fb1b5",
     "phases_out/f098a59fb1b5", "scripts/run_phase_s_exq5_modern.py",
     ("monthly.csv", "summary.json", "manifest.json")),
    ("T", "docs/PHASE_T_EXQ5_ROBUSTNESS_PREREG_2026-10-22.md", "ef29a743997b",
     "phaset_out/ef29a743997b", "scripts/run_phase_t_exq5_robustness.py",
     ("monthly.csv", "summary.json", "manifest.json")),
    ("U", "docs/PHASE_U_NW_LAG_AUDIT_PREREG_2026-10-22.md", "b97d66d91989",
     "phaseu_out/b97d66d91989", "scripts/run_phase_u_nw_lag_audit.py",
     ("profiles.csv", "summary.json", "manifest.json")),
]

REPORT_DOCS = [
    "docs/PHASE_I_RESULT_REPORT_2026-10-21.md",
    "docs/PHASE_J_RESULT_REPORT_2026-10-21.md",
    "docs/PHASE_O_RESULT_REPORT_2026-10-21.md",
    "docs/PHASE_S_RESULT_REPORT_2026-10-22.md",
    "docs/PHASE_T_RESULT_REPORT_2026-10-22.md",
    "docs/PHASE_U_RESULT_REPORT_2026-10-22.md",
    "docs/PHASE_U_AUDIT_ADDENDUM_2026-10-22.md",
]

RECON_REPORTS = [
    "reports/BROKER_FEE_CALCULATION.json",
    "reports/EXQ5_FEASIBILITY.json",
    "reports/COST_SENSITIVITY_AUDIT.md",
    "reports/PROPERTY_CHECKS.json",
    "docs/ARCHIVE_MANIFEST_2026-10-21.md",
]

AUDIT_DOCS = [
    # 本索引文件自身不计入（自引用会让哈希永远滞后）；见 EXQ5_EVIDENCE_INDEX.md §8.3
    "EXQ5_AUDIT_FINAL_REPORT.md",
    "EXQ5_BASELINE_REPRODUCTION.md",
    "EXQ5_CAPACITY_AND_COST_AUDIT.md",
    "EXQ5_EXECUTION_ASSUMPTIONS_AUDIT.md",
    "EXQ5_FORWARD_VALIDATION_PROTOCOL.md",
    "EXQ5_STATISTICAL_ROBUSTNESS_AUDIT.md",
    "EXQ5_STOCK_AND_LIQUIDITY_AUDIT.md",
    "EXQ5_TIME_CONCENTRATION_AUDIT.md",
    "FWD_LEDGER_BUILD_NOTE.md",
    "EXQ5_LEDGER_RECONCILIATION.md",
    "VIBE_TRADING_REPO_ASSESSMENT.md",
]

AUDIT_SCRIPTS = [
    "scripts/audit_common.py",
    "scripts/a1_baseline_repro.py",
    "scripts/a2_time_concentration.py",
    "scripts/a3_panel_rebuild.py",
    "scripts/a4_capacity_cost.py",
    "scripts/a6_stat_robustness.py",
    "scripts/fwd_ledger.py",
    "scripts/a5_ledger_recon.py",
    "scripts/tests/test_audit.py",
    "scripts/tests/test_fwd_ledger.py",
]

AUDIT_OUTPUTS = [
    "out/a1_baseline/repro_table.csv",
    "out/a1_baseline/repro_detail.json",
    "out/a2_time/concentration.json",
    "out/a2_time/monthly_table.csv",
    "out/a2_time/rolling.csv",
    "out/a3_panel/summary.json",
    "out/a3_panel/adv_groups.csv",
    "out/a3_panel/per_stock_contrib.csv",
    "out/a4_capacity/summary.json",
    "out/a4_capacity/cost_factors.csv",
    "out/a6_stat/summary.json",
    "out/a6_stat/grid_full.csv",
    "out/a3_panel/keep_lists.csv",
    "out/a5_ledger/summary.json",
    "out/a5_ledger/monthly_BASE_drift.csv",
    "out/a5_ledger/monthly_BASE_rebalance.csv",
]

DATA_DIRS = [
    ("exec_all", "data/exec_all"),
    ("qfq_all", "data/qfq_all"),
]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sidecar(path: str):
    """读取 .sha256 旁注；兼容「hash + 两个空格 + 文件名」与「裸 hash」两种格式。"""
    if not os.path.isfile(path):
        return None
    txt = open(path, encoding="utf-8").read().strip()
    if not txt:
        return None
    return txt.split()[0].lower()


def git_state() -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                                  text=True, check=True).stdout.strip()
        except Exception:
            return ""

    return {
        "head": run("rev-parse", "HEAD"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "tracked_modified": run("diff", "--name-only"),
        "staged": run("diff", "--cached", "--name-only"),
    }


def dir_stats(path: str) -> dict:
    n, size, newest = 0, 0, ""
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            n += 1
            size += st.st_size
            if st.st_mtime > (os.stat(path).st_mtime if newest == "" else 0):
                newest = name
    return {"n_files": n, "bytes": size}


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    problems = []

    def add(cat, rel, note=""):
        ab = os.path.join(REPO, rel)
        if not os.path.isfile(ab):
            rows.append({"category": cat, "path": rel, "exists": False,
                         "bytes": 0, "sha256": "", "note": note})
            problems.append(f"缺失：{rel}")
            return None
        h = sha256_file(ab)
        rows.append({"category": cat, "path": rel, "exists": True,
                     "bytes": os.path.getsize(ab), "sha256": h, "note": note})
        return h

    # 1) 预登记 + 产物 + 生产脚本（并校验前缀一致性）
    phase_checks = []
    for ph, prereg, expect, prod_dir, script, products in PHASES:
        h = add("prereg", prereg, f"阶段 {ph}")
        # 旁注有两种命名：xxx.md.sha256 与 xxx.sha256（后者更常见）
        side = read_sidecar(os.path.join(REPO, prereg + ".sha256"))
        if side is None:
            side = read_sidecar(os.path.join(REPO, os.path.splitext(prereg)[0] + ".sha256"))
        ok_side = (side == h) if side else None
        if side and side != h:
            problems.append(f"预登记哈希不符：{prereg} 记录 {side[:12]} 实算 {h[:12]}")
        # 运行目录名前缀
        dir_name = os.path.basename(prod_dir)
        ok_prefix = h.startswith(dir_name) if h else False
        if not ok_prefix:
            problems.append(f"运行目录前缀不符：{prod_dir} vs 哈希 {h[:12]}")
        phase_checks.append({"phase": ph, "prereg": prereg, "sha256": h,
                             "sidecar": side, "sidecar_ok": ok_side,
                             "run_dir": dir_name, "prefix_ok": ok_prefix})
        for prod in products:
            add("product", os.path.join(prod_dir, prod), f"阶段 {ph}")
        add("production_script", script, f"阶段 {ph} 生成脚本")

    # 2) 结题报告与对账文件
    for rel in REPORT_DOCS:
        add("result_report", rel)
    for rel in RECON_REPORTS:
        add("recon_report", rel)

    # 3) 审计产物（自证）
    for rel in AUDIT_DOCS:
        add("audit_doc", os.path.join("research", "exq5_audit_2026-10-22", rel))
    for rel in AUDIT_SCRIPTS:
        add("audit_script", os.path.join("research", "exq5_audit_2026-10-22", rel))
    for rel in AUDIT_OUTPUTS:
        add("audit_output", os.path.join("research", "exq5_audit_2026-10-22", rel))

    # 4) 数据存档概况（不重算指纹，只记规模）
    data_stats = []
    for name, rel in DATA_DIRS:
        p = os.path.join(REPO, rel)
        if os.path.isdir(p):
            st = dir_stats(p)
            data_stats.append({"name": name, "path": rel, **st,
                               "fingerprint_recorded": {
                                   "exec_all": "b52ef0b3",
                                   "qfq_all": "53f43f39"}.get(name, "")})
        else:
            problems.append(f"缺失数据目录：{rel}")

    g = git_state()
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "repo": REPO,
        "git": g,
        "n_items": len(rows),
        "n_missing": sum(1 for r in rows if not r["exists"]),
        "problems": problems,
        "phase_checks": phase_checks,
        "data": data_stats,
    }

    with open(os.path.join(OUT, "evidence_index.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["category", "path", "exists", "bytes", "sha256", "note"])
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, "evidence_index.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "items": rows}, f, ensure_ascii=False, indent=1)

    print(f"A0 证据索引：{len(rows)} 项，缺失 {summary['n_missing']}，问题 {len(problems)}")
    print(f"  HEAD {g['head'][:12]}  分支 {g['branch']}  tracked_modified={bool(g['tracked_modified'])}")
    for pc in phase_checks:
        flag = "OK" if (pc["sidecar_ok"] in (True, None) and pc["prefix_ok"]) else "CHECK"
        print(f"  阶段 {pc['phase']}: 预登记 {pc['sha256'][:12]} 旁注 "
              f"{'一致' if pc['sidecar_ok'] else ('缺失' if pc['sidecar_ok'] is None else '不符')} "
              f"目录前缀 {'一致' if pc['prefix_ok'] else '不符'}  [{flag}]")
    for d in data_stats:
        print(f"  数据 {d['name']}: {d['n_files']} 文件 / {d['bytes']/1e9:.2f} GB "
              f"（记忆指纹 {d['fingerprint_recorded']}）")
    for p in problems:
        print(f"  ! {p}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
