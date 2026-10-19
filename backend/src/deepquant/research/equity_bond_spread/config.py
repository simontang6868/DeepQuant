"""股债利差研究：参数集中配置。

设计约定（对应研究方案阶段 2/3）：
- 所有阈值、窗口、起止日期、成本参数都在这里集中管理，便于重复实验；
- 配置可序列化为 dict，随实验结果一起落盘，保证可追溯；
- 配置自带校验，非法组合在构造时报错，而不是在计算中悄悄产出 NaN。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

TRADING_DAYS_PER_YEAR = 252

#: 各观测频率下的年观测数（用于窗口与最小样本换算）
OBS_PER_YEAR = {"daily": 252, "weekly": 52, "monthly": 12}


class IndicatorType(str, Enum):
    """两个待检验的研究假设。"""

    #: 假设 A：过去 H 年股票年化收益率 − 当期 10 年期国债收益率
    A = "A"
    #: 假设 B：盈利收益率（1/PE_TTM）− 当期 10 年期国债收益率
    B = "B"


class PercentileWindow(str, Enum):
    """历史百分位计算窗口类型。"""

    #: 扩展窗口：使用截至当前时点之前的全部可用历史
    EXPANDING = "expanding"
    #: 固定窗口：只使用过去 N 年（按年观测数折算）的历史
    FIXED = "fixed"


class ReturnBasis(str, Enum):
    """股票历史收益口径。

    PRICE：仅价格指数收益（口径明确，但不含分红）。
    TOTAL_APPROX：价格收益 + 窗口内平均股息率的**近似**总收益。
        这是近似值，不等于真正的分红再投资全收益指数收益，报告中必须标注。
    """

    PRICE = "price"
    TOTAL_APPROX = "total_approx"


DEFAULT_FORWARD_WINDOWS_TD = (20, 60, 120, 250)


def _check_positive_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} 必须是 int，收到 {type(value)!r}")
    if value <= 0:
        raise ValueError(f"{name} 必须 > 0，收到 {value}")
    return value


@dataclass(frozen=True)
class DataConfig:
    """数据位置与口径。"""

    #: 沪深300 行情与估值序列（清洗后）相对路径
    equity_csv: str = "csi300_weekly.csv"
    #: 中国 10 年期国债收益率序列（清洗后）相对路径
    bond_csv: str = "cn10y.csv"
    #: 观测频率（daily / weekly / monthly）；影响年观测数换算
    frequency: str = "monthly"
    #: 国债收益率匹配规则：forward（用不晚于该日的最新值向前填充）
    bond_match_rule: str = "forward_fill"
    #: 向前填充的最大自然日数；超过则视为缺失（不得用更久之前的数据冒充当日）
    bond_max_fill_days: int = 10
    #: PE 合法区间（含端点）；区间外视为异常，不参与信号
    pe_valid_min: float = 1.0
    pe_valid_max: float = 300.0
    #: 国债收益率合法区间（百分数）
    bond_valid_min: float = 0.1
    bond_valid_max: float = 15.0

    def __post_init__(self) -> None:
        if self.frequency not in OBS_PER_YEAR:
            raise ValueError(
                f"frequency 必须是 {sorted(OBS_PER_YEAR)}，收到 {self.frequency!r}"
            )
        if self.bond_match_rule not in ("forward_fill", "exact"):
            raise ValueError(
                f"bond_match_rule 必须是 forward_fill/exact，收到 {self.bond_match_rule!r}"
            )
        _check_positive_int(self.bond_max_fill_days, "bond_max_fill_days")
        if not (0 < self.pe_valid_min < self.pe_valid_max):
            raise ValueError("pe_valid_min 必须小于 pe_valid_max 且为正")
        if not (0 < self.bond_valid_min < self.bond_valid_max):
            raise ValueError("bond_valid_min 必须小于 bond_valid_max 且为正")

    def obs_per_year(self) -> int:
        """该频率下每年大约的观测数（用于窗口换算）。"""
        return OBS_PER_YEAR[self.frequency]


@dataclass(frozen=True)
class ResearchConfig:
    """一次完整实验的全部参数。"""

    #: 指标类型（假设 A / B）
    indicator: IndicatorType = IndicatorType.B
    #: 股票历史收益口径（仅假设 A 使用）
    return_basis: ReturnBasis = ReturnBasis.PRICE
    #: 假设 A 的股票收益回溯年限（年）
    lookback_years: int = 5
    #: 历史百分位窗口类型
    percentile_window: PercentileWindow = PercentileWindow.EXPANDING
    #: 固定窗口百分位的年数（percentile_window=FIXED 时生效）
    percentile_fixed_years: int = 10
    #: 输出百分位所需的最小历史观测数；不足则不输出信号（避免样本不足的伪精确）
    #: 默认 36 = 月频下约 3 年；换频率时应按 obs_per_year 重新设定
    min_history_obs: int = 36
    #: 信号阈值（历史百分位，0-100）
    threshold_pct: float = 90.0
    #: 事件合并：两次信号间隔小于该观测数则合并为同一事件
    min_gap_obs: int = 4
    #: 信号确认后的执行延迟（观测数）；1 表示下一个观测执行，禁止 0
    exec_lag_obs: int = 1
    #: 研究区间起点（含）
    start_date: str = "2005-01-01"
    #: 研究区间终点（含）
    end_date: str = "2026-10-16"
    #: 未来收益观察窗口（交易日）
    forward_windows_td: tuple[int, ...] = DEFAULT_FORWARD_WINDOWS_TD
    #: 样本内截止日（含）；样本外为其之后的部分
    is_end_date: str = "2018-12-31"
    #: 基准指数标识
    benchmark: str = "000300.SH"
    #: 单边交易成本（含佣金与印花税等，研究用占位参数）
    cost_rate: float = 0.0015
    #: 实验名称（用于结果目录命名）
    experiment_name: str = "default"

    data: DataConfig = field(default_factory=DataConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.indicator, IndicatorType):
            raise TypeError(f"indicator 必须是 IndicatorType，收到 {type(self.indicator)!r}")
        if not isinstance(self.return_basis, ReturnBasis):
            raise TypeError(f"return_basis 必须是 ReturnBasis，收到 {type(self.return_basis)!r}")
        if not isinstance(self.percentile_window, PercentileWindow):
            raise TypeError(
                f"percentile_window 必须是 PercentileWindow，收到 {type(self.percentile_window)!r}"
            )
        _check_positive_int(self.lookback_years, "lookback_years")
        _check_positive_int(self.percentile_fixed_years, "percentile_fixed_years")
        _check_positive_int(self.min_history_obs, "min_history_obs")
        _check_positive_int(self.min_gap_obs, "min_gap_obs")
        if not isinstance(self.exec_lag_obs, int) or self.exec_lag_obs < 1:
            raise ValueError(
                f"exec_lag_obs 必须 >= 1（禁止信号日同日成交，属未来函数），收到 {self.exec_lag_obs}"
            )
        if not (0.0 < self.threshold_pct <= 100.0):
            raise ValueError(f"threshold_pct 必须在 (0, 100]，收到 {self.threshold_pct}")
        if not (0.0 <= self.cost_rate < 0.1):
            raise ValueError(f"cost_rate 必须在 [0, 0.1)，收到 {self.cost_rate}")
        if not self.forward_windows_td:
            raise ValueError("forward_windows_td 不能为空")
        for w in self.forward_windows_td:
            _check_positive_int(w, "forward_windows_td 元素")
        if len(set(self.forward_windows_td)) != len(self.forward_windows_td):
            raise ValueError(f"forward_windows_td 存在重复值：{self.forward_windows_td}")
        if list(self.forward_windows_td) != sorted(self.forward_windows_td):
            raise ValueError(f"forward_windows_td 必须升序：{self.forward_windows_td}")
        if self.start_date >= self.end_date:
            raise ValueError(f"start_date({self.start_date}) 必须早于 end_date({self.end_date})")
        if not (self.start_date <= self.is_end_date < self.end_date):
            raise ValueError(
                f"is_end_date({self.is_end_date}) 必须落在 [start_date, end_date) 内"
            )

    def to_dict(self) -> dict:
        """序列化（用于实验记录与结果落盘）。"""
        return {
            "experiment_name": self.experiment_name,
            "indicator": self.indicator.value,
            "return_basis": self.return_basis.value,
            "lookback_years": self.lookback_years,
            "percentile_window": self.percentile_window.value,
            "percentile_fixed_years": self.percentile_fixed_years,
            "min_history_obs": self.min_history_obs,
            "threshold_pct": self.threshold_pct,
            "min_gap_obs": self.min_gap_obs,
            "exec_lag_obs": self.exec_lag_obs,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "forward_windows_td": list(self.forward_windows_td),
            "is_end_date": self.is_end_date,
            "benchmark": self.benchmark,
            "cost_rate": self.cost_rate,
            "data": {
                "equity_csv": self.data.equity_csv,
                "bond_csv": self.data.bond_csv,
                "frequency": self.data.frequency,
                "bond_match_rule": self.data.bond_match_rule,
                "bond_max_fill_days": self.data.bond_max_fill_days,
                "pe_valid_min": self.data.pe_valid_min,
                "pe_valid_max": self.data.pe_valid_max,
                "bond_valid_min": self.data.bond_valid_min,
                "bond_valid_max": self.data.bond_valid_max,
            },
        }


def sensitivity_grid() -> list[ResearchConfig]:
    """阶段 5 要求的敏感性对比网格（阈值 × 回溯年限 × 指标）。

    注意：这是**预先定义**的对比网格，不是根据结果事后挑选的参数。
    """
    configs: list[ResearchConfig] = []
    # 假设 B：阈值敏感性（盈利收益率口径与回溯年限无关）
    for threshold in (80.0, 85.0, 90.0, 95.0):
        configs.append(
            ResearchConfig(
                indicator=IndicatorType.B,
                threshold_pct=threshold,
                experiment_name=f"B_pe_thr{int(threshold)}",
            )
        )
    # 假设 A：阈值 × 回溯年限
    for lookback in (5, 10):
        for threshold in (80.0, 85.0, 90.0, 95.0):
            configs.append(
                ResearchConfig(
                    indicator=IndicatorType.A,
                    lookback_years=lookback,
                    threshold_pct=threshold,
                    experiment_name=f"A_{lookback}y_thr{int(threshold)}",
                )
            )
    # 假设 A：总收益近似口径（口径稳健性）
    configs.append(
        ResearchConfig(
            indicator=IndicatorType.A,
            return_basis=ReturnBasis.TOTAL_APPROX,
            lookback_years=5,
            threshold_pct=90.0,
            experiment_name="A_5y_total_approx_thr90",
        )
    )
    # 假设 B：固定窗口百分位（窗口类型稳健性）
    configs.append(
        ResearchConfig(
            indicator=IndicatorType.B,
            percentile_window=PercentileWindow.FIXED,
            percentile_fixed_years=10,
            threshold_pct=90.0,
            experiment_name="B_fixed10y_thr90",
        )
    )
    return configs
