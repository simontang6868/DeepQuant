"""历史行情 CSV 导入。

约定：单个 CSV 文件对应单个标的的日线行情。
必需字段：date, open, high, low, close, volume。

处理原则：
- 列名统一为小写并去除首尾空格；
- date 解析失败置为 NaT，价格/成交量解析失败置为 NaN；
- 不在此处丢弃或填充任何数据，问题交给 validation 层报告。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS: tuple[str, ...] = ("date", "open", "high", "low", "close", "volume")
PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")
NUMERIC_COLUMNS: tuple[str, ...] = PRICE_COLUMNS + ("volume",)


class DataLoadError(Exception):
    """CSV 无法按约定载入时抛出。"""


def load_daily_csv(path: str | Path) -> pd.DataFrame:
    """读取日线行情 CSV 并返回规范化后的 DataFrame。

    返回的列顺序为：date, open, high, low, close, volume（及其余原始列），
    并按 date 升序排列。
    """
    path = Path(path)
    if not path.exists():
        raise DataLoadError(f"文件不存在: {path}")

    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataLoadError(f"缺少必需字段 {missing}；实际列: {list(df.columns)}")

    # 明确使用逐元素解析（format="mixed"），避免 pandas 因格式不一致而告警
    df["date"] = pd.to_datetime(df["date"], errors="coerce", format="mixed")
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 固定列顺序：必需列在前，其余保留在后
    ordered = list(REQUIRED_COLUMNS) + [c for c in df.columns if c not in REQUIRED_COLUMNS]
    df = df[ordered]

    return df.sort_values("date").reset_index(drop=True)
