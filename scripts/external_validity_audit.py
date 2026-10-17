"""外推有效性审计：抽样 → 拉真实行情 → 跑 walk-forward → 稳健性分析 → 数据质量。

用法：
    # 阶段 1：构造样本（只需跑一次，结果可缓存复用）
    python scripts/external_validity_audit.py --sample  --out /tmp/ev

    # 阶段 2：拉行情 + 跑 walk-forward（耗时，按标的数线性增长）
    python scripts/external_validity_audit.py --run     --out /tmp/ev

    # 阶段 3：波动率指标 + 稳健性分析 + 数据质量
    python scripts/external_validity_audit.py --analyze --out /tmp/ev

    # 全跑
    python scripts/external_validity_audit.py --all     --out /tmp/ev

设计约束（与 docs/EXTERNAL_VALIDITY_2026-10-17.md 一致，改动前请先读那份文档）：
1. **沿用原研究配置**，一个参数都不改。改了就不是外推检验，是调参（R2 / R11）。
2. 样本选择只用**非收益属性**：上市日期 + 回测期**开始前**的价格分位（防前视偏差）。
3. 报告的**相关性结论必须先做剔除离群点的稳健性检验**——
   这是本脚本存在的最大理由，见 §5 的经验教训。
4. 只做**诊断**，不产出任何"该怎么配"的建议。

已知局限（无法在本数据源下消除）：
- 交易所名录只含**当前仍在交易**的标的 → 幸存者偏差。
- 随机抽样够不着高价股（A 股高价股是极少数）。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# 配置：与 data/step75/research.py 逐字一致
# --------------------------------------------------------------------------- #
SEED = 20261017
N_CANDIDATES = 80
N_PER_STRATUM = 4
INITIAL_CASH = 100000.0
START, END = "20180101", "20241231"
A_SHARE_LOT_SIZE = 100

# 原研究对照组
ORIGINAL = [
    ("600519", "贵州茅台"), ("000001", "平安银行"), ("601318", "中国平安"),
    ("300750", "宁德时代"), ("000858", "五粮液"), ("600900", "长江电力"),
]

# 参与率与"剔除极端点"的判定阈值
VOL_OUTLIER = 1.0          # 年化波动率 > 100% 视为数据异常
EXTREME_CODES = ("300750", "600519")   # 数据异常 + 唯一买不起一手的标的


def _src_path() -> Path:
    """定位 backend/src（本脚本在 scripts/ 下）。"""
    return Path(__file__).resolve().parent.parent / "backend" / "src"


def tencent_code(code: str) -> str:
    code = str(code).strip()
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return ""


# =========================================================================== #
# 阶段 1：构造样本
# =========================================================================== #

def load_universe():
    """拉沪深两市名录（沪含上市日期，深含上市日期+行业）。"""
    import akshare as ak

    rows = []
    sh = ak.stock_info_sh_name_code()
    for _, r in sh.iterrows():
        code = str(r["证券代码"]).strip()
        if not code.startswith("6") or code.startswith("688"):
            continue
        rows.append((code, str(r["证券简称"]).strip(), str(r["上市日期"]).strip(), "sh_main"))

    sz = ak.stock_info_sz_name_code()
    for _, r in sz.iterrows():
        code = str(r["A股代码"]).strip()
        if not code.startswith(("0", "3")):
            continue
        rows.append((code, str(r["A股简称"]).strip().replace(" ", ""),
                     str(r["A股上市日期"]).strip(), f"sz_{r['板块']}"))
    return rows


def stage_sample(out: Path) -> dict:
    rng = random.Random(SEED)
    universe = load_universe()
    eligible = [r for r in universe if r[2] and r[2] < "2018-01-01"]
    print(f"[sample] 名录 {len(universe)} 只，2018 年前上市 {len(eligible)} 只")

    rng.shuffle(eligible)
    candidates = eligible[:N_CANDIDATES]

    import akshare as ak
    print(f"[sample] 拉 {len(candidates)} 只候选的 2018-01 行情（用于分层）...")
    prices: dict[str, float] = {}
    t0 = time.time()
    for i, (code, name, _ld, _s) in enumerate(candidates, 1):
        tc = tencent_code(code)
        if not tc:
            continue
        try:
            df = ak.stock_zh_a_hist_tx(symbol=tc, start_date="20180101",
                                       end_date="20180228", adjust="qfq")
            if df is None or len(df) == 0:
                continue
            # 腾讯源 date 列元素是 datetime.date，不能直接与字符串比较
            dts = pd.to_datetime(df["date"])
            jan = df[dts < pd.Timestamp("2018-02-01")]
            if len(jan) == 0:
                continue
            prices[code] = float(jan["close"].mean())
        except Exception as e:
            # 静默捕获会掩盖 bug：前 3 次失败必须暴露
            if len(prices) == 0 and i <= 3:
                print(f"[sample] [ERR] {code} {type(e).__name__}: {e}")
            continue
        if i % 20 == 0:
            print(f"[sample]   {i}/{len(candidates)} ({time.time()-t0:.0f}s)")

    print(f"[sample] 拿到期前价格 {len(prices)} 只 ({time.time()-t0:.0f}s)")

    ranked = sorted(prices.items(), key=lambda kv: kv[1])
    if len(ranked) < N_PER_STRATUM * 4:
        raise SystemExit(f"[sample] 有效候选 {len(ranked)} 不足 {N_PER_STRATUM*4}")

    k = len(ranked) // 4
    strata = [ranked[i * k:(i + 1) * k if i < 3 else len(ranked)] for i in range(4)]

    sample = []
    for si, st in enumerate(strata):
        picks = rng.sample(st, min(N_PER_STRATUM, len(st)))
        print(f"[sample] 档{si+1} [{st[0][1]:.2f}, {st[-1][1]:.2f}] 抽 {len(picks)} 只")
        for code, px in picks:
            meta = next((r for r in universe if r[0] == code), None)
            sample.append({"code": code, "tencent": tencent_code(code),
                           "name": meta[1] if meta else "",
                           "listing_date": meta[2] if meta else "",
                           "sector": meta[3] if meta else "",
                           "px_2018_01": round(px, 4), "stratum": si + 1})

    result = {"seed": SEED, "n_candidates": len(candidates), "n_priced": len(prices),
              "strata_bounds": [[round(s[0][1], 2), round(s[-1][1], 2)] for s in strata],
              "sample": sample}
    (out / "sample.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(f"[sample] 已保存 {out/'sample.json'} ({len(sample)} 只)")
    return result


# =========================================================================== #
# 阶段 2：拉行情 + 跑 walk-forward
# =========================================================================== #

def _fetch(code: str, cache_dir: Path) -> pd.DataFrame:
    from deepquant.data.loaders.akshare_loader import fetch_daily_a_share

    cache = cache_dir / f"{code}.csv"
    if cache.exists() and cache.stat().st_size > 500:
        return pd.read_csv(cache, parse_dates=["date"])
    df, _meta = fetch_daily_a_share(code, START, END)
    df.to_csv(cache, index=False)
    return df


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    out = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    out.index.name = None
    return out


def _run_one(code: str, cache_dir: Path) -> dict:
    from deepquant.backtest.config import BacktestConfig
    from deepquant.backtest.fee_presets import FEE_PRESET_SIMULATION
    from deepquant.strategies.ma_cross import MaCrossStrategy
    from deepquant.walkforward.design_audit import (
        check_affordability, summarize_participation,
    )
    from deepquant.walkforward.parameter_selection import (
        GridSearchConfig, run_walkforward_v2,
    )

    # —— 与原研究 data/step75/research.py:36-39 逐字一致 ——
    grid = GridSearchConfig(
        fast_candidates=(3, 5, 10, 20), slow_candidates=(15, 25, 40, 60),
        min_train_periods=150, min_oos_periods=50, min_trades=None, max_combinations=50,
    )

    def factory(p):
        return MaCrossStrategy(fast_window=int(p["fast_window"]),
                               slow_window=int(p["slow_window"]))

    df = _fetch(code, cache_dir)
    if df is None or len(df) < 400:
        return {"code": code, "ok": False, "rows": 0 if df is None else len(df)}

    px_mean = float(pd.to_numeric(df["close"], errors="coerce").mean())
    cfg = FEE_PRESET_SIMULATION.apply(BacktestConfig(symbol=code, initial_cash=INITIAL_CASH))
    _specs, results, summary = run_walkforward_v2(
        _prepare(df), cfg, strategy_factory=factory, grid_config=grid
    )

    part = summarize_participation(results)
    aff = check_affordability(price=px_mean, initial_cash=INITIAL_CASH)
    return {
        "code": code, "ok": True, "rows": len(df),
        "px_mean": round(px_mean, 4), "lot_cost": round(aff.lot_cost, 2),
        "tradable": aff.tradable,
        "n_windows": part.n_windows,
        "participation_rate": round(part.participation_rate, 4),
        "sum_trades": part.sum_trades,
        "median_ret": part.median_cumulative_return,
        "mean_ret": (round(part.mean_cumulative_return, 6)
                     if part.mean_cumulative_return is not None else None),
        "positive_ratio": (round(part.positive_ratio, 4)
                           if part.positive_ratio is not None else None),
        "median_ret_active": part.median_return_active,
        "n_active": part.n_active,
        "n_no_selection": part.n_no_selection,
        "sample_too_small": part.sample_too_small,
        "orig_median_ret": round(summary.median_cumulative_return, 6),
        "orig_positive_ratio": round(summary.positive_ratio, 4),
        "orig_total_trades": summary.total_oos_trades,
    }


def stage_run(out: Path) -> None:
    sampling = json.loads((out / "sample.json").read_text(encoding="utf-8"))
    targets = [{"code": s["code"], "name": s["name"], "group": "sample",
                "stratum": s["stratum"], "px_2018_01": s["px_2018_01"]}
               for s in sampling["sample"]]
    have = {t["code"] for t in targets}
    for code, name in ORIGINAL:
        if code not in have:
            targets.append({"code": code, "name": name, "group": "original",
                            "stratum": None, "px_2018_01": None})

    cache_dir = out / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"[run] 标的 {len(targets)}（样本 {len(sampling['sample'])} + 对照 {len(ORIGINAL)}）")
    results, t0 = [], time.time()
    for i, t in enumerate(targets, 1):
        try:
            r = _run_one(t["code"], cache_dir)
        except Exception as e:
            print(f"[run] {t['code']} {t['name']} FAIL {type(e).__name__}: {str(e)[:60]}")
            continue
        if not r["ok"]:
            print(f"[run] {t['code']} {t['name']} 数据不足 rows={r['rows']}")
            continue
        r.update({"name": t["name"], "group": t["group"],
                  "stratum": t["stratum"], "px_2018_01": t["px_2018_01"]})
        results.append(r)
        print(f"[run] {i}/{len(targets)} {t['code']} {t['name']:10} "
              f"参与率 {r['participation_rate']:.1%} 笔数 {r['sum_trades']:3d} "
              f"中位 {r['median_ret']:+.5f}  ({time.time()-t0:.0f}s)")

    (out / "results.json").write_text(
        json.dumps({"sampling": sampling, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[run] 已保存 {out/'results.json'} ({time.time()-t0:.0f}s)")


# =========================================================================== #
# 阶段 3：波动率 + 稳健性 + 数据质量
# =========================================================================== #

def _ann_vol(df: pd.DataFrame) -> float:
    d = pd.to_numeric(df["close"], errors="coerce").dropna()
    return float(d.pct_change().dropna().std() * np.sqrt(244))


def _corr(a, b, n: int) -> tuple[float, float]:
    r = float(np.corrcoef(a, b)[0, 1])
    if abs(r) >= 0.999:
        return r, math.inf
    return r, abs(r) * math.sqrt(n - 2) / math.sqrt(1 - r ** 2)


def _report_corr(title: str, rows: list[dict]) -> None:
    n = len(rows)
    if n < 4:
        print(f"  [{title}] n={n} 样本过小，不做相关")
        return
    v = np.array([r["vol"] for r in rows])
    p = np.array([r["participation_rate"] for r in rows])
    t = np.array([r["sum_trades"] for r in rows], dtype=float)
    px = np.log(np.array([r["px_mean"] for r in rows]))
    m = np.array([r["median_ret"] for r in rows])
    print(f"  [{title}] n={n}")
    for name, a, b in [("对数均价 vs 参与率", px, p), ("对数均价 vs 总笔数", px, t),
                       ("年化波动 vs 参与率", v, p), ("参与率 vs 中位收益", p, m),
                       ("对数均价 vs 中位收益", px, m)]:
        r_, t_ = _corr(a, b, n)
        print(f"    {name:20s} r = {r_:+.3f}  |t| = {t_:5.2f}  "
              f"{'显著' if t_ > 2.09 else '不显著'}")


def stage_analyze(out: Path) -> None:
    data = json.loads((out / "results.json").read_text(encoding="utf-8"))
    results = [r for r in data["results"] if r["ok"]]
    cache_dir = out / "cache"

    for r in results:
        p = cache_dir / f"{r['code']}.csv"
        r["vol"] = round(_ann_vol(pd.read_csv(p)), 4) if p.exists() else None

    ok = [r for r in results if r.get("vol")]
    n = len(ok)

    print(f"\n[analyze] 标的 {n} 只")
    for r in sorted(ok, key=lambda r: r["vol"]):
        mark = "  <== 波动率异常" if r["vol"] > VOL_OUTLIER else ""
        print(f"  {r['code']} {r['name']:10} 均价 {r['px_mean']:9.2f} "
              f"vol {r['vol']:7.3f} 参与率 {r['participation_rate']:6.1%}{mark}")

    # —— 分组对比 ——
    for grp in ["sample", "original"]:
        rs = [r for r in ok if r["group"] == grp]
        if not rs:
            continue
        c = len(rs)
        z = sum(1 for r in rs if r["median_ret"] == 0)
        print(f"\n[analyze] [{grp}] n={c}")
        print(f"    参与率均值 {sum(r['participation_rate'] for r in rs)/c:.1%}")
        print(f"    中位收益=0  {z}/{c} ({z/c:.1%})")
        print(f"    总笔数均值  {sum(r['sum_trades'] for r in rs)/c:.1f}")
        print(f"    买不起一手  {sum(1 for r in rs if not r['tradable'])}/{c}")

    # —— 中位收益符号分布 ——
    z = sum(1 for r in ok if r["median_ret"] == 0)
    neg = sum(1 for r in ok if r["median_ret"] < 0)
    pos = sum(1 for r in ok if r["median_ret"] > 0)
    print(f"\n[analyze] 中位收益符号： >0 的 {pos} 只， =0 的 {z} 只， <0 的 {neg} 只")

    # —— 稳健性：同一关系在四个口径下各算一遍 ——
    no_vol_out = [r for r in ok if r["vol"] <= VOL_OUTLIER]
    no_tail = [r for r in no_vol_out if r["code"] not in EXTREME_CODES]
    sample_only = [r for r in ok if r["group"] == "sample"]
    print("\n[analyze] 稳健性（剔除离群点后是否还成立）")
    _report_corr("全体", ok)
    _report_corr("剔除波动率异常", no_vol_out)
    _report_corr("剔除波动率异常+极端标的", no_tail)
    _report_corr("仅随机样本", sample_only)

    # —— 数据质量：qfq 非正价格 ——
    print("\n[analyze] 数据质量：qfq 非正价格")
    bad = []
    for r in ok:
        p = cache_dir / f"{r['code']}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        nonpos = (df[["open", "high", "low", "close"]].astype("float64") <= 0).any(axis=1)
        n_bad = int(nonpos.sum())
        if n_bad:
            minc = float(pd.to_numeric(df["close"], errors="coerce").min())
            bad.append({"code": r["code"], "name": r["name"], "n_bad": n_bad,
                        "min_close": round(minc, 2)})
            print(f"  {r['code']} {r['name']:10} {n_bad} 行非正价，最小收盘 {minc:.2f}")
    print(f"  含非正价格：{len(bad)}/{len(ok)}")
    if bad:
        print("  ⚠️ 这些数据会让 MA 计算失真。项目校验器（data/validation/checks.py）"
              "能报出 invalid_price（ERROR 级），但研究流程需自行检查 error 级别。")

    (out / "results.json").write_text(
        json.dumps({"sampling": data["sampling"], "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="./external_validity_out", help="输出目录")
    ap.add_argument("--sample", action="store_true", help="阶段1：构造样本")
    ap.add_argument("--run", action="store_true", help="阶段2：拉行情 + walk-forward")
    ap.add_argument("--analyze", action="store_true", help="阶段3：稳健性 + 数据质量")
    ap.add_argument("--all", action="store_true", help="全部阶段")
    args = ap.parse_args()

    do_all = args.all or not (args.sample or args.run or args.analyze)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(_src_path()))

    if do_all or args.sample:
        stage_sample(out)
    if do_all or args.run:
        stage_run(out)
    if do_all or args.analyze:
        stage_analyze(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
