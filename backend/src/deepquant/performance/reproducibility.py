"""可复现性：数据集摘要与策略元数据。

三个概念必须区分（不得混为一谈）：
1. 结果可序列化（result_serializable）—— 结果能被稳定 JSON 读写；
2. 输入可识别（inputs_identifiable）—— 能稳定标识数据集与策略；
3. 过程可复现（process_reproducible）—— 数据、策略参数、配置齐备，可据此重跑。

数据摘要的**确定性标准化**（相同逻辑数据 ⇒ 相同摘要）：
- 列顺序：``date`` 在前；行情列按 ``open, high, low, close, volume`` 固定顺序，
  其余列按字母序追加；
- 日期格式：``YYYY-MM-DD``；
- 数值格式：``float`` 的最短可往返十进制表示（``repr(float(v))``）；
- 缺失值表示：``NaN``；
- 排序：按日期升序（稳定排序，仅用于摘要，**不修改输入数据**）；
- 摘要范围：全部行 × 全部标准化列（含列名表头）。

摘要基于**规范化后的完整输入数据**计算，不随回测日期区间裁剪而变化
（日期区间由回测配置单独记录）。仅使用文件名或行数不足以作为内容标识。
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd

from deepquant.performance.json_encoding import SerializationError, to_jsonable

# 摘要使用固定列顺序中的行情列（其余列按字母序追加）
CANONICAL_PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

_MISSING_TOKEN = "NaN"
_DATE_FORMAT = "%Y-%m-%d"

_CANONICALIZATION_DOC: dict[str, str] = {
    "column_order": "date 在前；行情列按 open,high,low,close,volume 固定顺序，其余列按字母序追加",
    "date_format": "YYYY-MM-DD",
    "numeric_format": "float 的最短可往返十进制表示（repr(float(v))）",
    "missing_value": "NaN",
    "sort": "按日期升序（稳定排序，不修改输入数据）",
    "scope": "全部行 × 全部标准化列（含列名表头），基于规范化后的完整输入数据",
}


def _resolve_dates_columns(df: pd.DataFrame) -> tuple[pd.DatetimeIndex, list[str]]:
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df 必须是 pandas.DataFrame，收到 {type(df)!r}")

    if isinstance(df.index, pd.DatetimeIndex):
        dates = df.index
        return dates, list(df.columns)

    if "date" in df.columns:
        parsed = pd.to_datetime(df["date"], errors="coerce", format="mixed")
        dates = pd.DatetimeIndex(parsed)
        return dates, [c for c in df.columns if c != "date"]

    raise ValueError("数据集摘要需要 DatetimeIndex 或 date 列")


def _canonical_columns(columns: list[str]) -> list[str]:
    ordered = [c for c in CANONICAL_PRICE_COLUMNS if c in columns]
    ordered += sorted(c for c in columns if c not in CANONICAL_PRICE_COLUMNS)
    return ordered


def _number_token(value: Any) -> str:
    if value is None:
        return _MISSING_TOKEN
    try:
        if pd.isna(value):
            return _MISSING_TOKEN
    except (TypeError, ValueError):
        pass
    if isinstance(value, (bool, np.bool_)):
        return "True" if bool(value) else "False"
    try:
        return repr(float(value))
    except (TypeError, ValueError):
        return str(value)


def dataset_summary(df: pd.DataFrame, *, dataset_id: str | None = None) -> dict:
    """生成数据集的确定性摘要与描述。

    只读取输入，不修改 ``df``。相同逻辑数据（列顺序归一、日期升序、数值表示统一）
    得到相同 ``digest``；内容变化则 ``digest`` 变化。
    """
    dates, columns = _resolve_dates_columns(df)
    if dates.isna().any():
        raise ValueError("存在无法解析的日期，无法生成稳定摘要")
    if dates.has_duplicates:
        raise ValueError(
            "数据集存在重复日期，无法生成稳定的内容摘要（重复日期会使排序与内容标识产生歧义）"
        )

    ordered = _canonical_columns(columns)
    order_positions = sorted(range(len(dates)), key=lambda i: dates[i])

    header = "date|" + "|".join(ordered)
    lines = [header]
    column_values = {c: df[c] for c in ordered}
    for i in order_positions:
        row = [dates[i].strftime(_DATE_FORMAT)]
        for c in ordered:
            row.append(_number_token(column_values[c].iloc[i]))
        lines.append("|".join(row))

    payload = "\n".join(lines)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    date_range = None
    if len(dates) > 0:
        unique = dates
        date_range = {
            "start": min(unique).strftime(_DATE_FORMAT),
            "end": max(unique).strftime(_DATE_FORMAT),
        }

    return {
        "dataset_id": dataset_id,
        "n_rows": int(len(dates)),
        "columns": ordered,
        "date_range": date_range,
        "digest": f"sha256:{digest}",
        "digest_algorithm": "sha256",
        "canonicalization": dict(_CANONICALIZATION_DOC),
    }


def capture_strategy_metadata(strategy: Any) -> dict:
    """捕获策略的稳定标识与参数，缺失项明确标记。

    不强行序列化整个策略对象；参数不可序列化时记录 ``params=None`` 并说明原因。
    """
    name = getattr(strategy, "name", None)
    name = name if isinstance(name, str) and name.strip() else None
    version = getattr(strategy, "version", None)
    version = version if isinstance(version, str) and version.strip() else None

    notes: list[str] = []
    params: Any = None
    params_available = False

    describe = getattr(strategy, "describe", None)
    description: Any = None
    if callable(describe):
        try:
            description = describe()
        except Exception as exc:  # noqa: BLE001 - 需容纳任意策略的自定义实现
            notes.append(f"无法获取策略标识（describe 失败）：{type(exc).__name__}: {exc}")
    if isinstance(description, dict):
        if name is None and isinstance(description.get("name"), str):
            name = description["name"] or None
        if version is None and isinstance(description.get("version"), str):
            version = description["version"] or None
        params = description.get("params")

    if params is not None:
        try:
            to_jsonable(params)
            params_available = True
        except SerializationError as exc:
            notes.append(f"策略参数不可序列化：{exc}")
            params = None
    elif not notes:
        notes.append("策略未提供参数（params 为空或缺失）")

    identified = name is not None and version is not None
    if not identified:
        notes.append("缺少策略名称或版本，无法稳定标识策略")

    return {
        "name": name,
        "version": version,
        "params": params,
        "params_available": params_available,
        "identified": identified,
        "reproducible": identified and params_available,
        "notes": notes,
    }


def reproducibility_report(
    *,
    strategy_meta: dict | None,
    dataset_meta: dict | None,
    config_summary: dict | None,
) -> dict:
    """综合策略 / 数据集 / 配置，给出「输入可识别」与「过程可复现」判定。

    缺少任一关键项时**不得**宣称可复现，须在 ``missing`` 与 ``notes`` 中明确列出。
    """
    missing: list[str] = []
    notes: list[str] = []

    digest = (dataset_meta or {}).get("digest")
    if not dataset_meta:
        missing.append("数据集摘要")
    elif not digest:
        missing.append("数据集摘要 digest")

    if not strategy_meta:
        missing.append("策略元数据")
        strategy_identified = False
        strategy_params_available = False
    else:
        strategy_identified = bool(strategy_meta.get("identified"))
        strategy_params_available = bool(strategy_meta.get("params_available"))
        if not strategy_identified:
            missing.append("策略名称/版本")
        if not strategy_params_available:
            missing.append("策略参数")

    if not config_summary:
        missing.append("回测配置")

    inputs_identifiable = bool(digest) and strategy_identified
    process_reproducible = (
        inputs_identifiable and strategy_params_available and bool(config_summary)
    )

    if not process_reproducible:
        notes.append("缺少关键复现信息，结果仅可识别输入，不保证过程可复现")
    if dataset_meta is None or not digest:
        notes.append("未记录数据集内容摘要，无法识别输入数据是否变化")

    return {
        "inputs_identifiable": inputs_identifiable,
        "process_reproducible": process_reproducible,
        "missing": missing,
        "notes": notes,
    }
