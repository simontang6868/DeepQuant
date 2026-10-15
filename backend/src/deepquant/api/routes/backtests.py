"""POST /backtests：同步执行单标的日线回测。

职责边界（API 薄层）：
1. 请求参数已由 Pydantic 模型校验（未知字段拒绝、数值范围、日期范围）；
2. 按 dataset_symbol 在数据根目录内定位数据集（路径安全由 api.datasets 保证）；
3. 载入数据并复用既有数据质量校验（validate_daily）；
4. 通过显式注册表实例化策略；
5. 构造 BacktestConfig 并调用既有 run_backtest；
6. 复用 result_to_dict 生成响应（不重算绩效、不改写序列化逻辑）。

本层不撮合、不重算绩效、不改变账户状态，也不向客户端暴露文件系统路径。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends

from deepquant.api.config import get_data_root
from deepquant.api.datasets import ensure_data_root, find_dataset
from deepquant.api.errors import (
    BacktestRejectedError,
    ConfigInvalidError,
    DatasetUnavailableError,
)
from deepquant.api.registry import build_strategy
from deepquant.api.schemas import ApiInfo, BacktestRequest, BacktestResponse
from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.data.pipeline import DataValidationError
from deepquant.data.storage.io import read_daily_parquet
from deepquant.data.validation.checks import ERROR, validate_daily
from deepquant.performance.serialize import result_to_dict
from deepquant.version import __version__

router = APIRouter()


def _load_dataset_frame(processed_path: Path, symbol: str) -> pd.DataFrame:
    """读取已登记数据集；读取失败视为数据集不可用（详情只记录到服务端日志）。"""
    try:
        return read_daily_parquet(processed_path)
    except Exception as exc:  # noqa: BLE001 - 统一映射为「数据集不可用」，不泄露路径
        raise DatasetUnavailableError(
            f"数据集 {symbol!r} 已登记但不可用：数据文件无法读取"
        ) from exc


def _build_config(request: BacktestRequest) -> BacktestConfig:
    """把请求模型映射为 BacktestConfig（字段一一对应，业务语义由配置类校验）。

    exec_price_rule / market_rules / fee_source 使用引擎既定默认值
    （v1 仅支持 next_open 与 A 股规则；费用来源固定为模拟占位标识）。
    """
    try:
        return BacktestConfig(
            symbol=request.dataset_symbol,
            initial_cash=request.initial_cash,
            start_date=None if request.start_date is None else pd.Timestamp(request.start_date),
            end_date=None if request.end_date is None else pd.Timestamp(request.end_date),
            signal_lag=request.signal_lag,
            commission_rate=request.commission_rate,
            min_commission=request.min_commission,
            stamp_duty_rate=request.stamp_duty_rate,
            transfer_fee_rate=request.transfer_fee_rate,
            slippage_buy=request.slippage_buy,
            slippage_sell=request.slippage_sell,
        )
    except ValueError as exc:
        raise ConfigInvalidError(str(exc)) from exc


@router.post("/backtests")
def create_backtest(
    request: BacktestRequest, data_root: Path = Depends(get_data_root)
) -> BacktestResponse:
    """同步执行回测并返回结果（外层 api 元信息 + result_to_dict 原始输出）。"""
    ensure_data_root(data_root)

    _info, processed_path = find_dataset(data_root, request.dataset_symbol)
    df = _load_dataset_frame(processed_path, request.dataset_symbol)

    # 复用既有数据质量校验：ERROR 级问题按数据校验错误处理（400）
    report = validate_daily(df)
    if not report.is_valid:
        detail = "; ".join(f"{i.code}({i.count})" for i in report.issues if i.level == ERROR)
        raise DataValidationError(f"数据集 {request.dataset_symbol!r} 存在严重数据质量问题：{detail}")

    strategy = build_strategy(request.strategy_id, request.strategy_params)
    config = _build_config(request)

    try:
        result = run_backtest(df, strategy, config)
    except ValueError as exc:
        # 请求合法但执行条件不满足（如回测区间内无行情数据）
        raise BacktestRejectedError(str(exc)) from exc

    # SerializationError 由全局异常处理器映射为 500，不在此吞掉
    document = result_to_dict(result)
    return BacktestResponse(
        api=ApiInfo(name="deepquant-api", version=__version__),
        result=document,
    )
