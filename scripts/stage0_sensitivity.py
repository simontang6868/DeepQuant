"""阶段 0a 的事后敏感性检验 —— 从已落盘的结果 json 重算，不重跑回测。

为什么单独一个脚本
------------------
`docs/STAGE0_SURVIVORSHIP_2026-10-19.md` 的分层表是**看到主结果之后**做的稳健性检查，
按预登记它**不参与判定**（判定已由全样本机械执行）。但它改变了结论的适用范围
（"偏差只在前段存在"），所以必须可复算，不能只在报告里写几个数字。

**双口径**：预登记 v1.1 把行情从腾讯加性前复权换成乘性前复权（原因见
`docs/QFQ_ADDITIVE_ARTIFACT_2026-10-19.md`）。本脚本对两套口径都算一遍，
用来回答"结论是不是口径artifact"——如果两套口径给出同样的结构，才谈得上稳健。

输入只读两个结果 json，输出 `docs/STAGE0_SENSITIVITY_2026-10-19.json`。
统计函数直接复用探针脚本里的 `mannwhitney`，避免两份实现漂移。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "v12_full": ("docs/STAGE0_RESULTS_V12_2026-10-19.json",
                 "乘性前复权 + csi300 全量 298 只（主口径，预登记 v1.2）"),
    "v11_sampled": ("docs/STAGE0_RESULTS_MULT_2026-10-19.json",
                    "乘性前复权 + csi300 抽样 50 只（v1.1）"),
    "legacy_add": ("docs/STAGE0_RESULTS_2026-10-19.json",
                   "腾讯加性前复权 + 抽样 50 只（v1.0，仅作口径对照）"),
}
OUT = ROOT / "docs" / "STAGE0_SENSITIVITY_2026-10-19.json"
PROBE = ROOT / "scripts" / "stage0_survivorship_probe.py"


def _probe():
    spec = importlib.util.spec_from_file_location("stage0_probe", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SEGMENTS = [
    ("全样本", None, "预登记判据在此口径上执行过一次"),
    ("2017-2020 起始窗口", ("2017", "2018", "2019", "2020"), "时间切分上段"),
    ("2021-2024 起始窗口", ("2021", "2022", "2023", "2024"), "时间切分下段"),
]
FILTERS = [
    ("基准", lambda w: True, "未做任何剔除"),
    ("剔除 B 股（200*/900*）", lambda w: w["code"][:3] not in ("200", "900"), "B 股流动性与 A 股不可比"),
]


def main() -> None:
    probe = _probe()
    blocks: dict[str, list[dict]] = {}
    verdicts: dict[str, str] = {}

    for layout, (rel, desc) in SOURCES.items():
        p = ROOT / rel
        if not p.exists():
            blocks[layout] = []
            verdicts[layout] = "MISSING"
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        verdicts[layout] = d["verdict"]
        windows = [
            w for w in d["windows"]
            if w["ret"] is not None and w["selection_success"] and w["oos_success"] and w["is_complete"]
        ]
        rows = []
        for fname, ffn, fnote in FILTERS:
            for sname, yrs, snote in SEGMENTS:
                if fname != "基准" and yrs is not None:
                    continue  # 剔除与切分交叉的组合信息量低，不堆表
                ws = [w for w in windows if ffn(w)]
                if yrs is not None:
                    ws = [w for w in ws if (w["oos_start"] or "")[:4] in yrs]
                a = [w["ret"] for w in ws if w["group"] == "A_listed"]
                b = [w["ret"] for w in ws if w["group"] == "B_delisted"]
                if not a or not b:
                    continue
                mw = probe.mannwhitney(b, a)
                rows.append({
                    "filter": fname,
                    "segment": sname,
                    "note": f"{fnote} / {snote}" if fname != "基准" else snote,
                    "n_A": len(a),
                    "n_B": len(b),
                    "median_A": round(float(np.median(a)), 6),
                    "median_B": round(float(np.median(b)), 6),
                    "delta_median": round(float(np.median(b) - np.median(a)), 6),
                    "p_one_sided_B_less": mw.get("p_one_sided_B_less"),
                    "p_two_sided": mw.get("p_two_sided"),
                    "rank_biserial": mw.get("rank_biserial_B_minus_A"),
                })
        blocks[layout] = rows

    payload = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "status": "POST_HOC_NOT_USED_FOR_VERDICT",
        "warning": (
            "本表是看到主结果之后做的稳健性检查，不参与判定。"
            "判定已在全样本上由预登记判据机械执行过一次，此处不得改判。"
        ),
        "layout_desc": {k: v[1] for k, v in SOURCES.items()},
        "verdict_by_layout": verdicts,
        "rows_by_layout": blocks,
        "key_finding": (
            "三套口径给出同一结构：全样本均为 S1；偏差集中在前段。"
            "v1.2（298 只）全样本 −1.67pp / p<1e-16 / rb −0.109；"
            "v1.1（50 只）−1.47pp / 2e-6 / −0.094 —— A 组中位从 −1.52% 移到 −1.32%（+0.20pp），"
            "说明 50 只抽样略偏悲观，且抽样偏差不足以改变结论。"
            "后段(2021-2024)：v1.2 显著（−1.03pp, p=8e-4, rb −0.056），v1.1 不显著（−0.53pp, p=0.19）。"
            "差别不是单一原因：(a) A 组后段窗口 570→3348，功效提升；"
            "(b) 2021 年本身 Δ≈0（两版都为正，是数据事实不是抽样）；"
            "(c) 2023 年 50 只样本的 A 中位比 289 只低 1.13pp，属抽样假象。"
            "因此 v1.1 的『后段测不出』应改读为『功效不足 + 局部抽样假象』，不得读作『偏差在 2021 年后消失』。"
            "另：legacy_add 后段 Δ=+0.41pp / p=0.69 / rb +0.014，符号被翻转 —— "
            "加性口径不仅压缩组间差，还会反转时段结构，不得用于跨年份对比。"
        ),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for layout, rows in blocks.items():
        print(f"\n=== {layout} ({SOURCES[layout][1]}) verdict={verdicts[layout]}")
        print(f"{'剔除':24s}{'时段':20s}{'nA':>5}{'nB':>6}{'medA':>9}{'medB':>9}{'Δmed':>9}{'p1':>9}{'rb':>8}")
        for r in rows:
            print(f"{r['filter']:24s}{r['segment']:20s}{r['n_A']:>5}{r['n_B']:>6}"
                  f"{r['median_A']:>9.4f}{r['median_B']:>9.4f}{r['delta_median']:>9.4f}"
                  f"{r['p_one_sided_B_less']:>9.4f}{r['rank_biserial']:>8.4f}")
    print(f"\n[OUT] {OUT.name}")


if __name__ == "__main__":
    main()
