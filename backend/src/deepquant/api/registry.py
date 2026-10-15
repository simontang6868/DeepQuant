"""策略显式注册表（API 层唯一策略入口）。

设计原则：
- 客户端只能通过注册表中的 strategy_id 选择策略；
- 不做子类反射、不接受模块路径或类名，内部/测试用策略不会被意外暴露；
- 参数说明（类型/默认值/约束）以策略真实能力为依据，版本号与策略类保持一致；
- 参数校验不绕过策略自身的约束：注册表先做类型/未知参数检查，
  业务约束（如 slow_window > fast_window）仍由策略类在实例化时校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from deepquant.api.errors import StrategyNotFoundError, StrategyParamsError
from deepquant.strategies.base import Strategy
from deepquant.strategies.ma_cross import MaCrossStrategy


@dataclass(frozen=True)
class StrategyParamSpec:
    """单个策略参数的对外说明。"""

    type: str  # JSON 类型名（"integer" / "number" / "string" / "boolean"）
    default: Any
    description: str
    minimum: float | None = None


@dataclass(frozen=True)
class StrategySpec:
    """一个可对外提供回测的策略条目。"""

    strategy_id: str
    display_name: str
    version: str
    description: str
    params: dict[str, StrategyParamSpec]
    factory: Callable[[dict[str, Any]], Strategy]


def _build_ma_cross(params: dict[str, Any]) -> Strategy:
    return MaCrossStrategy(**params)


# 显式注册表：新增对外策略时必须在此登记（未登记者不对客户端可见）
STRATEGY_REGISTRY: dict[str, StrategySpec] = {
    "ma_cross": StrategySpec(
        strategy_id="ma_cross",
        display_name="双均线交叉策略",
        version=MaCrossStrategy.version,
        description=(
            "快线（短周期均线）上穿慢线（长周期均线）时买入，下穿时卖出；"
            "两条均线都有效时才判定交叉，预热期不产出信号。"
        ),
        params={
            "fast_window": StrategyParamSpec(
                type="integer",
                default=5,
                minimum=1,
                description="快线窗口长度（交易日），必须 >= 1",
            ),
            "slow_window": StrategyParamSpec(
                type="integer",
                default=20,
                minimum=2,
                description="慢线窗口长度（交易日），必须大于 fast_window",
            ),
        },
        factory=_build_ma_cross,
    ),
}


def list_strategies() -> list[StrategySpec]:
    """返回注册表条目（按 strategy_id 升序，排序确定）。"""
    return [STRATEGY_REGISTRY[k] for k in sorted(STRATEGY_REGISTRY)]


def _check_param_type(spec: StrategyParamSpec, value: Any, name: str) -> None:
    if spec.type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise StrategyParamsError(f"参数 {name} 必须是整数，收到 {value!r}")
    elif spec.type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise StrategyParamsError(f"参数 {name} 必须是数值，收到 {value!r}")
    elif spec.type == "boolean":
        if not isinstance(value, bool):
            raise StrategyParamsError(f"参数 {name} 必须是布尔值，收到 {value!r}")
    elif spec.type == "string":
        if not isinstance(value, str):
            raise StrategyParamsError(f"参数 {name} 必须是字符串，收到 {value!r}")


def build_strategy(strategy_id: str, params: dict[str, Any]) -> Strategy:
    """按注册表实例化策略。

    - 未注册的 strategy_id 抛出 StrategyNotFoundError（404）；
    - 未知参数、参数类型错误抛出 StrategyParamsError（422）；
    - 业务约束（如 slow_window > fast_window）由策略类校验，ValueError 转为 422。
    """
    spec = STRATEGY_REGISTRY.get(strategy_id)
    if spec is None:
        raise StrategyNotFoundError(f"策略 {strategy_id!r} 未注册")
    if not isinstance(params, dict):
        raise StrategyParamsError("strategy_params 必须是对象")

    unknown = sorted(k for k in params if k not in spec.params)
    if unknown:
        raise StrategyParamsError(
            f"策略 {strategy_id!r} 不支持参数 {unknown}，可选参数：{sorted(spec.params)}"
        )

    kwargs: dict[str, Any] = {name: p.default for name, p in spec.params.items()}
    for name, value in params.items():
        _check_param_type(spec.params[name], value, name)
        kwargs[name] = value

    try:
        return spec.factory(kwargs)
    except ValueError as exc:
        raise StrategyParamsError(str(exc)) from exc
