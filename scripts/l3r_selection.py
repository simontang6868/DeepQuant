"""L3r：选择效应检验 CLI。

用法：
    python scripts/l3r_selection.py [--quiet]

问题：ma_cross 的选股，相对「在池内**当期能成交**的标的上等权」，有没有正贡献？
背景：L3p 的 `per_invested = −7.87pp` 不可解释（L3q 证明两臂现金 69%/95% 不可投），
所以换两把不依赖「现金可投」假设的尺子重新问：R1 同口径组合对照、R2 截面级毛收益。

退出码：0 = SELECTION_EFFECT_DETECTED；2 = 降级为 BLOCKED；其他 = 其他初判。
硬约束：
1. 预登记 sha256 必须与脚本内常量一致（被改动即拒跑）；
2. 逐折读 L3p 存档 best_params，不重选参、不重切折；
3. 两把尺子必须并列报，结论不同向时以 R2 为准并说明 R1 被现金结构污染。
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

PREREG_PATH = ROOT / "docs" / "gates" / "l3r_prereg.json"
PREREG_SHA256 = "b5cfca5589c0d6470925c699bb76c6e6eb0382ec0b9fa11508aa29736524cc83"

SPEC_PATH = ROOT / "docs" / "gates" / "specs" / "H_L3P_MACROSS_000001.json"
UNIVERSE_PATH = ROOT / "docs" / "gates" / "universes" / "H_L3P_UNIVERSE_30.json"
L3P_RESULT = ROOT / "reports" / "gates" / "H-L3P-MACROSS-000001" / "l3p_result.json"
PRIOR_RESULTS = {
    "L1a": (ROOT / "reports" / "gates" / "H-L1A-MACROSS-000001" / "result.json", ("verdict",)),
    "L1b": (ROOT / "reports" / "gates" / "H-L1B-MACROSS-000001" / "l1b_result.json", ("result", "verdict")),
    "L2": (ROOT / "reports" / "gates" / "H-L2-MACROSS-000001" / "l2_result.json", ("result", "verdict")),
    "L3": (ROOT / "reports" / "gates" / "H-L3-MACROSS-000001" / "l3_result.json", ("result", "verdict")),
    "L3p": (ROOT / "reports" / "gates" / "H-L3P-MACROSS-000001" / "l3p_result.json", ("result", "verdict")),
}
OUT_DIR = ROOT / "reports" / "gates" / "H-L3R-SELECTION"
EVIDENCE_PATH = ROOT / "reports" / "L3R_SELECTION_EVIDENCE.json"

MATURITY = "M0"


def verify_prereg() -> dict[str, Any]:
    if not PREREG_PATH.exists():
        raise SystemExit(f"✗ 找不到预登记：{PREREG_PATH.relative_to(ROOT)}")
    raw = PREREG_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PREREG_SHA256:
        raise SystemExit(
            "✗ 预登记哈希不符，拒绝运行\n"
            f"  期望 {PREREG_SHA256}\n  实际 {digest}\n"
            "  预登记冻结后不得修改；要改就另开一份新的预登记。"
        )
    return json.loads(raw.decode("utf-8"))


def _dig(payload: Any, path: tuple[str, ...]) -> Any:
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def collect_prior_verdicts() -> tuple[dict[str, str], bool, list[str]]:
    verdicts: dict[str, str] = {}
    missing: list[str] = []
    for name, (path, keys) in PRIOR_RESULTS.items():
        if not path.exists():
            verdicts[name] = "UNKNOWN"
            missing.append(f"{name}：找不到 {path.relative_to(ROOT)}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            verdicts[name] = "UNKNOWN"
            missing.append(f"{name}：无法解析（{exc}）")
            continue
        value = _dig(payload, keys)
        verdicts[name] = str(value) if value else "UNKNOWN"
        if not value:
            missing.append(f"{name}：缺少 {'/'.join(keys)}")
    passed = all(v.endswith("PASSED") for v in verdicts.values())
    return verdicts, passed, missing


def _pp(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.3f}pp"


def _abs_pp(value: float | None) -> str:
    """量级类数字（MDE、标准差）不带正负号——符号对它们没有意义。"""
    return "—" if value is None or value != value else f"{value * 100:.3f}pp"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _num(value: float | None, digits: int = 3) -> str:
    return "—" if value is None or value != value else f"{value:.{digits}f}"


def evaluate_predictions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    r2 = payload["r2_summary"]
    r1 = payload["r1_summary"]
    cash = payload["cash_comparison"]
    corr = payload["instrument_consistency"]
    out: list[dict[str, Any]] = []

    mean = r2["mean"]
    t_iid = r2["t_iid"]
    out.append(
        {
            "id": "P1",
            "claim": "R2 月均 Δ ∈ [−0.40pp, +0.40pp] 且 \\|t_iid\\| < 2",
            "actual": f"月均 {_pp(mean)}，t_iid {_num(t_iid)}",
            "refuted": not (
                mean is not None
                and -0.004 <= mean <= 0.004
                and t_iid is not None
                and abs(t_iid) < 2
            ),
        }
    )

    r1_mean = r1["mean"]
    r1_win = r1["win_rate"]
    out.append(
        {
            "id": "P2",
            "claim": "R1 折均 Δ ∈ [−0.5pp, +1.0pp]，折胜率 40%~62%",
            "actual": f"折均 {_pp(r1_mean)}，胜率 {_pct(r1_win)}",
            "refuted": not (
                r1_mean is not None
                and -0.005 <= r1_mean <= 0.010
                and r1_win is not None
                and 0.40 <= r1_win <= 0.62
            ),
        }
    )

    out.append(
        {
            "id": "P3",
            "claim": "C_t 现金中位 ≤ 4pp，且两臂现金差中位 ≥ 5pp",
            "actual": f"C_t 现金中位 {_pct(cash['control_median'])}，"
            f"现金差中位 {_pct(cash['gap_median'])}",
            "refuted": not (
                cash["control_median"] is not None
                and cash["control_median"] <= 0.04
                and cash["gap_median"] is not None
                and cash["gap_median"] >= 0.05
            ),
        }
    )

    out.append(
        {
            "id": "P4",
            "claim": "R2 中 Δ>0 的期数占比 ∈ 40%~58%",
            "actual": f"胜率 {_pct(r2['win_rate'])}（{r2['periods_compared']} 期）",
            "refuted": not (
                r2["win_rate"] is not None and 0.40 <= r2["win_rate"] <= 0.58
            ),
        }
    )

    out.append(
        {
            "id": "P5",
            "claim": "R1 折级 Δ 与 R2 折内均值 Δ 相关 > 0.5",
            "actual": f"r = {_num(corr['pearson'])}（{corr['folds']} 折）",
            "refuted": not (
                corr["pearson"] is not None and corr["pearson"] > 0.5
            ),
        }
    )
    return out


def render_markdown(payload: dict[str, Any]) -> str:
    r1 = payload["r1_summary"]
    r2 = payload["r2_summary"]
    cash = payload["cash_comparison"]
    corr = payload["instrument_consistency"]
    lines: list[str] = []
    lines.append("# L3r：选择效应检验")
    lines.append("")
    lines.append(f"- 问题：{payload['question']}")
    lines.append(
        f"- 预登记：`{payload['prereg']}`，sha256 `{payload['prereg_sha256'][:16]}…`"
    )
    lines.append(f"- 折：{payload['folds_usable']}/{payload['folds_total']}；期：{r2['periods_compared']}")
    lines.append(f"- 成熟度：{payload['maturity']}（历史回测、日线、无实盘）")
    lines.append("")
    lines.append("## 一、结论")
    lines.append("")
    lines.append(f"**初判：`{payload['preliminary_verdict']}`**")
    lines.append("")
    lines.append(f"- 依据：{payload['preliminary_reason']}")
    lines.append(
        f"- 终态：`{payload['verdict']}`"
        + ("（前序未全部通过，按规矩降级；降级不改变初判文字）" if payload["verdict"] != payload["preliminary_verdict"] else "")
    )
    lines.append("")
    lines.append("## 二、R2 截面级（主尺子：毛收益、无现金、无整手）")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 可比期数 | {r2['periods_compared']} / {r2['periods']} |")
    lines.append(f"| 月均 Δ | {_pp(r2['mean'])} |")
    lines.append(f"| 中位 Δ | {_pp(r2['median'])} |")
    lines.append(f"| iid t | {_num(r2['t_iid'])} |")
    lines.append(f"| NW(3) t | {_num(r2['t_nw3'])} |")
    lines.append(f"| 胜率（Δ>0 期占比） | {_pct(r2['win_rate'])} |")
    lines.append(f"| bootstrap 95% CI | [{_pp(r2['ci_low'])}, {_pp(r2['ci_high'])}] |")
    lines.append(f"| MDE（2.8016×se） | {_abs_pp(r2['mde'])} |")
    lines.append(f"| 事后功效 | {_pct(r2['power'])} |")
    lines.append(
        f"| 平均持仓数（策略 / 池） | {_num(r2['mean_n_strategy'], 1)} / {_num(r2['mean_n_universe'], 1)} |"
    )
    lines.append(f"| 策略持仓可定价比例 | {_pct(r2['mean_coverage'])} |")
    lines.append("")
    lines.append("> R2 是**毛收益**：不计费用、不计整手、不进组合账户。它测的是「选得准不准」，")
    lines.append("> 不是「能不能赚到」。任何「成本后」结论都不得从这一节引用。")
    lines.append("")
    pa = payload["power_analysis"]
    lines.append("## 三、这个设计有多少功效（能不能回答这个问题）")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 可比较的独立期数 | {pa['periods']} |")
    lines.append(f"| 逐期 Δ 的标准差 | {_abs_pp(pa['sd'])} |")
    lines.append(f"| 当前 MDE | {_abs_pp(pa['mde_now'])} |")
    n_req = pa.get("n_required_for_target")
    y_req = pa.get("years_required_for_target")
    lines.append(
        f"| 把 MDE 压到 {_abs_pp(pa['target_mde'])} 所需期数 | "
        f"{'—' if n_req is None else f'{n_req:,.0f} 期（≈{y_req:,.0f} 年）'} |"
    )
    n_obs = pa.get("n_required_for_observed")
    y_obs = pa.get("years_required_for_observed")
    lines.append(
        f"| 检测当前点估计所需期数 | {'—' if n_obs is None else f'{n_obs:,.0f} 期（≈{y_obs:,.0f} 年）'} |"
    )
    lines.append("")
    lines.append(
        "> 逐期 Δ 的 sd 是结构性的：它约等于「12 只组合 vs 24 只组合」的等权差噪声"
        "（σ_idio×√(1/12+1/24)）。换池子、换频率都会改变它，但**不会**让它消失。"
    )
    lines.append("")
    mm = pa["mean_vs_median"]
    lines.append("### 均值与中位方向不同，必须两个都报")
    lines.append("")
    lines.append(
        f"- R2：均值 {_pp(mm['r2_mean'])}、中位 {_pp(mm['r2_median'])}；"
        f"R1：均值 {_pp(mm['r1_mean'])}、中位 {_pp(mm['r1_median'])}"
    )
    lines.append(
        f"- {'两者同号' if mm['same_sign'] else '**两者反号**：少数大负期拖垮均值，典型期其实是小正'}"
    )
    worst = payload["tails"]["worst"]
    best = payload["tails"]["best"]
    lines.append(
        "- 最差折：" + "、".join(f"折 {f} {_pp(d)}" for d, f in worst)
        + "；最好折：" + "、".join(f"折 {f} {_pp(d)}" for d, f in best)
    )
    lines.append("")

    lines.append("## 四、R1 组合级（同口径对照：C_t 只买当期能成交的标的）")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 折均 Δ | {_pp(r1['mean'])} |")
    lines.append(f"| 折中位 Δ | {_pp(r1['median'])} |")
    lines.append(f"| 折胜率 | {_pct(r1['win_rate'])}（{r1['wins']}/{r1['folds']}） |")
    lines.append(f"| iid t | {_num(r1['t_iid'])} |")
    lines.append(f"| NW(3) t | {_num(r1['t_nw3'])} |")
    lines.append(f"| 策略臂现金中位 | {_pct(cash['strategy_median'])} |")
    lines.append(f"| 对照臂 C_t 现金中位 | {_pct(cash['control_median'])} |")
    lines.append(f"| 现金差中位（S − C_t） | {_pct(cash['gap_median'])} |")
    lines.append("")
    lines.append("## 五、两把尺子是否指向同一方向")
    lines.append("")
    lines.append(
        f"- 折级相关 r = {_num(corr['pearson'])}（{corr['folds']} 折）；"
        f"符号一致 {corr['same_sign']}/{corr['folds']} 折"
    )
    lines.append(
        "- 相关低说明 R1 被现金与执行结构改写，R1 不能单独当选择效应的证据（这也是 L3p 的老问题）。"
    )
    lines.append("")
    lines.append("## 六、逐折明细")
    lines.append("")
    lines.append("| 折 | 测试窗 | 策略收益 | C_t 收益 | Δ(R1) | 策略现金 | C_t 现金 | R2 折内均值 | R2 期数 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for item in payload["folds"]:
        lines.append(
            f"| {item['fold']} | {item['test_start']} → {item['test_end']} "
            f"| {_pct(item['strategy_return'])} | {_pct(item['control_return'])} "
            f"| {_pp(item['delta'])} | {_pct(item['strategy_cash'])} | {_pct(item['control_cash'])} "
            f"| {_pp(item['r2_mean'])} | {item['r2_periods']} |"
        )
    lines.append("")
    lines.append("## 七、逐条核对预登记预测")
    lines.append("")
    lines.append("| 编号 | 预测 | 实测 | 是否被反驳 |")
    lines.append("|---|---|---|---|")
    for item in payload["predictions"]:
        lines.append(
            f"| {item['id']} | {item['claim']} | {item['actual']} | "
            f"{'**是**' if item['refuted'] else '否'} |"
        )
    lines.append("")
    lines.append("## 八、限制")
    lines.append("")
    for item in payload["limits"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3r 选择效应检验")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    prereg = verify_prereg()

    import pandas as pd

    from deepquant.backtest.config import BacktestConfig
    from deepquant.backtest.portfolio import PortfolioBacktestConfig, run_portfolio_backtest
    from deepquant.gates import load_spec
    from deepquant.gates.l3 import build_folds, parse_walk_forward
    from deepquant.gates.l3_portfolio import (
        _signal_dates_for,
        load_universe,
        parse_portfolio_spec,
        schedule_from_states,
        state_from_events,
    )
    from deepquant.gates.selection import (
        decide_selection,
        period_effects,
        summarise_effects,
        to_selection_verdict,
        tradable_control_schedule,
    )
    from deepquant.gates.sweep import default_strategy_factory

    spec = load_spec(SPEC_PATH)
    raw_spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    wf = parse_walk_forward(spec.walk_forward)
    pf = parse_portfolio_spec(raw_spec.get("portfolio"))
    universe = load_universe(
        UNIVERSE_PATH,
        data_dir=ROOT / "data" / "qfq_all",
        span_start=wf.span_start,
        span_end=wf.span_end,
    )
    saved = json.loads(L3P_RESULT.read_text(encoding="utf-8"))["result"]["folds"]

    all_days = sorted(set().union(*(set(f.index) for f in universe.frames.values())))
    calendar = pd.DatetimeIndex(all_days)
    calendar = calendar[
        (calendar >= pd.Timestamp(wf.span_start)) & (calendar <= pd.Timestamp(wf.span_end))
    ]
    folds, _ = build_folds(calendar, wf)
    if len(folds) != len(saved):
        raise SystemExit(f"✗ 折数不一致：重切 {len(folds)} vs 存档 {len(saved)}")

    prior, prior_passed, prior_missing = collect_prior_verdicts()
    started = time.perf_counter()
    fold_records: list[dict[str, Any]] = []
    all_effects: list[Any] = []

    def make_config(cash: float) -> PortfolioBacktestConfig:
        return PortfolioBacktestConfig(
            symbols=tuple(universe.constituents),
            initial_cash=cash,
            fee_config=BacktestConfig(
                symbol=universe.universe_id,
                initial_cash=cash,
                slippage_buy=0.0,
                slippage_sell=0.0,
            ),
            allocation="weights",
            max_weight_per_asset=pf.max_weight_per_asset,
        )

    for fold, saved_fold in zip(folds, saved):
        params = saved_fold["best_params"]
        states = {
            symbol: state_from_events(
                default_strategy_factory(spec.strategy_id, params).generate_signals(frame)
            )
            for symbol, frame in universe.frames.items()
        }
        signal_dates = _signal_dates_for(calendar, fold.test_start, fold.test_end)
        strategy = schedule_from_states(
            states, signal_dates, max_weight_per_asset=pf.max_weight_per_asset
        )
        control = tradable_control_schedule(
            universe.constituents,
            signal_dates,
            calendar=calendar,
            frames=universe.frames,
            max_weight_per_asset=pf.max_weight_per_asset,
        )

        # ---- R1：两条臂都进组合账户（真实费用、上限、整手 1 股）----
        strategy_result = run_portfolio_backtest(
            universe.frames,
            strategy,
            make_config(pf.initial_cash),
            start_date=fold.test_start,
            end_date=fold.test_end,
        )
        control_result = run_portfolio_backtest(
            universe.frames,
            control,
            make_config(pf.initial_cash),
            start_date=fold.test_start,
            end_date=fold.test_end,
        )
        strategy_return = strategy_result.cumulative_return
        control_return = control_result.cumulative_return
        delta = (
            float(strategy_return) - float(control_return)
            if strategy_return is not None and control_return is not None
            else None
        )

        def cash_median(result) -> float | None:
            values = [
                state.cash / state.total_equity
                for state in result.states
                if state.total_equity
            ]
            return statistics.fmean(values) if values else None

        # ---- R2：截面级，不进账户 ----
        effects = period_effects(
            strategy,
            symbols=universe.constituents,
            calendar=calendar,
            frames=universe.frames,
            window_end=fold.test_end,
        )
        all_effects.extend(effects)
        r2_deltas = [e.delta for e in effects if e.delta is not None]

        fold_records.append(
            {
                "fold": saved_fold["fold"],
                "test_start": str(fold.test_start.date()),
                "test_end": str(fold.test_end.date()),
                "best_params": params,
                "strategy_return": float(strategy_return) if strategy_return is not None else None,
                "control_return": float(control_return) if control_return is not None else None,
                "delta": delta,
                "strategy_cash": cash_median(strategy_result),
                "control_cash": cash_median(control_result),
                "r2_mean": statistics.fmean(r2_deltas) if r2_deltas else None,
                "r2_periods": len(r2_deltas),
            }
        )
        if not args.quiet:
            print(
                f"  折 {saved_fold['fold']:>2} S {_pct(strategy_return)} C_t {_pct(control_return)} "
                f"Δ {_pp(delta)}｜R2 {_pp(fold_records[-1]['r2_mean'])}（{len(r2_deltas)} 期）"
            )

    elapsed = time.perf_counter() - started

    # ---- 汇总 ----
    r2_summary = summarise_effects(all_effects)
    fold_deltas = [f["delta"] for f in fold_records if f["delta"] is not None]
    from deepquant.gates.selection import newey_west_mean, t_stat

    r1_base = t_stat(fold_deltas)
    r1_nw = newey_west_mean(fold_deltas, lag=3)
    r1_summary = {
        "folds": len(fold_deltas),
        "mean": r1_base["mean"] if r1_base["n"] else None,
        "median": statistics.median(fold_deltas) if fold_deltas else None,
        "se_iid": r1_base["se"] if r1_base["n"] else None,
        "t_iid": r1_base["t"] if r1_base["n"] else None,
        "t_nw3": r1_nw["t_nw"],
        "wins": sum(1 for d in fold_deltas if d > 0),
        "win_rate": (sum(1 for d in fold_deltas if d > 0) / len(fold_deltas)) if fold_deltas else None,
    }

    strategy_cash = [f["strategy_cash"] for f in fold_records if f["strategy_cash"] is not None]
    control_cash = [f["control_cash"] for f in fold_records if f["control_cash"] is not None]
    gaps = [
        f["strategy_cash"] - f["control_cash"]
        for f in fold_records
        if f["strategy_cash"] is not None and f["control_cash"] is not None
    ]
    cash_comparison = {
        "strategy_median": statistics.median(strategy_cash) if strategy_cash else None,
        "control_median": statistics.median(control_cash) if control_cash else None,
        "gap_median": statistics.median(gaps) if gaps else None,
    }

    pairs = [
        (f["delta"], f["r2_mean"])
        for f in fold_records
        if f["delta"] is not None and f["r2_mean"] is not None
    ]
    pearson: float | None = None
    if len(pairs) >= 3:
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
        pearson = num / den if den else None
    instrument = {
        "pearson": pearson,
        "folds": len(pairs),
        "same_sign": sum(1 for a, b in pairs if (a > 0) == (b > 0)),
    }

    preliminary, reason = decide_selection(r2_summary, fold_win_rate=r1_summary["win_rate"])
    verdict = to_selection_verdict(preliminary, prior_passed=prior_passed)

    # ---- 功效分析：这个设计到底需要多少样本才能回答 ----
    # 只报「不显著」是没用的：必须回答「要多少样本才可能显著」。MDE ∝ 1/√n，
    # 所以把 MDE 压到目标精度所需期数 = (2.8016·sd/target)²，与均值无关。
    sd = r2_summary.get("sd")
    n_periods = r2_summary.get("periods_compared") or 0
    power_analysis: dict[str, Any] = {
        "periods": n_periods,
        "sd": sd,
        "mde_now": r2_summary.get("mde"),
        "target_mde": 0.005,
        "target_note": "目标精度 0.5pp/月，略高于本项目已确认的成本量级（月度双边约 0.10%）",
    }
    if sd and sd == sd and sd > 0:
        power_analysis["n_required_for_target"] = (2.8016 * sd / 0.005) ** 2
        power_analysis["years_required_for_target"] = power_analysis["n_required_for_target"] / 12
        mean = r2_summary.get("mean")
        if mean and mean == mean and abs(mean) > 0:
            power_analysis["n_required_for_observed"] = (2.8016 * sd / abs(mean)) ** 2
            power_analysis["years_required_for_observed"] = (
                power_analysis["n_required_for_observed"] / 12
            )
    # 均值与中位的方向分歧：只报均值会把「少数大负期」说成「系统性为负」
    r2_mean_v = r2_summary.get("mean")
    r2_median_v = r2_summary.get("median")
    power_analysis["mean_vs_median"] = {
        "r2_mean": r2_mean_v,
        "r2_median": r2_median_v,
        "r1_mean": r1_summary.get("mean"),
        "r1_median": r1_summary.get("median"),
        "same_sign": (
            None
            if r2_mean_v is None or r2_median_v is None
            else (r2_mean_v > 0) == (r2_median_v > 0)
        ),
    }

    limits = [
        "R2 两端有价的匹配规则把死票同时从两臂剔除，因此不含「持有到退市」的真实损失；"
        "这条对两臂对称，但不等于完整组合收益。",
        "折边界处每折末期按窗口末日截断，约 200 期序列有 28 个小缺口，t 的独立性只是近似。",
        "逐折使用不同 best_params（walk-forward 固有），「策略」在不同折上是同族的不同参数。",
        "R1 不含涨停买不进的摩擦（组合层未启用涨跌停判据）。",
        "R2 的等权组合不计整手与费用，小资金下不可实现；不得与任何「成本后」数字并列。",
        "本实验只回答「选择有没有用」，不构成策略可用性的证据：终态为 BLOCKED，"
        "前序任一级未通过时都不得据此推进。",
    ]

    payload: dict[str, Any] = {
        "title": "L3r：选择效应检验",
        "experiment_id": prereg["experiment_id"],
        "question": prereg["title"],
        "prereg": PREREG_PATH.relative_to(ROOT).as_posix(),
        "prereg_sha256": PREREG_SHA256,
        "maturity": MATURITY,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": round(elapsed, 3),
        "prior_verdicts": prior,
        "prior_missing": prior_missing,
        "folds_total": len(folds),
        "folds_usable": len(fold_deltas),
        "r1_summary": r1_summary,
        "r2_summary": r2_summary,
        "cash_comparison": cash_comparison,
        "instrument_consistency": instrument,
        "power_analysis": power_analysis,
        "tails": {
            "worst": sorted(
                ((f["delta"], f["fold"]) for f in fold_records if f["delta"] is not None)
            )[:3],
            "best": sorted(
                ((f["delta"], f["fold"]) for f in fold_records if f["delta"] is not None)
            )[-3:],
        },
        "preliminary_verdict": preliminary,
        "preliminary_reason": reason,
        "verdict": verdict,
        "folds": fold_records,
        "limits": limits,
    }
    payload["predictions"] = evaluate_predictions(payload)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3r_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (OUT_DIR / "l3r_report.md").write_text(
        render_markdown(payload) + "\n", encoding="utf-8", newline="\n"
    )
    (OUT_DIR / "periods.json").write_text(
        json.dumps([e.to_dict() for e in all_effects], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    evidence = {
        "experiment_id": prereg["experiment_id"],
        "prereg_sha256": PREREG_SHA256,
        "maturity": MATURITY,
        "artifacts": {
            "result": (OUT_DIR / "l3r_result.json").relative_to(ROOT).as_posix(),
            "report": (OUT_DIR / "l3r_report.md").relative_to(ROOT).as_posix(),
            "periods": (OUT_DIR / "periods.json").relative_to(ROOT).as_posix(),
        },
        "verdict": verdict,
        "preliminary_verdict": preliminary,
        "preliminary_reason": reason,
        "r1_summary": r1_summary,
        "r2_summary": r2_summary,
        "cash_comparison": cash_comparison,
        "instrument_consistency": instrument,
        "predictions": payload["predictions"],
        "folds_total": len(folds),
        "folds_usable": len(fold_deltas),
    }
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    refuted = [p["id"] for p in payload["predictions"] if p["refuted"]]
    if not args.quiet:
        print("")
        print(f"R2 月均 Δ：{_pp(r2_summary['mean'])}（t_iid {_num(r2_summary['t_iid'])}，"
              f"t_nw3 {_num(r2_summary['t_nw3'])}，{r2_summary['periods_compared']} 期）")
        print(f"MDE {_pp(r2_summary['mde'])}，事后功效 {_pct(r2_summary['power'])}")
        print(f"R1 折均 Δ：{_pp(r1_summary['mean'])}，折胜率 {_pct(r1_summary['win_rate'])}")
        print(f"初判：{preliminary}")
        print(f"终态：{verdict}")
        print(f"被反驳的预测：{refuted or '无'}")
        print(f"报告：{(OUT_DIR / 'l3r_report.md').relative_to(ROOT)}")

    if verdict == "L3R_BLOCKED_PREREQUISITE":
        return 2
    if verdict == "SELECTION_EFFECT_DETECTED":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
