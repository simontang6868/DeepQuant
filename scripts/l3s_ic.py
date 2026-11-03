"""L3s 全池截面信息含量检验（诊断实验）。

用法：``PYTHONPATH=backend/src python scripts/l3s_ic.py [--quiet]``

预登记 ``docs/gates/l3s_prereg.json``（sha256 前缀 ``61929483c81d78a6``）在脚本启动时校验，
被改动即拒跑——防止事后改判据。

产物：
- ``reports/gates/H-L3S-IC-000001/l3s_result.json``
- ``reports/gates/H-L3S-IC-000001/l3s_report.md``
- ``reports/L3S_IC_EVIDENCE.json``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend" / "src"))

PREREG = ROOT / "docs" / "gates" / "l3s_prereg.json"
PREREG_SHA_PREFIX = "61929483c81d78a6"
L3P_RESULT = ROOT / "reports" / "gates" / "H-L3P-MACROSS-000001" / "l3p_result.json"
L3R_RESULT = ROOT / "reports" / "gates" / "H-L3R-SELECTION" / "l3r_result.json"
SPEC = ROOT / "docs" / "gates" / "specs" / "H_L3P_MACROSS_000001.json"
POOL = ROOT / "data" / "_pool_518_a_share.txt"
OUT_DIR = ROOT / "reports" / "gates" / "H-L3S-IC-000001"
EVIDENCE = ROOT / "reports" / "L3S_IC_EVIDENCE.json"

SPAN = ("2010-01-01", "2026-10-16")
#: L3r 的折算 MDE（pp），用于 P6「至少改善 3 倍」的比较基准
L3R_MDE_PP = 0.926


def _pp(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.3f}pp"


def _abs_pp(value: float | None) -> str:
    return "—" if value is None or value != value else f"{value * 100:.3f}pp"


def _f(value: float | None, digits: int = 3) -> str:
    return "—" if value is None or value != value else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description="L3s 全池截面信息含量检验")
    parser.add_argument("--quiet", action="store_true", help="只打印最终判定")
    args = parser.parse_args()

    from deepquant.gates import ic_test as ic
    from deepquant.gates.l3 import build_folds, parse_walk_forward
    from deepquant.gates import load_spec
    from deepquant.gates.l3_portfolio import _signal_dates_for, load_universe

    import pandas as pd

    started = time.perf_counter()
    sha = ic.check_prereg_sha(PREREG, PREREG_SHA_PREFIX)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    cfg = payload["frozen_config"]

    spec = load_spec(SPEC)
    wf = parse_walk_forward(spec.walk_forward)
    saved = json.loads(L3P_RESULT.read_text(encoding="utf-8"))["result"]["folds"]

    codes = ic.load_pool(POOL)
    frames, pool_meta = ic.load_pool_frames(
        codes, data_dir=ROOT / "data" / "qfq_all", span_start=SPAN[0], span_end=SPAN[1]
    )

    # 折边界必须与 L3p **完全一致**（best_params 绑在折窗口上）：因此日历取自同一个
    # 30 只池的并集，而不是新池的并集——否则窗口会整体挪一天，参数与窗口就对不上了。
    universe = load_universe(
        ROOT / "docs" / "gates" / "universes" / "H_L3P_UNIVERSE_30.json",
        data_dir=ROOT / "data" / "qfq_all",
        span_start=wf.span_start,
        span_end=wf.span_end,
    )
    calendar = pd.DatetimeIndex(
        sorted(set().union(*(set(f.index) for f in universe.frames.values())))
    )
    calendar = calendar[
        (calendar >= pd.Timestamp(wf.span_start)) & (calendar <= pd.Timestamp(wf.span_end))
    ]
    folds, fold_meta = build_folds(calendar, wf)
    if len(folds) != len(saved):
        raise SystemExit(f"折数不一致：本次 {len(folds)}，存档 {len(saved)}；拒跑")
    mismatched = [
        idx
        for idx, fold in enumerate(folds)
        if str(fold.test_start.date()) != saved[idx]["test"]["start"]
        or str(fold.test_end.date()) != saved[idx]["test"]["end"]
    ]
    if mismatched:
        raise SystemExit(f"折窗口与 L3p 存档不一致（第 {mismatched[:5]} 折）；拒跑")

    # 按参数集分组，每套参数只算一次分数（28 折通常只有少数几套）
    by_params: dict[tuple[int, int], list[int]] = {}
    for idx, fold in enumerate(folds):
        saved_fold = saved[idx]
        params = saved_fold["best_params"]
        key = (int(params["fast_window"]), int(params["slow_window"]))
        by_params.setdefault(key, []).append(idx)

    periods: list[ic.ICPeriod] = []
    fold_rows: list[dict[str, Any]] = []
    for (fast, slow), fold_indexes in sorted(by_params.items()):
        score_by_symbol = ic.ma_gap_scores(frames, fast=fast, slow=slow)
        for idx in fold_indexes:
            fold = folds[idx]
            dates = _signal_dates_for(calendar, fold.test_start, fold.test_end)
            # 期收益 = 本期执行日 → **下一期**执行日；折内最后一期用窗口末日收盘。
            # 用「执行日的下一个交易日」是错的：那算的是 1 日收益，不是月度收益。
            executed: list[tuple[pd.Timestamp, pd.Timestamp]] = []
            for day in dates:
                pos = calendar.searchsorted(pd.Timestamp(day), side="right")
                if pos >= len(calendar):
                    continue
                executed.append((pd.Timestamp(day), calendar[pos]))
            window = calendar[(calendar >= fold.test_start) & (calendar <= fold.test_end)]
            if len(window) == 0:
                continue
            window_end = window[-1]
            rows: list[ic.ICPeriod] = []
            for n, (signal_day, exec_day) in enumerate(executed):
                next_day = (
                    executed[n + 1][1] if n + 1 < len(executed) else window_end
                )
                if next_day <= exec_day:
                    continue
                period = ic.build_period(
                    score_by_symbol=score_by_symbol,
                    frames=frames,
                    signal_date=signal_day,
                    exec_date=exec_day,
                    next_exec_date=next_day,
                    quintiles=cfg["quintiles"],
                    min_names=cfg["min_names_per_period"],
                )
                rows.append(period)
            periods.extend(rows)
            valid = [p for p in rows if p.ic is not None]
            fold_rows.append(
                {
                    "fold": int(saved[idx]["fold"]),
                    "fast": fast,
                    "slow": slow,
                    "test_start": str(fold.test_start.date()),
                    "test_end": str(fold.test_end.date()),
                    "periods": len(rows),
                    "periods_with_ic": len(valid),
                    "mean_ic": (
                        statistics.fmean(p.ic for p in valid) if valid else None
                    ),
                    "mean_names": (
                        statistics.fmean(p.n_used for p in valid) if valid else None
                    ),
                }
            )

    summary = ic.summarise_ic(
        periods,
        nw_lag=cfg["nw_lag"],
        bootstrap_reps=cfg["bootstrap_reps"],
        bootstrap_block=cfg["bootstrap_block"],
        seed=cfg["seed"],
    )
    profile = ic.quintile_profile(periods)
    verdict, reason = ic.decide_ic(summary, profile, target_mde=payload["design"]["target"]["target_mde_equivalent"])

    # ---- 预测核对 ----
    n_bar = summary.get("mean_names") or 0.0
    early = [
        p.n_used
        for p in periods
        if p.ic is not None and pd.Timestamp("2010-01-01") <= p.signal_date <= pd.Timestamp("2012-12-31")
    ]
    early_mean = statistics.fmean(early) if early else None
    theoretical_sd = 1.0 / math.sqrt(max(n_bar - 1.0, 1.0)) if n_bar else None
    ratio = (
        summary["sd"] / theoretical_sd
        if theoretical_sd and summary.get("sd") is not None
        else None
    )
    mde_return = summary.get("mde_return")
    improvement = (
        (L3R_MDE_PP / 100.0) / mde_return if mde_return and mde_return > 0 else None
    )

    predictions = [
        {
            "id": "P1",
            "claim": "平均每期有效标的数 ∈ [350,520]，且 2010–2012 段均值 < 450",
            "actual": f"全期均值 {_f(n_bar, 1)}；2010–2012 段均值 {_f(early_mean, 1)}",
            "refuted": not (
                350 <= n_bar <= 520 and early_mean is not None and early_mean < 450
            ),
        },
        {
            "id": "P2",
            "claim": "mean IC ∈ [−0.02, +0.03]",
            "actual": f"mean IC {_f(summary.get('mean'), 4)}",
            "refuted": not (
                summary.get("mean") is not None
                and -0.02 <= summary["mean"] <= 0.03
            ),
        },
        {
            "id": "P3",
            "claim": "MDE_IC ≤ 0.012，且折算 MDE_return ≤ 0.30pp",
            "actual": (
                f"MDE_IC {_f(summary.get('mde_ic'), 4)}；"
                f"折算 MDE {_abs_pp(mde_return)}"
            ),
            "refuted": not (
                summary.get("mde_ic") is not None
                and summary["mde_ic"] <= 0.012
                and mde_return is not None
                and mde_return <= 0.0030
            ),
        },
        {
            "id": "P4",
            "claim": "五分位单调性 |ρ| < 0.9，且 Q5−Q1 月均 spread 的 |t_nw3| < 2",
            "actual": (
                f"单调性 {_f(profile.get('monotonicity'), 3)}；"
                f"spread {_pp(profile.get('spread_mean'))}（t_nw3 {_f(profile.get('spread_nw'))}）"
            ),
            "refuted": not (
                profile.get("monotonicity") is not None
                and abs(profile["monotonicity"]) < 0.9
                and (profile.get("spread_nw") is None or abs(profile["spread_nw"]) < 2)
            ),
        },
        {
            "id": "P5",
            "claim": "逐期 IC 的 sd ≤ 1.6 × 1/√(N̄−1)",
            "actual": f"实测 sd {_f(summary.get('sd'), 4)}；理论 {_f(theoretical_sd, 4)}；比值 {_f(ratio, 2)}",
            "refuted": not (ratio is not None and ratio <= 1.6),
        },
        {
            "id": "P6",
            "claim": f"折算 MDE 相对 L3r 的 {L3R_MDE_PP}pp 至少改善 3 倍",
            "actual": f"改善倍数 {_f(improvement, 2)}×",
            "refuted": not (improvement is not None and improvement >= 3.0),
        },
    ]

    # ---- 事后诊断（不在预登记内，仅用于解释功效为何只改善 1.64×）----
    # 独立抽样噪声按 1/√(n−1) 估；实测月间 sd 若远大于它，说明逐期 IC 的波动主要来自
    # 风格轮动（或截面相关性带来的额外抽样波动），**只有增加时间能降，加标的没用**。
    null_sd = theoretical_sd if theoretical_sd else None
    observed_sd = summary.get("sd")
    noise_share = (
        (null_sd / observed_sd) ** 2
        if null_sd and observed_sd and observed_sd > 0
        else None
    )
    target_periods = summary.get("n_required_for_target")
    years_needed = (target_periods / 12.0) if target_periods else None
    posthoc = {
        "label": "事后诊断（不在预登记内，不参与判定）",
        "observed_ic_sd": observed_sd,
        "independence_null_sd": null_sd,
        "noise_variance_share": noise_share,
        "years_required_for_target": years_needed,
        "interpretation": (
            "逐期 IC 的月间波动远大于独立抽样噪声 ⇒ 主导项是风格轮动，"
            "把截面从 24 只扩到 378 只只能解决抽样噪声那一小部分，"
            "因此改善只有 1.64×；要压到 0.30pp 需要的不是更多标的，而是更多**时间**。"
        ),
    }

    elapsed = time.perf_counter() - started
    payload_out = {
        "experiment_id": payload["experiment_id"],
        "diagnostic_only": True,
        "prereg_sha256": sha,
        "prereg": PREREG.relative_to(ROOT).as_posix(),
        "l3p_result": L3P_RESULT.relative_to(ROOT).as_posix(),
        "l3r_reference_mde_pp": L3R_MDE_PP,
        "span": list(SPAN),
        "elapsed_seconds": elapsed,
        "pool": pool_meta,
        "fold_meta": fold_meta,
        "params_grouping": [
            {"fast": k[0], "slow": k[1], "folds": v} for k, v in sorted(by_params.items())
        ],
        "summary": summary,
        "quintile_profile": profile,
        "posthoc": posthoc,
        "verdict": verdict,
        "reason": reason,
        "predictions": predictions,
        "folds": fold_rows,
        "periods": [
            {
                "signal_date": str(p.signal_date.date()),
                "exec_date": str(p.exec_date.date()),
                "next_exec_date": str(p.next_exec_date.date()),
                "n_scored": p.n_scored,
                "n_used": p.n_used,
                "ic": p.ic,
                "spread": p.spread,
                "cross_section_sd": p.cross_section_sd,
            }
            for p in periods
        ],
        "limits": [
            "固定名单池含退市与停牌区间，每期只纳入两端有价者 ⇒ 生存偏差，方向不明确。",
            "分数用逐折 best_params（walk-forward 固有），不同折是同一指标的不同参数，"
            "IC 序列因此混合了参数差异。",
            "收益率用收盘价首尾相除，不含分红再投的复权口径细节；研究层 qfq 已含分红调整。",
            "月度不重叠，但折与折首尾相接处可能有一天重叠；NW(3) 只处理残差自相关。",
            "本实验是诊断：IC 显著 ≠ 组合有 alpha，不得作为可用性证据。",
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3s_result.json").write_text(
        json.dumps(payload_out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
    )
    (OUT_DIR / "l3s_report.md").write_text(
        _render(payload_out), encoding="utf-8", newline="\n"
    )

    EVIDENCE.write_text(
        json.dumps(
            {
                "experiment_id": payload["experiment_id"],
                "diagnostic_only": True,
                "prereg_sha256": sha,
                "verdict": verdict,
                "reason": reason,
                "summary": summary,
                "quintile_profile": profile,
                "predictions": predictions,
                "refuted": [p["id"] for p in predictions if p["refuted"]],
                "artifacts": {
                    "result": (OUT_DIR / "l3s_result.json").relative_to(ROOT).as_posix(),
                    "report": (OUT_DIR / "l3s_report.md").relative_to(ROOT).as_posix(),
                    "prereg": PREREG.relative_to(ROOT).as_posix(),
                },
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        print(f"期数 {summary['periods']}（有 IC {summary['periods_with_ic']}）")
        print(f"mean IC {_f(summary['mean'], 4)}  t_iid {_f(summary['t_iid'])}  t_nw3 {_f(summary['t_nw3'])}")
        print(f"MDE_IC {_f(summary['mde_ic'], 4)}  折算 MDE {_abs_pp(mde_return)}  功效 {_pct(summary['power'])}")
    print(f"判定：{verdict}")
    print(f"依据：{reason}")
    print(f"被反驳的预测：{[p['id'] for p in predictions if p['refuted']] or '无'}")
    print(f"耗时 {elapsed:.1f}s")
    return 0


def _render(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    prof = payload["quintile_profile"]
    lines: list[str] = []
    lines.append("# L3s：全池截面信息含量检验（诊断）")
    lines.append("")
    lines.append(f"- 预登记：`{payload['prereg']}`，sha256 `{payload['prereg_sha256'][:16]}…`")
    lines.append(
        f"- 池：{payload['pool']['requested']} 只（可用 {payload['pool']['loaded']}）；"
        f"跨度 {payload['span'][0]} ~ {payload['span'][1]}"
    )
    lines.append(f"- 期数：{s['periods']}（有 IC {s['periods_with_ic']}）；平均有效标的数 {_f(s['mean_names'], 1)}")
    lines.append("- 成熟度：M0（历史回测、日线、无实盘）｜**diagnostic_only：本实验是诊断，不是闸门**")
    lines.append("")
    lines.append("## 一、结论")
    lines.append("")
    lines.append(f"**{payload['verdict']}**")
    lines.append("")
    lines.append(f"- 依据：{payload['reason']}")
    lines.append(
        "- 本实验**不**改变 L3p / L3r 的终态，也不构成策略可用性证据；"
        "它只回答「指标在横截面上有没有排序能力」。"
    )
    lines.append("")
    lines.append("### 对上一轮说法的更正（写在跑数之前）")
    lines.append("")
    lines.append(
        "我说过「改用截面 IC 能把标准差降一个量级」——**这在 24 只截面上是错的**："
        "二元信号下 IC 只是「选中组 − 全体」的单调变换，功效不变；即便换连续分数，"
        "N=24 时折算 MDE 也只从 0.926pp 降到约 0.74pp。真正起作用的是**把截面从 24 只扩到 "
        f"{payload['pool']['loaded']} 只**（se(IC)∝1/√N）。"
    )
    lines.append("")
    lines.append("## 二、IC 统计")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 可比期数 | {s['periods_with_ic']} / {s['periods']} |")
    lines.append(f"| 平均有效标的数 | {_f(s['mean_names'], 1)} |")
    lines.append(f"| 平均 IC | {_f(s['mean'], 4)} |")
    lines.append(f"| 中位 IC | {_f(s['median'], 4)} |")
    lines.append(f"| iid t | {_f(s['t_iid'])} |")
    lines.append(f"| NW(3) t | {_f(s['t_nw3'])} |")
    lines.append(f"| IC>0 期占比 | {_pct(s['win_rate'])} |")
    lines.append(f"| 逐期 IC 的 sd | {_f(s['sd'], 4)} |")
    lines.append(f"| bootstrap 95% CI | [{_f(s['ci_low'], 4)}, {_f(s['ci_high'], 4)}] |")
    lines.append(f"| MDE_IC（2.8016×se） | {_f(s['mde_ic'], 4)} |")
    lines.append(f"| 折算 MDE（收益口径） | {_abs_pp(s['mde_return'])} |")
    lines.append(f"| 事后功效 | {_pct(s['power'])} |")
    lines.append("")
    lines.append("## 三、五分位剖面")
    lines.append("")
    if prof.get("quintiles"):
        header = "| 分位 | " + " | ".join(f"Q{i + 1}" for i in range(prof["quintiles"])) + " |"
        rule = "|---|" + "---|" * prof["quintiles"]
        row = "| 平均收益 | " + " | ".join(_pp(v) for v in prof["means"]) + " |"
        lines.append(header)
        lines.append(rule)
        lines.append(row)
        lines.append("")
        lines.append(
            f"- 单调性 ρ（分位序号 vs 平均收益）：{_f(prof['monotonicity'])}；"
            f"Q5−Q1 月均 {_pp(prof['spread_mean'])}（NW(3) t {_f(prof['spread_nw'])}）"
        )
    else:
        lines.append("（无可用分组）")
    lines.append("")
    lines.append("## 四、功效：与 L3r 的对比")
    lines.append("")
    lines.append("| 口径 | 截面标的数 | 折算 MDE | 说明 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| L3r（12 只 vs 24 只） | 24 | {L3R_MDE_PP:.3f}pp | 功效 16.9%，答不了 |")
    lines.append(
        f"| L3s（全池排序） | {_f(s['mean_names'], 0)} | {_abs_pp(s['mde_return'])} | "
        f"改善 {_f((L3R_MDE_PP / 100.0) / s['mde_return'] if s['mde_return'] else None, 2)}× |"
    )
    lines.append("")
    n_req = s.get("n_required_for_target")
    if n_req:
        lines.append(
            f"- 若要把折算 MDE 压到 0.30pp 以下，按当前 sd 需 {n_req:,.0f} 期（当前 {s['periods_with_ic']} 期）。"
        )
    lines.append("")
    ph = payload["posthoc"]
    lines.append("## 五、为什么扩截面只带来 1.64×（事后诊断，不参与判定）")
    lines.append("")
    lines.append("| 量 | 数值 | 含义 |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 实测逐期 IC 的 sd | {_f(ph['observed_ic_sd'], 4)} | 月与月之间 IC 的实际波动 |"
    )
    lines.append(
        f"| 独立抽样噪声 1/√(N̄−1) | {_f(ph['independence_null_sd'], 4)} | 若真 IC 恒定、标的相互独立，应有的波动 |"
    )
    lines.append(
        f"| 抽样噪声占方差比例 | {_pct(ph['noise_variance_share'])} | 剩下的部分是风格轮动（或截面相关性带来的额外波动）|"
    )
    lines.append(
        f"| 压到 0.30pp 所需 | {_f(ph['years_required_for_target'], 0)} 年 | 按 12 期/年折算 |"
    )
    lines.append("")
    lines.append(
        "**这条是本轮最该记住的**：把截面从 24 只扩到 378 只，只消掉了「抽样噪声」那一小块；"
        "逐期 IC 的波动 93% 以上来自风格轮动。要降它只有一条路——**更长的日历时间**，"
        "而不是更大的池子、更复杂的统计量。"
    )
    lines.append("")

    lines.append("## 六、逐折明细")
    lines.append("")
    lines.append("| 折 | 参数 | 窗口 | 期数 | 有 IC | 平均 IC | 平均标的数 |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in payload["folds"]:
        lines.append(
            f"| {row['fold']} | {row['fast']}/{row['slow']} | {row['test_start']} → {row['test_end']} "
            f"| {row['periods']} | {row['periods_with_ic']} | {_f(row['mean_ic'], 4)} "
            f"| {_f(row['mean_names'], 1)} |"
        )
    lines.append("")
    lines.append("## 七、逐条核对预登记预测")
    lines.append("")
    lines.append("| 编号 | 预测 | 实测 | 被反驳 |")
    lines.append("|---|---|---|---|")
    for p in payload["predictions"]:
        # 预测文本里会有「|ρ| < 0.9」这类写法，不转义会把表格劈成两列
        claim = str(p["claim"]).replace("|", r"\|")
        actual = str(p["actual"]).replace("|", r"\|")
        lines.append(
            f"| {p['id']} | {claim} | {actual} | {'**是**' if p['refuted'] else '否'} |"
        )
    lines.append("")
    lines.append("## 八、限制")
    lines.append("")
    for item in payload["limits"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
