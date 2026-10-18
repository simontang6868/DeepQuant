"""股债利差研究：中文研究报告生成。

报告的证据分级只有三种，且判据写死在代码里（不能事后改动）：
- ``supported`` 有证据支持（弱）：样本内外方向一致为正，样本外事件数 >= MIN_OOS_EVENTS
- ``not_supported`` 暂时未发现支持证据：方向不一致、为负，或样本量不足
- ``insufficient`` 数据不足，无法判断：样本外事件数为 0，或数据覆盖缺失

禁止把"没有证据证明无效"写成"已经证明有效"。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .config import ResearchConfig
from .experiment import ExperimentResult

MIN_OOS_EVENTS = 3

VERDICT_LABELS = {
    "supported": "有证据支持（弱）",
    "not_supported": "暂时未发现支持证据",
    "insufficient": "数据不足，无法判断",
}


def _fmt(v, digits: int = 4, pct: bool = False) -> str:
    if v is None:
        return "—"
    try:
        if pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        return "—"
    f = float(v)
    if pct:
        return f"{f * 100:.2f}%"
    return f"{f:.{digits}f}"


def verdict(is_diff: float | None, oos_diff: float | None, n_oos: int) -> str:
    """按写死的判据给出证据分级。"""
    if n_oos == 0 or oos_diff is None or is_diff is None:
        return "insufficient"
    if oos_diff > 0 and is_diff > 0 and n_oos >= MIN_OOS_EVENTS:
        return "supported"
    return "not_supported"


def _md_table(df: pd.DataFrame, float_fmt: str = "%.4f") -> str:
    if df is None or len(df) == 0:
        return "（无数据）\n"
    return df.to_markdown(index=False, floatfmt=float_fmt) + "\n"


@dataclass
class ReportInput:
    """生成报告所需的全部材料。"""

    panel_info: dict
    results: dict[str, ExperimentResult]
    sensitivity: pd.DataFrame
    cases: dict[str, list[dict]]
    data_notes: list[str]


#: 需要以百分数展示的列
_PCT_COLS = (
    "signal_mean", "signal_median", "signal_pct_positive", "signal_worst",
    "signal_best", "signal_mean_maxdd", "baseline_mean", "baseline_median",
    "baseline_pct_positive", "baseline_worst", "mean_difference",
)


def _comparison_table(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    cols = [
        "window_td", "n_signals", "signal_mean", "signal_median",
        "signal_pct_positive", "signal_worst", "signal_mean_maxdd",
        "baseline_mean", "baseline_median", "baseline_pct_positive",
        "mean_difference", "signal_better",
    ]
    cols = [c for c in cols if c in df.columns]
    out = df[cols].copy()
    for c in out.columns:
        if c in _PCT_COLS:
            out[c] = out[c].map(lambda v: "—" if v is None or pd.isna(v) else f"{float(v)*100:.2f}%")
    return out


def _format_event_table(df: pd.DataFrame) -> pd.DataFrame:
    """把事件表的收益/回撤列格式化为百分数，日期格式化为 YYYY-MM-DD。"""
    out = df.copy()
    for c in out.columns:
        if c.startswith(("ret_", "dd_", "mfe_", "mae_")):
            out[c] = out[c].map(
                lambda v: "—" if v is None or pd.isna(v) else f"{float(v) * 100:.2f}%"
            )
        elif c in ("signal_date", "exec_date", "last_signal_date"):
            out[c] = pd.to_datetime(out[c], errors="coerce").dt.strftime("%Y-%m-%d")
        elif c in ("spread_at_signal", "percentile_at_signal", "bond_10y_at_signal",
                   "pe_at_signal"):
            out[c] = out[c].map(
                lambda v: "—" if v is None or pd.isna(v) else f"{float(v):.2f}"
            )
    return out


def render_report(inp: ReportInput) -> str:
    """渲染完整的中文研究报告（Markdown）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    add = lines.append

    add("# 股债利差与长期市场机会识别研究报告")
    add("")
    add(f"> 生成时间：{now} ｜ 标的：沪深300（000300.SH） ｜ 债券基准：中国 10 年期国债收益率")
    add("> 本研究仅为历史数据分析，不构成投资建议，不生成任何交易指令。")
    add("")

    # ---------------- A. 工程完成情况 ----------------
    add("## A. 工程完成情况")
    add("")
    add("### A1 数据来源与口径")
    add("")
    eq = inp.panel_info.get("equity", {})
    bd = inp.panel_info.get("bond", {})
    cov = inp.panel_info.get("coverage", {})
    add("| 数据 | 来源 | 口径 | 覆盖区间 | 有效观测 |")
    add("|---|---|---|---|---|")
    add(
        f"| 沪深300 行情与估值 | 腾讯自选股行情接口（westock-data `quote --date` 逐点快照） | "
        f"收盘价、PE(行情源口径)、PB、股息率TTM | {eq.get('date_min','—')} ~ {eq.get('date_max','—')} | "
        f"{eq.get('n_output_rows','—')} |"
    )
    add(
        f"| 中国 10 年期国债收益率 | 中国债券信息网（中债国债收益率曲线） | 到期收益率，百分数 | "
        f"{bd.get('date_min','—')} ~ {bd.get('date_max','—')} | {bd.get('n_output_rows','—')} |"
    )
    add("")
    add("**重要口径声明：**")
    add("")
    add("1. 股票序列为**价格指数**口径，不是分红再投资的全收益指数。")
    add("2. 报告中出现的「近似总收益」= 价格收益 + 窗口内平均股息率，是**近似值**，")
    add("   不等于真正的全收益指数收益。任何价格口径的收益都不会被描述为含分红总收益。")
    add("3. PE 取自行情源字段，口径未经指数公司官方确认，存在与 TTM 口径不完全一致的可能；")
    add("   盈利收益率 = 1/PE，是估值倒数，不等于股票实际回报率。")
    add("")

    add("### A2 数据质量")
    add("")
    add("| 指标 | 值 |")
    add("|---|---|")
    labels = {"n_obs": "面板观测数", "date_min": "面板起始日", "date_max": "面板结束日"}
    for k in ("n_obs", "date_min", "date_max"):
        add(f"| {labels.get(k, k)} | {cov.get(k, '—')} |")
    for c in ("price", "pe", "pb", "div_yield", "bond_10y"):
        key = f"coverage_{c}"
        if key in cov:
            add(f"| `{c}` 有效覆盖率 | {_fmt(cov[key], pct=True)} |")
    for k in ("n_exact", "n_filled", "n_missing", "max_fill_days"):
        if k in inp.panel_info.get("bond_match", {}):
            add(f"| 国债匹配 {k} | {inp.panel_info['bond_match'][k]} |")
    add("")
    add("| 文件 | SHA-256 |")
    add("|---|---|")
    add(f"| 股票序列 | `{eq.get('sha256', '—')}` |")
    add(f"| 国债序列 | `{bd.get('sha256', '—')}` |")
    add("")
    if inp.data_notes:
        add("**数据限制与缺口：**")
        add("")
        for n in inp.data_notes:
            add(f"- {n}")
        add("")

    # ---------------- B/C. 研究结果 ----------------
    add("## B. 研究结果")
    add("")
    for key, res in inp.results.items():
        cfg = res.config
        add(f"### B{key} 假设 {cfg.indicator.value}（实验 `{cfg.experiment_name}`）")
        add("")
        add(
            f"- 指标口径：{'过去 %d 年股票年化收益(%s) − 10年期国债收益率' % (cfg.lookback_years, cfg.return_basis.value)}"
            if cfg.indicator.value == "A"
            else "- 指标口径：盈利收益率(1/PE) − 10年期国债收益率"
        )
        add(f"- 历史百分位：{cfg.percentile_window.value} 窗口，最小历史样本 {cfg.min_history_obs} 个观测")
        add(f"- 信号阈值：历史 {cfg.threshold_pct:.0f}% 百分位；执行时点：信号后第 {cfg.exec_lag_obs} 个观测")
        add(f"- 研究区间：{cfg.start_date} ~ {cfg.end_date}；样本内截止 {cfg.is_end_date}")
        add(f"- 信号事件数：**{len(res.events)}**")
        add("")
        add("**全样本：信号组 vs 无条件基准组（同一窗口、随机时点）**")
        add("")
        add(_md_table(_comparison_table(res.comparison), "%.4f"))
        add("**样本内（IS）**")
        add("")
        add(_md_table(_comparison_table(res.comparison_is), "%.4f"))
        add("**样本外（OOS）**")
        add("")
        add(_md_table(_comparison_table(res.comparison_oos), "%.4f"))
        add("**基准：区间内简单持有沪深300（价格指数口径）**")
        add("")
        bh = res.buy_hold
        add("| 起始 | 结束 | 年数 | 总收益 | 年化收益 | 最大回撤 |")
        add("|---|---|---|---|---|---|")
        add(
            f"| {bh.get('start_date','—')} | {bh.get('end_date','—')} | {bh.get('years','—')} | "
            f"{_fmt(bh.get('total_return'), pct=True)} | {_fmt(bh.get('annualized_return'), pct=True)} | "
            f"{_fmt(bh.get('max_drawdown'), pct=True)} |"
        )
        add("")

    add("### B3 全部信号事件（不挑选成功案例）")
    add("")
    for key, res in inp.results.items():
        add(f"#### 假设 {res.config.indicator.value} · {res.config.experiment_name}")
        add("")
        if res.event_df is None or len(res.event_df) == 0:
            add("（该配置下没有产生任何信号事件）")
            add("")
            continue
        cols = [
            "event_id", "signal_date", "exec_date", "spread_at_signal",
            "percentile_at_signal", "pe_at_signal", "bond_10y_at_signal",
            "ret_20td", "ret_60td", "ret_120td", "ret_250td",
            "dd_250td", "mae_250td", "immature_any",
        ]
        cols = [c for c in cols if c in res.event_df.columns]
        add(_md_table(_format_event_table(res.event_df[cols]), "%.4f"))
    add("")

    add("### B4 参数敏感性（预先定义网格，非事后选参）")
    add("")
    if inp.sensitivity is not None and len(inp.sensitivity):
        piv = inp.sensitivity[inp.sensitivity["segment"].isin(["is", "oos"])].copy()
        cols = [
            "experiment", "indicator", "lookback_years", "threshold_pct", "segment",
            "window_td", "n_signals", "signal_mean", "baseline_mean", "mean_difference",
        ]
        cols = [c for c in cols if c in piv.columns]
        piv = piv[cols].sort_values(["indicator", "threshold_pct", "segment", "window_td"])
        for c in ("signal_mean", "baseline_mean", "mean_difference"):
            if c in piv.columns:
                piv[c] = piv[c].map(
                    lambda v: "—" if v is None or pd.isna(v) else f"{float(v) * 100:.2f}%"
                )
        add(_md_table(piv, "%.4f"))
    else:
        add("（未运行敏感性网格）")
    add("")

    # ---------------- D. 历史案例 ----------------
    add("## C. 重点历史阶段检查")
    add("")
    add("> 区间最低点由**数据**在区间内计算得出，不是记忆里的点位；")
    add("> 「信号领先」仅指信号日早于该区间最低价日，**不等于**指标具有预测能力。")
    add("")
    for key, case_list in inp.cases.items():
        add(f"### 假设 {key}")
        add("")
        for c in case_list:
            add(f"#### {c['case_name']}（{c['window_start']} ~ {c['window_end']}）")
            add("")
            if not c.get("data_available", True):
                add(f"- {c.get('note', '数据不足，无法验证')}")
                add("")
                continue
            add(f"- 区间最低价日：**{c.get('trough_date')}**，最低价 {_fmt(c.get('trough_price'), 2)}")
            add(f"- 区间内首次出现的信号事件数：**{c.get('n_events')}**"
                f"（另有 {c.get('n_events_active', 0)} 个事件的信号区间覆盖该窗口）")
            add(f"- 首次信号日：**{c.get('first_signal_date')}**")
            lead = c.get("signal_before_trough")
            add(
                f"- 信号是否早于区间最低点：**{'是' if lead is True else ('否' if lead is False else '无法判断')}**"
            )
            add(f"- {c.get('note', '')}".rstrip())
            add("")
            if c.get("events"):
                add(_md_table(pd.DataFrame(c["events"]), "%.4f"))
            elif c.get("pct_available_from"):
                # 区间前段没有百分位读数：这一段是「无法验证」，不是「无信号」
                add(
                    f"（该区间前段（至 {c['pct_available_from']}）指标尚不可用，"
                    "属于**无法验证**；此后没有满足规则的信号事件。"
                    "两者不可混为一谈。）"
                )
                add("")
            elif not c.get("n_events_active", 0):
                add("（该区间内没有满足规则的信号事件——这也是一个需要如实报告的结论）")
                add("")

    # ---------------- E. 结论 ----------------
    add("## D. 最终结论")
    add("")
    add("### D1 两个假设的证据分级")
    add("")
    add(
        "> 口径说明（审计后补充，防止误读）：表中的「均值差」= 信号组在**该窗口**的"
        "平均持有期净收益 − 同期无条件基准的平均持有期净收益。它**不是年化收益率**，"
        "而是 N 个观测（月频下 250 交易日 ≈ 12 个月）持有期收益的**算术平均差**。"
        "基准侧是「每一个观测出发」的重叠窗口算术平均，**不等于**买入持有的年化收益，"
        "两者不能互相替代（参见 B4 的买入持有年化数字）。"
    )
    add("")
    add("| 假设 | 判据窗口 | 样本内均值差 | 样本内事件数 | 样本外均值差 | 样本外事件数 | 证据分级 |")
    add("|---|---|---|---|---|---|---|")
    verdicts = {}
    for key, res in inp.results.items():
        # 取最长窗口（250 交易日）作为主判据
        def pick(rows):
            for r in rows:
                if r["window_td"] == max(res.config.forward_windows_td):
                    return r
            return rows[-1] if rows else None

        r_is = pick(res.comparison_is)
        r_oos = pick(res.comparison_oos)
        n_oos = int(r_oos["n_signals"]) if r_oos else 0
        n_is = int(r_is["n_signals"]) if r_is else 0
        v = verdict(
            r_is["mean_difference"] if r_is else None,
            r_oos["mean_difference"] if r_oos else None,
            n_oos,
        )
        verdicts[key] = v
        add(
            f"| 假设 {res.config.indicator.value}（{res.config.experiment_name}） | "
            f"{max(res.config.forward_windows_td)} 交易日 | "
            f"{_fmt(r_is['mean_difference'], pct=True) if r_is else '—'} | {n_is} | "
            f"{_fmt(r_oos['mean_difference'], pct=True) if r_oos else '—'} | {n_oos} | "
            f"{VERDICT_LABELS[v]} |"
        )
    add("")
    add(
        f"> 判据（写死在 `report.verdict`）：样本外事件数 = 0 → "
        f"「{VERDICT_LABELS['insufficient']}」；样本内外方向一致为正且样本外事件数 "
        f">= {MIN_OOS_EVENTS} → 「{VERDICT_LABELS['supported']}」；其余 → "
        f"「{VERDICT_LABELS['not_supported']}」。"
    )
    add("")

    add("### D2 逐条回答")
    add("")
    better_oos = None
    diffs = {}
    for key, res in inp.results.items():
        r = [x for x in res.comparison_oos
             if x["window_td"] == max(res.config.forward_windows_td)]
        diffs[key] = r[0]["mean_difference"] if r else None
    if diffs:
        valid = {k: v for k, v in diffs.items() if v is not None}
        better_oos = max(valid, key=valid.get) if valid else None

    add("1. **假设 A 是否获得支持？** "
        f"{VERDICT_LABELS.get(verdicts.get('A', 'insufficient'), '—')}"
        + (f"（样本外平均超额 {_fmt(diffs.get('A'), pct=True)}）" if diffs.get("A") is not None else "")
        + "。")
    add("2. **假设 B 是否获得支持？** "
        f"{VERDICT_LABELS.get(verdicts.get('B', 'insufficient'), '—')}"
        + (f"（样本外平均超额 {_fmt(diffs.get('B'), pct=True)}）" if diffs.get("B") is not None else "")
        + "。")
    add(f"3. **哪个指标在样本外表现更好？** "
        f"{'假设 ' + better_oos if better_oos else '无法判断（样本外事件数不足或无有效对比）'}。")
    add("4. **是否优于简单持有沪深300？** 本阶段只做了信号后前瞻收益与无条件基准的对比，"
        "未构建完整组合净值；因此只能回答「是否优于随机时点买入」，"
        "**不能**直接回答「是否优于全程持有」。相关对比见 B 节的基准表。")
    add("5. **最大风险和局限：** 观测频率受数据源限制（月频快照），前瞻窗口为「近似交易日」口径；"
        "PE 口径未经官方确认；样本外事件数很少，统计上不足以支持强结论；"
        "指标本身混合了「历史实现收益」与「当期到期收益率」两种不等价口径。")
    add("6. **是否值得进入下一轮研究？** 只有在取得更高频率、口径可确认的真实数据"
        "（全收益指数 + 官方 PE-TTM）之后才值得；当前证据强度不足以支撑参数细化。")
    add("7. **目前是否有足够证据将其用于实际投资决策？** **没有。** "
        "本研究为历史统计检验，样本外事件数少、口径存在近似，不具备决策依据的强度。")
    add("")
    add("---")
    add("")
    add("⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。")
    add("")
    return "\n".join(lines)


def build_report_input(
    panel_info: dict,
    results: dict[str, ExperimentResult],
    sensitivity: pd.DataFrame | None,
    cases_raw: dict[str, list],
    data_notes: list[str],
) -> ReportInput:
    """把各部分材料组装成报告输入。"""
    cases: dict[str, list[dict]] = {}
    for key, clist in cases_raw.items():
        cases[key] = [c.to_dict() if hasattr(c, "to_dict") else c for c in clist]
    return ReportInput(
        panel_info=panel_info,
        results=results,
        sensitivity=sensitivity if sensitivity is not None else pd.DataFrame(),
        cases=cases,
        data_notes=data_notes,
    )
