"""结论卡片注册表（claim registry）的闸门实现。

L3u 回答了「引用的数字对不对」；本模块回答「对外该怎么写」。三条硬约束：

1. **数字可回指**：卡片正文里出现的每个数字，必须登记 ``numbers`` 并回指 L3u
   的重算字段（``recomputed.mean / t_iid / t_nw3 / mde / power / n``），四舍五入
   后字面一致、符号一致。反向也查——登记了却没在正文出现，同样算漂移。
2. **强度只降不升**：``quotability`` 必须落在 L3u 正式分级的允许降级集合里。
   ``NOT_GRADED`` 的条目只能在引用块里当「不可引用」处理。
3. **限定结构化**：窗口 / 估计量 / ``d`` 定义 / 成本口径 / 单位按族强制，缺一项
   就是违规，不接受「仅供参考」这类无信息量的话糊过去。

为什么不用自由文本 + 人眼复核：本项目已经四次在同一种地方出错（pp 与小数互乘、
``abs()`` 吃掉负号、交叉验证只比交集、闸门只看 t 不看量级）。卡片是给人看的，
但校验必须是机器的。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

#: L3u 分级 → 卡片允许使用的分级（只能等于或更保守，不构成全序）
#:
#: ``EXPLORATORY`` 是 L3z 审计后新增的一档：单条 α=0.05 显著，但在同时报告全族的
#: 多重检验校正下未通过。它**不是**「无效应」，也不是「已确认方向」——
#: 只能记为待验证的方向性观察。
TIER_DOWNGRADES: dict[str, frozenset[str]] = {
    "QUOTABLE": frozenset({"QUOTABLE", "QUOTABLE_MARGINAL", "DIRECTION_ONLY", "NOT_GRADED"}),
    "QUOTABLE_MARGINAL": frozenset({"QUOTABLE_MARGINAL", "DIRECTION_ONLY", "NOT_GRADED"}),
    "DIRECTION_ONLY": frozenset(
        {"DIRECTION_ONLY", "EXPLORATORY", "UNDERPOWERED", "NULL_WEAK", "NOT_GRADED"}
    ),
    "EXPLORATORY": frozenset({"EXPLORATORY", "UNDERPOWERED", "NULL_WEAK", "NOT_GRADED"}),
    "NULL_CONFIRMED": frozenset({"NULL_CONFIRMED", "NULL_WEAK", "UNDERPOWERED", "NOT_GRADED"}),
    "NULL_WEAK": frozenset({"NULL_WEAK", "UNDERPOWERED", "NOT_GRADED"}),
    "UNDERPOWERED": frozenset({"UNDERPOWERED", "NOT_GRADED"}),
    "UNCLASSIFIED": frozenset({"NOT_GRADED"}),
    "NOT_GRADED": frozenset({"NOT_GRADED"}),
}

#: 引用块里可出现的窗口标签（预登记冻结；确实要别的就写 other:<一句说明>）
WINDOW_KINDS = frozenset(
    {
        "full_sample",
        "modern_era",
        "segment_2007_2015",
        "segment_2016_2026",
        "segment_pre_2016",
        "l3p_folds",
        "l3r_periods",
        "l3s_periods",
        "single_window",
    }
)

#: 判定所用的 t 口径。iid 与 NW3 混用是本项目复发最多的口径问题之一。
ESTIMATORS = frozenset({"iid", "nw3"})

#: 横截面 d 的定义。与文献 spread 差 2 倍，引用时必须写明。
D_DEFINITIONS = frozenset({"0.5*long-0.5*short", "long_minus_short", "not_applicable"})

#: 可回指的重算字段
FIELDS = frozenset(
    {
        "mean",
        "t_iid",
        "t_nw3",
        "mde",
        "power",
        "n",
        "ratio",
        "mde_over_mean",
        "stat_n",
        "l3t_mean",
        "l3t_t",
        "l3t_mde",
        "l3t_n",
    }
)

#: 家族 → 该族必须出现的最小数字集合（缺了说明分辨率没报）
FAMILY_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "A_cross_spread": frozenset({"mean", "mde"}),
    "B_long_only": frozenset({"mean", "mde"}),
    "C_regression": frozenset({"mean", "mde"}),
    "D_ic": frozenset({"mean", "mde"}),
    "E_fold": frozenset({"mean", "mde"}),
}

#: 数字 token。紧跟字母/数字/点之后的不算——``NW3``、``MAX5``、``m1`` 是口径标签，
#: 不是统计量。第一版漏了这条 lookbehind，13 处假阳性全是这些标签造成的。
NUM_TOKEN = re.compile(r"(?<![A-Za-z0-9_.])[-−+]?\d+(?:\.\d+)?")
MIN_LIMIT_CHARS = 8

CLAIMS_ERROR = "CLAIMS_INVALID"


class ClaimsError(ValueError):
    """卡片注册表结构非法（与「闸门检出违规」是两件事）。"""


def _norm(text: str) -> str:
    return str(text).replace("−", "-").replace("＋", "+").strip()


def _strip_sign(text: str) -> str:
    token = _norm(text)
    if token[:1] in "+-":
        return token[1:]
    return token


def _sign_of(text: str) -> str:
    """符号必须写在字面里。``abs()`` 吃掉负号是本项目复发过的坑（M 阶段）。"""
    return "-" if _norm(text).startswith("-") else "+"


def load_claims(path: str | Path) -> dict[str, Any]:
    """读入卡片表并做结构校验（结构错就拒跑，不进闸门）。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    claims = payload.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ClaimsError("卡片表缺少 claims")
    keys = [c.get("key") for c in claims]
    if any(not isinstance(k, str) or not k for k in keys):
        raise ClaimsError(f"卡片 key 缺失或非字符串：{keys}")
    if len(set(keys)) != len(keys):
        raise ClaimsError(f"卡片 key 重复：{keys}")

    required = (
        "headline",
        "numbers",
        "window",
        "estimator_for_verdict",
        "limits_of_use",
        "quotability",
    )
    for claim in claims:
        miss = [f for f in required if f not in claim]
        if miss:
            raise ClaimsError(f"[{claim['key']}] 缺字段 {miss}")
        if not isinstance(claim["numbers"], list) or not claim["numbers"]:
            raise ClaimsError(f"[{claim['key']}] numbers 必须是非空列表")
        for num in claim["numbers"]:
            for f in ("display", "field", "decimals"):
                if f not in num:
                    raise ClaimsError(f"[{claim['key']}] numbers 项缺 {f}：{num}")
            if num["field"] not in FIELDS:
                raise ClaimsError(f"[{claim['key']}] 未知字段 {num['field']}")
            dec = num["decimals"]
            if not isinstance(dec, int) or not 0 <= dec <= 6:
                raise ClaimsError(f"[{claim['key']}] decimals 非法：{dec}")
        for lit in claim.get("literals", []):
            if "display" not in lit or "reason" not in lit:
                raise ClaimsError(f"[{claim['key']}] literals 项缺 display/reason：{lit}")
        if claim["estimator_for_verdict"] not in ESTIMATORS:
            raise ClaimsError(
                f"[{claim['key']}] estimator_for_verdict 非法：{claim['estimator_for_verdict']}"
            )
    return payload


def check_coverage(claims: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> dict:
    """双向差集。只比交集会让「砍掉一半条目」也 PASS（本项目已发生过一次）。"""
    claim_keys = {c["key"] for c in claims}
    row_keys = {r["key"] for r in rows}
    by_key = {r["key"]: r for r in rows}
    family_bad = []
    unit_bad = []
    for claim in claims:
        row = by_key.get(claim["key"])
        if row is None:
            continue
        if claim.get("family") and claim["family"] != row["family"]:
            family_bad.append(
                {"key": claim["key"], "claim": claim["family"], "l3u": row["family"]}
            )
        if claim.get("unit") and claim["unit"] != row["unit"]:
            unit_bad.append({"key": claim["key"], "claim": claim["unit"], "l3u": row["unit"]})
    return {
        "n_claims": len(claims),
        "n_rows": len(rows),
        "missing": sorted(row_keys - claim_keys),
        "orphan": sorted(claim_keys - row_keys),
        "family_mismatch": family_bad,
        "unit_mismatch": unit_bad,
        "complete": not (row_keys - claim_keys) and not (claim_keys - row_keys),
    }


def resolve_field(row: Mapping[str, Any], field: str) -> float | None:
    """把字段名解析成可比较的数字；``ratio`` 是 |mean|/mde 的派生量。"""
    rc = row.get("recomputed") or {}
    l3t = row.get("l3t") or {}
    table = {
        "mean": rc.get("mean"),
        "t_iid": rc.get("t_iid"),
        "t_nw3": rc.get("t_nw3"),
        "mde": rc.get("mde"),
        "power": rc.get("power"),
        "n": rc.get("n"),
        "l3t_mean": l3t.get("mean"),
        "l3t_t": l3t.get("t_nw3"),
        "l3t_mde": l3t.get("mde"),
        "l3t_n": l3t.get("n"),
    }
    if field == "ratio":
        mean = rc.get("mean")
        mde = rc.get("mde")
        if mean is None or not mde:
            return None
        return abs(float(mean)) / float(mde)
    if field == "mde_over_mean":
        mean = rc.get("mean")
        mde = rc.get("mde")
        if not mean or mde is None:
            return None
        return float(mde) / abs(float(mean))
    if field == "stat_n":
        value = (row.get("stat") or {}).get("n")
        return None if value is None else float(value)
    value = table.get(field)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_expected(value: float, decimals: int, scale: float = 1.0) -> str:
    return f"{abs(float(value)) * float(scale):.{decimals}f}"


def check_numbers(claim: Mapping[str, Any], row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """数字三查：值、符号、双向覆盖。"""
    key = claim["key"]
    issues: list[dict[str, Any]] = []
    registered: dict[str, dict[str, Any]] = {}
    signs: dict[str, str] = {}

    for num in claim["numbers"]:
        field = num["field"]
        value = resolve_field(row, field)
        if value is None:
            issues.append(
                {"claim_key": key, "code": "UNRESOLVED_FIELD", "detail": f"{field} 无可比数值"}
            )
            continue
        scale = float(num.get("scale", 1.0))
        expected = format_expected(value, int(num["decimals"]), scale)
        got = _strip_sign(str(num["display"]))
        if got != expected:
            issues.append(
                {
                    "claim_key": key,
                    "code": "VALUE_DRIFT",
                    "detail": f"{field}: 卡片写 {num['display']}，重算应为 {expected}"
                    f"（scale={scale:g}, decimals={num['decimals']}）",
                }
            )
        if (value < 0) != (_sign_of(str(num["display"])) == "-"):
            issues.append(
                {
                    "claim_key": key,
                    "code": "SIGN_MISMATCH",
                    "detail": f"{field} 实际 {value:+.6g}，卡片写 {num['display']}"
                    "（负号必须写进 display，不能靠正文字面猜）",
                }
            )
        registered[got] = num
        signs[got] = "-" if value < 0 else "+"

    literals = {_strip_sign(str(lit["display"])) for lit in claim.get("literals", [])}
    used: set[str] = set()
    for token in NUM_TOKEN.findall(claim["headline"]):
        bare = _strip_sign(token)
        if bare in registered:
            used.add(bare)
            if _sign_of(token) != signs[bare]:
                issues.append(
                    {
                        "claim_key": key,
                        "code": "SIGN_IN_TEXT",
                        "detail": f"正文写 {token}，但该量实际符号为 {signs[bare]}",
                    }
                )
            continue
        if bare in literals:
            continue
        issues.append(
            {
                "claim_key": key,
                "code": "UNREGISTERED_NUMBER",
                "detail": f"正文数字 {token} 既未登记 numbers 也未在 literals 说明",
            }
        )
    for bare in registered:
        if bare not in used:
            issues.append(
                {
                    "claim_key": key,
                    "code": "UNUSED_NUMBER",
                    "detail": f"登记了 {bare} 但正文未出现（文案改过、数字没删）",
                }
            )
    return issues


#: 每种分级依据要求条目在该清单里处于什么状态。
#: ``l3w_unseal`` 是解封（要求 UNSEALED）；``l3z_audit`` 是审计后降级（要求 DOWNGRADED）。
#: 两者都不是放宽闸门——都要求卡片分级**逐字等于**清单给的那一档。
BASIS_REQUIRED_STATUS: dict[str, str] = {
    "l3w_unseal": "UNSEALED",
    "l3z_audit": "DOWNGRADED",
}


def check_tier(
    claim: Mapping[str, Any],
    row: Mapping[str, Any],
    unseal_index: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """可引用强度不得高于依据来源给出的分级。

    默认依据是 L3u 的正式分级。若卡片声明 ``tier_basis: "l3w_unseal"``，
    则分级必须**逐字等于**解封复核给出的那一档——解封不是放宽闸门，
    而是换一个有预登记、有网格验证的来源，所以不允许卡片自己另定一档。
    """
    key = claim["key"]
    claim_tier = claim["quotability"]
    basis = str(claim.get("tier_basis") or "l3u_grade")

    if basis != "l3u_grade":
        info = (unseal_index or {}).get(key)
        if info is None:
            return [
                {
                    "claim_key": key,
                    "code": "UNKNOWN_TIER_BASIS",
                    "detail": f"tier_basis={basis!r}，但依据清单里没有 {key}",
                }
            ]
        required = BASIS_REQUIRED_STATUS.get(basis, "UNSEALED")
        if info.get("status") != required:
            return [
                {
                    "claim_key": key,
                    "code": "UNSEAL_NOT_APPROVED",
                    "detail": (
                        f"{key} 在依据 {basis} 下的状态是 {info.get('status')!r}，"
                        f"而该依据要求 {required!r}，不能据此改分级"
                    ),
                }
            ]
        if claim_tier != info.get("tier"):
            return [
                {
                    "claim_key": key,
                    "code": "UNSEAL_TIER_MISMATCH",
                    "detail": f"解封给的是 {info.get('tier')}，卡片写 {claim_tier}",
                }
            ]
        return []

    l3u_tier = (row.get("grade") or {}).get("tier")
    if l3u_tier not in TIER_DOWNGRADES:
        return [
            {
                "claim_key": key,
                "code": "UNKNOWN_TIER",
                "detail": f"L3u 分级 {l3u_tier!r} 不在允许集合",
            }
        ]
    if claim_tier not in TIER_DOWNGRADES[l3u_tier]:
        return [
            {
                "claim_key": key,
                "code": "TIER_UPGRADE",
                "detail": f"卡片标 {claim_tier}，高于 L3u 正式分级 {l3u_tier}",
            }
        ]
    return []


def check_required_limits(claim: Mapping[str, Any], row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """按族强制必带限定；缺一项就是违规。"""
    key = claim["key"]
    issues: list[dict[str, Any]] = []

    window = str(claim.get("window") or "")
    if not window:
        issues.append({"claim_key": key, "code": "MISSING_WINDOW", "detail": "未标窗口"})
    elif window not in WINDOW_KINDS and not window.startswith("other:"):
        issues.append(
            {
                "claim_key": key,
                "code": "UNKNOWN_WINDOW",
                "detail": f"窗口标签 {window!r} 不在冻结枚举内",
            }
        )

    estimator = claim.get("estimator_for_verdict")
    t_fields = {n["field"] for n in claim["numbers"] if n["field"] in {"t_iid", "t_nw3"}}
    wrong = {
        "iid": "t_nw3",
        "nw3": "t_iid",
    }.get(estimator)
    if t_fields and wrong in t_fields and not t_fields - {wrong}:
        # 只引了「非判定口径」的 t：估计量混用，必须至少把判定口径也列出来
        issues.append(
            {
                "claim_key": key,
                "code": "ESTIMATOR_MISMATCH",
                "detail": f"判定用 {estimator}，但只登记了 {wrong}",
            }
        )

    family = row["family"]
    if family == "A_cross_spread":
        d_def = claim.get("d_definition")
        if d_def not in D_DEFINITIONS:
            issues.append(
                {
                    "claim_key": key,
                    "code": "MISSING_D_DEFINITION",
                    "detail": f"d 定义缺失或非法：{d_def!r}",
                }
            )
    if family == "B_long_only" and row.get("detected"):
        cost = str(claim.get("cost_id") or "")
        if not cost:
            issues.append(
                {
                    "claim_key": key,
                    "code": "MISSING_COST_ID",
                    "detail": "已检出的多头净收益必须标成本口径编号",
                }
            )
    if family in {"D_ic", "E_fold"}:
        note = str(claim.get("unit_note") or "")
        if len(note) < MIN_LIMIT_CHARS:
            issues.append(
                {
                    "claim_key": key,
                    "code": "MISSING_UNIT_NOTE",
                    "detail": f"{family} 必须说明单位（IC / %/折）",
                }
            )

    if row.get("detected") is False:
        fields = {n["field"] for n in claim["numbers"]}
        if not fields & {"mde", "power"}:
            issues.append(
                {
                    "claim_key": key,
                    "code": "MISSING_RESOLUTION",
                    "detail": "未检出必须同时报 MDE 或事后功效",
                }
            )

    need = FAMILY_REQUIRED_FIELDS.get(family, frozenset())
    fields = {n["field"] for n in claim["numbers"]}
    lack = need - fields
    if lack:
        issues.append(
            {
                "claim_key": key,
                "code": "MISSING_CORE_FIELD",
                "detail": f"{family} 缺核心统计量 {sorted(lack)}",
            }
        )

    limits = claim.get("limits_of_use") or []
    if not isinstance(limits, list) or not limits:
        issues.append(
            {"claim_key": key, "code": "MISSING_LIMITS", "detail": "至少一条使用限制"}
        )
    else:
        short = [t for t in limits if len(str(t)) < MIN_LIMIT_CHARS]
        if short:
            issues.append(
                {
                    "claim_key": key,
                    "code": "THIN_LIMIT",
                    "detail": f"限制条款过短，等于没写：{short}",
                }
            )
    return issues


def check_retractions(
    claims: Sequence[Mapping[str, Any]], retractions: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """撤回台账：被禁措辞不得出现在任何正文里；指向的条目必须存在。"""
    keys = {c["key"] for c in claims}
    heads = [(c["key"], _norm(c["headline"]).replace(" ", "")) for c in claims]
    issues: list[dict[str, Any]] = []
    for item in retractions:
        banned = _norm(str(item.get("banned_phrasing") or "")).replace(" ", "")
        if not banned:
            issues.append({"claim_key": None, "code": "EMPTY_BAN", "detail": str(item)})
            continue
        target = item.get("claim_key")
        if target is not None and target not in keys:
            issues.append(
                {
                    "claim_key": target,
                    "code": "ORPHAN_RETRACTION",
                    "detail": f"撤回台账指向不存在的卡片：{target}",
                }
            )
        for ckey, head in heads:
            if banned in head:
                issues.append(
                    {
                        "claim_key": ckey,
                        "code": "BANNED_PHRASING",
                        "detail": f"正文含已撤回措辞「{item.get('banned_phrasing')}」",
                    }
                )
    return issues


def duplicate_groups(rows: Sequence[Mapping[str, Any]], ndigits: int = 10) -> list[dict[str, Any]]:
    """数值指纹去重：多条结论共用同一份数字，不能算作多次独立验证。"""
    buckets: dict[tuple, list[str]] = {}
    for row in rows:
        rc = row.get("recomputed") or {}
        if rc.get("mean") is None or rc.get("mde") is None:
            continue
        fp = (
            int(rc.get("n")),
            round(float(rc["mean"]), ndigits),
            round(float(rc.get("t_nw3") or 0.0), ndigits),
            round(float(rc["mde"]), ndigits),
        )
        buckets.setdefault(fp, []).append(row["key"])
    groups = [{"keys": sorted(v), "n": len(v)} for v in buckets.values() if len(v) > 1]
    return sorted(groups, key=lambda g: (-g["n"], g["keys"][0]))


def render_citation_block(claim: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    """生成可直接粘进报告的引用块。"""
    grade = row.get("grade") or {}
    lines = [
        f"**`{claim['key']}`**（{row['family']}，单位 {row['unit']}）",
        f"> {claim['headline']}",
        "",
        f"- 窗口：{claim['window']}"
        + (f"（{claim['window_note']}）" if claim.get("window_note") else ""),
        f"- 判定估计量：{claim['estimator_for_verdict']}",
    ]
    if claim.get("d_definition") and claim["d_definition"] != "not_applicable":
        lines.append(f"- d 定义：{claim['d_definition']}")
    if claim.get("cost_id"):
        lines.append(f"- 成本口径：{claim['cost_id']}")
    if claim.get("unit_note"):
        lines.append(f"- 单位说明：{claim['unit_note']}")
    for limit in claim.get("limits_of_use") or []:
        lines.append(f"- 限定：{limit}")
    lines.append(f"- 可引用强度：{claim['quotability']}")
    if claim.get("quotability_note"):
        lines.append(f"- 分级依据：{claim['quotability_note']}")
    if str(claim.get("tier_basis") or "l3u_grade") != "l3u_grade":
        lines.append(
            f"- 分级来源：`{claim['tier_basis']}`（解封复核，非 L3u 正式分级；"
            f"L3u 记为 {grade.get('tier')}）"
        )
    if claim.get("reference_quotability"):
        lines.append(
            f"- 参考（不得用于对外）：{claim['reference_quotability']}"
            f"｜{grade.get('reference_reason') or ''}"
        )
    lines.append(
        f"- 来源：L3u 正式分级 {grade.get('tier')}｜序列 `{row.get('series')}`"
        f"（n={row.get('series_n')}）"
    )
    return "\n".join(lines)


def assemble_registry(
    claims_payload: Mapping[str, Any],
    l3u: Mapping[str, Any],
    unseal_index: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """跑全部闸门，产出注册表结果。"""
    claims = claims_payload["claims"]
    rows = l3u["rows"]
    by_key = {r["key"]: r for r in rows}

    coverage = check_coverage(claims, rows)
    violations: list[dict[str, Any]] = []

    if coverage["missing"]:
        violations.append(
            {
                "claim_key": None,
                "code": "MISSING_CLAIM",
                "detail": f"未覆盖的 L3u 条目：{coverage['missing']}",
            }
        )
    if coverage["orphan"]:
        violations.append(
            {
                "claim_key": None,
                "code": "ORPHAN_CLAIM",
                "detail": f"L3u 里不存在的卡片：{coverage['orphan']}",
            }
        )
    for item in coverage["family_mismatch"] + coverage["unit_mismatch"]:
        violations.append(
            {
                "claim_key": item["key"],
                "code": "METADATA_MISMATCH",
                "detail": f"族/单位与 L3u 不一致：{item}",
            }
        )

    for claim in claims:
        row = by_key.get(claim["key"])
        if row is None:
            continue
        violations.extend(check_numbers(claim, row))
        violations.extend(check_tier(claim, row, unseal_index))
        violations.extend(check_required_limits(claim, row))

    violations.extend(check_retractions(claims, claims_payload.get("retractions") or []))

    tiers: dict[str, list[str]] = {}
    for claim in claims:
        tiers.setdefault(claim["quotability"], []).append(claim["key"])

    dups = duplicate_groups(rows)
    dup_keys = {k for g in dups for k in g["keys"]}

    blocks = [render_citation_block(c, by_key[c["key"]]) for c in claims if c["key"] in by_key]

    codes: dict[str, int] = {}
    for v in violations:
        codes[v["code"]] = codes.get(v["code"], 0) + 1

    return {
        "coverage": coverage,
        "violations": violations,
        "violation_counts": dict(sorted(codes.items())),
        "n_violations": len(violations),
        "tier_counts": {k: len(v) for k, v in sorted(tiers.items())},
        "tier_keys": {k: sorted(v) for k, v in sorted(tiers.items())},
        "n_quoted_via_unseal": sum(
            1 for c in claims if str(c.get("tier_basis") or "l3u_grade") != "l3u_grade"
        ),
        "n_quotable": len(tiers.get("QUOTABLE", [])),
        "duplicate_groups": dups,
        "n_duplicate_keys": len(dup_keys),
        "n_independent_numbers": len(rows) - len(dup_keys) + len(dups),
        "citation_blocks": blocks,
        "status": "CLAIMS_CONSISTENT" if not violations and coverage["complete"] else "CLAIMS_INCONSISTENT",
    }
