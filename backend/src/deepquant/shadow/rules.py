"""异常规则：把「和基线不一样」拆成可解释、可定位、可核对的判定。

为什么不是「偏离度打分」
------------------------
影子复盘的产出要给质检人员用，而不是给模型当特征。质检要的是「哪一步、凭什么、
回到哪张工件」，一个 0.87 的异常分数无法回答其中任何一项。因此这里用**规则 +
证据**，每条命中都必须能说出：规则码、判定依据（基线里的哪个计数）、以及具体
``card_id`` / 链上 ``seq``。

「基线不够就停用」为什么必须显式
--------------------------------
如果某操作在基线里只出现过 1 次，那么「它的参数骨架没见过」这句判断毫无信息
——基线本身没资格定义什么叫正常。此时规则**停用**并登记到 ``skipped_rules``，
而不是照常判定。否则报告会变成「样本越少、异常越多」的机器。

停用与通过必须区分开：停用是「这次没能力判断」，通过是「判断了，正常」。
把两者混成一个「未发现异常」，就是本项目反复吃过的「没检出 ≠ 没问题」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable, Sequence

from deepquant.shadow.baseline import TERMINAL_OPS, skeleton_index, transition_support
from deepquant.shadow.sequences import OperationSequence, Step

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}
SEVERITY_LABEL = {"high": "高", "medium": "中", "low": "低"}

#: 声明式业务前置条件：某些操作只有在会话内先完成前置动作才成立。
#:
#: 这是**业务规则**，不是从基线学来的统计规律。区别很重要：基线只能告诉你
#: 「这个跳转没见过」，说不清「这个跳转在业务上不成立」；反过来，前置条件
#: 规则不依赖语料规模，冷启动当天就能用。两类规则互补，故分开登记。
PREREQUISITES: dict[str, dict[str, int]] = {
    # 对比至少需要两个已完成并持久化的回测结果，否则比的是同一个或没有对象
    "backtest.compare": {"backtest.run": 2},
    # 组合回测必须先确认可用标的池
    "portfolio.backtest": {"dataset.list": 1},
}

#: 耗时离群阈值：p99 的倍数。为什么不用「p99 + 2000ms」这种绝对余量——
#: 快接口（十几毫秒）上 50 倍尖峰也只有几百毫秒，绝对余量会把它整个吞掉，
#: 于是「下游劣化」这条规则在最需要它的地方失效。
DURATION_FACTOR = 5.0


@dataclass(frozen=True)
class RuleSpec:
    """规则的静态描述（码表）。报告与文档共用同一份来源。"""

    code: str
    title: str
    dimension: str
    severity: str
    suggestion: str
    requires: str


RULE_CATALOG: tuple[RuleSpec, ...] = (
    RuleSpec(
        code="operator_unknown",
        title="操作人不在基线名单内",
        dimension="上下文",
        severity="high",
        suggestion="核实账号来源；若为新增同事，先在基线语料中累积历史再复核本条",
        requires="基线至少含 2 个操作人，否则名单本身不具区分度",
    ),
    RuleSpec(
        code="step_order_unseen",
        title="出现基线中从未出现的相邻操作跳转",
        dimension="顺序",
        severity="medium",
        suggestion="核对是否漏做前置步骤（如未取数据集直接回测），或流程已变更需重建基线",
        requires="基线相邻跳转总数 ≥ 5，且两端操作都在基线中登记过",
    ),
    RuleSpec(
        code="param_skeleton_unseen",
        title="参数结构与基线不同（键集合或数量级变化）",
        dimension="条件模式",
        severity="medium",
        suggestion="核对资金量级、参数键是否误填；同量级的小幅调整不会触发本条",
        requires="该操作在基线中出现 ≥ 3 次",
    ),
    RuleSpec(
        code="status_unseen",
        title="该操作出现基线中未曾出现的执行结果",
        dimension="条件模式",
        severity="medium",
        suggestion="确认是否为新增的失败模式；连续出现应按故障处理",
        requires="该操作在基线中出现 ≥ 3 次",
    ),
    RuleSpec(
        code="reject_storm",
        title="同一会话内连续 ≥3 次被拒绝",
        dimension="顺序",
        severity="high",
        suggestion="判断是参数反复试错还是上游故障；连续拒绝应触发人工介入",
        requires="基线拒绝率 > 0，否则「拒绝」本身已由 status_unseen 报出",
    ),
    RuleSpec(
        code="prerequisite_missing",
        title="缺少业务前置动作（如对比前未完成两次回测）",
        dimension="顺序",
        severity="medium",
        suggestion="补齐前置动作后重做；前置不成立的操作结果不可引用",
        requires="无需基线；依据声明式前置条件表 PREREQUISITES",
    ),
    RuleSpec(
        code="duration_outlier",
        title="单步耗时显著超过基线上界",
        dimension="时长",
        severity="low",
        suggestion="检查数据源或引擎是否劣化；结合 T3 延迟档案对照",
        requires="该操作在基线中有 ≥ 20 条耗时样本",
    ),
    RuleSpec(
        code="burst_repeat",
        title="同一请求被连续重复 ≥4 次",
        dimension="顺序",
        severity="medium",
        suggestion="确认是客户端重试风暴还是人工误操作；重复提交可能放大成本",
        requires="无需基线样本，判定基于请求体摘要完全相同",
    ),
    RuleSpec(
        code="session_multi_operator",
        title="同一会话出现多个操作人",
        dimension="上下文",
        severity="high",
        suggestion="核实会话是否被共享或凭证被转借；共享会话会破坏追责链",
        requires="无需基线样本，判定基于会话内操作人集合",
    ),
    RuleSpec(
        code="dangling_reject",
        title="会话以被拒绝的操作收尾（未重试成功）",
        dimension="会话终态",
        severity="low",
        suggestion="确认该次作业是否被放弃；放弃应在备注里留下原因",
        requires="基线存在拒绝样本",
    ),
    RuleSpec(
        code="flow_without_terminal",
        title="会话有多步操作但没有任何终态产出",
        dimension="会话终态",
        severity="low",
        suggestion="确认是否中断/仅浏览；若大量出现说明流程设计让人白跑",
        requires="基线中带终态操作的会话占比 ≥ 50%",
    ),
    RuleSpec(
        code="off_hours",
        title="操作发生在基线工作时段之外",
        dimension="上下文",
        severity="medium",
        suggestion="核实是否为非工作时段运维；非工作时段操作应留额外说明",
        requires="基线覆盖的小时跨度 ≥ 4 小时（单时段语料无法定义「工作时段」）",
    ),
)


@dataclass(frozen=True)
class AnomalyHit:
    """一条命中：规则码 + 定位 + 依据。"""

    code: str
    severity: str
    dimension: str
    sequence_id: str
    step_index: int
    card_id: str
    seq: int
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "dimension": self.dimension,
            "sequence_id": self.sequence_id,
            "step_index": self.step_index,
            "card_id": self.card_id,
            "seq": self.seq,
            "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class RuleSkipped:
    """一条被停用的规则（附原因，绝不静默）。"""

    code: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class SessionFinding:
    """一个会话的判定汇总。"""

    sequence_id: str
    operator: str
    session: str | None
    started_at: str
    pattern: tuple[str, ...]
    hits: tuple[AnomalyHit, ...]
    injected: str | None = None

    @property
    def severity(self) -> str:
        if not self.hits:
            return "none"
        return max((hit.severity for hit in self.hits), key=lambda item: SEVERITY_ORDER[item])

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({hit.code for hit in self.hits}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence_id": self.sequence_id,
            "operator": self.operator,
            "session": self.session,
            "started_at": self.started_at,
            "pattern": list(self.pattern),
            "severity": self.severity,
            "codes": list(self.codes),
            "hits": [hit.to_dict() for hit in self.hits],
            "injected": self.injected,
        }


def _spec_map() -> dict[str, RuleSpec]:
    return {spec.code: spec for spec in RULE_CATALOG}


def _hit(
    spec: RuleSpec,
    sequence: OperationSequence,
    index: int,
    detail: str,
    **evidence: Any,
) -> AnomalyHit:
    step: Step = sequence.steps[index]
    return AnomalyHit(
        code=spec.code,
        severity=spec.severity,
        dimension=spec.dimension,
        sequence_id=sequence.sequence_id,
        step_index=index,
        card_id=step.card_id,
        seq=step.seq,
        detail=detail,
        evidence=evidence,
    )


def _consecutive_runs(flags: Sequence[bool]) -> Iterable[tuple[int, int]]:
    """返回 ``(结束下标, 连续长度)``，只产出长度 ≥ 2 的段。"""
    length = 0
    for index, flag in enumerate(flags):
        if flag:
            length += 1
        else:
            if length >= 2:
                yield index - 1, length
            length = 0
    if length >= 2:
        yield len(flags) - 1, length


def audit_sequence(
    sequence: OperationSequence,
    baseline: dict[str, Any],
    *,
    transitions: dict[tuple[str, str], int] | None = None,
    skeletons: dict[str, set[str]] | None = None,
) -> tuple[list[AnomalyHit], list[RuleSkipped]]:
    """对单个序列执行全部规则。返回 ``(命中, 停用登记)``。"""
    specs = _spec_map()
    hits: list[AnomalyHit] = []
    skipped: list[RuleSkipped] = []

    if not sequence.steps:
        return hits, skipped

    transitions = transitions if transitions is not None else transition_support(baseline)
    skeletons = skeletons if skeletons is not None else skeleton_index(baseline)
    operations: dict[str, Any] = baseline.get("operations") or {}
    operators: dict[str, int] = baseline.get("operators") or {}

    # ---- 上下文：操作人 ----
    if len(operators) >= 2:
        unknown_steps = [i for i, step in enumerate(sequence.steps) if step.operator not in operators]
        if unknown_steps:
            index = unknown_steps[0]
            hits.append(
                _hit(
                    specs["operator_unknown"],
                    sequence,
                    index,
                    f"操作人 {sequence.steps[index].operator!r} 不在基线名单 {sorted(operators)} 内",
                    baseline_operators=sorted(operators),
                )
            )
    else:
        skipped.append(
            RuleSkipped(
                code="operator_unknown",
                reason=f"基线只有 {len(operators)} 个操作人，名单无可比性",
            )
        )

    # ---- 上下文：同一会话多个操作人 ----
    distinct = sequence.operators
    if len(distinct) >= 2:
        index = next(i for i, step in enumerate(sequence.steps) if len({s.operator for s in sequence.steps[: i + 1]}) >= 2)
        hits.append(
            _hit(
                specs["session_multi_operator"],
                sequence,
                index,
                f"同一会话出现 {len(distinct)} 个操作人：{list(distinct)}",
                operators=list(distinct),
            )
        )

    # ---- 顺序：相邻跳转 ----
    transition_total = sum(transitions.values())
    if transition_total >= 5:
        for index in range(1, len(sequence.steps)):
            left = sequence.steps[index - 1].action
            right = sequence.steps[index].action
            if left not in operations or right not in operations:
                continue  # 未知操作本身属别的问题，此处不重复报
            if transitions.get((left, right), 0) == 0:
                hits.append(
                    _hit(
                        specs["step_order_unseen"],
                        sequence,
                        index,
                        f"基线中从未出现 {left} → {right} 这一跳转",
                        from_action=left,
                        to_action=right,
                        baseline_total=transition_total,
                    )
                )
    else:
        skipped.append(
            RuleSkipped(
                code="step_order_unseen",
                reason=f"基线相邻跳转仅 {transition_total} 次（<5），不足以定义「常见顺序」",
            )
        )

    # ---- 顺序：业务前置条件 ----
    for index, step in enumerate(sequence.steps):
        required = PREREQUISITES.get(step.action)
        if not required:
            continue
        for prerequisite, minimum in required.items():
            done = sum(1 for earlier in sequence.steps[:index] if earlier.action == prerequisite)
            if done < minimum:
                hits.append(
                    _hit(
                        specs["prerequisite_missing"],
                        sequence,
                        index,
                        f"{step.action} 前应已完成 ≥{minimum} 次 {prerequisite}，本会话只有 {done} 次",
                        prerequisite=prerequisite,
                        required=minimum,
                        actual=done,
                    )
                )

    # ---- 条件模式：参数骨架 / 执行结果 ----
    for index, step in enumerate(sequence.steps):
        spec = operations.get(step.action)
        if spec is None:
            continue
        count = int(spec.get("count") or 0)
        if count < 3:
            skipped.append(
                RuleSkipped(
                    code="param_skeleton_unseen",
                    reason=f"操作 {step.action} 在基线中仅 {count} 次（<3），参数分布无统计意义",
                )
            )
            skipped.append(
                RuleSkipped(
                    code="status_unseen",
                    reason=f"操作 {step.action} 在基线中仅 {count} 次（<3），结果分布无统计意义",
                )
            )
            continue
        known_skeletons = skeletons.get(step.action, set())
        if step.skeleton_digest not in known_skeletons:
            hits.append(
                _hit(
                    specs["param_skeleton_unseen"],
                    sequence,
                    index,
                    f"操作 {step.action} 的参数结构与基线 {len(known_skeletons)} 种形态均不同",
                    skeleton_digest=step.skeleton_digest,
                    skeleton_count=len(known_skeletons),
                )
            )
        statuses = set((spec.get("statuses") or {}).keys())
        if step.status and step.status not in statuses:
            hits.append(
                _hit(
                    specs["status_unseen"],
                    sequence,
                    index,
                    f"操作 {step.action} 出现了基线未见的执行结果 {step.status!r}（基线：{sorted(statuses)}）",
                    status=step.status,
                    baseline_statuses=sorted(statuses),
                )
            )

    # ---- 顺序：连续拒绝 ----
    reject_flags = [step.status == "rejected" for step in sequence.steps]
    if any(reject_flags):
        for end_index, length in _consecutive_runs(reject_flags):
            if length >= 3:
                hits.append(
                    _hit(
                        specs["reject_storm"],
                        sequence,
                        end_index,
                        f"连续 {length} 次操作被拒绝",
                        run_length=length,
                    )
                )

    # ---- 时长：单步离群 ----
    for index, step in enumerate(sequence.steps):
        spec = operations.get(step.action) or {}
        duration = step.duration_ms
        profile = spec.get("duration_ms") or {}
        samples = int(profile.get("samples") or 0)
        p99 = profile.get("p99")
        if duration is None or p99 is None:
            continue
        if samples < 20:
            skipped.append(
                RuleSkipped(
                    code="duration_outlier",
                    reason=f"操作 {step.action} 基线耗时样本仅 {samples} 条（<20），分位数不可靠",
                )
            )
            continue
        threshold = float(p99) * DURATION_FACTOR
        if float(duration) > threshold:
            hits.append(
                _hit(
                    specs["duration_outlier"],
                    sequence,
                    index,
                    f"耗时 {duration}ms 超过阈值 {threshold:.0f}ms（基线 p99={p99:.0f}ms × {DURATION_FACTOR:g}）",
                    duration_ms=duration,
                    threshold_ms=threshold,
                    baseline_p99=p99,
                )
            )

    # ---- 顺序：连续重复同一请求 ----
    repeat_flags = [
        bool(step.body_digest)
        and index > 0
        and step.body_digest == sequence.steps[index - 1].body_digest
        and step.action == sequence.steps[index - 1].action
        for index, step in enumerate(sequence.steps)
    ]
    for end_index, length in _consecutive_runs(repeat_flags):
        streak = length + 1  # 首步不计入 flags，故实际重复次数为段长 + 1
        if streak >= 4:
            hits.append(
                _hit(
                    specs["burst_repeat"],
                    sequence,
                    end_index,
                    f"同一请求（操作 + 请求体摘要）连续提交 {streak} 次",
                    streak=streak,
                )
            )

    # ---- 会话终态 ----
    reject_rate = float(baseline.get("reject_rate") or 0.0)
    if sequence.steps[-1].status == "rejected":
        if reject_rate > 0:
            hits.append(
                _hit(
                    specs["dangling_reject"],
                    sequence,
                    len(sequence.steps) - 1,
                    "会话最后一步被拒绝，之后没有重试或收尾动作",
                    baseline_reject_rate=reject_rate,
                )
            )
        else:
            skipped.append(
                RuleSkipped(
                    code="dangling_reject",
                    reason="基线拒绝率为 0，「以拒绝收尾」的罕见程度无法量化",
                )
            )

    terminal_rate = (baseline.get("corpus") or {}).get("terminal_session_rate")
    if terminal_rate is not None:
        terminal_rate = float(terminal_rate)
        if terminal_rate >= 0.5 and not any(step.action in TERMINAL_OPS for step in sequence.steps):
            if len(sequence.steps) >= 3:
                hits.append(
                    _hit(
                        specs["flow_without_terminal"],
                        sequence,
                        len(sequence.steps) - 1,
                        f"{len(sequence.steps)} 步操作但没有任何终态产出（{sorted(TERMINAL_OPS)}）",
                        baseline_terminal_rate=terminal_rate,
                    )
                )
    else:
        skipped.append(
            RuleSkipped(code="flow_without_terminal", reason="基线无序列样本，终态占比不可得")
        )

    # ---- 上下文：工作时段 ----
    hours = baseline.get("hours") or {}
    span = int(hours.get("span") or 0)
    low, high = hours.get("min"), hours.get("max")
    if span >= 4 and low is not None and high is not None:
        for index, step in enumerate(sequence.steps):
            if step.hour is None:
                continue
            if not (int(low) <= step.hour <= int(high)):
                hits.append(
                    _hit(
                        specs["off_hours"],
                        sequence,
                        index,
                        f"操作时间 {step.hour} 时不在基线时段 [{low}, {high}] 内",
                        hour=step.hour,
                        baseline_hours=[low, high],
                    )
                )
    else:
        skipped.append(
            RuleSkipped(
                code="off_hours",
                reason=f"基线覆盖小时跨度仅 {span} 小时（<4），无法定义工作时段",
            )
        )

    return hits, skipped


def audit_sequences(
    sequences: Iterable[OperationSequence],
    baseline: dict[str, Any],
    *,
    baseline_digest: str | None = None,
) -> dict[str, Any]:
    """对一批序列出报告（含逐条证据、规则命中统计、停用登记）。"""
    transitions = transition_support(baseline)
    skeletons = skeleton_index(baseline)
    findings: list[SessionFinding] = []
    skipped_counter: dict[tuple[str, str], int] = {}
    scanned = 0

    for sequence in sequences:
        scanned += 1
        hits, skipped = audit_sequence(
            sequence, baseline, transitions=transitions, skeletons=skeletons
        )
        for item in skipped:
            key = (item.code, item.reason)
            skipped_counter[key] = skipped_counter.get(key, 0) + 1
        findings.append(
            SessionFinding(
                sequence_id=sequence.sequence_id,
                operator=sequence.operator,
                session=sequence.session,
                started_at=sequence.started_at,
                pattern=sequence.pattern,
                hits=tuple(hits),
                injected=sequence.injected,
            )
        )

    flagged = [finding for finding in findings if finding.hits]
    by_code: dict[str, int] = {}
    for finding in flagged:
        for code in finding.codes:
            by_code[code] = by_code.get(code, 0) + 1
    by_severity: dict[str, int] = {}
    for finding in flagged:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1

    skipped_rules = [
        {"code": code, "reason": reason, "sequences": count}
        for (code, reason), count in sorted(skipped_counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    catalog_codes = {spec.code for spec in RULE_CATALOG}
    skipped_codes = {item["code"] for item in skipped_rules}
    # 「启用了但没命中」必须与「停用了」分开报：前者是"判断过、正常"，
    # 后者是"没能力判断"。合并成一个「未发现」就是标准的假阴性。
    enabled_no_hit = sorted(catalog_codes - set(by_code) - skipped_codes)

    return {
        "report_version": "t6-audit-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline_digest": baseline_digest,
        "baseline_version": baseline.get("baseline_version"),
        "scanned_sequences": scanned,
        "flagged_sequences": len(flagged),
        "flag_rate": (len(flagged) / scanned) if scanned else 0.0,
        "summary": {
            "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
            "by_severity": by_severity,
            "skipped_rules": skipped_rules,
            "enabled_no_hit": enabled_no_hit,
        },
        "findings": [finding.to_dict() for finding in findings if finding.hits],
        "clean_count": scanned - len(flagged),
    }


def render_markdown(report: dict[str, Any], *, title: str = "影子复盘异常审计报告") -> str:
    """把报告渲染成质检人员可直接阅读的 Markdown（含证据定位）。"""
    specs = _spec_map()
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- 生成时间：{report.get('generated_at')}")
    lines.append(f"- 基线版本：{report.get('baseline_version')}（摘要 `{report.get('baseline_digest')}`）")
    lines.append(f"- 扫描会话：{report.get('scanned_sequences')}，标记会话：{report.get('flagged_sequences')}")
    lines.append(
        f"- 标记率（标记会话 / 扫描会话）：{float(report.get('flag_rate') or 0):.1%}"
        "　——这是「有多少会话被标记」，不是准确率，也不代表真实异常率"
    )
    lines.append("")
    lines.append("## 一、命中规则统计")
    lines.append("")
    by_code = (report.get("summary") or {}).get("by_code") or {}
    if by_code:
        lines.append("| 规则码 | 规则说明 | 维度 | 严重度 | 命中会话数 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for code, count in by_code.items():
            spec = specs.get(code)
            if spec is None:
                lines.append(f"| `{code}` | （未登记规则） | - | - | {count} |")
                continue
            lines.append(
                f"| `{code}` | {spec.title} | {spec.dimension} | {SEVERITY_LABEL[spec.severity]} | {count} |"
            )
    else:
        lines.append("本次没有命中任何规则。**这不等于没有问题**：先看第三节的停用规则，"
                     "被停用的维度本次没有能力判断。")
    lines.append("")

    lines.append("## 二、逐条明细（按严重度排序）")
    lines.append("")
    findings = sorted(
        report.get("findings") or [],
        key=lambda item: (-SEVERITY_ORDER.get(str(item.get("severity")), 0), item.get("sequence_id") or ""),
    )
    if not findings:
        lines.append("（无命中）")
        lines.append("")
    for item in findings:
        lines.append(
            f"### {item.get('sequence_id')}（{SEVERITY_LABEL.get(str(item.get('severity')), item.get('severity'))}）"
        )
        lines.append("")
        lines.append(f"- 操作人：{item.get('operator')}｜开始时间：{item.get('started_at')}")
        lines.append(f"- 操作序列：{' → '.join(item.get('pattern') or [])}")
        if item.get("injected"):
            lines.append(f"- 评估标记：本次命中来自注入的 `{item.get('injected')}` 样本（仅供评估）")
        lines.append("")
        lines.append("| 规则码 | 说明 | 定位（card_id / seq） | 建议动作 |")
        lines.append("| --- | --- | --- | --- |")
        for hit in item.get("hits") or []:
            spec = specs.get(str(hit.get("code")))
            suggestion = spec.suggestion if spec else "-"
            lines.append(
                f"| `{hit.get('code')}` | {hit.get('detail')} | `{hit.get('card_id')}` / {hit.get('seq')} | {suggestion} |"
            )
        lines.append("")

    skipped = (report.get("summary") or {}).get("skipped_rules") or []
    lines.append("## 三、本次停用的规则（未判断 ≠ 正常）")
    lines.append("")
    if skipped:
        lines.append("| 规则码 | 停用原因 | 涉及会话数 |")
        lines.append("| --- | --- | --- |")
        for item in skipped:
            lines.append(f"| `{item.get('code')}` | {item.get('reason')} | {item.get('sequences')} |")
    else:
        lines.append("全部规则均已启用。")
    lines.append("")
    enabled_no_hit = (report.get("summary") or {}).get("enabled_no_hit") or []
    lines.append("## 四、本次已启用但未命中的规则（判断过、结果正常）")
    lines.append("")
    if enabled_no_hit:
        lines.append("、".join(f"`{code}`" for code in enabled_no_hit))
        lines.append("")
        lines.append("这些维度本次没有发现异常，可以当作「检查过」；第三节的规则则是「没检查」。")
    else:
        lines.append("无（所有启用规则均有命中，或全部被停用）。")
    lines.append("")
    return "\n".join(lines)
