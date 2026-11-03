"""卡片 → 操作序列：切分口径、参数骨架归一、可比较的序列表示。

切分口径为什么重要
------------------
「操作序列」只有在**同一会话内**才有顺序语义。T1 工件里有 ``session`` 字段
（由 ``X-Session-Id`` 头传入），但它是可选的：不传时为空。若直接把空 session
的卡片按操作人拼成一条长序列，任意两天的操作会被拼成一个「会话」，频率与顺序
统计全部失真。

因此本模块采用两级口径，并把口径写进产物（``grouping``），而不是藏在代码里：

- 有 ``session``：按 session 聚合，视为一次连续作业；
- 无 ``session``：按 ``(操作人, 自然日)`` 聚合，记 ``nosession:<operator>:<date>``，
  并在报告里单列这类会话的占比——占比过高说明上游没传会话头，顺序类结论要打折。

参数骨架（skeleton）：为什么不用原始参数
----------------------------------------
回测参数里 ``initial_cash`` 每次都可能不同，直接比参数会把「正常的不同资金量」
报成异常。骨架只保留**结构**：键集合 + 数值的数量级 + 字符串字面量（短的）。
于是 ``initial_cash=100000`` 与 ``120000`` 同骨架（都是 1e5 量级），而
``100000`` 与 ``10000000`` 不同骨架（1e5 vs 1e7）——后者才是「量级可疑」。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: 无会话头时的序列前缀。报告里按该前缀统计「口径缺失」占比。
MISSING_SESSION_PREFIX = "nosession"

#: 骨架递归深度上限：请求体是外部输入，不能假设它浅。
_MAX_DEPTH = 4

#: 保留字面量的字符串长度上限（超过则只记长度，避免工件里出现长文本指纹）。
_MAX_LITERAL = 32

#: 形如 run_id / uuid / 十六进制摘要的字符串：结构固定、取值每次不同。
_ID_LIKE = re.compile(r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})|(?:[0-9a-fA-F]{16,64})")

#: ISO 时间戳字符串（只做形态识别，不校验语义）。
_TS_LIKE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)


def canonical_json(value: Any) -> str:
    """规范化 JSON：同一结构只有一种字节表示（基线摘要的前提）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _num_bucket(value: float) -> str:
    """数值 → 数量级标签（保留符号）。0 单独成桶，避免 log10(0)。"""
    if value == 0:
        return "num:0"
    try:
        exponent = int(math.floor(math.log10(abs(value))))
    except ValueError:  # 极小/极大到 log 溢出的极端值
        return "num:extreme"
    sign = "-" if value < 0 else "+"
    return f"num:{sign}e{exponent}"


def _skeleton(value: Any, depth: int = 0) -> Any:
    if value is None:
        return "null"
    # bool 必须先于 int 判断：Python 里 True 是 int 的子类。
    if isinstance(value, bool):
        return "bool:true" if value else "bool:false"
    if isinstance(value, (int, float)):
        return _num_bucket(float(value))
    if isinstance(value, str):
        # 标识类字符串（run_id / uuid / 时间戳）每次请求都不同，直接当字面量会让
        # 骨架集合爆炸，把「正常的新 id」全报成参数异常。它们是**结构**不是内容，
        # 因此归一成固定标签。
        if _ID_LIKE.fullmatch(value):
            return "str:id"
        if _TS_LIKE.fullmatch(value):
            return "str:timestamp"
        if len(value) <= _MAX_LITERAL:
            return f"str:{value}"
        return f"str:len{len(value)}"
    if depth >= _MAX_DEPTH:
        return f"deep:{type(value).__name__}"
    if isinstance(value, Mapping):
        return {
            str(k): _skeleton(v, depth + 1)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        head = _skeleton(value[0], depth + 1) if value else "empty"
        return {"__list__": len(value), "head": head}
    return f"other:{type(value).__name__}"


def param_skeleton(body: Any, query: Mapping[str, Any] | None = None) -> Any:
    """把一次请求的入参归一成骨架（键集合 + 数量级 + 短字面量）。"""
    payload: dict[str, Any] = {"body": _skeleton(body)}
    if query:
        payload["query"] = _skeleton(dict(query))
    return payload


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Step:
    """序列中的一步（对应一张 T1 工件）。"""

    seq: int
    card_id: str
    action: str
    occurred_at: str
    operator: str
    session: str | None
    status: str | None
    reason_code: str | None
    duration_ms: int | None
    skeleton: Any
    body_digest: str | None
    #: 该步的异常注入类型（真实卡片恒为 ``None``）。基线构建**排除**这些步。
    injected: str | None = None

    @property
    def skeleton_digest(self) -> str:
        return digest_of(self.skeleton)

    @property
    def hour(self) -> int | None:
        parsed = _parse_ts(self.occurred_at)
        return parsed.hour if parsed else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "card_id": self.card_id,
            "action": self.action,
            "occurred_at": self.occurred_at,
            "operator": self.operator,
            "status": self.status,
            "reason_code": self.reason_code,
            "duration_ms": self.duration_ms,
            "skeleton_digest": self.skeleton_digest,
            "injected": self.injected,
        }

    def replace(self, **changes: Any) -> Step:
        data: dict[str, Any] = {
            "seq": self.seq,
            "card_id": self.card_id,
            "action": self.action,
            "occurred_at": self.occurred_at,
            "operator": self.operator,
            "session": self.session,
            "status": self.status,
            "reason_code": self.reason_code,
            "duration_ms": self.duration_ms,
            "skeleton": self.skeleton,
            "body_digest": self.body_digest,
            "injected": self.injected,
        }
        data.update(changes)
        return Step(**data)


@dataclass(frozen=True)
class OperationSequence:
    """一次会话内的连续操作。"""

    sequence_id: str
    operator: str
    session: str | None
    steps: tuple[Step, ...] = field(default_factory=tuple)

    @property
    def pattern(self) -> tuple[str, ...]:
        return tuple(step.action for step in self.steps)

    @property
    def pattern_text(self) -> str:
        return " → ".join(self.pattern)

    @property
    def started_at(self) -> str:
        return self.steps[0].occurred_at if self.steps else ""

    @property
    def ended_at(self) -> str:
        return self.steps[-1].occurred_at if self.steps else ""

    @property
    def operators(self) -> tuple[str, ...]:
        return tuple(sorted({step.operator for step in self.steps}))

    @property
    def injected(self) -> str | None:
        for step in self.steps:
            if step.injected:
                return step.injected
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence_id": self.sequence_id,
            "operator": self.operator,
            "session": self.session,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "pattern": list(self.pattern),
            "steps": [step.to_dict() for step in self.steps],
            "injected": self.injected,
        }

    def replace_steps(self, steps: Sequence[Step]) -> OperationSequence:
        return OperationSequence(
            sequence_id=self.sequence_id,
            operator=self.operator,
            session=self.session,
            steps=tuple(steps),
        )

    def renamed(self, sequence_id: str) -> OperationSequence:
        """换一个序列 id（评估样本用）。

        同一源会话可能被抽中多次，若沿用原标题，两条不同的注入样本会拿到同一个
        ``sequence_id``——报告里无法区分，按 id 汇总判定时还会互相覆盖。
        """
        return OperationSequence(
            sequence_id=sequence_id,
            operator=self.operator,
            session=self.session,
            steps=self.steps,
        )


def step_from_card(card: Mapping[str, Any]) -> Step:
    """单张工件 → Step。字段缺失如实留空（不用空串顶替）。

    T1 中间件的工件把请求体放在 ``basis.request.body``，``inputs`` 里只有摘要与
    字节数（这是有意的：``inputs`` 用来放"业务输入"，请求体属于"依据"的一部分）。
    早期版本按 ``inputs.body`` 取，结果骨架恒为空、请求体摘要恒为 ``None``——
    于是「重复提交」与「参数漂移」两条规则永远不可能命中，而报告会显示「未发现」。
    """
    inputs = card.get("inputs") or {}
    basis = card.get("basis") or {}
    request = basis.get("request") if isinstance(basis, Mapping) else None
    request = request if isinstance(request, Mapping) else {}
    body = request.get("body")
    query = request.get("query")
    outcome = card.get("outcome") or {}
    status = outcome.get("status") if isinstance(outcome, Mapping) else None
    reason = outcome.get("reason_code") if isinstance(outcome, Mapping) else None
    duration = outcome.get("duration_ms") if isinstance(outcome, Mapping) else None
    session = card.get("session")

    body_digest = request.get("body_sha256")
    if not body_digest and isinstance(inputs, Mapping):
        body_digest = inputs.get("body_sha256")
    if not body_digest and body is not None:
        # 有些写入路径（业务侧 record_operation）只留请求体、不留摘要；
        # 此时自己算：否则「连续重复提交」这条规则会在这些卡上永久失效。
        body_digest = digest_of(body)

    if body is not None:
        skeleton = param_skeleton(body, query if isinstance(query, Mapping) else None)
    elif body_digest:
        # 请求体超过记录上限时工件只留摘要：此时**不能**把骨架当作空体，
        # 否则所有大请求会被归成同一种形态，「参数漂移」规则将永久失效。
        skeleton = {"body": "digest_only"}
    else:
        skeleton = param_skeleton(None, query if isinstance(query, Mapping) else None)

    return Step(
        seq=int(card.get("seq") or 0),
        card_id=str(card.get("card_id") or ""),
        action=str(card.get("action") or ""),
        occurred_at=str(card.get("occurred_at") or ""),
        operator=str(card.get("operator") or "anonymous"),
        session=str(session) if session else None,
        status=str(status) if status is not None else None,
        reason_code=str(reason) if reason is not None else None,
        duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
        skeleton=skeleton,
        body_digest=str(body_digest) if body_digest else None,
    )


def _day_of(step: Step) -> str:
    return step.occurred_at[:10] if len(step.occurred_at) >= 10 else "unknown"


def sequences_from_cards(cards: Iterable[Mapping[str, Any]]) -> list[OperationSequence]:
    """按「会话优先、否则 (操作人,自然日)」聚合卡片为序列。

    输入顺序无关：内部按链上 ``seq`` 升序排列，避免调用方传错顺序导致
    「顺序类规则」把正常操作报成乱序。
    """
    steps = sorted((step_from_card(card) for card in cards), key=lambda item: item.seq)
    buckets: dict[str, list[Step]] = {}
    meta: dict[str, tuple[str, str | None]] = {}
    for step in steps:
        if step.session:
            key = f"session:{step.session}"
            session_value: str | None = step.session
        else:
            key = f"{MISSING_SESSION_PREFIX}:{step.operator}:{_day_of(step)}"
            session_value = None
        buckets.setdefault(key, []).append(step)
        meta.setdefault(key, (step.operator, session_value))

    sequences: list[OperationSequence] = []
    for key, bucket in buckets.items():
        operator, session_value = meta[key]
        sequences.append(
            OperationSequence(
                sequence_id=key,
                operator=operator,
                session=session_value,
                steps=tuple(bucket),
            )
        )
    sequences.sort(key=lambda seq: (seq.steps[0].seq if seq.steps else 0))
    return sequences


def load_sequences(root: Any) -> list[OperationSequence]:
    """按审计根目录加载序列。目录不存在／无卡片时抛 ``FileNotFoundError``。

    空库必须报错而不是返回空列表：空序列集合会让基线看起来「构建成功但零样本」，
    随后所有规则静默停用，最后报告「未发现异常」——这正是 T6 要避免的假阴性。
    """
    from deepquant.audit.store import open_store

    path = Path(root)
    if not path.exists():
        raise FileNotFoundError(f"审计根目录不存在：{path}")
    store = open_store(path)
    if store is None:
        raise FileNotFoundError(f"审计根目录不可用：{path}")
    cards = list(store.iter_all(ascending=True))
    if not cards:
        raise FileNotFoundError(f"审计根目录内没有任何工件：{path}")
    return sequences_from_cards(cards)
