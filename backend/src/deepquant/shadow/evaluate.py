"""评估：注入已知异常，量出「报告能不能抓到」——而不是宣称「能抓到」。

为什么不用「人工看一眼觉得对」
------------------------------
《推进计划》T6 的验收标准写的是「对随机抽取的异常操作，审计报告命中率 ≥ 80%」。
这句话只有先定义「异常」与「命中」才有意义。本模块的定义：

- **异常样本**：对留出会话做**有语义的扰动**（换顺序、删步骤、伪造拒绝、量级漂移…），
  每类扰动即一类异常，标签由注入器写下，检测侧读不到；
- **命中**：该会话在报告里被标记（任意规则），并额外统计「命中了该类异常专属规则」
  的严格口径。两个数字都报，避免只报对自己有利的那个；
- **不误报**：留出会话原样再过一遍 → 误报率；另设「合法变化」硬负例
  （换成本单位内的操作人、缩短会话）→ 若被报，说明规则把正常变异当异常。

注入发生在**序列层**，不写回工件库
----------------------------------
改写卡片会破坏 T1 的哈希链。破坏链本身是篡改证据，不是业务异常——把它混进
「异常样本」会让评估结论失去意义。因此注入只作用于内存中的序列副本，
并在报告里标注 ``injected``，真实语料永不被污染。
"""

from __future__ import annotations

import copy
import random
from typing import Any, Callable, Iterable, Sequence

from deepquant.shadow.baseline import TERMINAL_OPS
from deepquant.shadow.rules import audit_sequences
from deepquant.shadow.sequences import OperationSequence, Step, digest_of

#: 每类异常的默认样本数（累计 8 类 × 15 = 120 个异常样本）。
DEFAULT_PER_TYPE = 15

#: 固定随机种子：注入可复现，别人用同一语料能得到同一批样本。
DEFAULT_SEED = 20261027

#: 注入类型 → 期望命中的规则码（严格口径）。一个扰动可能被多条规则同时抓到，
#: 因此用集合而非单值。
EXPECTED_CODES: dict[str, frozenset[str]] = {
    "swap_order": frozenset({"step_order_unseen"}),
    "drop_step": frozenset({"step_order_unseen"}),
    "insert_rare_op": frozenset({"step_order_unseen", "param_skeleton_unseen", "status_unseen"}),
    "status_flip": frozenset({"status_unseen", "dangling_reject"}),
    "reject_burst": frozenset({"reject_storm", "status_unseen", "dangling_reject"}),
    "param_drift": frozenset({"param_skeleton_unseen"}),
    "operator_swap": frozenset({"operator_unknown", "session_multi_operator"}),
    "duration_spike": frozenset({"duration_outlier"}),
}

#: 硬负例（合法变化）→ 说明。这些**不应**被报成高危。
HARD_NEGATIVES: dict[str, str] = {
    "known_operator": "改用基线中存在的另一名操作人（应放行）",
    "short_session": "截断为 2 步（浏览型会话，流程不完整属正常）",
}


def _tagged(sequence: OperationSequence, steps: Sequence[Step], kind: str) -> OperationSequence:
    """给评估样本换新 id 并打标，避免与真实会话在同一份报告里串台。"""
    marked = tuple(step.replace(injected=kind) for step in steps)
    return OperationSequence(
        sequence_id=f"eval:{kind}:{sequence.sequence_id}",
        operator=sequence.operator,
        session=None,
        steps=marked,
    )


def _middle_indices(size: int) -> list[int]:
    """可安全改动的中段下标（避开首步，保留「作业开头」语义）。"""
    return list(range(1, max(1, size - 1))) or [0]


def inject_swap_order(sequence: OperationSequence, rng: random.Random) -> OperationSequence | None:
    """交换相邻两步：制造基线中不存在的跳转。"""
    if len(sequence.steps) < 3:
        return None
    index = rng.choice(_middle_indices(len(sequence.steps)))
    steps = list(sequence.steps)
    steps[index - 1], steps[index] = steps[index], steps[index - 1]
    return _tagged(sequence, steps, "swap_order")


def inject_drop_step(sequence: OperationSequence, rng: random.Random) -> OperationSequence | None:
    """删掉中段一步：模拟漏做前置动作。"""
    if len(sequence.steps) < 3:
        return None
    index = rng.choice(_middle_indices(len(sequence.steps)))
    steps = [step for position, step in enumerate(sequence.steps) if position != index]
    return _tagged(sequence, steps, "drop_step")


def inject_insert_rare_op(
    sequence: OperationSequence, rng: random.Random, baseline: dict[str, Any]
) -> OperationSequence | None:
    """插入一个本会话没做过的操作：模拟串流程/误入分支。"""
    operations: dict[str, Any] = baseline.get("operations") or {}
    candidates = [
        action
        for action, spec in sorted(operations.items(), key=lambda kv: int(kv[1].get("count") or 0))
        if action not in sequence.pattern
    ]
    if not candidates:
        return None
    action = candidates[0]
    index = rng.choice(range(1, len(sequence.steps) + 1))
    anchor = sequence.steps[min(index, len(sequence.steps) - 1)]
    synthetic = Step(
        seq=anchor.seq,
        # 合成步骤不是真实工件，id 用显式前缀区分：报告里能一眼看出哪些定位
        # 指向真实卡片、哪些指向评估时凭空插入的步骤。
        card_id=f"injected:synthetic:{digest_of(['insert', action, sequence.sequence_id])[:16]}",
        action=action,
        occurred_at=anchor.occurred_at,
        operator=anchor.operator,
        session=None,
        status="ok",
        reason_code=None,
        duration_ms=anchor.duration_ms,
        skeleton={"body": "null"},
        body_digest=None,
        injected="insert_rare_op",
    )
    steps = list(sequence.steps)
    steps.insert(index, synthetic)
    return _tagged(sequence, steps, "insert_rare_op")


def inject_status_flip(sequence: OperationSequence, rng: random.Random) -> OperationSequence | None:
    """把一步改成被拒绝：模拟风控拦截/参数非法后的失败操作。"""
    if not sequence.steps:
        return None
    index = rng.randrange(len(sequence.steps))
    steps = list(sequence.steps)
    steps[index] = steps[index].replace(status="rejected", reason_code="risk_limit_exceeded")
    return _tagged(sequence, steps, "status_flip")


def inject_reject_burst(sequence: OperationSequence, rng: random.Random) -> OperationSequence | None:
    """连续 3 步被拒绝：模拟重试风暴。"""
    if len(sequence.steps) < 3:
        return None
    start = rng.randrange(0, len(sequence.steps) - 2)
    steps = list(sequence.steps)
    for offset in range(3):
        steps[start + offset] = steps[start + offset].replace(
            status="rejected", reason_code="risk_limit_exceeded"
        )
    return _tagged(sequence, steps, "reject_burst")


def inject_param_drift(sequence: OperationSequence, rng: random.Random) -> OperationSequence | None:
    """把参数数量级抬高两位：模拟资金量/阈值填错。"""
    candidates = [i for i, step in enumerate(sequence.steps) if _has_numeric_bucket(step.skeleton)]
    if not candidates:
        return None
    index = rng.choice(candidates)
    steps = list(sequence.steps)
    steps[index] = steps[index].replace(skeleton=_drift(sequence.steps[index].skeleton))
    return _tagged(sequence, steps, "param_drift")


def _has_numeric_bucket(node: Any) -> bool:
    if isinstance(node, str):
        return node.startswith("num:")
    if isinstance(node, dict):
        return any(_has_numeric_bucket(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_numeric_bucket(value) for value in node)
    return False


def _drift(node: Any) -> Any:
    """把第一个数值桶抬高两个数量级（只改一个，保持其余结构不变）。"""
    if isinstance(node, str):
        if node.startswith("num:") and node != "num:0" and node != "num:extreme":
            try:
                exponent = int(node.split("e", 1)[1])
            except (IndexError, ValueError):
                return node
            return f"num:+e{exponent + 2}"
        return node
    if isinstance(node, dict):
        drifted = False
        result: dict[str, Any] = {}
        for key, value in node.items():
            if not drifted:
                new_value = _drift(value)
                if new_value != value:
                    drifted = True
                    result[key] = new_value
                    continue
            result[key] = value
        return result
    if isinstance(node, list):
        for position, value in enumerate(node):
            new_value = _drift(value)
            if new_value != value:
                clone = list(node)
                clone[position] = new_value
                return clone
    return node


def inject_operator_swap(sequence: OperationSequence, rng: random.Random) -> OperationSequence | None:
    """把某一步换成陌生操作人：模拟账号来源不明或凭证转借。"""
    if not sequence.steps:
        return None
    index = rng.randrange(len(sequence.steps))
    steps = list(sequence.steps)
    steps[index] = steps[index].replace(operator="outsider")
    return _tagged(sequence, steps, "operator_swap")


def inject_duration_spike(sequence: OperationSequence, rng: random.Random) -> OperationSequence | None:
    """把某一步耗时放大 50 倍：模拟下游劣化。"""
    candidates = [i for i, step in enumerate(sequence.steps) if step.duration_ms]
    if not candidates:
        return None
    index = rng.choice(candidates)
    steps = list(sequence.steps)
    steps[index] = steps[index].replace(duration_ms=int(steps[index].duration_ms or 0) * 50 + 1000)
    return _tagged(sequence, steps, "duration_spike")


INJECTORS: dict[str, Callable[..., OperationSequence | None]] = {
    "swap_order": inject_swap_order,
    "drop_step": inject_drop_step,
    "insert_rare_op": inject_insert_rare_op,
    "status_flip": inject_status_flip,
    "reject_burst": inject_reject_burst,
    "param_drift": inject_param_drift,
    "operator_swap": inject_operator_swap,
    "duration_spike": inject_duration_spike,
}


def hard_negative_known_operator(
    sequence: OperationSequence, rng: random.Random, baseline: dict[str, Any]
) -> OperationSequence | None:
    """换成基线里的另一名操作人：合法变化，不应被报。"""
    operators = [name for name in (baseline.get("operators") or {}) if name != sequence.operator]
    if not operators:
        return None
    replacement = operators[0]
    steps = [step.replace(operator=replacement) for step in sequence.steps]
    return _tagged(sequence, steps, "known_operator")


def hard_negative_short_session(
    sequence: OperationSequence, rng: random.Random, baseline: dict[str, Any]
) -> OperationSequence | None:
    """截断为 2 步：浏览型会话本来就短，不应被当异常。

    只截断「前两步都成功」的会话：若第 2 步恰好是被拒绝的操作，那么截断后的
    序列确实是「以拒绝收尾、没有后续」——报告标记它是对的，不构成误报。
    把这类样本算进硬负例，等于要求检测器放过一个真实问题。
    """
    if len(sequence.steps) <= 2:
        return None
    if sequence.steps[1].status == "rejected":
        return None
    return _tagged(sequence, sequence.steps[:2], "short_session")


HARD_NEGATIVE_BUILDERS: dict[str, Callable[..., OperationSequence | None]] = {
    "known_operator": hard_negative_known_operator,
    "short_session": hard_negative_short_session,
}


def wilson_interval(successes: int, total: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson 区间（小样本比正态近似稳，n=15 时尤其明显）。"""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5)
    return (max(0.0, (centre - margin) / denominator), min(1.0, (centre + margin) / denominator))


def _flag_map(report: dict[str, Any]) -> dict[str, tuple[bool, frozenset[str]]]:
    return {
        item["sequence_id"]: (True, frozenset(item.get("codes") or []))
        for item in report.get("findings") or []
    }


def _score(
    sequences: list[OperationSequence],
    baseline: dict[str, Any],
    *,
    expected: frozenset[str] | None,
) -> dict[str, Any]:
    report = audit_sequences(sequences, baseline)
    flags = _flag_map(report)
    flagged = 0
    matched = 0
    per_sequence: list[dict[str, Any]] = []
    for sequence in sequences:
        found, codes = flags.get(sequence.sequence_id, (False, frozenset()))
        flagged += 1 if found else 0
        hit_expected = bool(expected & codes) if expected else False
        matched += 1 if hit_expected else 0
        per_sequence.append(
            {
                "sequence_id": sequence.sequence_id,
                "flagged": found,
                "codes": sorted(codes),
                "expected_matched": hit_expected,
            }
        )
    total = len(sequences)
    return {
        "total": total,
        "flagged": flagged,
        "flagged_rate": (flagged / total) if total else 0.0,
        "expected_matched": matched,
        "expected_matched_rate": (matched / total) if total else 0.0,
        "flagged_ci": list(wilson_interval(flagged, total)),
        "per_sequence": per_sequence,
    }


def collect_injected(
    baseline: dict[str, Any],
    holdout: Sequence[OperationSequence],
    *,
    per_type: int = DEFAULT_PER_TYPE,
    seed: int = DEFAULT_SEED,
) -> tuple[dict[str, list[OperationSequence]], dict[str, int]]:
    """生成带标签的注入样本（每类 ``per_type`` 个），返回 ``(样本, 空操作次数)``。

    空操作（注入后序列签名完全没变）会被重抽并计数：检测器抓不到"没发生的异常"
    是应该的，把它算作未命中会低估检测器。
    """
    rng = random.Random(seed)
    clean = [sequence for sequence in holdout if sequence.steps]
    if not clean:
        raise ValueError("留出集为空：无法生成注入样本（不得用训练集自评）")

    samples: dict[str, list[OperationSequence]] = {}
    noop_counts: dict[str, int] = {}
    for kind, injector in INJECTORS.items():
        produced: list[OperationSequence] = []
        attempts = 0
        noops = 0
        while len(produced) < per_type and attempts < per_type * 20:
            attempts += 1
            source = rng.choice(clean)
            mutated = (
                injector(source, rng, baseline)
                if _takes_baseline(injector)
                else injector(source, rng)
            )
            if mutated is None:
                continue
            if NOOP_BASIS.get(kind) == "pattern":
                unchanged = mutated.pattern == source.pattern
            else:
                unchanged = _signature(mutated) == _signature(source)
            if unchanged:
                noops += 1
                continue
            # 加序号：同一源会话被抽中多次时，标题必须可区分
            produced.append(mutated.renamed(f"{mutated.sequence_id}#{len(produced) + 1}"))
        samples[kind] = produced
        noop_counts[kind] = noops
    return samples, noop_counts


def evaluate(
    baseline: dict[str, Any],
    holdout: Iterable[OperationSequence],
    *,
    per_type: int = DEFAULT_PER_TYPE,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """在留出会话上量化命中率与误报率。返回可直接写进验收证据的字典。"""
    rng = random.Random(seed)
    clean = [sequence for sequence in holdout if sequence.steps]
    if not clean:
        raise ValueError("留出集为空：无法评估命中率（不得用训练集自评）")

    # 1) 干净留出集 → 误报率（未注入前报告已经报了多少）
    clean_result = _score(clean, baseline, expected=frozenset())

    # 2) 注入异常 → 命中率
    by_type: dict[str, Any] = {}
    all_flagged = 0
    all_matched = 0
    all_total = 0
    samples: list[dict[str, Any]] = []
    injected, noop_counts = collect_injected(baseline, clean, per_type=per_type, seed=seed)
    for kind in INJECTORS:
        produced = injected.get(kind) or []
        if not produced:
            by_type[kind] = {"total": 0, "skipped": "该注入器在留出集上没有可用样本"}
            continue
        result = _score(produced, baseline, expected=EXPECTED_CODES[kind])
        by_type[kind] = {
            "total": result["total"],
            "flagged": result["flagged"],
            "flagged_rate": result["flagged_rate"],
            "flagged_ci": result["flagged_ci"],
            "expected_matched": result["expected_matched"],
            "expected_matched_rate": result["expected_matched_rate"],
            "source_examples": [item["sequence_id"] for item in result["per_sequence"][:3]],
        }
        all_flagged += result["flagged"]
        all_matched += result["expected_matched"]
        all_total += result["total"]
        samples.extend(result["per_sequence"])

    # 3) 硬负例 → 合法变化是否被误报
    negatives: dict[str, Any] = {}
    for kind, builder in HARD_NEGATIVE_BUILDERS.items():
        produced = []
        for sequence in clean[: max(1, per_type)]:
            candidate = builder(sequence, rng, baseline)
            if candidate is not None:
                produced.append(candidate)
        if not produced:
            negatives[kind] = {"total": 0, "note": HARD_NEGATIVES[kind]}
            continue
        result = _score(produced, baseline, expected=frozenset())
        negatives[kind] = {
            "total": result["total"],
            "flagged": result["flagged"],
            "flagged_rate": result["flagged_rate"],
            "note": HARD_NEGATIVES[kind],
        }

    overall_ci = wilson_interval(all_flagged, all_total)
    strict_ci = wilson_interval(all_matched, all_total)
    return {
        "evaluation_version": "t6-eval-v1",
        "seed": seed,
        "per_type": per_type,
        "holdout_sequences": len(clean),
        "clean": {
            "flagged_rate": clean_result["flagged_rate"],
            "flagged": clean_result["flagged"],
            "total": clean_result["total"],
            "ci": clean_result["flagged_ci"],
        },
        "anomalies": {
            "total": all_total,
            "flagged": all_flagged,
            "hit_rate": (all_flagged / all_total) if all_total else 0.0,
            "hit_rate_ci": list(overall_ci),
            "strict_matched": all_matched,
            "strict_rate": (all_matched / all_total) if all_total else 0.0,
            "strict_rate_ci": list(strict_ci),
            "by_type": by_type,
            "noop_injections": noop_counts,
        },
        "hard_negatives": negatives,
        "misclassified": [item for item in samples if not item["flagged"]],
    }


#: 「空操作」的判定基准。顺序类扰动只要**操作顺序没变**就是空操作——
#: 交换两个只有参数不同的同名步骤，从日志的流程视角看什么都没发生，
#: 把它算作「未命中」等于让检测器去抓一个不存在的顺序异常。
NOOP_BASIS: dict[str, str] = {
    "swap_order": "pattern",
    "drop_step": "pattern",
}


def _takes_baseline(func: Callable[..., Any]) -> bool:
    import inspect

    return "baseline" in inspect.signature(func).parameters


def _signature(sequence: OperationSequence) -> tuple[Any, ...]:
    """序列的可观测签名：用于识别「注入后其实什么都没变」的空操作。"""
    return tuple(
        (
            step.action,
            step.operator,
            step.status,
            step.reason_code,
            step.duration_ms,
            step.skeleton_digest,
            step.body_digest,
        )
        for step in sequence.steps
    )


def clone_sequences(sequences: Iterable[OperationSequence]) -> list[OperationSequence]:
    """深拷贝序列（注入器不得改动原对象，否则干净集会被污染）。"""
    return [copy.deepcopy(sequence) for sequence in sequences]
