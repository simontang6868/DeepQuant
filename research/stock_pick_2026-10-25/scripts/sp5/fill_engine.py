# -*- coding: utf-8 -*-
"""SP-5 / P0-3 撮合引擎：涨跌停 / 停牌 / 未成交

设计约束（来自 `05_SP5_任务书.md` P0-3 卡与 `SP5_预登记.md` v1.1）
---------------------------------------------------------------
1. **限价档必须分板块分制度期**：主板 10%、创业板 2020-08-24 起 20%（此前 10%）、
   科创板 20%、北交所 30%、深市 ST 5%（创业板 ST 20%）。
2. **沪市 ST 无真值** → 不给点估计，给**双界**：base（10%）与 sh5（全沪市按 5% 判，失败率上界）。
3. **涨跌幅用前复权收益判定**（涨跌停本就基于除权后价格），一字板用不复权 high==low 判定。
4. **失败订单必须留状态**，不得按理论价成交、不得静默删除。
5. 无委托簿 → **排队/部分成交无法建模**，逐条写入 limitations，不用滑点冒充。

口径
----
* `FILLED` 正常成交
* `FAIL_LIMIT_UP` 一字涨停（买不进）
* `FAIL_LIMIT_DOWN` 一字跌停（卖不出）
* `HALT` 停牌（当日无成交记录或 volume==0）
* `NO_BAR` 无该日数据（未上市/已退市/停牌）
"""

from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
QFQ_DIR = os.path.join(ROOT, "data", "qfq_all")
EXEC_DIR = os.path.join(ROOT, "data", "exec_all")

CAL_BASE = ("000001.SZ", "600036.SH")
SH_PREFIX = ("600", "601", "603", "605", "688", "689")
SZ_PREFIX = ("000", "001", "002", "003", "300", "301", "302")
TOL = 0.002          # 0.2pp 容差（报价取整误差）
DEFER_MAX = 10       # 顺延搜索上限（交易日）


# ---------------------------------------------------------------- 日历与日期
def market_calendar() -> list[str]:
    ds: set[str] = set()
    for b in CAL_BASE:
        p = os.path.join(QFQ_DIR, f"{b}.qfq.csv")
        if os.path.exists(p):
            ds.update(
                pd.read_csv(p, usecols=["date"])["date"]
                .astype(str).str.replace("-", "", regex=False).tolist()
            )
    return sorted(ds)


def month_buy_date(me: str, cal_pos: dict[str, int], cal: list[str]) -> str | None:
    """决策日（月末）之后的第 1 个交易日 = 建仓日。"""
    i = cal_pos.get(me)
    if i is None:
        cand = [d for d in cal if d <= me]
        if not cand:
            return None
        i = cal_pos[cand[-1]]
    return cal[i + 1] if i + 1 < len(cal) else None


# ---------------------------------------------------------------- 限价档
def board_of(code: str) -> str:
    p = code[:3]
    if p in ("688", "689"):
        return "STAR"
    if p in ("300", "301"):
        return "GEM"
    if code[:1] in ("4", "8"):
        return "BSE"
    if p in SH_PREFIX:
        return "SH_MAIN"
    if p in SZ_PREFIX:
        return "SZ_MAIN"
    return "OTHER"


def limit_pct(code: str, date: str, is_st: bool = False, sh_mode: str = "base") -> float:
    """涨跌幅限制（小数）。date 为 'YYYYMMDD'。

    sh_mode: 'base' 沪市一律 10%（ST 未知，按非 ST 处理）；
             'sh5'  沪市一律 5%（**失败率上界**，用于双界，不是真值）。
    """
    b = board_of(code)
    if b == "STAR":
        return 0.20
    if b == "GEM":
        base = 0.20 if date >= "20200824" else 0.10
        return 0.20 if (is_st and date >= "20200824") else base
    if b == "BSE":
        return 0.30
    if b == "SH_MAIN":
        return 0.05 if sh_mode == "sh5" else 0.10
    if b == "SZ_MAIN":
        return 0.05 if is_st else 0.10
    return 0.10


# ---------------------------------------------------------------- 日线容器
class Bars:
    """单只股票的日线（不复权 high/low/volume + 前复权 close）。"""

    __slots__ = ("code", "d", "pos", "hi", "lo", "vol", "qc")

    def __init__(self, code: str, ex_path: str, q_path: str) -> None:
        self.code = code
        e = pd.read_csv(ex_path, usecols=["date", "high", "low", "volume"])
        q = pd.read_csv(q_path, usecols=["date", "close"])
        e["date"] = e["date"].astype(str).str.replace("-", "", regex=False)
        q["date"] = q["date"].astype(str).str.replace("-", "", regex=False)
        m = e.merge(q, on="date", how="inner").dropna(subset=["close"])
        self.d = m["date"].to_numpy()
        self.pos = {v: i for i, v in enumerate(self.d)}
        self.hi = m["high"].to_numpy(float)
        self.lo = m["low"].to_numpy(float)
        self.vol = m["volume"].to_numpy(float)
        self.qc = m["close"].to_numpy(float)

    def ret(self, i: int) -> float:
        if i <= 0:
            return np.nan
        if not np.isfinite(self.qc[i - 1]) or self.qc[i - 1] <= 0:
            return np.nan
        return float(self.qc[i] / self.qc[i - 1] - 1.0)

    def close_at(self, date: str) -> float:
        i = self.pos.get(date)
        return float(self.qc[i]) if i is not None else np.nan


_IDX: dict[str, tuple[str, str]] | None = None


def build_index() -> dict[str, tuple[str, str]]:
    """{code -> (exec_path, qfq_path)}。

    ⚠️ 面板里的 code 是 6 位无后缀（'000001'），文件名带交易所后缀
    （'000001.SZ.exec.csv'）——直接拼路径会全部读不到，且**静默退化为 100% 现金**。
    """
    global _IDX
    if _IDX is not None:
        return _IDX
    idx: dict[str, tuple[str, str]] = {}
    for p in glob.glob(os.path.join(EXEC_DIR, "*.exec.csv")):
        code = os.path.basename(p).split(".")[0]
        q = os.path.join(QFQ_DIR, code + "." + os.path.basename(p).split(".")[1] + ".qfq.csv")
        if os.path.exists(q):
            idx[code] = (p, q)
    _IDX = idx
    return idx


def load_bars(code: str) -> Bars | None:
    pair = build_index().get(code)
    if pair is None:
        return None
    try:
        return Bars(code, pair[0], pair[1])
    except Exception:
        return None


def classify(bar: Bars, date: str, is_st: bool, sh_mode: str = "base") -> tuple[str, float, float]:
    """原始盘面分类（不分买卖方向）。返回 (raw_status, limit_pct, ret)。"""
    i = bar.pos.get(date)
    if i is None:
        return "NO_BAR", np.nan, np.nan
    if not np.isfinite(bar.vol[i]) or bar.vol[i] <= 0:
        return "HALT", np.nan, np.nan
    lim = limit_pct(bar.code, date, is_st, sh_mode)
    r = bar.ret(i)
    if not np.isfinite(r):
        return "NO_BAR", lim, r
    if bar.hi[i] == bar.lo[i]:                      # 全天一个价 —— 一字
        if r >= lim - TOL:
            return "LOCK_UP", lim, r
        if r <= -(lim - TOL):
            return "LOCK_DOWN", lim, r
        return "HALT", lim, r                        # 单价无波动：视为停牌/无流动性
    return "FREE", lim, r


def judge(bar: Bars, date: str, is_st: bool, side: str,
          sh_mode: str = "base") -> tuple[str, float]:
    """按买卖方向判定能否成交。

    ⚠️ 方向不对称（容易写错的地方）：
      * **买**：一字涨停买不进；**一字跌停反而容易买**（卖方排队）；
      * **卖**：一字跌停卖不出；**一字涨停反而容易卖**（买方排队）。
    这个不对称若不建模，会把「跌停买不到」这种不存在的失败算进收益损耗。
    """
    raw, lim, _ = classify(bar, date, is_st, sh_mode)
    if raw == "FREE":
        return "FILLED", lim
    if raw == "LOCK_UP":
        return ("FAIL_LIMIT_UP" if side == "buy" else "FILLED"), lim
    if raw == "LOCK_DOWN":
        return ("FAIL_LIMIT_DOWN" if side == "sell" else "FILLED"), lim
    return raw, lim          # HALT / NO_BAR 两个方向都不成交


def deferred(bar: Bars, date: str, cal: list[str], cal_pos: dict[str, int],
             is_st: bool, side: str, sh_mode: str = "base") -> tuple[str | None, str]:
    """顺延搜索：找 date 之后、上限 DEFER_MAX 个交易日内第一个可成交日。"""
    i0 = cal_pos.get(date, 0)
    stop = cal[min(i0 + DEFER_MAX, len(cal) - 1)]
    for dd in bar.d:
        if dd <= date:
            continue
        if dd > stop:
            break
        st, _ = judge(bar, dd, is_st, side, sh_mode)
        if st == "FILLED":
            return dd, "DEFERRED"
    return None, "NO_DEFER"


# ---------------------------------------------------------------- 向量化状态位图
# 为什么需要：路径依赖策略里，实际持仓会偏离「决策持仓」（失败就会卡住），
# 于是 (股票, 月份) 组合无法预先枚举。改为**按股票一次性算全历史能否成交**，
# 查询用 searchsorted，避免"查表落空被当成不可成交"这类静默错误。
ST_FILLED, ST_LOCK_UP, ST_LOCK_DOWN, ST_HALT, ST_NO_BAR = 0, 1, 2, 3, 4


def status_tables(bar: "Bars", st_months: set[str] | None = None,
                  sh_mode: str = "base") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (dates, buy_ok, sell_ok) 三个等长数组。

    st_months: 该股处于 ST 的月份集合，元素形如 '202001'。
    """
    n = len(bar.d)
    lim = np.full(n, 0.10)
    b = board_of(bar.code)
    if b == "STAR":
        lim[:] = 0.20
    elif b == "GEM":
        lim[:] = np.where(bar.d >= "20200824", 0.20, 0.10)
    elif b == "BSE":
        lim[:] = 0.30
    elif b == "SH_MAIN":
        lim[:] = 0.05 if sh_mode == "sh5" else 0.10
    elif b == "SZ_MAIN" and st_months:
        is_st = np.isin(np.array([d[:6] for d in bar.d]), np.array(sorted(st_months)))
        lim = np.where(is_st, 0.05, 0.10)
    elif b == "GEM" and st_months:
        pass  # 创业板 ST 仍 20%，上面已处理

    r = np.full(n, np.nan)
    prev = bar.qc[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        r[1:] = np.where(prev > 0, bar.qc[1:] / prev - 1.0, np.nan)

    lot = bar.hi == bar.lo
    vol_ok = np.isfinite(bar.vol) & (bar.vol > 0) & np.isfinite(r)
    kind = np.full(n, ST_FILLED)                      # 默认可成交
    kind[lot & (r >= lim - TOL)] = ST_LOCK_UP
    kind[lot & (r <= -(lim - TOL))] = ST_LOCK_DOWN
    kind[lot & (r < lim - TOL) & (r > -(lim - TOL))] = ST_HALT
    kind[~vol_ok] = ST_HALT
    kind[~np.isfinite(r)] = ST_NO_BAR

    buy_ok = (kind == ST_FILLED) | (kind == ST_LOCK_DOWN)   # 跌停也能买
    sell_ok = (kind == ST_FILLED) | (kind == ST_LOCK_UP)    # 涨停也能卖
    return bar.d, buy_ok, sell_ok
