"""API 请求 / 响应的 Pydantic 模型。

约定：
- 请求模型 ``extra="forbid"``：未知字段一律拒绝，避免静默忽略拼错的参数；
- 标识符（dataset_symbol / strategy_id）使用与数据集白名单一致的字符约束，
  在参数校验阶段即排除路径分隔符、盘符与 ``..``；
- 数值字段禁止 NaN / Infinity（allow_inf_nan=False）；
- 响应中的 ``result`` 原样承载 ``result_to_dict`` 输出（既有 JSON 契约不变），
  API 元信息放在外层 ``api`` 字段，不侵入结果文档。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from deepquant.backtest.config import (
    SIMULATION_COMMISSION_RATE,
    SIMULATION_MIN_COMMISSION,
    SIMULATION_SLIPPAGE_BUY,
    SIMULATION_SLIPPAGE_SELL,
    SIMULATION_STAMP_DUTY_RATE,
    SIMULATION_TRANSFER_FEE_RATE,
)

# 与 api.datasets.SYMBOL_PATTERN 保持一致（Pydantic 用字符串形式）
IDENTIFIER_REGEX = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


# --------------------------------------------------------------------------- #
# GET /datasets
# --------------------------------------------------------------------------- #


class DatasetEntry(BaseModel):
    """单个可用数据集的对外摘要（字段来自已落盘元数据）。"""

    symbol: str
    rows: int = Field(description="清洗后的日线行数（回测实际使用的数据量）")
    start_date: str
    end_date: str
    source_name: str | None = None
    source_sha256: str | None = Field(
        default=None, description="导入时原始 CSV 文件的 SHA-256 指纹"
    )
    ingested_at: str | None = None


class InvalidDatasetEntry(BaseModel):
    """未通过校验的元数据条目（只暴露文件名与通用原因）。"""

    metadata_file: str
    symbol: str | None = None
    reason: str


class DatasetListResponse(BaseModel):
    datasets: list[DatasetEntry]
    invalid_datasets: list[InvalidDatasetEntry]


# --------------------------------------------------------------------------- #
# GET /strategies
# --------------------------------------------------------------------------- #


class StrategyParamInfo(BaseModel):
    type: str
    default: Any
    description: str
    minimum: float | None = None


class StrategyInfo(BaseModel):
    strategy_id: str
    display_name: str
    version: str
    description: str
    params: dict[str, StrategyParamInfo]


class StrategyListResponse(BaseModel):
    strategies: list[StrategyInfo]


# --------------------------------------------------------------------------- #
# POST /backtests
# --------------------------------------------------------------------------- #


class BacktestRequest(BaseModel):
    """同步回测请求。

    费用与滑点参数的默认值为**模拟占位参数**（仅用于开发/测试/模拟），
    不代表真实券商费率；响应中通过 config.fee_source 标明实际来源。
    """

    model_config = ConfigDict(extra="forbid")

    dataset_symbol: str = Field(pattern=IDENTIFIER_REGEX, description="已登记的数据集标识")
    strategy_id: str = Field(pattern=IDENTIFIER_REGEX, description="策略注册表中的标识符")
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    initial_cash: float = Field(gt=0, allow_inf_nan=False, description="初始资金")
    start_date: date | None = Field(default=None, description="回测起始日（闭区间）")
    end_date: date | None = Field(default=None, description="回测结束日（闭区间）")
    signal_lag: int = Field(default=1, ge=1, description="信号延迟（>= 1 个交易日）")

    # --- 费用与滑点（模拟占位默认值，可在请求中显式覆盖）---
    commission_rate: float = Field(default=SIMULATION_COMMISSION_RATE, ge=0, allow_inf_nan=False)
    min_commission: float = Field(default=SIMULATION_MIN_COMMISSION, ge=0, allow_inf_nan=False)
    stamp_duty_rate: float = Field(default=SIMULATION_STAMP_DUTY_RATE, ge=0, allow_inf_nan=False)
    transfer_fee_rate: float = Field(default=SIMULATION_TRANSFER_FEE_RATE, ge=0, allow_inf_nan=False)
    slippage_buy: float = Field(default=SIMULATION_SLIPPAGE_BUY, ge=0, allow_inf_nan=False)
    slippage_sell: float = Field(default=SIMULATION_SLIPPAGE_SELL, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_date_range(self) -> "BacktestRequest":
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        return self


class ApiInfo(BaseModel):
    """响应外层元信息（不侵入既有结果 JSON 契约）。"""

    name: str
    version: str


class BacktestResponse(BaseModel):
    api: ApiInfo
    result: dict[str, Any] = Field(
        description="result_to_dict 的原始输出（schema_version/engine_version/config/period/"
        "strategy/dataset/orders/fills/account_states/performance/reproducibility 等）"
    )
