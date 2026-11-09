#!/usr/bin/env python3
"""PA-OCT2026-1 主回测：2026-10 下跌行情的双口径单窗口描述性归因。

预登记 research/stock_pick_2026-10-25/SP5_OCT2026_DOWNTURN_PREREG.md
冻结 sha256 cd5d016b8c9f…（启动校验，不匹配即拒绝运行）
产物 research/stock_pick_2026-10-25/oct2026_out/cd5d016b8c9f/

⚠️ 全部参数硬编码（R2/R11 禁调参择优）；不接受任何命令行覆盖窗口/权重/持仓。
⚠️ 结论只标 DESCRIPTIVE_SINGLE_MONTH，不判 PASS/FAIL、不改 S4 0/7。
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import pathlib
import statistics
import sys
from datetime import datetime, timezone, timedelta

REPO = pathlib.Path(__file__).resolve().parents[1]
PREREG = REPO / "research/stock_pick_2026-10-25/SP5_OCT2026_DOWNTURN_PREREG.md"
SIDECAR = PREREG.with_suffix(".md.sha256")
OUT = REPO / "research/stock_pick_2026-10-25/oct2026_out/cd5d016b8c9f"
LEDGER = REPO / "research/stock_pick_2026-10-25/forward/ledger/20260930.json"
OLD_NAV = REPO / "research/stock_pick_2026-10-25/forward/nav/20260930_nav.csv"
QFQ = REPO / "data/qfq_all"
EXEC = REPO / "data/exec_all"

# ── §2 硬编码参数 ────────────────────────────────────────────────────────
ASOF = "20261030"
T0 = "20261008"                      # 执行日：次月首个交易日收盘价
CAPITAL = 100000.0
CODES = ("603193", "301336", "301202", "000597", "002692")
W_A, W_B = 0.25, 0.20               # A=v1 历史还原 / B=v2 后验重演
CASH_YIELD = 0.03
TRADING_DAYS_PER_YEAR = 244
RATE_EXPLICIT = 0.00051             # 等价单边显性费率（项目 §10）
RATE_WITH_SPREAD = 0.00151          # 加 S2 单边价差 0.001 的敏感性
COMM_RATE, COMM_MIN = 0.00025, 5.0
TRANSFER, STAMP_SELL, SPREAD_ONEWAY = 1e-5, 0.0005, 0.0005
MDE_BORROWED = {"L3r": 0.926, "L3t_range": [0.087, 0.955], "S_exQ5_late": 0.140}
Z_POWER80 = 2.8016
SPOT_CHECK = ("000001.SZ", "600519.SH", "000002.SZ", "601318.SH",
              "600036.SH", "002415.SZ", "300750.SZ", "601988.SH")


def _load(path: pathlib.Path, name: str):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def verify_prereg() -> str:
    if not PREREG.exists() or not SIDECAR.exists():
        sys.exit("[abort] PREREG_MISSING：预登记或 .sha256 不存在")
    expected = SIDECAR.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    if expected != actual:
        sys.exit("[abort] PREREG_HASH_MISMATCH：预登记已被改动，按 L3ac D1 拒绝运行")
    return actual


def norm(d: str) -> str:
    t = d.strip().replace("-", "")
    if len(t) != 8 or not t.isdigit():
        raise ValueError("BAD_DATE:" + d)
    return t


def read_one(path: pathlib.Path, dates: set[str]) -> dict[str, float]:
    with path.open("r", encoding="utf-8") as g:
        hdr = g.readline().strip().split(",")
    if "close" not in hdr:
        raise RuntimeError("NO_CLOSE_COLUMN:" + path.name)
    ci = hdr.index("close")
    out: dict[str, float] = {}
    with path.open("r", encoding="utf-8") as g:
        g.readline()
        for ln in g:
            z = ln.strip().split(",")
            if len(z) <= ci or not z[0].strip():
                continue
            k = norm(z[0])
            if k in dates:
                try:
                    out[k] = float(z[ci])
                except ValueError:
                    pass
    return out


def pool_return(root: pathlib.Path, start: str, end: str):
    """全池等权（尾部读取 + 按列名 close + 日期归一化）；返回 (rets, n_missing)。"""
    rets, missing = [], 0
    for p in root.glob("*.csv"):
        with p.open("rb") as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - 16384))
            tail = f.read().decode("utf-8", errors="ignore").splitlines()
        with p.open("r", encoding="utf-8") as g:
            hdr = g.readline().strip().split(",")
        if "close" not in hdr:
            missing += 1
            continue
        ci = hdr.index("close")
        d: dict[str, float] = {}
        for ln in tail:                      # 不跳过首行：大文件时首行是数据不是表头
            z = ln.split(",")
            if len(z) <= ci or not z[0].strip():
                continue
            try:
                k = norm(z[0])
            except ValueError:
                continue
            if k in (start, end):
                try:
                    d[k] = float(z[ci])
                except ValueError:
                    pass
        if len(d) == 2 and d[start] > 0:
            rets.append(d[end] / d[start] - 1)
        else:
            missing += 1
    return rets, missing


def write_text(path: pathlib.Path, text: str) -> None:
    """显式 LF：避免 Windows 写 CRLF 导致产物哈希漂移。"""
    path.write_bytes((text if text.endswith("\n") else text + "\n").encode("utf-8"))


def main() -> int:
    prereg_sha = verify_prereg()
    guard = _load(REPO / "scripts/oct2026_asof_guard.py", "asof_guard")
    rpc = _load(REPO / "scripts/run_phase_c.py", "run_phase_c")
    OUT.mkdir(parents=True, exist_ok=True)

    # ── as-of 守卫（用户明确要求，跑前必过）────────────────────────────
    days = guard.trade_days(REPO)
    g = guard.evaluate(QFQ, CODES, days, ASOF)
    guard_payload = {k: v for k, v in g.items() if k != "closes"}
    guard_payload.update({"asof": ASOF, "root": str(QFQ), "n_trade_days": len(days),
                          "codes": list(CODES),
                          "verdict": "ASOF_OK" if g["ok"] else "ASOF_FAIL",
                          "prereg_sha256": prereg_sha})
    write_text(OUT / "asof_guard.json", json.dumps(guard_payload, ensure_ascii=False, indent=2))
    if not g["ok"]:
        sys.exit("[abort] ASOF_FAIL：" + ";".join(g["reasons"]))

    # ── 启动校验：v1/v2 选股规则逐字相同（不需重跑决策脚本的依据）──────
    v1 = json.loads((REPO / "research/stock_pick_2026-10-25/forward/sp4_config_v1.json").read_text(encoding="utf-8"))
    v2 = json.loads((REPO / "research/stock_pick_2026-10-25/forward/sp4_config_v2.json").read_text(encoding="utf-8"))
    diff_keys = [k for k in sorted(set(v1["strategy"]) | set(v2["strategy"]))
                 if v1["strategy"].get(k) != v2["strategy"].get(k)]
    if sorted(diff_keys) != ["rebalance_rule", "stock_weight"]:
        sys.exit("[abort] CONFIG_DRIFT：v1/v2 选股规则不再相同 ⇒ 必须另立预登记")

    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    if tuple(ledger["target_holds"]) != CODES:
        sys.exit("[abort] LEDGER_HOLDS_MISMATCH")

    # ── 行情（按列名 close；日期归一化）────────────────────────────────
    wanted = set(days) | {"20260930"}
    px = {}
    for c in CODES:
        p = next(QFQ.glob(c + ".*.qfq.csv"))
        s = read_one(p, wanted)
        miss = [d for d in days if d not in s]
        if miss or not all(s[d] > 0 for d in days):
            sys.exit("[abort] PRICE_INCOMPLETE:" + c + ":" + ",".join(miss))
        px[c] = s

    # ── 全池基准（同区间 10-08→10-30），qfq 主 + exec 独立交叉 ─────────
    qrets, qmiss = pool_return(QFQ, T0, ASOF)
    erets, emiss = pool_return(EXEC, T0, ASOF)
    r_pool = statistics.mean(qrets)
    r_pool_exec = statistics.mean(erets)
    spot = []
    for code in SPOT_CHECK:
        q = read_one(QFQ / (code + ".qfq.csv"), {T0, ASOF})
        x = read_one(EXEC / (code + ".exec.csv"), {T0, ASOF})
        if len(q) == 2 and len(x) == 2 and q[T0] > 0 and x[T0] > 0:
            spot.append({"code": code,
                         "qfq_pct": 100 * (q[ASOF] / q[T0] - 1),
                         "exec_pct": 100 * (x[ASOF] / x[T0] - 1)})

    # ── 逐日净值（A/B 主口径：比例权重、买入持有、现金 3%/244）─────────
    idx = {d: statistics.mean(px[c][d] / px[c][T0] for c in CODES) for d in days}
    r_stock = idx[ASOF] - 1.0
    daily_stock_rets = [0.0] + [idx[days[i]] / idx[days[i - 1]] - 1.0
                                for i in range(1, len(days))]
    noise_sd = statistics.stdev(daily_stock_rets)
    noise_mde_like = Z_POWER80 * noise_sd / math.sqrt(len(daily_stock_rets)) * 100

    arms = {}
    for tag, w in (("A", W_A), ("B", W_B)):
        cash0 = (1.0 - w) * CAPITAL
        interest_per_day = cash0 * CASH_YIELD / TRADING_DAYS_PER_YEAR
        rows, peak = [], -1.0
        for i, d in enumerate(days):
            cash = cash0 + i * interest_per_day
            stock_value = w * CAPITAL * idx[d]
            nav = cash + stock_value
            peak = max(peak, nav)
            rows.append({"date": d, "px_index": idx[d], "cash": cash,
                         "stock_value": stock_value, "nav_gross": nav,
                         "dd": nav / peak - 1.0})
        mdd = min(r["dd"] for r in rows)
        r_gross = rows[-1]["nav_gross"] / CAPITAL - 1.0
        cost = rpc.cost_of(w, r_gross, 0.0, 1, RATE_EXPLICIT)
        cost_spread = rpc.cost_of(w, r_gross, 0.0, 1, RATE_WITH_SPREAD)
        arms[tag] = {"label": "V1_HISTORICAL_REPLAY" if tag == "A" else "V2_EX_POST_REPLAY",
                     "w": w, "rows": rows, "mdd": mdd,
                     "r_gross": r_gross, "cost_pp_month": cost["cost_pp_month"],
                     "net_pp_month": cost["net_pp_month"],
                     "t_net": None,   # n=1 ⇒ nan；产物用 null 保持严格 JSON 合法
                     "t_net_reason": "single month, se not estimable",
                     "cost_pp_month_with_spread": cost_spread["cost_pp_month"],
                     "nav_end_net_of_cost": CAPITAL * (1.0 + cost["net_pp_month"] / 100.0)}
        hdr = "date,px_index,cash,stock_value,nav_gross,dd\n"
        body = "".join("%s,%.10f,%.6f,%.6f,%.6f,%.10f\n" % (
            r["date"], r["px_index"], r["cash"], r["stock_value"],
            r["nav_gross"], r["dd"]) for r in rows)
        write_text(OUT / ("nav_daily_" + tag + ".csv"), hdr + body.rstrip("\n"))

    # ── 副口径：A 的 10 万本金整手执行（复现既有 nav + 5 元下限单列）──
    shares = {o["code"]: o["shares"] for o in ledger["orders"]}
    buy = sum(shares[c] * px[c][T0] for c in CODES)
    fee_comm_min = sum(max(shares[c] * px[c][T0] * COMM_RATE, COMM_MIN) for c in CODES)
    fee_comm_prop = buy * COMM_RATE
    fee_transfer = buy * TRANSFER
    fee_spread = buy * SPREAD_ONEWAY
    fee_total = fee_comm_min + fee_transfer + fee_spread
    cash_lot = CAPITAL - buy - fee_total
    lot_rows, cross = [], []
    for i, d in enumerate(days):
        nav = cash_lot + sum(shares[c] * px[c][d] for c in CODES) \
              + i * (cash_lot * CASH_YIELD / TRADING_DAYS_PER_YEAR)
        lot_rows.append({"date": d, "lot_nav": nav})
    old = list(csv.DictReader(OLD_NAV.open(encoding="utf-8-sig")))
    for r in old:
        mine = next(x["lot_nav"] for x in lot_rows if x["date"] == r["date"])
        cross.append({"date": r["date"], "old_nav_top5": float(r["nav_top5"]),
                      "reproduced": mine, "abs_diff": abs(mine - float(r["nav_top5"]))})
    # 5 元下限影响（A 口径）
    min_comm_impact = {"fee_with_min_commission": fee_comm_min,
                       "fee_proportional_only": fee_comm_prop,
                       "ratio": fee_comm_min / fee_comm_prop,
                       "buy_amount": buy, "fee_transfer": fee_transfer,
                       "fee_spread": fee_spread, "fee_total": fee_total,
                       "cash_after_buy": cash_lot,
                       "lot_nav_end": lot_rows[-1]["lot_nav"],
                       "lot_r_pct": 100 * (lot_rows[-1]["lot_nav"] / CAPITAL - 1.0)}
    hdr = "date,lot_nav\n"
    write_text(OUT / "nav_daily_A_lot_execution.csv",
               (hdr + "".join("%s,%.6f\n" % (r["date"], r["lot_nav"]) for r in lot_rows)).rstrip("\n"))

    # ── §4 五条预测逐条判定（诚实执行，反驳即报）──────────────────────
    delta = arms["B"]["r_gross"] - arms["A"]["r_gross"]
    r_cash_A = (arms["A"]["rows"][-1]["cash"] / ((1 - W_A) * CAPITAL)) - 1.0
    r_cash_B = (arms["B"]["rows"][-1]["cash"] / ((1 - W_B) * CAPITAL)) - 1.0
    identity = (W_A - W_B) * (r_cash_A - r_stock)   # = 0.05×(现金收益−持股收益)
    alpha = r_stock - r_pool
    preds = {
        "P1": {"delta_pp": 100 * delta, "identity_pp": 100 * identity,
               "identity_holds": abs(delta - identity) < 1e-9,
               "delta_positive": delta > 0,
               "in_predicted_band_pp": [0.5, 1.0],
               "in_band": 0.5 <= 100 * delta <= 1.0},
        "P2": {"r_combo_B_pct": 100 * arms["B"]["r_gross"],
               "r_pool_samewindow_pct": 100 * r_pool,
               "ratio": abs(arms["B"]["r_gross"]) / abs(r_pool),
               "below_035": abs(arms["B"]["r_gross"]) < 0.35 * abs(r_pool),
               "in_predicted_band_pct": [-4.0, -1.0],
               "in_band": -4.0 <= 100 * arms["B"]["r_gross"] <= -1.0},
        "P3": {"mdd_A_pct": 100 * arms["A"]["mdd"], "mdd_B_pct": 100 * arms["B"]["mdd"],
               "B_less_than_A": arms["B"]["mdd"] < arms["A"]["mdd"],
               "B_gt_minus5pct": arms["B"]["mdd"] > -0.05},
        "P4": {"alpha_pp": 100 * alpha,
               "verdict": "NOT_TESTABLE_SINGLE_PATH",
               "note": "n=1 无 se；借用 MDE 来自不同信号/窗口，禁止与 alpha 并列判显著性"},
        "P5": {"r_stock_pct": 100 * r_stock, "r_pool_pct": 100 * r_pool,
               "stock_negative": r_stock < 0,
               "same_sign": (r_stock * r_pool) > 0},
    }

    metrics = {
        "prereg_sha256": prereg_sha,
        "out_dir": "research/stock_pick_2026-10-25/oct2026_out/cd5d016b8c9f",
        "script_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "run_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "statistical_verdict": "NOT_TESTED_SINGLE_PATH",
        "maturity_level": "EXPLORATORY_ONLY",
        "conclusion_tag": "DESCRIPTIVE_SINGLE_MONTH",
        "asof": ASOF, "exec_date": T0, "n_trade_days": len(days),
        "asof_guard": guard_payload["verdict"],
        "config_diff_keys": diff_keys,
        "v1_config_sha256": ledger["config_sha256"],
        "market": {"r_pool_samewindow_pct": 100 * r_pool,
                   "r_pool_exec_crosscheck_pct": 100 * r_pool_exec,
                   "n_pool_valid": len(qrets), "n_pool_missing_endpoints": qmiss,
                   "n_exec_valid": len(erets), "n_exec_missing_endpoints": emiss,
                   "spot_check": spot,
                   "background_0930_1030_pct": -12.4625},
        "holdings": {"codes": list(CODES), "r_stock_1008_1030_pct": 100 * r_stock,
                     "alpha_vs_pool_pp": 100 * alpha,
                     "daily_noise_sd_pct": 100 * noise_sd,
                     "daily_noise_not_mde_pp": noise_mde_like,
                     "n_daily_returns": len(daily_stock_rets)},
        "arms": {k: {kk: vv for kk, vv in v.items() if kk != "rows"}
                 for k, v in arms.items()},
        "lot_execution_A": min_comm_impact,
        "old_nav_crosscheck": {"n_rows": len(cross),
                               "max_abs_diff": max(x["abs_diff"] for x in cross),
                               "rows": cross},
        "mde": {"this_experiment": "MDE_NOT_ESTIMABLE_FROM_THIS_EXPERIMENT",
                "reason": "n=1 单月单一路径，无法估计 se；禁止用横截面/日频 sd 冒充",
                "borrowed_not_estimated_pp": MDE_BORROWED,
                "borrowed_citation": ".workbuddy/memory/RESULTS_INDEX.md",
                "daily_noise_not_mde_pp": noise_mde_like},
        "predictions": preds,
    }
    write_text(OUT / "metrics.json", json.dumps(metrics, ensure_ascii=False, indent=2,
                                                default=lambda o: str(o)))

    print("[PA-OCT2026-1] ASOF_OK | 预登记 " + prereg_sha[:12])
    print("  全池同区间 %+.4f%% (exec 交叉 %+.4f%%) | 持股端 %+.4f%% | alpha %+.4fpp"
          % (100 * r_pool, 100 * r_pool_exec, 100 * r_stock, 100 * alpha))
    for tag in ("A", "B"):
        a = arms[tag]
        print("  %s(%s w=%.2f) 月毛 %+.4f%% | 成本 %.4fpp | 净 %+.4f%% | MDD %+.4f%%"
              % (tag, a["label"], a["w"], 100 * a["r_gross"], a["cost_pp_month"],
                 a["net_pp_month"], 100 * a["mdd"]))
    print("  P1 delta %+.4fpp (恒等式 %+.4fpp) | A 整手复现既有 nav 最大差 %.4f 元"
          % (100 * delta, 100 * identity, max(x["abs_diff"] for x in cross)))
    print("  产物 -> " + str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
