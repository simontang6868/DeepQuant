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
from deepquant.data.instrument import normalize_display_name

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
    #: 标的显示名称（如「平安银行」）；缺失时为 None，界面回落到只显示 symbol。
    display_name: str | None = None


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

    # 名称是可选字段：缺失时数据集照常可用（历史元数据都没有这个键）。
    # 但**存在而非法**必须报错——那种情况下界面会显示一串乱码或把控制字符打进 DOM，
    # 与其展示可疑内容，不如明确列为不可用，让人去修元数据。
    instrument = data.get("instrument")
    display_name: str | None = None
    if instrument is not None:
        if not isinstance(instrument, dict):
            return "元数据 instrument 字段不是对象"
        try:
            display_name = normalize_display_name(instrument.get("display_name"))
        except ValueError as exc:
            return f"元数据标的名称不可用：{exc}"

    info = DatasetInfo(
        symbol=symbol,
        rows=n_rows,
        start_date=start,
        end_date=end,
        source_name=source_name if isinstance(source_name, str) else None,
        source_sha256=source_sha256 if isinstance(source_sha256, str) else None,
        ingested_at=ingested_at if isinstance(ingested_at, str) else None,
        display_name=display_name,
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


# --------------------------------------------------------------------------- #
# 不可用标的：解释「为什么列表里没有它」
# --------------------------------------------------------------------------- #
#
# 背景：导入脚本会**拒绝**数据不干净的标的（例如前复权减法实现造成的负价），
# 被拒的标的一次都不会进 metadata/，因此它在 /datasets 的 datasets 里没有、
# 在 invalid_datasets 里也没有 —— 界面上就是「凭空少了一只」。
#
# 这不是 bug（拒绝是对的：修一修会得到一份与文档不对应的行情），但「悄悄少一只」
# 会让人以为软件漏导入。所以这里把导入时留下的清单读出来，作为**解释性**信息展示。
#
# 三条边界，缺一条就会变成骗人：
# 1. 只读 data_root 内的 pool_manifest.json（不放外部路径，也不联网）；
# 2. 只取 status == "rejected" 的条目，且**当前仓库里确实不存在**该 symbol
#    （若已存在，说明数据后来被补上了，此时以仓库为准，不再说它不可用）；
# 3. reason 是**导入时**的判定结果，不是实时校验结论——字段名与文案都按此表述。

#: 导入清单文件名（由 scripts/import_pool.py 写出）
POOL_MANIFEST_NAME = "pool_manifest.json"

#: 只有这个状态的条目才解释「为何缺失」
_REJECTED_STATUS = "rejected"


@dataclass(frozen=True)
class UnavailableDataset:
    """一只**导入时被拒绝**的标的（仅用于解释缺失原因，不参与任何计算）。"""

    symbol: str
    display_name: str | None
    reason: str
    checked_at: str | None


def read_pool_manifest(data_root: Path) -> dict | None:
    """读取导入清单；不存在或不可解析时返回 None（这不是错误，只是没有清单）。"""
    path = _resolve_within(data_root, data_root / POOL_MANIFEST_NAME)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_unavailable(data_root: Path, available: set[str]) -> list[UnavailableDataset]:
    """列出被导入闸门拒绝、且当前仓库里确实没有的标的（按代码升序）。"""
    manifest = read_pool_manifest(data_root)
    if manifest is None:
        return []

    entries = manifest.get("symbols")
    if not isinstance(entries, list):
        return []

    checked_at = manifest.get("generated_at")
    checked_at = checked_at if isinstance(checked_at, str) else None

    out: list[UnavailableDataset] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        if item.get("status") != _REJECTED_STATUS:
            continue
        symbol = item.get("code")
        if not is_valid_symbol(symbol) or symbol in available:
            continue
        reason = item.get("reason")
        reason = reason if isinstance(reason, str) and reason.strip() else "导入时被拒绝"
        name: str | None = None
        try:
            name = normalize_display_name(item.get("name"))
        except ValueError:
            # 清单里的名称非法不影响「它为什么不可用」这个事实，宁可不显示名称
            name = None
        out.append(UnavailableDataset(symbol, name, reason, checked_at))

    out.sort(key=lambda d: d.symbol)
    return out
