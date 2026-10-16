"""Step 7.3：参数敏感性分析 + 过拟合风险审计。

与 Step 7.2 的关系：
- Step 7.2 在每个窗口的**训练期**内选择参数、在该窗口**独立 OOS** 区间评估；
- 本模块**不执行任何回测**，只消费 Step 7.2 已经产出的
  ``list[WindowWFResult]`` 结果，做事后分析：
  * 训练期敏感性：每个候选参数在各窗口训练期的表现分布；
  * 相邻参数表现差异：只改变 fast 或 slow 时的训练期收益差；
  * OOS 稳定性：各窗口选中参数的分布 / OOS 窗口集中度 / 正收益占比；
  * 过拟合风险审计：一组显式规则，每条规则都有可解释的阈值和触发状态。

**严格约束**：
- 不修改 Step 7.2 的任何结果对象；
- 不把全部候选的 OOS 收益拿来二次挑选"最佳参数"；
- 只在 Step 7.2 已经选定的参数上做 OOS 稳定性分析；
- 不输出"过拟合概率"这类未经统计验证的量。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd

from deepquant.walkforward.parameter_selection import (
    GridSearchConfig,
    WindowWFResult,
    generate_ma_cross_candidates,
)


# =========================================================================== #
# 结果数据类
# =========================================================================== #


@dataclass(frozen=True)
class CandidateTrainSummary:
    """单组候选参数在所有窗口训练期的聚合表现。"""

    params: dict
    n_windows_evaluated: int
    n_train_periods_total: int
    cumulative_return_median: float | None
    cumulative_return_mean: float | None
    cumulative_return_std: float | None
    max_drawdown_ratio_median: float | None
    n_closed_trades_median: float | None
    n_success: int
    n_failed: int


@dataclass(frozen=True)
class AdjacentPairDiff:
    """一对相邻合法参数组合的训练期收益差异。

    "相邻"定义：fast_window 或 slow_window 其中一个改变、另一个不变、
    两个组合都是 slow > fast 的合法候选。
    """

    params_a: dict
    params_b: dict
    varying_param: str       # "fast_window" or "slow_window"
    median_diff_a_minus_b: float | None
    # 上面是跨窗口的 (a.ret - b.ret) 中位数；None 表示数据不足


@dataclass(frozen=True)
class TrainingSensitivityReport:
    """训练期敏感性分析结果（每个候选参数在各窗口训练期的表现分布）。"""

    n_windows: int
    n_params: int                              # 候选参数总数
    candidates: tuple[CandidateTrainSummary, ...]
    adjacent_diffs: tuple[AdjacentPairDiff, ...]
    # 简单的跨窗口观察：训练期累计收益的总体中位数 / 分位数（所有合法候选合并）
    overall_train_return_median: float | None
    overall_train_return_p25: float | None
    overall_train_return_p75: float | None


@dataclass(frozen=True)
class ParamSelectionStability:
    """各窗口**选中参数**的分布。"""

    n_selection_success: int
    n_selection_failed: int
    n_valid_windows_for_stability: int        # selection_success 的窗口数
    param_distribution: dict                  # {参数组合字符串: 选中次数}
    selection_entropy: float | None           # 简单的 Shannon entropy；None = 不可计算
    n_distinct_selected_params: int
    # 相邻窗口选中参数是否不同
    n_window_transitions: int                 # 相邻窗口选中参数变化次数
    n_pairs: int                              # 相邻窗口对总数


@dataclass(frozen=True)
class OOSWindowConcentration:
    """OOS 窗口表现分布与集中度（只统计 n_oos_with_valid_metrics 的窗口）。"""

    n_valid: int
    positive_count: int
    positive_ratio: float | None
    median_return: float | None
    p25_return: float | None
    p75_return: float | None
    median_mdd_ratio: float | None
    median_trade_count: float | None
    # 贡献集中度：Top 25% 正向窗口贡献的正收益占全部正收益的比例
    # 只在正收益窗口数 >= 2 时计算；否则 None
    top_positive_contribution_ratio: float | None


@dataclass(frozen=True)
class RiskRule:
    """单条风险规则的定义 + 当前触发状态。"""

    rule_id: str                           # 稳定 ID
    level: str                             # "not_triggered" / "warning" / "elevated" / "low_evidence"
    reason: str                            # 人可读解释
    trigger_data: dict                     # 规则用到的关键数值（用于审计）
    threshold_source: str                  # "initial_conservative_rule" / "none" 等


@dataclass(frozen=True)
class OverfittingRiskReport:
    """过拟合风险审计整体结果。"""

    rules: tuple[RiskRule, ...]
    any_elevated: bool
    any_warning: bool
    any_low_evidence: bool
    summary_text: str


# =========================================================================== #
# 辅助：参数字典 → 可排序 / 可哈希的 key
# =========================================================================== #


def _param_key(params: dict) -> tuple:
    return tuple(sorted((k, v) for k, v in params.items()))


def _param_key_str(params: dict) -> str:
    return str(sorted((k, v) for k, v in params.items()))


# =========================================================================== #
# 相邻参数对生成
# =========================================================================== #


def build_adjacent_pairs(candidates: list[dict]) -> list[tuple[dict, dict, str]]:
    """从合法候选集合中生成"相邻参数对"。

    相邻 = 两个候选 fast_window 或 slow_window **恰好只有一个**不同，
    且差异为 1（或同方向的最近邻）。参数按数值升序排好再比较，保证确定性。

    返回 [(params_a, params_b, varying_param), ...]
    """
    pairs: list[tuple[dict, dict, str]] = []
    # 按 fast_window 分组，同组内看 slow_window 的相邻值
    from collections import defaultdict

    by_fast: dict[int, list[dict]] = defaultdict(list)
    by_slow: dict[int, list[dict]] = defaultdict(list)
    for c in candidates:
        by_fast[int(c["fast_window"])].append(c)
        by_slow[int(c["slow_window"])].append(c)

    # 同一 fast_window，slow_window 排序后两两相邻
    for fast, group in by_fast.items():
        sorted_group = sorted(group, key=lambda c: int(c["slow_window"]))
        for i in range(len(sorted_group) - 1):
            a, b = sorted_group[i], sorted_group[i + 1]
            pairs.append((a, b, "slow_window"))

    # 同一 slow_window，fast_window 排序后两两相邻
    for slow, group in by_slow.items():
        sorted_group = sorted(group, key=lambda c: int(c["fast_window"]))
        for i in range(len(sorted_group) - 1):
            a, b = sorted_group[i], sorted_group[i + 1]
            pairs.append((a, b, "fast_window"))

    # 去重（(a,b,v) 和 (b,a,v) 视为一对，统一用 fast/slow 升序放前面）
    seen: set = set()
    unique_pairs: list[tuple[dict, dict, str]] = []
    for a, b, v in pairs:
        key = (_param_key(a), _param_key(b), v)
        if key in seen:
            continue
        seen.add(key)
        unique_pairs.append((a, b, v))
    return unique_pairs


# =========================================================================== #
# 训练期敏感性
# =========================================================================== #


def analyze_training_sensitivity(
    results: list[WindowWFResult],
    *,
    candidates: list[dict] | None = None,
) -> TrainingSensitivityReport:
    """聚合每个候选参数在各窗口训练期的表现。

    输入：Step 7.2 的 ``run_walkforward_v2`` 产出的 ``WindowWFResult`` 列表；
          可选的 candidates（如果 None，从 results[0].train_candidate_results[*].params 提取）。

    不修改 results；纯读取。
    """
    if not results:
        return TrainingSensitivityReport(
            n_windows=0, n_params=0,
            candidates=(), adjacent_diffs=(),
            overall_train_return_median=None,
            overall_train_return_p25=None, overall_train_return_p75=None,
        )

    n_windows = len(results)

    # 候选集合
    if candidates is None:
        seen: set = set()
        cs: list[dict] = []
        for r in results:
            for c in r.train_candidate_results:
                k = _param_key(c.params)
                if k not in seen:
                    seen.add(k)
                    cs.append(c.params)
        candidates = cs

    # 每个候选在各窗口的训练期指标收集
    per_candidate: dict[tuple, dict] = {}
    for c in candidates:
        per_candidate[_param_key(c)] = {
            "candidate": c,
            "returns": [],      # 有效训练期 cum_return
            "mdds": [],         # max_drawdown_ratio
            "trades": [],       # n_closed_trades
            "success_count": 0,
            "fail_count": 0,
        }

    total_train_periods = 0
    for r in results:
        total_train_periods += r.train_periods
        for c in r.train_candidate_results:
            key = _param_key(c.params)
            if key not in per_candidate:
                per_candidate[key] = {
                    "candidate": c.params, "returns": [], "mdds": [],
                    "trades": [], "success_count": 0, "fail_count": 0,
                }
            bucket = per_candidate[key]
            if c.success:
                bucket["success_count"] += 1
                if c.cumulative_return is not None and math.isfinite(c.cumulative_return):
                    bucket["returns"].append(c.cumulative_return)
                if c.max_drawdown_ratio is not None and math.isfinite(c.max_drawdown_ratio):
                    bucket["mdds"].append(c.max_drawdown_ratio)
                if c.n_closed_trades is not None:
                    bucket["trades"].append(c.n_closed_trades)
            else:
                bucket["fail_count"] += 1

    # 构造 CandidateTrainSummary
    summaries: list[CandidateTrainSummary] = []
    for bucket in per_candidate.values():
        rets = bucket["returns"]
        mdds = bucket["mdds"]
        trades = bucket["trades"]
        summaries.append(CandidateTrainSummary(
            params=bucket["candidate"],
            n_windows_evaluated=bucket["success_count"] + bucket["fail_count"],
            n_train_periods_total=total_train_periods,
            cumulative_return_median=_median_or_none(rets),
            cumulative_return_mean=_mean_or_none(rets),
            cumulative_return_std=_std_or_none(rets),
            max_drawdown_ratio_median=_median_or_none(mdds),
            n_closed_trades_median=_median_or_none(trades),
            n_success=bucket["success_count"],
            n_failed=bucket["fail_count"],
        ))

    # 相邻参数对
    pairs = build_adjacent_pairs(candidates)
    pair_diffs: list[AdjacentPairDiff] = []
    for a, b, v in pairs:
        # 收集两个候选在各窗口的训练期收益，算 a - b 的分布
        rets_a: list[float] = []
        rets_b: list[float] = []
        for r in results:
            tr_a = _find_train_candidate(r, a)
            tr_b = _find_train_candidate(r, b)
            if (tr_a and tr_a.success and tr_a.cumulative_return is not None
                    and math.isfinite(tr_a.cumulative_return)
                    and tr_b and tr_b.success and tr_b.cumulative_return is not None
                    and math.isfinite(tr_b.cumulative_return)):
                rets_a.append(tr_a.cumulative_return)
                rets_b.append(tr_b.cumulative_return)
        if rets_a and rets_b and len(rets_a) == len(rets_b):
            diffs = [a - b for a, b in zip(rets_a, rets_b)]
            pair_diffs.append(AdjacentPairDiff(
                params_a=a, params_b=b, varying_param=v,
                median_diff_a_minus_b=_median_or_none(diffs),
            ))
        else:
            pair_diffs.append(AdjacentPairDiff(
                params_a=a, params_b=b, varying_param=v,
                median_diff_a_minus_b=None,
            ))

    # 总体分布（所有候选 × 所有窗口的有效训练期收益合并）
    all_rets: list[float] = []
    for s in summaries:
        # 从每个候选的 train_candidate_results 里拉
        for r in results:
            tc = _find_train_candidate(r, s.params)
            if tc and tc.success and tc.cumulative_return is not None:
                all_rets.append(tc.cumulative_return)

    return TrainingSensitivityReport(
        n_windows=n_windows,
        n_params=len(candidates),
        candidates=tuple(sorted(summaries, key=lambda s: _param_key(s.params))),
        adjacent_diffs=tuple(pair_diffs),
        overall_train_return_median=_median_or_none(all_rets),
        overall_train_return_p25=_percentile_or_none(all_rets, 25),
        overall_train_return_p75=_percentile_or_none(all_rets, 75),
    )


def _find_train_candidate(r: WindowWFResult, params: dict):
    pk = _param_key(params)
    for t in r.train_candidate_results:
        if _param_key(t.params) == pk:
            return t
    return None


# =========================================================================== #
# OOS 稳定性 + 参数选择稳定性
# =========================================================================== #


def analyze_param_selection_stability(
    results: list[WindowWFResult],
) -> ParamSelectionStability:
    """统计各窗口**选中参数**的分布。"""
    sel_ok = [r for r in results if r.selection_success and r.selected_params is not None]
    sel_fail = [r for r in results if not r.selection_success]

    n_ok = len(sel_ok)
    n_fail = len(sel_fail)

    # 参数分布
    dist: dict[str, int] = {}
    for r in sel_ok:
        k = _param_key_str(r.selected_params)
        dist[k] = dist.get(k, 0) + 1

    n_distinct = len(dist)

    # Shannon entropy (只在 n_ok > 1 且 n_distinct > 1 时有意义)
    entropy: float | None
    if n_ok >= 2 and n_distinct >= 1:
        ent = 0.0
        for count in dist.values():
            p = count / n_ok
            if p > 0:
                ent -= p * math.log2(p)
        entropy = ent
    else:
        entropy = None

    # 相邻窗口选中参数变化次数
    transitions = 0
    pairs = 0
    ordered = [r for r in results if r.selection_success and r.selected_params is not None]
    for i in range(1, len(ordered)):
        pairs += 1
        if _param_key(ordered[i].selected_params) != _param_key(ordered[i - 1].selected_params):
            transitions += 1

    return ParamSelectionStability(
        n_selection_success=n_ok,
        n_selection_failed=n_fail,
        n_valid_windows_for_stability=n_ok,
        param_distribution=dist,
        selection_entropy=entropy,
        n_distinct_selected_params=n_distinct,
        n_window_transitions=transitions,
        n_pairs=pairs,
    )


def analyze_oos_concentration(
    results: list[WindowWFResult],
) -> OOSWindowConcentration:
    """统计 OOS 窗口表现分布与集中度。

    只在 ``selection_success`` 且 ``oos_success`` 且 ``oos_is_complete=True``
    且 ``final_equity is not None`` 的窗口上统计。
    """
    valid = [
        r for r in results
        if r.selection_success and r.oos_success
        and r.oos_is_complete is True and r.final_equity is not None
    ]
    n = len(valid)

    if n == 0:
        return OOSWindowConcentration(
            n_valid=0, positive_count=0, positive_ratio=None,
            median_return=None, p25_return=None, p75_return=None,
            median_mdd_ratio=None, median_trade_count=None,
            top_positive_contribution_ratio=None,
        )

    rets = [r.cumulative_return for r in valid if r.cumulative_return is not None]
    mdds = [r.max_drawdown_ratio for r in valid if r.max_drawdown_ratio is not None]
    trades = [r.trade_count for r in valid if r.trade_count is not None]

    pos_rets = [v for v in rets if v is not None and v > 0]
    pos_count = len(pos_rets)
    pos_ratio = pos_count / n if n > 0 else None

    # 贡献集中度：只正收益样本 >= 2 且 >= 2 个有效窗口
    top_ratio: float | None = None
    if pos_count >= 2 and n >= 2:
        sorted_pos = sorted(pos_rets, reverse=True)
        top_k = max(1, math.ceil(len(sorted_pos) * 0.25))
        top_sum = sum(sorted_pos[:top_k])
        all_sum = sum(sorted_pos)
        if all_sum > 0:
            top_ratio = top_sum / all_sum

    return OOSWindowConcentration(
        n_valid=n,
        positive_count=pos_count,
        positive_ratio=pos_ratio,
        median_return=_median_or_none(rets),
        p25_return=_percentile_or_none(rets, 25),
        p75_return=_percentile_or_none(rets, 75),
        median_mdd_ratio=_median_or_none(mdds),
        median_trade_count=_median_or_none(trades),
        top_positive_contribution_ratio=top_ratio,
    )


# =========================================================================== #
# 过拟合风险规则
# =========================================================================== #


@dataclass(frozen=True)
class RiskThresholds:
    """风险规则阈值（保守初始值，可配置）。"""

    min_oos_valid_windows_for_confidence: int = 5     # 有效 OOS 窗口 < 此数 → low_evidence
    min_positive_oos_windows_for_confidence: int = 3  # 正收益 OOS 窗口 < 此数 → low_evidence
    # OOS 正收益占比过低 → warning
    low_positive_ratio: float = 0.3
    high_positive_ratio: float = 0.6                 # 用于区分 warning vs elevated
    # 参数选择不稳定 → warning：相邻窗口选中参数变化比例
    high_param_transition_ratio: float = 0.6
    # 正收益集中度 → warning：Top 25% 正窗口贡献占比
    high_top_positive_ratio: float = 0.8
    # 训练期收益中位数过低 → warning（说明训练期本身就没有足够 alpha）
    low_train_return_median: float = 0.0


def audit_overfitting_risk(
    results: list[WindowWFResult],
    *,
    train_sensitivity: TrainingSensitivityReport | None = None,
    param_stability: ParamSelectionStability | None = None,
    oos_concentration: OOSWindowConcentration | None = None,
    thresholds: RiskThresholds = RiskThresholds(),
) -> OverfittingRiskReport:
    """一组显式规则的过拟合风险审计。

    注意：输出中的所有 "warning" / "elevated" / "not_triggered" 都是相对于
    当前这组规则的触发状态，**不是**对策略真实过拟合概率的估计。
    """
    rules: list[RiskRule] = []

    if train_sensitivity is None:
        train_sensitivity = analyze_training_sensitivity(results)
    if param_stability is None:
        param_stability = analyze_param_selection_stability(results)
    if oos_concentration is None:
        oos_concentration = analyze_oos_concentration(results)

    n_valid = oos_concentration.n_valid
    n_pos = oos_concentration.positive_count
    pos_ratio = oos_concentration.positive_ratio

    # R1：有效 OOS 窗口数量不足
    if n_valid < thresholds.min_oos_valid_windows_for_confidence:
        rules.append(RiskRule(
            rule_id="R1_low_valid_oos_windows",
            level="low_evidence",
            reason=(
                f"有效 OOS 窗口仅 {n_valid} < {thresholds.min_oos_valid_windows_for_confidence}，"
                f"统计结论证据不足"
            ),
            trigger_data={"n_valid": n_valid, "threshold": thresholds.min_oos_valid_windows_for_confidence},
            threshold_source="initial_conservative_rule",
        ))
    else:
        rules.append(RiskRule(
            rule_id="R1_low_valid_oos_windows",
            level="not_triggered",
            reason=f"有效 OOS 窗口 {n_valid} ≥ {thresholds.min_oos_valid_windows_for_confidence}",
            trigger_data={"n_valid": n_valid, "threshold": thresholds.min_oos_valid_windows_for_confidence},
            threshold_source="initial_conservative_rule",
        ))

    # R2：正收益 OOS 窗口数不足
    if n_pos < thresholds.min_positive_oos_windows_for_confidence:
        rules.append(RiskRule(
            rule_id="R2_low_positive_oos_count",
            level="low_evidence",
            reason=(
                f"正收益 OOS 窗口仅 {n_pos} < {thresholds.min_positive_oos_windows_for_confidence}"
            ),
            trigger_data={"n_pos": n_pos, "threshold": thresholds.min_positive_oos_windows_for_confidence},
            threshold_source="initial_conservative_rule",
        ))
    else:
        rules.append(RiskRule(
            rule_id="R2_low_positive_oos_count",
            level="not_triggered",
            reason=f"正收益 OOS 窗口 {n_pos} ≥ {thresholds.min_positive_oos_windows_for_confidence}",
            trigger_data={"n_pos": n_pos, "threshold": thresholds.min_positive_oos_windows_for_confidence},
            threshold_source="initial_conservative_rule",
        ))

    # R3：OOS 正收益占比过低
    if pos_ratio is None:
        rules.append(RiskRule(
            rule_id="R3_oos_positive_ratio",
            level="low_evidence",
            reason="没有足够的有效 OOS 窗口计算正收益占比",
            trigger_data={"pos_ratio": None},
            threshold_source="none",
        ))
    elif pos_ratio < thresholds.low_positive_ratio:
        rules.append(RiskRule(
            rule_id="R3_oos_positive_ratio",
            level="elevated",
            reason=(
                f"OOS 正收益占比 {pos_ratio:.2%} < {thresholds.low_positive_ratio:.0%}，"
                f"多数窗口 OOS 亏损，参数选择可能只在少数训练期偶然有效"
            ),
            trigger_data={"pos_ratio": pos_ratio, "threshold": thresholds.low_positive_ratio},
            threshold_source="initial_conservative_rule",
        ))
    else:
        rules.append(RiskRule(
            rule_id="R3_oos_positive_ratio",
            level="not_triggered",
            reason=f"OOS 正收益占比 {pos_ratio:.2%} ≥ {thresholds.low_positive_ratio:.0%}",
            trigger_data={"pos_ratio": pos_ratio},
            threshold_source="initial_conservative_rule",
        ))

    # R4：参数选择不稳定（相邻窗口选中参数频繁改变）
    if param_stability.n_pairs < 1:
        rules.append(RiskRule(
            rule_id="R4_param_selection_instable",
            level="low_evidence",
            reason="只有一个成功选择的窗口，无法评估稳定性",
            trigger_data={"n_pairs": 0},
            threshold_source="none",
        ))
    else:
        ratio = param_stability.n_window_transitions / param_stability.n_pairs
        if ratio > thresholds.high_param_transition_ratio:
            rules.append(RiskRule(
                rule_id="R4_param_selection_instable",
                level="warning",
                reason=(
                    f"相邻窗口选中参数变化比例 {ratio:.2%} > "
                    f"{thresholds.high_param_transition_ratio:.0%}，"
                    f"参数选择可能对训练期噪声敏感"
                ),
                trigger_data={
                    "n_transitions": param_stability.n_window_transitions,
                    "n_pairs": param_stability.n_pairs,
                    "ratio": ratio,
                    "threshold": thresholds.high_param_transition_ratio,
                },
                threshold_source="initial_conservative_rule",
            ))
        else:
            rules.append(RiskRule(
                rule_id="R4_param_selection_instable",
                level="not_triggered",
                reason=f"参数选择过渡比例 {ratio:.2%} ≤ {thresholds.high_param_transition_ratio:.0%}",
                trigger_data={"ratio": ratio, "n_transitions": param_stability.n_window_transitions,
                              "n_pairs": param_stability.n_pairs},
                threshold_source="initial_conservative_rule",
            ))

    # R5：OOS 正收益集中在少数窗口
    top_ratio = oos_concentration.top_positive_contribution_ratio
    if top_ratio is None:
        rules.append(RiskRule(
            rule_id="R5_oos_positive_concentrated",
            level="low_evidence",
            reason="正收益窗口不足 2 个，无法评估集中度",
            trigger_data={"top_positive_ratio": None},
            threshold_source="none",
        ))
    elif top_ratio > thresholds.high_top_positive_ratio:
        rules.append(RiskRule(
            rule_id="R5_oos_positive_concentrated",
            level="warning",
            reason=(
                f"Top 25% 正收益窗口贡献 {top_ratio:.2%} 的正收益 > "
                f"{thresholds.high_top_positive_ratio:.0%}，"
                f"OOS 表现可能依赖少数偶然窗口"
            ),
            trigger_data={"top_positive_ratio": top_ratio,
                          "threshold": thresholds.high_top_positive_ratio},
            threshold_source="initial_conservative_rule",
        ))
    else:
        rules.append(RiskRule(
            rule_id="R5_oos_positive_concentrated",
            level="not_triggered",
            reason=f"Top 25% 正收益贡献 {top_ratio:.2%} ≤ {thresholds.high_top_positive_ratio:.0%}",
            trigger_data={"top_positive_ratio": top_ratio},
            threshold_source="initial_conservative_rule",
        ))

    # R6：训练期累计收益中位数偏低（参数选择可能无有效 alpha 可用）
    med_train = train_sensitivity.overall_train_return_median
    if med_train is None:
        rules.append(RiskRule(
            rule_id="R6_train_alpha_unclear",
            level="low_evidence",
            reason="训练期收益中位数不可计算",
            trigger_data={"train_median": None},
            threshold_source="none",
        ))
    elif med_train < thresholds.low_train_return_median:
        rules.append(RiskRule(
            rule_id="R6_train_alpha_unclear",
            level="warning",
            reason=(
                f"训练期累计收益中位数 {med_train:.4f} < {thresholds.low_train_return_median}，"
                f"训练期本身就没有足够的 alpha 可供后续参数选择"
            ),
            trigger_data={"train_median": med_train,
                          "threshold": thresholds.low_train_return_median},
            threshold_source="initial_conservative_rule",
        ))
    else:
        rules.append(RiskRule(
            rule_id="R6_train_alpha_unclear",
            level="not_triggered",
            reason=f"训练期累计收益中位数 {med_train:.4f} ≥ {thresholds.low_train_return_median}",
            trigger_data={"train_median": med_train},
            threshold_source="initial_conservative_rule",
        ))

    # R7：费用来源是否为模拟占位
    fee_sources = {r.fee_source for r in results}
    if fee_sources == {"simulation_placeholder"}:
        rules.append(RiskRule(
            rule_id="R7_fee_model_is_placeholder",
            level="warning",
            reason=(
                "费用为 simulation_placeholder（D6 模拟占位参数），"
                "不代表真实券商费率；费用变化可能显著影响 OOS 表现排名"
            ),
            trigger_data={"fee_sources": sorted(fee_sources)},
            threshold_source="project_constant",
        ))
    else:
        rules.append(RiskRule(
            rule_id="R7_fee_model_is_placeholder",
            level="not_triggered",
            reason=f"费用来源: {sorted(fee_sources)}",
            trigger_data={"fee_sources": sorted(fee_sources)},
            threshold_source="project_constant",
        ))

    # R8：参数网格大小相对于训练数据量
    # 候选数 / 总训练期 ≈ 过拟合机会比；粗略阈值
    n_candidates = train_sensitivity.n_params
    n_valid_windows = param_stability.n_selection_success
    grid_ratio = n_candidates / max(n_valid_windows, 1)
    high_grid_ratio = 1.0  # 每个有效窗口的训练期中值一个候选 → 合理；高于则 grid 偏大
    if grid_ratio > high_grid_ratio:
        rules.append(RiskRule(
            rule_id="R8_param_grid_too_large",
            level="warning",
            reason=(
                f"候选数 / 有效窗口数 = {grid_ratio:.2f} > {high_grid_ratio}，"
                f"参数搜索空间相对研究数据偏大，训练期过拟合风险升高"
            ),
            trigger_data={"n_candidates": n_candidates,
                          "n_valid_windows": n_valid_windows,
                          "grid_ratio": grid_ratio,
                          "threshold": high_grid_ratio},
            threshold_source="initial_conservative_rule",
        ))
    else:
        rules.append(RiskRule(
            rule_id="R8_param_grid_too_large",
            level="not_triggered",
            reason=f"候选数 / 有效窗口数 = {grid_ratio:.2f} ≤ {high_grid_ratio}",
            trigger_data={"grid_ratio": grid_ratio,
                          "n_candidates": n_candidates,
                          "n_valid_windows": n_valid_windows},
            threshold_source="initial_conservative_rule",
        ))

    any_elevated = any(r.level == "elevated" for r in rules)
    any_warning = any(r.level == "warning" for r in rules)
    any_low = any(r.level == "low_evidence" for r in rules)

    if any_elevated:
        summary = "至少一条规则触发 elevated：当前证据不足以支持策略稳健性结论。"
    elif any_warning:
        summary = "若干规则触发 warning：存在需要关注的风险信号，但尚无 elevated 级别问题。"
    elif any_low:
        summary = "多条规则返回 low_evidence：数据不足，无法作出判断。"
    else:
        summary = "所有规则 not_triggered。注意：not_triggered ≠ 策略没有过拟合，仅表示未触发当前规则。"

    return OverfittingRiskReport(
        rules=tuple(rules),
        any_elevated=any_elevated,
        any_warning=any_warning,
        any_low_evidence=any_low,
        summary_text=summary,
    )


# =========================================================================== #
# 顶层 entry：一步拿到所有分析
# =========================================================================== #


@dataclass(frozen=True)
class SensitivityAndRiskReport:
    """把三项分析 + 风险审计打包。"""

    n_total_windows: int
    n_valid_windows_for_selection: int          # selection_success
    training: TrainingSensitivityReport
    param_stability: ParamSelectionStability
    oos_concentration: OOSWindowConcentration
    risk: OverfittingRiskReport


def run_sensitivity_and_risk_audit(
    results: list[WindowWFResult],
    *,
    grid_config: GridSearchConfig | None = None,
    thresholds: RiskThresholds = RiskThresholds(),
) -> SensitivityAndRiskReport:
    """对 Step 7.2 的结果列表一次性跑完整敏感性分析 + 风险审计。

    纯函数：不修改传入的 ``results``。
    """
    # 候选参数集合
    candidates: list[dict] | None = None
    if grid_config is not None:
        candidates = generate_ma_cross_candidates(grid_config)

    training = analyze_training_sensitivity(results, candidates=candidates)
    stability = analyze_param_selection_stability(results)
    concentration = analyze_oos_concentration(results)
    risk = audit_overfitting_risk(
        results,
        train_sensitivity=training,
        param_stability=stability,
        oos_concentration=concentration,
        thresholds=thresholds,
    )

    return SensitivityAndRiskReport(
        n_total_windows=len(results),
        n_valid_windows_for_selection=stability.n_selection_success,
        training=training,
        param_stability=stability,
        oos_concentration=concentration,
        risk=risk,
    )


# =========================================================================== #
# 数值小工具（np/ pd 依赖在函数内部，方便单独测试）
# =========================================================================== #


def _median_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return float(pd.Series(values).median())


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return float(pd.Series(values).mean())


def _std_or_none(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return float(pd.Series(values).std(ddof=0))


def _percentile_or_none(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    return float(pd.Series(values).quantile(pct / 100.0))
