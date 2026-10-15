"""GET /datasets：列出当前可用的数据集。

只读操作：枚举数据根目录内的元数据，不修改任何文件。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from deepquant.api.config import get_data_root
from deepquant.api.datasets import discover_datasets, ensure_data_root
from deepquant.api.schemas import (
    DatasetEntry,
    DatasetListResponse,
    InvalidDatasetEntry,
)

router = APIRouter()


@router.get("/datasets")
def list_datasets(data_root: Path = Depends(get_data_root)) -> DatasetListResponse:
    """返回已登记且可用的数据集列表；损坏/不完整的元数据单独列出。"""
    ensure_data_root(data_root)
    valid, invalid = discover_datasets(data_root)
    return DatasetListResponse(
        datasets=[
            DatasetEntry(
                symbol=d.symbol,
                rows=d.rows,
                start_date=d.start_date,
                end_date=d.end_date,
                source_name=d.source_name,
                source_sha256=d.source_sha256,
                ingested_at=d.ingested_at,
            )
            for d in valid
        ],
        invalid_datasets=[
            InvalidDatasetEntry(
                metadata_file=d.metadata_file, symbol=d.symbol, reason=d.reason
            )
            for d in invalid
        ],
    )
