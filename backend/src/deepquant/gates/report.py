"""闸门报告渲染：给研究员与评审看的 Markdown。

排版原则：**先结论、再否决理由、最后明细**。评审最常问的两个问题是
「过了没有」和「不过是因为哪一条」，所以前两节必须能独立回答它们；明细留作复核。
"""

from __future__ import annotations

from typing import Any, Mapping

VERDICT_LABEL = {
    "L1A_PASS": "通过",
    "L1A_REJECTED_SPEC": "否决：契约不完整",
    "L1A_REJECTED_DATA": "否决：数据不达标",
    "L1A_REJECTED_LEAKAGE": "否决：检测到未来函数",
    "L1A_REJECTED_COVERAGE": "否决：网格覆盖不足",
    "L1A_REJECTED_BOUNDS": "否决：未达收益/回撤边界",
    "L1A_INCONCLUSIVE": "无结论：有指标不可判定",
    "L1A_ERROR": "异常：闸门自身故障",
}

STATUS_LABEL = {"pass": "通过", "fail": "不通过", "undetermined": "未判定", "skipped": "跳过"}


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(payload: Mapping[str, Any], *, title: str | None = None) -> str:
    spec = payload.get("spec") or {}
    verdict = payload.get("verdict", "")
    lines: list[str] = []
    lines.append(f"# {title or 'L1a 向量化粗筛闸门报告'}：{spec.get('title', '(未命名)')}")
    lines.append("")
    lines.append(f"- 结论：**{verdict}**（{VERDICT_LABEL.get(verdict, verdict)}）")
    lines.append(f"- 假设编号：{spec.get('hypothesis_id', '—')}")
    lines.append(f"- 契约摘要：`{(spec.get('spec_digest') or '')[:32]}`")
    lines.append(f"- 策略：`{spec.get('strategy_id', '—')}`｜参数网格 {spec.get('grid_size', '—')} 点")
    windows = f"样本内 {spec.get('in_sample', {}).get('start')}~{spec.get('in_sample', {}).get('end')}"
    windows += f"｜样本外 {spec.get('out_sample', {}).get('start')}~{spec.get('out_sample', {}).get('end')}"
    lines.append(f"- 区间：{windows}")
    lines.append("")

    lines.append("## 一、否决链")
    lines.append("")
    lines.append("| 环节 | 结论 | 说明 |")
    lines.append("| --- | --- | --- |")
    for stage in payload.get("stages") or []:
        lines.append(
            f"| {stage.get('name')} | {STATUS_LABEL.get(str(stage.get('status')), stage.get('status'))} | {stage.get('detail')} |"
        )
    lines.append("")

    data = payload.get("data") or {}
    if data:
        lines.append("## 二、数据门禁")
        lines.append("")
        lines.append(f"行数 {data.get('rows_total')}（样本内 {data.get('rows_in_sample')}），"
                     f"区间 {data.get('period', {}).get('start')}~{data.get('period', {}).get('end')}")
        lines.append("")
        lines.append("| 检查项 | 结论 | 说明 |")
        lines.append("| --- | --- | --- |")
        for check in data.get("checks") or []:
            status = "通过" if check.get("passed") is True else ("不通过" if check.get("passed") is False else "未判定")
            lines.append(f"| `{check.get('code')}` | {status} | {check.get('detail')} |")
        lines.append("")

    leakage = payload.get("leakage") or {}
    if leakage:
        lines.append("## 三、未来函数检测")
        lines.append("")
        lines.append(f"- 动态截断点：测试 {leakage.get('probes_tested')} 个，跳过 {leakage.get('probes_skipped')} 个")
        lines.append(f"- 结论：{'发现泄漏' if leakage.get('leaked') else '未发现'}")
        if leakage.get("dynamic_note"):
            lines.append(f"- 备注：{leakage.get('dynamic_note')}")
        lines.append("")
        findings = (leakage.get("static") or []) + (leakage.get("dynamic") or [])
        if findings:
            lines.append("| 类型 | 说明 | 位置 |")
            lines.append("| --- | --- | --- |")
            for item in findings:
                lines.append(f"| `{item.get('code')}` | {item.get('detail')} | {item.get('location')} |")
            lines.append("")

    sweep = payload.get("sweep") or {}
    if sweep:
        lines.append("## 四、网格扫描与选参")
        lines.append("")
        lines.append(
            f"- 覆盖：{sweep.get('executed_points')}/{sweep.get('declared_points')}"
            f"（失败 {sweep.get('failed_points')} 点）"
        )
        best = sweep.get("best") or {}
        if best:
            lines.append(f"- 样本内最优参数：`{best.get('params')}`（仅用样本内指标选出）")
        lines.append(f"- 样本外评估次数：{sweep.get('oos_evaluations')}（应为 1）")
        lines.append(
            f"- 参数高原（邻域正收益占比）：{_fmt(sweep.get('plateau_ratio'))}"
            f"　{sweep.get('plateau_note') or ''}"
        )
        lines.append("")

    checks = payload.get("bound_checks") or []
    if checks:
        lines.append("## 五、边界判定")
        lines.append("")
        lines.append("| 边界 | 实际 | 要求 | 结论 |")
        lines.append("| --- | --- | --- | --- |")
        for item in checks:
            passed = item.get("passed")
            label = "通过" if passed is True else ("不通过" if passed is False else "未判定")
            lines.append(
                f"| {item.get('name')} | {_fmt(item.get('actual'))} | {item.get('direction')} {_fmt(item.get('threshold'))} | {label} |"
            )
        lines.append("")

    metrics = payload.get("metrics") or {}
    if metrics:
        lines.append("## 六、指标明细（含不可计算项）")
        lines.append("")
        lines.append("| 指标 | 数值 | 状态 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        for name, metric in metrics.items():
            status = "可计算" if metric.get("status") == "ok" else "**不可计算**"
            lines.append(f"| `{name}` | {_fmt(metric.get('value'))} | {status} | {metric.get('reason') or '—'} |")
        lines.append("")

    notes = payload.get("notes") or []
    if notes:
        lines.append("## 七、备注")
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")

    lines.append("## 八、口径说明")
    lines.append("")
    lines.append(
        "- 回测跑在**研究层数据**（复权）上：L1a 不承担撮合保真度（那是 L1b 的职责），"
        "用不复权价会让除权日跳空污染收益序列；交易层数据只用于跨源一致性抽检。"
    )
    lines.append(
        "- 边界值取自假设契约（跑前冻结），不是跑完再填；契约摘要已列在报告头部，"
        "任何改动都会使摘要变化、与报告对不上。"
    )
    lines.append(
        "- 参数点「跑失败」计入未覆盖，不计入通过；指标「不可计算」既不通过也不否决，"
        "而是把结论降级为 `L1A_INCONCLUSIVE`。"
    )
    lines.append("")
    return "\n".join(lines)
