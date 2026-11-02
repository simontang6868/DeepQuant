# -*- coding: utf-8 -*-
"""P1-3：SP-4 前瞻观察系统（只做准备与模拟，不接真实下单）

三个关键设计决定（必须在报告里说明）：
1) 原策略的"买入 = 池内随机"**无法执行**（每次跑结果不同）。前瞻版改用确定性规则：
   池内按 amt20 降序取前 5 只（最容易成交的 5 只）。
   依据：审计已证 C2 与 C1c（规模匹配）等价，且 F3 的低端 alpha t=0.77，
   说明买入规则对期望收益无贡献，可自由替换为确定性规则。
2) 卖出规则保持原样：持仓股 F3 全池百分位 > 70% 即卖出，用同一确定性规则补入。
3) 观察起点 = 实际启动日，不用历史数据伪装样本外。

用法：
  python scripts/audit/sp4_forward.py freeze     # 生成/校验配置并冻结哈希
  python scripts/audit/sp4_forward.py signal     # 生成当日目标持仓（dry-run，写日志）
  python scripts/audit/sp4_forward.py verify     # 校验配置与日志完整性

产物：
  forward/sp4_config_v1.json        策略规格（冻结）
  forward/sp4_config_v1.sha256      哈希
  forward/observations.csv          每日观察台账
  forward/target_YYYYMMDD.csv       当日目标持仓
"""
import os, sys, json, hashlib, datetime as dt
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
FWD = os.path.join(RD, "forward")
PANEL = os.path.join(RD, "data", "panel_monthly_trunc.parquet")   # 用含退市损失的审计版
os.makedirs(FWD, exist_ok=True)

CONFIG = {
    "version": "sp4-v1",
    "frozen_at": "2026-10-26",
    "strategy": {
        "universe": "A股（qfq_all 全量，含已退市股）",
        "price_filter": [1.0, 50.0],
        "liquidity_filter": "amt20 > 500 万",
        "band": "amt20 全市场分位 < 20%",
        "f3_filter_entry": "f3pct <= 0.70",
        "f3_rule_exit": "持仓股 f3pct > 0.70 即卖出",
        "n_hold": 5,
        "entry_rule": "池内按 amt20 降序取前 5 只（确定性；替代原随机买入）",
        "exit_refill_rule": "按 amt20 降序补入，排除已持有",
        "signal_time": "每月最后一个交易日收盘后",
        "execution": "次月首个交易日收盘价",
        "stock_weight": 0.25,
        "cash_yield_annual": 0.03,
        "rebalance_rule": "每月末把股票权重调回 25%（不做则不成立）",
    },
    "cost": {"commission_rate": 0.00025, "min_commission": 5.0,
             "stamp_duty_sell": 0.0005, "transfer_fee": 0.00001,
             "spread_scenario": 0.0010, "note": "显性费用 + 0.10% 价差（S2 档）"},
    "benchmark": {"primary": "等权持有全部可买股票（档内等权）",
                  "secondary": "中证红利全收益 H00922"},
    "metrics": ["累计收益", "年化几何收益", "日频MDD", "年化波动", "Calmar",
                "月换手", "实际成本", "相对基准超额", "配对差（vs 同换手随机）"],
    "validation": {
        "min_observation_months": 36,
        "interim_review_every_months": 12,
        "success_criteria": {
            "annual_net_gt": 0.08,
            "mdd_daily_ge": -0.20,
            "excess_vs_benchmark_gt": 0.0,
        },
        "invalidation_criteria": [
            "连续 24 个月相对档内等权超额 < -5pp",
            "日频 MDD 突破 -25%",
            "档内可买股票数连续 6 个月 < 100",
            "小盘流动性溢价（最小档 - 最大档）近 5 年 < 0",
        ],
        "no_peeking_rule": "观察期内不得因结果好坏提前结束或改参数",
    },
    "known_limitations": [
        "买入规则由随机改为确定性（amt20 降序），与回测口径不同，但审计已证买入规则对期望无贡献",
        "ST 历史状态不可回溯，未做 ST 过滤",
        "成本未用真实交割单校准",
        "仅能证明观察期内表现，不保证未来",
    ],
}

OBS_COLS = ["date", "signal_time", "data_version", "pool_size", "n_hold_target",
            "target_holdings", "prev_holdings", "n_swaps", "est_cost_rate",
            "nav_model", "mdd_model", "benchmark_nav", "strategy_hash", "note"]


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def freeze():
    cfg = os.path.join(FWD, "sp4_config_v1.json")
    if os.path.exists(cfg):
        print(f"[freeze] 配置已存在，不覆盖：{cfg}")
    else:
        with open(cfg, "w", encoding="utf-8", newline="\n") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=1)
        print(f"[freeze] 已写入 {cfg}")
    h = sha256_of(cfg)
    with open(os.path.join(FWD, "sp4_config_v1.sha256"), "w", encoding="utf-8", newline="\n") as f:
        f.write(h + "  sp4_config_v1.json\n")
    print(f"[freeze] sha256 = {h}")
    # 初始化台账
    obs = os.path.join(FWD, "observations.csv")
    if not os.path.exists(obs):
        pd.DataFrame(columns=OBS_COLS).to_csv(obs, index=False, encoding="utf-8-sig")
        print(f"[freeze] 台账已初始化 {obs}")
    return h


def load_state():
    p = pd.read_parquet(PANEL)
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")
    return p


def signal(asof=None, dry=True):
    """生成目标持仓并写台账。dry=True 时只做模拟（不产生任何下单动作）。"""
    cfg_path = os.path.join(FWD, "sp4_config_v1.json")
    want = open(os.path.join(FWD, "sp4_config_v1.sha256"), encoding="utf-8").read().split()[0]
    got = sha256_of(cfg_path)
    assert want == got, "配置哈希不一致，停止（策略规格被改动）"
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    st = cfg["strategy"]

    p = load_state()
    last = asof or p["date"].max()
    g = p[p["date"] == last].copy()
    g = g[g["ok_buy"] & g["f3"].notna() & g["fwd20"].notna()]
    g = g[g["f3pct"].notna() & g["liqpct"].notna()]
    lo, hi = st["price_filter"]
    g = g[g["close"].between(lo, hi)]
    band = g[g["liqpct"] < 0.20]
    cand = band[band["f3pct"] <= 0.70].sort_values("amt20", ascending=False)
    if len(cand) < st["n_hold"]:
        print(f"[signal] 候选不足（{len(cand)} < {st['n_hold']}），本次不建仓")
        return None
    tgt = cand.head(st["n_hold"])
    out = os.path.join(FWD, f"target_{last}.csv")
    tgt[["code", "close", "amt20", "f3", "f3pct", "liqpct"]].to_csv(out, index=False, encoding="utf-8-sig")

    obs_path = os.path.join(FWD, "observations.csv")
    obs = pd.read_csv(obs_path, dtype=str) if os.path.exists(obs_path) else pd.DataFrame(columns=OBS_COLS)
    prev = obs.iloc[-1]["target_holdings"] if len(obs) else ""
    prev_set = set(str(prev).split("|")) if prev and prev != "nan" else set()
    cur_set = set(tgt["code"])
    n_swap = len(cur_set - prev_set) if prev_set else st["n_hold"]
    cost_rate = n_swap * 0.000204           # 显性费用；价差在 S2 档另计
    row = {"date": last, "signal_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "data_version": "panel_monthly_trunc.parquet@2026-10-26",
           "pool_size": len(cand), "n_hold_target": st["n_hold"],
           "target_holdings": "|".join(sorted(cur_set)),
           "prev_holdings": "|".join(sorted(prev_set)),
           "n_swaps": n_swap, "est_cost_rate": round(cost_rate, 8),
           "nav_model": "", "mdd_model": "", "benchmark_nav": "",
           "strategy_hash": want[:12],
           "note": "dry-run（未下单）；买入规则=amt20降序"}
    obs = pd.concat([obs, pd.DataFrame([row])], ignore_index=True)
    obs.to_csv(obs_path, index=False, encoding="utf-8-sig")
    print(f"[signal] {last} 候选 {len(cand)} 只，目标 {st['n_hold']} 只 -> {out}")
    print("         " + "、".join(f"{r.code}({r.close:.2f})" for r in tgt.itertuples()))
    print(f"[signal] 换 {n_swap} 只，显性成本 {cost_rate*100:.4f}%；已写 {obs_path}")
    return out


def verify():
    ok = True
    cfg = os.path.join(FWD, "sp4_config_v1.json")
    sha = os.path.join(FWD, "sp4_config_v1.sha256")
    if not (os.path.exists(cfg) and os.path.exists(sha)):
        print("[verify] 配置或哈希缺失"); return False
    want = open(sha, encoding="utf-8").read().split()[0]
    got = sha256_of(cfg)
    print(f"[verify] 配置哈希 {'一致' if want == got else '不一致!'}  {want[:16]}")
    ok &= (want == got)
    obs_path = os.path.join(FWD, "observations.csv")
    if os.path.exists(obs_path):
        obs = pd.read_csv(obs_path)
        missing = [c for c in OBS_COLS if c not in obs.columns]
        print(f"[verify] 台账 {len(obs)} 行，缺列 {missing if missing else '无'}")
        ok &= not missing
        if len(obs):
            bad = obs[obs["strategy_hash"] != want[:12]]
            print(f"[verify] 策略哈希不匹配的行 {len(bad)}")
            ok &= len(bad) == 0
    print(f"[verify] 结果：{'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "freeze"
    if cmd == "freeze":
        freeze()
    elif cmd == "signal":
        freeze()
        signal()
    elif cmd == "verify":
        verify()
    else:
        print(__doc__)
