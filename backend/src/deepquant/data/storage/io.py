"""本地数据落盘 / 读取的基础工具。

只提供文件级原语（CSV / JSON / 校验和）；行情 CSV 的解析统一走 loaders。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


def file_sha256(path: str | Path) -> str:
    """计算文件的 SHA-256，用于记录原始数据的指纹。"""
    digest = hashlib.sha256()
    digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def write_daily_csv(df: pd.DataFrame, path: str | Path) -> Path:
    """将行情 DataFrame 写为 CSV（不写索引）。

    明确使用 UTF-8 编码（不再依赖操作系统默认编码，避免 Windows 本地 GBK 乱码）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def write_daily_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    """将行情 DataFrame 写为 Parquet（保留列类型，不写索引）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def read_daily_parquet(path: str | Path) -> pd.DataFrame:
    """读取 Parquet 行情数据（date 等类型由 Parquet 元数据还原）。"""
    return pd.read_parquet(Path(path))


def write_json(obj: Any, path: str | Path) -> Path:
    """写出 UTF-8 缩进 JSON（支持中文，不转义）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
