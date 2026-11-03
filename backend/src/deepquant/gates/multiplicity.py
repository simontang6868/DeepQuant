"""多重检验校正：同时报告 N 条结论时，哪些还站得住。

此前所有阶段（L3t / L3u / L3v / L3w / L3x / L3y）管的都是**单条**结论的数字对不对、
分辨率够不够。这一层管的是另一件事：**这 N 条是一起报出来的**。

在 α=0.05 下独立地做 m 次检验，哪怕全部零效应，期望也有约 m×0.05 条「显著」。
所以「某条 t 很大」和「这批结论里有多少条可信」不是同一个问题。

三条刻意的设计约束：

1. **m 不等于条目数。** L3v 已证明 27 条只对应 24 份独立数字——3 组条目共用同一序列。
   把同一份数字登记两次，会凭空抬高其余结论的门槛。所以主口径用去重后的独立检验数。

2. **被封印的检验照样消耗多重性预算。** 一条结论因为「不同源」而不给数字，
   但它当初确实被检验过；把它从 m 里剔除，等于假装这次检验没发生过。

3. **校正只收紧「显著」，不制造「无效应」。** 校正后不显著的条目，
   如果它本来功效就不足，结论只能是「无法判断」——不是「确认无效应」。
   这条沿用本项目 R5/R6：功效不足 ≠ 无效应。
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

EXPERIMENT_ID = "H-L3Z-MULTIPLICITY"

#: 单条检验的名义显著性水平。
ALPHA = 0.05
#: BH 的目标错误发现率。
Q = 0.05
#: 「功效足够」的门槛。低于此值，「未检出」不能读作「无效应」。
POWER_FLOOR = 0.8

#: 不参与多重性校正的条目：判定不是由单一均值的显著性给出的。
NON_SIGNIFICANCE_CLASSES = frozenset({"NOT_APPLICABLE"})

_BETACF_ITMAX = 300
_BETACF_EPS = 3.0e-14
_FPMIN = 1.0e-300


class MultiplicityError(ValueError):
    """输入不合法（与「某条被校正掉」是两件事）。"""


# --------------------------------------------------------------------------
# t 分布的双侧 p 值
# --------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """正则化不完全 beta 的连分数部分（Lentz 算法）。"""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _BETACF_ITMAX + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _BETACF_EPS:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    """正则化不完全 beta 函数 I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: int) -> float:
    """t 统计量的双侧 p 值。

    ``scipy`` 在本环境不可用，因此这里自己实现：
    ``p = I_{df/(df+t²)}(df/2, 1/2)``。
    单测用已知值钉住精度（t=1.96 在 df=236 下约 0.0511）。
    """
    df = int(df)
    if df <= 0:
        raise MultiplicityError(f"自由度必须为正，得到 {df}")
    t = abs(float(t))
    if t == 0.0:
        return 1.0
    if math.isnan(t) or math.isinf(t):
        raise MultiplicityError(f"t 不是有限值：{t}")
    return betai(df / 2.0, 0.5, df / (df + t * t))


def p_to_t(p: float, df: int) -> float:
    """给定双侧 p 值反解 |t|（二分法）。用于把校正后的门槛翻译成 t 门槛。"""
    if not 0.0 < p < 1.0:
        raise MultiplicityError(f"p 必须落在 (0,1)，得到 {p}")
    lo, hi = 0.0, 1.0e3
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_two_sided_p(mid, df) > p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --------------------------------------------------------------------------
# 三种校正
# --------------------------------------------------------------------------


def bonferroni(pvals: Sequence[float], alpha: float = ALPHA, m: int | None = None) -> list[bool]:
    """族系错误率，最保守。假设检验独立；正相关时更保守。

    ``m`` 是「同时报告了多少条检验」，可能与 ``len(pvals)`` 不同：
    有些检验做过了却拿不到可算的 p（判定非单一均值），它们仍要计入 m。
    """
    m = len(pvals) if m is None else m
    if m <= 0 or not pvals:
        return []
    thresh = alpha / m
    return [p <= thresh for p in pvals]


def holm(pvals: Sequence[float], alpha: float = ALPHA, m: int | None = None) -> list[bool]:
    """逐步下降法。比 Bonferroni 一致更宽松，同样控制 FWER。"""
    m = len(pvals) if m is None else m
    if m <= 0 or not pvals:
        return []
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    out = [False] * len(pvals)
    still_rejecting = True
    for k, i in enumerate(order):
        if not still_rejecting:
            out[i] = False
            continue
        if pvals[i] <= alpha / (m - k):
            out[i] = True
        else:
            # 一旦某一步不拒绝，后面全部不拒绝——这是 step-down 的定义
            out[i] = False
            still_rejecting = False
    return out


def bh(pvals: Sequence[float], q: float = Q, m: int | None = None) -> list[bool]:
    """Benjamini–Hochberg，控制错误发现率。对正相关稳健，比 FWER 类宽松。"""
    m = len(pvals) if m is None else m
    if m <= 0 or not pvals:
        return []
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    last = 0
    for k, i in enumerate(order, start=1):
        if pvals[i] <= k / m * q:
            last = k
    out = [False] * len(pvals)
    for k, i in enumerate(order, start=1):
        out[i] = k <= last
    return out


def bh_threshold(pvals: Sequence[float], q: float = Q, m: int | None = None) -> float:
    """BH 实际生效的 p 门槛（最大的满足条件的 p 值）。用于报告。"""
    m = len(pvals) if m is None else m
    if m <= 0 or not pvals:
        return 0.0
    hit = 0.0
    for k, p in enumerate(sorted(pvals), start=1):
        if p <= k / m * q:
            hit = p
    return hit


def harmonic(m: int) -> float:
    """调和数 H_m = 1 + 1/2 + … + 1/m。

    BY 的收紧因子。用有理数精确求和后一次性转浮点：m 只有几十，
    浮点累加虽不至于出错，但门槛要拿去做 1e-12 级比对，能精确就精确。
    """
    m = int(m)
    if m <= 0:
        raise MultiplicityError(f"调和数要求 m ≥ 1，得到 {m}")
    from fractions import Fraction

    return float(sum(Fraction(1, i) for i in range(1, m + 1)))


def by_unit_threshold(q: float = Q, m: int = 24) -> float:
    """BY 第 1 档（最小 p）的门槛：q / (m·H_m)。"""
    if m <= 0:
        raise MultiplicityError(f"BY 要求 m ≥ 1，得到 {m}")
    return q / (m * harmonic(m))


def benjamini_yekutieli(
    pvals: Sequence[float], q: float = Q, m: int | None = None
) -> list[bool]:
    """Benjamini–Yekutieli：在**任意依赖结构**下控制 FDR。

    与 BH 的唯一差别是门槛多除一个 H_m：

        p_(k) ≤ k·q / (m·H_m)

    step-up：取满足条件的最大 k，前 k 条全部拒绝。

    为什么本项目需要它：BH 的正确性依赖 PRDS（正回归相依）或独立性，
    而本项目的家族是「同一份月度面板上反复切池、切窗口、切成本口径」，
    PRDS 既没被检验过、也很难论证。BY 不需要这个前提，代价是门槛收紧 H_m 倍
    （m=24 时 3.776 倍）。方向是单向的：BY 只会比 BH 更严，绝不会更松，
    所以「换用 BY」不可能被读成「为提高通过数而换口径」。

    两个可验证的性质（单测里钉住）：
    - ``BY ⊆ BH`` 恒成立（H_m ≥ 1 ⇒ 门槛逐档不高于 BH）；
    - m=1 时 H_1=1，退化为单条检验。
    """
    m = len(pvals) if m is None else int(m)
    if m <= 0 or not pvals:
        return []
    unit = by_unit_threshold(q=q, m=m)
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    last = 0
    for k, i in enumerate(order, start=1):
        if pvals[i] <= k * unit:
            last = k
    out = [False] * len(pvals)
    for k, i in enumerate(order, start=1):
        out[i] = k <= last
    return out


def by_threshold(
    pvals: Sequence[float], q: float = Q, m: int | None = None
) -> float:
    """BY 实际生效的 p 门槛（满足条件的最大 p 值）。用于报告。"""
    m = len(pvals) if m is None else int(m)
    if m <= 0 or not pvals:
        return 0.0
    unit = by_unit_threshold(q=q, m=m)
    hit = 0.0
    for k, p in enumerate(sorted(pvals), start=1):
        if p <= k * unit:
            hit = p
    return hit


def apply_by(
    rows: Sequence[Mapping[str, Any]],
    *,
    m: int,
    q: float = Q,
    estimator: str = "t_nw3",
) -> dict[str, Any]:
    """只算 BY，作为一条**附加**的、不依赖 PRDS 的判定。

    刻意不改 ``apply_corrections`` 的返回结构：L3z 的产物已冻结并进了 sha 链，
    给它加字段会让已归档的结果无法字节级复现。BY 单独走一条通路，
    需要时并列报告，不需要时完全不出现——两种情形都不影响既有结论。
    """
    keys: list[str] = []
    pvals: list[float] = []
    for row in rows:
        if row.get("_counts_only"):
            continue
        rc = row.get("recomputed") or {}
        t = rc.get(estimator)
        n = rc.get("n")
        if t is None or n is None:
            continue
        keys.append(row["key"])
        pvals.append(t_two_sided_p(t, max(int(n) - 1, 1)))

    n_scored = len(pvals)
    m_eff = max(int(m), n_scored)
    # 与 apply_corrections 同一口径：做过但拿不到 p 的检验补 p=1.0 占名额
    padded = list(pvals) + [1.0] * (m_eff - n_scored)
    flags = benjamini_yekutieli(padded, q=q, m=m_eff)[:n_scored]

    items = [
        {"key": k, "p": p, "by": bool(f)}
        for k, p, f in zip(keys, pvals, flags, strict=False)
    ]
    rejected = [i["key"] for i in items if i["by"]]
    return {
        "method": "Benjamini-Yekutieli",
        "estimator": estimator,
        "q": q,
        "m_used": m_eff,
        "n_scored": n_scored,
        "harmonic": harmonic(m_eff),
        "by_unit_threshold": by_unit_threshold(q=q, m=m_eff),
        "by_p_threshold": by_threshold(pvals, q=q, m=m_eff) if pvals else None,
        "n_rejected": len(rejected),
        "rejected": sorted(rejected),
        "items": items,
    }


# --------------------------------------------------------------------------
# 参与集合与 m 的口径
# --------------------------------------------------------------------------


def eligible(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """参与多重性计数的条目。

    判定依据不是「单一均值的显著性」的条目不参与**被校正**（它们的结论不由 t 给出），
    但它们**仍然计入 m**——那次检验确实做过，不能假装没发生。
    """
    out = []
    for row in rows:
        stat = row.get("stat") or {}
        if not stat.get("applicable", True):
            out.append({**row, "_counts_only": True})
            continue
        rc = row.get("recomputed") or {}
        if rc.get("t_nw3") is None or rc.get("n") is None:
            out.append({**row, "_counts_only": True})
            continue
        out.append({**row, "_counts_only": False})
    return out


def count_independent_tests(rows: Sequence[Mapping[str, Any]], ndigits: int = 10) -> int:
    """独立检验数：同一份数字被登记多次只算一次。

    这是主口径的 m。L3v 已确认 27 条只对应 24 份独立数字——
    把重复登记算进 m，会让真正独立的结论平白承受更严的门槛。

    ⚠️ 不能用 ``series`` 路径去重：共享同一份数字的条目路径各不相同
    （`phasel_out/…` 与 `phaser_out/…` 是两个目录，数值却逐位相同）。
    只能按数值指纹去重，与 L3v 的 ``duplicate_groups`` 同一口径。
    """
    seen: set[Any] = set()
    for row in rows:
        rc = row.get("recomputed") or {}
        mean = rc.get("mean")
        t = rc.get("t_nw3")
        n = rc.get("n")
        if mean is None or t is None or n is None:
            # 拿不到数值指纹的条目只能按自身 key 计一次
            seen.add(("key", str(row.get("key"))))
            continue
        seen.add((round(float(mean), ndigits), round(float(t), ndigits), int(n)))
    return len(seen)


def family_m(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """各族内的检验数，用于族内校正（同一族内的假设更相关）。"""
    out: dict[str, int] = {}
    for row in rows:
        fam = row.get("family") or "unknown"
        out[fam] = out.get(fam, 0) + 1
    return out


# --------------------------------------------------------------------------
# 逐条判定
# --------------------------------------------------------------------------


def classify_one(
    *,
    p: float,
    df: int,
    power: float | None,
    raw_significant: bool,
    bonf: bool,
    holm_ok: bool,
    bh_ok: bool,
    counts_only: bool,
) -> dict[str, Any]:
    """把「多重性」与「功效」分开判——这是本模块最要紧的一条。

    校正后不显著，且功效不足 ⇒ NEITHER（无法判断），**不是** NULL。
    校正后不显著，但功效足够 ⇒ NULL_CONFIRMED（可以引「未检出」）。
    """
    if counts_only:
        verdict = "NOT_APPLICABLE"
    elif not raw_significant:
        # 单条都不显著。此时「不显著」分两种，取决于功效。
        verdict = "NULL_CONFIRMED" if (power or 0.0) >= POWER_FLOOR else "NEITHER"
    elif bh_ok and holm_ok and bonf:
        verdict = "SURVIVES_ALL"
    elif bh_ok:
        verdict = "SURVIVES_FDR_ONLY"
    else:
        verdict = "DOWNGRADED"
    return {
        "p": p,
        "df": df,
        "raw_significant": raw_significant,
        "bonferroni": bonf,
        "holm": holm_ok,
        "bh": bh_ok,
        "power": power,
        "verdict": verdict,
    }


def apply_corrections(
    rows: Sequence[Mapping[str, Any]],
    *,
    m: int,
    alpha: float = ALPHA,
    q: float = Q,
    estimator: str = "t_nw3",
) -> dict[str, Any]:
    """对一批条目做三种校正。返回逐条结果与汇总。"""
    keys: list[str] = []
    pvals: list[float] = []
    dfs: list[int] = []
    for row in rows:
        # 「只计名额」的条目：那次检验做过了（占 m），但它的结论不是由这个 t 给出的，
        # 所以不给它算 p、也不给它判「通过/掉档」——否则会把多重性的结论挪到不该用的地方。
        if row.get("_counts_only"):
            continue
        rc = row.get("recomputed") or {}
        t = rc.get(estimator)
        n = rc.get("n")
        if t is None or n is None:
            continue
        keys.append(row["key"])
        pvals.append(t_two_sided_p(t, max(int(n) - 1, 1)))
        dfs.append(max(int(n) - 1, 1))

    n_scored = len(pvals)
    m_eff = max(int(m), n_scored)
    # 做过了却拿不到 p 的检验仍占名额：补成 p=1.0（最不显著）参与排序，
    # 否则它们会凭空把门槛放松。
    padded = list(pvals) + [1.0] * (m_eff - n_scored)
    b = bonferroni(padded, alpha=alpha, m=m_eff)[:n_scored]
    h = holm(padded, alpha=alpha, m=m_eff)[:n_scored]
    f = bh(padded, q=q, m=m_eff)[:n_scored]

    items = []
    for i, key in enumerate(keys):
        row = next(r for r in rows if r["key"] == key)
        rc = row.get("recomputed") or {}
        items.append(
            {
                "key": key,
                **classify_one(
                    p=pvals[i],
                    df=dfs[i],
                    power=rc.get("power"),
                    raw_significant=pvals[i] <= alpha,
                    bonf=b[i],
                    holm_ok=h[i],
                    bh_ok=f[i],
                    counts_only=bool(row.get("_counts_only")),
                ),
            }
        )

    counts: dict[str, int] = {}
    for item in items:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    return {
        "estimator": estimator,
        "m_used": m_eff,
        "n_scored": len(pvals),
        "alpha": alpha,
        "q": q,
        "bonferroni_p_threshold": alpha / m_eff if m_eff else 0.0,
        "bh_p_threshold": bh_threshold(pvals, q=q, m=m_eff) if pvals else None,
        "n_counts_only": sum(1 for r in rows if r.get("_counts_only")),
        "items": items,
        "verdict_counts": counts,
    }


def expected_false_positives(m: int, alpha: float = ALPHA) -> float:
    """全部零效应时的期望假阳性条数。用来说明「为什么需要这一层」。"""
    return m * alpha


# --------------------------------------------------------------------------
# 参数扫描：alpha 与 q 必须分离
# --------------------------------------------------------------------------


def alpha_sweep(
    rows: Sequence[Mapping[str, Any]],
    *,
    m: int,
    alphas: Sequence[float] = (0.01, 0.05, 0.10),
    q: float = Q,
    estimator: str = "t_nw3",
) -> list[dict]:
    """只扫 alpha，q 固定。

    两者必须分离：alpha 是 FWER 类（Bonferroni / Holm）的水平，q 是 BH 的目标错误发现率。
    把它们绑成同一个值会让「B 类集合随参数非单调」看起来像校正流程出了问题，
    而实际上只是两个参数被同时改动了。
    """
    out = []
    for a in alphas:
        run = apply_corrections(rows, m=m, alpha=a, q=q, estimator=estimator)
        rejected = {
            i["key"]: {"bonferroni": i["bonferroni"], "holm": i["holm"], "bh": i["bh"]}
            for i in run["items"]
        }
        raw = {i["key"] for i in run["items"] if i["raw_significant"]}
        rej_bh = {k for k, v in rejected.items() if v["bh"]}
        rej_bonf = {k for k, v in rejected.items() if v["bonferroni"]}
        rej_holm = {k for k, v in rejected.items() if v["holm"]}
        counts_only = [
            r["key"] for r in rows if r.get("_counts_only")
        ]
        out.append(
            {
                "alpha": a,
                "q": q,
                "m": run["m_used"],
                "n_scored": run["n_scored"],
                # A：校正后拒绝
                "rejected_bonferroni": sorted(rej_bonf),
                "rejected_holm": sorted(rej_holm),
                "rejected_bh": sorted(rej_bh),
                # 校正后未拒绝（含「单条就不显著」的那批，与 B 是两回事）
                "not_rejected_bh": sorted(set(rejected) - rej_bh),
                # B：原始 p<alpha 但校正后未拒绝
                "raw_significant": sorted(raw),
                "downgraded_bh": sorted(raw - rej_bh),
                # C：不具备单一 p 值，无法参与该校正流程
                "counts_only": sorted(counts_only),
            }
        )
    return out


def q_sweep(
    rows: Sequence[Mapping[str, Any]],
    *,
    m: int,
    qs: Sequence[float] = (0.01, 0.05, 0.10),
    alpha: float = ALPHA,
    estimator: str = "t_nw3",
) -> list[dict]:
    """只扫 BH 的 q，alpha 固定。"""
    out = []
    for q in qs:
        run = apply_corrections(rows, m=m, alpha=alpha, q=q, estimator=estimator)
        rejected = {i["key"]: i["bh"] for i in run["items"]}
        out.append(
            {
                "q": q,
                "alpha": alpha,
                "m": run["m_used"],
                "rejected_bh": sorted(k for k, v in rejected.items() if v),
                "not_rejected_bh": sorted(k for k, v in rejected.items() if not v),
            }
        )
    return out


def monotonicity_check(sweep: Sequence[Mapping[str, Any]], field: str) -> dict:
    """拒绝集合必须随水平放宽而单调增大（嵌套）。

    这是标准校正方法的基本性质；不满足说明实现有误。
    """
    violations = []
    prev: set[str] | None = None
    prev_level = None
    for step in sweep:
        cur = set(step[field])
        level = step.get("alpha", step.get("q"))
        if prev is not None and not prev <= cur:
            violations.append(
                {
                    "code": "MONOTONICITY_VIOLATED",
                    "field": field,
                    "detail": (
                        f"水平 {prev_level} → {level}：拒绝集合没有包含上一档"
                        f"（少了 {sorted(prev - cur)}）"
                    ),
                }
            )
        prev, prev_level = cur, level
    return {"field": field, "ok": not violations, "violations": violations}


def assemble(
    rows: Sequence[Mapping[str, Any]],
    *,
    alpha: float = ALPHA,
    q: float = Q,
    estimator: str = "t_nw3",
) -> dict[str, Any]:
    """主入口：三种 m 口径各跑一遍，返回对照。"""
    scored = eligible(rows)
    m_all = len(scored)
    m_indep = count_independent_tests(scored)
    fam = family_m(scored)

    runs = {
        "m_all": apply_corrections(scored, m=m_all, alpha=alpha, q=q, estimator=estimator),
        "m_independent": apply_corrections(
            scored, m=m_indep, alpha=alpha, q=q, estimator=estimator
        ),
    }
    # 族内校正：每条按自己所在族的 m 判一次
    by_family: dict[str, Any] = {}
    for name in fam:
        fam_rows = [r for r in scored if (r.get("family") or "unknown") == name]
        by_family[name] = apply_corrections(
            fam_rows, m=len(fam_rows), alpha=alpha, q=q, estimator=estimator
        )

    return {
        "experiment_id": EXPERIMENT_ID,
        "alpha": alpha,
        "q": q,
        "estimator": estimator,
        "m_all": m_all,
        "m_independent": m_indep,
        "family_m": fam,
        "expected_false_positives": expected_false_positives(m_indep, alpha),
        "runs": runs,
        "by_family": by_family,
    }
