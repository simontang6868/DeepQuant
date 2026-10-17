"""Step 7.5 端到端多标的研究 — v2（腾讯接口）。"""
import sys, os, json, hashlib, traceback
from pathlib import Path
from datetime import datetime, timezone

# --- 路径解析：禁止硬编码容器路径 ---
# 此前这里是 sys.path.insert(0, "/workspace/DeepQuant/backend/src")，
# 换机器后 import 立即失败（也解释了「CSV 缓存没同步」：原研究只在那台容器里跑过）。
# 现在按「环境变量 > 相对本文件」解析，任何 checkout 都能直接跑。
_HERE = Path(__file__).resolve().parent              # .../data/step75
_REPO_ROOT = _HERE.parents[1]                        # .../DeepQuant-main
_SRC_DIR = Path(os.environ.get("DEEPQUANT_SRC", str(_REPO_ROOT / "backend" / "src")))
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import pandas as pd
import numpy as np
import akshare as ak

from deepquant.data.validation.checks_extended import validate_daily_extended
from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.fee_presets import (
    FEE_PRESET_SIMULATION, FEE_PRESET_REFERENCE, FEE_PRESET_HIGH_COST,
)
from deepquant.strategies.ma_cross import MaCrossStrategy
from deepquant.strategies.mean_reversion import MeanReversionStrategy
from deepquant.walkforward.parameter_selection import GridSearchConfig, run_walkforward_v2
from deepquant.walkforward.sensitivity import run_sensitivity_and_risk_audit

STOCKS = [
    ("sh600519", "600519.SH", "贵州茅台", "白酒/消费"),
    ("sz000001", "000001.SZ", "平安银行", "银行/金融"),
    ("sh601318", "601318.SH", "中国平安", "保险/金融"),
    ("sz300750", "300750.SZ", "宁德时代", "新能源/制造"),
    ("sz000858", "000858.SZ", "五粮液", "白酒/消费"),
    ("sh600900", "600900.SH", "长江电力", "公用事业"),
]

# --- 股票池：默认是上面 6 只手挑标的（历史口径，保持向后可比）；
#     DEEPQUANT_UNIVERSE 指向 data/step75/build_universe.py 产出的 JSON 时改用该池。
#
# 为什么要能换池：docs/EXTERNAL_VALIDITY_2026-10-17.md 实测 22 只里 0 只为正、
# 「价格→参与率」相关性 r=-0.772 全靠茅台单点 → 6 只样本支撑不了任何总体结论。
# 扩样池的**幸存者偏差**由 build_universe.py 写进 meta，本文件原样带进输出，
# 不允许只报结果不报偏差。
def _load_stocks():
    path = os.environ.get("DEEPQUANT_UNIVERSE")
    if not path:
        return STOCKS, None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [
        (
            s["tencent_symbol"],
            s["code"],
            s.get("name", ""),
            s.get("industry", ""),
        )
        for s in payload["stocks"]
    ]
    if not rows:
        raise SystemExit(f"DEEPQUANT_UNIVERSE 为空：{path}")
    return rows, payload.get("meta")


STOCKS, UNIVERSE_META = _load_stocks()

DATA_ROOT = Path(os.environ.get("DEEPQUANT_DATA_ROOT", str(_HERE)))
DATA_ROOT.mkdir(parents=True, exist_ok=True)

# 历史区间：默认 8 年（2017-01-01 起）。
#
# 为什么不取 15 年（2010 起）——**实测否决，不是没试**：
#   qfq（前复权）以最新价为基准向前推，高分红/送转标的的早期价格会被减成负数。
#   2010-2024 实测（腾讯源，6 只，3,515~3,637 行/只）：
#     负价行数 —— 茅台 1575、五粮液 1231、长电 1170、中国平安 849、平安银行 0。
#     负价区间全部落在 2010-01 ~ 2016-09，2016-09-29 之后无一例外均为正。
#   配数据质量闸门后，2010 起只有 **1/6** 标的可用（仅平安银行），研究等于废掉。
#
# 取 2017 起的三个理由：
#   1. 全部老股负价在 2016-09-29 前结束 → 2017 起 **5/6** 标的干净可用；
#   2. 8 年日线正好达到外部惯例的 walk-forward 下限（TradingView 口径 ≥8 年）；
#   3. 宁德时代（300750）仍会被闸门拦下 —— 这是**正确结果**：它 2018-06 上市初期
#      2 行 qfq 负价，且原研究 JSON 自记「1593/1594 行跳变 > 15%」，本就是坏数据。
#
# 需要旧口径对照时用环境变量，例如 DEEPQUANT_START=20180101（7 年）或 20100101（15 年，仅 1 只可用）。
START = os.environ.get("DEEPQUANT_START", "20170101")
END = os.environ.get("DEEPQUANT_END", "20241231")
INITIAL_CASH = 100000.0

# min_trades=2：样本充分性门槛（判据是**交易次数**，不是收益）→ 不构成 R2/R11 择优。
# 取值 2 的结构性理由：IS 段 150 个交易日 / 网格最慢均线 60 日 → 至少容纳一个完整往返。
# 实测代价（docs/DECISION_RULING_2026-10-17.md）：参与率 56.7%→70.0%，但
# 「剔除零参与后的收益中位数」由 -0.002566 恶化到 -0.023935 —— 参与率是数量指标，
# **不得**用它挑候选。此处启用它的目的是「让没有交易能力的候选不进入 IS 排序」，
# 而不是「提高参与率」。
# --- 设计探针覆盖（默认全空 = 正式研究口径，落盘结果与之前完全一致） ---
#
# 为什么要这个开关：扩样后仍有三成标的「OOS 中位数为 0」，成因已定位到**事件稀疏**
# （slow=60 在 50 天 OOS 里平均只交叉 1.38 次）。但「网格太慢」和「OOS 太短」这两个解释
# 从未被分开验证过 —— 不分开就无法决定下一步该动哪个。
#
# 只允许改三个**研究设计**维度：
#   min_oos_periods  OOS 窗口长度
#   ma_slow_max      均线网格的慢线上限
#   fees             只跑哪些费率档（探针用单档省时间，正式研究仍跑三档）
#
# **严禁**通过这里改 min_trades / min_train_periods / 预热 —— 那三项是已裁决口径，
# 动了就会把「设计探针」变成「调参」（R2/R11）。改它们请先提案。
_DESIGN = json.loads(os.environ.get("DEEPQUANT_DESIGN_OVERRIDES", "{}"))
_ALLOWED_DESIGN_KEYS = {"min_oos_periods", "ma_slow_max", "fees"}
_unknown = set(_DESIGN) - _ALLOWED_DESIGN_KEYS
if _unknown:
    raise SystemExit(
        f"DEEPQUANT_DESIGN_OVERRIDES 含不允许的键 {sorted(_unknown)}；"
        f"只允许 {sorted(_ALLOWED_DESIGN_KEYS)}"
    )
OOS_WINDOW = int(_DESIGN.get("min_oos_periods", 50))
MA_SLOW_MAX = int(_DESIGN.get("ma_slow_max", 60))
MA_SLOW_CANDIDATES = tuple(c for c in (15, 25, 40, 60) if c <= MA_SLOW_MAX)
if not MA_SLOW_CANDIDATES:
    raise SystemExit(f"ma_slow_max={MA_SLOW_MAX} 过滤后没有可用慢线")
_FEE_SET = _DESIGN.get("fees")

MA_GRID = GridSearchConfig(
    fast_candidates=(3, 5, 10, 20), slow_candidates=MA_SLOW_CANDIDATES,
    min_train_periods=150, min_oos_periods=OOS_WINDOW, min_trades=2, max_combinations=50,
)
MR_GRID = GridSearchConfig(
    fast_candidates=(10, 20, 30, 50), slow_candidates=(15, 20, 25),   # ×10 → threshold 1.5/2.0/2.5
    min_train_periods=150, min_oos_periods=OOS_WINDOW, min_trades=2, max_combinations=50,
)

def ma_factory(p):
    return MaCrossStrategy(fast_window=int(p["fast_window"]), slow_window=int(p["slow_window"]))
def mr_factory(p):
    return MeanReversionStrategy(lookback=int(p["fast_window"]), threshold=float(p["slow_window"]) / 10.0)

STRATEGIES = [
    ("ma_cross", MA_GRID, ma_factory),
    ("mean_reversion", MR_GRID, mr_factory),
]
PRESETS = [FEE_PRESET_SIMULATION, FEE_PRESET_REFERENCE, FEE_PRESET_HIGH_COST]
if _FEE_SET:
    _by_source = {p.fee_source: p for p in PRESETS}
    _missing_fee = set(_FEE_SET) - set(_by_source)
    if _missing_fee:
        raise SystemExit(f"未知费率档 {sorted(_missing_fee)}；可选 {sorted(_by_source)}")
    PRESETS = [_by_source[s] for s in _FEE_SET]

# --- 下载 ---
download_results = []
for tencent_code, full_code, name, industry in STOCKS:
    csv_path = DATA_ROOT / f"{full_code}.qfq.csv"
    if csv_path.exists() and csv_path.stat().st_size > 500:
        df = pd.read_csv(csv_path)
        print(f"[CACHED] {full_code} {name} {len(df)} rows", flush=True)
    else:
        try:
            df = ak.stock_zh_a_hist_tx(symbol=tencent_code, start_date=START, end_date=END, adjust="qfq")
            df.to_csv(csv_path, index=False)
            print(f"[FETCH ] {full_code} {name} {len(df)} rows via tx", flush=True)
        except Exception as exc:
            print(f"[FAIL  ] {full_code} {name}: {exc}", flush=True)
            df = None

    if df is None:
        download_results.append({"code": full_code, "ok": False, "n_rows": 0})
        continue

    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    report_ext = validate_daily_extended(df, adjust_status="qfq")
    # --- 数据质量闸门（ERROR 级必须阻断） ---
    # 此前这里只收集 `i.level == "warning"`，ERROR 被静默丢弃：
    # qfq 会产生非正价格（实测宁德 2018-06-11 close=-2.07 / open=-5.42），
    # `invalid_price` 本身是 ERROR 级、校验器一直报得出来，只因这里没看 level 就漏了过去，
    # 导致失真数据直接进了 walk-forward（见 docs/EXTERNAL_VALIDITY_2026-10-17.md）。
    # 判据是**数据合法性**，与策略收益无关 → 不构成 R2/R11 意义上的择优。
    data_ok = report_ext.is_valid
    download_results.append({
        "code": full_code, "name": name, "industry": industry,
        "sha256": sha, "n_rows": len(df),
        "date_min": str(df["date"].min().date()), "date_max": str(df["date"].max().date()),
        "ok": True,
        # 数据质量是否通过；False 的标的不会进入 walk-forward（见下方闸门）
        "data_ok": data_ok,
        # R5：无论是否拦截，error / warning 一律原样保留，不删失败记录
        "errors": [
            {"code": i.code, "msg": i.message[:200], "count": i.count}
            for i in report_ext.issues if i.level == "error"
        ],
        "warnings": [
            {"code": i.code, "msg": i.message[:200], "count": i.count}
            for i in report_ext.issues if i.level == "warning"
        ],
    })
    if not data_ok:
        print(f"[GATE  ] {full_code} {name} 数据质量未通过，将跳过 walk-forward："
              f"{[i.code for i in report_ext.issues if i.level == 'error']}", flush=True)

# --- Walk-Forward ---
all_results = []
skipped = []
for dr in download_results:
    if not dr["ok"] or dr["n_rows"] < 400:
        continue
    if not dr.get("data_ok", True):
        # 数据质量闸门：显式记录被拦截的标的，不静默跳过（R5 不删失败记录）
        skipped.append({
            "code": dr["code"], "name": dr["name"],
            "reason": "data_quality_gate",
            "error_codes": [e["code"] for e in dr.get("errors", [])],
        })
        continue
    full_code = dr["code"]
    csv_path = DATA_ROOT / f"{full_code}.qfq.csv"
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df_wf = df.copy().set_index("date")[["open", "high", "low", "close", "volume"]]
    df_wf.index.name = None

    for strat_name, grid, factory in STRATEGIES:
        for preset in PRESETS:
            try:
                base_cfg = BacktestConfig(symbol=full_code, initial_cash=INITIAL_CASH)
                cfg = preset.apply(base_cfg)
                specs, wf_results, summary = run_walkforward_v2(df_wf, cfg, strategy_factory=factory, grid_config=grid)
                audit = run_sensitivity_and_risk_audit(wf_results, grid_config=grid)
                all_results.append({
                    "symbol": full_code, "name": dr["name"], "industry": dr["industry"],
                    "strategy": strat_name, "fee_source": preset.fee_source,
                    "n_windows": len(specs),
                    "n_sel_ok": summary.n_selection_success,
                    "n_oos_ok": summary.n_oos_success,
                    "positive_ratio": summary.positive_ratio,
                    "median_ret": summary.median_cumulative_return,
                    "median_mdd": summary.median_max_drawdown_ratio,
                    "total_trades": summary.total_oos_trades,
                    "total_fees": summary.total_oos_fees,
                    "param_dist": summary.param_distribution,
                    "transition_rate": (
                        audit.param_stability.n_window_transitions / audit.param_stability.n_pairs
                        if audit.param_stability.n_pairs > 0 else None
                    ),
                    "risk_summary": audit.risk.summary_text,
                    "risk_rules": [{"id": r.rule_id, "level": r.level} for r in audit.risk.rules],
                })
                print(f"  {strat_name:15s} {preset.fee_source[:28]:28s} | "
                      f"n={len(specs):2d} pos={summary.positive_ratio:4.0%} "
                      f"med={summary.median_cumulative_return:+.4f} "
                      f"mdd={summary.median_max_drawdown_ratio:.4f} "
                      f"risk={audit.risk.summary_text[:50]}", flush=True)
            except Exception as exc:
                all_results.append({"symbol": full_code, "strategy": strat_name,
                                    "fee_source": preset.fee_source, "error": str(exc)})
                print(f"  {strat_name:15s} {preset.fee_source[:28]:28s} FAIL: {exc}", flush=True)

out = {
    "run_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    "data_source": "akshare.stock_zh_a_hist_tx (tencent kline, qfq)",
    # 数据溯源：区间与门槛必须落盘，否则 7 年 / 15 年、None / 2 的结果无法区分
    "request_range": {"start": START, "end": END},
    "design": {
        "min_trades": 2,
        "min_train_periods": 150,
        # 以下三项可能被 DEEPQUANT_DESIGN_OVERRIDES 覆盖 —— 必须落盘实际生效值，
        # 否则 50/75/100 天 OOS 的结果混在一起无法区分谁是谁。
        "min_oos_periods": OOS_WINDOW,
        "ma_slow_candidates": list(MA_SLOW_CANDIDATES),
        "fees": [p.fee_source for p in PRESETS],
        "initial_cash": INITIAL_CASH,
        "history_years": round((pd.Timestamp(END) - pd.Timestamp(START)).days / 365.25, 2),
    },
    "stocks": download_results,
    # 扩样池的来源与偏差声明（默认 6 只池时为 None）。
    # 偏差必须跟结果一起落盘：否则下游只看到「50 只的收益中位数」，
    # 会误当成总体结论 —— 而该池是**抓取当日**的成分股，缺退市样本，收益偏乐观。
    "universe": UNIVERSE_META,
    # 被数据质量闸门拦截的标的（R5：保留记录，不静默丢弃）
    "skipped": skipped,
    "results": all_results,
}
out_path = DATA_ROOT / "step75_research_summary.json"
out_path.write_text(json.dumps(out, indent=2, default=str))
print(f"\n[SAVED] {out_path}")

# --- 终端汇总 ---
from collections import defaultdict
agg = defaultdict(lambda: {"pos": [], "ret": [], "mdd": [], "fees": []})
for r in all_results:
    if "error" in r or r.get("positive_ratio") is None: continue
    k = (r["strategy"], r["fee_source"])
    agg[k]["pos"].append(r["positive_ratio"])
    agg[k]["ret"].append(r["median_ret"])
    agg[k]["mdd"].append(r["median_mdd"])
    agg[k]["fees"].append(r["total_fees"])

print("\nMULTI-STOCK SUMMARY")
print(f"{'STRAT':15} {'FEE':28} {'N':>3} {'POS%':>8} {'RET_MED':>10} {'MDD_MED':>10} {'FEE_MED':>10}")
print("-"*80)
for (s, f), v in sorted(agg.items()):
    n = len(v["pos"])
    if n == 0: continue
    print(f"{s:15} {f:28} {n:>3} {np.mean(v['pos']):>7.1%} {np.mean(v['ret']):>+10.4f} "
          f"{np.mean(v['mdd']):>10.4f} {np.median(v['fees']):>10.2f}")

print("\nPER-STOCK (SIM)")
for r in sorted([x for x in all_results if x.get("fee_source") == "simulation_placeholder"],
                key=lambda x: (x["symbol"], x["strategy"])):
    if "error" in r: continue
    print(f"{r['symbol']:10} {r.get('name',''):6} {r['strategy']:15} "
          f"pos={r['positive_ratio']:.0%} med={r['median_ret']:+.4f} "
          f"mdd={r['median_mdd']:.4f} risk={r['risk_summary'][:40]}")

print("\nFEE SENSITIVITY (000001 ma_cross)")
for src in ["simulation_placeholder", "broker_reference_common_2024", "research_scenario_high_cost"]:
    for r in all_results:
        if r.get("symbol") == "000001.SZ" and r.get("strategy") == "ma_cross" and r.get("fee_source") == src:
            print(f"  {src[:28]:28s}: pos={r['positive_ratio']:.0%} med={r['median_ret']:+.4f} fees={r['total_fees']:.2f}")
            break

print("\nDone OK")
