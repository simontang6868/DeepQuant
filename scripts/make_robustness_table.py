#!/usr/bin/env python
"""生成 Gate 1~6 + 阶段 C/C-2/C-3 的稳健性总表（机器可读 + Markdown 表格）。

## 为什么要有这个脚本

"每个 Gate 一个 md" 的问题是：**跨实验的口径差异没人管**。
本项目已经因此出过至少两次错：
- 拿 `d`（自融资对半）直接与文献 `spread`（各投全额）比，差 2 倍；
- 拿含 B 股、无 252 日门槛的 Gate 3 与全 A as-of 的 Gate 6 并列比较。

所以总表**只做三件事**：
1. 把各实验的 `main` 从各自的 `summary.json` 原样取出，**统一单位到 pp**；
2. 明确标出**检验口径**（单侧/双侧）、**MDE**、**判定**、**成熟度**；
3. 把敏感性分析的结论并列，让"哪些数字被动过、动了多少"一眼可见。

## 不做的事

- **不重算任何绩效指标**。`mean_d` / `t` 一律取自产物；只有 `mde_pp` 在产物缺失时按
  `2.8016 × se` 重算并标注 `recomputed`。
- **不合并、不加权、不做元分析**。各实验池/窗口/构造都不同，合并是无意义的。

用法：
    python scripts/make_robustness_table.py
"""

from __future__ import annotations

import hashlib
import json
import math
import os

Z_MDE = 2.8016          # 80% power, 双侧 α=0.05

# 各实验的元信息（人工维护，来源见 docs/）
# test: 单侧 or 双侧 —— 这个必须标，因为单侧 1.645 与双侧 1.96 不可直接比
GATES = [
    dict(tag="Gate 1", dir="gate1_out/032a11f591553e25",
         topic="月频动量 60/5", pool="52→50 只", window="OOS 2021-01~2024-12",
         construct="k=10", test="单侧", maturity="M0",
         verdict_new="INCONCLUSIVE", verdict_note="功效不足：MDE 远大于目标效应"),
    dict(tag="Gate 2", dir="gate2_out/c440c77d284a",
         topic="反转 21/1", pool="548 只", window="2016-05~2026-10",
         construct="固定 k", test="单侧", maturity="M0",
         verdict_new="FAIL", verdict_note="有效检验下方向为负且未达门槛"),
    dict(tag="Gate 3", dir="gate3_out/9b7235d77689",
         topic="反转 21/1（长样本）", pool="532 只（含 14 只 B 股）", window="2007-01~2015-12",
         construct="k=110", test="单侧", maturity="M0",
         verdict_new="PASS", verdict_note="0.1% 成本后 t=1.24 不显著 → 非 M1"),
    dict(tag="Gate 4", dir="gate4_out/94988f7c57d1",
         topic="低波动", pool="全 A", window="2007-01~2026-09",
         construct="比例 k", test="单侧", maturity="M0",
         verdict_new="FAIL", verdict_note="MDE 0.537pp 对 1.08pp 看得见"),
    dict(tag="Gate 5", dir="gate5_out/7c0c89916b0c",
         topic="交易活跃度（双侧）", pool="全 A", window="2006-01~2026-09",
         construct="比例 k", test="双侧", maturity="M0",
         verdict_new="FAIL", verdict_note="MDE 0.426pp，双侧，t=0.155"),
    dict(tag="Gate 6", dir="gate6_out/b8c4f1ecba44",
         topic="反转 as-of 全 A", pool="as-of 全 A（5,444）", window="2007-01~2026-09",
         construct="比例 20%", test="双侧", maturity="M1",
         verdict_new="PASS", verdict_note="0.1% 后 t=3.39 显著；0.3% 后 t=1.40 不显著"),
]

PHASES = [
    dict(tag="阶段 C", dir="phasec_out/3a01759a37f0",
         topic="池效应 vs 构造 vs 门槛", pool="五格", window="2007-01~2026-09",
         construct="五格交叉", test="双侧（NW lag3）", maturity="M1",
         verdict_new="POOL_EFFECT_CONFIRMED",
         verdict_note="Δ=spread(B)−spread(A)，主检验唯一"),
    dict(tag="阶段 C-2", dir="phasec2_out/953c32fdc556",
         topic="不可成交缺口", pool="全 A（D 格）", window="2007-01~2026-09",
         construct="V0→V1 剔停牌/退市", test="双侧（NW lag3）", maturity="M1",
         verdict_new="SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT",
         verdict_note="+0.1069pp，为改善方向"),
    dict(tag="阶段 C-3", dir="phasec3_out/6a76a10687b4",
         topic="成交延迟代价", pool="全 A（D 格）", window="2007-01~2026-09",
         construct="V0→V1 pos+1 成交", test="双侧（NW lag3）", maturity="M1",
         verdict_new="LAG_COST_NEGLIGIBLE",
         verdict_note="+0.0468pp，不显著"),
]

# 敏感性分析（各自独立的 json，结构不同，单独取）
SENS = [
    dict(tag="Gate 3 剔 B 股", file="docs/GATE3_SENS_BSHARE.json",
         desc="532 池混入 14 只 B 股，剔除后 mean 变化"),
    dict(tag="Gate 6 复牌首日", file="docs/GATE6_SENS_RESUME.json",
         desc="复牌首日收益置 0，看 d 变化"),
    dict(tag="涨跌停阻塞率", file="docs/LIMIT_BLOCK_RATE_2026-10-21.json",
         desc="建仓/平仓日不可成交的比例"),
]


def load_main(path: str) -> dict:
    """取主检验统计量。**兼容两种产物结构**：

    - Gate 1~6：`main` 是水平值（mean(d)、spread=2·mean(d)）
    - 阶段 C 系列：`main_test` 是**配对差** Δ = spread(V1)−spread(V0)，`mean_pp` 已经是 pp

    为了表格列语义一致（spread 列 = 2 × mean 列），阶段行把 Δ 拆成 Δ/2 与 Δ。
    `is_paired` 标记用来在表里提醒"这不是水平值"。
    """
    d = json.load(open(path, encoding="utf-8"))
    if "main_test" in d:
        mt = d["main_test"]
        delta_pp = mt.get("mean_pp")
        return {
            "n": mt.get("n"),
            "mean_d_pp": (delta_pp / 2) if delta_pp is not None else None,
            "spread_pp": delta_pp,
            "t": mt.get("t_nw"),
            "sd_d_pp": None,
            "se_pp": mt.get("se_nw_pp"),
            "mde_pp": None,
            "mde_recomputed": False,
            "win_ratio": None,
            "old_verdict": (mt.get("judgement") or {}).get("verdict"),
            "prereg_version": d.get("experiment"),
            "prereg_sha256": d.get("prereg_sha256"),
            "window_in_file": (d.get("window") or {}).get("start"),
            "is_paired": True,
        }

    m = d.get("main") or {}
    n = m.get("n")
    sd = m.get("sd_d", m.get("std_d"))
    se = m.get("se")
    if se is None and sd and n:
        se = sd / math.sqrt(n)
    mde = m.get("mde_pp")
    recomputed = False
    if mde is None and se is not None:
        mde = Z_MDE * se
        recomputed = True
    mean = m.get("mean_d")
    return {
        "n": n,
        "mean_d_pp": (mean * 100) if mean is not None else None,
        "spread_pp": (2 * mean * 100) if mean is not None else None,
        "t": m.get("t_stat"),
        "sd_d_pp": (sd * 100) if sd else None,
        "se_pp": (se * 100) if se else None,
        "mde_pp": (mde * 100) if mde is not None else None,
        "mde_recomputed": recomputed,
        "win_ratio": m.get("win_ratio"),
        "old_verdict": (d.get("judgement") or {}).get("verdict"),
        "prereg_version": d.get("prereg_version"),
        "prereg_sha256": d.get("prereg_sha256") or d.get("prereg_sha256_prefix"),
        "window_in_file": d.get("window") or d.get("oos_window"),
        "is_paired": False,
    }


def fmt(v, nd=4, sign=True):
    if v is None:
        return "—"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def main() -> int:
    rows = []
    for g in GATES + PHASES:
        p = os.path.join(g["dir"], "summary.json")
        if not os.path.exists(p):
            print(f"[跳过] {p} 不存在")
            continue
        m = load_main(p)
        rows.append({**g, **m, "source": p})

    os.makedirs("reports", exist_ok=True)
    payload = {
        "generated_by": "scripts/make_robustness_table.py",
        "z_mde": Z_MDE,
        "note": ("mean_d / t 一律取自各产物 summary.json，本脚本不重算；"
                 "mde_pp 在产物缺失时按 2.8016×se 重算并标 mde_recomputed=true。"
                 "不同实验的池/窗口/构造/检验口径不同，**不可跨行相减或合并**。"),
        "rows": rows,
        "sensitivity_files": [s["file"] for s in SENS if os.path.exists(s["file"])],
    }
    jp = "reports/ROBUSTNESS_TABLE.json"
    with open(jp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")

    # ---- Markdown 表格
    lines = []
    lines.append("# 稳健性总表（机械生成，勿手改）")
    lines.append("")
    lines.append(f"> 由 `scripts/make_robustness_table.py` 生成。`mean_d`/`t` 取自各产物 `summary.json`，")
    lines.append(f"> 不改写、不重算。`mde_pp` 缺失时按 `{Z_MDE} × se` 重算并标 †。")
    lines.append(f"> **不同行的池/窗口/构造/检验口径不同，不可跨行相减或合并。**")
    lines.append(">")
    lines.append("> ⚠️ 标 `Δ` 的行（阶段 C 系列）是**配对差**（如 `spread(B)−spread(A)`），"
                 "不是水平值；")
    lines.append("> 为保持列语义一致，`mean(d)` 列放 Δ/2、`spread` 列放 Δ。**不要把它们与 Gate 的水平值相减。**")
    lines.append("")
    lines.append("| 实验 | 主题 | 池 | 窗口 | 构造 | n | mean(d) pp | spread pp | t | 检验 | MDE pp | "
                 "原判定 | 新判定 | 成熟度 |")
    lines.append("|---|---|---|---|---|---:|---:|---:|---:|---|---:|---|---|---|")
    for r in rows:
        mde = fmt(r["mde_pp"], 3, sign=False) + ("†" if r.get("mde_recomputed") else "")
        tag = r["tag"] + ("  Δ" if r.get("is_paired") else "")
        lines.append(
            f"| {tag} | {r['topic']} | {r['pool']} | {r['window']} | {r['construct']} | "
            f"{r['n']} | {fmt(r['mean_d_pp'])} | {fmt(r['spread_pp'])} | {fmt(r['t'], 3)} | "
            f"{r['test']} | {mde} | {r['old_verdict']} | {r['verdict_new']} | {r['maturity']} |")
    lines.append("")
    lines.append("† = 产物未存 `mde_pp`，由本脚本按 `2.8016 × se` 重算。")
    lines.append("")
    mp = "reports/ROBUSTNESS_TABLE.md"
    with open(mp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print("\n".join(lines[6:]))
    print()
    print(f"[总表] {jp}  ({os.path.getsize(jp)} B)")
    print(f"[总表] {mp}  ({os.path.getsize(mp)} B)")

    # 自检：mean_d 与 spread 必须是 2 倍关系
    bad = [r["tag"] for r in rows
           if r["mean_d_pp"] is not None and r["spread_pp"] is not None
           and abs(r["spread_pp"] - 2 * r["mean_d_pp"]) > 1e-6]
    if bad:
        print(f"[警告] spread ≠ 2×mean_d：{bad}")
        return 1
    print("[自检] 所有行 spread == 2×mean_d ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
