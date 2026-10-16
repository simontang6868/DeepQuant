"""akshare A 股日线行情适配器（Step 7.5 正式版）。

职责：封装 akshare.stock_zh_a_hist 接口，返回 deepquant 数据管道
统一约定的 DataFrame（列名标准化、日期规范化）。

设计原则：
1. 明确的依赖检查 —— akshare 未安装时给出清晰错误；
2. 接口变化时给出 akshare 原始错误 + 字段映射对照表，方便诊断；
3. 不静默回退到合成行情，失败就是失败；
4. 明确的复权状态枚举值；
5. 返回 DataFrame 可直接喂给 csv_loader.load_daily_csv 或 pipeline.ingest_daily_csv。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

import pandas as pd

from deepquant.data.loaders.csv_loader import REQUIRED_COLUMNS


class AdjustStatus(str, Enum):
    """复权口径枚举。"""

    NONE = "none"          # 不复权
    QFQ = "qfq"            # 前复权（forward adjust）
    HFQ = "hfq"            # 后复权（backward adjust）
    UNKNOWN = "unknown"


class AkshareAdapterError(Exception):
    """akshare 适配器统一异常基类。"""


class AkshareNotInstalledError(AkshareAdapterError):
    """akshare 未安装。"""


class AkshareNetworkError(AkshareAdapterError):
    """网络请求失败。"""


class AkshareSchemaError(AkshareAdapterError):
    """akshare 返回字段与预期不符。"""


# akshare 字段 → deepquant 统一列名
_AKSHARE_COLUMN_MAP: dict[str, str] = {
    "日期": "date",
    "股票代码": "source_symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}

# 复权状态 → akshare stock_zh_a_hist adjust 参数
_ADJUST_TO_PARAM: dict[AdjustStatus, str] = {
    AdjustStatus.NONE: "",
    AdjustStatus.QFQ: "qfq",
    AdjustStatus.HFQ: "hfq",
}


@dataclass(frozen=True)
class FetchConfig:
    """一次 akshare 拉取的配置快照（用于 metadata 记录）。"""
    symbol_code: str
    start_date: str           # YYYYMMDD
    end_date: str             # YYYYMMDD
    adjust: AdjustStatus
    period: str = "daily"

    def to_metadata(self) -> dict:
        return {
            "adapter": "akshare.stock_zh_a_hist",
            "symbol_code": self.symbol_code,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "adjust_status": self.adjust.value,
            "period": self.period,
        }


def _require_akshare():
    """延迟导入 akshare；未安装时抛 AkshareNotInstalledError。"""
    try:
        import akshare as ak  # noqa: F401 — 只为确认安装
        return ak
    except ImportError as exc:
        raise AkshareNotInstalledError(
            "akshare 未安装。请执行 `pip install akshare`。"
        ) from exc


def fetch_daily_a_share(
    symbol_code: str,
    start_date: str,
    end_date: str,
    *,
    adjust: AdjustStatus | Literal["none", "qfq", "hfq", "unknown"] = AdjustStatus.QFQ,
    timeout: int = 30,
) -> tuple[pd.DataFrame, FetchConfig]:
    """拉取单只 A 股日线行情并返回标准化 DataFrame。

    参数：
        symbol_code: 6 位 A 股代码，如 "000001"（平安银行）、"600519"（贵州茅台）。
        start_date / end_date: YYYYMMDD 字符串，如 "20180101"、"20241231"。
        adjust: 复权口径，默认 QFQ（前复权）。字符串也可（自动转枚举）。
        timeout: 网络超时秒数。

    返回：
        (标准化 DataFrame, FetchConfig) — DataFrame 列: date, open, high, low, close, volume, amount。
        调用方可以直接写 CSV / 喂给 data.pipeline.ingest_daily_csv。

    异常：
        AkshareNotInstalledError: akshare 未安装。
        AkshareNetworkError: HTTP 请求失败。
        AkshareSchemaError: 返回字段不符合预期。
        AkshareAdapterError: 其他适配器错误。
    """
    # --- 0. 依赖检查 ---
    ak = _require_akshare()

    # --- 1. 参数规范化 ---
    if isinstance(adjust, str):
        try:
            adjust = AdjustStatus(adjust)
        except ValueError as exc:
            raise AkshareAdapterError(f"无法识别 adjust={adjust!r}") from exc

    if not (start_date.isdigit() and len(start_date) == 8):
        raise AkshareAdapterError(f"start_date 必须是 YYYYMMDD 格式，收到 {start_date!r}")
    if not (end_date.isdigit() and len(end_date) == 8):
        raise AkshareAdapterError(f"end_date 必须是 YYYYMMDD 格式，收到 {end_date!r}")
    if not (symbol_code.isdigit() and len(symbol_code) == 6):
        raise AkshareAdapterError(f"symbol_code 必须是 6 位数字，收到 {symbol_code!r}")

    fetch_cfg = FetchConfig(
        symbol_code=symbol_code, start_date=start_date,
        end_date=end_date, adjust=adjust,
    )

    # --- 2. 调用 akshare ---
    adjust_param = _ADJUST_TO_PARAM.get(adjust, "")
    try:
        df_raw = ak.stock_zh_a_hist(
            symbol=symbol_code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust_param,
        )
    except Exception as exc:  # akshare 抛出 Requests 异常 / 网络超时 / HTTP 4xx 等
        raise AkshareNetworkError(
            f"akshare stock_zh_a_hist 拉取失败 symbol={symbol_code} "
            f"range={start_date}-{end_date} adjust={adjust.value}: {exc}"
        ) from exc

    # --- 3. 字段映射 ---
    if df_raw is None or len(df_raw) == 0:
        # 空结果不算错误（请求范围内确实没有数据），返回空 DataFrame
        empty = pd.DataFrame(columns=list(REQUIRED_COLUMNS) + ["amount", "source_symbol"])
        return empty, fetch_cfg

    missing_cols = [c for c in _AKSHARE_COLUMN_MAP if c not in df_raw.columns]
    if missing_cols:
        raise AkshareSchemaError(
            f"akshare 返回字段变化！缺少 {missing_cols}；实际字段: {list(df_raw.columns)}。"
            f"字段映射表: {_AKSHARE_COLUMN_MAP}"
        )

    df = df_raw.rename(columns=_AKSHARE_COLUMN_MAP)

    # --- 4. 日期与数值规范化 ---
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.sort_values("date").reset_index(drop=True)
    return df, fetch_cfg
