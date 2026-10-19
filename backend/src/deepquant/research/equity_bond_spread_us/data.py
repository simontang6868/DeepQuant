"""数据装载与派生序列。

口径（重要，报告中必须原样披露）：
- ``price``：标普 500 指数月度均价（multpl，等价于 Shiller 数据集里的 P，两者差异中位数 0.000）；
- ``cape``：周期调整市盈率（10 年通胀调整后平均盈利口径）；
- ``div_yield``：滚动 12 个月股息率（百分数）；
- ``cpi``：美国 CPI 指数（multpl，与 Shiller CPI 差异中位数 0.000）；
- ``earnings``：滚动 12 个月盈利（用于 PE1，最新月份可能滞后 1-2 个月）；
- ``y10``：美国 10 年期国债收益率（百分数）。1990-01 之前来自 Shiller 的 GS10，
  1990-01 起用 akshare 日频数据取月度均值；两源重叠 394 个月，
  平均绝对差 0.0044 个百分点，相关 0.99990。

总收益的构造方式（近似，但方向明确）：
    1 + r_t = (P_t + D_{t-1} / 12) / P_{t-1}
其中 ``D_{t-1} = div_yield_{t-1} / 100 * P_{t-1}`` 是上月末的滚动 12 个月股息额。
用**上月末**股息、而不是当月股息，是为了避免用到当月才可能知道的派息信息。
这不是官方全收益指数，长期年化误差在 0.1 个百分点量级，但个股/指数分红数据本身
在 19 世纪是估算值，报告中按「近似」处理。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

#: 面板必需列
REQUIRED_COLUMNS = ("price", "cape", "div_yield", "cpi", "y10")


def sha256_file(path: str | Path) -> str:
    """返回文件 SHA-256（用于数据可追溯清单）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def month_end(index: pd.Index) -> pd.DatetimeIndex:
    """把任意日期索引规范到「月末」，用于跨源对齐。"""
    idx = pd.DatetimeIndex(index)
    return idx + pd.offsets.MonthEnd(0)


@dataclass
class Panel:
    """面板数据 + 结构校验信息。"""

    frame: pd.DataFrame
    checks: dict

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def first_month(self) -> pd.Timestamp:
        return self.frame.index.min()

    @property
    def last_month(self) -> pd.Timestamp:
        return self.frame.index.max()


def load_raw_csv(path: str | Path) -> pd.DataFrame:
    """读取预处理脚本写入的原始面板 CSV。"""
    df = pd.read_csv(path)
    if "month" not in df.columns:
        raise ValueError(f"{path} 缺少 month 列")
    df["month"] = pd.to_datetime(df["month"])
    df["month"] = month_end(df["month"])
    df = df.set_index("month").sort_index()
    return df


def build_derived(frame: pd.DataFrame) -> pd.DataFrame:
    """由原始列派生：盈利收益率、利差、总收益指数、真实总收益指数。"""
    df = frame.copy()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"面板缺少必需列：{missing}")

    with np.errstate(divide="ignore", invalid="ignore"):
        df["ey_cape"] = 100.0 / df["cape"].where(df["cape"] > 0)
        if "earnings" in df.columns:
            df["pe1"] = df["price"] / df["earnings"].where(df["earnings"] > 0)
            df["ey_pe1"] = 100.0 / df["pe1"]
        else:  # pragma: no cover - 数据缺失时的显式降级
            df["pe1"] = np.nan
            df["ey_pe1"] = np.nan

    df["spread_cape"] = df["ey_cape"] - df["y10"]
    df["spread_pe1"] = df["ey_pe1"] - df["y10"]

    # 月度名义总收益（近似，见模块 docstring）
    p = df["price"].astype(float)
    dy = df["div_yield"].astype(float)
    d_prev = (dy.shift(1) / 100.0) * p.shift(1)          # 上月末滚动 12 个月股息额
    df["div_month"] = d_prev / 12.0
    df["ret"] = (p + df["div_month"]) / p.shift(1) - 1.0

    cpi = df["cpi"].astype(float)
    df["infl"] = cpi / cpi.shift(1) - 1.0

    # 指数：起点归一化为 1.0（不消耗首月缺失收益）
    tr = (1.0 + df["ret"].fillna(0.0)).cumprod()
    df["tr_index"] = tr
    df["real_tr_index"] = tr / (cpi / cpi.dropna().iloc[0])
    df["real_ret"] = (1.0 + df["ret"]) / (1.0 + df["infl"]) - 1.0
    return df


def validate(frame: pd.DataFrame) -> dict:
    """结构校验：月份连续性、缺失、异常值。返回可落盘的字典。"""
    idx = pd.DatetimeIndex(frame.index)
    expected = pd.date_range(idx.min(), idx.max(), freq="ME")
    gaps = expected.difference(idx)

    def _missing(col: str) -> int:
        return int(frame[col].isna().sum()) if col in frame.columns else -1

    checks = {
        "n_months": int(len(frame)),
        "first_month": str(idx.min().date()),
        "last_month": str(idx.max().date()),
        "expected_months": int(len(expected)),
        "missing_months": [str(d.date()) for d in gaps],
        "missing_counts": {c: _missing(c) for c in frame.columns},
        "monotonic": bool(idx.is_monotonic_increasing),
        "duplicated": int(idx.duplicated().sum()),
    }
    # 明显不合理的值（用于发现解析错误，不做修补）
    if "cape" in frame.columns:
        checks["cape_min"] = float(frame["cape"].min())
        checks["cape_max"] = float(frame["cape"].max())
    if "y10" in frame.columns:
        checks["y10_min"] = float(frame["y10"].min())
        checks["y10_max"] = float(frame["y10"].max())
    if "ret" in frame.columns:
        checks["ret_min"] = float(frame["ret"].min())
        checks["ret_max"] = float(frame["ret"].max())
        checks["ret_extreme_months"] = [
            str(d.date()) for d in frame.index[frame["ret"].abs() > 0.35]
        ]
    return checks


def load_panel(path: str | Path, with_derived: bool = True) -> Panel:
    """读取面板 CSV 并派生序列。"""
    raw = load_raw_csv(path)
    frame = build_derived(raw) if with_derived else raw
    return Panel(frame=frame, checks=validate(frame))


def slice_period(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """按 [start, end] 截取（含端点）。"""
    return frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]
