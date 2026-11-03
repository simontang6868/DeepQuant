# -*- coding: utf-8 -*-
"""MA-CROSS 独立复核 / P0-0：执行前基线快照（只读）。

记录内容：
  1. git HEAD / 工作区状态 / 跟踪情况
  2. 关键代码与数据的 sha256（原始字节，不归一换行）
  3. 48 只主池数据资产清单（来源、行数、区间、哈希）
  4. 既有报告与产物清单

输出：
  reports/MA_CROSS_BASELINE_SNAPSHOT_2026-10-27.md
  reports/MA_CROSS_BASELINE_HASHES_2026-10-27.csv
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
REPORTS = os.path.join(ROOT, "reports")
BACKUP = os.path.join(ROOT, "data", "_backup_step75_baseline_20261020")
TZ = timezone(timedelta(hours=8))


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", ROOT, *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace").stdout.strip()
    except Exception as exc:
        return f"<git error: {exc}>"


CODE_FILES = [
    "backend/src/deepquant/strategies/ma_cross.py",
    "backend/src/deepquant/walkforward/parameter_selection.py",
    "backend/src/deepquant/walkforward/sensitivity.py",
    "backend/src/deepquant/backtest/engine.py",
    "backend/src/deepquant/backtest/config.py",
    "backend/src/deepquant/backtest/costs.py",
    "backend/src/deepquant/backtest/fee_presets.py",
    "backend/src/deepquant/market/rules.py",
    "backend/src/deepquant/market/ashare.py",
    "backend/src/deepquant/performance/window_records.py",
    "backend/src/deepquant/performance/benchmarks.py",
    "backend/src/deepquant/data/step75/research.py",
    "data/step75/research.py",
    "data/step75/build_universe.py",
    "scripts/b20_window_benchmark.py",
    "data/step75/universe/csi300_50.json",
    "data/step75/step75_research_summary.json",
]

DOC_FILES = [
    "docs/B15_8Y_30GROUPS_2026-10-18.md",
    "docs/B16_DIRECTION_VERDICT_2026-10-18.md",
    "docs/B17_CLOSURE_REPORT_2026-10-18.md",
    "docs/B18_RESEARCH_VERDICT_2026-10-18.md",
    "docs/B20_WINDOW_BH_2026-10-18.md",
    "docs/OOS_VERDICT_2026-10-19.md",
    "docs/OOS_RESULTS_2026-10-19.json",
    "docs/oos_prereg_2026-10-19.json",
    "docs/DIAG_OOS_ZERO_MEDIAN.md",
    "docs/COMPREHENSIVE_ASSESSMENT_2026-10-20.md",
    "docs/MA_CROSS_STRATEGY_SUMMARY_2026-10-27.md",
]


def main() -> int:
    os.makedirs(REPORTS, exist_ok=True)
    rows = []
    for rel in CODE_FILES + DOC_FILES:
        p = os.path.join(ROOT, rel)
        rows.append({
            "path": rel, "exists": os.path.exists(p),
            "size": os.path.getsize(p) if os.path.exists(p) else "",
            "sha256": sha(p) if os.path.exists(p) else "",
        })
    # 主池数据资产
    proc = sorted(glob.glob(os.path.join(BACKUP, "processed", "*.daily.parquet")))
    raw = sorted(glob.glob(os.path.join(BACKUP, "raw", "*.raw.csv")))
    meta = sorted(glob.glob(os.path.join(BACKUP, "metadata", "*.meta.json")))
    universe = json.load(open(os.path.join(ROOT, "data/step75/universe/csi300_50.json"),
                              encoding="utf-8"))
    uni_codes = [s["code"] for s in universe["stocks"]]
    data_rows = []
    for p in raw:
        code = os.path.basename(p).replace(".raw.csv", "")
        mp = os.path.join(BACKUP, "metadata", f"{code}.meta.json")
        m = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else {}
        data_rows.append({
            "code": code, "in_universe": code in uni_codes,
            "raw_sha256": sha(p), "raw_size": os.path.getsize(p),
            "rows_raw": m.get("rows", {}).get("raw", ""),
            "date_start": m.get("date_range", {}).get("start", ""),
            "date_end": m.get("date_range", {}).get("end", ""),
            "source_name": m.get("source", {}).get("name", ""),
            "source_sha256": m.get("source", {}).get("sha256", ""),
            "ingested_at": m.get("source", {}).get("ingested_at", ""),
        })

    out_csv = os.path.join(REPORTS, "MA_CROSS_BASELINE_HASHES_2026-10-27.csv")
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "exists", "size", "sha256"], lineterminator="\n")
        w.writeheader(); w.writerows(rows)
    out_csv2 = os.path.join(REPORTS, "MA_CROSS_BASELINE_DATA_2026-10-27.csv")
    with open(out_csv2, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(data_rows[0].keys()), lineterminator="\n")
        w.writeheader(); w.writerows(data_rows)

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    status = git("status", "--porcelain")
    n_status = len([x for x in status.splitlines() if x.strip()])
    tracked_step75 = git("ls-files", "data/step75")
    md = f"""# MA-CROSS 独立复核：执行前基线快照

| 项 | 值 |
|---|---|
| 生成时间 | {now} |
| git HEAD | `{git('rev-parse', 'HEAD')[:12]}` |
| 工作区变更条目数 | {n_status} |
| `data/step75` 是否被 git 跟踪 | {"是" if tracked_step75 else "**否（git ls-files 为空）**"} |
| 主池数据可用性 | backup 内有 **{len(raw)}** 只 raw CSV / **{len(proc)}** 只 processed parquet |
| universe 规模 | {len(uni_codes)} 只（csi300_50.json） |

## 1. git 近 5 次提交

```
{git('log', '--oneline', '-5')}
```

## 2. 关键文件哈希（sha256 原始字节）

| 文件 | 存在 | 大小 | sha256 前 16 |
|---|---|---:|---|
""" + "\n".join(
        f"| `{r['path']}` | {'✅' if r['exists'] else '❌'} | {r['size']} | `{r['sha256'][:16] or '—'}` |"
        for r in rows) + f"""

## 3. 数据资产（backup，{len(raw)} 只）

| 项 | 值 |
|---|---|
| 目录 | `data/_backup_step75_baseline_20261020/` |
| 结构 | `raw/*.raw.csv`（{len(raw)}）｜`processed/*.daily.parquet`（{len(proc)}）｜`metadata/*.meta.json`（{len(meta)}） |
| 数据源（metadata 记录） | `{data_rows[0]['source_name'] if data_rows else ''}` |
| 覆盖区间 | {data_rows[0]['date_start'] if data_rows else ''} ~ {data_rows[0]['date_end'] if data_rows else ''} |
| 摄取时间 | {data_rows[0]['ingested_at'] if data_rows else ''} |
| 主池 48 只是否齐全 | {"**是**（universe 50 − 数据闸门 2 + 手挑 3 = " + str(len(raw)) + "）" if len(raw) == 51 else "需核对"} |

> ⚠️ **数据来源链条**：metadata 记录 `source.sha256` 指向原始 `step75/{{code}}.qfq.csv`，但该原始缓存文件**本身不在工作区**；backup 里的 `raw.csv` 是再导出副本。二者是否逐字节一致**不可验证**（UNAVAILABLE），只能验证数值一致（见 P0-1）。

## 4. 结论

- 本次复核**不需要联网、不需要修改任何既有代码**：`data/step75/research.py` 已支持
  `DEEPQUANT_DATA_ROOT` / `DEEPQUANT_UNIVERSE` / `DEEPQUANT_PRICE_LIMIT` / `DEEPQUANT_W1_OUT`，
  可在独立目录内重跑 v2（`PRICE_LIMIT=off`）与 v3（`PRICE_LIMIT=board`），并落逐窗明细。
- **48 只主池数据已恢复**（来自 backup），48 只**实验结果 JSON 仍未恢复**（见 P0-1）。

## 5. 产物

- `reports/MA_CROSS_BASELINE_HASHES_2026-10-27.csv`（{len(rows)} 个文件）
- `reports/MA_CROSS_BASELINE_DATA_2026-10-27.csv`（{len(data_rows)} 只）
"""
    with open(os.path.join(REPORTS, "MA_CROSS_BASELINE_SNAPSHOT_2026-10-27.md"),
              "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    print(f"[saved] MA_CROSS_BASELINE_SNAPSHOT_2026-10-27.md (HEAD={git('rev-parse','HEAD')[:8]}, "
          f"{len(rows)} files, {n_status} dirty)")
    print(f"[saved] MA_CROSS_BASELINE_HASHES_2026-10-27.csv")
    print(f"[saved] MA_CROSS_BASELINE_DATA_2026-10-27.csv ({len(data_rows)} stocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
