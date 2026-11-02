"""SP-5 产物哈希清单（替代 git commit 定位版本，与 SP-4 的 MANIFEST 同思路）"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
FILES = [
    # 文档（冻结件）
    "05_SP5_任务书.md",
    "06_P2-1_前瞻评估方案_冻结v1.0.md",
    "SP5_预登记.md",
    "04_最终方案.md",
    "reports/STAGE_REPORT_SP4_INDEPENDENT_AUDIT.md",
    "reports/SP5_PROGRESS.md",
    "reports/SP5_TRADABILITY_CONSTRAINT_AUDIT.md",
    "reports/SP5_ST_EFFECT_AUDIT.md",
    "reports/SP5_POSITION_DD_ROBUSTNESS.md",
    "reports/SP5_EXECUTABLE_WINDOW_AUDIT.md",
    "reports/SP5_INDUSTRY_EXPOSURE_AUDIT.md",
    # 代码
    "scripts/sp5/st_sz_build.py",
    "scripts/sp5/st_exposure_probe.py",
    "scripts/sp5/wording_audit.py",
    "scripts/sp5/fill_engine.py",
    "scripts/sp5/p03_tradability.py",
    "scripts/sp5/p03_diag.py",
    "scripts/sp5/p02c_st_effect.py",
    "scripts/sp5/p04_position_dd.py",
    "scripts/sp5/p11_exec_window.py",
    "scripts/sp5/p12_industry_map.py",
    "scripts/sp5/p12_industry.py",
    "scripts/sp5/tests/test_p11_exec_window.py",
    "scripts/sp5/tests/test_p04_ledger.py",
    "scripts/sp5/tests/test_fill_engine.py",
    "scripts/sp5/tests/test_sp5.py",
    # 数据与结果
    "data/st_sz_monthly.parquet",
    "sp5_out/sp5_st_coverage.json",
    "sp5_out/sp5_st_exposure.json",
    "sp5_out/wording_audit.csv",
    "sp5_out/wording_audit.json",
    "sp5_out/SP5_TASK_BACKLOG.csv",
    "sp5_out/sp5_tradability.json",
    "sp5_out/sp5_fill_ledger.csv",
    "sp5_out/sp5_p03_diag.json",
    "sp5_out/sp5_st_effect.json",
    "sp5_out/prereg_hash_history.json",
    "sp5_out/sp5_position_dd.json",
    "sp5_out/sp5_nav_recon_p0_2005.csv",
    "sp5_out/sp5_nav_recon_p0_2021.csv",
    "sp5_out/sp5_exec_window.json",
    "sp5_out/sp5_exec_monthly.csv",
    "sp5_out/sp5_industry.json",
]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    man = {}
    for rel in FILES:
        p = BASE / rel
        if not p.exists():
            man[rel] = {"exists": False}
            continue
        man[rel] = {"exists": True, "bytes": p.stat().st_size, "sha256": sha(p)}
    out = BASE / "sp5_out" / "MANIFEST_SP5.json"
    out.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for k, v in man.items():
        print(f"{'OK ' if v['exists'] else 'MISS'} {v.get('sha256', '-')[:16]}  {k}")

    # 冻结件单独存 .sha256（与项目既有 sp4_prereg.sha256 同格式）
    for rel, tag in [("SP5_预登记.md", "sp5_prereg.sha256"), ("05_SP5_任务书.md", "sp5_taskbook.sha256")]:
        p = BASE / rel
        (BASE / tag).write_text(f"{sha(p)}  {rel}\n", encoding="utf-8")
        print("frozen ->", tag)


if __name__ == "__main__":
    main()
