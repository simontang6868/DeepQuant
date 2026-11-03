"""质检基线：把「正常长什么样」写成可复现的结构，而不是一句经验判断。

基线包含什么（对应《推进计划》T6 的「规则提取：频率 / 顺序 / 条件模式」）
--------------------------------------------------------------------
- **频率**：操作 1/2/3-gram 的会话支持数、高频序列 Top-N（基线可复现的对象）；
- **顺序**：相邻操作的转移计数（谁后面通常跟谁）；
- **条件模式**：每个操作的状态分布、原因码分布、参数骨架集合、时长剖面；
- **上下文**：操作人集合、出现小时集合、拒绝率。

可复现怎么保证
--------------
1. 一切计数用 ``sort_keys`` 的规范化 JSON 摘要，摘要**不含**生成时间、路径、
   机器名——这些变了不代表基线变了；
2. 排序键全部是「计数降序 + 字典序」，杜绝并列项因字典序随机而抖动；
3. 输入序列按链上 ``seq`` 排序后再统计，与调用方传入顺序无关。

``baseline_digest`` 是「同一语料 → 同一摘要」的判据。两次构建摘要不同，
说明基线不是语料的函数（有隐藏状态），此时**不应**拿它做质检基准。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from deepquant.shadow.sequences import (
    MISSING_SESSION_PREFIX,
    OperationSequence,
    canonical_json,
    digest_of,
)

BASELINE_VERSION = "t6-baseline-v1"

#: 高频序列榜的长度（验收标准要求 Top 10 可复现）。
TOP_N = 10

#: 参与统计的 n-gram 阶数上限。
MAX_NGRAM = 3

#: 视为「一次作业有终态」的操作：会在系统里留下可核对的产出。
TERMINAL_OPS = frozenset({"backtest.run", "backtest.compare", "portfolio.backtest"})


def _percentile(values: Sequence[float], q: float) -> float | None:
    """nearest-rank 分位数（与 T3 延迟档案同口径，便于跨模块对照）。

    n<100 时 p99 会等于最大值——这是如实反映样本不足，而不是插值抹平。
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round(q * len(ordered) + 0.5))))
    return float(ordered[rank - 1])


def _sorted_counts(counter: Counter[Any]) -> list[list[Any]]:
    """计数 → ``[[key, count], ...]``，按「计数降序 + 键字典序」稳定排序。"""
    return [[key, count] for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))]


def _sequence_pattern(sequence: OperationSequence) -> tuple[str, ...]:
    return sequence.pattern


def _ngrams(pattern: Sequence[str], n: int) -> Iterable[tuple[str, ...]]:
    for index in range(len(pattern) - n + 1):
        yield tuple(pattern[index : index + n])


def build_baseline(
    sequences: Iterable[OperationSequence],
    *,
    top_n: int = TOP_N,
    max_ngram: int = MAX_NGRAM,
) -> dict[str, Any]:
    """从序列集合构建基线。返回可直接 JSON 序列化的字典。

    含注入标记的序列会被整条排除（并计入 ``excluded_injected``）：评估用的
    扰动序列若混进基线，基线就会把「异常」学成「正常」。
    """
    usable = [seq for seq in sequences if seq.steps and seq.injected is None]
    excluded = [seq for seq in sequences if seq.steps and seq.injected is not None]

    op_counter: Counter[str] = Counter()
    status_counter: dict[str, Counter[str]] = defaultdict(Counter)
    reason_counter: dict[str, Counter[str]] = defaultdict(Counter)
    duration_values: dict[str, list[float]] = defaultdict(list)
    skeleton_sets: dict[str, set[str]] = defaultdict(set)
    transitions: Counter[tuple[str, str]] = Counter()
    ngram_counters: dict[int, Counter[tuple[str, ...]]] = {n: Counter() for n in range(1, max_ngram + 1)}
    ngram_sessions: dict[int, Counter[tuple[str, ...]]] = {
        n: Counter() for n in range(1, max_ngram + 1)
    }
    pattern_counter: Counter[tuple[str, ...]] = Counter()
    operators: Counter[str] = Counter()
    hours: Counter[int] = Counter()
    session_based = 0
    nosession_based = 0
    step_total = 0
    rejected_total = 0
    terminal_sessions = 0

    for sequence in usable:
        pattern = _sequence_pattern(sequence)
        pattern_counter[pattern] += 1
        if any(action in TERMINAL_OPS for action in pattern):
            terminal_sessions += 1
        if sequence.sequence_id.startswith(f"{MISSING_SESSION_PREFIX}:"):
            nosession_based += 1
        else:
            session_based += 1
        for n in range(1, max_ngram + 1):
            seen = set(_ngrams(pattern, n))
            for gram in seen:
                ngram_sessions[n][gram] += 1
            for gram in _ngrams(pattern, n):
                ngram_counters[n][gram] += 1
        for left, right in zip(pattern, pattern[1:]):
            transitions[(left, right)] += 1
        for step in sequence.steps:
            step_total += 1
            op_counter[step.action] += 1
            status_counter[step.action][step.status or "unknown"] += 1
            if step.reason_code:
                reason_counter[step.action][step.reason_code] += 1
            if step.duration_ms is not None:
                duration_values[step.action].append(float(step.duration_ms))
            skeleton_sets[step.action].add(step.skeleton_digest)
            operators[step.operator] += 1
            if step.hour is not None:
                hours[step.hour] += 1
            if step.status == "rejected":
                rejected_total += 1

    operations: dict[str, Any] = {}
    for action, count in op_counter.most_common():
        durations = duration_values.get(action, [])
        operations[action] = {
            "count": count,
            "statuses": {key: value for key, value in _sorted_counts(status_counter[action])},
            "reason_codes": {key: value for key, value in _sorted_counts(reason_counter[action])},
            "duration_ms": {
                "p50": _percentile(durations, 0.50),
                "p90": _percentile(durations, 0.90),
                "p99": _percentile(durations, 0.99),
                "max": max(durations) if durations else None,
                "samples": len(durations),
            },
            # 只存摘要，不存骨架原文：骨架内含字符串字面量，原文会让基线体积随
            # 参数多样性线性膨胀，而判定只需要"是否见过"。
            "skeleton_digests": sorted(skeleton_sets.get(action, set())),
            "skeleton_count": len(skeleton_sets.get(action, set())),
        }

    top_sequences = [
        {"pattern": list(pattern), "sessions": count}
        for pattern, count in sorted(pattern_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    ]

    ngram_block: dict[str, Any] = {}
    for n in range(1, max_ngram + 1):
        entries = [
            {
                "pattern": list(gram),
                "occurrences": occurrences,
                "sessions": ngram_sessions[n][gram],
            }
            for gram, occurrences in sorted(
                ngram_counters[n].items(), key=lambda kv: (-kv[1], kv[0])
            )
        ]
        ngram_block[str(n)] = {
            "distinct": len(entries),
            "entries": entries,
        }

    hour_values = sorted(hours)
    return {
        "baseline_version": BASELINE_VERSION,
        "corpus": {
            "sequences": len(usable),
            "excluded_injected": len(excluded),
            "steps": step_total,
            "operators": sorted(operators),
            "grouping": {
                "session_based": session_based,
                "nosession_based": nosession_based,
            },
            "distinct_patterns": len(pattern_counter),
            # 带终态操作的会话占比：规则「无终态产出」的启用依据。
            # 若多数会话本来就只看不跑，这条规则会把正常浏览报成异常。
            "terminal_session_rate": (terminal_sessions / len(usable)) if usable else 0.0,
            "time_range": {
                "start": min((seq.started_at for seq in usable), default=None),
                "end": max((seq.ended_at for seq in usable), default=None),
            },
            "corpus_digest": digest_of(
                sorted(
                    [
                        [seq.sequence_id, list(seq.pattern), [[s.seq, s.action, s.status] for s in seq.steps]]
                        for seq in usable
                    ]
                )
            ),
        },
        "operations": operations,
        "top_sequences": top_sequences,
        "ngrams": ngram_block,
        "transitions": [
            {"from": left, "to": right, "count": count}
            for (left, right), count in sorted(
                transitions.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ],
        "operators": {key: value for key, value in _sorted_counts(operators)},
        "hours": {
            "observed": hour_values,
            "min": hour_values[0] if hour_values else None,
            "max": hour_values[-1] if hour_values else None,
            "span": (hour_values[-1] - hour_values[0]) if hour_values else 0,
        },
        "reject_rate": (rejected_total / step_total) if step_total else 0.0,
    }


def baseline_digest(baseline: dict[str, Any]) -> str:
    """基线的内容摘要（不含生成时间与摘要字段本身，保证「同语料 → 同摘要」）。

    必须把 ``baseline_digest`` 自己也排除：否则摘要会覆盖「摘要字段」，
    ``save`` 写进去的值与 ``load`` 重算的值永远差一轮，校验必然失败。
    """
    content = {
        key: value
        for key, value in baseline.items()
        if key not in {"generated_at", "baseline_digest", "note"}
    }
    return digest_of(content)


def save_baseline(path: str | Path, baseline: dict[str, Any], *, note: str = "") -> Path:
    """落盘基线。写入前补 ``generated_at`` 与 ``baseline_digest``，并显式用 LF。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(baseline)
    payload["generated_at"] = datetime.now(UTC).isoformat()
    if note:
        payload["note"] = note
    payload["baseline_digest"] = baseline_digest(payload)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    # Windows 文本模式会把 \n 写成 \r\n；基线是比对对象，必须显式 LF。
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text + "\n")
    return target


def load_baseline(path: str | Path) -> dict[str, Any]:
    """读基线，并核对摘要与版本（版本不符直接报错，不做兼容猜测）。"""
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("baseline_version") != BASELINE_VERSION:
        raise ValueError(
            f"基线版本不符：文件 {payload.get('baseline_version')!r} / 期望 {BASELINE_VERSION!r}"
        )
    recorded = payload.get("baseline_digest")
    if recorded and recorded != baseline_digest(payload):
        raise ValueError("基线内容摘要校验失败：文件被改动过，不能作为质检基准")
    return payload


def transition_support(baseline: dict[str, Any]) -> dict[tuple[str, str], int]:
    """转移支持度字典（供规则模块 O(1) 查询）。"""
    return {(entry["from"], entry["to"]): int(entry["count"]) for entry in baseline["transitions"]}


def skeleton_index(baseline: dict[str, Any]) -> dict[str, set[str]]:
    """每个操作见过的参数骨架摘要集合。"""
    return {
        action: set(spec.get("skeleton_digests") or [])
        for action, spec in (baseline.get("operations") or {}).items()
    }


def top_patterns(baseline: dict[str, Any], *, limit: int = TOP_N) -> list[list[str]]:
    return [entry["pattern"] for entry in baseline.get("top_sequences", [])[:limit]]
