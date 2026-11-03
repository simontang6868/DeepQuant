"""L1a 闸门的输入契约：策略假设、边界、数据源。

为什么先写契约再写判定
----------------------
闸门的公信力来自「判定标准事先写死」。如果边界值可以事后填，闸门就退化成
给已有结论补一张合格证。因此本模块把**仓位无关的全部输入**固化成一个可校验、
可摘要的对象：

- 策略与参数网格（含声明点数，用于核算覆盖率）
- 样本内 / 样本外区间（用于检查切分是否清晰）
- 收益与回撤边界（样本内外各一套，正数方向统一）
- 数据源（研究层 + 交易层，见下）

研究层与交易层：为什么必须是两份数据
------------------------------------
本仓库的约定是 ``qfq``（复权）算收益、``exec``（真不复权）判交易状态。L1a 是日频
粗筛，撮合保真度不是它的职责（那是 L1b），所以回测跑在**研究层**上，避免除权日
的价格跳空污染收益序列；交易层只用来做**跨源一致性抽检**——这是「数据不达标」
一票否决项里唯一能用现有数据算出来的部分。

契约校验失败一律视为不通过（fail-closed）：缺字段、方向写反、区间重叠、
网格为空，都不允许「用默认值补上」——默认值会让一个没想清楚的假设悄悄通过。
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

SPEC_VERSION = "l1a-hypothesis-v1"


class SpecError(ValueError):
    """假设契约不完整或自相矛盾。"""


@dataclass(frozen=True)
class DataSource:
    """数据源：研究层（算收益）+ 交易层（跨源抽检，可选）。"""

    symbol: str
    research_path: str
    trading_path: str | None = None
    source_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Boundaries:
    """预设边界。收益类用小数（0.15 = 15%），回撤用正数（0.30 = 回撤 30%）。"""

    min_in_sample_return: float
    max_in_sample_drawdown: float
    min_closed_trades: int
    min_out_sample_return: float
    max_out_sample_drawdown: float
    #: 允许的样本外相对衰减（0.5 表示样本外收益不得低于样本内的 50%）。
    max_oos_degradation: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Window:
    start: str
    end: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True)
class HypothesisSpec:
    """一份待过闸门的策略假设。"""

    hypothesis_id: str
    title: str
    strategy_id: str
    param_grid: dict[str, list[Any]]
    data: DataSource
    in_sample: Window
    out_sample: Window
    boundaries: Boundaries
    initial_cash: float = 100_000.0
    selection_metric: str = "cumulative_return"
    #: 声明要跑的网格点数。必须与「通过约束过滤后的组合数」一致，否则契约自相矛盾。
    declared_grid_points: int | None = None
    #: 边界取值依据（写进报告，避免"这个数是怎么定的"事后无从追溯）。
    boundary_basis: str = ""
    #: 精验（L1b）的执行假设段。L1a 契约为空；为空时不写进 to_dict，
    #: 以免改变既有 L1a 契约的 spec_digest。
    execution: dict[str, Any] = field(default_factory=dict)
    #: 滚动窗口（L3）的切窗规则与阈值。同样只在非空时写进 to_dict。
    walk_forward: dict[str, Any] = field(default_factory=dict)
    #: 组合层（L3p）的记账口径。非空即写进 to_dict——若这里不承载，调用方传入
    #: HypothesisSpec 时这段配置会被静默丢弃，闸门退回默认口径却毫无提示。
    portfolio: dict[str, Any] = field(default_factory=dict)
    #: 参数间的结构性约束，形如 ``("fast_window", "slow_window")`` 表示前者必须严格小于后者。
    #: 违反约束的组合**不计入分母**——它是契约里就不可能成立的组合，不是"漏跑"。
    constraints: tuple[tuple[str, str], ...] = ()
    spec_version: str = SPEC_VERSION

    # ---------------- 派生量 ---------------- #
    def violates_constraint(self, params: Mapping[str, Any]) -> bool:
        for left, right in self.constraints:
            if left in params and right in params and not params[left] < params[right]:
                return True
        return False

    def grid_points(self) -> list[dict[str, Any]]:
        """通过约束过滤后的参数组合（顺序确定）。"""
        names = sorted(self.param_grid)
        value_lists = [list(self.param_grid[name]) for name in names]
        combos = [dict(zip(names, combo)) for combo in itertools.product(*value_lists)]
        return [combo for combo in combos if not self.violates_constraint(combo)]

    @property
    def grid_size(self) -> int:
        """有效组合数（分母）。被约束排除的组合不算未覆盖。"""
        return len(self.grid_points())

    @property
    def excluded_by_constraint(self) -> int:
        total = 1
        for values in self.param_grid.values():
            total *= len(values)
        return total - self.grid_size

    def digest(self) -> str:
        """契约摘要：判定结果必须能对上这个摘要，否则报告无法追溯。"""
        payload = self.to_dict()
        payload.pop("spec_digest", None)
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "spec_version": self.spec_version,
            "hypothesis_id": self.hypothesis_id,
            "title": self.title,
            "strategy_id": self.strategy_id,
            "param_grid": self.param_grid,
            "data": self.data.to_dict(),
            "in_sample": self.in_sample.to_dict(),
            "out_sample": self.out_sample.to_dict(),
            "boundaries": self.boundaries.to_dict(),
            "boundary_basis": self.boundary_basis,
            "initial_cash": self.initial_cash,
            "selection_metric": self.selection_metric,
            "declared_grid_points": self.declared_grid_points,
            "grid_size": self.grid_size,
            "constraints": [list(pair) for pair in self.constraints],
            "excluded_by_constraint": self.excluded_by_constraint,
        }
        # 只在非空时写入：L1a 契约没有这一段，写空字典会改变其 spec_digest，
        # 让"已冻结的契约摘要"无故变化。
        if self.execution:
            payload["execution"] = dict(self.execution)
        if self.walk_forward:
            payload["walk_forward"] = dict(self.walk_forward)
        if self.portfolio:
            payload["portfolio"] = dict(self.portfolio)
        return payload


SUPPORTED_SELECTION_METRICS = ("cumulative_return", "calmar_like")


def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise SpecError(f"{where} 缺少字段 {key!r}")
    return mapping[key]


def _as_float(value: Any, where: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SpecError(f"{where} 必须是数值，收到 {value!r}") from exc


def _as_int(value: Any, where: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SpecError(f"{where} 必须是整数，收到 {value!r}") from exc


def parse_spec(payload: Mapping[str, Any]) -> HypothesisSpec:
    """从 JSON 字典构造契约，并把所有不自洽处一次性报出来。"""
    data_raw = _require(payload, "data", "顶层")
    boundaries_raw = _require(payload, "boundaries", "顶层")
    grid = _require(payload, "param_grid", "顶层")
    if not isinstance(grid, Mapping) or not grid:
        raise SpecError("param_grid 必须是非空对象（参数名 → 候选值列表）")
    cleaned_grid: dict[str, list[Any]] = {}
    for name, values in grid.items():
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
            raise SpecError(f"param_grid[{name!r}] 必须是非空列表")
        cleaned_grid[str(name)] = list(values)

    boundaries = Boundaries(
        min_in_sample_return=_as_float(
            _require(boundaries_raw, "min_in_sample_return", "boundaries"), "boundaries.min_in_sample_return"
        ),
        max_in_sample_drawdown=_as_float(
            _require(boundaries_raw, "max_in_sample_drawdown", "boundaries"),
            "boundaries.max_in_sample_drawdown",
        ),
        min_closed_trades=_as_int(
            _require(boundaries_raw, "min_closed_trades", "boundaries"), "boundaries.min_closed_trades"
        ),
        min_out_sample_return=_as_float(
            _require(boundaries_raw, "min_out_sample_return", "boundaries"),
            "boundaries.min_out_sample_return",
        ),
        max_out_sample_drawdown=_as_float(
            _require(boundaries_raw, "max_out_sample_drawdown", "boundaries"),
            "boundaries.max_out_sample_drawdown",
        ),
        max_oos_degradation=_as_float(
            boundaries_raw.get("max_oos_degradation", 0.5), "boundaries.max_oos_degradation"
        ),
    )
    _validate_boundaries(boundaries)

    in_sample = Window(
        start=str(_require(_require(payload, "in_sample", "顶层"), "start", "in_sample")),
        end=str(_require(_require(payload, "in_sample", "顶层"), "end", "in_sample")),
    )
    out_sample = Window(
        start=str(_require(_require(payload, "out_sample", "顶层"), "start", "out_sample")),
        end=str(_require(_require(payload, "out_sample", "顶层"), "end", "out_sample")),
    )
    _validate_windows(in_sample, out_sample)

    source = DataSource(
        symbol=str(_require(data_raw, "symbol", "data")),
        research_path=str(_require(data_raw, "research_path", "data")),
        trading_path=(str(data_raw["trading_path"]) if data_raw.get("trading_path") else None),
        source_note=str(data_raw.get("source_note", "")),
    )

    constraints: list[tuple[str, str]] = []
    for item in payload.get("constraints") or []:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
            raise SpecError(f"constraints 元素必须是 [参数A, 参数B]，收到 {item!r}")
        left, right = str(item[0]), str(item[1])
        if left not in cleaned_grid or right not in cleaned_grid:
            raise SpecError(f"constraint ({left}, {right}) 引用了 param_grid 中不存在的参数")
        constraints.append((left, right))

    selection_metric = str(payload.get("selection_metric", "cumulative_return"))
    if selection_metric not in SUPPORTED_SELECTION_METRICS:
        raise SpecError(
            f"selection_metric 必须是 {SUPPORTED_SELECTION_METRICS} 之一，收到 {selection_metric!r}"
        )

    declared = payload.get("declared_grid_points")
    spec = HypothesisSpec(
        hypothesis_id=str(_require(payload, "hypothesis_id", "顶层")),
        title=str(_require(payload, "title", "顶层")),
        strategy_id=str(_require(payload, "strategy_id", "顶层")),
        param_grid=cleaned_grid,
        data=source,
        in_sample=in_sample,
        out_sample=out_sample,
        boundaries=boundaries,
        initial_cash=_as_float(payload.get("initial_cash", 100_000.0), "initial_cash"),
        selection_metric=selection_metric,
        declared_grid_points=(_as_int(declared, "declared_grid_points") if declared is not None else None),
        boundary_basis=str(payload.get("boundary_basis", "")),
        execution=dict(payload.get("execution") or {}),
        walk_forward=dict(payload.get("walk_forward") or {}),
        portfolio=dict(payload.get("portfolio") or {}),
        constraints=tuple(constraints),
    )
    if spec.grid_size == 0:
        raise SpecError("约束把参数组合全部排除，网格为空")
    if spec.declared_grid_points is not None and spec.declared_grid_points != spec.grid_size:
        raise SpecError(
            f"declared_grid_points={spec.declared_grid_points} 与 param_grid 实际组合数 "
            f"{spec.grid_size} 不一致：契约自相矛盾，不允许事后修正"
        )
    if spec.initial_cash <= 0:
        raise SpecError("initial_cash 必须为正")
    return spec


def _validate_boundaries(boundaries: Boundaries) -> None:
    if boundaries.max_in_sample_drawdown <= 0 or boundaries.max_in_sample_drawdown >= 1:
        raise SpecError("boundaries.max_in_sample_drawdown 必须在 (0,1) 内")
    if boundaries.max_out_sample_drawdown <= 0 or boundaries.max_out_sample_drawdown >= 1:
        raise SpecError("boundaries.max_out_sample_drawdown 必须在 (0,1) 内")
    if boundaries.min_closed_trades < 1:
        raise SpecError("boundaries.min_closed_trades 必须 ≥1（0 笔交易的策略无法评价）")
    if not (0.0 <= boundaries.max_oos_degradation <= 1.0):
        raise SpecError("boundaries.max_oos_degradation 必须在 [0,1] 内")


def _validate_windows(in_sample: Window, out_sample: Window) -> None:
    if in_sample.start >= in_sample.end:
        raise SpecError(f"in_sample 区间非法：{in_sample.start} ≥ {in_sample.end}")
    if out_sample.start >= out_sample.end:
        raise SpecError(f"out_sample 区间非法：{out_sample.start} ≥ {out_sample.end}")
    if out_sample.start <= in_sample.end:
        raise SpecError(
            f"样本外区间必须完全在样本内之后（in_sample.end={in_sample.end}，"
            f"out_sample.start={out_sample.start}）：重叠即等于用样本外调参"
        )


def load_spec(path: str | Path) -> HypothesisSpec:
    """从文件读取契约。JSON 解析失败也按契约错误处理，便于闸门统一记否决。"""
    text = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"契约文件不是合法 JSON：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise SpecError("契约文件顶层必须是对象")
    return parse_spec(payload)


def dump_spec(spec: HypothesisSpec) -> dict[str, Any]:
    payload = spec.to_dict()
    payload["spec_digest"] = spec.digest()
    return payload
