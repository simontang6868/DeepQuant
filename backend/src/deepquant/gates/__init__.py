"""五级闸门中的 L1a（向量化粗筛）：机制层实现。

计划依据：《Vibe-Trading 调研结论落地推进计划 v1.1》§6.1 表 7。L1a 的定位是
"成本低、覆盖广"的第一道筛子——它**不判断策略能不能赚钱**，只判断三件事：

1. 这份假设值不值得进入更贵的引擎（契约完整、数据达标、没有未来函数）；
2. 参数空间是不是真的扫过了（覆盖率以声明点数为分母）；
3. 样本内外的收益与回撤是否落在**事先写死**的边界内。

模块分工
--------
- :mod:`deepquant.gates.spec`：假设契约（网格 / 区间 / 边界 / 数据源）
- :mod:`deepquant.gates.metrics`：诚实指标（算不出就写"不可计算"）
- :mod:`deepquant.gates.data_gate`：数据门禁（一票否决项之一）
- :mod:`deepquant.gates.leakage`：未来函数检测（静态 AST + 动态截断）
- :mod:`deepquant.gates.sweep`：网格扫描、覆盖率、样本内选参、样本外只评一次
- :mod:`deepquant.gates.gate`：否决链编排与终态枚举
- :mod:`deepquant.gates.report`：Markdown 报告

本包已实现的闸门：

- L1a（:mod:`deepquant.gates.gate`）：向量化粗筛，含契约/数据/未来函数/覆盖率/边界
- L1b（:mod:`deepquant.gates.l1b`）：事件驱动精验，量化成交摩擦
- L2（:mod:`deepquant.gates.l2`）：块重抽样蒙特卡洛，判"是否依赖极端路径"
- L3（:mod:`deepquant.gates.l3`）：滚动窗口 Walk-Forward，判"样本内外是否系统性背离"

边界：L1b 的撮合保真在 :mod:`deepquant.eventloop`，本包只消费它的产出；L4（限额模拟盘）
需要实盘/仿真通道，尚未实现。任一级通过**不等于**策略可用，只等于"值得进下一级"。
"""

from __future__ import annotations

from deepquant.gates.data_gate import Check, DataVerdict, check_data, check_frame, load_frame
from deepquant.gates.gate import (
    ERROR,
    INCONCLUSIVE,
    PASS,
    REJECTED_BOUNDS,
    REJECTED_COVERAGE,
    REJECTED_DATA,
    REJECTED_LEAKAGE,
    REJECTED_SPEC,
    VERDICTS,
    GateResult,
    Stage,
    run_l1a,
)
from deepquant.gates.leakage import LeakFinding, LeakResult, check_leakage, scan_source, truncation_test
from deepquant.gates.metrics import (
    NOT_COMPUTABLE,
    BoundCheck,
    Metric,
    check_bound,
    summarise,
    unavailable,
)
from deepquant.gates.report import render_markdown
from deepquant.gates.spec import Boundaries, DataSource, HypothesisSpec, SpecError, dump_spec, load_spec, parse_spec, Window
from deepquant.gates.sweep import SweepResult, expand_grid, run_sweep

__all__ = [
    "ERROR",
    "INCONCLUSIVE",
    "NOT_COMPUTABLE",
    "PASS",
    "REJECTED_BOUNDS",
    "REJECTED_COVERAGE",
    "REJECTED_DATA",
    "REJECTED_LEAKAGE",
    "REJECTED_SPEC",
    "VERDICTS",
    "Boundaries",
    "BoundCheck",
    "Check",
    "DataSource",
    "DataVerdict",
    "GateResult",
    "HypothesisSpec",
    "LeakFinding",
    "LeakResult",
    "Metric",
    "SpecError",
    "Stage",
    "SweepResult",
    "Window",
    "check_bound",
    "check_data",
    "check_frame",
    "check_leakage",
    "dump_spec",
    "expand_grid",
    "load_frame",
    "load_spec",
    "parse_spec",
    "render_markdown",
    "run_l1a",
    "run_sweep",
    "scan_source",
    "summarise",
    "truncation_test",
    "unavailable",
]
