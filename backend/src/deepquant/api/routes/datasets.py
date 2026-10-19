"""GET /datasets：列出当前可用的数据集。

只读操作：枚举数据根目录内的元数据，不修改任何文件。

除可用列表外，本接口还回答两个「界面看起来像 bug、其实是事实」的问题：

1. **为什么少了某只？** → ``unavailable_datasets``（导入时被闸门拒绝，附原因码）；
2. **区间为什么只到这里？** → ``coverage``（数据覆盖终点，不是接口限制）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from deepquant.api.config import get_data_root
from deepquant.api.datasets import (
    DatasetInfo,
    discover_datasets,
    ensure_data_root,
    list_unavailable,
    read_pool_manifest,
)
from deepquant.api.schemas import (
    DatasetCoverage,
    DatasetEntry,
    DatasetListResponse,
    InvalidDatasetEntry,
    UnavailableDatasetEntry,
)

router = APIRouter()


def _build_coverage(
    valid: list[DatasetInfo], n_unavailable: int, manifest: dict | None
) -> DatasetCoverage | None:
    """由可用数据集推导覆盖范围，并说明「为什么到这里为止」。

    没有可用数据集时返回 None（而不是编一个空区间）：空字符串日期在界面上
    会被格式化成「—」，看起来像「数据到某处但显示不出来」。
    """
    if not valid:
        return None

    starts = sorted(d.start_date for d in valid)
    ends = sorted(d.end_date for d in valid)
    parts = [f"{len(valid)} 个数据集覆盖 {starts[0]} ~ {ends[-1]}"]

    if starts[0] != ends[-1] and ends[0] != ends[-1]:
        parts.append(
            f"各数据集终点不一致（{ends[0]} ~ {ends[-1]}），"
            f"超过 {ends[0]} 的区间只有部分标的有数据"
        )
    else:
        parts.append(f"全部数据集都止于 {ends[-1]}：回测区间无法超过数据覆盖终点")

    pool = manifest.get("pool") if isinstance(manifest, dict) else None
    if isinstance(pool, dict) and isinstance(pool.get("label"), str):
        n_listed = pool.get("n_listed")
        listed = f"（清单登记 {n_listed} 只）" if isinstance(n_listed, int) else ""
        parts.append(f"标的池来自导入清单 {pool['label']}{listed}")

    if n_unavailable:
        parts.append(f"{n_unavailable} 只标的在导入时被数据闸门拒绝，见 unavailable_datasets")

    parts.append(
        "若标的池是「当前成分股」快照，回看历史存在幸存者偏差；"
        "软件没有历史成分股数据，无法修正这一偏差"
    )
    return DatasetCoverage(
        start_date=starts[0],
        end_date=ends[-1],
        n_datasets=len(valid),
        n_unavailable=n_unavailable,
        note="；".join(parts),
    )


@router.get("/datasets")
def list_datasets(data_root: Path = Depends(get_data_root)) -> DatasetListResponse:
    """返回已登记且可用的数据集列表；损坏的元数据与导入被拒的标的分别单列。"""
    ensure_data_root(data_root)
    valid, invalid = discover_datasets(data_root)
    unavailable = list_unavailable(data_root, {d.symbol for d in valid})
    manifest = read_pool_manifest(data_root)

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
                display_name=d.display_name,
            )
            for d in valid
        ],
        invalid_datasets=[
            InvalidDatasetEntry(
                metadata_file=d.metadata_file, symbol=d.symbol, reason=d.reason
            )
            for d in invalid
        ],
        unavailable_datasets=[
            UnavailableDatasetEntry(
                symbol=d.symbol,
                display_name=d.display_name,
                reason=d.reason,
                checked_at=d.checked_at,
            )
            for d in unavailable
        ],
        coverage=_build_coverage(valid, len(unavailable), manifest),
    )
