#!/usr/bin/env python
"""exQ5 审计公共模块：独立统计实现 + 冻结产物 IO。

设计原则（任务书 §11）：
- 统计实现**不复用**项目 `run_phase_c.newey_west`：本模块用 numpy 向量化路径
  （np.dot 自协方差 + Bartlett 权重），与项目实现（Python 循环 + statistics.mean）
  是不同代码路径；两者与记录值三方对照，一致才认 A 级复现。
- 所有输入只读冻结产物；所有输出显式 LF、显式单位。
- 单位铁律：序列单位在函数名/字段名里标注（_dec = 小数，_pp = 百分点）。
"""
from __future__ import annotations

import csv
import math
import os

import numpy as np

# ---- 路径（相对仓库根；本模块被 audit 脚本 import，cwd 必须是仓库根）----
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AUDIT_DIR = os.path.join(REPO, "research", "exq5_audit_2026-10-22")
OUT_DIR = os.path.join(AUDIT_DIR, "out")

FROZEN = {
    "phasei_monthly": os.path.join(REPO, "phasei_out/8f5c35121fdb/monthly.csv"),
    "phasei_summary": os.path.join(REPO, "phasei_out/8f5c35121fdb/summary.json"),
    "phaseo_monthly": os.path.join(REPO, "phaseo_out/ac7aa2830ab0/monthly.csv"),
    "phaseo_summary": os.path.join(REPO, "phaseo_out/ac7aa2830ab0/summary.json"),
    "phasej_monthly": os.path.join(REPO, "phasej_out/f76de8a277f6/monthly.csv"),
    "phases_monthly": os.path.join(REPO, "phases_out/f098a59fb1b5/monthly.csv"),
    "phases_summary": os.path.join(REPO, "phases_out/f098a59fb1b5/summary.json"),
    "phaset_monthly": os.path.join(REPO, "phaset_out/ef29a743997b/monthly.csv"),
    "phaset_summary": os.path.join(REPO, "phaset_out/ef29a743997b/summary.json"),
    "phaseu_profiles": os.path.join(REPO, "phaseu_out/b97d66d91989/profiles.csv"),
    "phaseu_summary": os.path.join(REPO, "phaseu_out/b97d66d91989/summary.json"),
}

T_CRIT = 1.96
MDE_COEF = 2.8016          # 双侧 5%、功效 80%（项目口径，预登记写死）
LAG_GRID = (0, 1, 2, 3, 4, 5, 6)

# 阶段 T 先验极端月（跑前写死，不许改）
DROP6 = ("2016-01", "2016-02", "2020-02", "2020-03", "2024-01", "2024-02")
MODERN_LO = "2016-01-01"

# 阶段 O 费率口径（预登记 §2）
COMM_RATE = 0.00025        # 佣金，双边
STAMP_RATE = 0.0005        # 印花税，仅卖出
TRANSFER_RATE = 0.00001    # 过户费，双边
STRESS_ONE_SIDE = 0.003    # 压力口径，单边综合（对称）
MIN_COMM_YUAN = 5.0        # 最低佣金（元/笔）


# ==================== 独立统计实现 ====================
def _clean(x) -> np.ndarray:
    a = np.asarray([v for v in x if v == v], dtype=float)  # 去 NaN（== 自比较）
    return a


def nw_profile(x, lags=LAG_GRID) -> dict:
    """对同一序列返回 {L: {mean, se_nw, t_nw, se_iid, t_iid, n}}，L=0 即 iid。

    Bartlett 核权重 1 - j/(L+1)，自协方差除以 n（标准 NW，均值已估）。
    **实现路径**：numpy 向量化（np.dot），与项目 run_phase_c.newey_west 的
    Python 循环实现相互独立。
    """
    a = _clean(x)
    n = a.size
    out = {}
    for L in lags:
        m = float(a.mean())
        e = a - m
        g0 = float(np.dot(e, e)) / n
        s = g0
        for j in range(1, L + 1):
            gj = float(np.dot(e[j:], e[:-j])) / n
            s += 2.0 * (1.0 - j / (L + 1)) * gj
        fell_back = False
        if s <= 0:                      # 小样本可能为负；与项目口径一致：退回 g0
            s, fell_back = g0, True
        se_nw = math.sqrt(s / n)
        se_iid = float(a.std(ddof=1)) / math.sqrt(n)   # ddof=1，与 statistics.stdev 一致
        out[L] = {"n": n, "mean": m, "se_nw": se_nw, "t_nw": (m / se_nw if se_nw > 0 else float("nan")),
                  "se_iid": se_iid, "t_iid": (m / se_iid if se_iid > 0 else float("nan")),
                  "fell_back": fell_back}
    return out


def nw_t(x, lag: int = 3) -> dict:
    return nw_profile(x, lags=(lag,))[lag]


def norm_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def power_two_sided(t: float) -> float:
    """事后功效 = Φ(|t|-1.96) + Φ(-|t|-1.96)。"""
    if t != t:
        return float("nan")
    return norm_cdf(abs(t) - T_CRIT) + norm_cdf(-abs(t) - T_CRIT)


def mde_of(se_nw: float, coef: float = MDE_COEF) -> float:
    return coef * se_nw


# ==================== 冻结产物 IO ====================
def read_csv_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: str, header: list[str], rows: list[list]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:   # newline="" + 显式 \n → LF
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_json(path: str, obj) -> None:
    import json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def col(rows: list[dict], key: str) -> list[float]:
    """取列并转 float；空串/None → nan。"""
    out = []
    for r in rows:
        v = r.get(key, "")
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(float("nan"))
    return out


def finite(x):
    return [v for v in x if v == v]


def sha256_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ==================== 独立容量与成本实现（定义同预登记，代码路径不同） ====================
def capacity_indep(lots, budget: float) -> dict:
    """可负担子集容量。定义与 `run_phase_i_exq5.capacity_of` 相同：
    按一手金额升序取最大 k 使 k*lots[k-1] <= budget；per = budget/k；
    每只整手 n_lot = floor(per/lot)；未投资 = 1 - Σn_lot*lot/budget。
    **实现路径**：numpy argsort + 向量化判定，非 Python 循环。
    """
    a = np.asarray([l for l in lots if l == l and l > 0], dtype=float)
    k_total = int(a.size)
    if k_total == 0 or budget <= 0:
        return {"k_total": k_total, "k": 0, "covered": 0.0, "uninvested": 1.0,
                "weight_mae_pp": float("nan"), "invested": 0.0}
    order = np.argsort(a, kind="stable")
    s = a[order]
    ks = np.arange(1, k_total + 1, dtype=float)
    ok = ks * s <= budget
    k = int(ks[ok].max()) if ok.any() else 0
    if k == 0:
        return {"k_total": k_total, "k": 0, "covered": 0.0, "uninvested": 1.0,
                "weight_mae_pp": float("nan"), "invested": 0.0}
    picked = s[:k]
    per = budget / k
    n_lot = np.floor(per / picked)
    invested = float((n_lot * picked).sum())
    ws = n_lot * picked / budget
    mae_pp = float(np.abs(ws - 1.0 / k).mean()) * 100.0
    return {"k_total": k_total, "k": k, "covered": k / k_total,
            "uninvested": 1.0 - invested / budget,
            "weight_mae_pp": mae_pp, "invested": invested}


def cost_base_indep(to: float) -> float:
    """BASE：佣金双边 + 印花税仅卖出 + 过户费双边（pp/月）。不对称，写死。"""
    return (2.0 * to * (COMM_RATE + TRANSFER_RATE) + to * STAMP_RATE) * 100.0


def cost_stress_indep(to: float) -> float:
    return 2.0 * to * STRESS_ONE_SIDE * 100.0


def cost_cons_indep(to: float, w: float, k: int) -> float:
    """CONSERVATIVE：BASE + 单笔佣金 max(从价, 5元)。"""
    if k <= 0:
        return cost_base_indep(to)
    per_trade = w / k
    n_trades = 2.0 * to * k
    eff = max(per_trade * COMM_RATE, MIN_COMM_YUAN)
    c_comm = n_trades * eff / w * 100.0
    return c_comm + (2.0 * to * TRANSFER_RATE + to * STAMP_RATE) * 100.0
