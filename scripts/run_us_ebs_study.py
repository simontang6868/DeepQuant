"""运行美股股债利差研究，产出结果文件。

用法::

    python scripts/run_us_ebs_study.py

输出（``data/equity_bond_spread_us/results/``）：
- ``study_summary.json``：配置、数据校验、IC、回归、功效、样本外
- ``events.csv`` / ``event_stats.csv``：阈值事件与事件后收益
- ``deciles.csv`` / ``subperiods.csv`` / ``cases.csv``
- ``strategy.json``：规则化策略与匹配暴露检验
- ``frame_monthly.csv``：逐月指标（供仪表盘使用）
- ``tables.md``：报告用表格片段（数字全部来自本次计算，不手抄）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from deepquant.research.equity_bond_spread_us.cases import check_cases  # noqa: E402
from deepquant.research.equity_bond_spread_us.config import USConfig  # noqa: E402
from deepquant.research.equity_bond_spread_us.data import load_panel, sha256_file  # noqa: E402
from deepquant.research.equity_bond_spread_us.experiment import run_study  # noqa: E402
from deepquant.research.equity_bond_spread_us.strategy import (  # noqa: E402
    matched_exposure_test,
    run_strategy,
)

DATA_DIR = REPO_ROOT / "data" / "equity_bond_spread_us"
RESULT_DIR = DATA_DIR / "results"


def _fmt(x, nd=2, suffix=""):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:,.{nd}f}{suffix}"


def markdown_tables(summary: dict) -> str:
    """把关键结果渲染成 markdown 片段（全部由 JSON 值生成）。"""
    lines: list[str] = []

    lines += ["## 表 A · 核心 IC 表（真实总收益）", "",
              "| 预测变量 | 窗口 | 样本 | n | Spearman IC | NW t | 块自举 p | 有效样本 | 最小可检测 IC |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in summary["ic"]:
        if r["predictor"] not in ("spread_cape", "ey_cape", "y10"):
            continue
        lines.append(
            f"| {r['predictor']} | {r['horizon']}m | {r['sample']} | {r['n']} | "
            f"{_fmt(r['ic'], 3)} | {_fmt(r['nw_t'], 2)} | {_fmt(r['boot_p'], 4)} | "
            f"{_fmt(r['n_eff'], 0)} | {_fmt(r['mdes'], 3)} |"
        )

    lines += ["", "## 表 B · 增量信息回归 R = a + b·z(EY) + c·z(Y10)", "",
              "| 窗口 | 样本 | n | b(z_EY) | t | c(z_Y10) | t | R² |",
              "|---|---|---|---|---|---|---|---|"]
    for key, v in summary["regression"].items():
        if "coef" not in v:
            continue
        c = v["coef"]
        lines.append(
            f"| {v['horizon']}m | {v['sample']} | {v['n']} | "
            f"{_fmt(c['z_ey']['param'], 3)} | {_fmt(c['z_ey']['t'], 2)} | "
            f"{_fmt(c['z_y10']['param'], 3)} | {_fmt(c['z_y10']['t'], 2)} | {_fmt(v['r2'], 3)} |"
        )

    lines += ["", "## 表 C · 阈值事件后收益（事件层面）", "",
              "| 窗口 | 可用事件 | 基准样本 | 信号均值 | 基准均值 | 均值差 | 信号>0 占比 | 基准>0 占比 | 信号期平均回撤 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in summary["event_stats"]:
        if r.get("n_events_usable", 0) == 0:
            continue
        lines.append(
            f"| {r['horizon']}m | {r['n_events_usable']} | {r['n_baseline']} | "
            f"{_fmt(r['signal_mean_pct'], 2)}% | {_fmt(r['baseline_mean_pct'], 2)}% | "
            f"{_fmt(r['diff_mean_pct'], 2)}% | {_fmt(r['signal_positive_share_pct'], 1)}% | "
            f"{_fmt(r['baseline_positive_share_pct'], 1)}% | {_fmt(r['signal_mean_dd_pct'], 2)}% |"
        )

    lines += ["", "## 表 D · 分位分组（spread_cape 历史百分位，60 个月真实收益）", ""]
    dec60 = next((d for d in summary["deciles"] if d["horizon"] == summary["config"]["primary_horizon"]), None)
    if dec60:
        head = "| " + " | ".join(f"D{b['bucket']}" for b in dec60["buckets"]) + " |"
        sep = "|" + "---|" * len(dec60["buckets"])
        vals = "| " + " | ".join(f"{b['mean_pct']:.2f}%" for b in dec60["buckets"]) + " |"
        ns = "| " + " | ".join(str(b["n"]) for b in dec60["buckets"]) + " |"
        lines += ["| 分组 | " + " | ".join(f"D{b['bucket']}" for b in dec60["buckets"]) + " |",
                  "|---|" + "---|" * len(dec60["buckets"]),
                  "| 平均 60m 真实收益 | " + " | ".join(f"{b['mean_pct']:.2f}%" for b in dec60["buckets"]) + " |",
                  "| 观测数 | " + " | ".join(str(b["n"]) for b in dec60["buckets"]) + " |"]
        lines += ["", f"分组单调性 IC = {_fmt(dec60['monotonic_ic'], 3)}，"
                      f"最高组 − 最低组 = {_fmt(dec60['high_minus_low_pct'], 2)} 个百分点。"]

    lines += ["", "## 表 E · 功效分析", "",
              "| 窗口 | 观测数 | 有效样本(n/h) | 最小可检测 IC | 实测 IC | 达到该效应所需独立观测 | 缺口倍数 |",
              "|---|---|---|---|---|---|---|"]
    for r in summary["power"]["by_horizon"]:
        lines.append(
            f"| {r['horizon']}m | {r['n_obs']} | {r['n_eff']} | {_fmt(r['mdes_ic'], 3)} | "
            f"{_fmt(r['observed_ic'], 3)} | {_fmt(r['required_independent_obs'], 0)} | "
            f"{_fmt(r.get('power_shortfall_x'), 1)}x |"
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = USConfig()
    panel = load_panel(DATA_DIR / cfg.panel_csv)
    frame = panel.frame

    if "partial_month" in frame.columns and frame["partial_month"].any():
        frame = frame[~frame["partial_month"].astype(bool)]
    frame = frame.loc[pd.Timestamp(cfg.start_date) : pd.Timestamp(cfg.end_date)]
    print(f"[data] {len(frame)} 个月：{frame.index.min().date()} ~ {frame.index.max().date()}")

    study = run_study(frame, cfg)
    print(f"[study] IC 行数={len(study.ic)} 事件数={len(study.events)} 检验计数={study.tests_counted}")

    # ---------------------------------------------------------------- 策略
    strat = run_strategy(study.frame, cfg, "spread_cape")
    match = matched_exposure_test(study.frame, cfg, strat)
    strat_pe = run_strategy(study.frame, cfg, "spread_cape", threshold=80.0)
    match_pe = matched_exposure_test(study.frame, cfg, strat_pe)
    for s in (strat, strat_pe):
        s.pop("_curve", None)
    strategy_out = {
        "main": strat,
        "main_matched": match,
        "thr80": strat_pe,
        "thr80_matched": match_pe,
    }

    # ---------------------------------------------------------------- 案例
    cases = [c.to_dict() for c in check_cases(study.frame, cfg.threshold_pct)]

    # ---------------------------------------------------------------- 落盘
    summary = {
        "config": cfg.to_dict(),
        "data": {
            "panel_sha256": sha256_file(DATA_DIR / cfg.panel_csv),
            "checks": panel.checks,
            "analysis_first": str(study.frame.index.min().date()),
            "analysis_last": str(study.frame.index.max().date()),
            "n_months": int(len(study.frame)),
        },
        "ic": study.ic,
        "regression": study.regression,
        "deciles": study.deciles,
        "events": study.events,
        "event_stats": study.event_stats,
        "subperiods": study.subperiods,
        "power": study.power,
        "oos": study.oos,
        "excess": study.excess,
        "tests_counted": study.tests_counted,
        "strategy": strategy_out,
        "cases": cases,
    }
    (RESULT_DIR / "study_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    pd.DataFrame(study.events).to_csv(RESULT_DIR / "events.csv", index=False, encoding="utf-8")
    pd.DataFrame(study.event_stats).to_csv(RESULT_DIR / "event_stats.csv", index=False, encoding="utf-8")
    pd.DataFrame(cases).to_csv(RESULT_DIR / "cases.csv", index=False, encoding="utf-8")
    pd.DataFrame(study.subperiods).to_csv(RESULT_DIR / "subperiods.csv", index=False, encoding="utf-8")

    dec_rows = []
    for d in study.deciles:
        for b in d["buckets"]:
            dec_rows.append({"horizon": d["horizon"], "bucket": b["bucket"],
                             "n": b["n"], "mean_pct": b["mean_pct"]})
    pd.DataFrame(dec_rows).to_csv(RESULT_DIR / "deciles.csv", index=False, encoding="utf-8")

    keep = ["price", "cape", "y10", "ey_cape", "spread_cape", "spread_cape_pct",
            "ey_cape_pct", "y10_pct", "real_tr_index", "tr_index"]
    keep += [c for c in study.frame.columns if c.startswith(("fwd_real_", "dd_"))]
    out = study.frame[keep].copy()
    out.index.name = "month"
    out.reset_index().to_csv(RESULT_DIR / "frame_monthly.csv", index=False,
                             encoding="utf-8", float_format="%.6f")

    (RESULT_DIR / "tables.md").write_text(markdown_tables(summary), encoding="utf-8")

    # ---------------------------------------------------------------- 控制台摘要
    print("\n=== 主检验（spread_cape, 60m, 全样本）===")
    for r in study.ic:
        if r["predictor"] == "spread_cape" and r["horizon"] == 60 and r["sample"] == "full":
            print(json.dumps(r, ensure_ascii=False))
    print("\n=== 增量信息回归（60m 全样本）===")
    print(json.dumps(study.regression.get("60m_full", {}), ensure_ascii=False))
    print("\n=== 事件 ===")
    print(f"共 {len(study.events)} 个合并事件")
    print(json.dumps(strategy_out["main"], ensure_ascii=False))
    print(json.dumps(strategy_out["main_matched"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
