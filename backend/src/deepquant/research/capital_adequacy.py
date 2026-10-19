"""资金充足性口径：研究组合的"这笔钱到底买得起几只标的"。

背景（为什么需要这个模块）
--------------------------
`initial_cash=100_000` + A 股 100 股整手，与标的价格相互作用后会产生一个**静默**后果：

- 某只标的一手成本 > 初始资金时，它的所有买单都被拒（引擎行为正确，符合交易所规则）；
- 于是该标的在训练期零成交 → `min_trades` 不满足 → **参数选择失败**；
- 该标的在样本外整段消失，而汇总表仍然写着"6 只池"。

`docs/OOS_VERDICT_2026-10-19.md` 实测：`600519.SH` 自 2021 年起一手 > 13 万，
导致 2021 年后"6 只池"实际只有 4 只在跑。这不是回测缺陷，是**研究口径没有声明约束**。

本模块把该约束变成可执行、可复算、可入报告的东西，而不是靠人在跑之前记得。

设计约束
--------
1. **纯计算，无 IO**：不读文件、不看环境变量、不碰全局状态。
2. **不改既有默认行为**：不 import 本模块时，仓库行为与之前逐字节一致。
   `as_is` 模式显式返回"什么都不改"，用于对照实验。
3. **判据分两档，且必须显式选用**：
   - `strict`（默认）：用窗口内**最高**收盘价判——保证任意时点都买得起；
   - `lenient`：用窗口内**最低**收盘价判——只保证曾经有机会买得起。
   两档结论不同，混用会让"可建仓"这个词失去意义，因此不设默认隐式切换。

价格口径的重要提醒
------------------
输入若是**前复权（qfq）**价格，须知道：qfq 以最新价为基准回推，历史价被复权因子调整，
通常**低于**当时真实成交价（分红送转会下调历史价）。因此：

    用 qfq 算出的"一手成本"是真实一手成本的**下界**。

换言之，用 qfq 判"买不起"是**保守方向**——真实情况只会更买不起。
反过来，用 qfq 判"买得起"则**不足以下结论**。本模块的输出带 `price_basis` 字段，
调用方必须自己保证不要把这个方向搞反。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import pandas as pd

DEFAULT_LOT_SIZE = 100
"""A 股最小交易单位（股）。科创板等特殊板块有差异，本模块不覆盖。"""


@dataclass(frozen=True)
class SymbolCost:
    """单只标的在给定窗口内的一手成本区间。"""

    code: str
    rows: int
    price_min: float
    price_max: float
    lot_size: int

    @property
    def one_lot_min(self) -> float:
        """最宽松判据：窗口内最便宜时点的一手成本。"""
        return round(self.price_min * self.lot_size, 2)

    @property
    def one_lot_max(self) -> float:
        """最严格判据：窗口内最贵时点的一手成本。"""
        return round(self.price_max * self.lot_size, 2)

    def affordable(self, cash: float, basis: str = "strict") -> bool:
        need = self.one_lot_max if basis == "strict" else self.one_lot_min
        return need <= cash


@dataclass(frozen=True)
class CapitalPlan:
    """一次口径解析的结果。可直接用于构造 BacktestConfig 与有效标的集合。"""

    mode: str
    """as_is | raise_cash | drop_symbols | both"""

    initial_cash: float
    universe: tuple[str, ...]
    """解析后**实际参与**的标的（drop_* 模式会少于输入）。"""
    dropped: Mapping[str, str] = field(default_factory=dict)
    """被剔除的标的 → 原因（含具体金额，便于写进报告）。"""
    costs: Mapping[str, SymbolCost] = field(default_factory=dict)
    price_basis: str = "unknown"
    """调用方声明的价格口径（qfq / raw / unknown）。仅作记录，模块不校验。"""
    note: str = ""

    @property
    def n_effective(self) -> int:
        return len(self.universe)

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "initial_cash": self.initial_cash,
            "n_effective": self.n_effective,
            "universe": list(self.universe),
            "dropped": dict(self.dropped),
            "one_lot_max_by_symbol": {c: s.one_lot_max for c, s in self.costs.items()},
            "one_lot_min_by_symbol": {c: s.one_lot_min for c, s in self.costs.items()},
            "price_basis": self.price_basis,
            "note": self.note,
        }


def symbol_cost(
    code: str,
    frame: pd.DataFrame,
    *,
    price_col: str = "close",
    lot_size: int = DEFAULT_LOT_SIZE,
) -> SymbolCost:
    """从单只标的的价格序列提取一手成本区间。

    ``frame`` 需含 ``price_col`` 列；含 ``date`` 列时不做额外裁剪（调用方负责选窗口）。
    非正价格会被忽略（qfq 负价是已知数据瑕疵，不能用来算成本）。
    """
    if price_col not in frame.columns:
        raise KeyError(f"{code}: 缺少列 {price_col!r}，实际列 {list(frame.columns)}")
    s = pd.to_numeric(frame[price_col], errors="coerce").dropna()
    s = s[s > 0]
    if s.empty:
        raise ValueError(f"{code}: 窗口内没有可用的正价格，无法判定资金充足性")
    return SymbolCost(
        code=code,
        rows=int(len(s)),
        price_min=float(s.min()),
        price_max=float(s.max()),
        lot_size=lot_size,
    )


def _median(values: Iterable[float]) -> float:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        raise ValueError("空序列无法取中位数")
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def _round_up(value: float, unit: float) -> float:
    return math.ceil(value / unit) * unit


def plan_as_is(
    costs: Mapping[str, SymbolCost],
    cash: float,
    *,
    price_basis: str = "unknown",
) -> CapitalPlan:
    """对照口径：原样传资金与标的，不做任何调整。

    存在的意义是让"改口径带来了多少差异"可以被量化，而不是靠感觉。
    """
    return CapitalPlan(
        mode="as_is",
        initial_cash=float(cash),
        universe=tuple(costs),
        dropped={},
        costs=dict(costs),
        price_basis=price_basis,
        note="对照基线：资金与标的均不调整，保持历史口径。",
    )


def plan_raise_cash(
    costs: Mapping[str, SymbolCost],
    base_cash: float,
    *,
    basis: str = "strict",
    safety_factor: float = 1.10,
    round_to: float = 10_000.0,
    price_basis: str = "unknown",
) -> CapitalPlan:
    """方案 A：池不变，把初始资金提到"池内最贵标的也买得起"。

    ``initial_cash = ceil(max(一手成本) * safety_factor / round_to) * round_to``
    —— ``safety_factor`` 留出一点缓冲，避免"刚好等于一手"时因滑点/费用仍买不进
    （滑点通过成交价体现，若现金恰好等于一手，成交价上浮后仍会不足）。

    代价：资金规模一变，所有标的的仓位比例、费用占比、最小交易约束同时变化。
    这不是"只修好茅台"，而是换了一个组合。因此必须与 ``drop_symbols`` 对照看。
    """
    if not costs:
        raise ValueError("空标的集合无法解析资金口径")
    need = max(
        (s.one_lot_max if basis == "strict" else s.one_lot_min) for s in costs.values()
    )
    raised = _round_up(need * safety_factor, round_to)
    return CapitalPlan(
        mode="raise_cash",
        initial_cash=float(raised),
        universe=tuple(costs),
        dropped={},
        costs=dict(costs),
        price_basis=price_basis,
        note=(
            f"按 {basis} 判据覆盖池内最贵一手 {need:,.0f} 元，"
            f"×{safety_factor:g} 后上取整到 {round_to:,.0f} 元 = {raised:,.0f} 元。"
        ),
    )


def plan_drop_symbols(
    costs: Mapping[str, SymbolCost],
    cash: float,
    *,
    basis: str = "strict",
    price_basis: str = "unknown",
) -> CapitalPlan:
    """方案 B：资金不变，把"给定资金下买不起"的标的移出有效池。

    与 A 互补：A 保住了标的不变但换了组合规模，B 保住了组合规模但换了标的。
    两者都会改变"池"的含义，区别只在于改变的是哪一维。

    剔除原因里写明具体金额，便于报告直接引用，避免"某只被剔了但没人知道为什么"。
    """
    dropped: dict[str, str] = {}
    kept: list[str] = []
    for code, sc in costs.items():
        need = sc.one_lot_max if basis == "strict" else sc.one_lot_min
        if need > cash:
            dropped[code] = (
                f"一手 {need:,.0f} 元 > 初始资金 {cash:,.0f} 元（{basis} 判据），无法建仓"
            )
        else:
            kept.append(code)
    return CapitalPlan(
        mode="drop_symbols",
        initial_cash=float(cash),
        universe=tuple(kept),
        dropped=dropped,
        costs=dict(costs),
        price_basis=price_basis,
        note=f"{len(costs)} 只输入，剔除 {len(dropped)} 只后有效 {len(kept)} 只。",
    )


def _anchor_value(values: list[float], anchor: str) -> float:
    """池内一手成本的锚点值。"""
    vals = sorted(values)
    if not vals:
        raise ValueError("空序列无法取锚点")
    if anchor == "min":
        return vals[0]
    if anchor == "median":
        return _median(vals)
    if anchor == "p75":
        # 覆盖约 3/4 的标的：既能装下多数标的，又不会被单只极贵标的绑架
        pos = 0.75 * (len(vals) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)
    if anchor == "max":
        return vals[-1]
    raise ValueError(f"未知锚点 {anchor!r}，可选：min / median / p75 / max")


def plan_both(
    costs: Mapping[str, SymbolCost],
    base_cash: float,
    *,
    basis: str = "strict",
    safety_factor: float = 1.10,
    round_to: float = 10_000.0,
    anchor: str = "p75",
    price_basis: str = "unknown",
) -> CapitalPlan:
    """方案 C：以"多数标的"的资金水平定资金，再把超出太多的标的显式剔除。

    纯 ``raise_cash`` 的问题：只要池里有一只茅台，资金就被拉到 25 万以上，
    其余 5 只的仓位比例随之全部改变——**为了一只标的改动整个组合**。
    纯 ``drop_symbols`` 的问题：贵标的一律消失，且资金仍停在 10 万这个
    从未被论证过的数字上。

    折中：用池内一手成本的 ``anchor`` 定资金，再剔除超过它的标的。

    锚点默认 ``p75`` 而非中位数：中位数会被"池里小盘股多"拉得很低
    （本仓库 6 只池的中位数约 2.2 万，会把 300750 一起剔掉，而它并不算离谱）。
    p75 覆盖约 3/4 的标的，只剔真正离群的那只。
    """
    if not costs:
        raise ValueError("空标的集合无法解析资金口径")
    needs = [
        (s.one_lot_max if basis == "strict" else s.one_lot_min) for s in costs.values()
    ]
    ref = _anchor_value(needs, anchor)
    anchor_cash = _round_up(ref * safety_factor, round_to)
    # 保底：不允许把资金降到 base_cash 以下。
    # 否则"降低资金"这个与本题无关的变量会混进来——本方案要回答的是
    # "钱不够时怎么办"，把资金调低只会让更多标的买不起，方向恰好相反。
    cash = max(float(base_cash), anchor_cash)
    floored = cash > anchor_cash
    dropped: dict[str, str] = {}
    kept: list[str] = []
    for code, sc in costs.items():
        need = sc.one_lot_max if basis == "strict" else sc.one_lot_min
        if need > cash:
            dropped[code] = (
                f"全区间一手 {need:,.0f} 元 > 口径资金 {cash:,.0f} 元"
                f"（按池内 {anchor} 锚定，超 {need / cash:.1f} 倍）"
                f"；注意这是**全区间**预判，逐窗口是否剔除按各窗口价格另判"
            )
        else:
            kept.append(code)
    return CapitalPlan(
        mode="both",
        initial_cash=float(cash),
        universe=tuple(kept),
        dropped=dropped,
        costs=dict(costs),
        price_basis=price_basis,
        note=(
            f"池内 {anchor} 一手成本 {ref:,.0f} 元 ×{safety_factor:g} → {anchor_cash:,.0f} 元"
            + (f"，低于保底 {base_cash:,.0f} 元，取保底" if floored else "")
            + f"；全区间预判剔除 {len(dropped)} 只。"
            f"实际执行按**逐窗口**价格判定（同一标的在不同年份可买性不同）。"
        ),
    )


def resolve(
    mode: str,
    costs: Mapping[str, SymbolCost],
    base_cash: float,
    *,
    basis: str = "strict",
    safety_factor: float = 1.10,
    round_to: float = 10_000.0,
    anchor: str = "p75",
    price_basis: str = "unknown",
) -> CapitalPlan:
    """按模式名解析，供 CLI / 研究脚本统一入口使用。"""
    if mode == "as_is":
        return plan_as_is(costs, base_cash, price_basis=price_basis)
    if mode == "raise_cash":
        return plan_raise_cash(
            costs,
            base_cash,
            basis=basis,
            safety_factor=safety_factor,
            round_to=round_to,
            price_basis=price_basis,
        )
    if mode == "drop_symbols":
        return plan_drop_symbols(costs, base_cash, basis=basis, price_basis=price_basis)
    if mode == "both":
        return plan_both(
            costs,
            base_cash,
            basis=basis,
            safety_factor=safety_factor,
            round_to=round_to,
            anchor=anchor,
            price_basis=price_basis,
        )
    raise ValueError(
        f"未知口径 {mode!r}，可选：as_is / raise_cash / drop_symbols / both"
    )


__all__ = [
    "CapitalPlan",
    "DEFAULT_LOT_SIZE",
    "SymbolCost",
    "plan_as_is",
    "plan_both",
    "plan_drop_symbols",
    "plan_raise_cash",
    "resolve",
    "symbol_cost",
]
