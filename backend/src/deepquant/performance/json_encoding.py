"""通用 JSON 安全编码 / 解码（不依赖回测模型）。

目标：把回测结果及其辅助结构中的常见 Python / NumPy / pandas 类型，
转换为「标准 JSON 解析器可读取」的结构，**绝不输出 NaN / Infinity / -Infinity**。

本项目约定（供各模块统一遵循）：
- 日期时间：无时区 ISO-8601 字符串（``pd.Timestamp.isoformat()``），保留无时区约定；
- 枚举（Enum）：使用其 ``.value``（稳定的字符串值），不写 Python 类名；
- Decimal：编码为 ``{"__type__": "decimal", "value": "<字符串>"}``，避免浮点精度丢失；
- None：保持为 JSON ``null``（缺失值使用稳定且明确的 ``null`` 表示）；
- 数值：``int`` / ``float``；浮点必须有限，否则抛出 :class:`SerializationError`；
- 字典键：必须是字符串（否则抛出 :class:`SerializationError`），以保证 JSON 稳定；
- 数据类（dataclass）：按键声明顺序展开为字典；
- 集合 / ndarray：转换为列表（集合按 ``repr`` 排序保证确定性）。

本模块只做「类型 → JSON 安全值」的机械转换，不含任何回测语义。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import math
from decimal import Decimal
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd


class SerializationError(ValueError):
    """序列化 / 反序列化失败（不支持的字段类型或非标准 JSON 数值）。"""


# Decimal 的标签化表示（避免二进制浮点精度丢失，且可无损还原类型）
_DECIMAL_TYPE_KEY = "__type__"
_DECIMAL_TYPE_NAME = "decimal"
_DECIMAL_VALUE_KEY = "value"


def to_jsonable(value: Any, *, _path: str = "$") -> Any:
    """把任意受支持的值转换为 JSON 安全结构。

    不支持的输入或非标准数值（NaN / Inf）会抛出 :class:`SerializationError`，
    并在消息中给出具体字段路径。``None`` 映射为 ``null``。
    """
    if value is None:
        return None
    # bool 必须在 int 之前判断（bool 是 int 的子类）
    if isinstance(value, bool):
        return value
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, Enum):
        return to_jsonable(value.value, _path=_path)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not math.isfinite(number):
            raise SerializationError(
                f"{_path}: 非标准 JSON 数值 {number!r}（NaN/Infinity 不允许）"
            )
        return number
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise SerializationError(f"{_path}: Decimal 必须有限，收到 {value!r}")
        return {_DECIMAL_TYPE_KEY: _DECIMAL_TYPE_NAME, _DECIMAL_VALUE_KEY: str(value)}
    if isinstance(value, str):
        return value
    if isinstance(value, (pd.Timestamp, _dt.datetime, _dt.date)):
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            raise SerializationError(f"{_path}: 日期不能为 NaT/缺失")
        if ts.tzinfo is not None:
            raise SerializationError(f"{_path}: 必须是无时区（tz-naive）时间戳，收到 {ts}")
        return ts.isoformat()
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v, _path=f"{_path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        return [
            to_jsonable(v, _path=f"{_path}[{i}]")
            for i, v in enumerate(sorted(value, key=repr))
        ]
    if isinstance(value, np.ndarray):
        return [to_jsonable(v, _path=f"{_path}[{i}]") for i, v in enumerate(value.tolist())]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_jsonable(getattr(value, f.name), _path=f"{_path}.{f.name}")
            for f in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            if not isinstance(key, str):
                raise SerializationError(f"{_path}: 字典键必须是字符串，收到 {key!r}")
            out[key] = to_jsonable(val, _path=f"{_path}.{key}")
        return out
    raise SerializationError(f"{_path}: 不支持的类型 {type(value)!r}")


def from_jsonable(value: Any) -> Any:
    """``to_jsonable`` 的逆操作：还原标签化的 Decimal。

    普通标量（字符串日期 / 数字 / null）保持原样；日期字符串由各类型化反序列化器
    按本项目的无时区约定还原为 ``pd.Timestamp``。
    """
    if isinstance(value, dict):
        if (
            value.get(_DECIMAL_TYPE_KEY) == _DECIMAL_TYPE_NAME
            and set(value.keys()) <= {_DECIMAL_TYPE_KEY, _DECIMAL_VALUE_KEY}
        ):
            return Decimal(value[_DECIMAL_VALUE_KEY])
        return {k: from_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_jsonable(v) for v in value]
    return value
