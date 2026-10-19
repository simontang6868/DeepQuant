"""股债利差与长期市场机会识别研究（Equity-Bond Spread Research）。

研究目标（可证伪）：检验「股票相对债券的收益/估值优势处于历史高位时，
是否领先于沪深300 的较好后续表现」这一假设在 A 股历史上是否成立。

模块划分：
- ``config``：全部参数集中管理与校验
- ``data``：数据装载、清洗、对齐、覆盖率诊断与 SHA-256 摘要
- ``indicators``：利差、盈利收益率、滚动年化收益、历史百分位
- ``signals``：阈值信号、连续信号合并、执行时点
- ``experiment``：前瞻收益、基准比较、样本内外、敏感性
- ``report``：中文研究报告生成

红线（Red lines，研究纪律）：
R1 不使用未来数据（百分位、前瞻收益、执行时点均严格滞后）
R2 不根据样本外结果挑选参数（参数只能在样本内比较）
R3 不把合成数据标为真实；数据缺口必须如实披露
R4 不把价格指数收益称为含分红总收益；近似口径必须标注
R5 不因结论不利而删改失败案例；所有满足规则的信号事件全部报告
R6 不用"没有证据证明无效"冒充"已经证明有效"
"""

from __future__ import annotations

from .config import (
    DataConfig,
    IndicatorType,
    PercentileWindow,
    ResearchConfig,
    ReturnBasis,
    sensitivity_grid,
)
from .data import ResearchPanel, build_panel, sha256_file, validate_panel
from .experiment import ExperimentResult, run_experiment, run_sensitivity
from .indicators import (
    build_indicator_frame,
    compute_spread,
    earnings_yield,
    historical_percentile,
    rolling_annualized_return,
)
from .signals import SpreadEvent, extract_events, merge_signal_runs, trading_days_to_obs

__all__ = [
    "DataConfig",
    "IndicatorType",
    "PercentileWindow",
    "ResearchConfig",
    "ReturnBasis",
    "sensitivity_grid",
    "ResearchPanel",
    "build_panel",
    "sha256_file",
    "validate_panel",
    "ExperimentResult",
    "run_experiment",
    "run_sensitivity",
    "build_indicator_frame",
    "compute_spread",
    "earnings_yield",
    "historical_percentile",
    "rolling_annualized_return",
    "SpreadEvent",
    "extract_events",
    "merge_signal_runs",
    "trading_days_to_obs",
]
