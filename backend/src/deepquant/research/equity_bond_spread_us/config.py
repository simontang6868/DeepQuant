"""美股股债利差研究：参数集中配置。

设计约定（沿用 A 股研究的纪律）：
- 所有阈值、窗口、起止日期、随机种子集中管理，便于重复实验；
- 配置可序列化，随结果一起落盘；
- 非法组合在构造时报错，而不是在计算中悄悄产出 NaN；
- **主检验参数不来自事后挑选**：主口径（阈值 90%、扩展窗口、60 个月）在看结果前固定。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 月度观测下的年观测数
OBS_PER_YEAR = 12

#: 预先登记的候选观察窗口（月）：1 年 / 3 年 / 5 年 / 10 年
DEFAULT_HORIZONS = (12, 36, 60, 120)


def _check_pos_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} 必须是 int，收到 {type(value)!r}")
    if value <= 0:
        raise ValueError(f"{name} 必须 > 0，收到 {value}")
    return value


@dataclass(frozen=True)
class USConfig:
    """一次完整实验的全部参数。"""

    #: 前视收益窗口（月）
    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    #: 主检验窗口（月）；预登记为 60（5 年），其余为次级
    primary_horizon: int = 60
    #: 信号阈值（历史百分位，0-100）
    threshold_pct: float = 90.0
    #: 百分位窗口长度（月）；扩展窗口下用于计算百分位的滚动窗口
    pct_window: int = 120
    #: 扩展窗口百分位所需的最小历史观测数（月）；120 = 10 年
    min_history: int = 120
    #: 信号确认后的执行延迟（月）；1 表示下一个月成交，禁止 0
    exec_lag: int = 1
    #: 事件合并：两次触发间隔小于该月数则合并为同一事件
    event_gap: int = 12
    #: 研究区间（含端点，按月末标记）
    start_date: str = "1881-01-31"
    end_date: str = "2026-09-30"
    #: 样本内截止日（含）；样本外为其之后
    is_end_date: str = "1949-12-31"
    #: 块自举次数
    n_boot: int = 5000
    #: 随机种子（固定，保证可复现）
    seed: int = 20261018
    #: 是否使用扩展窗口百分位（False 则用固定 ``pct_window`` 滚动窗口）
    expanding: bool = True
    #: Newey-West 滞后阶数相对窗口的倍数
    nw_lag_multiple: float = 1.5

    #: 数据文件名（相对 ``data/equity_bond_spread_us``）
    panel_csv: str = "panel_us.csv"

    def __post_init__(self) -> None:
        if not self.horizons:
            raise ValueError("horizons 不能为空")
        for h in self.horizons:
            _check_pos_int(h, "horizons 元素")
        if list(self.horizons) != sorted(self.horizons):
            raise ValueError(f"horizons 必须升序：{self.horizons}")
        if len(set(self.horizons)) != len(self.horizons):
            raise ValueError(f"horizons 存在重复值：{self.horizons}")
        if self.primary_horizon not in self.horizons:
            raise ValueError(f"primary_horizon({self.primary_horizon}) 必须在 horizons 内")
        if not (0.0 < self.threshold_pct <= 100.0):
            raise ValueError(f"threshold_pct 必须在 (0, 100]，收到 {self.threshold_pct}")
        _check_pos_int(self.pct_window, "pct_window")
        _check_pos_int(self.min_history, "min_history")
        if self.min_history > self.pct_window and not self.expanding:
            raise ValueError("固定窗口下 min_history 不能大于 pct_window")
        if not isinstance(self.exec_lag, int) or self.exec_lag < 1:
            raise ValueError(
                f"exec_lag 必须 >= 1（禁止信号月同月成交，属未来函数），收到 {self.exec_lag}"
            )
        _check_pos_int(self.event_gap, "event_gap")
        if self.start_date >= self.end_date:
            raise ValueError(f"start_date({self.start_date}) 必须早于 end_date({self.end_date})")
        if not (self.start_date <= self.is_end_date < self.end_date):
            raise ValueError(f"is_end_date({self.is_end_date}) 必须落在 [start_date, end_date) 内")
        _check_pos_int(self.n_boot, "n_boot")
        if not isinstance(self.seed, int):
            raise TypeError(f"seed 必须是 int，收到 {type(self.seed)!r}")
        if self.nw_lag_multiple <= 0:
            raise ValueError(f"nw_lag_multiple 必须 > 0，收到 {self.nw_lag_multiple}")

    # ------------------------------------------------------------------ 派生
    def nw_lag(self, horizon: int) -> int:
        """重叠 h 期收益的 Newey-West 滞后阶数。

        重叠窗口会引入至少 h-1 阶的自相关；按 1.5 倍窗口取滞后，属于保守设定。
        """
        return max(1, int(round(horizon * self.nw_lag_multiple)))

    def to_dict(self) -> dict:
        """序列化（用于结果落盘，保证可追溯）。"""
        return {
            "horizons": list(self.horizons),
            "primary_horizon": self.primary_horizon,
            "threshold_pct": self.threshold_pct,
            "pct_window": self.pct_window,
            "min_history": self.min_history,
            "exec_lag": self.exec_lag,
            "event_gap": self.event_gap,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "is_end_date": self.is_end_date,
            "n_boot": self.n_boot,
            "seed": self.seed,
            "expanding": self.expanding,
            "nw_lag_multiple": self.nw_lag_multiple,
            "panel_csv": self.panel_csv,
        }


@dataclass(frozen=True)
class SensitivityGrid:
    """敏感性网格（事前一并登记，不是看结果后挑参数）。"""

    thresholds: tuple[float, ...] = (80.0, 85.0, 90.0, 95.0)
    horizons: tuple[int, ...] = (12, 36, 60, 120)
    predictors: tuple[str, ...] = ("spread_cape", "ey_cape")
    windows: tuple[str, ...] = ("expanding", "fixed")

    def configs(self) -> list[USConfig]:
        """展开成配置列表。"""
        out: list[USConfig] = []
        for thr in self.thresholds:
            for h in self.horizons:
                out.append(
                    USConfig(
                        threshold_pct=thr,
                        horizons=tuple(sorted({h, 12, 36, 60, 120})),
                    )
                )
        return out


@dataclass
class RunSpec:
    """单个「预测变量 × 窗口」的检验描述。"""

    predictor: str
    horizon: int
    sample: str = "full"  # full / is / oos
    extra: dict = field(default_factory=dict)

    def label(self) -> str:
        return f"{self.predictor}@{self.horizon}m/{self.sample}"
