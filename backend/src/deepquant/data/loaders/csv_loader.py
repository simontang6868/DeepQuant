"""历史行情 CSV 导入。

约定：单个 CSV 文件对应单个标的的日线行情。
必需字段：date, open, high, low, close, volume。

处理原则：
- 列名统一为小写并去除首尾空格；
- date 解析失败置为 NaT，价格/成交量解析失败置为 NaN；
- 不在此处丢弃或填充任何数据，问题交给 validation 层报告。

编码策略（修复 Windows 中文乱码风险）：
- 读取：默认按 ``CANDIDATE_ENCODINGS`` 顺序尝试（``utf-8-sig`` → ``gb18030``），
  也可显式传入 ``encoding`` 指定单一编码；均失败时抛出 ``DataLoadError``，
  **绝不静默返回空数据**；
- 写出：统一使用 ``DEFAULT_WRITE_ENCODING``（UTF-8），不再依赖操作系统默认编码；
- ``utf-8-sig`` 兼容带/不带 BOM 的 UTF-8；``gb18030`` 是 Windows 中文常见
  GBK/GB2312 的超集。两者均以「整文件可成功解码」为约束，避免把错误解码的数据
  当成有效行情。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS: tuple[str, ...] = ("date", "open", "high", "low", "close", "volume")
PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")
NUMERIC_COLUMNS: tuple[str, ...] = PRICE_COLUMNS + ("volume",)

# 读取编码的受支持范围与尝试顺序（限定范围，避免把错误解码当成有效行情）
CANDIDATE_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "gb18030")
# 写出编码：明确使用 UTF-8，避免依赖 Windows 本地默认编码（如 GBK）
DEFAULT_WRITE_ENCODING = "utf-8"


class DataLoadError(Exception):
    """CSV 无法按约定载入时抛出。"""


def _read_csv(path: Path, encoding: str | None) -> pd.DataFrame:
    """按既定编码策略读取 CSV；失败时抛出可理解的 DataLoadError。"""
    def _attempt(enc: str) -> pd.DataFrame:
        try:
            return pd.read_csv(path, encoding=enc)
        except LookupError as exc:  # 未知编码名
            raise DataLoadError(f"未知编码 {enc!r}：{exc}") from exc
        except UnicodeDecodeError as exc:
            raise DataLoadError(f"以编码 {enc!r} 无法解码文件 {path}：{exc}") from exc
        except pd.errors.EmptyDataError as exc:
            raise DataLoadError(f"文件为空，无法解析行情数据: {path}") from exc

    if encoding is not None:
        return _attempt(encoding)

    last_error: UnicodeDecodeError | None = None
    for candidate in CANDIDATE_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=candidate)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        except pd.errors.EmptyDataError as exc:
            raise DataLoadError(f"文件为空，无法解析行情数据: {path}") from exc
    raise DataLoadError(
        f"无法用受支持的编码 {list(CANDIDATE_ENCODINGS)} 解码文件 {path}"
        "（Windows 中文 CSV 常见 GBK/GB18030）。请在确认文件编码后显式传入 encoding 参数。"
    ) from last_error


def load_daily_csv(path: str | Path, *, encoding: str | None = None) -> pd.DataFrame:
    """读取日线行情 CSV 并返回规范化后的 DataFrame。

    参数：
        path: CSV 文件路径（str 或 pathlib.Path 均可，跨平台）。
        encoding: 可选；显式指定单一编码。为 None 时按 CANDIDATE_ENCODINGS 顺序尝试。

    返回的列顺序为：date, open, high, low, close, volume（及其余原始列），
    并按 date 升序排列。
    """
    path = Path(path)
    if not path.exists():
        raise DataLoadError(f"文件不存在: {path}")

    df = _read_csv(path, encoding)
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
