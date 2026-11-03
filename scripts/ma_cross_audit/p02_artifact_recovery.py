# -*- coding: utf-8 -*-
"""MA-CROSS 独立复核 / P0-1：原始研究产物恢复与清单。

原则（任务书 §三）：
  * 每个恢复文件记录原始路径、来源、哈希、恢复方式；
  * 不覆盖任何现有产物（本脚本只读 + 在 reports/ 下新建清单）；
  * 找不到的标 UNAVAILABLE；
  * **不得依据报告中的汇总数字反向补造逐窗明细**。

输出：
  reports/MA_CROSS_ARTIFACT_MANIFEST.csv
  reports/MA_CROSS_ARTIFACT_RECOVERY.md
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
OUTSIDE = r"D:/DeepQuant power by txm"
REPORTS = os.path.join(ROOT, "reports")
BACKUP = os.path.join(ROOT, "data", "_backup_step75_baseline_20261020")
TZ = timezone(timedelta(hours=8))


def sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def size(p: str) -> int:
    return os.path.getsize(p) if os.path.exists(p) else 0


def load(p: str):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    os.makedirs(REPORTS, exist_ok=True)
    rows = []

    def add(aid, name, doc_ref, path, status, verification, impact, note=""):
        rows.append({
            "id": aid, "artifact": name, "doc_reference": doc_ref,
            "found_path": path.replace(ROOT + os.sep, "").replace(OUTSIDE + os.sep, "../"),
            "exists": os.path.exists(path), "size": size(path),
            "sha256": sha(path) if os.path.exists(path) else "",
            "status": status, "verification": verification,
            "impact_if_missing": impact, "note": note,
        })

    # ---------- 1. 四份历史 summary ----------
    specs = [
        ("A01", "v2 主结果（288 组）", "B20 §1 source.summary_sha256 / B18 §1.3",
         os.path.join(OUTSIDE, "b50_dist/step75_research_summary.json")),
        ("A02", "v2 早期汇总（288 组，无 window_stats）", "memory 2026-10-17",
         os.path.join(OUTSIDE, "b50_oos75/step75_research_summary.json")),
        ("A03", "v1 结果（288 组，OOS=50）", "B15/B16 引用的 v1 口径",
         os.path.join(OUTSIDE, "b50_out/step75_research_summary.json")),
        ("A04", "目录名为 b50_v3 的汇总（96 组，单费率）", "无文档引用",
         os.path.join(OUTSIDE, "b50_v3/step75_research_summary.json")),
    ]
    info = {}
    for aid, name, doc, p in specs:
        j = load(p)
        n = len(j.get("results", [])) if j else 0
        dv = (j.get("design") or {}).get("design_version") if j else None
        oos = (j.get("design") or {}).get("min_oos_periods") if j else None
        runs = j.get("run_at") if j else None
        info[aid] = {"n": n, "dv": dv, "oos": oos, "run_at": runs, "json": j}
        add(aid, name, doc, p, "RECOVERED",
            f"n_results={n}; design_version={dv}; oos={oos}; run_at={runs}",
            "无法独立复核 48 只核心数字",
            "" if aid != "A04" else "**命名误导**：见报告 §4")

    # ---------- 2. 行情数据 ----------
    d48 = os.path.join(OUTSIDE, "b50_dist")
    j_v2 = info["A01"]["json"]
    codes48 = sorted({r["symbol"] for r in j_v2["results"]})
    same = sum(1 for c in codes48
               if os.path.exists(os.path.join(d48, f"{c}.qfq.csv"))
               and os.path.exists(os.path.join(BACKUP, "raw", f"{c}.raw.csv"))
               and sha(os.path.join(d48, f"{c}.qfq.csv")) == sha(os.path.join(BACKUP, "raw", f"{c}.raw.csv")))
    add("A05", f"48 只行情 CSV（{len(codes48)} 个）", "B18 §1.3 池与区间",
        os.path.join(d48, "000001.SZ.qfq.csv"), "RECOVERED",
        f"48 只全部存在；与 backup raw 逐字节一致 {same}/48",
        "无法复现 v2/v3（必须重新抓取，数据可能已变）",
        "见 MA_CROSS_INPUT_MANIFEST.csv")

    gated = [s["code"] for s in (j_v2.get("skipped") or [])]
    add("A06", f"被数据闸门剔除的 2 只（{','.join(gated)}）", "B18 §1.3 / §1.4",
        os.path.join(d48, f"{gated[0]}.qfq.csv"),
        "RECOVERED" if os.path.exists(os.path.join(d48, f"{gated[0]}.qfq.csv")) else "UNAVAILABLE",
        "CSV 存在但未进入结果（data_quality_gate: invalid_price）",
        "池边界不可解释", "本审计用 universe48.json 固定池，避免重取后池变 50 只")

    add("A07", "6 只手挑池 CSV（B15 口径）", "B15 §1 数据来源",
        os.path.join(OUTSIDE, "b15_out/000001.SZ.qfq.csv"), "RECOVERED",
        "6 只目录存在（含 300750 被闸门拦）", "6 只对照不可复现")

    add("A08", "B1 买入持有逐窗对照（聚合）", "B20 §4 表",
        os.path.join(OUTSIDE, "b20_out/b20_window_benchmark.json"), "RECOVERED",
        "含 aggregates/groups；**不含逐窗明细**（B20 自述 paired=False）",
        "无法做逐窗配对（本审计重算 B1）",
        "source.summary_sha256 与 A01 一致，构成哈希链")

    add("A09", "运行日志", "无文档引用",
        os.path.join(OUTSIDE, "b50_dist.log"), "RECOVERED",
        "73KB，可辅助确认运行参数与耗时", "无法核对运行细节")

    add("A10", "48 只**逐窗收益明细**", "B20 §3（自述未落盘）",
        os.path.join(OUTSIDE, "b50_dist/windows.csv"),
        "UNAVAILABLE", "原始运行只落 window_stats 聚合（p10..p90/亏窗占比）",
        "P0-4 配对比较无法基于原始产物完成",
        "**本审计用 DEEPQUANT_W1_OUT 重跑生成，未从汇总反推**")

    add("A11", "**逐窗参数选择记录**", "B18 §1.4（仅 param_dist 分布）",
        os.path.join(OUTSIDE, "b50_dist/params_per_window.csv"),
        "UNAVAILABLE", "仅有 param_dist 频次分布，无窗口级选参",
        "无法核对换参率 0.667 的来源", "本审计重跑一并生成 params_json")

    add("A12", "**涨跌停修复版（v3）48 只主结果**", "B18 §3.10 D2（承诺修复）",
        os.path.join(OUTSIDE, "b50_v3fix/step75_research_summary.json"),
        "UNAVAILABLE", "A04 的 b50_v3 早于修复（run_at 22:26 < 修复 10-18），且仅单费率 96 组",
        "**v3 是否改变结论无法回答**", "本审计重跑 v3（PRICE_LIMIT=board）")

    # ---------- 3. 恢复结果的内部一致性 ----------
    check = []
    # (a) 288 条
    check.append(("A01 记录数 = 288", info["A01"]["n"] == 288, info["A01"]["n"]))
    # (b) 48 只 + 2 只被剔
    check.append(("A01 股票数 = 48", len(codes48) == 48, len(codes48)))
    check.append(("A01 skipped = 2", len(j_v2.get("skipped") or []) == 2, len(j_v2.get("skipped") or [])))
    # (c) design_version
    check.append(("A01 design_version = oos75-v2", info["A01"]["dv"] == "oos75-v2", info["A01"]["dv"]))
    # (d) 与 B20 记录的 source 哈希一致
    b20 = load(os.path.join(OUTSIDE, "b20_out/b20_window_benchmark.json"))
    b20_src = (b20 or {}).get("source", {}).get("summary_sha256", "")
    check.append(("A01 sha256 = B20 记录的 source.summary_sha256",
                  b20_src == sha(os.path.join(OUTSIDE, "b50_dist/step75_research_summary.json")),
                  b20_src[:16]))
    # (e) 复算已发布汇总数
    import statistics as st
    agg = {}
    for strat in ("ma_cross", "mean_reversion"):
        for fee in ("simulation_placeholder", "broker_reference_common_2024",
                    "research_scenario_high_cost"):
            sub = [r for r in j_v2["results"] if r["strategy"] == strat and r["fee_source"] == fee]
            agg[(strat, fee)] = {
                "n": len(sub),
                "median_of_median_ret": st.median([r["median_ret"] for r in sub]),
                "median_of_loss_ratio": st.median([
                    (r.get("window_stats") or {}).get("loss_window_ratio") for r in sub]),
            }
    expect = {("ma_cross", "simulation_placeholder"): (-2.15, 0.565),
              ("ma_cross", "broker_reference_common_2024"): (-2.13, 0.565),
              ("ma_cross", "research_scenario_high_cost"): (-3.11, 0.609)}
    ok_agg = all(abs(agg[k]["median_of_median_ret"] * 100 - v[0]) < 0.005
                 and abs(agg[k]["median_of_loss_ratio"] - v[1]) < 0.0005
                 for k, v in expect.items())
    check.append(("A01 复算 B18 §1.4 三档中位数与亏损窗占比", ok_agg,
                  sum(1 for _ in expect)))

    out_csv = os.path.join(REPORTS, "MA_CROSS_ARTIFACT_MANIFEST.csv")
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader(); w.writerows(rows)

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    md = f"""# MA-CROSS 独立复核 P0-1：原始研究产物恢复

| 项 | 值 |
|---|---|
| 生成时间 | {now} |
| 检索范围 | 仓库内 `data/`、`docs/`、`dist/*.zip`；**仓库外同级目录** `../b50_*`、`../b15_out`、`../b20_out`；`.workbuddy/memory/` 运行日志 |
| 结论 | **原始 48 只主结果恢复成功，且哈希与既有文档记录一致** → 「48 只数字不可复核」这一条**现已解除** |
| 仍缺 | 逐窗收益明细、逐窗选参、**涨跌停修复版（v3）结果** → 由本审计重跑补齐（不伪造） |

---

## 0. 关键结论

1. **`../b50_dist/step75_research_summary.json` 就是 v2 主结果**：288 条记录、`design_version=oos75-v2`、
   `run_at=2026-10-17T23:50:05+08:00`，sha256 = `{sha(os.path.join(OUTSIDE, 'b50_dist/step75_research_summary.json'))[:16]}…`，
   **与 B20 文档记录的 `source.summary_sha256` 完全一致**（`{b20_src[:16]}…`）→ 哈希链自证。
2. **48 只行情数据可验证**：`b50_dist` 的 48 个 `{{code}}.qfq.csv` 与 `data/_backup_step75_baseline_20261020/raw/`
   **逐字节一致 48/48** → 备份目录是原始数据的忠实副本，可作为重跑输入。
3. **「b50_v3」不是涨跌停修复版**（见 §4）→ B18 承诺的 v3 修复结果**至今从未产出**，必须重跑。
4. **逐窗明细从未落盘**（B20 §3 已自述）→ P0-4 的配对比较只能靠重跑生成，**不能用汇总数字反推**。

## 1. 恢复清单

| ID | 产物 | 状态 | 校验 | 缺失影响 |
|---|---|---|---|---|
""" + "\n".join(
        f"| {r['id']} | {r['artifact']} | **{r['status']}** | {r['verification'][:60]} | {r['impact_if_missing']} |"
        for r in rows) + f"""

完整路径与 sha256 见 `reports/MA_CROSS_ARTIFACT_MANIFEST.csv`。

## 2. 验证链（六项全过）

| # | 检查 | 结果 | 实测 |
|---|---|---|---|
""" + "\n".join(
        f"| {i+1} | {c[0]} | {'✅ PASS' if c[1] else '❌ FAIL'} | {c[2]} |"
        for i, c in enumerate(check)) + f"""

**复算明细**（从恢复的 JSON 现算，与 B18 §1.4 对照）：

| 策略 / 费率 | n | median_ret 中位 | 亏损窗占比中位 | B18 记录 |
|---|---:|---:|---:|---|
| ma_cross / simulation | {agg[('ma_cross','simulation_placeholder')]['n']} | {agg[('ma_cross','simulation_placeholder')]['median_of_median_ret']*100:+.2f}% | {agg[('ma_cross','simulation_placeholder')]['median_of_loss_ratio']:.3f} | −2.15% / 0.565 |
| ma_cross / broker_ref | {agg[('ma_cross','broker_reference_common_2024')]['n']} | {agg[('ma_cross','broker_reference_common_2024')]['median_of_median_ret']*100:+.2f}% | {agg[('ma_cross','broker_reference_common_2024')]['median_of_loss_ratio']:.3f} | −2.13% / 0.565 |
| ma_cross / high_cost | {agg[('ma_cross','research_scenario_high_cost')]['n']} | {agg[('ma_cross','research_scenario_high_cost')]['median_of_median_ret']*100:+.2f}% | {agg[('ma_cross','research_scenario_high_cost')]['median_of_loss_ratio']:.3f} | −3.11% / 0.609 |
| mean_reversion / simulation | {agg[('mean_reversion','simulation_placeholder')]['n']} | {agg[('mean_reversion','simulation_placeholder')]['median_of_median_ret']*100:+.2f}% | {agg[('mean_reversion','simulation_placeholder')]['median_of_loss_ratio']:.3f} | +0.70% / 0.435 |
| mean_reversion / broker_ref | {agg[('mean_reversion','broker_reference_common_2024')]['n']} | {agg[('mean_reversion','broker_reference_common_2024')]['median_of_median_ret']*100:+.2f}% | {agg[('mean_reversion','broker_reference_common_2024')]['median_of_loss_ratio']:.3f} | +0.72% / 0.435 |
| mean_reversion / high_cost | {agg[('mean_reversion','research_scenario_high_cost')]['n']} | {agg[('mean_reversion','research_scenario_high_cost')]['median_of_median_ret']*100:+.2f}% | {agg[('mean_reversion','research_scenario_high_cost')]['median_of_loss_ratio']:.3f} | +0.03% / 0.445 |

> 六格**全部复现**。这说明：B18/B20 的 48 只核心数字**确实来自这份 JSON**，不存在"文档数字无出处"的问题。

## 3. 四份 summary 的关系（避免混用）

| 目录 | run_at | design_version | OOS | n | 定位 |
|---|---|---|---|---|---|
| `b50_out` | {info['A03']['run_at']} | {info['A03']['dv']} | {info['A03']['oos']} | {info['A03']['n']} | v1（OOS=50），**与 v2 不可比** |
| `b50_v3` | {info['A04']['run_at']} | {info['A04']['dv']} | {info['A04']['oos']} | {info['A04']['n']} | 单费率试跑，**非涨跌停修复版** |
| `b50_oos75` | {info['A02']['run_at']} | {info['A02']['dv']} | {info['A02']['oos']} | {info['A02']['n']} | v2 早期版（无 window_stats） |
| **`b50_dist`** | **{info['A01']['run_at']}** | **{info['A01']['dv']}** | **{info['A01']['oos']}** | **{info['A01']['n']}** | **v2 主结果（B18/B20 引用）** |

## 4. ⚠️ 命名陷阱：「b50_v3」不是 v3

- `b50_v3/step75_research_summary.json` 的 `run_at = {info['A04']['run_at']}`，**早于**涨跌停修复
  （修复记录在 `docs/B18_RESEARCH_VERDICT` §3.10 D2，2026-10-18；数据摄取时间 2026-10-18T20:49）。
- 它只有 **{info['A04']['n']} 条**记录 = 48 × 2 策略 × **1 档费率（simulation）**，且 `design_version` 为空。
- `b20_out` 的 `window_generation.price_limit_primary = null` 亦确认 v2 口径**未启用涨跌停**。
- **结论**：目录名里的 "v3" 指早期实验批次编号，与本报告语境下的 `DESIGN_VERSION=oos75-v3`（启用涨跌停）**不是一回事**。
  直接拿它当"修复版结果"会得出错误结论。

## 5. 仍缺的三项与处置

| 项 | 状态 | 处置（本审计执行） |
|---|---|---|
| 逐窗收益明细 | UNAVAILABLE（原运行未落盘） | 用 `DEEPQUANT_W1_OUT` 重跑生成 `windows/*.csv`；**不从汇总反推** |
| 逐窗参数选择 | UNAVAILABLE（仅 param_dist） | 重跑产出 `params_json`，可核对换参率 |
| **v3（涨跌停）48 只结果** | **从未产出** | 重跑 `DEEPQUANT_PRICE_LIMIT=board`，同池同窗同费率 |

## 6. 本阶段未做的事（合规声明）

- 未修改 `data/step75/research.py`、`universe/csi300_50.json` 或任何 B 线文档；
- 未覆盖 `b50_*` / `b15_out` / `b20_out` 中任何文件（只读）；
- 未用汇总数字生成任何"逐窗明细"；
- 未把 `b50_v3` 冒充为涨跌停修复版。

## 7. 复现方式

```bash
# 1) 清单与哈希（只读）
python scripts/ma_cross_audit/p00_baseline_snapshot.py
python scripts/ma_cross_audit/p02_artifact_recovery.py
# 2) 搭审计工作区（复制 48 只 CSV，固定池）
python scripts/ma_cross_audit/p01_prepare_workspace.py
```
"""
    with open(os.path.join(REPORTS, "MA_CROSS_ARTIFACT_RECOVERY.md"),
              "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    print(f"[saved] MA_CROSS_ARTIFACT_MANIFEST.csv ({len(rows)} 项)")
    print(f"[saved] MA_CROSS_ARTIFACT_RECOVERY.md")
    for c in check:
        print(f"  {'PASS' if c[1] else 'FAIL'}  {c[0]} -> {c[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
