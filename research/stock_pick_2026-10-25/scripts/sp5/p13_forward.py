# -*- coding: utf-8 -*-
"""SP-5 / P1-3：纸面前瞻记录（不接真实下单）

为什么不复用 panel_monthly.parquet
--------------------------------
该面板带 `fwd20`（未来 20 交易日收益），构建时要求 `sell < len(dates)`，
**尾部 1~2 个月会被剔除**（20260930 的行因为未来 20 日数据还没到而不存在）。
前瞻的本质是「当下不知道未来」，所以必须用**无标签截面**重新算：
只取截至决策日 T 的特征，`fwd20` 一律不算、不用、不看。

与回测的口径差异（必须登记，否则前瞻与回测不可比）
------------------------------------------------
1. **池筛选不用 `fwd20.notna()`**。回测里这条件隐含「只买后来有数据的股票」
   （轻微幸存者偏差），前瞻不知道谁会停牌退市，故池子会略大；
2. ⚠️ **entry_rule 的口径未经验证**。`forward/sp4_config_v1.json` 写的是「池内 amt20 降序取前 5」，
   并声称「审计已证买入规则对期望无贡献」。但 `p13_entry_rule_check.py` 实测：
   2023-01 起 44 月的 `top5 − rand5` = **+0.221pp/月（t=0.28）**，95% CI **[−1.33, +1.77]pp**，
   MDE **2.22pp/月** ⇒ 判定 `ENTRY_RULE_CANNOT_VERIFY`——**既不能证等价，也不能证有偏**。
   故本脚本**同时记录两套目标**（top5 与固定种子的 rand5），避免前瞻只测一个未经验证的变体。
3. ⚠️ **冻结配置的 `price_filter [1.0, 50.0]` 与回测实现 `between(1.0, 200.0)` 不一致**。
   `p13c_rule_diff_check.json` 实测 4 个月末：pool 差 72~125 只、**top5 重合 0/5**
   ⇒ 影响**不小**（旧版此处曾写「只差 6~7 只，<1%」，与产物矛盾，2026-11-02 已更正）。
   本脚本**主口径用 1-200（回测实际跑的）**，同时把 1-50 口径的池和目标写进对照列。
   该缺陷已登记待更正配置（Phase 1.2 走 amendment）。

输出
----
`forward/ledger/YYYYMMDD.json` —— 单条记录
`forward/ledger/YYYYMMDD_targets.csv` —— 目标持仓明细（含因子值，便于事后核对）
`forward/observations.csv` —— 追加一行汇总（保持与既有文件同列）
`forward/heartbeat.json` —— 每次运行写心跳（供漏跑检测）
`forward/corrections.jsonl` —— 追加式更正日志（覆盖、补跑等）

用法
----
    python scripts/sp5/p13_forward.py                    # 最新已完结月末
    python scripts/sp5/p13_forward.py --asof 20260930    # 指定（自检）
    python scripts/sp5/p13_forward.py --selftest         # 标记为自检，不计入观察期
    python scripts/sp5/p13_forward.py --late-run         # 补跑（决策日 < 今天）
    python scripts/sp5/p13_forward.py --dry-run          # 只算不写（校验用）
    python scripts/sp5/p13_forward.py --force            # 覆盖已存在台账（先备份）
    python scripts/sp5/p13_forward.py --verify           # 只读自检，不生成决策

模式（`mode`）与观察期
--------------------
- `FORWARD`   ：决策日 == 今天（当日收盘后生成）         → 计入观察期
- `LATE_RUN`  ：决策日 < 今天（漏跑补做，只用决策日数据） → **计入观察期**，但必须显式声明
- `SELFTEST`  ：`--selftest`，管道自检                    → **不计入**观察期

补跑为什么也能计入：信号只用 `build_snapshot(me)`（数据截断到 me），
不看 me 之后的任何价格；`late_run` 与 `late_trading_days` 会写进台账，
事后可分辨哪条是当日跑的、哪条是补的。
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_panel as bp  # noqa: E402
import fill_engine as fe  # noqa: E402
import trade_calendar as tcal  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parents[2]
FWD = BASE / "forward"
LEDGER = FWD / "ledger"
HB = FWD / "heartbeat.json"
CORRECTIONS = FWD / "corrections.jsonl"
# 配置的**唯一**位置。原写法是
#     KF = BASE/"sp5_out"/... if 存在 else FWD/...
# 那个 `if exists` 是个静默切换：哪天有人在 sp5_out/ 放一份配置（跑某个脚本就会生成），
# 前瞻会悄悄改用那份，而 `--verify` 校验的仍是 forward/sp4_config_v1.sha256 ⇒ 校验通过、
# 用的却是另一份配置。现在只认一处，出现重复真源直接报错。
KF = FWD / "sp4_config_v1.json"
_DUP_KF = BASE / "sp5_out" / "sp4_config_v1.json"
if _DUP_KF.exists():
    raise RuntimeError(
        f"发现重复的配置真源：{_DUP_KF}。策略参数只允许在 {KF} 一处定义。"
        "若是某个脚本生成的副本，请删除或改为只读引用，不要留着两份。"
    )

# ---------------------------------------------------------------------------
# 策略参数：**只从 `strategy_params` 取**，本文件不再硬编码任何阈值。
# 任务书 Phase 1.1 要求「策略参数只在一处定义，回测/前瞻/实盘都从它读取」。
# `tests/test_strategy_params.py` 会用 AST 扫描全部脚本，发现硬编码即失败。
# ---------------------------------------------------------------------------
import strategy_params as sp  # noqa: E402

_P = sp.load_params()          # 配置缺失或格式不符 ⇒ 直接抛 ConfigError，不静默回退

K = _P.n_hold
THRESH, LIQ_MAX = _P.thresh, _P.liq_max
AMT_MIN = _P.amt_min

#: 配置口径的价格上限（sp4-v1 写的是 50）。
CLOSE_HI_CFG = _P.price_hi
#: 回测实际口径 200。**这是已登记的偏差** `CONFIG_PRICE_FILTER_MISMATCH`，
#: 不写死在这里、而取自 KNOWN_DIVERGENCES，是为了让「它是个已知问题」在代码里可见。
#: Phase 1.2 的 amendment 才会处理它；本阶段两边并列报告，不择优。
CLOSE_HI_MAIN = float(sp.KNOWN_DIVERGENCES["price_hi"]["code_value"])

#: 运行资金，**不是策略参数**（策略参数只描述规则；资金量属 D1 决策）。
CAPITAL = 100_000.0
COMM_RATE, COMM_MIN, TRANSFER, STAMP, SPREAD = (
    _P.commission_rate, _P.min_commission, _P.transfer_fee,
    _P.stamp_duty_sell, _P.spread_scenario,
)

#: 数据末日至今天允许的最大自然日间隔。超过即判「数据过期」并拒绝生成决策。
#: 取值 7 覆盖「周五月末 → 次周一」（3 天）与最长的十一/春节长假尾部；
#: 宁可拒绝，也不要用半月前的数据冒充月末信号。
MAX_DECISION_AGE_DAYS = 7

#: 补跑容忍的最大自然日间隔。取 3 = 「周五月末 → 次周一」的周末跨度。
#: 超过这个跨度，信号对应的下一期收益已经部分可见，补跑就变成回溯。
MAX_LATE_RUN_DAYS = 3


class DataStaleError(RuntimeError):
    """数据过期或该月未结束，拒绝生成决策。"""


class DecisionWindowMissed(DataStaleError):
    """决策窗口已过（距月末太远）。

    与 ``DataStaleError``（数据过期）分开：这种情况下**数据可能是最新的**，
    过期的是"时机"。混成一个错误会让人去查数据源，而该改的是调度。
    """


class MonthEndUnverified(DataStaleError):
    """数据末日不是该月最后一个交易日。

    **不能降级为警告**：月度信号的全部含义都建立在「这是月末」之上。若数据只更新到
    月末前一天，返回那一天会生成一个**日期错误但格式完全正常**的决策记录，而事后
    从台账上看不出任何异常（``decision`` 字段合法、账单齐全）。
    """


def _rel(p: pathlib.Path) -> str:
    """相对 BASE 的路径；不在 BASE 下时退回绝对路径（**不要**因此崩溃）。

    背景：配置/台账可能被指到仓库外（测试、迁移），`relative_to` 会抛
    ValueError 把整条流程打断。路径只是给人读的元数据，不该成为失败点。
    """
    try:
        return str(p.relative_to(BASE)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _today_str() -> str:
    return time.strftime("%Y%m%d")


def _parse(d: str) -> dt.date:
    return dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))


def _tail_date(path: str) -> str:
    """只读文件**最后一行**的首字段（日期），不解整份 CSV。

    为什么不是 ``pd.read_csv``：全池 5,444 个文件读一遍约 4 分钟（原注释里的数字），
    而尾部扫描实测 **2.7 秒**。数据末日这个量只需要最后一行的日期。
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - 512))
            lines = f.read().decode("utf-8", "ignore").strip().splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    d = lines[-1].split(",")[0].replace("-", "").strip()
    return d if len(d) == 8 and d.isdigit() else ""


_LAST_DATE_CACHE: dict[str, str] = {}


def data_last_date(*, refresh: bool = False) -> str:
    """全池 qfq 文件的**最大**日期。

    ⚠️ 原实现是 ``fast_calendar()[-1]``——只抽样文件名排序靠前的 **40 只**。
    这个抽样会**静默钉死**数据末日：若那 40 只在月末停牌（或恰好没被更新），
    最大日期就停在更早的一天，而函数照常返回，决策日随之提前一天。
    2026-11-03 改为全池扫描：多花 2.7 秒，换掉一个无声的错误来源。

    ``refresh=True`` 用于同一进程内数据刚被更新过的场景（不读缓存）。
    """
    if not refresh and "v" in _LAST_DATE_CACHE:
        return _LAST_DATE_CACHE["v"]
    paths = glob.glob(os.path.join(bp.QFQ, "*.qfq.csv"))
    if not paths:
        raise DataStaleError(f"qfq_all 下没有可解析文件：{bp.QFQ}")
    mx = ""
    for p in paths:
        d = _tail_date(p)
        if d > mx:
            mx = d
    if not mx:
        raise DataStaleError("全池扫描未取到任何日期（文件可能全为空/格式变更）")
    _LAST_DATE_CACHE["v"] = mx
    return mx


def fast_calendar(sample: int = 40) -> list[str]:
    """⛔ **已弃用，仅保留给旧调用点**。请用 ``data_last_date()``。

    保留原因：``--verify`` 里还有一处需要"一组日期"而非"一个最大日期"。
    新代码一律不要用它判断月末——它的抽样特性正是钉死的来源。
    """
    ds: set[str] = set()
    for p in sorted(glob.glob(os.path.join(bp.QFQ, "*.qfq.csv")))[:sample]:
        try:
            d = pd.read_csv(p, usecols=["date"])["date"].astype(str)
        except Exception:
            continue
        ds.update(d.str.replace("-", "", regex=False).tolist())
    return sorted(x for x in ds if len(x) == 8 and x.isdigit())


def _prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[4:6])
    if m == 1:
        return f"{y - 1}12"
    return f"{y}{m - 1:02d}"


def resolve_decision_day(today: str, official: list[str], data_last: str,
                         *, check_window: bool = True
                         ) -> tuple[str | None, str, Exception | None]:
    """推出「最近一个已完结月的最后一个交易日」。**不抛异常**，返回三元组。

    返回 ``(决策日, 说明, 异常实例或 None)``。返回**异常实例**而不是错误码，
    是为了让调用方 ``raise err`` 就能保留 ``CalendarNotCovered`` /
    ``MonthEndUnverified`` / ``DecisionWindowMissed`` 的区别——运维看到
    "日历未覆盖"与"数据未覆盖"要做的事完全不同，压成一个码就分不出来了。

    ## 为什么决策日不再取「数据末日」

    原实现把两件事当成了一件：**「哪天是月末」** 与 **「数据到哪天了」**。
    在"每月只跑一次、跑的那天数据刚好到月末"的假设下两者相同，但只要**每天跑**
    （R2 要求的日更链），数据末日就等于今天：11-05 跑一次，函数会返回 20261105，
    一个既不是月末、也不在任何人预期里的决策日——而它格式完全合法。

    2026-11-03 的修法：**决策日由独立日历给出，数据只需要"覆盖"它**。
    这样日更跑多少次都不会改变决策日，而"数据没更新到月末"变成一个显式的拒绝。

    ## 三条例外各自对应一个真实场景

    - ``today > 月末``：本月已走完 ⇒ 用本月月末（最常见）。
    - ``today == 月末``：当天收盘后跑 ⇒ 用今天。**这是唯一能产生真正 FORWARD
      记录的时刻**，也是长假前那几个月末唯一可行的窗口。
    - ``today == 月末 但数据没到``：等更新，不退回上个月
      （退回会拿一个已有台账的月份反复重算，错误信息也会指向错的月份）。
    """
    if not official:
        return None, "交易日历为空", tcal.CalendarUnavailable("交易日历为空")
    ym = today[:6]
    last_nc: Exception | None = None
    for _ in range(4):
        try:
            me = tcal.last_trading_day(ym, official)
        except tcal.CalendarNotCovered as e:
            last_nc = e
            # 日历被截断在月中：本月判不了，退回上个月（例如跨年时日历止于 12-31）
            prev = _prev_month(ym)
            if int(prev[:4]) < int(ym[:4]) - 2:
                return None, str(e), e
            ym = prev
            continue
        except tcal.CalendarUnavailable as e:
            return None, str(e), e

        if today > me:
            if data_last < me:
                return (None,
                        f"数据末日 {data_last} 未覆盖 {me}（{me[:6]} 的最后一个交易日）。"
                        "⇒ 数据更新没跑完，或行情源当天缺失。等更新完成后再跑。",
                        MonthEndUnverified(
                            f"数据末日 {data_last} 未覆盖 {me}（{me[:6]} 的最后一个交易日）"))
            gap = (_parse(today) - _parse(me)).days
            if check_window and gap > MAX_DECISION_AGE_DAYS:
                return (None,
                        f"决策日 {me} 距今 {gap} 天（上限 {MAX_DECISION_AGE_DAYS}）⇒ 决策窗口已过",
                        DecisionWindowMissed(
                            f"决策日 {me} 距今 {gap} 天（上限 {MAX_DECISION_AGE_DAYS}）"
                            "⇒ 决策窗口已过。该月的下一期收益已部分可见，此时生成的记录属回溯"
                            "而非前瞻。请如实记为未跑，不要用补跑记录充数。"))
            return me, (f"决策日 {me} = {me[:6]} 最后一个交易日（独立日历确认），"
                        f"距今 {gap} 天；数据末日 {data_last} 已覆盖"), None
        if today == me:
            if data_last >= me:
                return me, (f"决策日 {me} = 今日，且是 {me[:6]} 最后一个交易日"
                            "（独立日历确认）；收盘后运行，属真实前瞻"), None
            return (None,
                    f"今日 {today} 是月末交易日，但数据只到 {data_last}。"
                    "⇒ 等收盘后数据更新完成再跑（此刻跑会拿到不含今日的截面）。",
                    MonthEndUnverified(
                        f"今日 {today} 是月末交易日，但数据只到 {data_last}"))
        # today < me：本月还没走完 ⇒ 上一个月
        ym = _prev_month(ym)
    # 循环耗尽时优先抛出「未覆盖」而不是笼统的「日历不可用」：
    # 两者的处置不同（刷新日历 vs 检查文件），而这里最常见的原因确实是日历没覆盖。
    return (None, "连续 4 个月都无法判定月末（日历可能严重缺失或未覆盖）",
            last_nc or tcal.CalendarUnavailable("连续 4 个月都无法判定月末"))


def latest_closed_month(cal: list[str] | str | None = None, today: str | None = None,
                        official: list[str] | None = None) -> tuple[str, str]:
    """返回 (决策日 YYYYMMDD, 说明)；不可生成时抛异常。

    ``cal`` 是**数据末日**（兼容旧调用：传列表时取 max），不是决策日来源。
    ``official`` 仅供测试注入；生产读 ``forward/trade_calendar.json``，
    缺失/被改/过旧一律 ``CalendarUnavailable``（**失败关闭**，不按星期几猜）。
    """
    today = today or _today_str()
    off = official if official is not None else tcal.load(today=today)
    if cal is None:
        data_last = data_last_date()
    elif isinstance(cal, str):
        data_last = cal
    else:
        if not cal:
            raise DataStaleError("交易日历为空（qfq_all 无可解析文件）")
        data_last = max(cal)

    me, why, err = resolve_decision_day(today, off, data_last)
    if err is not None:
        raise err
    assert me is not None
    return me, why


def resolve_prev_holdings(ledger_dir: pathlib.Path, me: str) -> tuple[list[str], str | None, list[str]]:
    """取「上一期真实持仓」，返回 ``(持仓, 来源文件名, 被跳过的非观察期文件)``。

    三条规则，每条都对应一个已发生的缺陷：

    1. **只认 ``counts_toward_observation is True``**。原实现取 LEDGER 里最后一个
       文件、不看这个字段，于是 SELFTEST 的自检持仓被当成真实底仓
       ——2026-10-30 那条 LATE_RUN 因此凭空产生 5 笔换手（prev 是 20260930 的
       SELFTEST 5 只，目标与之完全不同）。与 T7 是同一个缺陷的两个落点。
    2. **排除 ``me`` 自己**。``--force`` 覆盖时本期台账已存在，否则会「自己当自己的
       前持仓」，``prev_source`` 指向自身、``prev_holdings == target_holds``、
       ``n_swaps`` 恒为 0。
    3. **严格布尔判断**（``is True``），不用 ``bool(x)``：台账里若写成字符串
       ``"false"``，``bool("false")`` 为真，会静默反转语义。
    """
    hold: list[str] = []
    source: str | None = None
    skipped: list[str] = []
    for fp in sorted(ledger_dir.glob("*.json"), reverse=True):
        if fp.stem == me:
            continue
        rec = json.loads(fp.read_text(encoding="utf-8"))
        if rec.get("counts_toward_observation") is True:
            hold = rec.get("target_holds") or []
            source = fp.name
            break
        skipped.append(fp.name)
    return hold, source, skipped


def code_provenance() -> dict:
    """台账必须能回答「这份记录是哪一版代码产出的」。

    ⚠️ 本项目 ``research/`` 目录**不在 git 里**（Phase 0 实测 `git ls-files research` = 0），
    所以 ``git_head`` 不能定位到源码。真正起作用的标识是 ``code_sha256``；
    ``code_in_git`` 明确写出脚本是否被跟踪，避免读者误以为 ``git_head`` 够用。
    """
    p = pathlib.Path(__file__).resolve()
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    head, tracked = "", False
    try:
        head = subprocess.run(["git", "-C", str(BASE), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=15).stdout.strip()
        r = subprocess.run(["git", "-C", str(BASE), "ls-files", "--error-unmatch", str(p)],
                           capture_output=True, text=True, timeout=15)
        tracked = r.returncode == 0
    except Exception:
        pass
    try:
        rel = str(p.relative_to(BASE)).replace("\\", "/")
    except ValueError:
        rel = str(p)
    return {
        # 字段名刻意用 `code_sha`（不是 code_sha256）：追溯工具与 `test_T10` 都按这个名字找。
        # 算法单独写进 `code_sha_algo`，避免「叫 sha 但不知道哪种 sha」。
        "code_sha": sha,
        "code_sha_algo": "sha256",
        "git_commit": head if (head and tracked) else "NOT_IN_GIT",
        "code_in_git": tracked,
        "code_path": rel,
    }


def build_snapshot(me: str, close_hi: float | None = None) -> pd.DataFrame:
    """截至 me 的全 A 截面特征。**不算 fwd20，不看任何未来数据。**

    close_hi=None 时返回未经 ok_buy 过滤的原始截面（供不同价格口径复用，只遍历一次）。
    """
    files = sorted(glob.glob(os.path.join(bp.QFQ, "*.qfq.csv")))
    ME_arr = np.array([me])
    rows, bad = [], 0
    for p in files:
        code = os.path.basename(p).split(".")[0]
        try:
            df = pd.read_csv(p, usecols=["date", "open", "close", "high", "low", "volume"])
        except Exception:
            bad += 1
            continue
        df = df.dropna()
        df["date"] = df["date"].astype(str).str.replace("-", "", regex=False)
        df = df[df["close"] > 0].sort_values("date").reset_index(drop=True)
        if len(df) < bp.W + 5:
            bad += 1
            continue
        f = bp.factor_series(df)
        dates = df["date"].to_numpy()
        pos = int(np.searchsorted(dates, me, side="right") - 1)
        if pos < bp.W - 1:
            bad += 1
            continue
        stale = (pd.to_datetime(me, format="%Y%m%d") - pd.to_datetime(dates[pos], format="%Y%m%d")).days
        nxt = pos + 1
        rows.append({
            "date": me, "code": code,
            "close": f["close"][pos], "f2": f["f2"][pos], "vol20": f["vol20"][pos],
            "f3": f["f3"][pos], "amt20": f["amt20"][pos],
            "stale_days": stale,
            "next_td": dates[nxt] if nxt < len(dates) else None,
        })
    s = pd.DataFrame(rows)
    base = (s["f2"].notna() & s["vol20"].notna() & (s["amt20"] > AMT_MIN)
            & s["f3"].notna() & (s["stale_days"] <= bp.MAX_STALE))
    if close_hi is None:
        s["ok_buy"] = base & s["close"].between(1.0, 1e9)   # 占位，调用方会重设
        print(f"[snapshot] {me}｜{len(s):,} 只｜原始截面（未按价格过滤）｜解析失败 {bad}")
        s.attrs["base_filter"] = base
        return s
    s["ok_buy"] = base & s["close"].between(1.0, close_hi)
    print(f"[snapshot] {me}｜{len(s):,} 只｜ok_buy {int(s['ok_buy'].sum()):,}"
          f"（价格上限 {close_hi:.0f}）｜解析失败 {bad}")
    return s


def pick(s: pd.DataFrame, seed: int = 20261026) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """返回 (pool, top5, rand5)。

    top5 = 冻结配置写的 entry_rule（amt20 降序）；
    rand5 = 与回测 200 路径同口径（池内随机 5 只，固定种子以便逐月复现）。
    两套都记，因为 `ENTRY_RULE_CANNOT_VERIFY`（见模块文档第 2 条）。
    """
    g = s[s["ok_buy"]].copy()
    g["f3pct"] = g["f3"].rank(pct=True, method="average")
    g["liqpct"] = g["amt20"].rank(pct=True, method="average")
    pool = g[(g["f3pct"] <= THRESH) & (g["liqpct"] < LIQ_MAX)].copy()
    target = pool.sort_values("amt20", ascending=False).head(K).copy()
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(pool), min(K, len(pool)), replace=False) if len(pool) else np.array([], int)
    rand = pool.iloc[sorted(idx)].copy()
    return pool, target, rand


# ---------------------------------------------------------------------------
# D4 安全过滤账本（Phase 1.5 / Amendment A2）
# ---------------------------------------------------------------------------
# 任务书 §5 1.5 要求「在前瞻中并列记录『带过滤 / 不带过滤』两本账，衡量其影响」。
#
# ⚠️ 这里**只新增账本**，不触碰主口径：
#    `target_holds` / `target_holds_rand5` 的算法一字未改（任务书 1.6 要求入场规则冻结）。
#    D4 账本是同一套 `pick()` 在**更小的 ok_buy 子集**上重跑，因此它自然会与主口径不同
#    ——这不是 bug，是 `liqpct = rank(amt20)/N` 的分母效应（见 A1 报告 §3）。
#
# ⚠️ 快照缺失时**必须**让 D4 账本为空并写明原因，不能回退成「不过滤」。
#    否则台账会显示两个一模一样的 top5，读者会误以为「安全过滤没有影响」。

def _universe_state(me: str) -> tuple[dict | None, str]:
    """取可用的 D4 状态快照，返回 ``(state, reason)``。取不到时 state 为 None。"""
    try:
        import p17_universe_filter as uf
    except Exception as exc:                      # noqa: BLE001
        return None, f"无法加载 p17_universe_filter：{exc!r}"
    d = uf.STATE_DIR
    if not d.exists():
        return None, f"无快照目录 {_rel(d)}（先跑 p17_universe_filter.py --fetch）"
    files = [p for p in sorted(d.glob("*.json")) if ".bak." not in p.name]
    if not files:
        return None, "无快照文件（先跑 p17_universe_filter.py --fetch）"
    asof = files[-1].stem
    # 状态必须不早于决策日。用旧状态判新决策是「拿上个月的 ST 名单买这个月的票」，
    # 方向不一定保守——上个月不是 ST、这个月变 ST 的票会被放进来。
    if asof < me:
        return None, f"最新快照 {asof} 早于决策日 {me}，状态已过期"
    try:
        st = uf.load_state(asof)
    except Exception as exc:                      # noqa: BLE001
        return None, f"快照 {asof} 自校验失败：{exc}"
    if st is None:
        return None, f"快照 {asof} 读取为空"
    if not st.get("no_quote_reliable", True):
        return None, f"快照 {asof} 为盘中抓取，「停牌」判据不可靠"
    return st, "ok"


def build_d4_targets(raw: pd.DataFrame, base: pd.Series, me: str) -> dict:
    """按 D4 口径重算目标持仓。**不改主口径**，只多出两组字段。

    两个口径并列，因为 D4 的创业板权限**待用户确认**：
    - ``d4``       ：仅沪深主板（严格）
    - ``d4_gem``   ：沪深主板 + 创业板（宽松，对应「已开通创业板权限」）
    只报严格版会掩盖「创业板占池 26.5%」这一最大单项（见 A2 预登记 P5）。
    """
    st, reason = _universe_state(me)
    if st is None:
        return {
            "target_holds_d4": None,
            "target_holds_d4_gem": None,
            "signal_pool_n_d4": None,
            "signal_pool_n_d4_gem": None,
            "ok_buy_n_d4": None,
            "ok_buy_n_d4_gem": None,
            "universe_state": {"asof": None, "reason": reason, "applied": False},
        }
    import p17_universe_filter as uf

    rows = list(st["stocks"].values())
    out: dict = {
        "universe_state": {
            "asof": st.get("asof"),
            "sha256": st.get("sha256"),
            "path": _rel(uf.state_paths(str(st.get("asof")))[0]),
            "source": st.get("source"),
            "fetch_ts": st.get("fetch_ts"),
            "n_total": st.get("n_total"),
            "n_is_st": st.get("n_is_st"),
            "n_is_delisting": st.get("n_is_delisting"),
            "n_no_quote": st.get("n_no_quote"),
            "applied": True,
        }
    }
    ok_price = base & raw["close"].between(1.0, CLOSE_HI_MAIN)
    for key, with_gem in (("d4", False), ("d4_gem", True)):
        mask = uf.tradable_mask(rows, with_gem=with_gem)
        s = raw.copy()
        s["ok_buy"] = ok_price & raw["code"].map(lambda c: bool(mask.get(c, False)))
        pool, top, _ = pick(s)
        out[f"target_holds_{key}"] = list(top["code"])
        out[f"signal_pool_n_{key}"] = int(len(pool))
        out[f"ok_buy_n_{key}"] = int(s["ok_buy"].sum())
    return out


def judge_fills(target: pd.DataFrame, next_td: str | None = None) -> pd.DataFrame:
    """判定次一交易日能否成交。买卖两侧用 fill_engine 位图（含涨跌停/停牌/缺 bar）。

    ⚠️ `next_td` 未知时**必须**标 `PENDING_NO_CALENDAR`。
    原实现回退成「用决策日自己当执行日」，那会把信号日当成交日，是**静默错判**：
    数据只更新到月末时，`next_td` 恰好为空，于是每次都拿月末当天判成交。

    ``next_td`` 形参**只为兼容既有调用方与测试而保留，不再参与判定**：
    逐只股票的次日以行内 ``next_td`` 为准，全局兜底值正是上面那个 bug 的来源。
    """
    out = []
    for _, r in target.iterrows():
        c = r["code"]
        ntd = r.get("next_td")
        unknown = ntd is None or (isinstance(ntd, float) and not np.isfinite(ntd)) or ntd == ""
        if unknown:
            out.append({"code": c, "side": "buy", "status": "PENDING_NO_CALENDAR", "close": np.nan})
            continue
        bar = fe.load_bars(c)
        if bar is None:
            out.append({"code": c, "side": "buy", "status": "NO_BAR", "close": np.nan})
            continue
        st, _ = fe.judge(bar, str(ntd), False, "buy")
        i = bar.pos.get(str(ntd))
        out.append({"code": c, "side": "buy", "status": st,
                    "close": float(bar.qc[i]) if i is not None else np.nan})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- 落盘辅助
def _ledger_paths(me: str) -> tuple[pathlib.Path, pathlib.Path]:
    return LEDGER / f"{me}.json", LEDGER / f"{me}_targets.csv"


def _backup_ledger(me: str) -> list[str]:
    """--force 时把旧件改名保存。**不删除任何东西。**"""
    done: list[str] = []
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for p in _ledger_paths(me):
        if p.exists():
            b = p.with_name(p.name + f".bak.{stamp}")
            p.replace(b)
            done.append(_rel(b))
    return done


def _append_correction(rec: dict) -> None:
    CORRECTIONS.parent.mkdir(parents=True, exist_ok=True)
    with CORRECTIONS.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def _write_heartbeat(payload: dict) -> None:
    """原子写心跳：先写 .tmp 再 replace，避免读到半截文件。"""
    HB.parent.mkdir(parents=True, exist_ok=True)
    tmp = HB.with_name("heartbeat.json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, HB)


def assert_config_frozen(cfg_hash: str) -> None:
    """运行时校验冻结配置哈希，不一致**立即中止**。

    原实现只把哈希写进台账、从不比较（`test_T1` 钉的就是这条）：配置被改过
    也照样出记录，台账里那串 sha256 于是变成了「当时是什么」而非「应该是」。

    ⚠️ 用 `assert` 是有意的，但它有个已知弱点：`python -O` 会移除 assert。
    因此 `--verify` 里另有一条不依赖 assert 的同等校验，两条互为兜底。
    """
    side = FWD / "sp4_config_v1.sha256"
    if not side.exists():
        print("[warn] 缺 sp4_config_v1.sha256，无法校验冻结配置（--verify 会报错）")
        return
    declared = side.read_text(encoding="utf-8").split()[0]
    assert cfg_hash == declared, (
        f"冻结配置哈希不符：当前 {cfg_hash[:12]} vs 声明 {declared[:12]}。"
        "配置被改动过就不能继续——先确认是不是预期内的 amendment。")


def verify() -> int:
    """只读自检：配置哈希 + 台账自校验 + observations 一致性。**不写任何文件。**"""
    bad: list[str] = []
    cfg = KF.read_bytes()
    h = hashlib.sha256(cfg).hexdigest()
    side = FWD / "sp4_config_v1.sha256"
    if side.exists():
        declared = side.read_text(encoding="utf-8").split()[0]
        if declared != h:
            bad.append(f"配置哈希不符：实际 {h[:12]} vs 声明 {declared[:12]}")
    else:
        bad.append("缺少 sp4_config_v1.sha256")

    files = sorted(LEDGER.glob("*.json"))
    if not files:
        bad.append("台账为空")
    recs: dict[str, dict] = {}
    for p in files:
        rec = json.loads(p.read_text(encoding="utf-8"))
        claimed = rec.get("ledger_sha256")
        actual = hashlib.sha256(json.dumps(
            {k: v for k, v in rec.items() if k != "ledger_sha256"},
            ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        if claimed != actual:
            bad.append(f"{p.name} ledger_sha256 不符：声明 {claimed} vs 实算 {actual}")
        recs[rec["date"]] = rec
        if rec.get("config_sha256") not in (None, h):
            bad.append(f"{p.name} 记录的是另一版配置 {str(rec.get('config_sha256'))[:12]}")

    obs = FWD / "observations.csv"
    if obs.exists():
        df = pd.read_csv(obs, dtype=str)
        for _, r in df.iterrows():
            d = str(r["date"])
            rec = recs.get(d)
            if rec is None:
                bad.append(f"observations 有 {d} 但台账没有")
                continue
            if str(r["target_holdings"]) != "|".join(rec.get("target_holds", [])):
                bad.append(f"{d} target_holdings 不一致")
            try:
                if int(float(r["n_swaps"])) != int(rec.get("n_swaps", -1)):
                    bad.append(f"{d} n_swaps 不一致")
            except Exception:
                bad.append(f"{d} n_swaps 不可解析")
            flag = str(rec.get("counts_toward_observation"))
            if f"计入观察期={flag}" not in str(r.get("note", "")):
                bad.append(f"{d} note 的计入观察期与台账不符")

    print(f"[verify] 配置 {h[:12]}｜台账 {len(files)} 条｜observations "
          f"{0 if not obs.exists() else len(pd.read_csv(obs, dtype=str))} 行")
    # P1（预登记在验收标准 §6）：单条自校验发现不了「整条记录被删」
    print("[verify] 注：ledger_sha256 为单条自校验，无 prev 链 ⇒ 无法检测记录被删除")
    if bad:
        for b in bad:
            print("  ✗", b)
        return 1
    print("  ✓ 全部通过")
    return 0


def main() -> int:
    t0 = time.time()
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="决策日 YYYYMMDD；默认最新已完结月末")
    ap.add_argument("--selftest", action="store_true", help="标记为管道自检，不计入观察期")
    ap.add_argument("--late-run", action="store_true", help="显式声明补跑（决策日 < 今天）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写：不落台账/明细/汇总/心跳")
    ap.add_argument("--force", action="store_true", help="允许覆盖已存在台账（旧件先备份）")
    ap.add_argument("--verify", action="store_true", help="只读自检，不生成决策")
    ap.add_argument("--if-due", action="store_true",
                    help="仅在决策窗口内运行；不在窗口内则打印并退出 0（写 NOT_DUE 心跳）")
    args = ap.parse_args()

    if args.verify:
        return verify()

    today = _today_str()
    if args.if_due and not args.asof:
        # ⭐ 日更链的入口。数据每天更新，但决策一个月只有一次：
        #    没有这个开关，日更链会在非窗口日反复撞上「台账已存在」(rc=5)，
        #    而那看起来像故障，实际是正常运行。**用退出码区分"没事发生"与"出事"**。
        try:
            off = tcal.load(today=today)
        except tcal.CalendarUnavailable as e:
            print(f"[abort] 交易日历不可用，无法确认月末：{e}")
            if not args.dry_run:
                _write_heartbeat({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "decision": None,
                                  "mode": "ABORT_CALENDAR", "ok": False,
                                  "duration_s": round(time.time() - t0, 3), "reason": str(e)})
            return 6
        dl = data_last_date()
        # check_window=False：窗口判定交给本函数自己做，因为"是否漏跑"还要看台账在不在。
        # 先抛 DecisionWindowMissed 会让这里永远走不到 MISSED 分支。
        me_due, why_due, err_due = resolve_decision_day(today, off, dl,
                                                        check_window=False)
        if me_due is None:
            print(f"[abort] {why_due}")
            # 日历问题与数据问题分开报：前者要刷新日历，后者要重跑更新。
            is_cal = isinstance(err_due, tcal.CalendarUnavailable)
            if not args.dry_run:
                _write_heartbeat({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "decision": None,
                                  "mode": "ABORT_CALENDAR" if is_cal else "ABORT_STALE",
                                  "ok": False,
                                  "duration_s": round(time.time() - t0, 3), "reason": why_due})
            return 6 if is_cal else 3
        gap_due = (_parse(today) - _parse(me_due)).days
        led_due, _t = _ledger_paths(me_due)
        if led_due.exists():
            print(f"[skip] 决策日 {me_due} 的台账已存在（{_rel(led_due)}）⇒ 非窗口，跳过")
            if not args.dry_run:
                _write_heartbeat({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "decision": me_due,
                                  "mode": "NOT_DUE", "ok": True,
                                  "duration_s": round(time.time() - t0, 3),
                                  "reason": "台账已存在"})
            return 0
        if gap_due > MAX_LATE_RUN_DAYS:
            # 台账不存在 + 已超补跑窗口 = **真的漏跑了**。
            # 不能报成"跳过"：跳过意味着一切正常，而这一条意味着证据链缺了一期，
            # 且按规则**不能**用补跑记录补上（补出来的属回溯）。
            print(f"[missed] 决策日 {me_due} 无台账，且距今 {gap_due} 天 > "
                  f"{MAX_LATE_RUN_DAYS} ⇒ 该月已漏跑，无法以 LATE_RUN 补记。")
            print("         按 R2 第五节：如实记录，不得用补跑记录充数。请人工确认后在 STATUS.md 登记。")
            if not args.dry_run:
                _write_heartbeat({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "decision": me_due,
                                  "mode": "MISSED", "ok": False,
                                  "duration_s": round(time.time() - t0, 3),
                                  "reason": f"决策日 {me_due} 无台账，距月末 {gap_due} 天，超补跑窗口"})
            return 8
        me, why = me_due, why_due
    elif args.asof:
        me, why = args.asof, "指定"
        # ⚠️ 手工指定也要过月末校验：否则「钉死」只是从代码搬到了命令行参数上，
        #    而手工钉死更难发现——它只出现在有人改过调度脚本的时候。
        #
        # 两条例外，各有明确理由：
        #   --selftest     自检日期可以不是月末，但它不计入观察期；
        #   未来日期       未来日不可能是「数据末日」（数据来自文件，文件不可能来自未来），
        #                  这条路径只用于模拟 FORWARD。**打印 guard 行**让它可见，
        #                  而不是静默跳过——静默跳过正是本项目反复出问题的地方。
        if args.selftest:
            pass
        elif me > today:
            print(f"[guard] --asof {me} 晚于今天 {today}：跳过月末校验"
                  "（未来日期不可能是数据末日，属模拟 FORWARD 路径）")
        else:
            try:
                off = tcal.load(today=today)
                if not tcal.is_month_end(me, off):
                    print(f"[abort] --asof {me} 不是月末交易日：{tcal.describe(me, off)}")
                    print("        月末信号必须落在该月最后一个交易日。自检请加 --selftest。")
                    return 7
            except tcal.CalendarUnavailable as e:
                print(f"[abort] 交易日历不可用，无法确认月末：{e}")
                print("        先跑 `python trade_calendar.py --refresh`。不按星期几猜。")
                return 6
    else:
        try:
            me, why = latest_closed_month(today=today)
        except tcal.CalendarUnavailable as e:
            # 与"数据过期"分开退出码：处置动作不同（刷新日历 vs 更新数据）。
            print(f"[abort] 交易日历不可用，无法确认月末：{e}")
            print("        先跑 `python trade_calendar.py --refresh`。不按星期几猜。")
            if not args.dry_run:
                _write_heartbeat({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "decision": None,
                                  "mode": "ABORT_CALENDAR", "ok": False,
                                  "duration_s": round(time.time() - t0, 3), "reason": str(e)})
            return 6
        except DataStaleError as e:
            print(f"[abort] {e}")
            # ⚠️ dry-run 连心跳都不写：否则「只算不写」的承诺立刻破产，
            #    而心跳恰恰是最容易被误读成「今天跑成功了」的文件。
            if not args.dry_run:
                _write_heartbeat({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "decision": None,
                                  "mode": "ABORT_STALE", "ok": False,
                                  "duration_s": round(time.time() - t0, 3), "reason": str(e)})
            return 3
    print(f"[decision] {me}（{why}）")

    # ---- 模式判定 ----
    # 规则表（顺序有意义）：
    #   --selftest                 -> SELFTEST（不计入）
    #   me >= today                -> FORWARD （真实前瞻）
    #   me <  today 且 gap <= 3    -> LATE_RUN（周末级延迟，结果尚不可见；计入，但标注）
    #   me <  today 且 gap >  3    -> SELFTEST（**强制降级**：已能看见结果，属回溯）
    # gap>3 的强制降级是既有闸门，理由见 test_guard_downgrades_stale_decision_date：
    # 拿一个月前的月末当「前瞻记录」会把回溯伪装成样本外证据，不能靠操作者自觉。
    gap_days = (_parse(today) - _parse(me)).days if me < today else 0
    if args.selftest:
        mode = "SELFTEST"
    elif me >= today:
        mode = "FORWARD"
    elif gap_days <= MAX_LATE_RUN_DAYS:
        mode = "LATE_RUN"
    else:
        mode = "SELFTEST"
        print(f"[guard] 决策日 {me} 距今 {gap_days} 天 > {MAX_LATE_RUN_DAYS} 天"
              f" → **自动降级为 SELFTEST**（不计入观察期）")
        print(f"[guard] 真正的前瞻起点应是决策日 = 运行日；补跑只容忍周末级延迟")
    if args.late_run and mode != "LATE_RUN":
        print(f"[abort] 声明了 --late-run，但决策日 {me} 距今 {gap_days} 天，"
              f"当前判定为 {mode}（补跑只容忍 <= {MAX_LATE_RUN_DAYS} 天）")
        return 4
    counts = mode != "SELFTEST"
    print(f"[mode] {mode}｜计入观察期 = {counts}｜距今 {gap_days} 天")

    # ---- 台账不可变：已存在则拒绝 ----
    led, tgt = _ledger_paths(me)
    if led.exists() and not args.force and not args.dry_run:
        print(f"[abort] 台账已存在：{_rel(led)}")
        print("        台账不可变。要重算请加 --force（旧件会先备份为 *.bak.<UTC>，再追加更正日志）。")
        return 5

    cfg_hash = sha256_file(KF)
    assert_config_frozen(cfg_hash)
    raw = build_snapshot(me)
    base = raw.attrs["base_filter"]
    s_main = raw.copy(); s_main["ok_buy"] = base & raw["close"].between(1.0, CLOSE_HI_MAIN)
    s_cfg = raw.copy(); s_cfg["ok_buy"] = base & raw["close"].between(1.0, CLOSE_HI_CFG)
    print(f"          主口径 1-{CLOSE_HI_MAIN:.0f}: ok_buy {int(s_main['ok_buy'].sum()):,}"
          f"｜配置口径 1-{CLOSE_HI_CFG:.0f}: ok_buy {int(s_cfg['ok_buy'].sum()):,}")
    pool_m, target_m, target_r = pick(s_main)
    pool_c, target_c, _ = pick(s_cfg)
    print(f"[pool] 主口径 1-200：pool {len(pool_m)}｜top5 {list(target_m['code'])}")
    print(f"[pool] 同池随机5(rand5)：{list(target_r['code'])}")
    same_cfg = list(target_c["code"]) == list(target_m["code"])
    print(f"[pool] 配置口径 1-50：pool {len(pool_c)}｜top5 {list(target_c['code'])}"
          f"｜{'与主口径一致' if same_cfg else '⚠️ 与主口径不一致（边界并列导致选人变化）'}")

    # ---- D4 安全过滤账本（Phase 1.5 / A2）：只新增，不改主口径 ----
    d4 = build_d4_targets(raw, base, me)
    if d4["universe_state"]["applied"]:
        print(f"[d4] 状态快照 {d4['universe_state']['asof']}"
              f"（ST {d4['universe_state']['n_is_st']}、退 {d4['universe_state']['n_is_delisting']}"
              f"、无报价 {d4['universe_state']['n_no_quote']}，共 {d4['universe_state']['n_total']}）")
        print(f"[d4] 仅主板      ok_buy {d4['ok_buy_n_d4']:,}｜pool {d4['signal_pool_n_d4']}"
              f"｜top5 {d4['target_holds_d4']}")
        print(f"[d4] 主板+创业板  ok_buy {d4['ok_buy_n_d4_gem']:,}｜pool {d4['signal_pool_n_d4_gem']}"
              f"｜top5 {d4['target_holds_d4_gem']}")
    else:
        print(f"[d4] ⚠️ 无可用状态快照 ⇒ D4 账本留空（**不回退成不过滤**）："
              f"{d4['universe_state']['reason']}")

    fills = judge_fills(target_m, target_m["next_td"].iloc[0] if len(target_m) else me)
    print(f"[fills] " + "｜".join(f"{r['code']}:{r['status']}" for _, r in fills.iterrows()))
    if (fills["status"] == "PENDING_NO_CALENDAR").any():
        print("[fills] 注：next_td 未知 ⇒ 成交状态挂起，待次一交易日数据到位后由 p13b 回填")

    prev_hold, prev_source, prev_skipped = resolve_prev_holdings(LEDGER, me)
    if prev_skipped:
        print(f"[prev] 跳过 {len(prev_skipped)} 条非观察期记录：{prev_skipped}")

    n_filled = int((fills["status"] == "FILLED").sum())
    # 首次建仓：n_swaps = 实际成交只数；后续：换出+换入
    n_swaps = n_filled if not prev_hold else len(set(target_m["code"]) - set(prev_hold))

    # 成本估算（建仓按成交只数；后续按换手只数，单边双边各算）
    notional = CAPITAL * 0.25 / K
    per_trade = max(COMM_MIN, notional * COMM_RATE) + notional * TRANSFER + notional * SPREAD * 0.5
    est_cost = n_filled * (per_trade + max(COMM_MIN, notional * COMM_RATE) + notional * TRANSFER + notional * STAMP)

    prov = code_provenance()
    rec = {
        "date": me,
        "record_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "counts_toward_observation": counts,
        "late_run": mode == "LATE_RUN",
        "late_calendar_days": gap_days if mode == "LATE_RUN" else 0,
        "code_sha": prov["code_sha"],
        "code_sha_algo": prov["code_sha_algo"],
        "git_commit": prov["git_commit"],
        "code_in_git": prov["code_in_git"],
        "code_path": prov["code_path"],
        "data_last_date": data_last_date(),
        "config_sha256": cfg_hash,
        "config_path": _rel(KF),
        "data_snapshot": f"qfq_all/exec_all @ {me}",
        "signal_pool_n": int(len(pool_m)),
        "signal_pool_n_close50": int(len(pool_c)),
        "amt20_range_top5_pct": float(target_m["amt20"].max() / target_m["amt20"].min() - 1)
        if len(target_m) > 1 else None,
        "target_holds": list(target_m["code"]),
        "target_holds_rand5": list(target_r["code"]),
        "target_holds_close50": list(target_c["code"]),
        # ---- D4 安全过滤（Amendment A2）。主口径三行**不得**被这些覆盖。----
        "target_holds_d4": d4["target_holds_d4"],
        "target_holds_d4_gem": d4["target_holds_d4_gem"],
        "signal_pool_n_d4": d4["signal_pool_n_d4"],
        "signal_pool_n_d4_gem": d4["signal_pool_n_d4_gem"],
        "ok_buy_n_d4": d4["ok_buy_n_d4"],
        "ok_buy_n_d4_gem": d4["ok_buy_n_d4_gem"],
        "universe_state": d4["universe_state"],
        "prev_holdings": prev_hold,
        "prev_source": prev_source,              # None = 本次为观察期内首次建仓
        "prev_skipped_non_observation": prev_skipped,
        "n_swaps": int(n_swaps),
        "orders": [{"code": r["code"], "side": r["side"], "shares": int(notional / r["close"] // 100 * 100)
                    if np.isfinite(r["close"]) and r["close"] > 0 else 0,
                    "ref_close": None if not np.isfinite(r["close"]) else float(r["close"])}
                   for _, r in fills.iterrows()],
        "fill_status": {r["code"]: r["status"] for _, r in fills.iterrows()},
        "fill_price": {},
        "fees": {"est_total": float(est_cost),
                 "comm_rate": COMM_RATE, "comm_min": COMM_MIN,
                 "transfer": TRANSFER, "stamp_sell": STAMP, "spread": SPREAD},
        "nav": CAPITAL, "cash": CAPITAL, "stock_weight": 0.0, "n_positions": 0,
        "unfilled_orders": [r["code"] for _, r in fills.iterrows() if r["status"] != "FILLED"],
        "target_weight": 0.25,
        "notes": [
            "纸面前瞻：未接真实下单，nav/cash 为初始值，待次一交易日收盘后回填",
            "池筛选不含 fwd20（前瞻不知道未来），故池子略大于回测口径",
            "price_filter 主口径 1-200（回测实现）；冻结配置写的 1-50 作为对照列",
            "entry_rule 判定 ENTRY_RULE_CANNOT_VERIFY：top5 与 rand5 的差异 CI 达 3pp，故两套都记",
            f"mode={mode}；late_run={mode == 'LATE_RUN'}；计入观察期={counts}",
            "D4 安全过滤为并列账本（Amendment A2）：target_holds 主口径未变，"
            "target_holds_d4 / _d4_gem 为「仅沪深主板」/「+创业板」口径",
            (f"D4 未应用：{d4['universe_state']['reason']}" if not d4["universe_state"]["applied"]
             else f"D4 状态快照 {d4['universe_state']['asof']}"
                  f"（晚于决策日 {me}，非决策日当日状态）"),
        ],
    }
    rec["ledger_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in rec.items() if k != "ledger_sha256"},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    obs_row = {
        "date": me, "signal_time": rec["record_time"],
        "data_version": rec["data_snapshot"], "pool_size": len(pool_m),
        "n_hold_target": len(target_m), "target_holdings": "|".join(target_m["code"]),
        "prev_holdings": "|".join(prev_hold), "n_swaps": n_swaps,
        "est_cost_rate": round(est_cost / CAPITAL, 6),
        "nav_model": "", "mdd_model": "", "benchmark_nav": "",
        "strategy_hash": cfg_hash[:12],
        "note": f"{mode}；买入规则=amt20降序；计入观察期={counts}",
    }

    if args.dry_run:
        body = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        print("\n[dry-run] 未写任何文件。将要写入的内容：")
        print(f"  ledger        {_rel(led)}  sha256={hashlib.sha256(body.encode()).hexdigest()[:16]}"
              f" ({len(body)} B)")
        print(f"  targets       {_rel(tgt)}  {len(target_m)} 行")
        print(f"  observations  追加/替换 {me} 一行")
        print(f"  D4 账本       " + (f"d4={rec['target_holds_d4']}｜d4_gem={rec['target_holds_d4_gem']}"
                                  if rec["target_holds_d4"] is not None
                                  else f"留空（{d4['universe_state']['reason']}）"))
        print(f"  heartbeat     不写（dry-run 不产生心跳，避免伪装成已运行）")
        return 0

    # ---- 覆盖保护：--force 时先备份 + 记更正 ----
    if led.exists():
        backups = _backup_ledger(me)
        _append_correction({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": "LEDGER_OVERWRITTEN_WITH_FORCE",
            "decision": me,
            "backups": backups,
            "new_mode": mode,
            "reason": "--force 显式覆盖；旧件已备份，未删除",
        })
        print(f"[force] 旧件已备份：{backups}")

    LEDGER.mkdir(parents=True, exist_ok=True)
    (LEDGER / f"{me}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target_m.assign(**{"fill_status": target_m["code"].map(rec["fill_status"])}).to_csv(
        LEDGER / f"{me}_targets.csv", index=False, encoding="utf-8")

    # 汇总行（沿用既有 observations.csv 的列）
    obs = FWD / "observations.csv"
    row = pd.DataFrame([obs_row])
    if obs.exists():
        old = pd.read_csv(obs)
        old = old[old["date"].astype(str) != str(me)]     # 同月重跑则覆盖
        pd.concat([old, row], ignore_index=True).to_csv(obs, index=False, encoding="utf-8-sig")
    else:
        row.to_csv(obs, index=False, encoding="utf-8-sig")

    if mode == "LATE_RUN":
        _append_correction({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": "LATE_RUN_RECORDED",
            "decision": me,
            "late_calendar_days": gap_days,
            "counts_toward_observation": counts,
            "reason": "决策日早于运行日，属补跑；信号仅用 build_snapshot(me)（数据截断到决策日）",
        })

    _write_heartbeat({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "decision": me, "mode": mode, "ok": True,
        "counts_toward_observation": counts,
        "duration_s": round(time.time() - t0, 3),
        "ledger_sha256": rec["ledger_sha256"],
    })

    print(f"\n[ledger] {LEDGER / f'{me}.json'}  sha={rec['ledger_sha256']}")
    print(f"[mode] {rec['mode']}｜计入观察期 = {rec['counts_toward_observation']}")
    print(f"[unfilled] {rec['unfilled_orders'] or '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
