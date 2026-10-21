#!/usr/bin/env python
"""阶段 C：股票池效应 vs 构造效应 vs 入池门槛 —— 五格交叉实验。

预登记：`docs/PHASE_C_UNIVERSE_PREREG_2026-10-21.md`
冻结 sha256：`3a01759a37f03c57ec19a6090f52bbda11ed5a284271edfc4328504de64d1ab3`

## 这个脚本回答什么

Gate 6 的 spread 1.417pp/月 比 Gate 3 的 0.968pp 高。这个差距里多少来自**股票池**、
多少来自**选股比例**、多少来自**入池门槛**？五格交叉把三者拆开。

## ⚠️ 运行前声明：预登记里的两处口径歧义，及本脚本的处理

**歧义 1：主检验用含约束还是无约束的 spread？**
§5 标题写"交易约束（本实验新增，比 Gate 2~6 更严）"并逐条给出规则，
§8.2 又明确预期"加约束后效应很可能低于 Gate 6 的 1.417pp"。但 §2 表格里的
"预期 mean(d)"（A0 +0.4598 / A +0.4450 / B +1.000 / D +0.7084）全部是**无约束**口径。

→ **处理**：两套都算、都报。
  - 主检验取 **含约束**（理由：§5 声明为本实验新增约束，§8.2 明确预期主结果含约束）；
  - 无约束套仅作**复现校验**，用于核对能否重现 §2 的四个预期值，**不参与任何判定**。
  - 这个决定写在运行之前，不因结果好坏更改。

**歧义 2：§3.2 三档判据存在覆盖缺口。**
若 `n ≥ 150` 且 `|NW t| ≥ 1.96` 但 `|mean(Δ)| < 0.30pp`，三档都不匹配
（CONFIRMED 缺经济门槛、NOT_DETECTED 缺 |t|<1.96、INCONCLUSIVE 缺样本不足）。

→ **处理**：运行前补充第四档 `POOL_EFFECT_SIGNIFICANT_BUT_SMALL`
  （统计显著但未达预先写死的经济门槛）。**不改动原有三档的任何条件**。

## 本脚本的两处自我修正（都保留修复前产物）

| # | 错误 | 后果 | 修复 | 修复前产物 |
|---|---|---|---|---|
| 1 | 把预登记 §7 的 `2×to×12×费率`（**年化**）当月度成本扣 | D 格月成本 1.9326pp（正确 0.1610pp），成本后收益全部大幅为负（净 −1.27pp，t=−7.96） | 月度成本 = `2×to×费率`，年化单列 `cost_pp_year`（与 Gate 6 的 0.1606 对齐） | `phasec_out/<sha>_pre_costfix/` |
| 2 | 辅助检验按 `(id, hi, lo)` 传参，标签却写 `spread(lo)−spread(hi)` | S3~S5 **符号全反**：S3 报 +0.5703，真相是 D 比 B 低 0.5703 | 改为 `(id, FROM, TO)`，Δ 恒为 `spread(TO)−spread(FROM)`；用正则从 label 反解方向做单测 | `phasec_out/<sha>_pre_auxdirfix/` |

判据不受影响（成本不参与判定；辅助检验不论符号都不构成证据），
但**成本后的解读与 S3 的方向结论必须用修正值**。

## 数据前置（缺一即拒绝运行，见 §9.2）

1. 预登记 sha256 == 冻结值
2. `data/exec_all/_acceptance.json`：`n_missing == 0` 且 `v2_bad == []`
3. `data/qfq_all` 的 `*.qfq.csv` ≥ 5,400
4. 518 只纯 A 股名单 sha256 == `dc1ac858…`
5. 参数硬编码，**不接受任何命令行覆盖**（本脚本没有 argparse）

## 实现要点

- **价格层**用 ADJ（`qfq_all`），**交易状态**用 EXEC（`exec_all`）。两者缺一不入池（§1 双覆盖）。
- 全 A 的 ADJ 矩阵只加载一次；518 池三格取列子集但**保留同一行索引**，
  这样五格的交易日历完全一致，配对差不会混入日历差异。
- 信号与前瞻收益**全向量化预计算**（`shift` + `rolling`），逐月只做取行 + 排序。
- 可成交性用**流式扫描**：逐只股票读一次 EXEC csv，只保留被检查到的日期及其前一根 bar，
  内存 O(被检查的 日期×股票 数) 而不是 O(全市场全历史)。
  流式实现与 `asof_universe.TradabilityIndex` 在启动时做**交叉验证**（不一致即拒绝运行）。

用法
----
    python scripts/run_phase_c.py
"""

from __future__ import annotations

import bisect
import csv
import glob
import hashlib
import json
import math
import os
import statistics
import sys
import time
import datetime as dt
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage3_sigma_d import month_end_positions          # noqa: E402
from asof_universe import TradabilityIndex, limit_ratio  # noqa: E402

# ==================== 冻结参数（逐字照抄预登记，不许命令行覆盖） ====================

PREREG_PATH = "docs/PHASE_C_UNIVERSE_PREREG_2026-10-21.md"
PREREG_SHA256 = "3a01759a37f03c57ec19a6090f52bbda11ed5a284271edfc4328504de64d1ab3"

POOL_FILE = "data/_gate3_pool_532.json"
POOL_518_SHA256 = "dc1ac8589cc550a1668292f972e55f29e90be697ea6866b8feda804d1c7e7ea5"

ADJ_DIR = "data/qfq_all"
EXEC_DIR = "data/exec_all"
ACCEPT_PATH = "data/exec_all/_acceptance.json"
DIV_DIRS = ("data/div_all_em", "data/div_all_sina")
MIN_QFQ_FILES = 5400

LOOKBACK, SKIP = 21, 1
MIN_VALID_RATIO = 0.90
MIN_VALID_DAYS = math.ceil(LOOKBACK * MIN_VALID_RATIO)     # 19
MIN_LISTED_DAYS = 252
K_FIXED = 110
K_FRAC = 0.20
MIN_POOL = 10

START, END_SIGNAL, END_HOLD = "2007-01-01", "2026-09-30", "2026-10-31"

NW_LAG = 3
MIN_N_MAIN = 150
MIN_NW_EFF = 100
T_CRIT = 1.96
ECON_MIN_PP = 0.30          # 经济门槛，pp/月，写死于看到结果之前
ALPHA = 0.05
Z_MDE = 2.8016              # 80% power, 双侧 α=0.05（与 Gate 6 一致）

COST_TIERS = (0.001, 0.003)

PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasec_out", PREREG_SHA12)


# ==================== 前置校验 ====================

def _sha256_file(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def pool_518() -> list[str]:
    """518 只纯 A 股（532 池剔除 14 只 B 股）。**用代码前缀显式判断，不靠数据缺失被动排除。**"""
    codes = json.load(open(POOL_FILE, encoding="utf-8"))["codes"]
    out = sorted(c for c in codes if not _prefix(c).startswith(("200", "900")))
    return out


def _prefix(code: str) -> str:
    return code.split(".")[0]


def verify() -> dict:
    """§9.2 五项校验。任何一项不过即拒绝运行。"""
    info: dict = {}

    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    got = _sha256_file(PREREG_PATH)
    if got != PREREG_SHA256:
        raise SystemExit(f"预登记 sha256 不符，拒绝运行。\n  期望 {PREREG_SHA256}\n  实际 {got}")
    info["prereg_sha256"] = got
    print(f"[C] ① 预登记校验 OK  {got[:16]}")

    if not os.path.exists(ACCEPT_PATH):
        raise SystemExit(f"缺少验收文件 {ACCEPT_PATH}；请先跑 scripts/verify_exec_all.py")
    acc = json.load(open(ACCEPT_PATH, encoding="utf-8"))
    if acc.get("n_missing", 1) != 0:
        raise SystemExit(f"exec_all 验收未通过：n_missing={acc.get('n_missing')}")
    if acc.get("v2_bad"):
        raise SystemExit(f"exec_all 验收 V2 未通过（{len(acc['v2_bad'])} 只不一致）")
    info["exec_acceptance"] = {"n_have": acc.get("n_have"), "n_target": acc.get("n_target"),
                               "n_missing": acc.get("n_missing"), "v2_bad": acc.get("v2_bad")}
    print(f"[C] ② exec_all 验收 OK  {acc.get('n_have')}/{acc.get('n_target')}  "
          f"V2 {acc.get('v2_checked')} 只 0 不一致")

    n_qfq = len(glob.glob(os.path.join(ADJ_DIR, "*.qfq.csv")))
    if n_qfq < MIN_QFQ_FILES:
        raise SystemExit(f"qfq_all 文件数 {n_qfq} < {MIN_QFQ_FILES}，拒绝运行")
    info["n_qfq_files"] = n_qfq
    print(f"[C] ③ qfq_all 覆盖 OK  {n_qfq} ≥ {MIN_QFQ_FILES}")

    p518 = pool_518()
    h = hashlib.sha256(",".join(p518).encode()).hexdigest()
    if h != POOL_518_SHA256:
        raise SystemExit(f"518 池 sha256 不符，拒绝运行。\n  期望 {POOL_518_SHA256}\n  实际 {h}")
    info["pool_518_sha256"] = h
    info["pool_518_n"] = len(p518)
    print(f"[C] ④ 518 池校验 OK  {len(p518)} 只  {h[:16]}")

    info["params_hardcoded"] = True
    print("[C] ⑤ 参数硬编码 OK（本脚本无 argparse，不可覆盖）")
    return info


# ==================== 数据加载 ====================

def load_close(codes: set[str] | None = None) -> pd.DataFrame:
    """加载 ADJ 收盘价矩阵（宽表，行=交易日，列=代码）。<=0 视为缺失。"""
    paths = sorted(glob.glob(os.path.join(ADJ_DIR, "*.qfq.csv")))
    if codes is not None:
        paths = [p for p in paths if os.path.basename(p).replace(".qfq.csv", "") in codes]
    cols: dict[str, pd.Series] = {}
    t0 = time.time()
    for i, p in enumerate(paths, 1):
        code = os.path.basename(p).replace(".qfq.csv", "")
        df = pd.read_csv(p, usecols=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        s = df.drop_duplicates("date", keep="last").set_index("date")["close"].astype(float)
        s = s[s > 0]
        if not s.empty:
            cols[code] = s
        if i % 1000 == 0:
            print(f"      读入 {i}/{len(paths)} … {time.time()-t0:.0f}s")
    out = pd.DataFrame(cols).sort_index()
    print(f"[C] ADJ 载入 {out.shape[1]} 只 × {len(out)} 天 "
          f"({out.index[0].date()} ~ {out.index[-1].date()})  {time.time()-t0:.0f}s")
    return out


def build_mask(close: pd.DataFrame, min_listed: int) -> pd.DataFrame:
    """as-of 可投资掩码。`min_listed<=0` = 不要求上市天数（Gate 3 的真实口径）。"""
    v = (close.notna() & (close > 0)).to_numpy()
    if min_listed <= 0:
        m = v
    else:
        cum = np.cumsum(v, axis=0, dtype=np.int32)
        m = v & (cum >= min_listed)
    return pd.DataFrame(m, index=close.index, columns=close.columns)


# ==================== 信号与逐月选股 ====================

class Panels:
    """预计算面板：信号分数、有效性、前瞻收益用的 ffill 价。**一次算好，五格共用。**"""

    def __init__(self, close: pd.DataFrame):
        self.close = close
        self.cal = close.index
        valid = close.notna() & (close > 0)
        # 信号：close(t-1) / close(t-21) - 1
        self.score = (close.shift(SKIP) / close.shift(SKIP + LOOKBACK - 1) - 1.0)
        # 窗口 [pos-21, pos-1] 内的有效日数（rolling 在 pos-1 处取到该窗口）
        cnt = valid.rolling(LOOKBACK, min_periods=1).sum()
        self.nd_ok = cnt.shift(1) >= MIN_VALID_DAYS
        self.ffill = close.ffill()

    def month_rows(self, mask: pd.DataFrame, positions: list[int], k_mode: str) -> list[dict]:
        """逐月选股。**不应用交易约束**（约束在事后按 code 批量检查后剔除）。"""
        sc_all, nd_all = self.score, self.nd_ok
        cal = self.cal
        rows: list[dict] = []
        for i in range(len(positions) - 1):
            pos, nxt = positions[i], positions[i + 1]
            sdate = str(cal[pos].date())
            if sdate > END_SIGNAL:
                continue
            if pos + 1 >= len(cal):
                continue
            ok = mask.iloc[pos].to_numpy()
            pool0 = mask.columns[ok]
            if len(pool0) < MIN_POOL:
                continue
            # 信号有效
            good = nd_all.iloc[pos].to_numpy()
            sc = pd.Series(sc_all.iloc[pos].to_numpy(), index=mask.columns)[pool0]
            sc = sc[good[ok] & sc.notna()]
            # 前瞻收益（ffill 价）
            p0 = self.ffill.iloc[pos]
            p1 = self.ffill.iloc[nxt]
            fwd = (p1 / p0 - 1.0).loc[sc.index].dropna()
            pool = sc.index.intersection(fwd.index)
            n_pool = len(pool)
            if n_pool < MIN_POOL:
                continue
            if k_mode == "fixed":
                k = max(1, min(K_FIXED, n_pool // 2))
            else:
                k = max(1, min(int(round(K_FRAC * n_pool)), n_pool // 2))
            vals = sc.loc[pool].to_numpy()
            order = np.argsort(vals, kind="stable")        # 升序：低分在前（反转做多）
            idx = pool.to_numpy()
            longs = list(idx[order[:k]])
            shorts = list(idx[order[-k:]])
            f = fwd.loc[pool]
            rl, rs = float(f.loc[longs].mean()), float(f.loc[shorts].mean())
            rows.append({
                "signal_date": sdate, "month": str(cal[nxt].date()),
                "n_pool": int(n_pool), "k_used": int(k), "k_frac": round(k / n_pool, 4),
                "longs": longs, "shorts": shorts, "fwd": f,
                "entry": str(cal[pos + 1].date()), "exit": str(cal[nxt].date()),
                "r_long_raw": rl, "r_short_raw": rs, "d_raw": 0.5 * (rl - rs),
            })
        return rows


# ==================== 交易约束（流式 + 交叉验证） ====================

def load_ex_dates(code: str, div_dirs=DIV_DIRS) -> list[str]:
    """两源合并的除权除息日（升序去重）。缺失即视为"无事件"——
    ⚠️ 这与 D-1 事故同构，但此处**只用于豁免涨跌停判定**，
    最坏后果是少豁免几天（更严格），不会凭空造出收益。已在报告中声明。"""
    out: set[str] = set()
    for d in div_dirs:
        p = os.path.join(d, f"{code}.div.json")
        if not os.path.exists(p):
            continue
        try:
            arr = json.load(open(p, encoding="utf-8"))
        except Exception:      # noqa: BLE001
            continue
        for r in arr or []:
            ex = str(r.get("EX_DIVIDEND_DATE") or "")[:10]
            if ex:
                out.add(ex)
    return sorted(out)


def collect_needed(cells: dict[str, list[dict]]) -> dict[str, set[str]]:
    """收集五格里所有"需要判可成交性"的 (code, day)。"""
    need: dict[str, set[str]] = defaultdict(set)
    for rows in cells.values():
        for r in rows:
            for c in r["longs"]:
                need[c].add(r["entry"])
            for c in r["shorts"]:
                need[c].add(r["exit"])
    return need


def stream_bars(needed: dict[str, set[str]]) -> tuple[dict, dict, dict]:
    """逐只扫描 EXEC csv，只保留被检查的日期及其前一根 bar。

    返回 (bars[code][day] = {bar, prev, prev_date, idx}, ex_aligned[code] = set[交易日],
          span[code] = {first, last, n_days})

    `span` 用来区分两种截然不同的"当日无 bar"：
      - `day > last`   → 序列已终止（退市或数据截断，**两者不可区分**）
      - 否则           → 区间内缺失（真停牌）
    混成一个 `no_bar` 就是 D-1 事故的同构错误（把"未知"当成"不存在"）。

    除权日对齐规则与 `rebuild_adj_from_exec` 一致：取**第一个 >= ex_date 的交易日**。
    """
    bars: dict[str, dict] = {}
    ex_aligned: dict[str, set[str]] = {}
    span: dict[str, dict] = {}
    codes = sorted(needed)
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        path = os.path.join(EXEC_DIR, f"{code}.exec.csv")
        if not os.path.exists(path):
            bars[code] = {}
            span[code] = {"first": "", "last": "", "n_days": 0}
            continue
        want = needed[code]
        pending = load_ex_dates(code)
        aligned: set[str] = set()
        recs: dict[str, dict] = {}
        prev, prev_date, idx = None, None, -1
        first_date = None
        with open(path, encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            hdr = [h.strip().lower() for h in (next(rd, None) or [])]
            ix = {h: hdr.index(h) for h in ("date", "open", "high", "low", "close", "volume")
                  if h in hdr}
            if len(ix) < 6:
                bars[code] = {}
                span[code] = {"first": "", "last": "", "n_days": 0}
                continue
            ci, oi, hi, li, cli, vi = (ix["date"], ix["open"], ix["high"],
                                       ix["low"], ix["close"], ix["volume"])
            for row in rd:
                try:
                    d = row[ci][:10]
                    bar = (float(row[oi]), float(row[hi]), float(row[li]),
                           float(row[cli]), float(row[vi]))
                except (ValueError, IndexError):
                    continue
                idx += 1
                if first_date is None:
                    first_date = d
                while pending and d >= pending[0]:      # 除权日对齐到交易日
                    aligned.add(d)
                    pending.pop(0)
                if d in want:
                    recs[d] = {"bar": bar, "prev": prev, "prev_date": prev_date, "idx": idx}
                prev, prev_date = bar, d
        bars[code] = recs
        ex_aligned[code] = aligned
        span[code] = {"first": first_date or "", "last": prev_date or "", "n_days": idx + 1}
        if i % 1000 == 0:
            print(f"      扫描 {i}/{len(codes)} … {time.time()-t0:.0f}s")
    print(f"[C] EXEC 流式扫描完成 {len(codes)} 只  {time.time()-t0:.0f}s "
          f"（{sum(len(v) for v in bars.values())} 个检查点）")
    return bars, ex_aligned, span


def flags_fast(rec: dict | None, code: str, day: str, ex_set: set[str]) -> dict:
    """与 `TradabilityIndex.flags` **同语义**的轻量版（启动时交叉验证）。"""
    if rec is None:
        return {"tradable": False, "reason": "no_bar"}
    prev = rec["prev"]
    if prev is None:
        return {"tradable": False, "reason": "no_prev"}
    o, h, lo, c, _v = rec["bar"]
    pc = prev[3]
    if pc <= 0:
        return {"tradable": False, "reason": "bad_prev_close"}
    ret = c / pc - 1.0
    lim = limit_ratio(code, day)
    exempt = False
    if rec["idx"] < TradabilityIndex.WARMUP_DAYS:
        exempt = True
    else:
        try:
            gap = (dt.date.fromisoformat(day) - dt.date.fromisoformat(rec["prev_date"])).days
        except ValueError:
            gap = 1
        if gap > TradabilityIndex.RESUME_GAP_DAYS:
            exempt = True
        elif (day in ex_set) or (rec["prev_date"] in ex_set):
            exempt = True
    if exempt:
        return {"tradable": True, "reason": "exempt", "ret": ret, "limit": lim}
    tol = TradabilityIndex.TOL
    up = ret >= lim - tol
    down = ret <= -(lim - tol)
    one_word = (abs(o - h) < 1e-6 and abs(h - lo) < 1e-6 and abs(lo - c) < 1e-6)
    if one_word and (up or down):
        return {"tradable": False, "reason": "one_word_board", "ret": ret, "limit": lim,
                "dir": "up" if up else "down"}
    if up:
        return {"tradable": False, "reason": "limit_up", "ret": ret, "limit": lim, "dir": "up"}
    if down:
        return {"tradable": False, "reason": "limit_down", "ret": ret, "limit": lim, "dir": "down"}
    return {"tradable": True, "reason": "ok", "ret": ret, "limit": lim}


def cross_validate_flags(bars: dict, ex_aligned: dict, n_sample: int = 400) -> dict:
    """流式实现 vs 参考实现的一致性。**不做这一步就等于相信自己没写错。**"""
    ref = TradabilityIndex(EXEC_DIR)
    items = [(c, d) for c in sorted(bars) for d in sorted(bars[c])]
    if not items:
        return {"checked": 0, "bad": [], "note": "无可检查点"}
    rng = np.random.default_rng(20261021)
    sample = [items[i] for i in rng.choice(len(items), size=min(n_sample, len(items)),
                                           replace=False)]
    bad = []
    for code, day in sample:
        r = flags_fast(bars[code].get(day), code, day, ex_aligned.get(code, set()))
        q = ref.flags(code, day)
        same = (r["tradable"] == q["tradable"] and
                (r["tradable"] or r["reason"] == q["reason"]) and
                (r["tradable"] or "dir" not in q or r.get("dir") == q.get("dir")))
        if not same:
            bad.append({"code": code, "day": day, "fast": r, "ref": q})
    return {"checked": len(sample), "bad": bad[:20], "n_bad": len(bad)}


def _reason_of(f: dict, code: str, day: str, span: dict) -> str:
    """把 `no_bar` 拆成两种**性质完全不同**的情况。

    - `after_last_data`：该日已晚于该股最后一个行情日 → 退市或数据截断（**不可区分**）；
    - `no_bar`：区间内缺失 → 真停牌。

    两者都不可成交，但含义不同：前者是样本终止，后者是临时不可交易。
    合并计数会让"退市"消失在一个叫"停牌"的桶里——那就是 D-1 的老毛病。
    """
    if f["reason"] == "no_bar":
        last = (span.get(code) or {}).get("last") or ""
        if last and day > last:
            return "after_last_data"
    return f["reason"]


def apply_constraints(rows: list[dict], bars: dict, ex_aligned: dict,
                      span: dict | None = None) -> list[dict]:
    """§5：建仓日涨停/一字板(上) → 剔多头；平仓日跌停/一字板(下) → 剔空头。

    ⚠️ 严格按预登记字面执行：
      - 建仓日跌停**不**剔除（跌停可以买入）
      - 平仓日涨停**不**剔除（涨停可以卖出）
      - 停牌/序列终止 **不**剔除，只计数（§5 只对"信号日停牌"规定了不入池，
        建仓/平仓日停牌未被授权剔除；分解在诊断里报出，供后续预登记使用）
    """
    span = span or {}
    out = []
    for r in rows:
        dl, ds = defaultdict(int), defaultdict(int)
        keep_l, keep_s = [], []
        for c in r["longs"]:
            f = flags_fast(bars.get(c, {}).get(r["entry"]), c, r["entry"],
                           ex_aligned.get(c, set()))
            reason = _reason_of(f, c, r["entry"], span)
            blocked_up = (reason == "limit_up" or
                          (reason == "one_word_board" and f.get("dir") == "up"))
            if blocked_up:
                dl[reason] += 1
            else:
                keep_l.append(c)
                if not f["tradable"]:
                    dl[f"kept:{reason}"] += 1
        for c in r["shorts"]:
            f = flags_fast(bars.get(c, {}).get(r["exit"]), c, r["exit"],
                           ex_aligned.get(c, set()))
            reason = _reason_of(f, c, r["exit"], span)
            blocked_dn = (reason == "limit_down" or
                          (reason == "one_word_board" and f.get("dir") == "down"))
            if blocked_dn:
                ds[reason] += 1
            else:
                keep_s.append(c)
                if not f["tradable"]:
                    ds[f"kept:{reason}"] += 1
        rec = dict(r)
        rec["n_drop_long"] = sum(v for k, v in dl.items() if not k.startswith("kept:"))
        rec["n_drop_short"] = sum(v for k, v in ds.items() if not k.startswith("kept:"))
        rec["drop_long_reasons"] = {k: v for k, v in dl.items() if not k.startswith("kept:")}
        rec["drop_short_reasons"] = {k: v for k, v in ds.items() if not k.startswith("kept:")}
        rec["kept_blocked_long"] = {k[5:]: v for k, v in dl.items() if k.startswith("kept:")}
        rec["kept_blocked_short"] = {k[5:]: v for k, v in ds.items() if k.startswith("kept:")}
        f = r["fwd"]
        if keep_l and keep_s:
            rl = float(f.loc[keep_l].mean())
            rs = float(f.loc[keep_s].mean())
            rec.update({"longs_c": keep_l, "shorts_c": keep_s,
                        "r_long": rl, "r_short": rs, "d": 0.5 * (rl - rs)})
        else:
            rec.update({"longs_c": keep_l, "shorts_c": keep_s,
                        "r_long": float("nan"), "r_short": float("nan"), "d": float("nan")})
        out.append(rec)
    return out


# ==================== 统计 ====================

def stats_of(x: list[float]) -> dict:
    x = [v for v in x if v == v]
    n = len(x)
    if n < 2:
        return {"n": n}
    m = statistics.mean(x)
    sd = statistics.stdev(x)
    se = sd / math.sqrt(n)
    t = m / se if se > 0 else float("nan")
    return {"n": n, "mean_d": m, "mean_spread": 2 * m, "sd_d": sd, "se": se,
            "t_stat": t, "p_two_sided": _p2(t), "mde_pp": Z_MDE * se,
            "win_ratio": sum(1 for v in x if v > 0) / n,
            "min_d": min(x), "max_d": max(x)}


def _p2(t: float) -> float:
    if t != t:
        return float("nan")
    return math.erfc(abs(t) / math.sqrt(2))


def newey_west(x: list[float], lag: int = NW_LAG) -> dict:
    """均值的新颖估计量（Bartlett 核，滞后 lag）。用于配对差 Δ 的显著性。"""
    x = [v for v in x if v == v]
    n = len(x)
    if n < lag + 3:
        return {"n": n, "mean": float("nan"), "se_nw": float("nan"),
                "t_nw": float("nan"), "p": float("nan"), "n_eff": max(0, n - lag)}
    m = statistics.mean(x)
    e = [v - m for v in x]
    g0 = sum(v * v for v in e) / n
    s = g0
    for j in range(1, lag + 1):
        gj = sum(e[t] * e[t - j] for t in range(j, n)) / n
        s += 2.0 * (1.0 - j / (lag + 1)) * gj
    fell_back = False
    if s <= 0:                      # 小样本理论可能为负；退回 g0 并标记
        s, fell_back = g0, True
    se = math.sqrt(s / n)
    t = m / se if se > 0 else float("nan")
    return {"n": n, "mean": m, "mean_pp": m * 100, "se_nw": se, "se_nw_pp": se * 100,
            "t_nw": t, "p": _p2(t), "lag": lag, "n_eff": n - lag,
            "variance_fell_back_to_g0": fell_back}


def holm(pvals: dict[str, float], alpha: float = ALPHA) -> dict:
    """Holm-Bonferroni 校正（family = 传入的全部比较）。"""
    items = sorted(((k, v) for k, v in pvals.items() if v == v), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, dict] = {}
    still = True
    for i, (k, p) in enumerate(items):
        thr = alpha / (m - i)
        rej = bool(still and p <= thr)
        if not rej:
            still = False
        out[k] = {"p": p, "rank": i + 1, "threshold": thr, "reject": rej}
    for k, v in pvals.items():
        if v != v:
            out[k] = {"p": None, "rank": None, "threshold": None, "reject": False,
                      "note": "p 为 NaN（样本不足）"}
    return out


def pair_diff(rows_a: list[dict], rows_b: list[dict], key_d: str) -> tuple[list[str], list[float], list[float]]:
    """按 signal_date 配对，返回 (共同月份, Δ=2*(d_b-d_a), 各月 d_a/d_b 略)。"""
    ma = {r["signal_date"]: r for r in rows_a if r.get(key_d) == r.get(key_d)}
    mb = {r["signal_date"]: r for r in rows_b if r.get(key_d) == r.get(key_d)}
    common = sorted(set(ma) & set(mb))
    delta = [2.0 * (mb[k][key_d] - ma[k][key_d]) for k in common]
    return common, delta


def turnover_of(rows: list[dict], key_l: str = "longs_c", key_s: str = "shorts_c") -> float:
    """实测单边换手（权重漂移法，与 Gate 6 同口径）。"""
    tos, pl, ps_ = [], [], []
    for r in rows:
        f = r["fwd"]

        def leg(curr, prev):
            if not curr:
                return float("nan")
            wp = {c: 1.0 / len(prev) for c in prev} if prev else {}
            drift, tot = {}, 0.0
            for c, w in wp.items():
                v = f.get(c, float("nan"))
                if v != v:
                    continue
                drift[c] = w * (1 + v)
                tot += drift[c]
            if tot > 0:
                drift = {c: x / tot for c, x in drift.items()}
            wn = 1.0 / len(curr)
            return 0.5 * sum(abs((wn if c in curr else 0.0) - drift.get(c, 0.0))
                             for c in set(drift) | set(curr))
        tl, ts = leg(r[key_l], pl), leg(r[key_s], ps_)
        if tl == tl and ts == ts:
            tos.append(0.5 * tl + 0.5 * ts)
        pl, ps_ = r[key_l], r[key_s]
    tos = [x for i, x in enumerate(tos) if i > 0]
    return statistics.mean(tos) if tos else float("nan")


def cost_of(to: float, mean_d: float, sd_d: float, n: int, rate: float) -> dict:
    """月度成本与成本后收益。

    ⚠️ 口径：**月度成本 = 2 × 单边换手 × 费率**；预登记 §7 写的
    `2 × 单边换手 × 12 × 费率` 是**年化**，年化值单独放在 `cost_pp_year`。
    第一版把年化当月度扣，D 格月成本被算成 1.93pp（正确 0.161pp），
    成本后收益因此全部大幅为负。`test_cost_is_monthly_not_annualized` 用 Gate 6 的
    实测值把这个口径钉死。
    """
    cm = 2.0 * to * rate * 100.0
    net = mean_d * 100.0 - cm
    t = net / (sd_d * 100.0 / math.sqrt(n)) if (sd_d > 0 and n > 0) else float("nan")
    return {"cost_pp_month": cm, "cost_pp_year": cm * 12.0, "net_pp_month": net, "t_net": t}


def judge_pool_effect(nw: dict, delta: list[float]) -> dict:
    """§3.2 三档 + 运行前补充的第四档。"""
    n = nw["n"]
    n_eff = nw["n_eff"]
    t = nw["t_nw"]
    mean_pp = abs(nw["mean_pp"]) if nw["mean_pp"] == nw["mean_pp"] else float("nan")
    hits = []
    if n < MIN_N_MAIN:
        hits.append(f"n={n} < {MIN_N_MAIN}")
    if n_eff < MIN_NW_EFF:
        hits.append(f"NW 有效样本 {n_eff} < {MIN_NW_EFF}")
    if hits:
        return {"verdict": "INCONCLUSIVE", "rule_hits": hits}
    sig = abs(t) >= T_CRIT
    big = mean_pp >= ECON_MIN_PP
    if sig and big:
        direction = "池效应为正（全 A 池的 spread 更大）" if nw["mean"] > 0 else "池效应为负（全 A 池的 spread 更小）"
        return {"verdict": "POOL_EFFECT_CONFIRMED",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                              f"|mean|={mean_pp:.4f}pp >= {ECON_MIN_PP}pp", direction],
                "direction": direction}
    if not sig:
        return {"verdict": "POOL_EFFECT_NOT_DETECTED",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} < {T_CRIT}",
                              f"|mean|={mean_pp:.4f}pp（未达 {ECON_MIN_PP}pp 亦不影响本档）"]}
    # 运行前补充：统计显著但未达经济门槛
    return {"verdict": "POOL_EFFECT_SIGNIFICANT_BUT_SMALL",
            "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                          f"|mean|={mean_pp:.4f}pp < {ECON_MIN_PP}pp（预先写死的经济门槛）"],
            "note": "预登记 §3.2 三档未覆盖此情形，运行前补充第四档，不改动原三档。"}


# ==================== 主流程 ====================

CELLS = [
    # (tag, pool, min_listed, k_mode, 说明)
    ("A0", "518", 0, "fixed", "518 只 · 无 252 日门槛 · 固定 k=110（Gate 3 真实口径）"),
    ("A", "518", MIN_LISTED_DAYS, "fixed", "518 只 · 252 日门槛 · 固定 k=110（统一口径基线）"),
    ("B", "all", MIN_LISTED_DAYS, "fixed", "as-of 全 A · 252 日 · 固定 k=110（池效应终点）"),
    ("C", "518", MIN_LISTED_DAYS, "frac", "518 只 · 252 日 · 比例 20%（构造效应起点）"),
    ("D", "all", MIN_LISTED_DAYS, "frac", "as-of 全 A · 252 日 · 比例 20%（与 Gate 6 对齐）"),
]

# 辅助检验定义（预登记 §4，五个比较里 S1 = 主检验故不重复）。
# ⚠️ 元组顺序是 (id, FROM, TO, label)，且 **Δ = spread(TO) − spread(FROM)**。
#    第一版写成 (id, hi, lo) 并把 hi 放在第一位，导致 S3~S5 的符号与标签相反
#    （S3 报 +0.57，实际是 D 比 B 低 0.57）。`test_aux_defs_direction_consistent_with_label`
#    用正则从 label 里反解方向来钉死这个约定。
AUX_DEFS = (
    ("S2", "A0", "A", "门槛效应：spread(A) − spread(A0)"),
    ("S3", "B", "D", "构造效应：spread(D) − spread(B)"),
    ("S4", "A", "C", "构造效应：spread(C) − spread(A)"),
    ("S5", "A0", "C", "构造效应：spread(C) − spread(A0)"),
)


def main() -> int:
    t_start = time.time()

    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}
    print("=" * 78)
    print("阶段 C：股票池效应 vs 构造效应 vs 入池门槛（五格交叉）")
    print(f"预登记 {PREREG_SHA256[:16]}  窗口 {START} ~ {END_SIGNAL}  NW 滞后 {NW_LAG}")
    print("=" * 78)
    vi = verify()
    print()

    p518 = pool_518()
    close_all = load_close()
    cal = close_all.index

    missing_518 = [c for c in p518 if c not in close_all.columns]
    if missing_518:
        raise SystemExit(f"518 池中有 {len(missing_518)} 只在 ADJ 缺失：{missing_518[:5]}")
    close_518 = close_all[p518]          # 列子集，**保留同一行索引**

    panels_all = Panels(close_all)
    panels_518 = Panels(close_518)

    positions = month_end_positions(cal, START, END_HOLD)
    print(f"[C] 调仓点 {len(positions)} 个（{len(positions)-1} 个月度区间）")

    raw_rows: dict[str, list[dict]] = {}
    for tag, pool, mld, kmode, desc in CELLS:
        t0 = time.time()
        panels = panels_518 if pool == "518" else panels_all
        mask = build_mask(panels.close, mld)
        rows = panels.month_rows(mask, positions, kmode)
        raw_rows[tag] = rows
        print(f"    格 {tag:<2} 选股完成 {len(rows):>3} 月  "
              f"n_pool 中位 {statistics.median(r['n_pool'] for r in rows):>5.0f}  "
              f"k 中位 {statistics.median(r['k_used'] for r in rows):>4.0f}  {time.time()-t0:.1f}s")

    # ---- 交易约束
    print()
    print("[C] 构建可成交性检查（流式扫描 EXEC）")
    need = collect_needed(raw_rows)
    print(f"    需检查 {len(need)} 只股票 · {sum(len(v) for v in need.values())} 个 (股票,日期) 点")
    bars, ex_aligned, span = stream_bars(need)

    print("[C] 交叉验证：流式实现 vs TradabilityIndex 参考实现")
    cv = cross_validate_flags(bars, ex_aligned)
    print(f"    抽样 {cv['checked']} 点，不一致 {cv.get('n_bad', 0)}")
    if cv.get("n_bad", 0):
        for b in cv["bad"][:5]:
            print(f"      ✗ {b}")
        raise SystemExit("可成交性实现与参考实现不一致，拒绝继续（先修实现，不许放宽判据）")

    cells: dict[str, list[dict]] = {}
    for tag, *_ in CELLS:
        t0 = time.time()
        cells[tag] = apply_constraints(raw_rows[tag], bars, ex_aligned, span)
        n_dl = sum(r["n_drop_long"] for r in cells[tag])
        n_ds = sum(r["n_drop_short"] for r in cells[tag])
        print(f"    格 {tag:<2} 约束应用完成  剔多头 {n_dl} 只次 / 剔空头 {n_ds} 只次  "
              f"{time.time()-t0:.1f}s")

    # ---- 统计
    print()
    print("=" * 78)
    print("五格统计（含约束 = 主口径；无约束 = 仅复现校验，不参与判定）")
    print("=" * 78)
    cell_stats: dict[str, dict] = {}
    print(f"{'格':<3}{'n':>4}{'d含约束':>11}{'spread':>10}{'t':>8}"
          f"{'d无约束':>11}{'spread':>10}{'t':>8}{'门槛预期':>10}")
    expect = {"A0": 0.4598, "A": 0.4450, "B": 1.000, "C": None, "D": 0.7084}
    for tag, pool, mld, kmode, desc in CELLS:
        rows = cells[tag]
        sc = stats_of([r["d"] for r in rows])
        sr = stats_of([r["d_raw"] for r in raw_rows[tag]])
        cell_stats[tag] = {"desc": desc, "pool": pool, "min_listed_days": mld, "k_mode": kmode,
                           "constrained": sc, "raw": sr,
                           "n_drop_long_total": sum(r["n_drop_long"] for r in rows),
                           "n_drop_short_total": sum(r["n_drop_short"] for r in rows)}
        e = expect.get(tag)
        e_s = f"{e:+.4f}" if e is not None else "—"
        print(f"{tag:<3}{sc['n']:>4}{sc['mean_d']*100:>+11.4f}{sc['mean_spread']*100:>10.4f}"
              f"{sc['t_stat']:>+8.3f}{sr['mean_d']*100:>+11.4f}{sr['mean_spread']*100:>10.4f}"
              f"{sr['t_stat']:>+8.3f}{e_s:>10}")

    print()
    print("  复现校验（无约束 mean(d) 与预登记 §2 预期值的差）：")
    repro: dict[str, dict] = {}
    for tag in ("A0", "A", "B", "D"):
        e = expect[tag]
        if e is None:
            continue
        got = cell_stats[tag]["raw"]["mean_d"] * 100
        tol = max(0.005, 0.01 * abs(e))     # B 格预登记只给了约值 1.000，故按 1% 相对容差
        ok = abs(got - e) <= tol
        repro[tag] = {"expected_raw_mean_d_pp": e, "actual_raw_mean_d_pp": round(got, 6),
                      "diff_pp": round(got - e, 6), "tolerance_pp": round(tol, 6), "within": ok}
        print(f"    {tag:<3} 实测 {got:+.4f}pp  预期 {e:+.4f}pp  差 {got-e:+.4f}pp"
              f"（容差 ±{tol:.4f}）" + ("  ✓" if ok else "  ⚠ 需说明"))

    # ---- 窗口归因（A0/A 的预期值口径与统一窗口不同）
    print()
    print("  ⚠ 预登记 §2 的 A0/A 预期值（+0.4598 / +0.4450）来自 Gate 3 的 **2007-2015** 窗口，")
    print("    而 §1 写死的统一窗口是 **2007-2026**。两者不是同一个东西。验证如下：")
    window_attr: dict[str, dict] = {}
    for tag, exp in (("A0", 0.4598), ("A", 0.4450)):
        sub = [r for r in raw_rows[tag] if r["signal_date"] <= "2015-12-31"]
        s = stats_of([r["d_raw"] for r in sub])
        got = s["mean_d"] * 100
        ok = abs(got - exp) <= 0.005
        window_attr[tag] = {"sub_window": ["2007-01-01", "2015-12-31"], "n": s["n"],
                            "actual_raw_mean_d_pp": round(got, 6), "expected_pp": exp,
                            "diff_pp": round(got - exp, 6), "within": ok}
        print(f"    {tag:<3} 2007-2015 实测 {got:+.4f}pp  预期 {exp:+.4f}pp  差 {got-exp:+.4f}pp  "
              f"n={s['n']}" + ("  ✓ 口径确认：偏差全部来自窗口" if ok else "  ⚠ 仍未复现，需再查"))

    # ---- 分段（主检验的 Δ 会吸收时间交互，必须单独报）
    SEGS = (("2007-01-01", "2015-12-31", "2007-2015"), ("2016-01-01", "2026-09-30", "2016-2026"))
    print()
    print("分段描述（不参与判定）：含约束 mean(d) pp/月 [n 月]")
    seg_stats: dict[str, dict] = {}
    for tag, *_ in CELLS:
        parts = []
        seg_stats[tag] = {}
        for lo, hi, lab in SEGS:
            s = stats_of([r["d"] for r in cells[tag] if lo <= r["signal_date"] <= hi])
            seg_stats[tag][lab] = clean(s)
            parts.append(f"{lab} {s['mean_d']*100:+7.4f}[{s['n']:>3}]")
        print(f"    {tag:<3} " + "   ".join(parts))

    print()
    print(f"分段 Δ 与 Δ'（描述性）：")
    seg_delta: dict[str, dict] = {}
    for lo, hi, lab in SEGS:
        row = {}
        for name, (ka, kb) in (("Δ=A→B", ("A", "B")), ("Δ'=C→D", ("C", "D"))):
            ra = [r for r in cells[ka] if lo <= r["signal_date"] <= hi]
            rb = [r for r in cells[kb] if lo <= r["signal_date"] <= hi]
            _cm, dl = pair_diff(ra, rb, "d")
            r = newey_west(dl, NW_LAG)
            row[name] = clean(r)
            print(f"    {lab}  {name:<8} Δ={r['mean_pp']:+8.4f}pp  NW t={r['t_nw']:+6.3f}  n={r['n']}")
        seg_delta[lab] = row

    # ---- 主检验
    print()
    print("=" * 78)
    print("主检验（唯一）：Δ_t = spread(B) − spread(A) ，仅含约束口径")
    print("=" * 78)
    common, delta = pair_diff(cells["A"], cells["B"], "d")
    nw = newey_west(delta, NW_LAG)
    verdict = judge_pool_effect(nw, delta)
    print(f"  配对月份 {len(common)}（A/B 共同观测）  NW 有效样本 {nw['n_eff']}")
    print(f"  mean(Δ) = {nw['mean_pp']:+.4f} pp/月    spread 差同值（Δ 已含 ×2）")
    print(f"  NW se   = {nw['se_nw_pp']:.4f} pp（滞后 {NW_LAG}）")
    print(f"  NW t    = {nw['t_nw']:+.4f}   p = {nw['p']:.4f}")
    print(f"  判据：n ≥ {MIN_N_MAIN} 且 |t| ≥ {T_CRIT} 且 |mean| ≥ {ECON_MIN_PP}pp")
    print(f"  >>> 主判定：{verdict['verdict']}")
    for h in verdict["rule_hits"]:
        print(f"      · {h}")
    if nw.get("variance_fell_back_to_g0"):
        print("      ⚠ NW 方差为负，已退回 γ0（记录在案）")

    # ---- 交叉验证
    print()
    print("交叉验证（必报，不参与判定）：Δ'_t = spread(D) − spread(C)")
    common2, delta2 = pair_diff(cells["C"], cells["D"], "d")
    nw2 = newey_west(delta2, NW_LAG)
    print(f"  配对月份 {len(common2)}  mean(Δ') = {nw2['mean_pp']:+.4f} pp  "
          f"NW t = {nw2['t_nw']:+.4f}  p = {nw2['p']:.4f}")
    same_sign = (nw["mean"] * nw2["mean"] > 0) if (nw["mean"] == nw["mean"] and
                                                   nw2["mean"] == nw2["mean"]) else None
    if same_sign is True:
        cross_note = "两口径一致（同号）"
    elif same_sign is False:
        cross_note = "两口径异号 → 主结论降级为 EVIDENCE_CONFLICT"
        verdict = {"verdict": "EVIDENCE_CONFLICT",
                   "rule_hits": [f"主检验 {nw['mean_pp']:+.4f}pp vs 交叉 {nw2['mean_pp']:+.4f}pp 异号",
                                 "两级结果并列报告，不得只报一个"],
                   "original_verdict": verdict["verdict"]}
    else:
        cross_note = "无法判断（数值缺失）"
    print(f"  >>> {cross_note}")

    # ---- 辅助检验 Holm
    print()
    print("=" * 78)
    print("辅助检验（探索性，Holm 校正 α=0.05，family=4，不构成证据）")
    print("=" * 78)
    aux: dict[str, dict] = {}
    pv: dict[str, float] = {}
    for sid, frm, to, label in AUX_DEFS:
        cm, dl = pair_diff(cells[frm], cells[to], "d")     # Δ = 2*(d_to - d_from)
        r = newey_west(dl, NW_LAG)
        aux[sid] = {"label": label, "from": frm, "to": to, "n_pairs": len(cm), **r}
        pv[sid] = r["p"]
    hol = holm(pv)
    for sid, frm, to, label in AUX_DEFS:
        r = aux[sid]
        h = hol[sid]
        print(f"  {sid}  {label:<38} n={r['n']:>3}  "
              f"Δ={r['mean_pp']:+7.4f}pp  NW t={r['t_nw']:+6.3f}  "
              f"p={r['p']:.4f}  Holm 阈值={h['threshold']:.4f}  "
              f"{'拒原假设' if h['reject'] else '不拒绝'}")
    print("  ⚠ 全部为探索性结果，不构成证据；禁止事后把任何 S 升格为主检验。")

    # ---- 约束影响诊断
    print()
    print("=" * 78)
    print("约束影响诊断（主口径 vs 无约束）")
    print("=" * 78)
    print(f"{'格':<3}{'Δmean(pp)':>12}{'Δ%':>9}{'剔多头':>9}{'剔空头':>9}")
    constraint_effect = {}
    for tag, *_ in CELLS:
        a = cell_stats[tag]["constrained"]["mean_d"]
        b = cell_stats[tag]["raw"]["mean_d"]
        d = (a - b) * 100
        pct = (a - b) / b * 100 if b else float("nan")
        constraint_effect[tag] = {"delta_pp": round(d, 6), "delta_pct": round(pct, 4),
                                  "n_drop_long": cell_stats[tag]["n_drop_long_total"],
                                  "n_drop_short": cell_stats[tag]["n_drop_short_total"]}
        print(f"{tag:<3}{d:>+12.4f}{pct:>+9.2f}%"
              f"{cell_stats[tag]['n_drop_long_total']:>9}{cell_stats[tag]['n_drop_short_total']:>9}")

    # 被保留的阻塞（未授权剔除的）——单独报，供后续预登记
    kept = defaultdict(int)
    for tag in cells:
        for r in cells[tag]:
            for k, v in r["kept_blocked_long"].items():
                kept[f"long:{k}"] += v
            for k, v in r["kept_blocked_short"].items():
                kept[f"short:{k}"] += v
    if kept:
        print()
        print("  ⚠ 未剔但确实不可成交（§5 未授权剔除，仅计数）：")
        for k, v in sorted(kept.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<22} {v:>6} 只次")
        print("  说明：`after_last_data` = 该日已晚于该股最后一个行情日（退市或数据截断，")
        print("        两者不可区分）；`no_bar` = 区间内缺失（真停牌）。**分开计数，不合并。**")
        print("        §5 只规定'信号日停牌不入池'，建仓/平仓日停牌未被授权剔除，")
        print("        故这里只计数不改结果——这是预登记的缺口，需下一轮预登记补。")

    # ---- 池规模漂移
    print()
    pool_drift = {}
    for tag, *_ in CELLS:
        rows = cells[tag]
        vals = [r["n_pool"] for r in rows]
        jumps = []
        for i in range(1, len(vals)):
            if vals[i - 1] > 0 and abs(vals[i] / vals[i - 1] - 1) > 0.30:
                jumps.append({"signal_date": rows[i]["signal_date"],
                              "prev": vals[i - 1], "cur": vals[i]})
        kvals = [r["k_used"] for r in rows]
        pool_drift[tag] = {"n_pool_first": vals[0], "n_pool_last": vals[-1],
                           "n_pool_median": statistics.median(vals),
                           "k_first": kvals[0], "k_last": kvals[-1],
                           "n_jump_gt30pct": len(jumps), "jumps": jumps[:5]}
        print(f"  {tag:<3} 池 {vals[0]}→{vals[-1]}（中位 {statistics.median(vals):.0f}）  "
              f"k {kvals[0]}→{kvals[-1]}  月度跳变>30% 的月份 {len(jumps)} 个")

    # ---- 换手与成本
    print()
    print("=" * 78)
    print("换手与成本（实测换手；月度成本 = 2 × 单边换手 × 费率，年化 = 月度 × 12）")
    print("=" * 78)
    turnover_cost = {}
    for tag, *_ in CELLS:
        to = turnover_of(cells[tag])
        st = cell_stats[tag]["constrained"]
        tiers = {}
        for c in COST_TIERS:
            r = cost_of(to, st["mean_d"], st["sd_d"], st["n"], c)
            tiers[f"{c*100:.1f}pct"] = {k: round(v, 4) for k, v in r.items()}
        turnover_cost[tag] = {"one_side_per_month": round(to, 6), "cost": tiers}
        print(f"  {tag:<3} 单边 {to*100:5.1f}%/月  净@0.1% {tiers['0.1pct']['net_pp_month']:+.4f}pp"
              f"(t={tiers['0.1pct']['t_net']:+.2f})  净@0.3% {tiers['0.3pct']['net_pp_month']:+.4f}pp"
              f"(t={tiers['0.3pct']['t_net']:+.2f})")
    print("  注：月度成本 = 2 × 单边换手 × 费率；预登记 §7 的 ×12 是年化，已在 cost_pp_year 报告。")

    # ---- 分年度（描述性）
    yearly = {}
    for tag in cells:
        by: dict[str, list[float]] = defaultdict(list)
        for r in cells[tag]:
            if r["d"] == r["d"]:
                by[r["signal_date"][:4]].append(r["d"])
        yearly[tag] = {y: {"n": len(v), "mean_d_pp": round(statistics.mean(v) * 100, 4)}
                       for y, v in sorted(by.items())}

    # ---- 写盘
    os.makedirs(OUT_DIR, exist_ok=True)

    with open(os.path.join(OUT_DIR, "five_cells.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("cell,signal_date,month,n_pool,k_used,k_frac,n_long,n_short,"
                "n_drop_long,n_drop_short,r_long,r_short,d,d_raw\n")
        for tag, *_ in CELLS:
            for r in cells[tag]:
                f.write(f"{tag},{r['signal_date']},{r['month']},{r['n_pool']},{r['k_used']},"
                        f"{r['k_frac']},{len(r['longs_c'])},{len(r['shorts_c'])},"
                        f"{r['n_drop_long']},{r['n_drop_short']},"
                        f"{r['r_long']:.10f},{r['r_short']:.10f},{r['d']:.10f},{r['d_raw']:.10f}\n")

    with open(os.path.join(OUT_DIR, "paired_diffs.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("kind,signal_date,d_low,d_high,spread_low,spread_high,delta_spread\n")
        ma, mb = {r["signal_date"]: r for r in cells["A"]}, {r["signal_date"]: r for r in cells["B"]}
        for k in common:
            da, db = ma[k]["d"], mb[k]["d"]
            f.write(f"main_A_to_B,{k},{da:.10f},{db:.10f},{2*da:.10f},{2*db:.10f},{2*(db-da):.10f}\n")
        mc, md = {r["signal_date"]: r for r in cells["C"]}, {r["signal_date"]: r for r in cells["D"]}
        for k in common2:
            dc, dd = mc[k]["d"], md[k]["d"]
            f.write(f"cross_C_to_D,{k},{dc:.10f},{dd:.10f},{2*dc:.10f},{2*dd:.10f},{2*(dd-dc):.10f}\n")

    cells_json = {}
    for tag, pool, mld, kmode, desc in CELLS:
        cs = cell_stats[tag]
        cells_json[tag] = {
            "desc": desc, "pool": pool, "min_listed_days": mld, "k_mode": kmode,
            "constrained": clean(cs["constrained"]),
            "raw": clean(cs["raw"]),
            "n_drop_long_total": cs["n_drop_long_total"],
            "n_drop_short_total": cs["n_drop_short_total"],
            "turnover_cost": turnover_cost[tag],
            "pool_drift": pool_drift[tag],
            "yearly_descriptive": yearly[tag],
        }

    summary = {
        "experiment": "phasec-universe-attribution-v1.1",
        "prereg_sha256": PREREG_SHA256,
        "prereg_version_note": "v1.0→v1.1：原格 E 与格 A 语义必然同值，改为格 A0（无 252 日门槛）",
        "window": {"start": START, "end_signal": END_SIGNAL, "end_hold": END_HOLD},
        "params": {"lookback": LOOKBACK, "skip": SKIP, "min_valid_days": MIN_VALID_DAYS,
                   "min_listed_days": MIN_LISTED_DAYS, "k_fixed": K_FIXED, "k_frac": K_FRAC,
                   "min_pool": MIN_POOL, "nw_lag": NW_LAG,
                   "min_n": MIN_N_MAIN, "min_nw_eff": MIN_NW_EFF,
                   "t_crit": T_CRIT, "econ_min_pp": ECON_MIN_PP},
        "prerun_clarifications": [
            "歧义1（主检验口径）：主检验取含约束；无约束套仅作复现校验，不参与判定。"
            "理由：§5 声明约束为本实验新增，§8.2 明确预期约束会压低效应。决定写在运行前。",
            "歧义2（判据缺口）：n≥150 且 |t|≥1.96 且 |mean|<0.30pp 三档均不匹配，"
            "运行前补充第四档 POOL_EFFECT_SIGNIFICANT_BUT_SMALL，不改动原三档条件。",
            "歧义3（建仓/平仓日停牌）：§5 只规定信号日停牌不入池，未授权在建仓/平仓日剔除停牌股。"
            "本脚本按字面执行（不剔除），并把这类点单独计数报告，供后续预登记。",
        ],
        "postrun_corrections": [
            "成本口径（第一版写错，已修，修复前产物保留在 phasec_out/<sha>_pre_costfix/）："
            "预登记 §7 的 `2 × 单边换手 × 12 × 费率` 中 ×12 是**年化**，第一版把它当月度扣减，"
            "D 格月成本被算成 1.9326pp（正确 0.1610pp），成本后收益全部大幅为负。"
            "现按 Gate 6 口径：月度成本 = 2 × to × 费率，年化另列 cost_pp_year。"
            "判据不受影响（成本不参与判定，§7 已声明），但成本后的解读必须用修正值。",
            "辅助检验方向（第一版写错，已修，修复前产物保留在 phasec_out/<sha>_pre_auxdirfix/）："
            "AUX_DEFS 原按 (id, hi, lo) 传参，而 pair_diff 的 Δ 恒为 2*(第二个参数−第一个参数)，"
            "所以 S3~S5 的实际方向与 label 相反（S3 报 +0.5703，真相是 spread(D) 比 spread(B) 低 0.5703）。"
            "现已改为 (id, FROM, TO) 并把标签解析式写成单测。"
            "辅助检验属探索性、不构成证据，故主判定与交叉验证不受影响。",
            "预登记 §2 的 A0/A 预期值与 §1 统一窗口不同源（见 expectation_window_attribution）。"
            "预登记是运行前冻结的，故不改预登记、不改窗口，只在此登记并给出 2007-2015 子窗口实测："
            "A0 +0.4598（预期 +0.4598，差 −0.0000）、A +0.4450（预期 +0.4450，差 −0.0000），逐位复现。",
        ],
        "verify": vi,
        "n_symbols_adj": int(close_all.shape[1]),
        "n_symbols_pool518": len(p518),
        "n_months": len(positions) - 1,
        "cells": cells_json,
        "main_test": {"comparison": "spread(B) - spread(A)", "definition": "Δ_t = 2*(d_B - d_A)",
                      "n_pairs": len(common), **clean(nw), "judgement": verdict},
        "cross_validation": {"comparison": "spread(D) - spread(C)", "n_pairs": len(common2),
                             **clean(nw2), "same_sign_as_main": same_sign, "reading": cross_note},
        "auxiliary_holm": {"alpha": ALPHA, "family_size": 4,
                           "results": {sid: {"label": aux[sid]["label"],
                                             "from": aux[sid]["from"], "to": aux[sid]["to"],
                                             **clean({k: aux[sid][k] for k in
                                                      ("n", "mean_pp", "t_nw", "p", "se_nw_pp")}),
                                             "holm": hol[sid]} for sid, *_ in AUX_DEFS},
                           "note": "全部为探索性，不构成证据；禁止事后升格为主检验"},
        "constraint_effect_vs_raw": constraint_effect,
        "kept_blocked_not_removed": dict(kept),
        "expectation_check": repro,
        "expectation_window_attribution": {
            "issue": "预登记 §2 的 A0/A 预期值来自 Gate 3 的 2007-2015 窗口，§1 的统一窗口是 2007-2026；"
                     "两者口径不同，A0/A 在全窗口下必然不等于该预期值。B/D 的预期值来自 Gate 6（同窗口），可逐位复现。",
            "detail": window_attr,
        },
        "segment_descriptive": {"cell_mean_d": seg_stats, "paired_delta": seg_delta,
                                "note": "描述性。主检验 Δ 是全样本平均，会吸收'池×时间'交互，"
                                        "故必须同时报分段值，不得把 Δ 全归因为池规模。"},
        "limitations": [
            "ST 无历史标记 → 5% 涨跌幅未区分，会漏判 ST 涨跌停（已知限制，不假装解决）",
            "做空腿不可得（M4 结构阻塞）→ d 是多空相对强弱，不是可实现组合收益",
            "冲击成本未建模",
            "持有期内退市按最后价估值（沿用 Gate 6 口径，未新增假设）",
            "2005-2006 不可用于跨期比较（左截断，2005 年末仅 6 只可投资）",
        ],
        "note": "不得与 Gate 3 的 +0.4841 直接相减（口径不同）；不得把五格相减当作多次独立证据。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    files = ["five_cells.csv", "paired_diffs.csv", "summary.json"]
    manifest = {
        "experiment": "phasec-universe-attribution-v1.1",
        "prereg_sha256": PREREG_SHA256,
        # 记录脚本自身哈希：产物与代码的对应关系必须可核，否则"可复现"只是口号
        "script_sha256": _sha256_file(os.path.abspath(__file__)),
        "data": {"adj_dir": ADJ_DIR, "exec_dir": EXEC_DIR,
                 "n_adj": int(close_all.shape[1]), "n_exec_acceptance": vi["exec_acceptance"]},
        "files": {n: {"sha256": _sha256_file(os.path.join(OUT_DIR, n)),
                      "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files},
        "runtime_sec": round(time.time() - t_start, 1),
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print()
    print("=" * 78)
    print(f"产物 -> {OUT_DIR}/")
    print(f"主判定：{verdict['verdict']}   交叉验证：{cross_note}")
    print(f"总耗时 {time.time()-t_start:.0f}s")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
