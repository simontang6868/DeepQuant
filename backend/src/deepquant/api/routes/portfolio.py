"""POST /portfolio-backtests：多标的组合回测路由。

本路由只做三件事：取数据根目录、调执行薄层、包一层 API 元信息。
参数校验在 Pydantic 模型里（`api/schemas.py`），撮合与绩效在
``backtest/portfolio.py`` 里；这里没有第二套规则。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from deepquant.api.config import get_data_root
from deepquant.api.datasets import ensure_data_root
from deepquant.api.portfolio import run_portfolio
from deepquant.api.schemas import (
    ApiInfo,
    PortfolioBacktestRequest,
    PortfolioBacktestResponse,
)
from deepquant.version import __version__

router = APIRouter()


@router.post(
    "/portfolio-backtests",
    response_model=PortfolioBacktestResponse,
    summary="多标的组合回测（同步）",
    description=(
        "多标的、共享现金池、连续净值的月度调仓组合回测。默认参数即 Gate 1 主实验口径"
        "（动量 60·5 / 等权 top 20% / min_assets=5）。响应中的 limitations 会如实列出"
        "无法修正的已知偏差（flat 10% 涨跌停、池快照的幸存者偏差、权重记账不受整手约束）。"
    ),
)
def create_portfolio_backtest(
    request: PortfolioBacktestRequest,
    data_root: Path = Depends(get_data_root),
) -> PortfolioBacktestResponse:
    ensure_data_root(data_root)
    result = run_portfolio(request, data_root)
    return PortfolioBacktestResponse(api=ApiInfo(name="DeepQuant", version=__version__), result=result)
