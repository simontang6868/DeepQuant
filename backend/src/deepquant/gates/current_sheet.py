"""当前有效引用清单的生成逻辑。

与 `deliverable.py`（L3y 的对外定稿）的分工：

- `deliverable.py` 回答「哪些结论可以对外说、原话是什么」，它把定稿做成**抄袭来源**；
- 这里回答「在多重检验与依赖结构复核之后，这 6 条的**当前状态**是什么」，
  并把「统计显著 / 经济有意义 / 可交易」三件事拆成三列。

两者都是脚本产物、都回源到同一批权威产物，但解决的问题不同：
定稿面向「怎么写」，本清单面向「凭什么还能这么写」。

一条刻意的设计：经济属性的判定**不靠人工映射表**，而是按判定量类型机械推导
（净收益 / 毛利 alpha / 价差 / 回归系数 / 其他）。人工映射表是这类审计里
最容易悄悄失真的地方——某条被挪进「净收益」一栏，就成了没做过的证据。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import family as fam
from . import multiplicity as mp

#: 本清单的版本号。内容或依据变化时必须改，否则读清单的人无法判断看到的是哪一版。
SHEET_VERSION = "L3AB-CURRENT-v1"

#: 生成清单时依赖的权威产物（相对仓库根）。
AUTHORITATIVE_INPUTS = (
    "reports/gates/H-L3U-CITATION-REGISTRY/l3u_citation.json",
    "reports/gates/H-L3Z-MULTIPLICITY/l3z_multiplicity.json",
    "reports/gates/H-L3AA-DEPENDENCE/l3aa_dependence.json",
    "reports/gates/H-L3AB-FAMILY/l3ab_family.json",
    "docs/gates/l3v_claims.json",
    "docs/gates/l3ab_family_decisions.json",
    "reports/L3Z_LAG_SENSITIVITY.json",
)


def economic_basis(key: str) -> str:
    """按判定量的**类型**判经济属性，不按条目的人工归类。

    顺序有意义：先判净收益（名称里带 net 的），再判毛利 alpha，再判价差，
    最后才是回归系数与其他。顺序写死是为了可复现——换个顺序，个别条目会换栏。
    """
    k = key.lower()
    if "net" in k:
        return "net_return"
    if "alpha" in k:
        return "gross_alpha"
    if k.endswith("_d") or "spread" in k or "delta" in k:
        return "cross_section_spread"
    if "max5" in k or "ic" in k:
        return "regression_coef"
    return "other"


BASIS_LABEL = {
    "net_return": "扣除成本后的组合净收益（已带成本口径与资金档）",
    "gross_alpha": "毛利 alpha（未扣成本）",
    "cross_section_spread": "横截面价差（不是任何组合的收益水平）",
    "regression_coef": "截面回归系数 / 秩相关（非收益量）",
    "other": "其他（非收益量）",
}

#: 表格里用的短标签。长标签带括号解释，直接进表格会把列挤变形。
BASIS_SHORT = {
    "net_return": "净收益",
    "gross_alpha": "毛利 alpha",
    "cross_section_spread": "横截面价差",
    "regression_coef": "回归系数",
    "other": "其他",
}

#: 哪些经济属性可以被读成「扣成本后有正向收益证据」。只有净收益一类。
COST_EVIDENCE_BASES = frozenset({"net_return"})


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_hashes(root: Path) -> dict[str, str]:
    out = {}
    for rel in AUTHORITATIVE_INPUTS:
        p = Path(root) / rel
        out[rel] = sha(p) if p.exists() else "MISSING"
    return out


def lag_profile(root: Path, keys: Sequence[str]) -> dict[str, dict]:
    """每条在 NW 滞后网格下的分级轨迹。用来说明「分级对滞后是否敏感」。"""
    path = Path(root) / "reports" / "L3Z_LAG_SENSITIVITY.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    lags = [str(x) for x in data.get("lags", [])]
    out = {}
    for row in data.get("rows", []):
        if row["key"] not in keys:
            continue
        tiers = [row["per_lag"][l]["tier"] for l in lags]
        out[row["key"]] = {
            "lags": lags,
            "tiers": tiers,
            "distinct": sorted(set(tiers)),
            "crosses": len(set(tiers)) > 1,
        }
    return out


def build_status(
    *,
    root: Path,
    inventory: Sequence[Mapping[str, Any]],
    family_result: Mapping[str, Any],
    cards: Sequence[Mapping[str, Any]],
    l3u_rows: Sequence[Mapping[str, Any]],
    retractions: Sequence[Mapping[str, Any]],
    l3aa: Mapping[str, Any],
) -> dict[str, Any]:
    """逐条拼 9 个字段。所有取值要么由产物算出，要么标 UNKNOWN——不留空。"""
    card_by_key = {c["key"]: c for c in cards}
    row_by_key = {r["key"]: r for r in l3u_rows}
    inv_by_key = {r["key"]: r for r in inventory}

    primary = next(s for s in family_result["specs"] if s["adopted"])
    by_spec = {s["id"]: s for s in family_result["specs"]}
    items = {i["key"]: i for i in primary["items"]}
    m27 = by_spec.get("S2_m27", {}).get("items") or []
    m27_by_key = {i["key"]: i for i in m27}
    lag_prof = lag_profile(root, list(inv_by_key))
    banned = {}
    for r in retractions:
        if r.get("claim_key"):
            banned.setdefault(r["claim_key"], []).append(r["banned_phrasing"])

    quotable = [
        k for k, c in card_by_key.items()
        if c.get("quotability", "").startswith("QUOTABLE") or c.get("quotability") == "NULL_CONFIRMED"
    ]

    statuses = []
    for key in sorted(quotable, key=lambda k: (card_by_key[k].get("quotability"), k)):
        card = card_by_key[key]
        inv = inv_by_key.get(key, {})
        item = items.get(key, {})
        row = row_by_key.get(key, {})
        comp = row.get("comparison") or {}
        basis = economic_basis(key)
        lp = lag_prof.get(key)
        it27 = m27_by_key.get(key)

        # 1 数字可追溯
        trace = {
            "l3u_class": comp.get("class"),
            "detail": comp.get("detail"),
        }
        # 2 独立核对：L3u 判 MATCH，或后续层（L3w 解封 / L3x 仅 t / L3z 审计）明确复核通过。
        # 只认「有复核记录」这一件事；不把「分级不是 NOT_GRADED」直接当成核对通过。
        tier_basis = card.get("tier_basis")
        later_ok = (
            tier_basis in {"l3w_unseal", "l3x_t_only"}
            and card.get("quotability") not in (None, "NOT_GRADED")
        )
        verified = comp.get("class") == "MATCH" or later_ok
        verification = {
            "l3u_class": comp.get("class"),
            "later_layer": tier_basis,
            "later_note": card.get("quotability_note"),
            "conclusion": "PASS" if verified else "NOT_PASS",
        }
        # 3 多重检验
        multiplicity = {
            "m": primary["m"],
            "bonferroni": bool(item.get("bonferroni")),
            "holm": bool(item.get("holm")),
            "bh": bool(item.get("bh")),
            "by": bool(item.get("by")),
            "at_m27": (
                {
                    "bonferroni": bool(it27.get("bonferroni")),
                    "holm": bool(it27.get("holm")),
                    "bh": bool(it27.get("bh")),
                    "by": bool(it27.get("by")),
                }
                if it27
                else None
            ),
        }
        all_pass = all(multiplicity[k] for k in ("bonferroni", "holm", "bh", "by"))
        # 4 敏感性
        sensitivity = {
            "lag_crosses_tier": bool(lp and lp["crosses"]),
            "lag_tiers": lp["tiers"] if lp else None,
            "m_changes_verdict": bool(it27 and (it27.get("bonferroni") != item.get("bonferroni"))),
        }
        statuses.append(
            {
                "key": key,
                "tier": card.get("quotability"),
                "headline": card.get("headline"),
                "hypothesis": inv.get("hypothesis"),
                "n": inv.get("n"),
                "window": inv.get("window"),
                "p": inv.get("p"),
                "t_nw3": inv.get("t_nw3"),
                "traceability": trace,
                "verified_independently": verified,
                "verification": verification,
                "multiplicity": multiplicity,
                "passes_all_four": all_pass,
                "sensitivity": sensitivity,
                "economic_basis": basis,
                "economic_label": BASIS_LABEL[basis],
                "economic_short": BASIS_SHORT[basis],
                "economic_note": (
                    "判定为「净效应被成本吸收」，方向与正向收益相反"
                    if "ABSORBED" in str(card.get("headline", "")) + str(inv.get("verdict_original"))
                    else ""
                ),
                "cost_adjusted_positive_evidence": basis in COST_EVIDENCE_BASES,
                "out_of_sample_evidence": False,
                "out_of_sample_note": "全部为历史样本内结果；无前瞻期数据参与",
                "allowed_wording": card.get("headline"),
                "limits_of_use": list(card.get("limits_of_use") or []),
                "forbidden": banned.get(key, []),
                "null_claim": card.get("quotability") == "NULL_CONFIRMED",
            }
        )

    # 全族层面的三条限制（写给读者，不带具体条目）
    global_limits = [
        f"多重检验主口径 m={primary['m']}（数值指纹去重）；m=27 为替代口径，"
        f"两者对 5 条正面结论的结论相同，仅 `phasel_alpha` 在 m=27 + Bonferroni 下掉出。",
        "Benjamini–Yekutieli 在任意依赖下控制错误发现率；它不验证正相依（PRDS）假设，只是绕开。",
        "标准误一律 Newey-West(3)，p 用 t 分布双侧；滞后网格 {0,1,2,3,4,6} 下的分级轨迹单独列出。",
        "全部结论都在历史样本内产生，不存在前瞻期证据；「统计显著」不等于「经济有意义」，"
        "「净收益为正（样本内）」也不等于「可实盘交易」。",
        "本清单只覆盖 L3u 登记在册的 27 条。至少有一个已产生结论的实验（`phasei_out`，exQ5 可成交）"
        "不在该家族内，因此也不在本清单的校正范围内。",
    ]
    return {
        "sheet_version": SHEET_VERSION,
        "m_primary": primary["m"],
        "m_alt": by_spec.get("S2_m27", {}).get("m"),
        "n_claims_registered": len(inventory),
        "n_quotable": len(statuses),
        "claims": statuses,
        "global_limits": global_limits,
        "l3aa_status": l3aa.get("status"),
    }
