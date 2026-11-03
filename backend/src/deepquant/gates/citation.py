"""引用规范登记：把 L3t 分辨率表的每一行对齐到该实验**判定时真正用的统计量**。

为什么单独一个模块
------------------
L3t 的登记表按「取某列均值」构造，但并非所有实验的判定统计量都是某一列的均值：

* 阶段 C / C2 / C3 的判定量是**格间配对差**（``spread(B) − spread(A)``、``spread(V1) − spread(V0)``），
  不是任何单格的水平值；
* 阶段 J 的判定档是 3000 万，而 CSV 里的 ``net_A_pp`` 对应 1000 万档；
* gate3 的判定窗口是 2007-2015（n=108），而目录里另有一个 2016-2026 的重跑文件。

拿「某列均值」代表这些实验的效应量，会得出完全相反的结论（阶段 C 的 +1.9390pp/t=6.37
会被登记成 +0.0141pp/t=0.09，然后被写成「分辨率不足」）。本模块的职责就是把这个
差别**逐条**查出来，并据此给出「可引用强度」分级。

纪律
----
* 只读既有产物，不重跑任何实验（``audit_only``）。
* 不修改 L3t 的产物（R5：保留原始结果 + 出更正）。
* 核对通过才谈分级；核对不通过的条目**不**给分级结论。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from deepquant.gates.selection import (
    minimum_detectable_effect,
    newey_west_mean,
    observed_power,
    t_stat,
)

#: 与 L3t / L3r / L3s 完全一致的 MDE 系数（双侧 1.96 + 80% 功效）
MDE_FACTOR = 2.8016

MATCH = "MATCH"
MISMATCH_MEAN = "MISMATCH_MEAN"
MISMATCH_N = "MISMATCH_N"
UNVERIFIABLE = "UNVERIFIABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"

MATCH_LABELS = {
    MATCH: "一致",
    MISMATCH_MEAN: "判定统计量不一致",
    MISMATCH_N: "样本数不一致",
    UNVERIFIABLE: "无法核对",
    NOT_APPLICABLE: "该实验无单一判定均值",
}

QUOTABILITY_LABELS = {
    "QUOTABLE": "可引用点估计",
    "QUOTABLE_MARGINAL": "可引用，须标 MDE",
    "DIRECTION_ONLY": "只能引方向",
    "NOT_QUOTABLE": "点估计不可信",
    "NULL_CONFIRMED": "可引用为「确认无效应」",
    "NULL_WEAK": "「未检出」但功效不足",
    "UNDERPOWERED": "只能说「无法判断」",
    "UNCLASSIFIED": "判定字符串无法识别",
    "NOT_GRADED": "核对未通过，不参与分级",
}


class CitationError(ValueError):
    """引用规范登记的前置条件不满足。"""


@dataclass(frozen=True)
class RebuiltSeries:
    """重建后的判定序列。"""

    values: tuple[float, ...]
    unit: str
    description: str
    n: int


def check_prereg_sha(path: str | Path, expected: str) -> str:
    """预登记哈希校验：被改过就拒跑，而不是带着新判据继续。"""
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected:
        raise CitationError(
            f"预登记哈希不符：期望 {expected}，实际 {digest}；预登记被改动，拒跑"
        )
    return digest


def load_prereg(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload.get("entries"), list) or not payload["entries"]:
        raise CitationError("预登记缺少 entries")
    keys = [e["key"] for e in payload["entries"]]
    if len(set(keys)) != len(keys):
        raise CitationError(f"预登记条目 key 重复：{keys}")
    return payload


def _dig(data: Any, path: str) -> Any:
    node = data
    for part in [p for p in path.split(".") if p]:
        if not isinstance(node, Mapping) or part not in node:
            raise KeyError(part)
        node = node[part]
    return node


def _dig_optional(data: Any, path: str | None) -> Any:
    if not path:
        return None
    try:
        return _dig(data, path)
    except (KeyError, TypeError):
        return None


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def rebuild_series(entry: Mapping[str, Any], root: Path) -> RebuiltSeries:
    """按预登记的声明重建该实验的**判定序列**。

    四种形态：
    * ``column``   —— 单列（最常见）；
    * ``terms``    —— 多列线性组合（如 ``spread_v1 − spread``）；
    * ``pivot``    —— 先按单元格透视再组合（如 ``2*(d[B] − d[A])``）；
    * ``json_list``—— 从结果 JSON 的列表里取字段。
    """
    spec = entry["series"]
    key = entry["key"]
    file = root / spec["path"]
    if not file.exists():
        raise CitationError(f"[{key}] 序列文件不存在：{spec['path']}")

    scale = float(spec.get("scale", 1.0))
    kind = spec["kind"]

    if kind == "json_list":
        payload = json.loads(file.read_text(encoding="utf-8"))
        items = _dig(payload, spec["list_path"]) if spec.get("list_path") else payload
        if not isinstance(items, list):
            raise CitationError(f"[{key}] {spec.get('list_path')} 不是列表")
        values = [
            float(item[spec["value"]]) * scale
            for item in items
            if isinstance(item, Mapping)
            and isinstance(item.get(spec["value"]), (int, float))
        ]
        desc = f"{spec['path']}::{spec.get('list_path') or '<root>'}[{spec['value']}]"
    else:
        frame = pd.read_csv(file)
        for column, value in (spec.get("filter") or {}).items():
            if column not in frame.columns:
                raise CitationError(f"[{key}] 缺少过滤列 {column}")
            frame = frame[frame[column] == value]
        if frame.empty:
            raise CitationError(f"[{key}] 过滤后为空（filter={spec.get('filter')}）")

        multiplier = float(spec.get("multiplier", 1.0))
        if kind == "column":
            series = _numeric(frame[spec["column"]])
            desc = f"{spec['path']}::{spec['column']}"
        elif kind == "terms":
            total = None
            for term in spec["terms"]:
                if term["column"] not in frame.columns:
                    raise CitationError(f"[{key}] 缺少列 {term['column']}")
                part = _numeric(frame[term["column"]]) * float(term["weight"])
                total = part if total is None else total + part
            series = total
            desc = " + ".join(
                f"{t['weight']:+.0f}×{t['column']}" for t in spec["terms"]
            )
            desc = f"{spec['path']}::{desc}"
        elif kind == "pivot":
            index, col, value = spec["index"], spec["pivot_column"], spec["value_column"]
            wide = frame.pivot_table(index=index, columns=col, values=value, aggfunc="first")
            total = None
            for term in spec["terms"]:
                if term["key"] not in wide.columns:
                    raise CitationError(
                        f"[{key}] 透视后缺少单元格 {term['key']}（有 {list(wide.columns)}）"
                    )
                part = _numeric(wide[term["key"]]) * float(term["weight"])
                total = part if total is None else total + part
            series = total
            desc = " + ".join(f"{t['weight']:+.0f}×{value}[{t['key']}]" for t in spec["terms"])
            desc = f"{spec['path']}::{desc}"
        else:
            raise CitationError(f"[{key}] 未知序列形态 {kind}")

        values = [
            float(v) * multiplier * scale
            for v in series.dropna().tolist()
            if math.isfinite(float(v))
        ]

    if len(values) < 10:
        raise CitationError(f"[{key}] 重建后有效样本仅 {len(values)} 条")

    return RebuiltSeries(
        values=tuple(values), unit=entry["unit"], description=desc, n=len(values)
    )


def read_stat(summary: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    """从 summary 里读出该实验**判定时用的**统计量。

    返回 ``None`` 表示该条目不适用（预登记已声明），而不是「读不到」——
    读不到必须抛错，否则会把「没找到」当成「没问题」。
    """
    spec = entry["stat"]
    if spec.get("mean") is None:
        if not entry.get("stat_note"):
            raise CitationError(f"[{entry['key']}] stat.mean 为空但未声明 stat_note")
        return {
            "applicable": False,
            "note": entry["stat_note"],
            "mean": None,
            "t": None,
            "n": None,
        }

    mean_raw = None
    for candidate in [spec["mean"], *(spec.get("mean_alt_keys") or [])]:
        value = _dig_optional(summary, candidate)
        if isinstance(value, (int, float)):
            mean_raw = float(value)
            break
    if mean_raw is None:
        raise CitationError(f"[{entry['key']}] 读不到判定均值：{spec['mean']}")

    t_value = None
    for candidate in [spec.get("t"), spec.get("t_alt")]:
        value = _dig_optional(summary, candidate)
        if isinstance(value, (int, float)):
            t_value = float(value)
            break

    n_value = None
    value = _dig_optional(summary, spec.get("n"))
    if isinstance(value, (int, float)):
        n_value = int(value)

    return {
        "applicable": True,
        "mean": mean_raw * float(spec.get("scale", 1.0)),
        "t": t_value,
        "n": n_value,
    }


def read_verdict(summary: Mapping[str, Any], entry: Mapping[str, Any]) -> str | None:
    """取判定字符串**原文**；没有就返回 None，绝不猜。"""
    if entry.get("verdict"):
        value = _dig_optional(summary, entry["verdict"])
        return str(value) if isinstance(value, str) else None
    literal = entry.get("verdict_literal")
    return str(literal) if literal else None


def classify_verdict(text: str | None, semantics: Mapping[str, Any]) -> bool | None:
    """把判定字符串映射到「是否检出」。

    先查否定词（``ABSORBED`` / ``NOT_DETECTED`` / ``FAILURE`` …），再查肯定词。
    两者都不含则返回 ``None``——不得默认成任一边。
    """
    if not text:
        return None
    upper = text.upper()
    lowered = text
    for marker in semantics["negative_markers"]:
        if marker.isupper():
            if marker in upper:
                return False
        elif marker in lowered:
            return False
    for marker in semantics["positive_markers"]:
        if marker.upper() in upper:
            return True
    return None


def summarise_series(values: Sequence[float], *, nw_lag: int = 3) -> dict[str, Any]:
    """与 L3t 完全同一实现：iid t、NW(3) t、MDE=2.8016×se_NW。"""
    base = t_stat(values)
    nw = newey_west_mean(values, lag=nw_lag)
    return {
        "n": len(values),
        "mean": base["mean"],
        "se_iid": base["se"],
        "se_nw": nw["se_nw"],
        "t_iid": base["t"],
        "t_nw3": nw["t_nw"],
        "mde": minimum_detectable_effect(nw["se_nw"]),
        "power": observed_power(nw["t_nw"]),
    }


def compare_row(
    l3t_row: Mapping[str, Any],
    stat: Mapping[str, Any],
    tolerance: Mapping[str, Any],
) -> dict[str, Any]:
    """把 L3t 登记行与判定统计量对比，给出主分类（+ t_only 子标记）。"""
    key = l3t_row["key"]
    if not stat.get("applicable"):
        return {"class": NOT_APPLICABLE, "t_only": False, "detail": stat.get("note", "")}

    if stat.get("mean") is None:
        return {"class": UNVERIFIABLE, "t_only": False, "detail": "判定均值读不到"}

    mean_l3t = float(l3t_row["mean"])
    mean_stat = float(stat["mean"])
    mean_diff = mean_l3t - mean_stat

    n_stat = stat.get("n")
    if n_stat is not None and int(n_stat) != int(l3t_row["n"]):
        # mean 一致而 n 不同 ⇒ 效应量本身没错，只是分母口径不同（如回归用全样本、
        # 而结论落在分段上）。这与「用了另一个窗口的文件」是两件事，必须分开。
        mean_identical = abs(mean_diff) <= float(tolerance["mean_abs"])
        return {
            "class": MISMATCH_N,
            "t_only": False,
            "mean_identical": mean_identical,
            "detail": (
                f"n: L3t={l3t_row['n']} vs 判定={n_stat}；"
                f"mean: {mean_l3t:+.4f} vs {mean_stat:+.4f}"
                + ("（mean 一致，仅分母口径不同）" if mean_identical else "（mean 也不同，窗口取错）")
            ),
            "mean_diff": mean_diff,
        }

    if abs(mean_diff) > float(tolerance["mean_abs"]):
        return {
            "class": MISMATCH_MEAN,
            "t_only": False,
            "detail": f"mean: L3t={mean_l3t:+.6f} vs 判定={mean_stat:+.6f}（差 {mean_diff:+.6f}）",
            "mean_diff": mean_diff,
        }

    t_stat_value = stat.get("t")
    t_l3t = l3t_row.get("t_nw3")
    if t_stat_value is not None and t_l3t is not None:
        if abs(float(t_l3t) - float(t_stat_value)) > float(tolerance["t_abs"]):
            return {
                "class": MISMATCH_MEAN,
                "t_only": True,
                "detail": (
                    f"mean 一致但 t 不同：L3t={float(t_l3t):+.4f} vs 判定={float(t_stat_value):+.4f}"
                    "（多为 NW 滞后或估计量口径差异）"
                ),
                "mean_diff": mean_diff,
            }

    return {"class": MATCH, "t_only": False, "detail": "", "mean_diff": mean_diff}


def grade_quotability(
    *,
    effect: float | None,
    mde: float | None,
    detected: bool | None,
    power: float | None,
    rules: Mapping[str, Any],
) -> dict[str, Any]:
    """按预登记规则给出可引用强度。

    「检出」看效应量相对 MDE 的倍数；「未检出」看功效。两者不可互换——
    功效不足时「没检出」只能写成「无法判断」。
    """
    if detected is None:
        return {"tier": "UNCLASSIFIED", "ratio": None, "reason": "判定字符串无法识别"}
    if mde is None or mde != mde or mde <= 0:
        return {"tier": "NOT_GRADED", "ratio": None, "reason": "MDE 不可用"}

    if detected:
        if effect is None:
            return {"tier": "NOT_GRADED", "ratio": None, "reason": "效应量不可用"}
        ratio = abs(effect) / mde
        if ratio >= 2.0:
            tier = "QUOTABLE"
        elif ratio >= 1.0:
            tier = "QUOTABLE_MARGINAL"
        elif ratio >= 0.5:
            tier = "DIRECTION_ONLY"
        else:
            tier = "NOT_QUOTABLE"
        return {
            "tier": tier,
            "ratio": ratio,
            "reason": f"|效应|/MDE = {abs(effect):.4f}/{mde:.4f} = {ratio:.2f}",
        }

    p = power if power is not None and power == power else 0.0
    if p >= 0.8:
        tier = "NULL_CONFIRMED"
    elif p >= 0.5:
        tier = "NULL_WEAK"
    else:
        tier = "UNDERPOWERED"
    return {
        "tier": tier,
        "ratio": None,
        "reason": f"未检出，事后功效 {p:.2%}",
    }


def build_row(
    entry: Mapping[str, Any],
    l3t_row: Mapping[str, Any],
    summary: Mapping[str, Any],
    root: Path,
    *,
    prereg: Mapping[str, Any],
    nw_lag: int = 3,
) -> dict[str, Any]:
    """单条：重建序列 → 重算 MDE → 核对 → 分级。"""
    rebuilt = rebuild_series(entry, root)
    stats = summarise_series(rebuilt.values, nw_lag=nw_lag)
    stat = read_stat(summary, entry)
    verdict = read_verdict(summary, entry)
    detected = classify_verdict(verdict, prereg["verdict_semantics"])
    comparison = compare_row(l3t_row, stat, prereg["tolerance"])

    # 分级用**判定统计量**的效应量（不是重建序列的均值：两者对 phasec 类本就不同）
    effect = stat.get("mean") if stat.get("applicable") else None
    comparison_class = comparison["class"]
    basis = entry.get("grading_basis", "stat_mean")

    if comparison_class == MATCH:
        gradeable = True
    elif comparison_class == NOT_APPLICABLE:
        # self_series：本实验自身持有判定序列 ⇒ 序列均值就是判定量，可分级
        # multi_criteria：判定由多门槛/区间规则给出，序列均值不是判定量 ⇒ 只给参考值
        #   否则会把「净效应被吸收」误算成「这个序列没有效应」（如 phasel 的毛 alpha t=3.13）
        gradeable = basis == "self_series"
        if gradeable and effect is None:
            effect = stats["mean"]
    else:
        gradeable = False
    # 按修正后的数字（判定统计量 + 重建序列的 MDE）算出「本该是什么分级」，
    # 但只有核对通过的条目才把它作为正式分级——预登记明写 MISMATCH 不下分级结论。
    reference = grade_quotability(
        effect=effect,
        mde=stats["mde"],
        detected=detected,
        power=stats["power"],
        rules=prereg["quotability_rules"],
    )
    if not gradeable:
        if comparison_class == NOT_APPLICABLE and not gradeable:
            why = "判定不是该序列的均值（multi_criteria），序列均值不能代表该结论"
        else:
            why = f"核对未通过（{MATCH_LABELS[comparison_class]}），不给分级"
        grade = {
            "tier": "NOT_GRADED",
            "ratio": reference.get("ratio"),
            "reason": why,
            "reference_tier": reference["tier"],
            "reference_reason": reference.get("reason"),
        }
        # 参考分级用的效应量可能与正式分级不同（multi_criteria 用的是序列均值），单独记
        grade["reference_effect"] = effect
    else:
        grade = reference
        grade["grading_basis"] = basis

    return {
        "key": entry["key"],
        "family": entry["family"],
        "unit": entry["unit"],
        "verdict_original": verdict,
        "verdict_recorded_l3t": l3t_row["verdict"],
        "verdict_matches_l3t": bool(verdict is not None and verdict == l3t_row["verdict"]),
        "detected": detected,
        "series": rebuilt.description,
        "series_n": rebuilt.n,
        "l3t": {
            "n": int(l3t_row["n"]),
            "mean": float(l3t_row["mean"]),
            "t_nw3": float(l3t_row["t_nw3"]),
            "mde": float(l3t_row["mde"]),
        },
        "stat": {
            "applicable": stat.get("applicable"),
            "mean": stat.get("mean"),
            "t": stat.get("t"),
            "n": stat.get("n"),
        },
        "recomputed": stats,
        "comparison": comparison,
        "grade": grade,
    }
