"""数据集发现与安全载入（API 层）。

安全边界（本模块是数据路径的唯一入口）：
- 所有文件访问都限制在配置的数据根目录（data_root）之内；
- 数据集只能通过 symbol 标识符引用，symbol 受白名单字符约束
  （字母开头，仅含字母/数字/``.``/``_``/``-``），天然排除路径分隔符、盘符与 ``..``；
- 元数据中的文件名字段必须是纯文件名（不含任何路径分隔符）；
- 拼接后的路径经 ``resolve()`` 解析，并验证仍位于 data_root 之内
  （可拦截符号链接逃逸、绝对路径注入等）；
- 只枚举 ``data_root/metadata/`` 下的 ``*.meta.json``，不遍历其它目录；
- 损坏/不完整/不一致的元数据不会伪装成正常数据集：发现接口将其列入
  ``invalid_datasets``，按 symbol 定位时视为不可用。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from deepquant.api.errors import (
    DataRootUnavailableError,
    DatasetNotFoundError,
    DatasetUnavailableError,
)

# 数据集标识符白名单：字母或数字开头，仅含字母/数字/点/下划线/连字符
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_METADATA_SUFFIX = ".meta.json"
_REQUIRED_META_KEYS = ("symbol", "files", "rows", "date_range")


@dataclass(frozen=True)
class DatasetInfo:
    """一个可用数据集的对外摘要（字段均来自已落盘的元数据，不含本机路径）。"""

    symbol: str
    rows: int
    start_date: str
    end_date: str
    source_name: str | None
    source_sha256: str | None
    ingested_at: str | None


@dataclass(frozen=True)
class InvalidDataset:
    """一个无法通过校验的元数据条目（只暴露文件名与通用原因，不暴露路径）。"""

    metadata_file: str
    symbol: str | None
    reason: str


def is_valid_symbol(symbol: object) -> bool:
    return isinstance(symbol, str) and SYMBOL_PATTERN.match(symbol) is not None


def ensure_data_root(data_root: Path) -> None:
    """数据根目录必须存在且为目录，否则抛出 DataRootUnavailableError（503）。"""
    if not data_root.is_dir():
        raise DataRootUnavailableError("数据根目录不存在或不可访问，请配置 DEEPQUANT_DATA_ROOT")


def _resolve_within(root: Path, candidate: Path) -> Path | None:
    """解析候选路径；解析结果不在 root 之内时返回 None。

    root 必须已是 resolve 后的路径。resolve 会跟随符号链接，
    因此指向 root 之外的符号链接会在前缀校验处被拦截。
    """
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_relative_to(root) else None


def _is_plain_filename(name: object) -> bool:
    """元数据中的文件名字段必须是不含分隔符的纯文件名。"""
    if not isinstance(name, str) or not name:
        return False
    if "/" in name or "\\" in name or name in (".", ".."):
        return False
    return Path(name).name == name


def _parse_metadata(data: object, *, expected_symbol: str) -> tuple[DatasetInfo, str] | str:
    """校验元数据内容并生成 DatasetInfo 与 processed 文件名；失败时返回原因字符串。"""
    if not isinstance(data, dict):
        return "元数据不是 JSON 对象"
    missing = [k for k in _REQUIRED_META_KEYS if k not in data]
    if missing:
        return f"元数据缺少必需字段 {missing}"

    symbol = data["symbol"]
    if symbol != expected_symbol:
        return "元数据 symbol 与文件名不一致"
    if not is_valid_symbol(symbol):
        return "元数据 symbol 不符合标识符规范"

    files = data["files"]
    if not isinstance(files, dict) or not _is_plain_filename(files.get("processed")):
        return "元数据缺少合法的 processed 文件名"
    processed_name = files["processed"]

    rows = data["rows"]
    n_rows = rows.get("processed") if isinstance(rows, dict) else None
    if not isinstance(n_rows, int) or isinstance(n_rows, bool) or n_rows < 0:
        return "元数据行数（rows.processed）无效"

    date_range = data["date_range"]
    if not isinstance(date_range, dict):
        return "元数据日期范围无效"
    start, end = date_range.get("start"), date_range.get("end")
    if not (isinstance(start, str) and isinstance(end, str)):
        return "元数据日期范围无效"

    source = data.get("source")
    source = source if isinstance(source, dict) else {}
    source_name = source.get("name")
    source_sha256 = source.get("sha256")
    ingested_at = source.get("ingested_at")

    info = DatasetInfo(
        symbol=symbol,
        rows=n_rows,
        start_date=start,
        end_date=end,
        source_name=source_name if isinstance(source_name, str) else None,
        source_sha256=source_sha256 if isinstance(source_sha256, str) else None,
        ingested_at=ingested_at if isinstance(ingested_at, str) else None,
    )
    return info, processed_name


def _check_entry(
    data_root: Path, meta_path: Path
) -> tuple[DatasetInfo, Path] | InvalidDataset:
    """校验单个元数据文件及其对应数据文件。

    成功时返回 ``(DatasetInfo, resolve 后的数据文件路径)``，失败时返回无效原因。
    """
    meta_file = meta_path.name
    symbol = meta_file[: -len(_METADATA_SUFFIX)]

    if not is_valid_symbol(symbol):
        return InvalidDataset(meta_file, None, "文件名不是合法的数据集标识符")

    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return InvalidDataset(meta_file, symbol, "元数据无法解析")

    parsed = _parse_metadata(data, expected_symbol=symbol)
    if isinstance(parsed, str):
        return InvalidDataset(meta_file, symbol, parsed)
    info, processed_name = parsed

    processed_path = _resolve_within(data_root, data_root / "processed" / processed_name)
    if processed_path is None or not processed_path.is_file():
        return InvalidDataset(meta_file, symbol, "数据文件缺失或越出数据根目录")

    return info, processed_path


def discover_datasets(data_root: Path) -> tuple[list[DatasetInfo], list[InvalidDataset]]:
    """枚举数据根目录内的数据集（调用方需保证 data_root 存在且已 resolve）。

    只读取 ``metadata/`` 下的 ``*.meta.json``；子目录不存在时视为空数据集列表。
    结果按 symbol / 文件名升序排列，排序确定且可复现。
    """
    metadata_dir = data_root / "metadata"
    valid: list[DatasetInfo] = []
    invalid: list[InvalidDataset] = []
    if metadata_dir.is_dir():
        for meta_path in sorted(metadata_dir.glob(f"*{_METADATA_SUFFIX}"), key=lambda p: p.name):
            entry = _check_entry(data_root, meta_path)
            if isinstance(entry, InvalidDataset):
                invalid.append(entry)
            else:
                valid.append(entry[0])
    valid.sort(key=lambda d: d.symbol)
    invalid.sort(key=lambda d: d.metadata_file)
    return valid, invalid


def find_dataset(data_root: Path, symbol: str) -> tuple[DatasetInfo, Path]:
    """按 symbol 定位数据集，返回摘要与 resolve 后的数据文件路径。

    未登记抛出 DatasetNotFoundError；已登记但不可用抛出 DatasetUnavailableError。
    """
    if not is_valid_symbol(symbol):
        raise DatasetNotFoundError("数据集不存在")

    meta_path = _resolve_within(data_root, data_root / "metadata" / f"{symbol}{_METADATA_SUFFIX}")
    if meta_path is None or not meta_path.is_file():
        raise DatasetNotFoundError(f"数据集 {symbol!r} 未登记")

    entry = _check_entry(data_root, meta_path)
    if isinstance(entry, InvalidDataset):
        raise DatasetUnavailableError(
            f"数据集 {symbol!r} 已登记但不可用：{entry.reason}"
        )
    return entry
