"""行情导入流水线：载入 → 校验 → 清洗 → 落盘。

落盘约定（原始与处理后分离）：
- raw/      原始 CSV 原样保存，只读、不修改；
- processed/ 清洗后的日线数据（Parquet，保留列类型）；
- metadata/  数据来源、时间范围、处理方式与校验结果（可用于复现）。

清洗规则（确定性，绝不伪造数据）：
1. 丢弃重复日期行（保留首次出现）；
2. 丢弃 open/high/low/close/volume 存在缺失或无法解析的行（不做任何插值/填充）；
3. 按日期升序排列。

硬错误（缺少字段、空表、日期无法解析、OHLC 矛盾、价格非正、负成交量）
不自动修复，直接以 DataValidationError 中止。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from deepquant.data.instrument import normalize_display_name
from deepquant.data.loaders.csv_loader import NUMERIC_COLUMNS, load_daily_csv
from deepquant.data.storage import io as storage_io
from deepquant.data.validation.checks import ERROR, ValidationReport, validate_daily


class DataValidationError(Exception):
    """原始数据存在无法自动修复的问题时抛出。"""


@dataclass
class IngestResult:
    """一次导入的结果汇总。"""

    symbol: str
    raw_path: Path
    processed_path: Path
    metadata_path: Path
    metadata: dict
    data: pd.DataFrame
    report_before: ValidationReport
    report_after: ValidationReport


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _report_to_dict(report: ValidationReport) -> dict:
    return {
        "valid": report.is_valid,
        "n_rows": report.n_rows,
        "issues": [
            {"level": i.level, "code": i.code, "message": i.message, "count": i.count}
            for i in report.issues
        ],
    }


def _clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """执行文档化的清洗规则，返回清洗后的数据与清洗统计。"""
    before = len(df)

    # 1) 去重：重复日期保留首次出现
    out = df.drop_duplicates(subset="date", keep="first").copy()
    duplicate_dropped = before - len(out)

    # 2) 丢弃行情数值缺失的行（不填充）
    complete = out[list(NUMERIC_COLUMNS)].notna().all(axis=1)
    missing_dropped = int((~complete).sum())
    out = out[complete].copy()

    # 3) 按日期排序
    out = out.sort_values("date").reset_index(drop=True)

    stats = {
        "duplicate_dates_dropped": int(duplicate_dropped),
        "missing_value_rows_dropped": missing_dropped,
        "sorted_by_date": True,
    }
    return out, stats


def ingest_daily_csv(
    source_path: str | Path,
    *,
    symbol: str,
    data_root: str | Path,
    source_name: str | None = None,
    instrument_name: str | None = None,
) -> IngestResult:
    """导入单个标的的日线 CSV。

    参数：
        source_path: 待导入的 CSV 路径。
        symbol: 标的代码（用于文件命名与元数据）。
        data_root: 数据根目录，其下自动使用 raw/ processed/ metadata/。
        source_name: 数据来源标识（供应商/渠道），默认取源文件名，用于记录数据许可与出处。
        instrument_name: 标的显示名称（如「平安银行」），可选。只影响元数据与界面展示，
            **不参与任何计算**；未提供时元数据里不出现 ``instrument`` 键，
            与历史行为逐字节一致。
    """
    # 名称校验放在最前面：名字不合法必须在写任何文件之前失败，
    # 否则会留下「raw/processed 落盘了、metadata 没写」的半成品目录。
    display_name = normalize_display_name(instrument_name)

    source_path = Path(source_path)
    data_root = Path(data_root)

    raw_dir = data_root / "raw"
    processed_dir = data_root / "processed"
    metadata_dir = data_root / "metadata"
    for directory in (raw_dir, processed_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=True)

    raw_bytes = source_path.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # --- 载入与校验 ---
    df = load_daily_csv(source_path)
    report_before = validate_daily(df)
    if not report_before.is_valid:
        errors = [i for i in report_before.issues if i.level == ERROR]
        detail = "; ".join(f"{i.code}({i.count})" for i in errors)
        raise DataValidationError(f"原始数据存在严重问题，已中止导入：{detail}")

    rows_raw = int(len(df))

    # --- 清洗 ---
    cleaned, cleaning_stats = _clean(df)
    report_after = validate_daily(cleaned)
    if not report_after.is_valid:
        detail = "; ".join(f"{i.code}({i.count})" for i in report_after.issues if i.level == ERROR)
        raise DataValidationError(f"清洗后数据仍存在严重问题：{detail}")

    # --- 落盘 ---
    raw_path = raw_dir / f"{symbol}.raw.csv"
    raw_path.write_bytes(raw_bytes)  # 原样保存，不做任何改写

    processed_path = storage_io.write_daily_parquet(
        cleaned, processed_dir / f"{symbol}.daily.parquet"
    )

    dates = cleaned["date"]
    metadata = {
        "symbol": symbol,
        "files": {
            "raw": raw_path.name,
            "processed": processed_path.name,
            "metadata": f"{symbol}.meta.json",
        },
        "storage": {"raw_format": "csv", "processed_format": "parquet"},
        "source": {
            "name": source_name or source_path.name,
            "file": str(source_name or source_path.name),
            "sha256": sha256,
            "ingested_at": _now_iso(),
        },
        "rows": {"raw": rows_raw, "processed": int(len(cleaned))},
        "date_range": {
            "start": dates.min().strftime("%Y-%m-%d"),
            "end": dates.max().strftime("%Y-%m-%d"),
        },
        "cleaning": cleaning_stats,
        "validation": {
            "raw": _report_to_dict(report_before),
            "processed": _report_to_dict(report_after),
        },
        "schema": {"required_columns": list(NUMERIC_COLUMNS)},
        "notes": "原始数据原样保存在 raw/，处理后数据保存在 processed/；缺失值未做任何填充。",
    }
    if display_name is not None:
        # 条件插入而非固定写 None：历史数据集元数据里没有这个键，
        # 保持「没传名称 = 元数据一字不变」这一可验证的性质。
        metadata["instrument"] = {"display_name": display_name}

    metadata_path = storage_io.write_json(metadata, metadata_dir / f"{symbol}.meta.json")

    return IngestResult(
        symbol=symbol,
        raw_path=raw_path,
        processed_path=processed_path,
        metadata_path=metadata_path,
        metadata=metadata,
        data=cleaned,
        report_before=report_before,
        report_after=report_after,
    )
