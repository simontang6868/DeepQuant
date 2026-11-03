"""分辨率登记：用同一套实现重算各实验的最小可检测效应（MDE）。

为什么需要这个东西：项目里不同实验的 MDE 相差两个数量级（Gate 6 存档 0.4525pp、
L3r 0.926pp、阶段 O 的 se 只有 0.03pp），但从来没人把它们放在一张表上。结果引用
某条结论时，读者无法判断「这个效应量，本项目的设计到底分辨得出来吗」。

本模块只做三件事：
1. **统一单位**：所有序列换算成百分点（原始小数的 ×100），并做量级自检；
2. **统一估计量**：iid t、NW(3) t、MDE = 2.8016×se_nw（与全项目一致，不新造统计量）；
3. **按口径族登记**：族内可比较，跨族不得并列（se 同时取决于 n、截面标的数 k 与波动）。

⚠️ MDE 是「该设计的分辨率」，不是「效应有多可信」。效应是否真实还取决于预登记、
稳健性与成本；本模块不产生任何新证据。
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from deepquant.gates.selection import (
    minimum_detectable_effect,
    newey_west_mean,
    observed_power,
    t_stat,
)

#: MDE 常数（双侧 5%、功效 80%），与全项目一致
MDE_FACTOR = 2.8016

FAMILY_LABELS = {
    "A_cross_spread": "月度截面多空价差（Q5−Q1 / long−short）",
    "B_long_only": "月度 long-only 相对池内等权基准的超额",
    "C_regression": "月度截面回归系数",
    "D_ic": "逐期秩相关 IC（无量纲）",
    "E_fold": "Walk-Forward 折级收益（n 是折数）",
}


class ResolutionError(ValueError):
    """分辨率登记的前置条件不满足。"""


@dataclass(frozen=True)
class SeriesSpec:
    """一条待登记的月度/折级序列。"""

    key: str
    family: str
    label: str
    path: str
    column: str
    unit: str  # "decimal" | "pp" | "ic"
    source: str = "csv"  # "csv" | "json"
    item_path: str = ""  # json：取 list 的路径，如 "result.folds"
    filters: Mapping[str, str] = field(default_factory=dict)
    k_column: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.family not in FAMILY_LABELS:
            raise ResolutionError(f"未知口径族：{self.family}")
        if self.unit not in {"decimal", "pp", "ic"}:
            raise ResolutionError(f"未知单位：{self.unit}")


def _to_pp(values: Sequence[float], unit: str) -> list[float]:
    """小数口径 ×100，pp 口径原样，IC 口径原样（无量纲）。"""
    clean = [float(v) for v in values if v is not None and float(v) == float(v)]
    if unit == "decimal":
        return [v * 100.0 for v in clean]
    return clean


def _dig(data: Any, path: str) -> Any:
    node = data
    for part in [p for p in path.split(".") if p]:
        node = node[part]
    return node


def load_series(spec: SeriesSpec, root: Path) -> tuple[list[float], dict[str, Any]]:
    """读一条序列，返回 (换算后的值, 元信息)。"""
    file = root / spec.path
    if not file.exists():
        raise ResolutionError(f"[{spec.key}] 文件不存在：{spec.path}")

    k_median: float | None = None
    if spec.source == "csv":
        frame = pd.read_csv(file)
        for column, value in spec.filters.items():
            if column not in frame.columns:
                raise ResolutionError(f"[{spec.key}] 缺少过滤列 {column}")
            frame = frame[frame[column] == value]
        if frame.empty:
            raise ResolutionError(f"[{spec.key}] 过滤后为空（filters={dict(spec.filters)}）")
        raw = pd.to_numeric(frame[spec.column], errors="coerce").dropna().tolist()
        if spec.k_column and spec.k_column in frame.columns:
            k_median = float(
                pd.to_numeric(frame[spec.k_column], errors="coerce").median()
            )
    else:
        payload = json.loads(file.read_text(encoding="utf-8"))
        items = _dig(payload, spec.item_path) if spec.item_path else payload
        if not isinstance(items, list):
            raise ResolutionError(f"[{spec.key}] {spec.item_path} 不是列表")
        raw = [item.get(spec.column) for item in items]
        raw = [v for v in raw if v is not None and isinstance(v, (int, float))]
        if spec.k_column:
            ks = [item.get(spec.k_column) for item in items]
            ks = [float(v) for v in ks if isinstance(v, (int, float))]
            if ks:
                k_median = statistics.median(ks)

    if len(raw) < 10:
        raise ResolutionError(f"[{spec.key}] 有效样本仅 {len(raw)} 条")

    values = _to_pp(raw, spec.unit)
    meta = {
        "path": spec.path,
        "column": spec.column,
        # 与 summary 里归一化后的 unit（"pp"/"IC"）区分开，否则会把它覆盖掉
        "source_unit": spec.unit,
        "n": len(values),
        "k_median": k_median,
        "filters": dict(spec.filters),
    }
    return values, meta


def summarise(
    values: Sequence[float],
    *,
    unit: str,
    nw_lag: int = 3,
) -> dict[str, Any]:
    """统一估计量：均值 / 中位 / iid t / NW(3) t / MDE。"""
    base = t_stat(values)
    nw = newey_west_mean(values, lag=nw_lag)
    mde = minimum_detectable_effect(nw["se_nw"])
    return {
        "n": len(values),
        "unit": "IC" if unit == "ic" else "pp",
        "mean": base["mean"],
        "median": float(pd.Series(list(values)).median()),
        "sd": statistics.stdev(values) if len(values) >= 2 else float("nan"),
        "se_iid": base["se"],
        "se_nw": nw["se_nw"],
        "t_iid": base["t"],
        "t_nw3": nw["t_nw"],
        "mde": mde,
        "power": observed_power(nw["t_nw"]),
        "nw_variance_fell_back": nw["variance_fell_back_to_g0"],
    }


def flag_row(row: Mapping[str, Any]) -> list[str]:
    """量级与自相关自检。单位写错会被第一条抓住，而不是静默通过。"""
    flags: list[str] = []
    if row["unit"] == "pp":
        sd = row.get("sd")
        mean = row.get("mean")
        if row.get("family") == "E_fold":
            # 折级收益的单位是「%/折」，量级比月度 pp 大得多，用另一套界
            if sd is None or not (1.0 <= sd <= 100.0):
                flags.append(f"sd={sd:.3f}%/折 越界 [1,100]：疑似单位错")
            if mean is None or abs(mean) > 100.0:
                flags.append(f"mean={mean:.3f}%/折 越界 |·|<=100：疑似单位错")
        else:
            if sd is None or not (0.2 <= sd <= 30.0):
                flags.append(f"sd={sd:.3f}pp 越界 [0.2,30]：疑似单位错")
            if mean is None or abs(mean) > 5.0:
                flags.append(f"mean={mean:.3f}pp 越界 |·|<=5：疑似单位错")
    t_nw = row.get("t_nw3")
    if t_nw is not None and t_nw == t_nw and abs(t_nw) > 8 and row["n"] < 300:
        flags.append(f"|t_nw|={abs(t_nw):.2f} > 8 且 n={row['n']} < 300：疑似重复计数或强自相关")
    if row["n"] < 50:
        flags.append(f"n={row['n']} < 50：分辨率声明不可靠，族中位不采用")
    return flags


def family_rollup(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """族内中位/极值。**只用通过自检的条目算中位**（n≥50 且无单位越界）。"""
    out: dict[str, Any] = {}
    for family in FAMILY_LABELS:
        members = [r for r in rows if r["family"] == family]
        if not members:
            continue
        usable = [r for r in members if r["n"] >= 50 and not r["unit_suspect"]]
        mdes = [r["mde"] for r in usable if r["mde"] == r["mde"]]
        out[family] = {
            "label": FAMILY_LABELS[family],
            "n_series": len(members),
            "n_usable": len(usable),
            "mde_median": statistics.median(mdes) if mdes else None,
            "mde_min": min(mdes) if mdes else None,
            "mde_max": max(mdes) if mdes else None,
            "unit": members[0]["unit"],
        }
    return out


def best_resolution(rollup: Mapping[str, Any], family: str) -> float | None:
    item = rollup.get(family)
    return item["mde_min"] if item else None


def references_below_best_resolution(
    rows: Sequence[Mapping[str, Any]], rollup: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """哪些条目「自报的效应量」小于同族最好分辨率——换更强的设计可能就检不出。

    注意这不是在质疑该结论的 t：它的 t 由它自己的 se 决定。这里问的是
    「若用本项目同族里分辨率最好的设计去测同样大小的效应，还测得出吗」。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["unit"] != "pp" or row["unit_suspect"]:
            continue
        best = best_resolution(rollup, row["family"])
        if best is None:
            continue
        if abs(row["mean"]) < best:
            out.append(
                {
                    "key": row["key"],
                    "family": row["family"],
                    "effect_pp": row["mean"],
                    "family_best_mde_pp": best,
                    "ratio": abs(row["mean"]) / best if best else None,
                    "note": row.get("note", ""),
                }
            )
    return sorted(out, key=lambda item: item["ratio"] or 0.0)


def check_prereg_sha(path: str | Path, expected: str) -> str:
    import hashlib

    file = Path(path)
    if not file.exists():
        raise ResolutionError(f"预登记不存在：{file}")
    actual = hashlib.sha256(file.read_bytes()).hexdigest()
    if not actual.startswith(expected):
        raise ResolutionError(
            f"预登记被改动：期望 {expected}…，实际 {actual}…；拒绝运行以免事后改判据"
        )
    return actual


__all__ = [
    "FAMILY_LABELS",
    "MDE_FACTOR",
    "ResolutionError",
    "SeriesSpec",
    "best_resolution",
    "check_prereg_sha",
    "family_rollup",
    "flag_row",
    "load_series",
    "references_below_best_resolution",
    "summarise",
]
