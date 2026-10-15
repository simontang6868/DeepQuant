"""GET /strategies：列出当前支持的策略及参数说明（来自显式注册表）。"""

from __future__ import annotations

from fastapi import APIRouter

from deepquant.api.registry import list_strategies
from deepquant.api.schemas import StrategyInfo, StrategyListResponse, StrategyParamInfo

router = APIRouter()


@router.get("/strategies")
def get_strategies() -> StrategyListResponse:
    """返回显式注册表中的策略列表（按 strategy_id 升序）。"""
    return StrategyListResponse(
        strategies=[
            StrategyInfo(
                strategy_id=spec.strategy_id,
                display_name=spec.display_name,
                version=spec.version,
                description=spec.description,
                params={
                    name: StrategyParamInfo(
                        type=p.type,
                        default=p.default,
                        description=p.description,
                        minimum=p.minimum,
                    )
                    for name, p in spec.params.items()
                },
            )
            for spec in list_strategies()
        ]
    )
