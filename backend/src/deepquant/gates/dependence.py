"""多重检验的依赖结构：把「同期、同池、同族」从说法变成可量的数。

L3z 审计报告第 8 节留了一句「BH 的依赖结构：未验证」。这个模块负责把它做成
可计算的量，但**刻意不声称能验证 PRDS**：

- 两两 Pearson ρ 只是二阶矩的描述。PRDS 是关于「一个检验的 p 值随另一个变小
  而不变大」的整体联合性质，两两相关既不能证明它、也不能否证它。
- 因此这里的输出只有两种用途：
  1. 给出「这些检验到底有多同步」的实测分布，让读者自己判断 PRDS 是否可信；
  2. 给 BY 校正提供一个参照——BY 不需要任何相依假设，所以它的结论不受这里
     任何一个数字影响。

一条容易踩的坑：本项目各阶段的月度文件日期列并不统一。一部分文件同时有
``signal_date`` 与 ``month``（前者是信号月、后者是持有月），另一部分只有
``signal_date``。如果不同序列按不同口径对齐，会凭空造出 1 个月的错位、
把真实相关性压低。这里的处理不是「假定对齐正确」，而是**把三个偏移都算出来**，
并以对「相关性高」这一假设最不利的方式（取 |ρ| 的最大值）作为主口径。
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: 日期列候选，按优先级。``month`` 是持有月，语义上更适合与收益对齐；
#: 只有 ``signal_date`` 的文件会被标记出来，供上层披露口径差异。
DATE_COLUMNS = ("month", "signal_date", "date")

#: 相关性主口径的偏移网格（月）。
DEFAULT_SHIFTS = (-1, 0, 1)


class DependenceError(ValueError):
    """依赖诊断的输入有问题（与「某两条不相关」是两件事）。"""


class SeriesFileMissing(DependenceError):
    """序列文件不存在。

    与 ``SeriesFilterEmpty`` 分开是有意为之：项目里「文件不存在」被当成
    「确认没有事件」已经错过 10 次（东财分红表对退市股返 0 行那次最典型）。
    两个错误必须能被上游区分，不能都盖成「无数据」。
    """


class SeriesFilterEmpty(DependenceError):
    """文件存在，但按 filters 筛完一行不剩。"""


class SeriesColumnMissing(DependenceError):
    """文件里没有需要的列。"""


class SeriesMonthNotUnique(DependenceError):
    """同一月份出现多行。若不报错，后面的 dict 会静默覆盖，相关性会算错。"""


@dataclass(frozen=True)
class MonthSeries:
    """一条逐月序列，保留月份索引（L3u 的 load_series 会丢索引，不能复用）。"""

    key: str
    path: str
    column: str
    date_column: str
    values: Mapping[str, float]
    filters: Mapping[str, str] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def months(self) -> list[str]:
        return sorted(self.values)

    @property
    def derived_date(self) -> bool:
        """日期是否退化为 signal_date（可能与有 month 的序列差 1 个月）。"""
        return self.date_column != "month"

    def span(self) -> tuple[str, str]:
        ms = self.months
        if not ms:
            raise DependenceError(f"[{self.key}] 序列为空")
        return ms[0], ms[-1]


# --------------------------------------------------------------------------
# 月份索引
# --------------------------------------------------------------------------


def month_key(text: Any) -> str:
    """把 ``2007-02-28`` / ``2007-02`` / ``200702`` 统一成 ``2007-02``。"""
    raw = str(text).strip()
    if len(raw) >= 7 and raw[4] == "-":
        return raw[:7]
    if len(raw) == 6 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:]}"
    raise DependenceError(f"无法识别的月份：{text!r}")


def _month_index(key: str) -> int:
    year, month = key.split("-")
    return int(year) * 12 + (int(month) - 1)


def shift_month(key: str, shift: int) -> str:
    idx = _month_index(key) + int(shift)
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------


def load_monthly(
    root: Any,
    *,
    key: str,
    path: str,
    column: str,
    filters: Mapping[str, str] | None = None,
) -> MonthSeries:
    """读一条带月份索引的序列。

    三种失败分别抛不同异常（文件不存在 / 筛完为空 / 缺列），
    不接受「反正都是没数据」的合并处理。
    """
    from pathlib import Path

    file = Path(root) / path
    if not file.exists():
        raise SeriesFileMissing(f"[{key}] 文件不存在：{path}")

    filters = dict(filters or {})
    with file.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        if column not in header:
            raise SeriesColumnMissing(f"[{key}] {path} 缺列 {column}（现有 {header}）")
        date_column = next((c for c in DATE_COLUMNS if c in header), None)
        if date_column is None:
            raise SeriesColumnMissing(
                f"[{key}] {path} 没有任何日期列（候选 {DATE_COLUMNS}）"
            )
        missing = [c for c in filters if c not in header]
        if missing:
            raise SeriesColumnMissing(f"[{key}] {path} 缺过滤列 {missing}")

        values: dict[str, float] = {}
        n_rows_raw = 0
        for record in reader:
            n_rows_raw += 1
            if any(str(record.get(c)) != str(v) for c, v in filters.items()):
                continue
            raw = record.get(column)
            if raw is None or str(raw).strip() == "":
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            if math.isnan(value):
                continue
            mk = month_key(record[date_column])
            if mk in values:
                raise SeriesMonthNotUnique(
                    f"[{key}] {path} 月份 {mk} 出现多行"
                    f"（filters={filters}）；若是有意分组，必须补 filters"
                )
            values[mk] = value

    if not values:
        raise SeriesFilterEmpty(
            f"[{key}] {path} 有 {n_rows_raw} 行，但按 filters={filters} 与列 {column} "
            "筛完一行不剩"
        )
    return MonthSeries(
        key=key,
        path=path,
        column=column,
        date_column=date_column,
        values=values,
        filters=filters,
    )


# --------------------------------------------------------------------------
# 两两依赖
# --------------------------------------------------------------------------


def align(
    a: MonthSeries, b: MonthSeries, shift: int = 0
) -> tuple[list[float], list[float], list[str]]:
    """按月份取交集。``shift`` 把 b 的月份整体移动，用来吸收对齐口径差异。"""
    moved = {shift_month(k, shift): v for k, v in b.values.items()}
    common = sorted(set(a.values) & set(moved))
    return [a.values[m] for m in common], [moved[m] for m in common], common


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson 相关。样本量或方差不足时返回 nan（不返回 0——0 会被读成「无相关」）。"""
    n = len(xs)
    if n != len(ys):
        raise DependenceError("配对长度不一致")
    if n < 3:
        return float("nan")
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def rho_p_value(rho: float, n: int) -> float:
    """相关系数的 t 检验双侧 p 值（H0: ρ=0，t 自由度 n-2）。"""
    from .multiplicity import t_two_sided_p

    if n < 3 or math.isnan(rho):
        return float("nan")
    if abs(rho) >= 1.0:
        return 0.0
    t = rho * math.sqrt(n - 2) / math.sqrt(1.0 - rho * rho)
    return t_two_sided_p(t, n - 2)


def overlap(a: MonthSeries, b: MonthSeries) -> dict[str, Any]:
    """窗口重叠。"""
    common = set(a.values) & set(b.values)
    shorter = min(a.n, b.n)
    return {
        "n_overlap": len(common),
        "n_a": a.n,
        "n_b": b.n,
        "ratio": (len(common) / shorter) if shorter else 0.0,
    }


def pairwise_dependence(
    a: MonthSeries,
    b: MonthSeries,
    shifts: Iterable[int] = DEFAULT_SHIFTS,
    *,
    min_overlap: int = 24,
) -> dict[str, Any]:
    """一对序列的依赖度量。

    ``max_abs_rho`` 取各偏移下 |ρ| 的最大值，并记录取到最大值的偏移。
    这是对「相关性低」这一方向最不利的选择——如果连它都小，
    「同步性不强」这个说法就不可能是对齐口径造的假象。
    """
    ov = overlap(a, b)
    by_shift: dict[str, dict[str, Any]] = {}
    best_shift = None
    best_rho = float("nan")
    for s in shifts:
        xs, ys, months = align(a, b, s)
        rho = pearson(xs, ys) if len(months) >= min_overlap else float("nan")
        # 注意 pearson 在多取月份后 n 可能为 0，此时 nan
        by_shift[str(s)] = {
            "n": len(months),
            "rho": None if math.isnan(rho) else rho,
            "p": None if math.isnan(rho) else rho_p_value(rho, len(months)),
        }
        if not math.isnan(rho) and (
            math.isnan(best_rho) or abs(rho) > abs(best_rho)
        ):
            best_rho, best_shift = rho, s
    return {
        "a": a.key,
        "b": b.key,
        "n_overlap": ov["n_overlap"],
        "overlap_ratio": ov["ratio"],
        "shift0": by_shift.get("0"),
        "max_abs_rho": None if math.isnan(best_rho) else best_rho,
        "max_abs_rho_shift": best_shift,
        "by_shift": by_shift,
        "alignment_warning": a.derived_date != b.derived_date,
    }


# --------------------------------------------------------------------------
# 有效检验数（Li & Ji 2005）
# --------------------------------------------------------------------------


def li_ji_m_eff(corr: Sequence[Sequence[float]]) -> dict[str, Any]:
    """有效检验数：m_eff = 1 + (m-1)·(1 - Var(λ)/m)。

    λ 为相关矩阵特征值，Var 用样本方差（分母 m-1）。
    两个端点必须成立，单测里钉住：
    - 单位矩阵（完全独立）→ m_eff = m；
    - 全 1 矩阵（完全相依）→ m_eff = 1。

    ⚠️ 这只是「平均相关强度」的换算，不是 FDR 控制成立的依据。
    """
    import numpy as np

    arr = np.asarray(corr, dtype=float)
    if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
        raise DependenceError("相关矩阵必须是方阵")
    m = arr.shape[0]
    if m == 0:
        raise DependenceError("相关矩阵为空")
    if m == 1:
        return {"m": 1, "m_eff": 1.0, "var_lambda": 0.0, "mean_offdiag": float("nan")}
    lam = np.linalg.eigvalsh(arr)
    var = float(lam.var(ddof=1))
    m_eff = 1.0 + (m - 1) * (1.0 - var / m)
    off = arr[~np.eye(m, dtype=bool)]
    return {
        "m": m,
        "m_eff": m_eff,
        "var_lambda": var,
        "lambda_min": float(lam.min()),
        "lambda_max": float(lam.max()),
        "mean_offdiag": float(off.mean()),
        "mean_abs_offdiag": float(np.abs(off).mean()),
    }


def complete_submatrix(
    series: Mapping[str, MonthSeries],
    *,
    min_n: int = 200,
    min_overlap: int = 150,
    shifts: Iterable[int] = (0,),
) -> tuple[list[str], list[list[float]]]:
    """挑出「两两都够长、够重叠」的最大子集并算相关矩阵。

    贪心的做法会随选取顺序变，因此这里按 ``n`` 从大到小逐个尝试加入：
    只有与当前集合里**每一条**都满足门槛才纳入。结果可复现，且不依赖字典顺序。
    """
    order = sorted(series, key=lambda k: (-series[k].n, k))
    chosen: list[str] = []
    mat: list[list[float]] = []
    for key in order:
        cand = series[key]
        if cand.n < min_n:
            continue
        ok = True
        row: list[float] = []
        for other in chosen:
            xs, ys, months = align(cand, series[other], 0)
            if len(months) < min_overlap:
                ok = False
                break
            rho = pearson(xs, ys)
            if math.isnan(rho):
                ok = False
                break
            row.append(rho)
        if not ok:
            continue
        chosen.append(key)
        mat.append([*row, 1.0])
        for i, prev in enumerate(mat[:-1]):
            prev.append(row[i])
    return chosen, mat


# --------------------------------------------------------------------------
# 按 L3u 预登记的序列声明重建（保留月份索引）
# --------------------------------------------------------------------------


#: 判定序列的三种带索引形态。``json_list`` 没有月份索引，不进入依赖诊断。
INDEXED_KINDS = ("column", "terms", "pivot")


def _pick_date_column(columns: Sequence[str]) -> str | None:
    for candidate in DATE_COLUMNS:
        if candidate in columns:
            return candidate
    return None


def load_entry(
    root: Any,
    entry: Mapping[str, Any],
    *,
    date_priority: Sequence[str] = DATE_COLUMNS,
) -> MonthSeries:
    """按 L3u 预登记的 ``series`` 声明重建，并**保留月份索引**。

    为什么不直接用 ``citation.rebuild_series``：它返回的是丢索引的
    ``tuple[float, ...]``（判定时只需要数值），而依赖诊断必须要月份对其。

    为什么不用 ``l3t_registry`` 的规格：两者的口径并不相同。例如 ``phasec_d``
    在 L3t 里是「cell=A 的 d_raw」，在 L3u 里是 ``2×(d[B] − d[A])``——
    后者才是它判定时用的量。用错一份就是拿两个不同的东西去算相关。
    单测里逐条比对 ``load_entry`` 与 ``rebuild_series`` 的数值完全一致。
    """
    import math as _math
    from pathlib import Path

    import pandas as pd

    spec = entry["series"]
    key = entry["key"]
    kind = spec["kind"]
    if kind not in INDEXED_KINDS:
        raise DependenceError(
            f"[{key}] 序列形态 {kind} 没有月份索引，不能进入依赖诊断"
        )
    path = Path(root) / spec["path"]
    if not path.exists():
        raise SeriesFileMissing(f"[{key}] 文件不存在：{spec['path']}")

    frame = pd.read_csv(path)
    for column, value in (spec.get("filter") or {}).items():
        if column not in frame.columns:
            raise SeriesColumnMissing(f"[{key}] 缺少过滤列 {column}")
        frame = frame[frame[column] == value]
    if frame.empty:
        raise SeriesFilterEmpty(f"[{key}] 过滤后为空（filter={spec.get('filter')}）")

    multiplier = float(spec.get("multiplier", 1.0))
    scale = float(spec.get("scale", 1.0))

    if kind == "pivot":
        index = spec["index"]
        wide = frame.pivot_table(
            index=index, columns=spec["pivot_column"], values=spec["value_column"], aggfunc="first"
        )
        total = None
        for term in spec["terms"]:
            if term["key"] not in wide.columns:
                raise SeriesColumnMissing(
                    f"[{key}] 透视后缺少单元格 {term['key']}（有 {list(wide.columns)}）"
                )
            part = pd.to_numeric(wide[term["key"]], errors="coerce") * float(term["weight"])
            total = part if total is None else total + part
        series = total * multiplier * scale
        date_values = list(wide.index)
    else:
        date_column = _pick_date_column(list(frame.columns))
        if date_column is None:
            raise SeriesColumnMissing(
                f"[{key}] {spec['path']} 没有任何日期列（候选 {DATE_COLUMNS}）"
            )
        date_values = list(frame[date_column])
        if kind == "column":
            series = pd.to_numeric(frame[spec["column"]], errors="coerce")
        else:
            total = None
            for term in spec["terms"]:
                if term["column"] not in frame.columns:
                    raise SeriesColumnMissing(f"[{key}] 缺少列 {term['column']}")
                part = pd.to_numeric(frame[term["column"]], errors="coerce") * float(
                    term["weight"]
                )
                total = part if total is None else total + part
            series = total
        series = series * multiplier * scale

    values: dict[str, float] = {}
    for raw_date, raw_value in zip(date_values, series.tolist(), strict=False):
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not _math.isfinite(value):
            continue
        mk = month_key(raw_date)
        if mk in values:
            raise SeriesMonthNotUnique(
                f"[{key}] {spec['path']} 月份 {mk} 出现多行；"
                "若是有意分组，必须在 series.filter 里说明"
            )
        values[mk] = value
    if not values:
        raise SeriesFilterEmpty(f"[{key}] 重建后没有有效月份")

    date_column = spec["index"] if kind == "pivot" else _pick_date_column(list(frame.columns))
    return MonthSeries(
        key=key,
        path=spec["path"],
        column=spec.get("column") or "+".join(
            t.get("column") or t.get("key") for t in spec.get("terms", [])
        ),
        date_column=date_column or "unknown",
        values=values,
        filters=dict(spec.get("filter") or {}),
    )

