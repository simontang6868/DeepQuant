"""akshare A 股日线行情适配器（Step 7.5 正式版 + B 线双源 fallback）。

职责：封装 akshare 的 A 股日线接口，返回 deepquant 数据管道
统一约定的 DataFrame（列名标准化、日期规范化）。

数据源（两个都是 akshare 公开接口返回的**真实公开行情**，任选其一都不改变数据性质）：
- eastmoney —— ``ak.stock_zh_a_hist``，返回**中文**列名；
- tencent   —— ``ak.stock_zh_a_hist_tx``，返回**英文**列名，需要 ``sh/sz/bj`` 市场前缀。

为什么要两个源：本机实测（2026-10-17）``push2his.eastmoney.com`` 被云端出口 proxy 拦截
（``ProxyError: Unable to connect to proxy``），东财源直接不可用；而腾讯源同环境下
1.3~1.4 秒返回 38 行真实行情。单源实现会让这条链路在部分网络环境下直接死掉。

设计原则：
1. 明确的依赖检查 —— akshare 未安装时给出清晰错误；
2. 接口变化时给出 akshare 原始错误 + 字段映射对照表，方便诊断；
3. 不静默回退到合成行情，失败就是失败 —— 这里 fallback 的是**另一个真实数据源**，
   不是合成数据，且实际使用的源会写进 metadata，保证可溯源；
4. 全部数据源都失败时抛异常，绝不返回空 DataFrame 冒充"无数据"；
5. 明确的复权状态枚举值；
6. 返回 DataFrame 可直接喂给 csv_loader.load_daily_csv 或 pipeline.ingest_daily_csv。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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

# 腾讯源（stock_zh_a_hist_tx）字段 → deepquant 统一列名。
# 腾讯返回英文列名且已经是统一命名，映射表恒等 —— 保留它是为了做**缺列检测**：
# 上游一旦改名/删列，这里会立刻抓到，而不是悄悄产出缺列的 DataFrame。
_TENCENT_COLUMN_MAP: dict[str, str] = {
    "date": "date",
    "open": "open",
    "close": "close",
    "high": "high",
    "low": "low",
    "volume": "volume",
    "amount": "amount",
}

# 复权状态 → akshare adjust 参数（东财 / 腾讯两源取值一致）
_ADJUST_TO_PARAM: dict[AdjustStatus, str] = {
    AdjustStatus.NONE: "",
    AdjustStatus.QFQ: "qfq",
    AdjustStatus.HFQ: "hfq",
}

# 数据源标识 → (akshare 函数名, 字段映射表)
_SOURCES: dict[str, tuple[str, dict[str, str]]] = {
    "eastmoney": ("stock_zh_a_hist", _AKSHARE_COLUMN_MAP),
    "tencent": ("stock_zh_a_hist_tx", _TENCENT_COLUMN_MAP),
}

# 源选择策略 → 实际尝试顺序。auto = 东财优先、被拦则 fallback 腾讯。
_SOURCE_ORDER: dict[str, tuple[str, ...]] = {
    "auto": ("eastmoney", "tencent"),
    "eastmoney": ("eastmoney",),
    "tencent": ("tencent",),
}

# 6 位代码 → 腾讯代码需要的市场前缀
_TENCENT_PREFIX: dict[str, str] = {
    "6": "sh",   # 沪市主板 / 科创板（688）
    "0": "sz",   # 深市主板
    "3": "sz",   # 创业板
    "4": "bj",   # 北交所
    "8": "bj",   # 北交所
}

AkshareSource = Literal["auto", "eastmoney", "tencent"]


@dataclass(frozen=True)
class FetchConfig:
    """一次 akshare 拉取的配置快照（用于 metadata 记录）。"""
    symbol_code: str
    start_date: str           # YYYYMMDD
    end_date: str             # YYYYMMDD
    adjust: AdjustStatus
    period: str = "daily"
    source: str = "auto"      # 请求时指定的源选择策略：auto / eastmoney / tencent
    used_source: str = ""     # 实际返回数据的源；"" 表示尚未成功取到数据

    def to_metadata(self) -> dict:
        adapter = _SOURCES[self.used_source][0] if self.used_source else ""
        return {
            "adapter": f"akshare.{adapter}" if adapter else "akshare.<none>",
            "source": self.source,
            "used_source": self.used_source,
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


def _to_tencent_symbol(symbol_code: str) -> str:
    """6 位 A 股代码 → 腾讯源需要的带市场前缀代码（如 000001 → sz000001）。

    前缀推断不了就报错：宁可明确失败，也不猜一个市场去拉错数据。
    """
    prefix = _TENCENT_PREFIX.get(symbol_code[:1])
    if prefix is None:
        raise AkshareAdapterError(
            f"无法为腾讯源推断市场前缀，symbol_code={symbol_code!r}；"
            f"已知映射: {_TENCENT_PREFIX}"
        )
    return f"{prefix}{symbol_code}"


def _call_source(
    ak,
    source: str,
    symbol_code: str,
    tencent_symbol: str,
    start_date: str,
    end_date: str,
    adjust: AdjustStatus,
) -> pd.DataFrame:
    """调用单个数据源的原始 akshare 接口，网络类失败统一包成 AkshareNetworkError。"""
    fn_name, _ = _SOURCES[source]
    adjust_param = _ADJUST_TO_PARAM.get(adjust, "")
    try:
        fn = getattr(ak, fn_name)
        if source == "eastmoney":
            return fn(
                symbol=symbol_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjust_param,
            )
        return fn(
            symbol=tencent_symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust_param,
        )
    except AkshareAdapterError:
        raise
    except Exception as exc:  # akshare 抛出 Requests 异常 / 网络超时 / HTTP 4xx / proxy 拦截
        raise AkshareNetworkError(
            f"{fn_name} 请求失败 symbol={symbol_code} "
            f"range={start_date}-{end_date} adjust={adjust.value}: {exc}"
        ) from exc


def _normalize_frame(
    df_raw,
    column_map: dict[str, str],
    source: str,
    symbol_code: str,
) -> pd.DataFrame:
    """字段映射 + 日期/数值规范化。

    空结果不算错误（请求区间内确实没有数据），返回带完整列的空 DataFrame。
    """
    if df_raw is None or len(df_raw) == 0:
        # 空结果不算错误（请求范围内确实没有数据），返回空 DataFrame
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS) + ["amount", "source_symbol"])

    missing_cols = [c for c in column_map if c not in df_raw.columns]
    if missing_cols:
        raise AkshareSchemaError(
            f"akshare {source} 源返回字段变化！缺少 {missing_cols}；"
            f"实际字段: {list(df_raw.columns)}。字段映射表: {column_map}"
        )

    df = df_raw.rename(columns=column_map)

    # 腾讯源返回里没有 stock 代码列，补上与东财源一致的 source_symbol，保证两源输出口径一致
    if "source_symbol" not in df.columns:
        df["source_symbol"] = symbol_code

    # 日期与数值规范化
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.sort_values("date").reset_index(drop=True)


def fetch_daily_a_share(
    symbol_code: str,
    start_date: str,
    end_date: str,
    *,
    adjust: AdjustStatus | Literal["none", "qfq", "hfq", "unknown"] = AdjustStatus.QFQ,
    source: AkshareSource = "auto",
    timeout: int = 30,
) -> tuple[pd.DataFrame, FetchConfig]:
    """拉取单只 A 股日线行情并返回标准化 DataFrame。

    参数：
        symbol_code: 6 位 A 股代码，如 "000001"（平安银行）、"600519"（贵州茅台）。
        start_date / end_date: YYYYMMDD 字符串，如 "20180101"、"20241231"。
        adjust: 复权口径，默认 QFQ（前复权）。字符串也可（自动转枚举）。
        source: 数据源策略。
            - "auto"（默认）：东财优先，**网络失败时**自动 fallback 到腾讯；
            - "eastmoney" / "tencent"：只用指定源，失败即抛。
        timeout: 保留参数（当前 akshare 接口未暴露超时控制，暂不生效）。

    返回：
        (标准化 DataFrame, FetchConfig) — DataFrame 列: date, open, high, low, close, volume, amount。
        调用方可以直接写 CSV / 喂给 data.pipeline.ingest_daily_csv。
        **FetchConfig.used_source 会记录实际生效的数据源**，用于数据溯源（红线 R3）。

    异常：
        AkshareNotInstalledError: akshare 未安装。
        AkshareNetworkError: 全部候选数据源都请求失败。
        AkshareSchemaError: 返回字段不符合预期（**不 fallback**，需人工确认上游变更）。
        AkshareAdapterError: 参数错误等其他适配器错误。
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
    if source not in _SOURCE_ORDER:
        raise AkshareAdapterError(
            f"无法识别 source={source!r}；可选: {sorted(_SOURCE_ORDER)}"
        )

    # 腾讯源的市场前缀属于参数问题（不是网络问题），提前算，失败直接抛、不做 fallback
    tencent_symbol = (
        _to_tencent_symbol(symbol_code)
        if "tencent" in _SOURCE_ORDER[source]
        else ""
    )

    errors: list[str] = []
    for name in _SOURCE_ORDER[source]:
        try:
            df_raw = _call_source(
                ak, name, symbol_code, tencent_symbol,
                start_date, end_date, adjust,
            )
        except AkshareNetworkError as exc:
            # 网络/可达性失败 → 换下一个真实数据源（不是合成数据）
            errors.append(f"[{name}] {exc}")
            continue

        df = _normalize_frame(df_raw, _SOURCES[name][1], name, symbol_code)
        cfg = FetchConfig(
            symbol_code=symbol_code, start_date=start_date,
            end_date=end_date, adjust=adjust,
            source=source, used_source=name,
        )
        return df, cfg

    raise AkshareNetworkError(
        f"全部数据源均失败 symbol={symbol_code} range={start_date}-{end_date} "
        f"adjust={adjust.value} source={source}: " + " | ".join(errors)
    )
