"""L3q：L3p 组合臂现金占比归因审计的 CLI。

用法：
    python scripts/l3q_cash_audit.py [--quiet]

本脚本**不产生放行结论**，只回答一个问题：L3p 报告里的
``avg_cash_ratio_median = 11.71%`` 是哪些机制造成的、各占多少。

三条硬约束（违反即退出码 1）：
1. 预登记文件的 sha256 必须等于脚本里写死的值——预登记一旦被改过就不能再跑，
   否则「先预测后测量」变成「先测量后补预测」。
2. 基线臂（weights @ 1000 万）必须逐折复现 ``l3p_result.json`` 的 ``avg_cash_ratio``
   （容差 1e-12）。复现不了说明口径已经漂了，全部归因作废。
3. 参数不重选：逐折直接读 L3p 存档的 ``best_params``。重选参就把「现金口径」换成
   「参数选择」问题，两者混淆后无法归因。
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

PREREG_PATH = ROOT / "docs" / "gates" / "l3q_prereg.json"
#: 冻结时的哈希。预登记被改动后本脚本拒绝运行。
PREREG_SHA256 = "176cd8c3b1d4661194fa9b1f32d2487436a60a7b4114efc247b4d0e665f86857"

SPEC_PATH = ROOT / "docs" / "gates" / "specs" / "H_L3P_MACROSS_000001.json"
UNIVERSE_PATH = ROOT / "docs" / "gates" / "universes" / "H_L3P_UNIVERSE_30.json"
L3P_RESULT = ROOT / "reports" / "gates" / "H-L3P-MACROSS-000001" / "l3p_result.json"
OUT_DIR = ROOT / "reports" / "gates" / "H-L3Q-CASH-AUDIT"
EVIDENCE_PATH = ROOT / "reports" / "L3Q_CASH_AUDIT_EVIDENCE.json"

#: 资本敏感性档位（元）。硬编码：从命令行传会让「同一份预登记跑出不同结果」。
CAPITAL_LEVELS: tuple[float, ...] = (1_000_000.0, 3_000_000.0, 10_000_000.0, 30_000_000.0, 100_000_000.0)

#: 基线与 L3p 存档的复现容差
REPRO_TOLERANCE = 1e-12


def verify_prereg() -> dict[str, Any]:
    """校验预登记未被改动，返回其内容。"""
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


def _ratio_pp(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.3f}pp"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _money(capital: float) -> str:
    """资金标签：≥1 亿用「亿」，否则用「万」。"""
    return f"{capital / 1e8:.0f} 亿" if capital >= 1e8 else f"{capital / 1e4:.0f} 万"


def _f(value: float | None) -> str:
    return "—" if value is None else f"{value:+.4f}"


def evaluate_predictions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """逐条核对预登记里的六条预测。只陈述事实与是否被反驳，不改判据。"""
    folds = payload["folds"]
    summary = payload["summary"]
    control = payload["control_summary"]
    capital = payload["capital_sweep"]
    diagnostic = payload["diagnostic_arm"]
    base_returns = [item["test_return"] for item in folds]

    out: list[dict[str, Any]] = []

    # ---- P1 整手增量 ----
    deltas = [item["lots_delta"] for item in folds if item["lots_delta"] is not None]
    p1_median = statistics.median(deltas)
    p1_max = max(deltas)
    out.append(
        {
            "id": "P1",
            "claim": "整手增量中位 < 0.5pp，最大 < 2pp",
            "actual": f"中位 {p1_median * 100:+.3f}pp，最大 {p1_max * 100:+.3f}pp",
            "refuted": not (p1_median < 0.005 and p1_max < 0.02),
        }
    )

    # ---- P2 权重上限 ----
    cap_ratio = (
        summary["periods_with_cap"] / summary["periods_total"]
        if summary["periods_total"]
        else 0.0
    )
    cap_median = summary["component_median"].get("cap", 0.0)
    out.append(
        {
            "id": "P2",
            "claim": "上限触发期 < 10%，对现金占比的中位贡献 < 1pp",
            "actual": f"触发期 {cap_ratio:.2%}（{summary['periods_with_cap']}/{summary['periods_total']}），"
            f"中位贡献 {cap_median * 100:+.3f}pp",
            "refuted": not (cap_ratio < 0.10 and cap_median < 0.01),
        }
    )

    # ---- P3 数据终止 ----
    from deepquant.gates.cash_audit import spearman

    rho = payload["dead_weight_spearman"]
    fold_gap = folds[-1]["avg_cash_ratio"] - folds[0]["avg_cash_ratio"]
    out.append(
        {
            "id": "P3",
            "claim": "死票权重与现金占比 Spearman > 0.7；末折比首折高 5pp 以上",
            "actual": f"ρ = {rho:.4f}；折 {folds[-1]['fold']} − 折 {folds[0]['fold']} = {fold_gap * 100:+.3f}pp",
            "refuted": not (rho is not None and rho > 0.7 and fold_gap > 0.05),
            "caveat": "机制非盲测（探针先看过 3 折），只有量级与相关计为预测",
        }
    )

    # ---- P4 对照臂 ----
    out.append(
        {
            "id": "P4",
            "claim": "对照臂现金中位 > 8pp，其中未上市 + 数据终止占现金 > 70%",
            "actual": f"中位 {_pct(control['avg_cash_ratio_median'])}；"
            f"未上市 + 数据终止占现金 {control['unpriced_share']:.2%}",
            "refuted": not (
                control["avg_cash_ratio_median"] > 0.08 and control["unpriced_share"] > 0.70
            ),
        }
    )

    # ---- P5 资本敏感性 ----
    ladder = [
        {"capital": item["capital"], "delta_vs_base": item["delta_vs_base"]}
        for item in capital
    ]
    monotone = all(
        ladder[i]["delta_vs_base"] >= ladder[i + 1]["delta_vs_base"] - 1e-9
        for i in range(len(ladder) - 1)
    )
    low = next(item["delta_vs_base"] for item in ladder if item["capital"] == 1_000_000.0)
    high = next(item["delta_vs_base"] for item in ladder if item["capital"] == 100_000_000.0)
    out.append(
        {
            "id": "P5",
            "claim": "整手增量随资金单调递减；100 万 > 3pp，1 亿 < 0.1pp",
            "actual": "；".join(f"{_money(item['capital'])} {item['delta_vs_base'] * 100:+.3f}pp" for item in ladder),
            "refuted": not (monotone and low > 0.03 and high < 0.001),
        }
    )

    # ---- P6 诊断臂 ----
    e_cash = diagnostic["avg_cash_ratio_median"]
    delta_return = diagnostic["test_return_mean"] - statistics.fmean(base_returns)
    base_positive = sum(1 for r in base_returns if r > 0) / len(base_returns)
    e_positive = diagnostic["test_positive_fold_ratio"]
    crossed = (base_positive - 0.5) * (e_positive - 0.5) <= 0
    p6_t = diagnostic.get("paired_t")
    p6_t_text = "—" if p6_t is None else f"{p6_t:.3f}"
    out.append(
        {
            "id": "P6",
            "claim": "诊断臂现金中位 < 3pp，收益均值升 1~6pp，正收益折占比不跨 50%",
            "actual": f"现金中位 {_pct(e_cash)}；收益均值 {delta_return * 100:+.3f}pp；"
            f"正收益折 {e_positive:.2%}（基线 {base_positive:.2%}）；"
            f"逐折配对差 {_f(diagnostic.get('paired_mean_delta'))}（t = {p6_t_text}）",
            "refuted": not (e_cash < 0.03 and 0.01 <= delta_return <= 0.06 and not crossed),
        }
    )
    return out


def render_markdown(payload: dict[str, Any]) -> str:
    from deepquant.gates.cash_audit import BASE_COMPONENTS, COMPONENT_LABELS

    summary = payload["summary"]
    folds = payload["folds"]
    control = payload["control_summary"]
    capital = payload["capital_sweep"]
    diagnostic = payload["diagnostic_arm"]

    lines: list[str] = []
    lines.append("# L3q：L3p 组合臂现金占比归因审计")
    lines.append("")
    lines.append(f"- 审计对象：`{payload['subject']}`")
    lines.append(f"- 预登记：`{payload['prereg']}`，sha256 `{payload['prereg_sha256'][:16]}…`")
    lines.append(f"- 生成的基线复现：{payload['reproduced_folds']}/{payload['folds_total']} 折逐位一致"
                 f"（最大偏差 {payload['repro_max_gap']:.2e}）")
    lines.append(f"- 归因自洽：恒等式残差最大 {summary['residual_identity_max']:.2e} 元、"
                 f"回放残差最大 {summary['residual_ledger_max']:.2e} 元")
    lines.append("")
    lines.append("> 本文只做归因，**不改变 L3p 的任何终态**。诊断臂不是策略改动，不进判定。")
    lines.append("")

    lines.append("## 一、结论先行")
    lines.append("")
    lines.append(payload["headline"])
    lines.append("")
    lines.append(f"> {payload['headline_note']}")
    lines.append("")

    lines.append("## 二、逐折主因分布")
    lines.append("")
    lines.append("| 主因分量 | 含义 | 折数 |")
    lines.append("|---|---|---|")
    for name, count in sorted(
        payload["dominant_counts"].items(), key=lambda kv: -kv[1]
    ):
        lines.append(f"| `{name}` | {COMPONENT_LABELS[name]} | {count} |")
    lines.append("")

    lines.append("## 三、现金占比归因（28 折，合计口径）")
    lines.append("")
    lines.append("| 分量 | 含义 | 中位（pp） | 合计占比 |")
    lines.append("|---|---|---|---|")
    for name in BASE_COMPONENTS:
        lines.append(
            f"| `{name}` | {COMPONENT_LABELS[name]} | "
            f"{summary['component_median'][name] * 100:+.3f} | "
            f"{summary['share_total'][name]:.2%} |"
        )
    lines.append("")
    lines.append(f"- 平均现金占比：折中位 **{_pct(summary['avg_cash_ratio_median'])}**、"
                 f"折均值 {_pct(summary['avg_cash_ratio_mean'])}")
    lines.append(f"- 调仓期合计 {summary['periods_total']}，其中上限触发 "
                 f"{summary['periods_with_cap']} 次、含无价标的 {summary['periods_with_unpriced']} 次")
    lines.append("")

    lines.append("## 四、逐折明细")
    lines.append("")
    lines.append("| 折 | 测试窗 | 现金占比 | dead | suspended | not_listed | cap | rounding | insufficient | 死票权重均值 | 整手增量 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for item in folds:
        c = item["components"]
        lines.append(
            f"| {item['fold']} | {item['test_start']} → {item['test_end']} "
            f"| {_pct(item['avg_cash_ratio'])} "
            f"| {c['dead'] * 100:+.2f} | {c['suspended'] * 100:+.2f} | {c['not_listed'] * 100:+.2f} "
            f"| {c['cap'] * 100:+.2f} | {c['rounding'] * 100:+.2f} | {c['insufficient'] * 100:+.2f} "
            f"| {_pct(item['unpriced_weight_mean'])} | {_ratio_pp(item['lots_delta'])} |"
        )
    lines.append("")

    lines.append("## 五、对照臂（等权持有全部 30 只）的现金")
    lines.append("")
    lines.append("| 分量 | 合计占比 |")
    lines.append("|---|---|")
    for name in BASE_COMPONENTS:
        lines.append(f"| `{name}` | {control['share_total'][name]:.2%} |")
    lines.append("")
    lines.append(f"- 对照臂现金中位：**{_pct(control['avg_cash_ratio_median'])}**；"
                 f"其中未上市 + 数据终止占 **{control['unpriced_share']:.2%}**")
    lines.append("")

    lines.append("## 六、整手约束的资本敏感性")
    lines.append("")
    lines.append("| 资金 | 现金占比中位 | 逐折配对增量中位（vs weights@1000万） | rounding 分量中位 |")
    lines.append("|---|---|---|---|")
    for item in capital:
        rounding = item["rounding_median"]
        lines.append(
            f"| {_money(item['capital'])} | {_pct(item['avg_cash_ratio_median'])} "
            f"| {_ratio_pp(item['delta_vs_base'])} "
            f"| {'—' if rounding is None else f'{rounding * 100:+.3f}pp'} |"
        )
    lines.append("")
    lines.append("> 增量是**逐折配对**后取中位（同一折的 lots@该资金 − weights@1000 万），"
                 "不是「两档中位相减」——后者是两个不同折的中位相减，没有配对含义。")
    lines.append(">")
    lines.append("> 本表的 `rounding` 来自 **lots** 臂；基线的 `weights` 臂按 1 股取整，"
                 "`rounding` 中位只有 0.002pp。两者不要混引。")
    lines.append("")

    lines.append("## 七、诊断臂：把无价标的的权重还给可成交标的")
    lines.append("")
    lines.append("| 指标 | 基线（weights @1000万） | 诊断臂 |")
    lines.append("|---|---|---|")
    lines.append(f"| 现金占比中位 | {_pct(summary['avg_cash_ratio_median'])} | {_pct(diagnostic['avg_cash_ratio_median'])} |")
    lines.append(f"| 测试收益均值 | {_pct(statistics.fmean([f['test_return'] for f in folds]))} | {_pct(diagnostic['test_return_mean'])} |")
    lines.append(f"| 正收益折占比 | {_pct(sum(1 for f in folds if f['test_return'] > 0) / len(folds))} | {_pct(diagnostic['test_positive_fold_ratio'])} |")
    paired_t = diagnostic.get("paired_t")
    lines.append(
        f"| 逐折配对差（诊断 − 基线） | — | "
        f"{_f(diagnostic['paired_mean_delta'])}（sd {_f(diagnostic['paired_sd'])}，"
        f"t = {'—' if paired_t is None else f'{paired_t:.3f}'}，"
        f"{diagnostic['paired_folds']} 折） |"
    )
    lines.append("")
    lines.append("> 诊断臂的数字**不得**作为收益结论引用：它修复的是数据缺陷（行情已终止的标的仍被")
    lines.append("> 记为持有），不是模型改进。若要把该口径升为主口径，必须另开预登记重跑 L3p。")
    lines.append("")

    lines.append("### 事后臂 E2（未预登记）：只剔「行情已终止 / 未上市」，保留停牌")
    lines.append("")
    d2 = payload["diagnostic_arm_2"]
    d2_t = d2.get("paired_t")
    lines.append("| 指标 | 基线 | E1（预登记口径） | E2（事后，只修数据缺陷） |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| 现金占比中位 | {_pct(summary['avg_cash_ratio_median'])} "
        f"| {_pct(diagnostic['avg_cash_ratio_median'])} | {_pct(d2['avg_cash_ratio_median'])} |"
    )
    lines.append(
        f"| 测试收益均值 | {_pct(statistics.fmean([f['test_return'] for f in folds]))} "
        f"| {_pct(diagnostic['test_return_mean'])} | {_pct(d2['test_return_mean'])} |"
    )
    lines.append(
        f"| 逐折配对差均值 | — | {_f(diagnostic['paired_mean_delta'])} | {_f(d2['paired_mean_delta'])} |"
    )
    e1_t = diagnostic.get("paired_t")
    lines.append(
        f"| 配对 t | — | "
        f"{'—' if e1_t is None else f'{e1_t:.3f}'} "
        f"| {'—' if d2_t is None else f'{d2_t:.3f}'} |"
    )
    lines.append("")
    lines.append(
        "> E1 之所以不能单独解释「现金拖累」，是因为它把**停牌票**也剔出了目标："
        "停牌期间无价、复牌后首次调仓即卖出，等于顺手改了持有期。折 3 因此出现"
        "−18pp 的离群（停牌重组股复牌后暴涨被卖在起点）。E2 只剔数据缺陷，"
        "因此更接近「纯粹修 bug 还剩多少」。E2 未预登记，仅作机制说明。"
    )
    lines.append("")

    lines.append("## 八、对 L3p `per_invested_excess_mean` 的影响（需要加限定）")
    lines.append("")
    forced = payload["forced_cash_share"]
    lines.append("| 臂 | 正分量合计 | 其中不可成交标的占用 | 占比 | 上限压仓占比 |")
    lines.append("|---|---|---|---|---|")
    for tag, key in (("策略臂", "strategy"), ("对照臂", "control")):
        item = forced[key]
        lines.append(
            f"| {tag} | {item['positive_total']:.4f} | {item['forced']:.4f} "
            f"| {item['share']:.2%} | {item['cap_share']:.2%} |"
        )
    lines.append("")
    lines.append(
        "L3p 用 `per_invested = (1+r)/(1−cash) − 1` 把两臂的现金差异折算掉，得出"
        "「原始超额 +0.26pp、按投入资金折算后 **−7.87pp**」，并据此写下"
        "「选择没有正贡献，收益来自持有这批股票本身」。"
    )
    lines.append("")
    lines.append(
        f"本审计显示这个折算的前提不成立：策略臂现金里 **{forced['strategy']['share']:.0%}**、"
        f"对照臂 **{forced['control']['share']:.0%}** 来自**不可成交标的**"
        "（行情终止 / 未上市 / 停牌），不是「可投而未投」。"
        "`1−cash` 因此不是「主动投出去的比例」，而是「能投出去的比例」。"
    )
    lines.append("")
    lines.append(
        "所以 `−7.87pp` **不能**被读成「选择没有正贡献」：它测的是两个都不可实现的"
        "假想组合之间的差（各自 100% 投满、且按原比例分配）。"
        "要回答「选择有没有用」，需要一个**只在可成交标的上等权**的新对照臂——"
        "那是新实验，必须另开预登记。本轮不给这个数字。"
    )
    lines.append("")
    lines.append("## 九、逐条核对预登记预测")
    lines.append("")
    lines.append("| 编号 | 预测 | 实测 | 是否被反驳 |")
    lines.append("|---|---|---|---|")
    for item in payload["predictions"]:
        lines.append(
            f"| {item['id']} | {item['claim']} | {item['actual']} | "
            f"{'**是**' if item['refuted'] else '否'} |"
        )
    lines.append("")
    for item in payload["predictions"]:
        if item.get("caveat"):
            lines.append(f"- **{item['id']} 声明**：{item['caveat']}")
    lines.append("")

    lines.append("## 十、限制")
    lines.append("")
    for item in payload["limits"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3q 现金归因审计")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    prereg = verify_prereg()

    import pandas as pd

    from deepquant.backtest.config import BacktestConfig
    from deepquant.backtest.portfolio import PortfolioBacktestConfig, run_portfolio_backtest
    from deepquant.gates import load_spec
    from deepquant.gates.cash_audit import (
        CASH_CAP,
        CASH_DEAD,
        CASH_NOT_LISTED,
        CASH_PRESTART,
        CASH_SUSPENDED,
        COMPONENT_LABELS,
        audit_cash,
        spearman,
        summarise_audits,
        tradable_only_schedule,
    )
    from deepquant.gates.l3 import build_folds, parse_walk_forward
    from deepquant.gates.l3_portfolio import (
        _signal_dates_for,
        control_schedule,
        load_universe,
        parse_portfolio_spec,
        schedule_from_states,
        state_from_events,
    )
    from deepquant.gates.sweep import default_strategy_factory

    spec = load_spec(SPEC_PATH)
    payload = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    wf = parse_walk_forward(spec.walk_forward)
    pf = parse_portfolio_spec(payload.get("portfolio"))
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

    def make_config(cash: float, allocation: str) -> PortfolioBacktestConfig:
        return PortfolioBacktestConfig(
            symbols=tuple(universe.constituents),
            initial_cash=cash,
            fee_config=BacktestConfig(
                symbol=universe.universe_id,
                initial_cash=cash,
                slippage_buy=0.0,
                slippage_sell=0.0,
            ),
            allocation=allocation,  # type: ignore[arg-type]
            max_weight_per_asset=pf.max_weight_per_asset,
        )

    started = time.perf_counter()
    fold_records: list[dict[str, Any]] = []
    audits_weights: list[Any] = []
    audits_lots: list[Any] = []
    audits_control: list[Any] = []
    repro_max_gap = 0.0
    reproduced = 0
    capital_rows: dict[float, list[dict[str, Any]]] = {c: [] for c in CAPITAL_LEVELS}
    capital_failures: list[dict[str, Any]] = []
    diagnostic_returns: list[float] = []
    diagnostic_cash: list[float] = []
    diagnostic2_returns: list[float] = []
    diagnostic2_cash: list[float] = []

    def safe_run(frames, schedule, config, start, end):
        """引擎的内部一致性守卫可能因一分钱的双重取整而触发。

        这是引擎既有缺陷（``total_equity`` 用未取整市值算，存下来的市值却取整了），
        本审计**不在引擎侧修复**（改引擎属于另一场实验）。这里如实捕获并记账，
        绝不静默跳过：失败的格必须在报告里出现。
        """
        try:
            return run_portfolio_backtest(
                frames, schedule, config, start_date=start, end_date=end
            ), None
        except RuntimeError as exc:  # 引擎一致性守卫
            return None, str(exc)

    for fold, saved_fold in zip(folds, saved):
        params = saved_fold["best_params"]
        states = {
            symbol: state_from_events(
                default_strategy_factory(spec.strategy_id, params).generate_signals(frame)
            )
            for symbol, frame in universe.frames.items()
        }
        signal_dates = _signal_dates_for(calendar, fold.test_start, fold.test_end)
        schedule = schedule_from_states(
            states, signal_dates, max_weight_per_asset=pf.max_weight_per_asset
        )

        # ---- A 基线（必须成功：失败的基线无法归因）----
        base_result, err = safe_run(
            universe.frames,
            schedule,
            make_config(pf.initial_cash, "weights"),
            fold.test_start,
            fold.test_end,
        )
        if base_result is None:
            raise SystemExit(f"✗ 折 {saved_fold['fold']} 基线臂跑不动：{err}")
        base_audit = audit_cash(
            base_result, universe.frames, schedule, initial_cash=pf.initial_cash, unit=1
        )
        gap = abs(base_audit.avg_cash_ratio - float(saved_fold["avg_cash_ratio"]))
        repro_max_gap = max(repro_max_gap, gap)
        if gap <= REPRO_TOLERANCE:
            reproduced += 1
        audits_weights.append(base_audit)

        # ---- B 整手 ----
        lots_result, lots_err = safe_run(
            universe.frames,
            schedule,
            make_config(pf.initial_cash, "lots"),
            fold.test_start,
            fold.test_end,
        )
        lots_audit = (
            audit_cash(
                lots_result, universe.frames, schedule, initial_cash=pf.initial_cash, unit=100
            )
            if lots_result is not None
            else None
        )
        if lots_audit is not None:
            audits_lots.append(lots_audit)

        # ---- C 对照臂 ----
        control = control_schedule(universe.constituents, signal_dates)
        control_result, control_err = safe_run(
            universe.frames,
            control,
            make_config(pf.initial_cash, "weights"),
            fold.test_start,
            fold.test_end,
        )
        control_audit = (
            audit_cash(
                control_result, universe.frames, control, initial_cash=pf.initial_cash, unit=1
            )
            if control_result is not None
            else None
        )
        if control_audit is not None:
            audits_control.append(control_audit)

        # ---- D 资本敏感性（1000 万已是 B，不重复跑）----
        for capital in CAPITAL_LEVELS:
            if capital == pf.initial_cash:
                audit = lots_audit
            else:
                result, err_d = safe_run(
                    universe.frames,
                    schedule,
                    make_config(capital, "lots"),
                    fold.test_start,
                    fold.test_end,
                )
                audit = (
                    audit_cash(
                        result, universe.frames, schedule, initial_cash=capital, unit=100
                    )
                    if result is not None
                    else None
                )
            if audit is None:
                capital_failures.append(
                    {
                        "fold": saved_fold["fold"],
                        "capital": capital,
                        "error": lots_err if capital == pf.initial_cash else err_d,
                    }
                )
                continue
            capital_rows[capital].append(
                {
                    "fold": saved_fold["fold"],
                    "avg_cash_ratio": audit.avg_cash_ratio,
                    "rounding": audit.components["rounding"],
                }
            )

        # ---- E 诊断臂 ----
        tradable = tradable_only_schedule(
            states,
            signal_dates,
            calendar=calendar,
            frames=universe.frames,
            max_weight_per_asset=pf.max_weight_per_asset,
        )
        tradable_result, tradable_err = safe_run(
            universe.frames,
            tradable,
            make_config(pf.initial_cash, "weights"),
            fold.test_start,
            fold.test_end,
        )
        tradable_audit = (
            audit_cash(
                tradable_result, universe.frames, tradable, initial_cash=pf.initial_cash, unit=1
            )
            if tradable_result is not None
            else None
        )
        if tradable_audit is not None:
            diagnostic_cash.append(tradable_audit.avg_cash_ratio)
        if tradable_result is not None and tradable_result.cumulative_return is not None:
            diagnostic_returns.append(float(tradable_result.cumulative_return))

        # ---- E2 事后臂（**未预登记**）：只剔「行情已终止 / 未上市」，保留停牌 ----
        # 预登记口径的 E 臂把停牌票也剔了，等于「复牌后首次调仓就卖」，顺手改了持有期
        # （折 3 因此产生 −18pp 假效应）。E2 只修数据缺陷，用于说明机制，不进判定。
        narrow = tradable_only_schedule(
            states,
            signal_dates,
            calendar=calendar,
            frames=universe.frames,
            max_weight_per_asset=pf.max_weight_per_asset,
            drop_causes=(CASH_DEAD, CASH_NOT_LISTED),
        )
        narrow_result, narrow_err = safe_run(
            universe.frames,
            narrow,
            make_config(pf.initial_cash, "weights"),
            fold.test_start,
            fold.test_end,
        )
        narrow_audit = (
            audit_cash(
                narrow_result, universe.frames, narrow, initial_cash=pf.initial_cash, unit=1
            )
            if narrow_result is not None
            else None
        )
        if narrow_audit is not None:
            diagnostic2_cash.append(narrow_audit.avg_cash_ratio)
        if narrow_result is not None and narrow_result.cumulative_return is not None:
            diagnostic2_returns.append(float(narrow_result.cumulative_return))

        unpriced = [item.unpriced_weight for item in base_audit.rebalances]
        fold_records.append(
            {
                "fold": saved_fold["fold"],
                "best_params": params,
                "test_start": str(fold.test_start.date()),
                "test_end": str(fold.test_end.date()),
                "avg_cash_ratio": base_audit.avg_cash_ratio,
                "saved_avg_cash_ratio": float(saved_fold["avg_cash_ratio"]),
                "components": dict(base_audit.components),
                "shares": dict(base_audit.shares),
                "unpriced_weight_mean": statistics.fmean(unpriced) if unpriced else 0.0,
                "lots_cash_ratio": (
                    lots_audit.avg_cash_ratio if lots_audit is not None else None
                ),
                "lots_delta": (
                    lots_audit.avg_cash_ratio - base_audit.avg_cash_ratio
                    if lots_audit is not None
                    else None
                ),
                "lots_error": lots_err,
                "control_error": control_err,
                "diagnostic_error": tradable_err,
                "test_return": float(saved_fold["test_return"]),
                "diagnostic_return": (
                    float(tradable_result.cumulative_return)
                    if tradable_result is not None
                    and tradable_result.cumulative_return is not None
                    else None
                ),
                "diagnostic2_return": (
                    float(narrow_result.cumulative_return)
                    if narrow_result is not None
                    and narrow_result.cumulative_return is not None
                    else None
                ),
                "diagnostic2_error": narrow_err,
                "periods": base_audit.periods,
            }
        )
        if not args.quiet:
            print(
                f"  折 {saved_fold['fold']:>2} 现金 {base_audit.avg_cash_ratio * 100:6.2f}% "
                f"dead {base_audit.components[CASH_DEAD] * 100:6.2f}pp "
                f"整手 {fold_records[-1]['lots_delta'] * 100:+5.2f}pp"
            )

    elapsed = time.perf_counter() - started

    # ---- 汇总 ----
    summary = summarise_audits(audits_weights)
    control_summary = summarise_audits(audits_control)
    unpriced_total = control_summary["component_total"].get(
        CASH_DEAD, 0.0
    ) + control_summary["component_total"].get(CASH_NOT_LISTED, 0.0)
    cash_total = sum(control_summary["component_total"].values())
    control_summary["unpriced_share"] = unpriced_total / cash_total if cash_total else 0.0
    control_summary["avg_cash_ratio_median"] = statistics.median(
        a.avg_cash_ratio for a in audits_control
    )

    # 逐折配对：同一折的 lots@capital 减 weights@10M。**必须逐折配对再取中位**，
    # 不能拿「各档中位」相减——那是两个不同折的中位相减，差值没有配对含义。
    base_by_fold = {item["fold"]: item["avg_cash_ratio"] for item in fold_records}
    capital_sweep = []
    for capital in CAPITAL_LEVELS:
        deltas = [
            row["avg_cash_ratio"] - base_by_fold[row["fold"]]
            for row in capital_rows[capital]
            if row["fold"] in base_by_fold
        ]
        capital_sweep.append(
            {
                "capital": capital,
                "avg_cash_ratio_median": (
                    statistics.median(r["avg_cash_ratio"] for r in capital_rows[capital])
                    if capital_rows[capital]
                    else None
                ),
                "delta_vs_base": statistics.median(deltas) if deltas else None,
                "rounding_median": (
                    statistics.median(r["rounding"] for r in capital_rows[capital])
                    if capital_rows[capital]
                    else None
                ),
                "folds": len(deltas),
            }
        )

    # ---- 诊断臂：逐折配对差（固定同一折、同一窗口，唯一差别是要不要死票权重）----
    paired = [
        (item["diagnostic_return"] - item["test_return"])
        for item in fold_records
        if item["diagnostic_return"] is not None
    ]
    paired_mean = statistics.fmean(paired) if paired else None
    paired_sd = statistics.stdev(paired) if len(paired) > 1 else None
    paired_t = (
        paired_mean / (paired_sd / math.sqrt(len(paired)))
        if paired and paired_sd and paired_sd > 0
        else None
    )

    diagnostic_arm = {
        "avg_cash_ratio_median": statistics.median(diagnostic_cash),
        "test_return_mean": statistics.fmean(diagnostic_returns) if diagnostic_returns else None,
        "test_positive_fold_ratio": (
            sum(1 for r in diagnostic_returns if r > 0) / len(diagnostic_returns)
            if diagnostic_returns
            else None
        ),
        "folds": len(diagnostic_returns),
        "paired_mean_delta": paired_mean,
        "paired_sd": paired_sd,
        "paired_t": paired_t,
        "paired_folds": len(paired),
    }

    paired2 = [
        (item["diagnostic2_return"] - item["test_return"])
        for item in fold_records
        if item.get("diagnostic2_return") is not None
    ]
    paired2_mean = statistics.fmean(paired2) if paired2 else None
    paired2_sd = statistics.stdev(paired2) if len(paired2) > 1 else None
    paired2_t = (
        paired2_mean / (paired2_sd / math.sqrt(len(paired2)))
        if paired2 and paired2_sd and paired2_sd > 0
        else None
    )
    diagnostic_arm_2 = {
        "definition": "只剔「行情已终止 / 未上市」的持仓，保留停牌；**未预登记**（事后设计）",
        "avg_cash_ratio_median": (
            statistics.median(diagnostic2_cash) if diagnostic2_cash else None
        ),
        "test_return_mean": (
            statistics.fmean(diagnostic2_returns) if diagnostic2_returns else None
        ),
        "test_positive_fold_ratio": (
            sum(1 for r in diagnostic2_returns if r > 0) / len(diagnostic2_returns)
            if diagnostic2_returns
            else None
        ),
        "folds": len(diagnostic2_returns),
        "paired_mean_delta": paired2_mean,
        "paired_sd": paired2_sd,
        "paired_t": paired2_t,
        "paired_folds": len(paired2),
    }

    # ---- 逐折主因：哪一类分量是这只折的最大正贡献 ----
    dominant_counts: dict[str, int] = {}
    for item in fold_records:
        positive = {k: v for k, v in item["components"].items() if v > 0}
        top = max(positive, key=lambda k: positive[k]) if positive else None
        item["dominant"] = top
        if top is not None:
            dominant_counts[top] = dominant_counts.get(top, 0) + 1

    dead_weight = [item["unpriced_weight_mean"] for item in fold_records]
    cash_ratio = [item["avg_cash_ratio"] for item in fold_records]
    rho = spearman(dead_weight, cash_ratio)

    limits = [
        "现金占比是逐日 `cash/total_equity` 的均值，不是「平均未投资金额」；权益波动大的折两者不同。",
        "归因是「调仓日缺口 → 逐日按实测值分摊」，不是逐笔因果推断。",
        "诊断臂的归一化会改变集中度（k 变小 ⇒ 单只权重上升，可能更早触及 0.20 上限），"
        "因此它衡量的是「修复后的净效果」，不是「纯现金拖累」。",
        "本审计不解决标的池的生存偏差；死票问题与生存偏差是两个独立缺陷。",
        "涨跌停判据在 L3p 组合层未启用（`BacktestConfig` 默认 `price_limit=None`），"
        "因此本节所有数字都**不含**涨停买不进的摩擦——真实摩擦只会更大。",
        "整手敏感性只做了 100 股一档；科创板 200 股起的规则未单独建模。",
        "无价分量可以为**负**：停牌股的冻结市值高于当期目标权重时缺口为负"
        "（折 2 的 `suspended` = −2.49pp），它表示「可成交标的的现金被它挤占」，"
        "不是「多出了现金」。",
    ]
    if capital_failures:
        limits.append(
            f"资本敏感性有 {len(capital_failures)} 个（折 × 资金）格因引擎内部一致性守卫"
            f"（一分钱的双重取整）未能出数，已在 `capital_failures` 里逐格记账，"
            "未用任何插值填补。"
        )
    if len(audits_control) != len(folds):
        limits.append(
            f"对照臂只有 {len(audits_control)}/{len(folds)} 折可用，其汇总不能当成全样本中位。"
        )

    def _forced_share(block: dict[str, Any]) -> dict[str, float]:
        """不可成交标的占用的现金比例（分母只取正的合计，避免负数抵消）。"""
        totals = block["component_total"]
        positive = sum(v for v in totals.values() if v > 0)
        forced = sum(totals.get(k, 0.0) for k in (CASH_DEAD, CASH_NOT_LISTED, CASH_SUSPENDED))
        return {
            "positive_total": positive,
            "forced": forced,
            "share": (forced / positive) if positive else 0.0,
            "cap_share": (totals.get(CASH_CAP, 0.0) / positive) if positive else 0.0,
        }

    forced_cash_share = {
        "strategy": _forced_share(summary),
        "control": _forced_share(control_summary),
    }

    dead_share = summary["share_total"].get(CASH_DEAD, 0.0)
    suspended_share = summary["share_total"].get(CASH_SUSPENDED, 0.0)
    cap_share = summary["share_total"].get(CASH_CAP, 0.0)
    lots_median = statistics.median(
        [item["lots_delta"] for item in fold_records if item["lots_delta"] is not None]
    )
    dominant_text = "、".join(
        f"{COMPONENT_LABELS.get(k, k)} {v} 折"
        for k, v in sorted(dominant_counts.items(), key=lambda kv: -kv[1])
    )
    headline = (
        f"**{summary['avg_cash_ratio_median'] * 100:.2f}% 的现金（折中位）不是「钱不够」造成的："
        f"整手约束的中位贡献只有 {lots_median * 100:+.2f}pp。**"
        f"按合计占比拆：停牌 **{suspended_share:.1%}**、行情终止仍记为持有 "
        f"**{dead_share:.1%}**、权重上限压仓 **{cap_share:.1%}**、"
        f"折首空仓 {summary['share_total'].get(CASH_PRESTART, 0.0):.1%}。"
        f"逐折看主因：{dominant_text}。"
        "结论：现金来自**买不进**——现实约束（停牌）与数据缺陷（行情已终止仍被记为持有）各占一半，"
        "权重上限再吃掉两成。"
    )
    headline_note = (
        "⚠️ 口径提醒：`component_median` 是**每个分量各自的中位**，不同折取中位的分量不在同一折上，"
        "**不可相加**去对 `avg_cash_ratio_median`。要看构成请用可加的 `share_total`（分母是全部折的分量总和），"
        "或直接看 `folds[].components`（逐折可加，误差 ≤ 1e-12）。"
    )

    payload_out: dict[str, Any] = {
        "title": "L3q：L3p 组合臂现金占比归因审计",
        "audit_id": prereg["audit_id"],
        "subject": L3P_RESULT.relative_to(ROOT).as_posix(),
        "prereg": PREREG_PATH.relative_to(ROOT).as_posix(),
        "prereg_sha256": PREREG_SHA256,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": round(elapsed, 3),
        "folds_total": len(folds),
        "reproduced_folds": reproduced,
        "repro_max_gap": repro_max_gap,
        "headline": headline,
        "headline_note": headline_note,
        "dominant_counts": dominant_counts,
        "summary": summary,
        "control_summary": control_summary,
        "capital_sweep": capital_sweep,
        "diagnostic_arm": diagnostic_arm,
        "diagnostic_arm_2": diagnostic_arm_2,
        "dead_weight_spearman": rho,
        "forced_cash_share": forced_cash_share,
        "folds": fold_records,
        "capital_failures": capital_failures,
        "limits": limits,
    }
    payload_out["predictions"] = evaluate_predictions(payload_out)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3q_audit.json").write_text(
        json.dumps(payload_out, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown = render_markdown(payload_out)
    (OUT_DIR / "l3q_audit.md").write_text(markdown + "\n", encoding="utf-8", newline="\n")
    evidence = {
        "audit_id": prereg["audit_id"],
        "prereg_sha256": PREREG_SHA256,
        "artifacts": {
            "json": (OUT_DIR / "l3q_audit.json").relative_to(ROOT).as_posix(),
            "markdown": (OUT_DIR / "l3q_audit.md").relative_to(ROOT).as_posix(),
        },
        "summary": summary,
        "control_summary": control_summary,
        "capital_sweep": capital_sweep,
        "diagnostic_arm": diagnostic_arm,
        "diagnostic_arm_2": diagnostic_arm_2,
        "predictions": payload_out["predictions"],
        "reproduced_folds": reproduced,
        "folds_total": len(folds),
        "repro_max_gap": repro_max_gap,
    }
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    refuted = [item["id"] for item in payload_out["predictions"] if item["refuted"]]
    if not args.quiet:
        print("")
        print(f"现金占比中位：{summary['avg_cash_ratio_median'] * 100:.2f}%")
        for name in ("dead", "suspended", "cap", "rounding", "insufficient", "not_listed"):
            print(
                f"  {name:<12} 中位 {summary['component_median'][name] * 100:+7.3f}pp "
                f"合计占比 {summary['share_total'][name]:6.2%}"
            )
        print(
            "整手增量中位："
            f"{statistics.median([f['lots_delta'] for f in fold_records if f['lots_delta'] is not None]) * 100:+.3f}pp"
        )
        print(f"复现：{reproduced}/{len(folds)}（最大偏差 {repro_max_gap:.2e}）")
        print(f"引擎一致性守卫触发的格：{len(capital_failures)}")
        print(f"被反驳的预测：{refuted or '无'}")
        print(f"报告：{(OUT_DIR / 'l3q_audit.md').relative_to(ROOT)}")

    if reproduced != len(folds):
        print("✗ 基线未能逐折复现，审计不成立", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
