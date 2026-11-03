"""L3ae：phasei 的成本档补全。

只做**算术**：把 phasei 已冻结的单边换手与逐月 alpha 代入同一成本公式，
在不同费率下给出净值与 t。不重跑回测，不接触选股逻辑。

本模块存在的理由是一条纪律问题，而不是计算方法问题：

phasei 的判定 ``EXQ5_COST_ABSORBED`` 只对预登记主成本 **0.3%** 成立。
0.3% 是真实综合费率（等价单边 0.051%）的 **5.9 倍**，属于压力参数。
对外只说「成本吸收」会让读者以为「真实费率下也吸收」，而两者是不同的结论。
补一档是披露完整性问题——补出来的档**不能改变任何枚举判定**，
所以不存在「为提高通过数而加档」的动机。
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import family as fam

#: 预登记哈希。改预登记就拒跑——与 L3ac / L3ad 同一约定。
PREREG_SHA = "b452f926840bb198d9458dec3bd206ec19f5bca895cfe064edc729bd28fa1a73"

#: 费率构成（元/股口径的百分比形式）。来源 `scripts/run_phase_o_fee_convention.py`，
#: 由 `scripts/tests/test_phase_o.py::test_equivalent_symmetric_rate_matches_prereg` 钉住。
COMM_RATE = 0.00025
STAMP_RATE = 0.0005
TRANSFER_RATE = 0.00001
MIN_COMMISSION = 5.0

#: 判定口径：phasei 的 primary_cost，本阶段不改它。
PRIMARY_COST = 0.003

#: 实际执行的闸门条数（含 G1b 与 G9 —— 预登记只写了 8 条）。
#: 报告里引用这个数而不是硬写，避免加闸门后忘记同步。
GATE_COUNT = 10


class CostTierError(RuntimeError):
    """冻结输入被改动或复现失败。"""


def equivalent_single_side(
    *,
    comm: float = COMM_RATE,
    stamp: float = STAMP_RATE,
    transfer: float = TRANSFER_RATE,
) -> float:
    """等价**对称**单边综合费率。

    真实费用不对称：佣金与过户费双边，印花税只在卖出方。
    若换手为 ``to``，则成本 = ``2·to·(佣金+过户) + to·印花``。
    想用一个对称费率 ``r`` 表达，令 ``2·to·r`` 等于上式 ⇒ ``r = (2(佣金+过户)+印花)/2``。
    """
    return (2.0 * (comm + transfer) + stamp) / 2.0


def effective_rate_with_min_commission(notional: float, *, comm: float = COMM_RATE) -> float:
    """单笔金额为 ``notional`` 元时，佣金的**实际**费率。

    不足 5 元按 5 元收 ⇒ 小额时实际费率高于名义费率。这是「费率越低所需资金越大」
    这句话的算术来源：``notional ≥ 5/comm = 20,000`` 元才摆脱下限。
    """
    if notional <= 0:
        raise CostTierError("单笔金额必须为正")
    return max(comm * notional, MIN_COMMISSION) / notional


def cost_tier(
    *,
    rate: float,
    turnover: float,
    mean_gross: float,
    se_iid: float,
    se_nw3: float,
) -> dict[str, Any]:
    """单档成本后的净值与两套 t。

    ⚠️ 口径与 phasei 的 ``cost_of`` **逐字一致**：月度成本 = 2 × 单边换手 × 费率，
    换手是**单边**、已经含了「买 + 卖」两边的交易量。写成 ``2×换手×12×费率`` 是年化，
    本项目第一版就在这里错过（月成本被算成 1.93pp，正确 0.161pp）。

    ``se_iid`` 与 ``se_nw3`` 都由调用方从逐月数据独立算出。**t 不随 rate 变分母**——
    这是 phasei 的实现事实（成本按确定性扣减，不并入方差），本阶段如实沿用并在报告里
    标为已知简化，而不是悄悄改成更宽松的口径。
    """
    cm = 2.0 * turnover * rate * 100.0
    net = mean_gross - cm
    return {
        "rate": rate,
        "rate_pct": rate * 100.0,
        "cost_pp_month": cm,
        "cost_pp_year": cm * 12.0,
        "net_pp_month": net,
        "t_iid": net / se_iid if se_iid > 0 else float("nan"),
        "t_nw3": net / se_nw3 if se_nw3 > 0 else float("nan"),
    }


def iid_se(values: Sequence[float]) -> float:
    """样本标准误（ddof=1）。与 phasei 的 ``sd_d*sqrt(n)`` 反推值一致。

    ddof 必须是 1：用总体标准差（ddof=0）会得到 0.048312，与产物 0.048414 差 0.2%，
    这个量级足以让 G5 通过而实际口径不同。
    """
    clean = [float(v) for v in values if v == v]
    if len(clean) < 2:
        return float("nan")
    return statistics.stdev(clean) / math.sqrt(len(clean))


def load_prereg(root: Path) -> dict[str, Any]:
    path = Path(root) / "docs" / "gates" / "l3ae_prereg.json"
    raw = path.read_bytes()
    import hashlib

    got = hashlib.sha256(raw).hexdigest()
    if got != PREREG_SHA:
        raise CostTierError(f"预登记被改动：{got[:12]}… ≠ 冻结值 {PREREG_SHA[:12]}…")
    return json.loads(raw.decode("utf-8"))


def load_inputs(root: Path, prereg: Mapping[str, Any]) -> dict[str, Any]:
    """按冻结清单逐个校验 sha256 后读入。任何一项不符即拒跑。"""
    import hashlib

    loaded: dict[str, Any] = {}
    for name, spec in prereg["inputs_frozen"].items():
        path = Path(root) / spec["path"]
        if not path.exists():
            raise CostTierError(f"冻结输入缺失：{spec['path']}")
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != spec["sha256"]:
            raise CostTierError(
                f"冻结输入被改动：{spec['path']} {got[:12]}… ≠ {spec['sha256'][:12]}…"
            )
        loaded[name] = path
    return loaded


def monthly_values(csv_path: Path, column: str) -> list[float]:
    """读逐月序列。只认表头里的列名，不做任何插补——缺值直接暴露。"""
    import csv

    with Path(csv_path).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise CostTierError("逐月文件为空")
    if column not in rows[0]:
        raise CostTierError(f"列 {column} 不存在；可用列 {sorted(rows[0])}")
    out = []
    for r in rows:
        v = r[column]
        if v is None or v == "":
            raise CostTierError(f"列 {column} 有缺失，拒绝静默补全")
        out.append(float(v))
    return out


def storage_floor(observed: float, decimals: int = 6) -> float:
    """由产物**存储精度**推出的最小可达相对偏差。

    phasei 的 summary 把 ``net_pp_month`` 存成 6 位小数（如 0.125687），
    所以任何「逐位复现」的尝试都带着 ≤ 5×10⁻⁷ 的绝对误差。
    对 net≈0.1257 来说这就是 ≈4×10⁻⁶ 的**相对**误差——比 1e-6 大四倍。

    这条函数存在的唯一目的：区分「实现错了」与「判据不可满足」。
    L3ae 预登记 G1 写的是「相对偏差 ≤ 1e-6」，在这组输入下**逻辑上不可达**，
    若不留这条证据，一个正确的实现会被报成复现失败。
    """
    if observed == 0:
        return float("inf")
    return (0.5 * 10.0 ** (-decimals)) / abs(observed)


def build(root: Path | str) -> dict[str, Any]:
    """跑完整阶段：校验 → 复现两档 → 补三档 → 闸门判定。"""
    root = Path(root)
    prereg = load_prereg(root)
    inputs = load_inputs(root, prereg)

    summary = json.loads(Path(inputs["phasei_summary"]).read_text(encoding="utf-8"))
    v1 = summary["V1_executable"]
    turnover = summary["turnover"]["v1_one_side"]
    n = int(v1["n"])

    values = monthly_values(Path(inputs["phasei_monthly"]), "alpha_v1_pp")
    if len(values) != n:
        raise CostTierError(f"逐月行数 {len(values)} ≠ summary.n {n}")

    # ⚠️ 均值必须从**原始逐月数据**算，不能取 summary 的 `mean_pp`。
    # 取后者是循环论证：用产物里已舍入的均值去「复现」产物，当然对得上。
    # 代价是复现时会出现存储量级的偏差（≈4e-6 相对），那正是 G1b 要区分的东西。
    mean_gross = statistics.fmean(values)
    summary_mean = float(v1["mean_pp"])

    se_iid = iid_se(values)
    nw = fam.reproduce_from_series(values, lag=3)
    se_nw3 = float(nw["se_nw"])

    # 逐档计算
    tiers = []
    for spec in prereg["tiers"]:
        row = cost_tier(
            rate=float(spec["rate"]),
            turnover=turnover,
            mean_gross=mean_gross,
            se_iid=se_iid,
            se_nw3=se_nw3,
        )
        row.update({"id": spec["id"], "label": spec["label"], "role": spec["role"]})
        tiers.append(row)

    # G1：既有两档必须与 phasei 产物逐位一致
    repro = []
    for tid, key in (("T_0p1", "0.1pct"), ("T_0p3", "0.3pct")):
        mine = next(t for t in tiers if t["id"] == tid)
        theirs = summary["cost"][key]
        for field, their_key in (
            ("cost_pp_month", "cost_pp_month"),
            ("net_pp_month", "net_pp_month"),
            ("t_iid", "t_net"),
        ):
            a, b = mine[field], float(theirs[their_key])
            rel = abs(a - b) / max(abs(b), 1e-15)
            repro.append(
                {
                    "tier": tid,
                    "field": field,
                    "mine": a,
                    "phasei": b,
                    "rel_diff": rel,
                    # 该档 net 的绝对值：用来算「这一档的存储下限」
                    "net_ref": abs(float(theirs["net_pp_month"])),
                }
            )

    # 最低佣金：单笔金额与等效费率
    n_keep_median = _median_n_keep(Path(inputs["phasei_monthly"]))
    min_comm = []
    for w, tid in ((1e8, "T_base_1e8"), (3e7, "T_base_3e7"), (1e7, "T_base_1e7")):
        notional = w / n_keep_median
        comm_eff = effective_rate_with_min_commission(notional)
        eq = comm_eff + STAMP_RATE / 2.0 + TRANSFER_RATE
        min_comm.append(
            {
                "capital": w,
                "notional_per_stock": notional,
                "commission_effective": comm_eff,
                "equivalent_single_side": eq,
                "tier": tid,
                "tier_rate": next(t["rate"] for t in tiers if t["id"] == tid),
            }
        )

    res = {
        "experiment_id": prereg["experiment_id"],
        "prereg_sha256": PREREG_SHA,
        "phasei_prereg_sha256": summary.get("prereg_sha256"),
        "phasei_verdict": summary.get("verdict"),
        "phasei_criteria": summary.get("criteria"),
        "phasei_primary_cost": float(summary["design"]["primary_cost"]),
        "n": n,
        "mean_gross_pp": mean_gross,
        "summary_mean_pp": summary_mean,
        "mean_source": "逐月 alpha_v1_pp 的算术均值（非 summary.mean_pp）",
        "turnover_one_side": turnover,
        "turnover_source": "summary.turnover.v1_one_side（逐月文件无换手列，这是唯一可用来源）",
        "se_iid_pp": se_iid,
        "se_nw3_pp": se_nw3,
        "se_ratio": se_iid / se_nw3 if se_nw3 else float("nan"),
        "nw3_reproduced_t": nw["t"],
        "summary_se_nw_pp": float(v1["se_nw_pp"]),
        "n_keep_median": n_keep_median,
        # 产物里的 0.1% 档数（G5 要用它做**独立**的反推基准，不能用我自己算的）
        "phasei_0p1": {
            "net": summary["cost"]["0.1pct"]["net_pp_month"],
            "t": summary["cost"]["0.1pct"]["t_net"],
        },
        "tiers": tiers,
        "min_commission": min_comm,
        "reproduction": repro,
        "g1_floor": min(storage_floor(r["net_ref"]) for r in repro) if repro else float("nan"),
    }
    res["gates"] = gate_codes(res, prereg)
    res["predictions"] = check_predictions(res, prereg)
    # 阻塞只看「实现/输入是否可信」，不看「判据是否可满足」。
    # G1 的 1e-6 已被证明不可达（见 storage_floor），真正该阻塞的是 G1b。
    blocking = {"G1B_BEYOND_STORAGE_FLOOR", "G5_SE_MISMATCH", "G7_NW_SE_MISMATCH"}
    res["status"] = (
        "BLOCKED_BY_REPRODUCTION_FAILURE"
        if any(g["code"] in blocking for g in res["gates"])
        else (
            "COST_TIERS_COMPLETED_WITH_DEFECTS"
            if res["gates"] or any(not p["holds"] for p in res["predictions"] if p["timing"] == "priori")
            else "COST_TIERS_COMPLETED"
        )
    )
    return res


def _median_n_keep(csv_path: Path) -> int:
    import csv

    with Path(csv_path).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    vals = [int(r["n_keep"]) for r in rows if r.get("n_keep")]
    if not vals:
        raise CostTierError("逐月文件没有 n_keep 列，无法算最低佣金影响")
    return int(statistics.median(vals))


def gate_codes(res: Mapping[str, Any], prereg: Mapping[str, Any]) -> list[dict[str, str]]:
    """预登记 G1–G8。每一条都写成「能算出反例」的形式。"""
    out: list[dict[str, str]] = []

    # G1：既有两档复现（预登记原文：相对偏差 ≤ 1e-6）
    for r in res["reproduction"]:
        if r["rel_diff"] > 1e-6:
            out.append(
                {
                    "code": "G1_REPRODUCTION_FAILED",
                    "detail": f"{r['tier']}.{r['field']}: 我 {r['mine']!r} vs 产物 {r['phasei']!r}"
                    f"（相对偏差 {r['rel_diff']:.3e}）",
                }
            )

    # G1b：按产物存储精度推出的**可达**容差。
    # 这不是放宽 G1，而是把「判据不可满足」与「实现错了」分开。
    # 注意 floor 必须按**该档的 net** 算：t = net/se、se 与档位无关，
    # 所以 net 和 t 两栏的相对误差同源，都由 net 的 6 位小数决定。
    for r in res["reproduction"]:
        floor = storage_floor(r["net_ref"])
        if r["rel_diff"] > floor * 1.5:
            out.append(
                {
                    "code": "G1B_BEYOND_STORAGE_FLOOR",
                    "detail": f"{r['tier']}.{r['field']}: 偏差 {r['rel_diff']:.3e} 超出该档存储下限 "
                    f"{floor:.3e} 的 1.5 倍——这不只是四舍五入",
                }
            )

    # G2：成本对费率线性
    ratios = [
        t["cost_pp_month"] / t["rate"] for t in res["tiers"] if t["rate"] > 0
    ]
    if ratios and (max(ratios) - min(ratios)) / max(ratios) > 1e-12:
        out.append(
            {"code": "G2_COST_NOT_LINEAR", "detail": f"cost/rate 比值跨度 {min(ratios)!r}~{max(ratios)!r}"}
        )

    # G3：净值恒等
    for t in res["tiers"]:
        want = res["mean_gross_pp"] - t["cost_pp_month"]
        if abs(t["net_pp_month"] - want) > 1e-12:
            out.append(
                {"code": "G3_NET_IDENTITY", "detail": f"{t['id']}: {t['net_pp_month']!r} ≠ {want!r}"}
            )

    # G4：判定字符串与 phasei 一致（本阶段不得产生新 verdict）
    if res["phasei_verdict"] != "EXQ5_COST_ABSORBED":
        out.append(
            {
                "code": "G4_VERDICT_CHANGED",
                "detail": f"phasei verdict={res['phasei_verdict']!r}，与预期 EXQ5_COST_ABSORBED 不同",
            }
        )

    # G5：se_iid 与 summary 反推一致。
    # ⚠️ 反推必须用**产物自己的** net 与 t_net，不能用我算的 t：
    # 若用 `c01["net_pp_month"] / c01["t_iid"]`，那么把 iid_se 改成 ddof=0 时
    # 分子分母同时变，两者仍然相等——闸门放过（变异 M3 首轮就是这么漏的，
    # 与 L3ac 的 M6 完全同型：**闸门不能拿被测函数的结果当期望值**）。
    p01 = res["phasei_0p1"]
    se_from_summary = float(p01["net"]) / float(p01["t"])
    # 容差与 G1b 同源：反推用的 net 与 t 都是 6 位小数的舍入值，
    # 所以这个反推本身只精确到 storage_floor(net) 量级（≈4e-6）。
    # 用 1e-6 会造成「判据不可满足」，与 D1 同一类错误——这里直接用可达值。
    tol = storage_floor(float(p01["net"])) * 1.5
    if abs(res["se_iid_pp"] - se_from_summary) / abs(se_from_summary) > tol:
        out.append(
            {
                "code": "G5_SE_MISMATCH",
                "detail": f"独立算 {res['se_iid_pp']!r} vs 由**产物** net/t 反推 "
                f"{se_from_summary!r}（容差 {tol:.2e}）",
            }
        )
    # 且 se 与 rate 无关：所有档的 t 比必须一致
    tr = [t["t_nw3"] / t["t_iid"] for t in res["tiers"] if t["t_iid"]]
    if tr and (max(tr) - min(tr)) > 1e-12:
        out.append({"code": "G5_SE_VARIES_WITH_RATE", "detail": f"t 比不恒定：{min(tr)!r}~{max(tr)!r}"})

    # G6：t_nw3 / t_iid 恒等于 se_iid / se_nw3
    want_ratio = res["se_iid_pp"] / res["se_nw3_pp"]
    for t in res["tiers"]:
        if t["t_iid"] and t["t_nw3"]:
            if abs(t["t_nw3"] / t["t_iid"] - want_ratio) > 1e-12:
                out.append(
                    {"code": "G6_T_RATIO", "detail": f"{t['id']}: {t['t_nw3'] / t['t_iid']!r} ≠ {want_ratio!r}"}
                )

    # G7：NW(3) 与 summary 的 se 一致（证明我用的是同一套估计量）
    if abs(res["se_nw3_pp"] - res["summary_se_nw_pp"]) / res["summary_se_nw_pp"] > 1e-3:
        out.append(
            {
                "code": "G7_NW_SE_MISMATCH",
                "detail": f"重算 {res['se_nw3_pp']!r} vs summary {res['summary_se_nw_pp']!r}",
            }
        )

    # G8：单笔金额达到 20,000 元时含最低佣金的等效费率回落为 0.051%
    for m in res["min_commission"]:
        if m["notional_per_stock"] >= MIN_COMMISSION / COMM_RATE:
            if abs(m["equivalent_single_side"] - equivalent_single_side()) > 1e-9:
                out.append(
                    {
                        "code": "G8_MIN_COMMISSION_THRESHOLD",
                        "detail": f"资金 {m['capital']:.0f} 单笔 {m['notional_per_stock']:.0f} 元，"
                        f"等效费率 {m['equivalent_single_side']!r} ≠ 0.00051",
                    }
                )

    # G9：逐月重算的均值必须与 summary 的 mean_pp 在存储精度内一致。
    # 这一条防的是「读错文件」——若 monthly.csv 与 summary 不是同一次实验，均值会对不上，
    # 而后面所有档位都会安静地用错数据。
    floor_m = storage_floor(res["summary_mean_pp"])
    rel_m = abs(res["mean_gross_pp"] - res["summary_mean_pp"]) / abs(res["summary_mean_pp"])
    if rel_m > floor_m * 1.5:
        out.append(
            {
                "code": "G9_MEAN_SOURCE_MISMATCH",
                "detail": f"逐月均值 {res['mean_gross_pp']!r} vs summary {res['summary_mean_pp']!r}"
                f"（相对 {rel_m:.3e} > 存储下限 {floor_m:.3e} 的 1.5 倍）",
            }
        )

    return out


def check_predictions(res: Mapping[str, Any], prereg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """先验预测逐条核对。判据在写预登记时就定死，这里只执行。"""
    by_id = {t["id"]: t for t in res["tiers"]}
    base = by_id["T_base_1e8"]
    m3 = next(m for m in res["min_commission"] if m["tier"] == "T_base_3e7")
    m1 = next(m for m in res["min_commission"] if m["tier"] == "T_base_1e7")
    t3 = by_id["T_base_3e7"]
    t1 = by_id["T_base_1e7"]
    t03 = by_id["T_0p3"]

    preds: list[dict[str, Any]] = []

    worst = max((r["rel_diff"] for r in res["reproduction"]), default=0.0)
    preds.append(
        {
            "id": "P1",
            "timing": "priori",
            "claim": "两档既有档逐位复现（相对偏差 ≤ 1e-6）",
            "observed": f"最大相对偏差 {worst:.3e}；存储下限 {res['g1_floor']:.3e}"
            f"（判据 {'可达' if res['g1_floor'] < 1e-6 else '**不可满足**'}）",
            "holds": worst <= 1e-6,
            "note": "判据不可满足：产物 net 存 6 位小数，相对下限已达 4e-6。"
            "实测偏差落在舍入范围内 ⇒ 属判据缺陷 D1，非实现错误",
        }
    )
    preds.append(
        {
            "id": "P2",
            "timing": "priori",
            "claim": "0.051% 档 t_iid ∈ [2.90,3.05]、net ∈ [0.1435,0.1449]",
            "observed": f"t_iid={base['t_iid']:.4f}、net={base['net_pp_month']:.6f}",
            "holds": 2.90 <= base["t_iid"] <= 3.05 and 0.1435 <= base["net_pp_month"] <= 0.1449,
        }
    )
    preds.append(
        {
            "id": "P3",
            "timing": "priori",
            "claim": "0.051% 档 t_nw3 ∈ [3.90,4.10]",
            "observed": f"t_nw3={base['t_nw3']:.4f}（比值 {base['t_nw3'] / base['t_iid']:.4f}）",
            "holds": 3.90 <= base["t_nw3"] <= 4.10,
        }
    )
    preds.append(
        {
            "id": "P4",
            "timing": "priori",
            "claim": "两档含最低佣金的档位 t_iid 均 > 1.96",
            "observed": f"3000万 {t3['t_iid']:.4f}、1000万 {t1['t_iid']:.4f}",
            "holds": t3["t_iid"] > 1.96 and t1["t_iid"] > 1.96,
        }
    )
    preds.append(
        {
            "id": "P5",
            "timing": "priori",
            "claim": "phasei verdict 不变",
            "observed": res["phasei_verdict"],
            "holds": res["phasei_verdict"] == "EXQ5_COST_ABSORBED",
        }
    )
    preds.append(
        {
            "id": "P6",
            "timing": "priori",
            "claim": "0.3% 档 NW(3) t 仍 < 1.96",
            "observed": f"t_nw3={t03['t_nw3']:.4f}",
            "holds": t03["t_nw3"] < 1.96,
        }
    )
    preds.append(
        {
            "id": "P7",
            "timing": "descriptive",
            "claim": "最低佣金摆脱点：单笔 ≥ 20,000 元",
            "observed": f"n_keep 中位 {res['n_keep_median']} 只 ⇒ W ≥ "
            f"{MIN_COMMISSION / COMM_RATE * res['n_keep_median'] / 1e4:.0f} 万元",
            "holds": True,
        }
    )
    return preds
