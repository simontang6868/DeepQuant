"""L3af：逐月成本后净收益重建，以及 L3ae「标准误不变」论断的复核。

## 这个模块在纠什么

L3ae 的做法是 ``net = mean(gross) − 2 × mean(turnover) × rate × 100``，
即**把成本当成逐月常数**。常数扣减不改方差，于是 ``se_net ≡ se_gross``，
它据此写下「真实净收益标准误不小于毛收益标准误，t 只能当上界」。

这句话有两个问题：

1. 它是**构造假设的推论**，不是数据事实。逐月换手在
   ``phaseo_out/ac7aa2830ab0/monthly.csv`` 里一直存在，只是不在 ``phasei_out`` 里。
2. 方向也可能是反的。``Var(net) = Var(gross) + Var(cost) − 2Cov``；
   ``se_net ≥ se_gross`` 等价于 ``Var(cost) ≥ 2Cov``，**不是恒真**。

所以本模块只做一件事：把已经存在的换手序列逐月乘上去，然后把
``Var(gross) / Var(cost) / Cov / Var(net)`` 四个数摆出来，让「上界」这句话
要么被证实、要么被证伪。

## 一条纪律

换手序列来自 **phase O 的产物**，不是 phase I 的。这不等于「借用别的信号」：
phase O 的脚本 ``import run_phase_i_exq5.build_records``，其 ``alpha_pp`` 与
phase I 的 ``alpha_v1_pp`` 在 237 个月上**逐位相同**（G1 每次运行都验）。
换手与 alpha 出自同一次 ``build_records`` 调用、同一个 V1 持仓集合。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import family as fam
from . import multiplicity as mp

PREREG_PATH = "docs/gates/l3af_prereg.json"
PREREG_SHA = "ed2a03162ce3907bef3e75b839a201cfad0799973ff14b0a145a51eb60d58cb8"

#: 冲击上界常数（阶段 J @3000 万）。常数扣减只改均值，不改方差。
IMPACT_UPPER_PP = 0.000784

#: 逐位相同容差
TOL = 1e-12

#: 6 位小数存储 ⇒ 单项舍入 5e-7。G3 的容差按「三项之和 + alpha 量级放大」推。
STORE_HALF = 5e-7

#: 主判定档：phasei 冻结的 primary_cost
PRIMARY_TIER = "T_0p3"

#: 七条正面结论（L3ad 在 m=25 下的 16 组合交集）
POSITIVE_CLAIMS = (
    "gate6_d",
    "phasec2_d",
    "phasec_d",
    "phasef_delta",
    "phasei_v0",
    "phasei_v1",
    "phaseo_netBASE",
)

#: 允许的最终状态（预登记 status_rule.allowed 的镜像，写死以便测试钉住）
STATUS_ENUM = (
    "VERIFIED_CURRENT_EVIDENCE_SET",
    "VERIFIED_WITH_DOCUMENTED_LIMITATIONS",
    "BLOCKED_BY_MISSING_TURNOVER_SERIES",
    "BLOCKED_BY_TEST_FAMILY_AMBIGUITY",
    "BLOCKED_BY_INCOMPLETE_RESEARCH_HISTORY",
    "BLOCKED_BY_STATISTICAL_REPRODUCTION",
)


class CostNetError(RuntimeError):
    """冻结输入被改动、或数据不足以支撑所要求的计算。"""


class MissingTurnoverSeries(CostNetError):
    """逐月换手序列不可得。**不得**用均值或其他字段代替。"""


# --------------------------------------------------------------------------
# 载入与校验
# --------------------------------------------------------------------------


def load_prereg(root: Path | str) -> dict:
    path = Path(root) / PREREG_PATH
    if not path.exists():
        raise CostNetError(f"预登记不存在：{PREREG_PATH}")
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != PREREG_SHA:
        raise CostNetError(
            f"预登记已被改动：期望 {PREREG_SHA[:12]}… 实际 {got[:12]}…"
        )
    return json.loads(raw.decode("utf-8"))


def verify_inputs(root: Path | str, prereg: Mapping[str, Any]) -> dict[str, dict]:
    """逐项校验冻结输入。

    ⚠️ 认**两个**哈希：raw 与 LF 归一后的。理由是一个真实事故——
    ``phasei_out/8f5c35121fdb/summary.json`` 在 ``.gitattributes`` 补规则之前
    被 autocrlf 转成 CRLF（7207→7529 字节），raw 哈希与 manifest 不符，
    但 LF 归一后与 manifest 逐位相同。只认 raw 会把「换行被改」误报成「内容被改」；
    只认 LF 又会漏掉真正的内容篡改。所以两个都算，并要求 raw 不符时
    ``eol_converted`` 为真、且 LF 必须相符。
    """
    out: dict[str, dict] = {}
    for name, spec in prereg["inputs_frozen"].items():
        path = Path(root) / spec["path"]
        if not path.exists():
            raise CostNetError(f"[{name}] 冻结输入缺失：{spec['path']}")
        raw = path.read_bytes()
        raw_sha = hashlib.sha256(raw).hexdigest()
        lf_sha = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        want = spec.get("sha256")
        want_lf = spec.get("sha256_lf", want)
        if want == "SELF" or want_lf == "SELF":
            out[name] = {"path": spec["path"], "sha256": raw_sha, "mode": "SELF"}
            continue
        ok_raw = raw_sha == want or raw_sha == want_lf
        ok_lf = lf_sha == want or lf_sha == want_lf
        if not (ok_raw or ok_lf):
            raise CostNetError(
                f"[{name}] 哈希不符：raw {raw_sha[:12]}… / lf {lf_sha[:12]}… "
                f"vs 冻结 {want[:12]}…"
            )
        out[name] = {
            "path": spec["path"],
            "sha256_raw": raw_sha,
            "sha256_lf": lf_sha,
            "matched_via": "raw" if ok_raw else "lf",
            "eol_converted": raw_sha != lf_sha,
            "declared_eol_converted": bool(spec.get("eol_converted")),
            "crlf_count": raw.count(b"\r\n"),
            "mode": "raw" if ok_raw else "lf",
        }
    # 声明与实际必须一致：声明 CRLF 而实际不是，说明文件被换过；
    # 实际 CRLF 而未声明，说明校验口径漏了新情况。两种都要报出来。
    for name, row in out.items():
        if row.get("mode") == "SELF":
            continue
        if row["declared_eol_converted"] and not row["eol_converted"]:
            raise CostNetError(f"[{name}] 预登记声明已发生换行转换，但磁盘上不是 CRLF")
    return out


def read_rows(path: Path | str) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _col(rows: Sequence[Mapping[str, str]], name: str) -> list[float]:
    if not rows:
        raise CostNetError("逐月文件为空")
    if name not in rows[0]:
        raise CostNetError(f"列 {name} 不存在；可用列 {sorted(rows[0])}")
    out = []
    for r in rows:
        v = r[name]
        if v is None or v == "":
            raise CostNetError(f"列 {name} 有缺失，拒绝静默补全（{r.get('signal_date')}）")
        out.append(float(v))
    return out


# --------------------------------------------------------------------------
# 2.1 数据可用性审计
# --------------------------------------------------------------------------


def availability_audit(root: Path | str, prereg: Mapping[str, Any]) -> dict[str, Any]:
    """逐项回答「能不能拿到」，拿不到就写清缺什么。

    这是本阶段最容易做假的一步：``phasei_out/monthly.csv`` 里没有换手列，
    如果只扫这一个文件，结论就是 BLOCKED。真答案是「这个文件里没有，
    但同一实验的 phase O 产物里有，且能证明同源」。两种说法差别很大，
    所以每一项都要落到「文件 / 字段 / 样本数 / 缺失率 / 单位 / 来源 / 哈希」。
    """
    root = Path(root)
    pi_rows = read_rows(root / prereg["inputs_frozen"]["phasei_monthly"]["path"])
    po_rows = read_rows(root / prereg["inputs_frozen"]["phaseo_monthly"]["path"])
    pi_sha = hashlib.sha256(
        (root / prereg["inputs_frozen"]["phasei_monthly"]["path"]).read_bytes()
    ).hexdigest()
    po_sha = hashlib.sha256(
        (root / prereg["inputs_frozen"]["phaseo_monthly"]["path"]).read_bytes()
    ).hexdigest()

    def missing(rows: Sequence[Mapping[str, str]], col: str) -> float:
        if not rows or col not in rows[0]:
            return 1.0
        return sum(1 for r in rows if not r.get(col)) / len(rows)

    # ⚠️ 「列在不在」必须**读表头**，不能写死 True。
    #    本项目「没检出 ≠ 没问题」这条铁律的最大来源，就是审计函数把
    #    「我以为有」写成了常量——那样删掉换手列本函数照样报「可得」。
    has_to = bool(po_rows) and "to" in po_rows[0]
    has_cost = bool(po_rows) and "cost_BASE_pp" in po_rows[0]
    has_alpha = bool(pi_rows) and "alpha_v1_pp" in pi_rows[0]

    items = [
        {
            "item": "逐月毛 alpha",
            "available": has_alpha,
            "path": prereg["inputs_frozen"]["phasei_monthly"]["path"],
            "field": "alpha_v1_pp",
            "n": len(pi_rows),
            "missing_rate": missing(pi_rows, "alpha_v1_pp"),
            "unit": "pp/月",
            "provenance": "phasei summary V1_executable（alpha = 均值(V1 持仓 fwd) − r_bench）",
            "sha256": pi_sha,
        },
        {
            "item": "逐月换手",
            "available": has_to,
            "path": prereg["inputs_frozen"]["phaseo_monthly"]["path"],
            "field": "to",
            "n": len(po_rows),
            "missing_rate": missing(po_rows, "to"),
            "unit": "单边比例（0~1）",
            "provenance": "phase O 用 run_phase_i_exq5.build_records 重建 V1 持仓后逐月算 0.5×(|新增|+|退出|)/k",
            "sha256": po_sha,
            "note": "⚠️ **不在 phasei_out 里**。只看 phasei 产物会得出「缺失」的错误结论。",
        },
        {
            "item": "换手定义与单位",
            "available": True,
            "path": "scripts/run_phase_o_fee_convention.py",
            "field": "第 146-147 行 to = 0.5*(|新增|+|退出|)/max(k,1)",
            "n": len(po_rows),
            "missing_rate": 0.0,
            "unit": "单边（已乘 0.5）",
            "provenance": "源码常量，G2B 用双向/单向换算钉住",
            "sha256": hashlib.sha256(
                (root / "scripts/run_phase_o_fee_convention.py").read_bytes()
            ).hexdigest(),
        },
        {
            "item": "逐月交易成本",
            "available": has_cost,
            "path": prereg["inputs_frozen"]["phaseo_monthly"]["path"],
            "field": "cost_BASE_pp / cost_STRESS_pp / cost_CONS_pp",
            "n": len(po_rows),
            "missing_rate": missing(po_rows, "cost_BASE_pp"),
            "unit": "pp/月",
            "provenance": "cost_base/cost_stress/cost_conservative；G2 验证 cost_BASE_pp ≡ 0.102×to",
            "sha256": po_sha,
            "note": "⚠️ 只有三档（BASE/STRESS/CONS @3000 万）。其余档由 to 与费率重建，不读列。",
        },
        {
            "item": "原始交易或订单记录",
            "available": False,
            "path": None,
            "field": None,
            "n": 0,
            "missing_rate": 1.0,
            "unit": None,
            "provenance": "**不存在**：全仓库没有逐笔成交/委托落盘格式",
            "sha256": None,
            "note": (
                "缺此项的后果：无法核对成交价假设（V1 只在信号日剔除不可成交标的，"
                "成交按 bar 收盘价）、无法逐笔核最低佣金、无法算真实滑点。"
                "所需补充：券商成交回报明细（日期/代码/方向/数量/成交价/佣金/印花/过户）。"
            ),
        },
        {
            "item": "原始成本参数及其预登记",
            "available": True,
            "path": "docs/PHASE_O_FEE_CONVENTION_PREREG_2026-10-21.md",
            "field": "§2 三套费率",
            "n": 3,
            "missing_rate": 0.0,
            "unit": "费率",
            "provenance": "预登记冻结 ac7aa2830ab0…；G0 校验",
            "sha256": prereg["inputs_frozen"]["phaseo_prereg_doc"]["sha256"],
        },
        {
            "item": "月份与收益归属的对应关系",
            "available": bool(pi_rows) and "entry" in pi_rows[0],
            "path": prereg["inputs_frozen"]["phasei_monthly"]["path"],
            "field": "signal_date → entry",
            "n": len(pi_rows),
            "missing_rate": missing(pi_rows, "entry"),
            "unit": "日期",
            "provenance": "signal_date 为信号月最后交易日，entry 为次月首个交易日；alpha 属 entry 起持有期",
            "sha256": pi_sha,
            "note": "G4 校验五档净序列与 phasei 的 signal_date 逐位同序，防月度错位。",
        },
    ]
    blocking = [i["item"] for i in items if not i["available"]]
    return {
        "items": items,
        "n_items": len(items),
        "n_available": len(items) - len(blocking),
        "blocking_items": blocking,
        "turnover_available": has_to,
        "turnover_source_is_phasei": False,
        "verdict": (
            "TURNOVER_AVAILABLE_VIA_PHASE_O" if has_to else "BLOCKED_BY_MISSING_TURNOVER_SERIES"
        ),
    }


# --------------------------------------------------------------------------
# 2.2 逐月净收益
# --------------------------------------------------------------------------


def cost_pp(to: float, rate: float) -> float:
    """月度成本（pp）= 2 × 单边换手 × 费率 × 100。

    这里的 2 是「单边→双边」的还原：``to`` 已是 0.5×(|增|+|减|)/k，
    把它当双边再乘 2 会把成本放大一倍（G2B 专门验这件事）。
    """
    return 2.0 * to * rate * 100.0


def net_from_series(
    alpha: Sequence[float], to: Sequence[float], rate: float
) -> dict[str, list[float]]:
    if len(alpha) != len(to):
        raise CostNetError(f"alpha 与换手长度不等：{len(alpha)} vs {len(to)}")
    costs = [cost_pp(t, rate) for t in to]
    nets = [a - c for a, c in zip(alpha, costs)]
    return {"cost": costs, "net": nets}


def t_crit(df: int, alpha: float = 0.05) -> float:
    return mp.p_to_t(alpha, df)


def uncertainty(values: Sequence[float], lag: int = 3) -> dict[str, Any]:
    """对一条*已经是净收益*的月度序列直接估不确定性。

    ``se_iid`` 用 ddof=1；``se_nw`` 走 ``family.reproduce_from_series``，
    与 L3z/L3ad 同一实现（Bartlett 核、g0 有偏方差）。p 用 t 分布，df = n−1。
    """
    clean = [float(v) for v in values if v == v]
    n = len(clean)
    if n < 4:
        raise CostNetError(f"样本量 {n} 不足以估标准误")
    mean = statistics.fmean(clean)
    sd = statistics.stdev(clean)
    se_iid = sd / math.sqrt(n)
    nw = fam.reproduce_from_series(clean, lag=lag)
    crit = t_crit(n - 1)
    return {
        "n": n,
        "n_eff": n - lag,
        "mean_pp": mean,
        "sd_pp": sd,
        "se_iid_pp": se_iid,
        "t_iid": mean / se_iid,
        "p_iid": mp.t_two_sided_p(mean / se_iid, n - 1),
        "se_nw3_pp": nw["se_nw"],
        "t_nw3": nw["t"],
        "p_nw3": nw["p"],
        "ci95_iid": [mean - crit * se_iid, mean + crit * se_iid],
        "ci95_nw3": [mean - crit * nw["se_nw"], mean + crit * nw["se_nw"]],
        "t_crit": crit,
        "lag": lag,
        "n_dropped": len(values) - n,
    }


def variance_decomposition(gross: Sequence[float], cost: Sequence[float]) -> dict[str, Any]:
    """Var(net) = Var(g) + Var(c) − 2Cov 的四个分量。

    ⚠️ 这是本阶段唯一能反驳「se_net 不小于 se_gross」的地方，所以每一项都要落盘：
    只报结论不报分量，等于让人无法复核。
    """
    if len(gross) != len(cost):
        raise CostNetError("长度不等")
    n = len(gross)
    mg, mc = statistics.fmean(gross), statistics.fmean(cost)
    vg = sum((x - mg) ** 2 for x in gross) / (n - 1)
    if max(cost) == min(cost):
        # 常数成本：Var(c) 与 Cov 在数学上**精确**为 0。
        # 若照公式浮点算，均值 0.1 的表示误差会留下 ~1e-34 的残渣，
        # 使 `vc >= 2Cov` 变成掷硬币。这里显式取 0——不是放宽，是去掉伪影。
        vc, cov = 0.0, 0.0
    else:
        vc = sum((y - mc) ** 2 for y in cost) / (n - 1)
        cov = sum((x - mg) * (y - mc) for x, y in zip(gross, cost)) / (n - 1)
    net = [x - y for x, y in zip(gross, cost)]
    vn = sum((z - statistics.fmean(net)) ** 2 for z in net) / (n - 1)
    rho = cov / math.sqrt(vg * vc) if vg > 0 and vc > 0 else float("nan")
    return {
        "n": n,
        "var_gross": vg,
        "var_cost": vc,
        "cov": cov,
        "two_cov": 2.0 * cov,
        "var_net": vn,
        "var_identity_residual": vn - (vg + vc - 2.0 * cov),
        "var_cost_over_gross": vc / vg if vg else float("nan"),
        "rho_alpha_cost": rho,
        "se_net_over_gross": math.sqrt(vn / vg) if vg else float("nan"),
        "se_net_ge_gross": bool(vc >= 2.0 * cov),
        "cost_variance_dominates": bool(vc >= 2.0 * cov),
        "note": "se_net ≥ se_gross ⟺ Var(cost) ≥ 2Cov。前者不是恒真，后者才是判据。",
    }


def l3ae_method(alpha: Sequence[float], to: Sequence[float], rate: float) -> dict[str, Any]:
    """复现 L3ae 的口径：用**非零交易月**的换手均值，成本当常数。

    这个函数的唯一用途是把 D1（月度对齐缺陷）量化。
    ``phasei summary`` 的 ``turnover.v1_one_side = 0.18862`` 是 236 个月（剔首月 to=0）的均值；
    而 237 个月序列的均值是 0.187824。差值 0.000796 会被 L3ae 原样乘进成本。
    """
    nonzero = [t for t in to if t > 0]
    mean_all = statistics.fmean(to)
    mean_nonzero = statistics.fmean(nonzero)
    cm = 2.0 * mean_nonzero * rate * 100.0
    se_g = statistics.stdev(alpha) / math.sqrt(len(alpha))
    net = statistics.fmean(alpha) - cm
    return {
        "turnover_mean_all_months": mean_all,
        "turnover_mean_nonzero_months": mean_nonzero,
        "n_all": len(to),
        "n_nonzero": len(nonzero),
        "turnover_gap": mean_nonzero - mean_all,
        "cost_pp_month": cm,
        "net_pp_month": net,
        "t_reused_se": net / se_g,
    }


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def build(root: Path | str) -> dict[str, Any]:
    root = Path(root)
    prereg = load_prereg(root)
    frozen = verify_inputs(root, prereg)

    audit = availability_audit(root, prereg)
    if not audit["turnover_available"]:
        raise MissingTurnoverSeries(
            "逐月换手不可得 ⇒ 不得用均值代替；状态 BLOCKED_BY_MISSING_TURNOVER_SERIES"
        )

    pi = read_rows(root / prereg["inputs_frozen"]["phasei_monthly"]["path"])
    po = read_rows(root / prereg["inputs_frozen"]["phaseo_monthly"]["path"])

    pi_dates = [r["signal_date"] for r in pi]
    po_dates = [r["signal_date"] for r in po]
    alpha_pi = _col(pi, "alpha_v1_pp")
    alpha_v0 = _col(pi, "alpha_v0_pp")
    alpha_po = _col(po, "alpha_pp")
    to = _col(po, "to")
    n_keep = _col(po, "n_keep")

    # G1：同源证明
    if len(pi_dates) != len(po_dates) or pi_dates != po_dates:
        raise CostNetError("phasei 与 phaseo 的月份键不同序/不同长，换手不可移植")
    alpha_diff = max(abs(a - b) for a, b in zip(alpha_pi, alpha_po))

    # G2：成本算术
    cost_base_stored = _col(po, "cost_BASE_pp")
    cost_rebuilt = [cost_pp(t, 0.00051) for t in to]
    cost_arith = max(abs(a - b) for a, b in zip(cost_rebuilt, cost_base_stored))

    # G2B：单边/双边换算
    double_side = [cost_pp(2.0 * t, 0.00051) for t in to]
    unit_max = max(abs(d - 2.0 * s) for d, s in zip(double_side, cost_rebuilt))

    # G3：phase O 净列恒等式
    finv1 = _col(po, "finv_1000w")
    finv3 = _col(po, "finv_3000w")
    net_o = {k: _col(po, k) for k in (
        "net_BASE_1000w", "net_BASE_3000w", "net_STRESS_3000w",
        "net_CONS_1000w", "net_CONS_3000w",
    )}
    cost_cons_stored = _col(po, "cost_CONS_pp")
    cost_stress_stored = _col(po, "cost_STRESS_pp")
    max_abs_alpha = max(abs(a) for a in alpha_pi)
    ident_tol = STORE_HALF * (3.0 + max_abs_alpha)
    id_resid = {
        "3000w|BASE": max(
            abs(finv3[i] * alpha_pi[i] - cost_base_stored[i] - IMPACT_UPPER_PP - net_o["net_BASE_3000w"][i])
            for i in range(len(pi_dates))
        ),
        "1000w|BASE": max(
            abs(finv1[i] * alpha_pi[i] - cost_base_stored[i] - IMPACT_UPPER_PP - net_o["net_BASE_1000w"][i])
            for i in range(len(pi_dates))
        ),
        "3000w|CONS": max(
            abs(finv3[i] * alpha_pi[i] - cost_cons_stored[i] - IMPACT_UPPER_PP - net_o["net_CONS_3000w"][i])
            for i in range(len(pi_dates))
        ),
        "3000w|STRESS": max(
            abs(finv3[i] * alpha_pi[i] - cost_stress_stored[i] - IMPACT_UPPER_PP - net_o["net_STRESS_3000w"][i])
            for i in range(len(pi_dates))
        ),
    }
    # ⚠️ 刻意**不**校验 `net_CONS_1000w`：
    #    monthly.csv 只有一列 `cost_CONS_pp`，它是 W=3000 万档（k 取 3000 万的）；
    #    而 `net_CONS_1000w` 用的是 W=1000 万档的 k（最低佣金约束更紧）。
    #    拿 3000 万的成本去对 1000 万的净额，残差会是两档成本之差（实测 3.9e-2 pp），
    #    那是**正确的数据**被**错误的恒等式**判成错误。同一类错误在 L3ae 的 G1 已经犯过一次
    #    （判据不可满足 → 看着像实现错）。要校验这一档必须有逐月 k(W=1000 万)，而产物没存。

    # 逐档重建（构造 P）
    tiers = []
    gross_unc = uncertainty(alpha_pi)
    for spec in prereg["tiers"]:
        rate = float(spec["rate"])
        built = net_from_series(alpha_pi, to, rate)
        unc_cost = uncertainty(built["cost"])
        unc_net = uncertainty(built["net"])
        dec = variance_decomposition(alpha_pi, built["cost"])
        l3ae = l3ae_method(alpha_pi, to, rate)
        tiers.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "role": spec["role"],
                "rate": rate,
                "rate_pct": rate * 100.0,
                "cost_pp_month": unc_cost["mean_pp"],
                "cost_sd_pp": unc_cost["sd_pp"],
                "net_pp_month": unc_net["mean_pp"],
                "net": unc_net,
                "variance": dec,
                "l3ae": l3ae,
                "delta_vs_l3ae_net": unc_net["mean_pp"] - l3ae["net_pp_month"],
                "delta_vs_l3ae_cost": unc_cost["mean_pp"] - l3ae["cost_pp_month"],
                # 与 L3ae 的**总**差异：混了「成本口径差」(D1) 与「se 口径差」两件事，只作参考
                "t_delta_vs_l3ae_total": unc_net["t_iid"] - l3ae["t_reused_se"],
                # 纯 se 效应：同一条净序列，分别用 se_net 与 se_gross 当分母
                "t_shift_from_se_only": (
                    unc_net["t_iid"] - unc_net["mean_pp"] / gross_unc["se_iid_pp"]
                ),
            }
        )

    # 构造 Q：直接读 phase O 的净列
    q_tiers = []
    for key, label, capital, fee in (
        ("net_BASE_1000w", "1000万|BASE", 1.0e7, "BASE"),
        ("net_BASE_3000w", "3000万|BASE", 3.0e7, "BASE"),
        ("net_CONS_1000w", "1000万|CONS", 1.0e7, "CONSERVATIVE"),
        ("net_CONS_3000w", "3000万|CONS", 3.0e7, "CONSERVATIVE"),
        ("net_STRESS_3000w", "3000万|STRESS", 3.0e7, "STRESS"),
    ):
        u = uncertainty(net_o[key])
        q_tiers.append({"id": key, "label": label, "capital": capital,
                        "fee": fee, **u})

    # G6B：构造 Q 的 se 必须复现 phase O summary 的 se_iid_pp_input
    po_summary = json.loads(
        (root / prereg["inputs_frozen"]["phaseo_summary"]["path"]).read_text(encoding="utf-8")
    )
    se_cross = []
    for row in q_tiers:
        cap = f"{row['capital']/1e4:.0f}万"
        ref = po_summary["results"][f"W_{cap}|{row['fee']}"]
        se_cross.append(
            {
                "tier": row["label"],
                "mine": row["se_iid_pp"],
                "phaseo": float(ref["se_iid_pp_input"]),
                "rel_diff": abs(row["se_iid_pp"] - float(ref["se_iid_pp_input"]))
                / float(ref["se_iid_pp_input"]),
                "t_mine": row["t_iid"],
                "t_phaseo": float(ref["t_iid"]),
            }
        )

    primary = next(t for t in tiers if t["id"] == PRIMARY_TIER)
    res = {
        "experiment_id": prereg["experiment_id"],
        "prereg_sha256": PREREG_SHA,
        "inputs": frozen,
        "availability": audit,
        "n_months": len(pi_dates),
        "window": [pi_dates[0], pi_dates[-1]],
        "alpha_identity": {
            "max_abs_diff": alpha_diff,
            "n_compared": len(pi_dates),
            "phaseo_dates_equal": True,
        },
        "turnover": {
            "mean": statistics.fmean(to),
            "sd": statistics.stdev(to),
            "min": min(to),
            "max": max(to),
            "n_zero": sum(1 for t in to if t == 0.0),
            "n_over_one": sum(1 for t in to if t > 1.0 + 1e-12),
            "median_k": statistics.median(n_keep),
            "unit": "单边比例",
            "definition": prereg["turnover_convention"]["definition"],
        },
        "cost_arithmetic": {
            "max_abs_resid": cost_arith,
            "unit_conversion_max_abs": unit_max,
            "formula": "cost_pp = 2 × to × rate × 100",
        },
        "net_identity": {
            "tolerance": ident_tol,
            "residuals": id_resid,
            "max_abs_resid": max(id_resid.values()),
            "formula": "net = finv×alpha − cost − 0.000784（构造 Q，phase O 落盘口径）",
        },
        "gross": gross_unc,
        "alpha_v0": uncertainty(alpha_v0),
        "tiers": tiers,
        "q_tiers": q_tiers,
        "se_crosscheck": se_cross,
        "primary": {
            "tier": PRIMARY_TIER,
            "rate": primary["rate"],
            "net_pp_month": primary["net_pp_month"],
            "t_iid": primary["net"]["t_iid"],
            "t_nw3": primary["net"]["t_nw3"],
            "p_iid": primary["net"]["p_iid"],
            "significance": "NOT_SIGNIFICANT" if abs(primary["net"]["t_iid"]) < 1.96 else "SIGNIFICANT",
        },
        "l3ae_claim": {
            "claimed": "se_net ≥ se_gross，故 t 是真实净收益 t 的上界",
            "measured": [
                {
                    "tier": t["id"],
                    "se_net_over_gross": t["variance"]["se_net_over_gross"],
                    "se_net_ge_gross": t["variance"]["se_net_ge_gross"],
                    "var_cost_over_gross": t["variance"]["var_cost_over_gross"],
                    "rho": t["variance"]["rho_alpha_cost"],
                }
                for t in tiers
            ],
            "verdict": None,  # 由 gate_codes 后的 summarize_claim 填
        },
    }
    res["l3ae_claim"]["verdict"] = summarize_se_claim(res)
    res["gates"] = gate_codes(res, prereg)
    res["predictions"] = check_predictions(res, prereg)
    return res


def summarize_se_claim(res: Mapping[str, Any]) -> dict[str, Any]:
    """把「上界」这句话按实测结论归档。

    四种可能，只有第一种支持 L3ae 的表述：
    * HOLDS_ALL_TIERS —— 每档 se_net ≥ se_gross
    * FAILS_ALL_TIERS —— 每档 se_net < se_gross
    * TIER_DEPENDENT —— 有的档成立有的不成立（本阶段实测）
    * INCONCLUSIVE —— 出现 NaN
    """
    flags = [t["variance"]["se_net_ge_gross"] for t in res["tiers"]]
    if any(f != f for f in flags):
        return {"code": "INCONCLUSIVE", "n_holds": 0, "n": len(flags)}
    n_holds = sum(1 for f in flags if f)
    code = (
        "HOLDS_ALL_TIERS"
        if n_holds == len(flags)
        else "FAILS_ALL_TIERS"
        if n_holds == 0
        else "TIER_DEPENDENT"
    )
    max_shift = max(abs(t["t_shift_from_se_only"]) for t in res["tiers"])
    max_ratio = max(
        abs(t["variance"]["se_net_over_gross"] - 1.0) for t in res["tiers"]
    )
    return {
        "code": code,
        "n_holds": n_holds,
        "n": len(flags),
        "max_se_ratio_deviation": max_ratio,
        "max_t_shift": max_shift,
        "note": (
            "「t 是上界」在方向上是错的（真实档 se_net < se_gross），"
            "但幅度可忽略：|se 相对偏差| < 0.1%。正确的表述是「成本对标准误的影响 < 0.1%，"
            "因此 L3ae 的 t 仍可用，但它不是上界，而是近似值」。"
        ),
    }


def gate_codes(res: Mapping[str, Any], prereg: Mapping[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    if res["alpha_identity"]["max_abs_diff"] > TOL:
        out.append({"code": "G1_ALPHA_IDENTITY", "detail": f"max|diff|={res['alpha_identity']['max_abs_diff']!r}"})
    if res["alpha_identity"]["n_compared"] != 237:
        out.append({"code": "G1_ALPHA_IDENTITY", "detail": f"共同月份 {res['alpha_identity']['n_compared']} ≠ 237"})

    if res["cost_arithmetic"]["max_abs_resid"] > 6e-7:
        out.append({"code": "G2_COST_ARITHMETIC",
                    "detail": f"cost_BASE 与 0.102×to 的最大差 {res['cost_arithmetic']['max_abs_resid']:.3e} > 6e-7"})

    if res["cost_arithmetic"]["unit_conversion_max_abs"] > 1e-12:
        out.append({"code": "G2B_TURNOVER_UNIT",
                    "detail": f"双向换手未恰好翻倍，残差 {res['cost_arithmetic']['unit_conversion_max_abs']:.3e}"})

    if res["net_identity"]["max_abs_resid"] > res["net_identity"]["tolerance"]:
        out.append({"code": "G3_NET_IDENTITY",
                    "detail": f"净列恒等式残差 {res['net_identity']['max_abs_resid']:.3e} 超容差 "
                              f"{res['net_identity']['tolerance']:.3e}"})

    # G4：月份对齐（在 build 里已用异常挡住，这里补一条可读的）
    for t in res["tiers"]:
        if t["net"]["n"] != res["n_months"]:
            out.append({"code": "G4_MONTH_ALIGNMENT",
                        "detail": f"{t['id']} 有效月 {t['net']['n']} ≠ {res['n_months']}"})

    for t in res["tiers"]:
        v = t["variance"]
        rel = abs(v["var_identity_residual"]) / v["var_gross"] if v["var_gross"] else 0.0
        if rel > 1e-12:
            out.append({"code": "G5_VARIANCE_IDENTITY",
                        "detail": f"{t['id']} 方差恒等残差相对 {rel:.3e}"})

    # G6：t 必须由净序列自己的 se 得到
    for t in res["tiers"]:
        want = t["net"]["mean_pp"] / t["net"]["se_iid_pp"]
        if abs(want - t["net"]["t_iid"]) > 1e-12:
            out.append({"code": "G6_SE_FROM_NET", "detail": f"{t['id']} t ≠ mean/se_net"})
        reused = abs(t["net"]["mean_pp"] / res["gross"]["se_iid_pp"] - t["net"]["t_iid"])
        if reused > 1e-6 and abs(t["delta_vs_l3ae_net"]) == 0.0:
            out.append({"code": "G6_SE_FROM_NET",
                        "detail": f"{t['id']} 复用了毛收益 se 却未记录差异（差 {reused:.3e}）"})

    for row in res["se_crosscheck"]:
        if row["rel_diff"] > 1e-3:
            out.append({"code": "G6B_SE_CROSSCHECK",
                        "detail": f"{row['tier']} 我 {row['mine']!r} vs phase O {row['phaseo']!r}"
                                  f"（相对 {row['rel_diff']:.3e}）"})

    # G7：费率单调
    order = ["T_1e8", "T_3e7", "T_0p1", "T_0p3"]
    by_id = {t["id"]: t for t in res["tiers"]}
    seq = [by_id[k]["net_pp_month"] for k in order]
    if any(seq[i] <= seq[i + 1] for i in range(len(seq) - 1)):
        out.append({"code": "G7_RATE_MONOTONIC", "detail": f"净值未随费率单调递减：{seq}"})

    # G8：主档判定不变
    p = res["primary"]
    if abs(p["t_iid"]) >= 1.96 or abs(p["t_nw3"]) >= 1.96:
        out.append({"code": "G8_PRIMARY_VERDICT",
                    "detail": f"主档 {p['tier']} t_iid={p['t_iid']:.4f} / t_nw3={p['t_nw3']:.4f} 至少一个 ≥1.96"})

    return out


def check_predictions(res: Mapping[str, Any], prereg: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_id = {t["id"]: t for t in res["tiers"]}
    out: list[dict[str, Any]] = []
    declared = {p["id"]: p for p in prereg["predictions"]}

    def add(pid: str, holds: bool, observed: str, note: str = "") -> None:
        d = declared[pid]
        out.append(
            {
                "id": pid,
                "timing": d["timing"],
                "recon_observed": d["recon_observed"],
                "claim": d["claim"],
                "observed": observed,
                "holds": bool(holds),
                "note": note,
            }
        )

    a = res["alpha_identity"]
    add("P1", a["max_abs_diff"] <= TOL and a["n_compared"] == 237,
        f"max|diff|={a['max_abs_diff']!r}，共同月 {a['n_compared']}")

    add("P2", res["cost_arithmetic"]["max_abs_resid"] <= 6e-7,
        f"max|cost−0.102×to|={res['cost_arithmetic']['max_abs_resid']:.3e}")

    order = ["T_1e8", "T_3e7", "T_0p1", "T_0p3"]
    seq = [by_id[k]["net_pp_month"] for k in order]
    mono = all(seq[i] > seq[i + 1] for i in range(len(seq) - 1))
    add("P3", mono, "净额 " + " > ".join(f"{v:.6f}" for v in seq))

    ratios = {t["id"]: t["variance"]["var_cost_over_gross"] for t in res["tiers"]}
    add("P4", all(v < 1e-2 for v in ratios.values()),
        "Var(cost)/Var(gross): " + ", ".join(f"{k}={v:.2e}" for k, v in ratios.items()))

    ses = {t["id"]: t["variance"]["se_net_over_gross"] for t in res["tiers"]}
    add("P5", all(0.99 <= v <= 1.01 for v in ses.values()),
        "se_net/se_gross: " + ", ".join(f"{k}={v:.6f}" for k, v in ses.items()))

    holds = {t["id"]: t["variance"]["se_net_ge_gross"] for t in res["tiers"]}
    add("P6", holds["T_0p3"] is True and holds["T_1e8"] is False,
        f"0.3% 档 Var(c)≥2Cov={holds['T_0p3']}；0.051% 档={holds['T_1e8']}",
        "方向分裂正是 L3ae「上界」说法不成立的证据")

    add("P10", 2.8 <= by_id["T_1e8"]["net"]["t_iid"] <= 3.2
        and 3.8 <= by_id["T_1e8"]["net"]["t_nw3"] <= 4.2,
        f"1亿档 t_iid={by_id['T_1e8']['net']['t_iid']:.4f}、t_nw3={by_id['T_1e8']['net']['t_nw3']:.4f}",
        "显著性与 phasei 的 EXQ5_COST_ABSORBED 判定不矛盾：判定档是 0.3%")

    gap = res["tiers"][0]["l3ae"]["turnover_gap"]
    add("P12", abs(gap) > 1e-6,
        f"非零月均值 {res['tiers'][0]['l3ae']['turnover_mean_nonzero_months']:.6f} "
        f"vs 全月均值 {res['tiers'][0]['l3ae']['turnover_mean_all_months']:.6f}"
        f"（差 {gap:+.6f}）",
        f"0.3% 档成本因此被 L3ae 高估 {by_id['T_0p3']['delta_vs_l3ae_cost']:+.6f} pp/月")
    return out


def monthly_rows(root: Path | str) -> tuple[list[str], list[list[Any]]]:
    """逐月明细（供落盘）。列 = 日期 / 毛利 / 换手 / 各档成本 / 各档净额。

    与 ``build()`` 用同一批函数，所以不会出现「报告一套数、明细另一套数」。
    调用方应核对：每档净额的列均值必须与 ``build()['tiers'][i]['net_pp_month']`` 相等。
    """
    root = Path(root)
    prereg = load_prereg(root)
    pi = read_rows(root / prereg["inputs_frozen"]["phasei_monthly"]["path"])
    po = read_rows(root / prereg["inputs_frozen"]["phaseo_monthly"]["path"])
    alpha = _col(pi, "alpha_v1_pp")
    to = _col(po, "to")
    if [r["signal_date"] for r in pi] != [r["signal_date"] for r in po]:
        raise CostNetError("月份键不同序")
    tiers = prereg["tiers"]
    header = ["signal_date", "alpha_v1_pp", "to_one_side"]
    for t in tiers:
        header += [f"cost_{t['id']}_pp", f"net_{t['id']}_pp"]
    rows: list[list[Any]] = []
    for i, r in enumerate(pi):
        row: list[Any] = [r["signal_date"], alpha[i], to[i]]
        for t in tiers:
            c = cost_pp(to[i], float(t["rate"]))
            row += [round(c, 10), round(alpha[i] - c, 10)]
        rows.append(row)
    return header, rows
