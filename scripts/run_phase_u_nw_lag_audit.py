#!/usr/bin/env python
"""阶段 U：NW 滞后稳健性全项目审计（只读审计）。

预登记 `docs/PHASE_U_NW_LAG_AUDIT_PREREG_2026-10-22.md`，冻结 `b97d66d91989…`。

审计问题（预登记 §1）：
  Q1 16 条被引用月度序列的 t 剖面 t_nw(L)，L ∈ {0,…,6}
  Q2 哪些结论的显著/不显著判定会在网格内翻转
  Q3 承重正面结论（A 类）里有没有 L 敏感的

工程约定（踩过的坑）：
- 所有落盘文件一律显式 LF 行尾（Windows 文本模式默认 CRLF）；
- `newey_west()` 入参必须是小数（pp/100），读 `mean_pp`/`se_nw_pp`；
  本脚本每条序列都同时校 t 与 mean_pp（单位铁律：只看 t 发现不了单位错）；
- 只读 6 个冻结 CSV，不 import 任何读 `data/` 行情的模块（闸门 G5 由构造保证）。

用法：
    python scripts/run_phase_u_nw_lag_audit.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402  （canonical newey_west）

# ==================== 预登记常量（跑前冻结，脚本启动校验） ====================
PREREG_SHA256 = "b97d66d91989ba6b35ccbb41c41feab5909f34ffcc4bf40003a651f3dbb894af"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phaseu_out", PREREG_SHA12)
PREREG_MD = "docs/PHASE_U_NW_LAG_AUDIT_PREREG_2026-10-22.md"

LAG_GRID = (0, 1, 2, 3, 4, 5, 6)     # 预登记 §3
T_CRIT = 1.96                        # 双侧；被审计结论全部属 Gate5/6 以后时代
G1_TOL = 1e-4                        # 预登记 §7 G1
G2_TOL = 1e-9                        # 预登记 §7 G2
G3_TOL = 1e-12                       # 预登记 §7 G3
T1C_TOL = 1e-3                       # 预登记 §7 G1 附：U11 剖面 vs 阶段 T 报告表

# —— 源 CSV（sha256 为工作区字节，跑前重算校验；预登记 §7 G4）——
G6_CSV = "gate6_out/b8c4f1ecba44/monthly_paired.csv"
I_CSV = "phasei_out/8f5c35121fdb/monthly.csv"
F_CSV = "phasef_out/aec46d88bbda/monthly.csv"
L_CSV = "phasel_out/9ffc3710cf6f/monthly.csv"
O_CSV = "phaseo_out/ac7aa2830ab0/monthly.csv"
S_CSV = "phases_out/f098a59fb1b5/monthly.csv"
E_CSV = "phasee_out/2a3893f3f5b1/monthly.csv"
G_CSV = "phaseg_out/e7da0f9ff3f1/monthly.csv"

INPUT_SHA = {
    G6_CSV: "6ffb6bbb56d4b271e2d85c7ff987ebca1eb1e14aeadb117ba531ba655c1c0c42",
    I_CSV: "aa1404cf3b68da30fd31113deade356c98829d85e13e23c267cb4b40e870a99a",
    F_CSV: "d8bbd55dad2ee752882411d196269358386fbe1b6f07004406ae708d2f042b98",
    L_CSV: "e302874f3eaa984640dcb42b2570dfb05ab442ddfdb67e5ae267dfabcd874d5f",
    O_CSV: "768e49a9462cd7c00f3aacfcdb1e87bbe37bb75b348cd6fd85809e12493a4152",
    S_CSV: "3333efe96ac0db29d1568057678eda5187d0c03b42c1e508f5eb8736bd24da91",
    E_CSV: "282bfc94861ad50e5b642fa85fa3b88309511f5685eb060683ee001f6f487e7d",
    G_CSV: "917aff3aaed2a7ad7dea1271a561ec96be6e03c2f7e0969ce65bf08e5eedf243",
}

# —— 16 条序列（预登记 §2.1；期望值 = 各产物 summary.json 的记录头条）——
# unit: "dec"=CSV 原生小数；"pp"=CSV 原生 pp（读入后 /100）
# est:  记录头条当年实际使用的估计量——"nw3"=Newey-West L=3（15 条）；
#       "iid"=stdev/sqrt(n)（仅 U1；Gate 6 从未规定 HAC，见跑前更正说明
#       docs/PHASE_U_AUDIT_ADDENDUM_2026-10-22.md，预登记原文不改写）
# filter: ("full",) | ("segment", "2016-2026") | ("segment_drop", "2016-2026", months)
# cls: A=承重正面 / B=现代段组 / C=负面或已关闭（预登记 §4）
SERIES = [
    dict(id="U1", name="Gate 6 反转全A 毛 alpha", csv=G6_CSV, col="d", unit="dec",
         est="iid", filt=("full",), n=237, t3=4.3861083442,
         mean_pp=0.70844569, cls="A"),
    dict(id="U2", name="阶段 I exQ5 V0", csv=I_CSV, col="alpha_v0_pp", unit="pp",
         est="nw3", filt=("full",), n=237, t3=6.357163, mean_pp=0.230703, cls="A"),
    dict(id="U3", name="阶段 I exQ5 V1", csv=I_CSV, col="alpha_v1_pp", unit="pp",
         est="nw3", filt=("full",), n=237, t3=4.528365, mean_pp=0.163411, cls="A"),
    dict(id="U4", name="阶段 F MAX5 分层价差", csv=F_CSV, col="delta", unit="dec",
         est="nw3", filt=("full",), n=237, t3=3.4085764613, mean_pp=0.7404925843, cls="A"),
    dict(id="U5", name="阶段 L SURGE V1", csv=L_CSV, col="alpha_v1_pp", unit="pp",
         est="nw3", filt=("full",), n=237, t3=3.129485, mean_pp=0.106057, cls="C"),
    dict(id="U6", name="阶段 O BASE@3000万", csv=O_CSV, col="net_BASE_3000w", unit="pp",
         est="nw3", filt=("full",), n=237, t3=4.0386, mean_pp=0.135959, cls="A"),
    dict(id="U7", name="阶段 O CONSERVATIVE@3000万", csv=O_CSV, col="net_CONS_3000w",
         unit="pp", est="nw3", filt=("full",), n=237, t3=3.8996, mean_pp=0.131486, cls="A"),
    dict(id="U8", name="阶段 O STRESS@3000万", csv=O_CSV, col="net_STRESS_3000w",
         unit="pp", est="nw3", filt=("full",), n=237, t3=1.2687, mean_pp=0.042423, cls="C"),
    dict(id="U9", name="阶段 O BASE@1000万", csv=O_CSV, col="net_BASE_1000w", unit="pp",
         est="nw3", filt=("full",), n=237, t3=4.0021, mean_pp=0.125467, cls="A"),
    dict(id="U10", name="阶段 O CONSERVATIVE@1000万", csv=O_CSV, col="net_CONS_1000w",
         unit="pp", est="nw3", filt=("full",), n=237, t3=3.1152, mean_pp=0.098073, cls="A"),
    dict(id="U11", name="阶段 S 现代段 net_S@3000万", csv=S_CSV, col="net_S_3000w",
         unit="pp", est="nw3", filt=("segment", "2016-2026"), n=129,
         t3=2.218023521179044, mean_pp=0.111195653875969, cls="B"),
    dict(id="U12", name="阶段 T T1a（剔6极端月）", csv=S_CSV, col="net_S_3000w",
         unit="pp", est="nw3",
         filt=("segment_drop", "2016-2026",
               ("2016-01", "2016-02", "2020-02", "2020-03", "2024-01", "2024-02")),
         n=123, t3=1.9206676463201275, mean_pp=0.10717170284552846, cls="B"),
    dict(id="U13", name="阶段 S 毛 alpha 现代段", csv=S_CSV, col="alpha_pp", unit="pp",
         est="nw3", filt=("segment", "2016-2026"), n=129,
         t3=2.8633589255164926, mean_pp=0.15661508527131784, cls="B"),
    dict(id="U14", name="阶段 S BASE 现代段", csv=S_CSV, col="net_BASE_3000w",
         unit="pp", est="nw3", filt=("segment", "2016-2026"), n=129,
         t3=2.581295917681533, mean_pp=0.129407503875969, cls="B"),
    dict(id="U15", name="阶段 E 反转单边多头", csv=E_CSV, col="alpha", unit="dec",
         est="nw3", filt=("full",), n=237, t3=1.9013197797, mean_pp=0.2766985907, cls="C"),
    dict(id="U16", name="阶段 G MAX5 单边 V1", csv=G_CSV, col="alpha_v1", unit="dec",
         est="nw3", filt=("full",), n=237, t3=1.7640531446, mean_pp=0.1813083784, cls="C"),
]

# —— 阶段 T T1c 已测得的 U11 剖面（报告表，4 位小数；预登记 §6 可算基础二）——
T1C_KNOWN = {0: 1.5848, 1: 1.8445, 2: 2.0638, 3: 2.2180,
             4: 2.2984, 5: 2.4056, 6: 2.5320}

# —— U14 的精确缩放系数（常数移位恒等式，预登记 §6 可算基础一）——
U14_RATIO = 0.129407503875969 / 0.111195653875969


# ==================== 校验 ====================
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_prereg() -> None:
    got = sha256_file(PREREG_MD)
    if got != PREREG_SHA256:
        raise SystemExit(
            f"预登记哈希不匹配：{PREREG_MD}\n  期望 {PREREG_SHA256}\n  实测 {got}\n"
            "拒绝运行（预登记跑前冻结纪律）。")


def verify_inputs() -> dict:
    out = {}
    for path, want in INPUT_SHA.items():
        if not os.path.exists(path):
            raise SystemExit(f"源文件缺失：{path}")
        got = sha256_file(path)
        if got != want:
            raise SystemExit(f"源文件被改动：{path}\n  期望 {want}\n  实测 {got}")
        out[path] = got
    return out


# ==================== 序列加载 ====================
def load_rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def apply_filter(rows: list[dict], filt: tuple) -> list[dict]:
    if filt[0] == "full":
        return rows
    if filt[0] == "segment":
        return [r for r in rows if r["segment"] == filt[1]]
    if filt[0] == "segment_drop":
        seg, drop = filt[1], set(filt[2])
        return [r for r in rows
                if r["segment"] == seg and r["signal_date"][:7] not in drop]
    raise ValueError(f"未知过滤器 {filt}")


def to_decimal(rows: list[dict], col: str, unit: str) -> list[float]:
    vals = []
    for r in rows:
        v = float(r[col])
        if v != v:                      # NaN 防御：NaN 会让 newey_west 静默丢月
            raise SystemExit(f"列 {col} 出现 NaN（signal_date={r['signal_date']}）")
        vals.append(v / 100.0 if unit == "pp" else v)
    return vals


# ==================== 两套 NW 实现 ====================
def nw_canonical(x: list[float], lag: int) -> dict:
    """主实现：项目 canonical（run_phase_c.newey_west，Bartlett 核）。"""
    return C.newey_west(x, lag=lag)


def nw_independent(x: list[float], lag: int) -> tuple[float, float, float]:
    """独立实现：numpy.correlate 路径，与 canonical 的手写循环不同代码。

    同一估计量定义（Bartlett 权重 1−j/(L+1)），不同计算路径，
    用于闸门 G2（抓转写/公式错，不重新定义估计量）。
    """
    arr = np.asarray(x, dtype=float)
    n = arr.size
    m = float(arr.mean())
    e = arr - m
    ac = np.correlate(e, e, mode="full")[n - 1:] / n   # ac[j] = Σ e[t]e[t-j]/n
    s = float(ac[0])
    for j in range(1, lag + 1):
        s += 2.0 * (1.0 - j / (lag + 1.0)) * float(ac[j])
    if s <= 0.0:
        s = float(ac[0])
    se = math.sqrt(s / n)
    t = m / se if se > 0 else float("nan")
    return m, se, t


def iid_t(x: list[float]) -> float:
    """Gate 6 的记录口径：stdev(ddof=1)/sqrt(n)（run_gate6_allasof.stats_of）。

    仅 U1 使用（见跑前更正说明 docs/PHASE_U_AUDIT_ADDENDUM_2026-10-22.md）。
    """
    n = len(x)
    m = sum(x) / n
    sd = statistics.stdev(x)
    se = sd / math.sqrt(n)
    return m / se if se > 0 else float("nan")


# ==================== 主流程 ====================
def profile_series(spec: dict) -> dict:
    rows_all = load_rows(spec["csv"])
    rows = apply_filter(rows_all, spec["filt"])
    if len(rows) != spec["n"]:
        raise SystemExit(f"{spec['id']} 行数应为 {spec['n']}，实测 {len(rows)}")
    x = to_decimal(rows, spec["col"], spec["unit"])

    prof = {}
    for lag in LAG_GRID:
        rc = nw_canonical(x, lag)
        m_i, se_i, t_i = nw_independent(x, lag)
        dt = abs(t_i - rc["t_nw"])
        if dt > G2_TOL:
            raise SystemExit(
                f"G2 不过：{spec['id']} L={lag} 两套实现 t 差 {dt:.3e}")
        prof[lag] = {"mean_pp": rc["mean_pp"], "se_nw_pp": rc["se_nw_pp"],
                     "t_nw": rc["t_nw"], "p_nw": rc["p"],
                     "t_independent": t_i, "n": rc["n"],
                     "signif": bool(rc["t_nw"] >= T_CRIT)}

    # G1：按记录口径复现产物记录头条（t 与 mean_pp 同时校，单位铁律）。
    # est="nw3" → 与 L=3 剖面对比；est="iid" → 与 Gate 6 的 iid 口径对比
    # （U1；见跑前更正说明，预登记原文不改写）。
    r3 = prof[3]
    if spec["est"] == "iid":
        got_t = iid_t(x)
    else:
        got_t = r3["t_nw"]
    dt = abs(got_t - spec["t3"])
    dm = abs(r3["mean_pp"] - spec["mean_pp"])
    if dt > G1_TOL or dm > G1_TOL:
        raise SystemExit(
            f"G1 不过：{spec['id']} 记录口径 {spec['est']} 重算 t={got_t:.6f} "
            f"vs 记录 {spec['t3']}（差 {dt:.2e}）；mean_pp={r3['mean_pp']:.6f} "
            f"vs 记录 {spec['mean_pp']}（差 {dm:.2e}）")

    # U11 附加：剖面 vs 阶段 T 报告 T1c 表（4 位小数）
    extra = None
    if spec["id"] == "U11":
        worst = max(abs(prof[L]["t_nw"] - T1C_KNOWN[L]) for L in LAG_GRID)
        if worst > T1C_TOL:
            raise SystemExit(f"G1 附：U11 剖面与阶段 T T1c 表最大差 {worst:.2e}")
        extra = {"vs_T1c_table_max_abs_diff": worst}

    # 翻转检测（预登记 §4）
    base = prof[3]["signif"]
    flip_lags = [L for L in LAG_GRID if prof[L]["signif"] != base]

    return {"profile": prof, "flip_lags": flip_lags, "extra": extra,
            "dates": [r["signal_date"] for r in rows]}


def gate_g3(rows_s: list[dict]) -> dict:
    """常数移位恒等：net_S − net_BASE 逐月常数；U11/U14 的 se_nw 全网格恒等。"""
    diffs = [float(r["net_S_3000w"]) - float(r["net_BASE_3000w"]) for r in rows_s]
    spread = max(diffs) - min(diffs)
    if spread > G3_TOL:
        raise SystemExit(f"G3 不过：net_S−net_BASE 非常数（max−min={spread:.3e}）")
    late = [r for r in rows_s if r["segment"] == "2016-2026"]
    xs = [float(r["net_S_3000w"]) / 100.0 for r in late]
    xb = [float(r["net_BASE_3000w"]) / 100.0 for r in late]
    worst = 0.0
    for lag in LAG_GRID:
        ds = abs(C.newey_west(xs, lag=lag)["se_nw"]
                 - C.newey_west(xb, lag=lag)["se_nw"])
        worst = max(worst, ds)
    if worst > G3_TOL:
        raise SystemExit(f"G3 不过：U11/U14 se_nw 最大差 {worst:.3e}")
    return {"const_shift": {"value": diffs[0], "max_minus_min": spread},
            "se_nw_max_abs_diff_U11_U14": worst}


def predicted_profile(spec: dict, u11_prof: dict) -> dict:
    """预登记 §6 的预测（跑前写死）：U14 精确（恒等式），其余模板缩放。"""
    if spec["id"] == "U11":
        return {"kind": "anchor", "t": {L: u11_prof[L]["t_nw"] for L in LAG_GRID}}
    if spec["id"] == "U14":
        return {"kind": "exact",
                "t": {L: u11_prof[L]["t_nw"] * U14_RATIO for L in LAG_GRID}}
    r = {L: T1C_KNOWN[L] / T1C_KNOWN[3] for L in LAG_GRID}
    return {"kind": "template",
            "t": {L: spec["t3"] * r[L] for L in LAG_GRID}}


def verdict_of(flips: dict, cls_of: dict) -> str:
    """预登记 §5 的三档判定（跑前写死）。"""
    if not any(flips.values()):
        return "NW_LAG_ROBUST_ALL"
    if any(flips[sid] for sid, c in cls_of.items() if c == "A"):
        return "NW_LAG_SENSITIVITY_LOAD_BEARING"
    return "NW_LAG_SENSITIVITY_CONFIRMED"


def main() -> None:
    t0 = time.time()
    verify_prereg()
    inputs = verify_inputs()
    os.makedirs(OUT_DIR, exist_ok=True)

    rows_s = load_rows(S_CSV)
    g3 = gate_g3(rows_s)

    results = {}
    u11_prof = None
    for spec in SERIES:
        res = profile_series(spec)
        if spec["id"] == "U11":
            u11_prof = res["profile"]
        results[spec["id"]] = res

    # 预测对照 + 判定（预登记 §5）
    flips = {sid: r["flip_lags"] for sid, r in results.items()}
    cls_of = {s["id"]: s["cls"] for s in SERIES}
    a_flips = [sid for sid, f in flips.items() if f and cls_of[sid] == "A"]
    verdict = verdict_of(flips, cls_of)

    pred_check = {}
    for spec in SERIES:
        p = predicted_profile(spec, u11_prof)
        pred_signif = {L: p["t"][L] >= T_CRIT for L in LAG_GRID}
        pred_flip = [L for L in LAG_GRID
                     if pred_signif[L] != pred_signif[3]]
        actual = results[spec["id"]]
        pred_check[spec["id"]] = {
            "kind": p["kind"],
            "pred_t": {L: p["t"][L] for L in LAG_GRID},
            "pred_flip_lags": pred_flip,
            "actual_flip_lags": actual["flip_lags"],
            "flip_prediction_hit": pred_flip == actual["flip_lags"],
            "max_abs_t_err": max(abs(p["t"][L] - actual["profile"][L]["t_nw"])
                                 for L in LAG_GRID),
        }
        if spec["id"] == "U1":
            pred_check["U1"]["note"] = (
                "模板以 iid 记录值 4.3861 为基（预登记 §6 原文）；若改以 NW(3) "
                "4.6443 为基，L=0 预测为 3.32，'不翻转'结论不变")

    monotone = all(
        results[s["id"]]["profile"][L]["t_nw"]
        <= results[s["id"]]["profile"][L + 1]["t_nw"] + 1e-12
        for s in SERIES for L in LAG_GRID[:-1])

    gates = {"G1_reproduce_headline": "16/16 通过（t 与 mean_pp 同时校，tol 1e-4；"
             "其中 15 条按 NW(3)、U1 按 iid 记录口径，见跑前更正说明）",
             "G1_extra_U11_vs_T1c": "通过（tol 1e-3）",
             "G2_independent_nw": "16×7=112 组合通过（tol 1e-9）",
             "G3_const_shift": "通过",
             "G4_input_integrity": f"{len(inputs)}/{len(inputs)} 个源文件 sha256 一致",
             "G5_no_market_data_import": "通过（脚本不 import 任何 data/ 读取模块）"}

    summary = {
        "experiment": "phaseu-nw-lag-audit-v1",
        "prereg_sha256": PREREG_SHA256,
        "kind": "只读审计（不改变任何既有判定）",
        "addendum": "docs/PHASE_U_AUDIT_ADDENDUM_2026-10-22.md（U1 记录口径为 iid）",
        "inputs": inputs,
        "lag_grid": list(LAG_GRID),
        "t_crit": T_CRIT,
        "series": {sid: {"name": next(s["name"] for s in SERIES if s["id"] == sid),
                         "class": next(s["cls"] for s in SERIES if s["id"] == sid),
                         "est": next(s["est"] for s in SERIES if s["id"] == sid),
                         "n": next(s["n"] for s in SERIES if s["id"] == sid),
                         "recorded_t3": next(s["t3"] for s in SERIES if s["id"] == sid),
                         "profile": {str(L): r["profile"][L] for L in LAG_GRID},
                         "flip_lags": r["flip_lags"]}
                   for sid, r in results.items()},
        "gates": gates,
        "gate_g3_detail": g3,
        "flips": flips,
        "a_class_flips": a_flips,
        "verdict": verdict,
        "monotone_in_L": bool(monotone),
        "prediction_check": pred_check,
        "honest_limits": [
            "只审计持久化了逐月序列的头条/现代段结论；阶段 R/P/Q 不在范围（预登记 §2.2）",
            "模板预测的锚只有 U11 一条序列；预测错只说明自相关结构不同型",
            "本审计不改判；翻转结论的引用必须附带 t(L) 剖面（预登记 §5 后果规则）",
        ],
    }

    manifest = {
        "prereg_sha256": PREREG_SHA256,
        "prereg_md": PREREG_MD,
        "inputs": inputs,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "runtime_sec": None,  # 回填
        "outputs": ["summary.json", "profiles.csv", "manifest.json", "run.log"],
    }

    # —— 落盘（显式 LF）——
    with open(os.path.join(OUT_DIR, "summary.json"), "w",
              newline="\n", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    with open(os.path.join(OUT_DIR, "profiles.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["series_id", "name", "class", "L", "n", "mean_pp",
                    "se_nw_pp", "t_nw", "signif_1p96"])
        for spec in SERIES:
            r = results[spec["id"]]
            for L in LAG_GRID:
                p = r["profile"][L]
                w.writerow([spec["id"], spec["name"], spec["cls"], L, p["n"],
                            f"{p['mean_pp']:.10f}", f"{p['se_nw_pp']:.10f}",
                            f"{p['t_nw']:.10f}", int(p["signif"])])

    summary["runtime_sec"] = round(time.time() - t0, 2)
    manifest["runtime_sec"] = summary["runtime_sec"]
    with open(os.path.join(OUT_DIR, "summary.json"), "w",
              newline="\n", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "manifest.json"), "w",
              newline="\n", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # —— 人读摘要（同时写 run.log）——
    lines = []
    lines.append("阶段 U：NW 滞后稳健性全项目审计（只读）")
    lines.append(f"预登记 {PREREG_SHA256[:12]}…  判定 {verdict}")
    lines.append(f"t 对 L 单调不减：{monotone}")
    lines.append("")
    lines.append(f"{'ID':<4}{'类':<3}{'n':<5}{'t(3)':>9}"
                 + "".join(f"{'L=' + str(L):>9}" for L in LAG_GRID)
                 + "   翻转")
    for spec in SERIES:
        r = results[spec["id"]]
        row = f"{spec['id']:<4}{spec['cls']:<3}{spec['n']:<5}{spec['t3']:>9.4f}"
        row += "".join(f"{r['profile'][L]['t_nw']:>9.4f}" for L in LAG_GRID)
        row += "   " + (",".join(f"L={L}" for L in r["flip_lags"])
                        if r["flip_lags"] else "—")
        lines.append(row)
    lines.append("")
    lines.append("预测对照（翻转预测命中数）："
                 + f"{sum(1 for v in pred_check.values() if v['flip_prediction_hit'])}/16")
    for sid, v in pred_check.items():
        if not v["flip_prediction_hit"]:
            lines.append(f"  {sid}: 预测翻转 {v['pred_flip_lags']} vs "
                         f"实测 {v['actual_flip_lags']}")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(OUT_DIR, "run.log"), "w",
              newline="\n", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
