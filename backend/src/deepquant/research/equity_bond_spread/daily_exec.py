"""日频执行核验层。

**这个模块只做一件事**：把月频口径下已经确定的信号事件，映射到真实交易日执行，
再用日频收盘价精确计算前瞻收益，从而量化「月频折算」带来的执行误差。

设计原则（均为防前视偏差的硬约束）：

1. 信号判定**完全沿用**月频口径（:func:`signals.extract_events`），本模块不重新判定
   信号、不调整阈值、不改动任何参数。唯一变化的是执行与收益计量方式。
2. 执行价取「信号日**之后**第一个交易日」的收盘价。信号在月末收盘后才可知，
   最短可执行时点即次一交易日；不允许用信号日当日收盘成交。
3. 持有期使用**精确交易日数**（20/60/120/250），不再按月折算。
   月频口径的 250 交易日会被折成 12 个月（约 244~250 个交易日，随月份长度漂移）。
4. 日频数据未覆盖的区间**不得**用插值、月频值或任何估计量填充，
   直接标记 ``daily_available=False`` 并在结果中如实体现。
5. 未成熟样本（持有期超出日频数据末端）标记 ``is_immature=True``，不参与聚合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

#: 精确交易日窗口
DAILY_WINDOWS: tuple[int, ...] = (20, 60, 120, 250)

#: 执行日与最近可用日频观测之间的最大容许间隔（自然日）。
#: 超过此值视为「日频数据未覆盖该时点」，**不允许**顺延使用远端价格。
#: 这条约束防止把 2015 年的信号错误映射到 2021 年才补上的价格上。
MAX_EXEC_GAP_DAYS = 10

#: 持有期内相邻观测的最大容许间隔（自然日）。
#: A 股最长连续休市为春节/国庆，约 9~10 个自然日；超过即说明序列有空洞。
MAX_HOLD_GAP_DAYS = 20


# --------------------------------------------------------------------------- #
# 数据装载
# --------------------------------------------------------------------------- #
def load_daily_close(path: str | Path) -> pd.Series:
    """从 CSV 装载日频收盘价序列。

    接受列名 ``date`` / ``close``（大小写不敏感，容忍 BOM 与前导空格）。
    重复日期保留最后一条并去重，按日期升序排列。
    """
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    lower = {c.lower(): c for c in df.columns}
    for need in ("date", "close"):
        if need not in lower:
            raise ValueError(f"日频文件缺少列 {need!r}，实际列：{list(df.columns)}")
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df[lower["date"]], errors="coerce"),
            "close": pd.to_numeric(df[lower["close"]], errors="coerce"),
        }
    )
    out = out.dropna(subset=["date"]).sort_values("date")
    out = out.drop_duplicates(subset=["date"], keep="last")
    s = out.set_index("date")["close"].astype("float64")
    s = s.where(s > 0)
    s.name = "close"
    return s.dropna()


def load_trading_calendar(path: str | Path) -> pd.DatetimeIndex:
    """装载交易日历（单列日期）。"""
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lstrip("\ufeff") for c in df.columns]
    col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    idx = pd.to_datetime(df[col], errors="coerce").dropna().sort_values().unique()
    return pd.DatetimeIndex(idx)


# --------------------------------------------------------------------------- #
# 时点映射
# --------------------------------------------------------------------------- #
def next_trading_day(calendar: pd.DatetimeIndex, date: pd.Timestamp) -> pd.Timestamp | None:
    """返回严格晚于 ``date`` 的第一个交易日；无可用后续日期时返回 None。"""
    pos = calendar.searchsorted(pd.Timestamp(date), side="right")
    if pos >= len(calendar):
        return None
    return pd.Timestamp(calendar[pos])


def _price_at_or_after(daily: pd.Series, date: pd.Timestamp) -> pd.Timestamp | None:
    """日频序列中不早于 ``date`` 的第一个有价日期（用于基准观测日对齐）。"""
    pos = daily.index.searchsorted(pd.Timestamp(date), side="left")
    if pos >= len(daily):
        return None
    return pd.Timestamp(daily.index[pos])


# --------------------------------------------------------------------------- #
# 路径统计
# --------------------------------------------------------------------------- #
def _path_stats(prices: np.ndarray) -> dict:
    """持有期内的路径风险（基于日频收盘）。"""
    if prices.size < 2:
        return {"max_drawdown": np.nan, "max_favorable": np.nan, "max_adverse": np.nan}
    peak = np.maximum.accumulate(prices)
    dd = prices / peak - 1.0
    return {
        "max_drawdown": float(np.min(dd)),
        "max_favorable": float(np.max(prices / prices[0] - 1.0)),
        "max_adverse": float(np.min(prices / prices[0] - 1.0)),
    }


@dataclass
class DailyOutcome:
    """单个 (事件 × 窗口) 的日频执行结果。"""

    window_td: int
    exec_date: pd.Timestamp | None = None
    exec_price: float | None = None
    end_date: pd.Timestamp | None = None
    end_price: float | None = None
    gross_return: float = np.nan
    net_return: float = np.nan
    max_drawdown: float = np.nan
    max_favorable: float = np.nan
    max_adverse: float = np.nan
    actual_td: int = 0
    daily_available: bool = True
    is_immature: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "window_td": self.window_td,
            "exec_date": None if self.exec_date is None else str(self.exec_date.date()),
            "exec_price": self.exec_price,
            "end_date": None if self.end_date is None else str(self.end_date.date()),
            "end_price": self.end_price,
            "gross_return": self.gross_return,
            "net_return": self.net_return,
            "max_drawdown": self.max_drawdown,
            "max_favorable": self.max_favorable,
            "max_adverse": self.max_adverse,
            "actual_td": self.actual_td,
            "daily_available": self.daily_available,
            "is_immature": self.is_immature,
            "note": self.note,
        }


def daily_forward(
    daily: pd.Series,
    calendar: pd.DatetimeIndex,
    signal_date: pd.Timestamp,
    window_td: int,
    *,
    cost_rate: float = 0.0,
) -> DailyOutcome:
    """从「信号日之后第一个交易日」出发，计算 ``window_td`` 个交易日的收益。

    Args:
        daily: 日频收盘价序列（升序 DatetimeIndex）。
        calendar: 交易日历。
        signal_date: 信号确认日（月频观测日）。
        window_td: 精确交易日持有期。
        cost_rate: 单次买入成本率。

    Returns:
        :class:`DailyOutcome`。日频未覆盖 → ``daily_available=False``；
        持有期末端超出数据 → ``is_immature=True``。
    """
    if daily.empty:
        return DailyOutcome(window_td=window_td, daily_available=False, note="日频序列为空")

    exec_date = next_trading_day(calendar, signal_date)
    if exec_date is None:
        return DailyOutcome(
            window_td=window_td, daily_available=False, note="交易日历无后续交易日"
        )

    if exec_date < daily.index[0]:
        return DailyOutcome(
            window_td=window_td,
            exec_date=exec_date,
            daily_available=False,
            note=f"日频数据自 {daily.index[0].date()} 起，未覆盖该执行日",
        )

    # 执行日必须真实存在于日频序列（不插值）
    if exec_date not in daily.index:
        real = _price_at_or_after(daily, exec_date)
        if real is None:
            return DailyOutcome(
                window_td=window_td,
                exec_date=exec_date,
                daily_available=False,
                note="执行日在日频序列中无对应收盘价",
            )
        gap_days = (real - exec_date).days
        if gap_days > MAX_EXEC_GAP_DAYS:
            return DailyOutcome(
                window_td=window_td,
                exec_date=exec_date,
                daily_available=False,
                note=(
                    f"执行日 {exec_date.date()} 距最近可用日频观测 {real.date()} "
                    f"相隔 {gap_days} 天，视为数据缺失（不跨空洞顺延）"
                ),
            )
        note = f"执行日 {exec_date.date()} 无收盘价，顺延至 {real.date()}（间隔 {gap_days} 天）"
        exec_date = real
    else:
        note = ""

    exec_pos = int(daily.index.get_loc(exec_date))
    end_pos = exec_pos + int(window_td)
    out = DailyOutcome(
        window_td=window_td,
        exec_date=exec_date,
        exec_price=float(daily.iloc[exec_pos]),
        note=note,
    )

    # 先做空洞检查（必须在「未成熟」判断之前）：
    # 序列中间有多年空洞时，更准确的诊断是「数据缺失」而非「持有期未走完」。
    probe_end = min(end_pos, len(daily) - 1)
    probe_index = daily.index[exec_pos : probe_end + 1]
    if len(probe_index) > 1:
        gaps = np.diff(probe_index.values).astype("timedelta64[D]").astype(int)
        worst = int(gaps.max())
        if worst > MAX_HOLD_GAP_DAYS:
            hole_at = int(gaps.argmax())
            out.daily_available = False
            out.note = (note + "；" if note else "") + (
                f"持有期内存在 {worst} 天数据空洞（{probe_index[hole_at].date()} "
                f"→ {probe_index[hole_at + 1].date()}），拒绝计算"
            )
            out.actual_td = int(probe_end - exec_pos)
            return out

    if end_pos >= len(daily):
        # 持有期未走完：区分「时间未到」与「数据缺失」
        remaining = len(daily) - 1 - exec_pos
        out.is_immature = True
        out.actual_td = remaining
        out.note = (note + "；" if note else "") + (
            f"持有期未走完，仅有 {remaining} 个交易日数据（需 {window_td}）"
        )
        return out

    seg = daily.iloc[exec_pos : end_pos + 1].to_numpy(dtype="float64")
    p0, p1 = float(seg[0]), float(seg[-1])
    stats = _path_stats(seg)
    gross = p1 / p0 - 1.0
    out.end_date = pd.Timestamp(daily.index[end_pos])
    out.end_price = p1
    out.gross_return = gross
    out.net_return = (1.0 + gross) * (1.0 - cost_rate) - 1.0
    out.max_drawdown = stats["max_drawdown"]
    out.max_favorable = stats["max_favorable"]
    out.max_adverse = stats["max_adverse"]
    out.actual_td = int(window_td)
    return out


# --------------------------------------------------------------------------- #
# 事件层
# --------------------------------------------------------------------------- #
@dataclass
class DailyCheck:
    """一个事件的日频执行核验汇总。"""

    event_id: int
    signal_date: pd.Timestamp
    last_signal_date: pd.Timestamp | None
    exec_date: pd.Timestamp | None
    exec_price: float | None
    daily_available: bool = True
    outcomes: dict[int, DailyOutcome] = field(default_factory=dict)
    monthly_exec_date: pd.Timestamp | None = None
    monthly_exec_price: float | None = None

    def exec_gap_pct(self) -> float | None:
        """日频执行价相对月频执行价的偏移（正数 = 月频买贵了）。"""
        if self.exec_price is None or self.monthly_exec_price is None:
            return None
        if self.monthly_exec_price <= 0:
            return None
        return self.monthly_exec_price / self.exec_price - 1.0

    def to_row(self) -> dict:
        row = {
            "event_id": self.event_id,
            "signal_date": str(pd.Timestamp(self.signal_date).date()),
            "last_signal_date": (
                None if self.last_signal_date is None
                else str(pd.Timestamp(self.last_signal_date).date())
            ),
            "exec_date_daily": None if self.exec_date is None else str(self.exec_date.date()),
            "exec_price_daily": self.exec_price,
            "exec_date_monthly": (
                None if self.monthly_exec_date is None
                else str(pd.Timestamp(self.monthly_exec_date).date())
            ),
            "exec_price_monthly": self.monthly_exec_price,
            "exec_gap_pct": self.exec_gap_pct(),
            "daily_available": self.daily_available,
        }
        for td, o in self.outcomes.items():
            row[f"ret_{td}td"] = o.net_return
            row[f"dd_{td}td"] = o.max_drawdown
            row[f"mfe_{td}td"] = o.max_favorable
            row[f"mae_{td}td"] = o.max_adverse
            row[f"immature_{td}td"] = bool(o.is_immature or not o.daily_available)
            row[f"end_{td}td"] = None if o.end_date is None else str(o.end_date.date())
        return row


def check_events_daily(
    monthly_events: pd.DataFrame,
    daily: pd.Series,
    calendar: pd.DatetimeIndex,
    *,
    windows: tuple[int, ...] = DAILY_WINDOWS,
    cost_rate: float = 0.0,
) -> pd.DataFrame:
    """对月频事件表逐事件做日频执行核验。

    Args:
        monthly_events: :func:`experiment.compute_event_outcomes` 的输出，
            需含 ``event_id`` / ``signal_date`` / ``last_signal_date`` / ``exec_date``。
        daily: 日频收盘价。
        calendar: 交易日历。
        windows: 精确交易日窗口。
        cost_rate: 单次买入成本率。

    Returns:
        逐事件 DataFrame，含日频与月频执行价、偏移比例、各窗口收益与回撤。
    """
    if monthly_events is None or len(monthly_events) == 0:
        return pd.DataFrame()

    # 月频执行价：从月频价格列无法直接取得，改由调用方传入的事件表携带 exec_price 时使用
    prices_col = "exec_price" if "exec_price" in monthly_events.columns else None
    rows = []
    for _, ev in monthly_events.iterrows():
        sig = pd.to_datetime(ev["signal_date"])
        last_sig = pd.to_datetime(ev.get("last_signal_date")) if "last_signal_date" in ev else None
        m_exec = pd.to_datetime(ev["exec_date"]) if pd.notna(ev.get("exec_date")) else None
        m_price = float(ev[prices_col]) if prices_col and pd.notna(ev.get(prices_col)) else None

        chk = DailyCheck(
            event_id=int(ev.get("event_id", len(rows) + 1)),
            signal_date=sig,
            last_signal_date=last_sig,
            exec_date=None,
            exec_price=None,
            monthly_exec_date=m_exec,
            monthly_exec_price=m_price,
        )
        for td in windows:
            out = daily_forward(daily, calendar, sig, td, cost_rate=cost_rate)
            chk.outcomes[td] = out
        first = chk.outcomes[windows[0]]
        chk.exec_date = first.exec_date
        chk.exec_price = first.exec_price
        chk.daily_available = first.daily_available
        rows.append(chk.to_row())
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 基准层（同一收益口径）
# --------------------------------------------------------------------------- #
def build_baseline_daily(
    monthly_frame: pd.DataFrame,
    daily: pd.Series,
    calendar: pd.DatetimeIndex,
    window_td: int,
    *,
    cost_rate: float = 0.0,
) -> pd.DataFrame:
    """**同一日频收益口径**下的无条件基准。

    观测起点沿用月频框架的观测日集合（与信号触发频率匹配），
    但持有期与收益一律用日频精确交易日计算 —— 这样信号组与基准组的
    **唯一差异是入场时点集合**，不再是收益计量口径。

    Returns:
        含 ``obs_date`` / ``exec_date`` / ``net_return`` / ``max_drawdown`` 的 DataFrame。
    """
    rows = []
    for obs in monthly_frame.index:
        out = daily_forward(daily, calendar, pd.Timestamp(obs), window_td, cost_rate=cost_rate)
        if not out.daily_available or out.is_immature or np.isnan(out.net_return):
            continue
        rows.append(
            {
                "obs_date": pd.Timestamp(obs),
                "exec_date": out.exec_date,
                "net_return": out.net_return,
                "gross_return": out.gross_return,
                "max_drawdown": out.max_drawdown,
            }
        )
    return pd.DataFrame(rows)


def compare_daily(
    events_daily: pd.DataFrame,
    baseline_daily: pd.DataFrame,
    window_td: int,
    *,
    is_end_date: str | pd.Timestamp | None = None,
) -> dict:
    """在日频统一口径下比较信号组与基准组。"""
    col = f"ret_{window_td}td"
    imm = f"immature_{window_td}td"
    if events_daily is None or len(events_daily) == 0 or col not in events_daily.columns:
        return {"window_td": window_td, "n_signals": 0, "note": "无可用事件"}

    ev = events_daily[events_daily["daily_available"]].copy()
    ev = ev[ev[col].notna()]
    if imm in ev.columns:
        ev = ev[~ev[imm].astype(bool)]
    ev["exec_dt"] = pd.to_datetime(ev["exec_date_daily"], errors="coerce")

    def _seg(sub: pd.DataFrame) -> dict:
        if sub.empty:
            return {"n": 0, "mean": np.nan, "median": np.nan, "pct_pos": np.nan,
                    "worst": np.nan, "best": np.nan, "mean_dd": np.nan}
        v = sub[col].astype("float64")
        dd = sub[f"dd_{window_td}td"].astype("float64")
        return {
            "n": int(len(sub)),
            "mean": float(v.mean()),
            "median": float(v.median()),
            "pct_pos": float((v > 0).mean()),
            "worst": float(v.min()),
            "best": float(v.max()),
            "mean_dd": float(dd.min()) if dd.notna().any() else np.nan,
        }

    base = baseline_daily.copy()
    result: dict = {"window_td": window_td, "full": _seg(ev)}

    if is_end_date is not None:
        cut = pd.Timestamp(is_end_date)
        is_ev = ev[ev["exec_dt"] <= cut]
        oos_ev = ev[ev["exec_dt"] > cut]
        is_base = base[base["exec_date"] <= cut]
        oos_base = base[base["exec_date"] > cut]
        for name, sub, bsub in (("is", is_ev, is_base), ("oos", oos_ev, oos_base)):
            seg = _seg(sub)
            bs = bsub["net_return"].astype("float64") if len(bsub) else pd.Series(dtype="float64")
            seg["baseline_n"] = int(len(bsub))
            seg["baseline_mean"] = float(bs.mean()) if len(bs) else np.nan
            seg["baseline_median"] = float(bs.median()) if len(bs) else np.nan
            seg["baseline_pct_pos"] = float((bs > 0).mean()) if len(bs) else np.nan
            seg["mean_difference"] = (
                seg["mean"] - seg["baseline_mean"] if len(bs) else np.nan
            )
            result[name] = seg

    allb = base["net_return"].astype("float64")
    result["baseline_full"] = {
        "n": int(len(base)),
        "mean": float(allb.mean()) if len(allb) else np.nan,
        "median": float(allb.median()) if len(allb) else np.nan,
        "pct_pos": float((allb > 0).mean()) if len(allb) else np.nan,
    }
    result["n_signals"] = result["full"]["n"]
    return result


def split_segments(
    daily: pd.Series, *, max_gap_days: int = MAX_HOLD_GAP_DAYS
) -> list[pd.Series]:
    """按数据空洞把日频序列切成连续段。

    多源合并（如「近 5 年全量 + 早期点采」）会产生跨越数年的空洞。
    任何跨空洞的收益率、波动率、年化计算都会失真，必须先切段。
    """
    if daily.empty:
        return []
    idx = daily.index
    gaps = np.diff(idx.values).astype("timedelta64[D]").astype(int)
    cuts = np.flatnonzero(gaps > max_gap_days)
    segments: list[pd.Series] = []
    start = 0
    for c in list(cuts) + [len(idx) - 1]:
        seg = daily.iloc[start : c + 1]
        if len(seg):
            segments.append(seg)
        start = c + 1
    return segments


def buy_and_hold_daily(
    daily: pd.Series,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> dict:
    """日频买入持有统计。

    **若有数据空洞，按连续段分别计算，不给出跨段合并的年化**——
    跨段的「年化收益」与「年化波动」在数学上没有意义。
    """
    seg = daily.loc[(daily.index >= pd.Timestamp(start)) & (daily.index <= pd.Timestamp(end))]
    if len(seg) < 2:
        return {"n_obs": int(len(seg)), "note": "区间内日频数据不足"}

    parts = split_segments(seg)
    details = []
    for part in parts:
        if len(part) < 2:
            continue
        p0, p1 = float(part.iloc[0]), float(part.iloc[-1])
        years = (part.index[-1] - part.index[0]).days / 365.25
        arr = part.to_numpy(dtype="float64")
        peak = np.maximum.accumulate(arr)
        dd = arr / peak - 1.0
        rets = part.pct_change().dropna()
        vol = float(rets.std() * np.sqrt(252)) if len(rets) > 1 else np.nan
        cagr = (p1 / p0) ** (1.0 / years) - 1.0 if years > 0 else np.nan
        details.append(
            {
                "start": str(part.index[0].date()),
                "end": str(part.index[-1].date()),
                "n_obs": int(len(part)),
                "years": float(years),
                "total_return": float(p1 / p0 - 1.0),
                "cagr": float(cagr) if not np.isnan(cagr) else np.nan,
                "max_drawdown": float(np.min(dd)),
                "ann_vol": vol,
                "sharpe": float(cagr / vol) if vol and vol > 0 and not np.isnan(cagr) else np.nan,
            }
        )

    if not details:
        return {"n_obs": int(len(seg)), "note": "区间内日频数据不足"}

    if len(details) == 1:
        return details[0]

    return {
        "segments": details,
        "n_segments": len(details),
        "note": (
            "区间内存在数据空洞，已按连续段分别计算；"
            "**不提供跨段合并的年化收益与波动率**，因为跨段年化无统计意义"
        ),
    }
