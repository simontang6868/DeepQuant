# -*- coding: utf-8 -*-
"""SP-5 / P1-3（续）：纸面前瞻的成交回填与逐日盯市

p13_forward.py 只记录了**决策**（14 字段台账：目标持仓、估计成本），
但真正的"前瞻收益"必须来自**次一交易日按实际收盘价成交、之后逐日盯市**。
本脚本补上这一环，使 P1-3 端到端闭环：

    决策台账(月末)  →  次一交易日成交(实际收盘)  →  逐日 NAV 直到下个决策/数据末

设计严守（与 p04_position_dd.simulate 同口径，保证和回测审计可比）
---------------------------------------------------------------------------
* 整数手（100 股），余数留现金；
* 现金按 3%/年逐日计息（244 交易日）；
* 费用逐笔：佣金 max(5元, 0.025%) + 过户 0.001% 双边 + 印花 0.05% 仅卖出 + 价差 0.10%；
* 成交受涨跌停/停牌约束（沿用 fill_engine 位图，买方向按"跌停也能买"）；
* 买不进则顺延搜索（DEFER_MAX 个交易日内首个可成交日）。

⚠️ 不接真实下单：成交价来自**历史日线实际收盘**，对"决策日 > 今天"的 FORWARD 记录，
   次一交易日当时尚未发生，故必须等该日收盘后数据到位才能回填——
   这正是前瞻"不窥未来"的体现：决策当下不知成交与否、不知价格。

输出（不改动原始决策台账，保持其 ledger_sha256 不可变）
-------------------------------------------------------
`forward/nav/<YYYYMMDD>_track.json` —— 成交明细 + NAV 摘要
`forward/nav/<YYYYMMDD>_nav.csv`   —— 逐日 (date, nav_top5, nav_rand5, stock_value, cash, dd)

用法
----
    python scripts/sp5/p13b_forward_track.py --decision forward/ledger/20260930.json
    python scripts/sp5/p13b_forward_track.py --decision ... --end 20261130
    python scripts/sp5/p13b_forward_track.py --decision ... --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_engine as fe  # noqa: E402

BASE = Path(__file__).resolve().parents[2]
FWD = BASE / "forward"
NAV_DIR = FWD / "nav"

CAPITAL = 100_000.0
LOT = 100
CASH_ANN = 0.03
COMM_RATE, COMM_MIN = 0.00025, 5.0
TRANSFER, STAMP, SPREAD = 1e-05, 0.0005, 0.001
WEIGHT = 0.25
K = 5


# ---------------------------------------------------------------- 纯函数（可测）
def execution_day(me: str, cal: list[str], cal_pos: dict[str, int]) -> str | None:
    """决策日 me 之后的第 1 个交易日 = 建仓日。"""
    return fe.month_buy_date(me, cal_pos, cal)


def fill_one(code: str, exe_d: str, loader, is_st: bool, cal: list[str],
             cal_pos: dict[str, int]) -> tuple[str | None, float, str]:
    """返回 (成交日, 成交价, 状态)。买不进顺延；全失败返回 (None, nan, 'DEFER_FAIL')。"""
    bar = loader(code)
    if bar is None:
        return None, np.nan, "NO_BAR"
    st, _ = fe.judge(bar, exe_d, is_st, "buy")
    if st == "FILLED":
        return exe_d, float(bar.close_at(exe_d)), "FILLED"
    dd, stat = fe.deferred(bar, exe_d, cal, cal_pos, is_st, "buy")
    if dd is None:
        return None, np.nan, "DEFER_FAIL"
    return dd, float(bar.close_at(dd)), stat


def shares_for(notional: float, price: float, lot: int = LOT) -> int:
    """按目标市值折算整数手。"""
    if not (np.isfinite(price) and price > 0 and np.isfinite(notional) and notional > 0):
        return 0
    return int(notional // (price * lot)) * lot


def buy_cost(shares: int, price: float) -> float:
    """买入一笔的真实费用（佣金 + 过户 + 半边价差；买入无印花）。"""
    if shares <= 0 or not np.isfinite(price) or price <= 0:
        return 0.0
    notional = shares * price
    comm = max(COMM_MIN, notional * COMM_RATE)
    return comm + notional * TRANSFER + notional * SPREAD * 0.5


def track_nav(holds: list[str], fills: dict[str, tuple[str, float]], exe_d: str,
              end_d: str, loader, capital: float = CAPITAL, weight: float = WEIGHT,
              lot: int = LOT, cash_ann: float = CASH_ANN,
              cal: list[str] | None = None, cal_pos: dict[str, int] | None = None) -> pd.DataFrame:
    """从建仓日 exe_d 逐日盯市到 end_d，返回每日 (date, stock_value, cash, nav, dd)。"""
    if cal is None or cal_pos is None:
        raise ValueError("必须提供 cal / cal_pos")
    i0, i1 = cal_pos[exe_d], cal_pos[end_d]
    # 缓存 bars，避免每日重读文件
    cache: dict[str, object] = {}
    def close_of(code: str, day: str) -> float:
        b = cache.get(code)
        if b is None:
            b = loader(code)
            cache[code] = b
        if b is None:
            return np.nan
        return float(b.close_at(day))

    shares: dict[str, int] = {}
    cash = float(capital)
    cost_cum = 0.0
    r = cash_ann / 244.0
    rows = []
    for k in range(i0, i1 + 1):
        day = cal[k]
        if k > i0:
            cash *= (1.0 + r)            # 现金逐日计息
        # 当日有成交则建仓/加仓
        for c in holds:
            fd, fp = fills.get(c, (None, np.nan))
            if fd == day and c not in shares and np.isfinite(fp) and fp > 0:
                tgt = capital * weight / max(1, len(holds))
                sh = shares_for(tgt, fp, lot)
                if sh > 0:
                    cost = sh * fp + buy_cost(sh, fp)
                    if cash >= cost:
                        cash -= cost
                        cost_cum += buy_cost(sh, fp)
                        shares[c] = sh
        sv = sum(shares[c] * close_of(c, day) for c in shares) if shares else 0.0
        nav = sv + cash
        rows.append((day, float(sv), float(cash), float(nav), float(cost_cum)))
    df = pd.DataFrame(rows, columns=["date", "stock_value", "cash", "nav", "cost_cum"])
    df["dd"] = df["nav"] / df["nav"].cummax() - 1.0
    return df


def nav_metrics(df: pd.DataFrame) -> dict:
    nav = df["nav"].to_numpy(float)
    nav = nav / nav[0]
    n = len(nav) - 1
    dd = df["dd"].to_numpy(float)
    rr = nav[1:] / nav[:-1] - 1
    return {
        "days": int(n),
        "ann": float(nav[-1] ** (244.0 / n) - 1) if n > 0 else None,
        "mdd": float(dd.min()),
        "final": float(nav[-1]),
        "vol": float(np.std(rr, ddof=1) * np.sqrt(244)) if n > 1 else None,
        "calmar": float((nav[-1] ** (244.0 / n) - 1) / abs(dd.min())) if (n > 0 and dd.min() < 0) else None,
    }


# ---------------------------------------------------------------- 数据装载
def load_st_months() -> dict[str, set[str]]:
    """深市 ST 月份（沪市无真值，按非 ST 处理）。"""
    p = BASE / "data" / "st_sz_monthly.parquet"
    if not p.exists():
        return {}
    st = pd.read_parquet(p)
    st["date"] = pd.to_datetime(st["date"]).dt.strftime("%Y%m%d")
    out: dict[str, set[str]] = {}
    for c, d, v in zip(st["code"], st["date"], st["is_st"]):
        if v:
            out.setdefault(str(c), set()).add(str(d)[:6])
    return out


def latest_data_date() -> str:
    """取基准股并集日历的最后一个交易日（数据末端）。"""
    cal = fe.market_calendar()
    return cal[-1] if cal else ""


# ---------------------------------------------------------------- 主流程
def run(decision_path: str, end_d: str | None = None, selftest: bool | None = None) -> int:
    """回填并盯市。

    ``selftest`` 语义（2026-11-02 修正）：
      * ``None``（默认）→ **从台账 ``counts_toward_observation`` 推导**，
        即 ``selftest = not counts_toward_observation``；
      * ``True/False``（显式 ``--selftest`` / ``--forward``）→ 作为**断言**校验，
        与台账不一致即报错退出，而不是静默覆盖。

    为什么这样改：原实现把 ``--selftest`` 当独立开关，**只 print 台账的
    ``counts_toward_observation`` 却从不使用它**。实测后果——台账
    `20260930.json` 写 `counts_toward_observation=false`，而
    `nav/20260930_track.json` 写 `selftest=false`，两个文件互相矛盾。
    这正是 `test_T7_selftest_excluded_by_enforcement` 要钉的缺陷。
    """
    dec = json.loads(Path(decision_path).read_text(encoding="utf-8"))
    me = dec["date"]
    if "counts_toward_observation" not in dec:
        print("[abort] 台账缺 counts_toward_observation，无法判定是否计入观察期")
        return 6
    # 显式三态：True / False / 其他（字符串 "True" 之类一律拒绝）。
    # 不写成 `not bool(x)` —— "false" 这样的字符串会被当成 True，静默反转语义。
    if dec["counts_toward_observation"] is False:
        derived, ledger_counts = True, False
    elif dec["counts_toward_observation"] is True:
        derived, ledger_counts = False, True
    else:
        print(f"[abort] counts_toward_observation 不是布尔："
              f"{dec['counts_toward_observation']!r}")
        return 6
    if selftest is not None and bool(selftest) != derived:
        print(f"[abort] 显式声明 selftest={bool(selftest)} 与台账 "
              f"counts_toward_observation={ledger_counts} 矛盾")
        print("        台账是唯一真源；如确需覆盖，请先修台账并记更正，不要用命令行绕过。")
        return 6
    selftest = derived
    NAV_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[decision] {me}｜mode={dec.get('mode')}｜counts={ledger_counts}｜selftest={selftest}")

    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    exe_d = execution_day(me, cal, cal_pos)
    if exe_d is None or exe_d not in cal_pos:
        print("[abort] 找不到建仓日（数据可能未更新到次月）")
        return 2
    end_d = end_d or latest_data_date()
    if end_d not in cal_pos or cal_pos[end_d] < cal_pos[exe_d]:
        print(f"[abort] end_d={end_d} 早于建仓日 {exe_d} 或不在日历（数据未到位）")
        return 2
    print(f"[exec] 建仓日 {exe_d}｜跟踪至 {end_d}｜跨度 {cal_pos[end_d]-cal_pos[exe_d]+1} 交易日")

    st_months = load_st_months()
    sets = {"top5": dec.get("target_holds", []),
            "rand5": dec.get("target_holds_rand5", [])}
    out_nav = {lab: None for lab in sets}
    fills_all = {}
    summary = {"decision": me, "execution_day": exe_d, "end_day": end_d,
               "selftest": selftest, "sets": {}}
    for lab, holds in sets.items():
        if not holds:
            continue
        fills = {}
        for c in holds:
            is_st = (exe_d[:6] in st_months.get(c, set()))
            fd, fp, stt = fill_one(c, exe_d, fe.load_bars, is_st, cal, cal_pos)
            fills[c] = (fd, fp)
            fills_all[f"{lab}:{c}"] = {"fill_d": fd, "fill_p": (None if not np.isfinite(fp) else fp), "status": stt}
        df = track_nav(holds, fills, exe_d, end_d, fe.load_bars, cal=cal, cal_pos=cal_pos)
        out_nav[lab] = df
        m = nav_metrics(df)
        summary["sets"][lab] = {"n_hold": len(holds), "metrics": m, "fills": fills_all}
        print(f"  [{lab}] 成交 {sum(1 for c in holds if np.isfinite(fills[c][1]))}/{len(holds)}"
              f"｜年化 {m['ann']*100:+.2f}%｜MDD {m['mdd']*100:+.2f}%｜终值 {m['final']*100:.2f}%")

    # 合并 CSV（双口径）
    if out_nav["top5"] is not None:
        merged = out_nav["top5"][["date"]].copy()
        for lab, df in out_nav.items():
            if df is not None:
                merged[f"nav_{lab}"] = df["nav"].to_numpy()
                merged[f"dd_{lab}"] = df["dd"].to_numpy()
        merged.to_csv(NAV_DIR / f"{me}_nav.csv", index=False, encoding="utf-8-sig")
    track = {
        "decision": me, "execution_day": exe_d, "end_day": end_d,
        "selftest": selftest,
        "counts_toward_observation": bool(ledger_counts),
        "selftest_source": "ledger.counts_toward_observation",
        "decision_mode": dec.get("mode"),
        "decision_late_run": bool(dec.get("late_run", False)),
        "capital": CAPITAL, "weight": WEIGHT,
        "fills": {k: v for k, v in fills_all.items()},
        "summary": {lab: summary["sets"][lab]["metrics"] for lab in sets if lab in summary["sets"]},
        "note": "成交价取自历史实际收盘；FORWARD 记录须待建仓日收盘后数据到位方可回填",
    }
    (NAV_DIR / f"{me}_track.json").write_text(
        json.dumps(track, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[saved] {NAV_DIR / me}_nav.csv ｜ {NAV_DIR / me}_track.json")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decision", required=True, help="决策台账 JSON 路径")
    ap.add_argument("--end", default=None, help="跟踪终点 YYYYMMDD（默认数据末日）")
    ap.add_argument("--selftest", action="store_true",
                    help="断言这是自检（必须与台账一致，不一致则报错）")
    ap.add_argument("--forward", action="store_true",
                    help="断言这计入观察期（必须与台账一致，不一致则报错）")
    a = ap.parse_args()
    if a.selftest and a.forward:
        print("[abort] --selftest 与 --forward 互斥")
        return 6
    declared = True if a.selftest else (False if a.forward else None)
    return run(a.decision, a.end, declared)


if __name__ == "__main__":
    raise SystemExit(main())
