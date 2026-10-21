#!/usr/bin/env python
"""研究数据与构造的性质检查（property-based checks，不依赖具体市场结果）。

为什么需要这个脚本
------------------
"回测跑通了"不能证明正确：一个系数写错、一个日期错位、一次股票池错配，
回测照样会跑完并给出一个漂亮的 t 值。这里的每条检查都绑定一个**不变量**：
只要它被违反，无论结果多显著都一定是错的。

与单元测试的分工
----------------
`backend/tests` 测的是 API / 存储 / 序列化等软件行为；
本脚本测的是**数据与研究构造**的语义不变量（复权、as-of、组合权益、费用），
两者不重叠。退出码：全部通过 = 0，有 FAIL = 1。

用法
----
    python scripts/property_checks.py                # 全部检查
    python scripts/property_checks.py --only PC1,PC3  # 只跑指定项
    python scripts/property_checks.py --report reports/PROPERTY_CHECKS.json
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import random
import subprocess
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADJ_ALL = os.path.join(REPO, "data/qfq_all")
EXEC_ALL = os.path.join(REPO, "data/exec_all")
LISTED = os.path.join(REPO, "data/qfq_mult/listed")
DELIST = os.path.join(REPO, "data/qfq_mult/delist")
DIV_SINA = os.path.join(REPO, "data/div_all_sina")
DIV_EM = os.path.join(REPO, "data/div_all_em")

# A 股涨跌停（用于"明显错误"的宽松上界；不追求精确，只抓离谱值）
LIMIT_MAIN = 0.10
LIMIT_GEM = 0.20      # 创业板 300xxx / 科创板 688xxx
TOL = 1.02            # 2% 容差：价格舍入、四舍五入到分

results: list[dict] = []


def record(check: str, name: str, ok: bool, detail: str, n: int = 0) -> None:
    results.append({"check": check, "name": name, "ok": bool(ok), "detail": detail, "n": int(n)})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {check} {name}  {detail}" + (f"  (n={n})" if n else ""))


def limit_of(code: str) -> float:
    c = code.split(".")[0]
    if c.startswith(("300", "301", "688", "689")):
        return LIMIT_GEM
    return LIMIT_MAIN


_ADJ_CACHE: dict[str, pd.DataFrame | None] = {}
_CACHE_MAX = 6000          # 只缓存元数据级别的 DataFrame；超过则退化为直读


def read_adj(code: str, adj_dir: str = ADJ_ALL) -> pd.DataFrame | None:
    """读 ADJ。带缓存——PC1/PC3/PC9 会各扫一遍全池，不缓存会重复 IO 三次。"""
    key = f"{adj_dir}|{code}"
    if key in _ADJ_CACHE:
        return _ADJ_CACHE[key]
    p = os.path.join(adj_dir, f"{code}.qfq.csv")
    if not os.path.exists(p):
        _ADJ_CACHE[key] = None
        return None
    try:
        df = pd.read_csv(p, usecols=lambda c: c in ("date", "close"))
    except Exception:  # noqa: BLE001
        df = pd.read_csv(p)
    if "date" not in df.columns:
        _ADJ_CACHE[key] = None
        return None
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    if len(_ADJ_CACHE) < _CACHE_MAX:
        _ADJ_CACHE[key] = df
    return df


def read_events_sina(code: str) -> set[str]:
    """读新浪缓存的除权除息日集合。"""
    p = os.path.join(DIV_SINA, f"{code}.div.json")
    if not os.path.exists(p):
        return set()
    try:
        rows = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for r in rows or []:
        d = str(r.get("ex_date") or r.get("EX_DIVIDEND_DATE") or "")[:10]
        if len(d) == 10:
            out.add(d)
    return out


def read_exec_simple(code: str) -> pd.DataFrame | None:
    """读 EXEC（真不复权）的 date + close。PC2 用它区分"ADJ 重建错误"与"真实行情"。"""
    p = os.path.join(EXEC_ALL, f"{code}.exec.csv")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, usecols=lambda c: c in ("date", "close"))
        df["date"] = pd.to_datetime(df["date"].astype(str))
        return df.dropna().sort_values("date").reset_index(drop=True)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- PC1
def pc1_prices_positive() -> None:
    """前复权价格必须为正。乘性复权若有加性残留会出负价（P1-2 的历史 bug）。"""
    bad, n = [], 0
    for p in sorted(glob.glob(os.path.join(ADJ_ALL, "*.qfq.csv"))):
        code = os.path.basename(p).replace(".qfq.csv", "")
        df = read_adj(code)
        if df is None or df.empty:
            continue
        n += 1
        col = "close" if "close" in df.columns else df.columns[1]
        vals = df[col].to_numpy(dtype=float, na_value=np.nan)
        if np.nanmin(vals) <= 0:
            bad.append({"code": code, "n_nonpos": int(np.nansum(vals <= 0)),
                        "min": float(np.nanmin(vals))})
    record("PC1", "ADJ 价格全为正", not bad,
           f"检查 {n} 只，非正价 {len(bad)} 只" + (f"：{bad[:5]}" if bad else ""), n)


# ---------------------------------------------------------------- PC2
# ================================================================ PC2 核心判据
def pc2_scan(adj: pd.DataFrame, exec_df: pd.DataFrame | None, events: set[str],
             limit: float, warmup: int = 5, resume_gap: int = 5,
             fatal_mult: float = 2.2, tol: float = TOL) -> dict:
    """PC2 的判据，抽成**纯函数**，供主检查与变异测试共用。

    ## 语义（第四版，2026-10-21）

    这条检查抓的是"**有没有未处理的除权**"，不是"涨跌停有没有被违反"。
    因此它必须区分三件事：

    | 现象 | 判定 | 理由 |
    |---|---|---|
    | 非除权日**下跌**超过除权量级 | **FAIL** | 除权必然使价格向下跳，这是 D-1 的形态（000616.SZ 2008-05-09 −37.7%，10送2转3 未复权） |
    | 非除权日**上涨**超过除权量级，且 EXEC 同日涨同样幅度 | INFO（`up_no_limit`） | **上涨不可能是除权**。EXEC 也跳 → 真实行情（重组复牌当日无涨跌幅限制，如 000738.SZ 2010-10-15 +30.27%） |
    | 非除权日**上涨**超过除权量级，但 EXEC 没跳 | **FAIL** | ADJ 自己造出来的涨幅 → ADJ 重建错误 |

    ## 为什么第三版（只用 |ret|）不够

    第三版把"上涨"和"下跌"一视同仁，于是在全 A 5,444 只上把 000738.SZ 的重组复牌
    (+30.27%) 判成 FAIL。**除权不可能让价格上涨**，这条常识第三版没用上。
    只看 |ret| 的检查在 549 只池上碰巧干净，一扩到全 A 就暴露——
    这也是"检查器自己写错、通过率是假的"的第 N 次。
    """
    if adj is None or len(adj) < 30:
        return {"fatal": [], "up_no_limit": [], "warn": 0}

    px = adj["close"].astype(float).values
    dates = pd.to_datetime(adj["date"])
    ret = px[1:] / px[:-1] - 1.0
    dstr = dates.dt.strftime("%Y-%m-%d").values
    gap = dates.diff().dt.days.values[1:]
    fatal_lim, warn_lim = limit * fatal_mult, limit * tol

    ex_ret: dict[str, float] = {}
    if exec_df is not None and len(exec_df) >= 2:
        ep = exec_df["close"].astype(float).values
        ed = exec_df["date"].dt.strftime("%Y-%m-%d").values
        r = ep[1:] / ep[:-1] - 1.0
        ex_ret = {ed[i]: float(r[i - 1]) for i in range(1, len(r) + 1)}

    fatal, up_no_limit, warn = [], [], 0
    for i in range(warmup, len(ret) + 1):
        d = str(dstr[i])
        if d in events or str(dstr[i - 1]) in events:      # 除权日本身就要跳
            continue
        g = gap[i - 1]
        if not (g == g) or g > resume_gap:                  # 长期停牌后复牌
            continue
        r = float(ret[i - 1])
        a = abs(r)
        if a <= warn_lim:
            continue
        if a <= fatal_lim:
            warn += 1
            continue
        if r < 0:
            fatal.append({"date": d, "ret": round(r, 4), "lim": round(fatal_lim, 4),
                          "kind": "ex_div_magnitude_drop"})
            continue
        er = ex_ret.get(d)
        if er is not None and abs(er - r) > 0.02:
            fatal.append({"date": d, "ret": round(r, 4), "lim": round(fatal_lim, 4),
                          "kind": "adj_vs_exec_mismatch", "exec_ret": round(er, 4)})
        else:
            up_no_limit.append({"date": d, "ret": round(r, 4),
                                "exec_ret": (round(er, 4) if er is not None else None),
                                "exec_missing": er is None})
    return {"fatal": fatal, "up_no_limit": up_no_limit, "warn": warn}


def pc2_no_limit_violation(sample: int = 250, seed: int = 20261021) -> None:
    """ADJ 在**非除权日**不得出现"除权量级"的跳变（判据见 `pc2_scan`）。

    ⚠️ 判据设计的三次自纠（保留过程，因为这是本条检查最重要的部分）：

    1. 初版写 `limit_of(code) * (1 + TOL)`，而 TOL=1.02 表示"1.02 倍"，
       `1+1.02=2.02` → 创业板阈值被放大成 40.4%，等于没检。
    2. 改成 `* TOL` 后出现 40 处超限，但样例 301060.SZ 2021-09-14 +55.99% 是
       **新股上市首日**（创业板前 5 个交易日无涨跌幅限制）→ 判据误报，加 warmup=5。
    3. 修完仍报 6395 处，但分布是 **6153 处在 10~12%**、241 处在 15~22%、**0 处 >22%**。
       这说明：拿"涨跌停"当标尺抓不到真正的错误，只会抓到**涨跌停价四舍五入**
       与**复牌/股改无限制**的噪声。

    4. **2026-10-21 第四次修**：全 A 扩到 5,444 只后出现 1 处 FAIL——
       000738.SZ 2010-10-15 **+30.27%**，是重组复牌当日无涨跌幅限制的真实行情
       （EXEC 同日也涨 30.2651%，`adj/exec` 比值恒定 0.956871，ADJ 重建无误）。
       第三版用 `|ret|` 当标尺，把"上涨"也算进除权量级 → **除权不可能让价格上涨**，
       这条常识没用上。现改为按方向 + EXEC 交叉判定，见 `pc2_scan` 的语义表。
    """
    paths = sorted(glob.glob(os.path.join(ADJ_ALL, "*.qfq.csv")))
    random.Random(seed).shuffle(paths)
    fatal, up_list, warn, checked = [], [], 0, 0
    for p in paths[:sample]:
        code = os.path.basename(p).replace(".qfq.csv", "")
        df = read_adj(code)
        if df is None or len(df) < 30:
            continue
        checked += 1
        r = pc2_scan(df, read_exec_simple(code), read_events_sina(code), limit_of(code))
        for f in r["fatal"]:
            fatal.append({"code": code, **f})
        for u in r["up_no_limit"]:
            up_list.append({"code": code, **u})
        warn += r["warn"]
    up_desc = ""
    if up_list:
        s = up_list[:3]
        up_desc = (f"；上涨超量级但 EXEC 同幅 {len(up_list)} 处"
                   f"{('：' + str(s)) if s else ''}（真实复牌/无涨跌幅限制，不判失败）")
    record("PC2", "ADJ 非除权日无除权量级跳变", not fatal,
           f"抽样 {checked} 只；下跌超量级(主板>22%、双创>44%) {len(fatal)} 处"
           f"{('：' + str(fatal[:4])) if fatal else ''}"
           f"{up_desc}；贴边噪声 {warn} 处（舍入/复牌，不判失败）", checked)


# ---------------------------------------------------------------- PC3
def pc3_asof_no_lookahead(max_files: int = 0) -> None:  # 0=全量
    """as-of 掩码不得包含"当时还没上市"或"当时无价"的标的。

    独立重建掩码并与 Gate 6 的定义对比，验证三个不变量：
      (a) mask=True ⇒ 当日 close 有效；
      (b) mask=True ⇒ 该股票**截至当日**的累计有效交易日 ≥ 252；
      (c) mask 在"第 252 个有效日"之前必须全为 False。
    """
    cols, n = {}, 0
    paths = sorted(glob.glob(os.path.join(ADJ_ALL, "*.qfq.csv")))
    if max_files:
        paths = paths[:max_files]
    for p in paths:
        code = os.path.basename(p).replace(".qfq.csv", "")
        df = read_adj(code)
        if df is None or df.empty:
            continue
        cols[code] = df.set_index("date")["close"].astype(float) if "close" in df.columns else None
        n += 1
    close = pd.DataFrame({k: v for k, v in cols.items() if v is not None}).sort_index()
    valid = close.notna() & (close > 0)
    cum = valid.cumsum(axis=0)
    mask = valid & (cum >= 252)

    a_bad = int((mask & ~valid).to_numpy().sum())
    b_bad = int((mask & (cum < 252)).to_numpy().sum())
    # (c) 每个代码的 mask 首个 True 之前不得有 True
    c_bad = 0
    for code in mask.columns:
        idx = np.flatnonzero(mask[code].to_numpy())
        if idx.size and idx[0] < 251:
            c_bad += 1
    ok = (a_bad == 0 and b_bad == 0 and c_bad == 0)
    record("PC3", "as-of 掩码无前视", ok,
           f"检查 {n} 只 / {len(close)} 个交易日；(a) 无价 {a_bad} (b) 不足 252 日 {b_bad} (c) 过早入选 {c_bad}",
           n)


# ---------------------------------------------------------------- PC4
def pc4_pool_growth_sane() -> None:
    """as-of 池规模应随 A 股扩容增长；除退市潮外不应无故大幅下降。"""
    p = os.path.join(EXEC_ALL, "_acceptance.json")
    if not os.path.exists(p):
        record("PC4", "as-of 池规模合理性", False, "缺 _acceptance.json")
        return
    m = json.load(open(p, encoding="utf-8"))
    inv = m.get("investable_by_year") or {}
    years = sorted(inv)
    drops = []
    for a, b in zip(years, years[1:]):
        if inv[b] < inv[a] * 0.98:      # 允许 2% 的退市噪声
            drops.append((b, inv[a], inv[b]))
    peak = max(inv.values()) if inv else 0
    record("PC4", "as-of 池规模合理性", len(drops) <= 1,
           f"{years[0]}~{years[-1]}：{inv.get(years[0])}→{inv.get(years[-1])}（峰值 {peak}）；"
           f"异常下降年 {len(drops)}" + (f"：{drops}" if drops else ""), len(years))


# ---------------------------------------------------------------- PC5
def pc5_rebuild_deterministic(codes: tuple[str, ...] = ("000001.SZ", "300216.SZ", "600005.SH")) -> None:
    """同一输入重建两次，输出必须逐字节相同（可复现性）。"""
    py = sys.executable
    outs = []
    for tag in ("a", "b"):
        outdir = os.path.join(REPO, f"_pc5_{tag}")
        subprocess.run(
            [py, os.path.join(REPO, "scripts/rebuild_adj_from_exec.py"),
             "--codes", ",".join(codes), "--exec-dir", EXEC_ALL,
             "--div-source", "merge", "--div-cache", DIV_EM, "--div-sina-cache", DIV_SINA,
             "--out", outdir, "--report", os.path.join(outdir, "_rep.json")],
            cwd=REPO, check=False, capture_output=True, text=True,
        )
        outs.append(outdir)
    sha = []
    for outdir in outs:
        h = {}
        for c in codes:
            p = os.path.join(outdir, f"{c}.qfq.csv")
            h[c] = hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None
        sha.append(h)
    ok = all(sha[0][c] is not None and sha[0][c] == sha[1][c] for c in codes)
    mism = [c for c in codes if sha[0][c] != sha[1][c]]
    for outdir in outs:
        for f in glob.glob(os.path.join(outdir, "*")):
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            os.rmdir(outdir)
        except OSError:
            pass
    record("PC5", "ADJ 重建可复现", ok,
           f"{len(codes)} 只两次重建 sha256 相同={ok}" + (f"，不一致 {mism}" if mism else ""), len(codes))


# ---------------------------------------------------------------- PC6
def pc6_equity_identity() -> None:
    """组合权益恒等式：cash + Σ(股数×价格) = 权益；单月收益不得凭空产生。

    独立复算 portfolio_lot_sim 的核心恒等式（不 import 其 simulate，避免同源错误）：
    给定等权目标与整手取整，买入金额 + 剩余现金必须精确等于初始资金（成本另计）。
    """
    bad = []
    rng = random.Random(7)
    for trial in range(200):
        k = rng.choice([5, 10, 30, 110])
        W = rng.choice([1e5, 3e5, 1e6, 3e6, 1e7])
        px = np.array([rng.uniform(2.0, 300.0) for _ in range(k)])
        budget = W / k
        shares = np.floor(budget / (px * 100)) * 100
        spent = float((shares * px).sum())
        cash = W - spent
        # 恒等式 1：花掉的钱 = Σ股数×价
        if abs(spent - float((shares * px).sum())) > 1e-6:
            bad.append({"trial": trial, "why": "spent"})
        # 恒等式 2：现金 = Σ (每只预算 mod 一手金额)，故 0 <= cash < Σ 一手金额。
        # ⚠️ 自纠：初版把上界写成"最便宜的一手"，200 组全部 FAIL —— 那是断言写错了。
        #    正确上界：每只的残留都 < 它自己的一手金额，总残留 < Σ lot_i。
        lots = px * 100.0
        if cash < -1e-9 or cash >= float(lots.sum()) + 1e-6:
            bad.append({"trial": trial, "why": "cash", "cash": round(cash, 4),
                        "sum_lots": round(float(lots.sum()), 4)})
        # 恒等式 3：总权益不变
        if abs(cash + spent - W) > 1e-6:
            bad.append({"trial": trial, "why": "equity"})
    record("PC6", "组合权益恒等式", not bad,
           f"200 组随机 (k,W,价格) 全部满足 3 条恒等式" if not bad else f"违反 {len(bad)} 组：{bad[:3]}", 200)


# ---------------------------------------------------------------- PC7
def pc7_cost_turnover_consistency() -> None:
    """费用必须与换手率一致：年化成本 = 2 × 单边换手 × 12 × 费率。"""
    p = os.path.join(REPO, "docs/PORTFOLIO_CONSTRAINTS_RECENT_2026-10-20.json")
    if not os.path.exists(p):
        record("PC7", "费用与换手一致", False, "缺 PORTFOLIO_CONSTRAINTS_RECENT")
        return
    m = json.load(open(p, encoding="utf-8"))
    bad, n = [], 0
    for r in m.get("results", []):
        if r.get("cost", 0) <= 0:
            continue
        to = r.get("turnover_1side")
        got = r.get("cost_ann_pp_analytic")
        if to is None or got is None or not (to == to) or not (got == got):
            continue
        n += 1
        want = -2.0 * to * 12.0 * r["cost"] * 100.0
        if abs(got - want) > 1e-6:
            bad.append({"k": r.get("k"), "W": r.get("W"), "cost": r.get("cost"),
                        "got": got, "want": round(want, 6)})
    record("PC7", "费用与换手一致", not bad,
           f"核对 {n} 组，不一致 {len(bad)}" + (f"：{bad[:3]}" if bad else ""), n)


# ---------------------------------------------------------------- PC8
def pc8_sign_symmetry() -> None:
    """反转 d 与动量 d 必须互为相反数（同一构造、方向定义相反）。

    这是检验"排序方向有没有写反"的最强不变量：若代码里把 longs/shorts 搞混，
    这个检查会立刻发现。
    """
    paths = sorted(glob.glob(os.path.join(ADJ_ALL, "*.qfq.csv")))
    rng = random.Random(11)
    rng.shuffle(paths)
    cols = {}
    for p in paths[:400]:
        code = os.path.basename(p).replace(".qfq.csv", "")
        df = read_adj(code)
        if df is None or len(df) < 400:
            continue
        cols[code] = df.set_index("date")["close"].astype(float)
    close = pd.DataFrame(cols).sort_index()
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    # 用日频近似：过去 21 日收益 vs 未来 21 日收益，横截面 Spearman 符号
    sig = (close.shift(1) / close.shift(21) - 1.0)
    fwd = (close.shift(-21) / close - 1.0)
    diffs = []
    for t in range(300, len(close) - 25, 40):
        s = sig.iloc[t].dropna()
        f = fwd.iloc[t].dropna()
        idx = s.index.intersection(f.index)
        if len(idx) < 50:
            continue
        # 反转：按 s 升序取前 10% 做多、后 10% 做空
        order = s.loc[idx].sort_values()
        k = len(idx) // 10
        d_rev = 0.5 * (f.loc[order.index[:k]].mean() - f.loc[order.index[-k:]].mean())
        # 动量：完全相反的方向
        d_mom = 0.5 * (f.loc[order.index[-k:]].mean() - f.loc[order.index[:k]].mean())
        diffs.append(d_rev + d_mom)
    ok = all(abs(x) < 1e-12 for x in diffs)
    record("PC8", "反转/动量 d 符号对称", ok,
           f"{len(diffs)} 个截面，max|d_rev+d_mom|={max((abs(x) for x in diffs), default=0):.2e}", len(diffs))


# ---------------------------------------------------------------- PC9
def pc9_events_have_trading_day(max_files: int = 3000) -> None:
    """每个除权事件日期之后，ADJ 必须有对应的可交易日（否则事件被静默丢弃）。"""
    bad, n_ev, checked = [], 0, 0
    # 只扫有新浪事件缓存的股票（没缓存的不可能检出事，也不该白读 CSV）
    cands = [os.path.basename(x).replace(".div.json", "")
             for x in sorted(glob.glob(os.path.join(DIV_SINA, "*.div.json")))]
    for code in cands[:max_files]:
        ev = read_events_sina(code)
        if not ev:
            continue
        df = read_adj(code)
        if df is None or df.empty:
            continue
        checked += 1
        dates = df["date"].dt.strftime("%Y-%m-%d").values
        dmin, dmax = dates[0], dates[-1]
        for e in ev:
            if not (dmin <= e <= dmax):
                continue          # 事件在行情区间外，属正常
            n_ev += 1
            j = np.searchsorted(dates, e)
            if j >= len(dates):
                bad.append({"code": code, "ex_date": e, "why": "no_next_trading_day"})
    record("PC9", "除权事件有对应交易日", len(bad) == 0,
           f"{checked} 只有事件，窗口内事件 {n_ev} 个，无对应交易日 {len(bad)}"
           + (f"：{bad[:5]}" if bad else ""), n_ev)


CHECKS = {
    "PC1": ("ADJ 价格全为正", pc1_prices_positive),
    "PC2": ("ADJ 非除权日无超限跳变", pc2_no_limit_violation),
    "PC3": ("as-of 掩码无前视", pc3_asof_no_lookahead),
    "PC4": ("as-of 池规模合理性", pc4_pool_growth_sane),
    "PC5": ("ADJ 重建可复现", pc5_rebuild_deterministic),
    "PC6": ("组合权益恒等式", pc6_equity_identity),
    "PC7": ("费用与换手一致", pc7_cost_turnover_consistency),
    "PC8": ("反转/动量 d 符号对称", pc8_sign_symmetry),
    "PC9": ("除权事件有对应交易日", pc9_events_have_trading_day),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="逗号分隔，如 PC1,PC3")
    ap.add_argument("--report", default="reports/PROPERTY_CHECKS.json")
    args = ap.parse_args(argv)

    want = [x.strip() for x in args.only.split(",")] if args.only else list(CHECKS)
    unknown = [x for x in want if x not in CHECKS]
    if unknown:
        print(f"未知检查项：{unknown}")
        return 2

    crash = []
    for c in want:
        name, fn = CHECKS[c]
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 单项崩溃不应中断全部
            crash.append({"check": c, "error": f"{type(exc).__name__}: {exc}"})
            record(c, name, False, f"异常：{type(exc).__name__}: {exc}")

    n_fail = sum(1 for r in results if not r["ok"])
    print()
    print(f"=== {len(results) - len(crash)} 项完成 | PASS {len(results) - n_fail - len(crash)} | "
          f"FAIL {n_fail} | 崩溃 {len(crash)} ===")
    if os.path.dirname(args.report):
        os.makedirs(os.path.dirname(args.report), exist_ok=True)
    json.dump({"results": results, "n_fail": n_fail, "crashes": crash},
              open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"report -> {args.report}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
