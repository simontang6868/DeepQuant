"""审计工件（run card）：结构定义、校验、脱敏与内容摘要。

为什么需要这一层
----------------
现有链路已经能回答「这次回测算出了什么」（``performance.serialize``）、
「结果有没有被改过」（``window_records_integrity``）与「输入能否识别」
（``performance.reproducibility``），但回答不了 T1 要回答的那个问题：

    谁、在什么时间、依据哪条规则、做了什么操作、结果如何、有什么注意事项。

前四项在既有模块里是**散落的**（操作人只出现在日志文本里，依据规则只写在
代码注释里），第五项「注意事项」几乎无处安放——而它恰恰是本研究线最常被
引用、也最容易被丢掉的部分（引用结论必带限定）。本模块把六件事固化成一张
不可改写的卡片，字段缺失直接报错，而不是写成空字符串糊过去。

与既有摘要的分工（不得互相替代）
--------------------------------
- ``dataset_summary.digest``：输入**数据内容**的摘要；
- ``result_digest``：回测**结果文档**的摘要；
- ``card_sha256``：**这张卡片**（含上述两者的引用值）的摘要，用于哈希链。

三者语义不同，卡片里以不同字段名分列，禁止用一个顶替另一个。

五元组之外为什么还要别的字段
----------------------------
``action`` / ``subject`` 是检索维度（按操作类型、按标的回放）；
``prev_sha256`` 是链式防篡改的锚点；``seq`` 保证顺序可判定。
它们都不是「业务含义」，而是让五元组**可回放、可验证**所必需的结构。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence

#: 工件结构版本。字段增删改必须升版本，便于历史工件按各自版本解读。
CARD_VERSION = "run-card-v1"

#: 哈希链首卡的 ``prev_sha256``。用全零而非空串：空串与「字段缺失」难以区分。
GENESIS_SHA256 = "0" * 64

#: 脱敏后的占位符。刻意显眼，避免被误读为真实值。
REDACTED = "***REDACTED***"

#: T1 五元组字段名（缺任一项即为不合格工件，不允许用空值顶替）。
REQUIRED_FIELDS: tuple[str, ...] = (
    "operator",  # 操作人
    "occurred_at",  # 时间
    "basis",  # 依据规则
    "outcome",  # 执行结果
    "caveats",  # 注意事项
)

#: 敏感键名模式。命中即整体替换为 ``REDACTED``，只记录**字段路径**不记录值。
#: 覆盖口令/令牌/密钥/凭证/会话/身份与联系类信息（后者对应个人信息保护要求）。
_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|credential|cookie|authorization|auth[_-]?header|session[_-]?id"
    r"|id[_-]?card|bank[_-]?card|card[_-]?no|account[_-]?no|phone|mobile|email)",
    re.IGNORECASE,
)

#: 值里出现这些前缀说明是明文凭证（即使键名没命中），按值脱敏。
_SENSITIVE_VALUE_PREFIXES: tuple[str, ...] = (
    "bearer ",
    "basic ",
    "ghp_",
    "gho_",
    "sk-",
    "akia",
    "-----begin",
)


class AuditSchemaError(ValueError):
    """工件不符合 T1 结构要求（缺字段、类型错误、时间不可解析等）。"""


@dataclass(frozen=True)
class CompletenessReport:
    """五元组完整率报告。

    ``ratio`` 是「五元组齐全的卡片数 / 卡片总数」。刻意按**卡片**而不是按字段
    统计：一张卡缺一个字段就是一张不可回放的卡，用字段数做分母会让缺一个字段
    的卡片看起来仍有 80% 完整度。
    """

    n_cards: int
    n_complete: int
    ratio: float
    incomplete: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_cards": self.n_cards,
            "n_complete": self.n_complete,
            "ratio": self.ratio,
            "incomplete": list(self.incomplete),
        }


# --------------------------------------------------------------------------- #
# 规范化与摘要
# --------------------------------------------------------------------------- #


def canonical_json(value: Any) -> str:
    """规范化 JSON 文本：键排序、无多余空白、保留中文。

    摘要必须建立在**规范化文本**上，否则同一份内容因字典插入顺序不同会得到
    不同摘要，哈希链就会在重写一次文件后无辜断裂。
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(value: Any) -> str:
    """对任意可 JSON 化对象取 sha256（十六进制小写）。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #


def _looks_like_secret(value: str) -> bool:
    lowered = value.strip().lower()
    return any(lowered.startswith(p) for p in _SENSITIVE_VALUE_PREFIXES)


def redact(value: Any, *, path: str = "") -> tuple[Any, list[str]]:
    """递归脱敏，返回 ``(脱敏后的值, 被脱敏字段路径列表)``。

    只返回**路径**不返回值：若把原值带回调用方，调用方一旦记进日志，脱敏就白做了。
    """
    redacted_paths: list[str] = []

    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            child_path = f"{path}.{key_str}" if path else key_str
            if _SENSITIVE_KEY.search(key_str):
                out[key_str] = REDACTED
                redacted_paths.append(child_path)
            else:
                out[key_str], sub = redact(item, path=child_path)
                redacted_paths.extend(sub)
        return out, redacted_paths

    if isinstance(value, (list, tuple)):
        out_list: list[Any] = []
        for i, item in enumerate(value):
            child_path = f"{path}[{i}]" if path else f"[{i}]"
            redacted_item, sub = redact(item, path=child_path)
            out_list.append(redacted_item)
            redacted_paths.extend(sub)
        return out_list, redacted_paths

    if isinstance(value, str) and _looks_like_secret(value):
        redacted_paths.append(path or "<value>")
        return REDACTED, redacted_paths

    return value, redacted_paths


# --------------------------------------------------------------------------- #
# 构建与校验
# --------------------------------------------------------------------------- #


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuditSchemaError(f"字段 {name!r} 必须是非空字符串，收到 {value!r}")
    return value


def _require_mapping(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AuditSchemaError(f"字段 {name!r} 必须是对象（dict），收到 {type(value).__name__}")
    return dict(value)


def _require_caveats(value: Any) -> list[str]:
    """注意事项必须是字符串列表。

    允许空列表（确实没有已知限制），但不允许 ``None`` 或字符串——把「没写」
    和「写了没有」区分开，是这条规则的全部意义。
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AuditSchemaError(
            f"字段 'caveats' 必须是字符串列表（无内容时传空列表），收到 {type(value).__name__}"
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise AuditSchemaError(f"字段 'caveats' 的元素必须是非空字符串，收到 {item!r}")
        out.append(item)
    return out


def validate_card(card: Mapping[str, Any]) -> None:
    """校验一张工件是否满足 T1 结构要求（不校验哈希链，链由存储层校验）。"""
    if not isinstance(card, Mapping):
        raise AuditSchemaError(f"工件必须是对象，收到 {type(card).__name__}")

    missing = [f for f in REQUIRED_FIELDS if card.get(f) is None]
    if missing:
        raise AuditSchemaError(f"工件缺少 T1 必需字段：{missing}")

    _require_text("operator", card["operator"])
    _require_text("occurred_at", card["occurred_at"])
    _require_mapping("basis", card["basis"])
    _require_mapping("outcome", card["outcome"])
    _require_caveats(card["caveats"])

    occurred_at = card["occurred_at"]
    try:
        datetime.fromisoformat(str(occurred_at))
    except ValueError as exc:
        raise AuditSchemaError(f"字段 'occurred_at' 不是可解析的 ISO8601 时间：{occurred_at!r}") from exc

    if not isinstance(card.get("action"), str) or not card["action"].strip():
        raise AuditSchemaError("字段 'action' 必须是非空字符串")

    for name in ("subject", "inputs", "env"):
        if card.get(name) is not None and not isinstance(card[name], Mapping):
            raise AuditSchemaError(f"字段 {name!r} 必须是对象或 null")

    for name in ("seq",):
        if not isinstance(card.get(name), int) or isinstance(card.get(name), bool):
            raise AuditSchemaError(f"字段 {name!r} 必须是非负整数")

    prev = card.get("prev_sha256")
    if not isinstance(prev, str) or len(prev) != 64:
        raise AuditSchemaError("字段 'prev_sha256' 必须是 64 位十六进制字符串")


def card_digest(card: Mapping[str, Any]) -> str:
    """计算卡片摘要——**排除** ``card_sha256`` 自身，否则摘要无法自洽。"""
    body = {k: v for k, v in card.items() if k != "card_sha256"}
    return digest_of(body)


def build_card(
    *,
    action: str,
    operator: str,
    occurred_at: str,
    basis: Mapping[str, Any],
    outcome: Mapping[str, Any],
    caveats: Sequence[str],
    subject: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
    env: Mapping[str, Any] | None = None,
    session: str | None = None,
    seq: int = 0,
    prev_sha256: str = GENESIS_SHA256,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造并封章一张工件。

    脱敏在**构造阶段**完成：调用方即使误把令牌放进 ``basis``，落盘的也只有占位符，
    且脱敏事实会作为一条 caveat 留下痕迹（否则「为什么这个字段是占位符」无法解释）。
    """
    _require_text("action", action)
    # 先校验类型再转换：``dict("ab")`` / ``list("ab")`` 都能"成功"，
    # 若先转换后校验，把 caveats 误传成字符串会被拆成单字符列表并通过检查，
    # 于是「没写注意事项」伪装成「写了很多条」。
    caveats_list = _require_caveats(caveats)
    basis_map = _require_mapping("basis", basis)
    outcome_map = _require_mapping("outcome", outcome)
    subject_map = _require_mapping("subject", subject) if subject is not None else None
    inputs_map = _require_mapping("inputs", inputs) if inputs is not None else None
    env_map = _require_mapping("env", env) if env is not None else None
    extra_map = _require_mapping("extra", extra) if extra is not None else None
    redacted_paths: list[str] = []

    def _clean(name: str, value: Any) -> Any:
        cleaned, paths = redact(value, path=name)
        redacted_paths.extend(paths)
        return cleaned

    card: dict[str, Any] = {
        "card_version": CARD_VERSION,
        "card_id": digest_of(
            {"action": action, "operator": operator, "occurred_at": occurred_at, "seq": seq}
        )[:32],
        "seq": seq,
        "action": action,
        "operator": operator,
        "occurred_at": occurred_at,
        "session": session,
        "basis": _clean("basis", basis_map),
        "outcome": _clean("outcome", outcome_map),
        "caveats": caveats_list,
        "subject": _clean("subject", subject_map) if subject_map is not None else None,
        "inputs": _clean("inputs", inputs_map) if inputs_map is not None else None,
        "env": _clean("env", env_map) if env_map is not None else None,
        "prev_sha256": prev_sha256,
    }
    if extra_map is not None:
        cleaned_extra, paths = redact(extra_map, path="extra")
        redacted_paths.extend(paths)
        card["extra"] = cleaned_extra

    if redacted_paths:
        card["caveats"].append(
            "已对敏感字段脱敏（只记字段路径、不记原值）：" + ", ".join(sorted(set(redacted_paths)))
        )

    validate_card(card)
    card["card_sha256"] = card_digest(card)
    return card


def verify_card(card: Mapping[str, Any]) -> tuple[bool, str]:
    """重算单张卡片摘要并比对。返回 ``(是否一致, 说明)``。"""
    recorded = card.get("card_sha256")
    if not isinstance(recorded, str) or len(recorded) != 64:
        return False, "card_sha256 缺失或格式非法"
    recomputed = card_digest(card)
    if recomputed != recorded:
        return False, f"摘要不一致：记录 {recorded[:16]}… / 重算 {recomputed[:16]}…"
    return True, "摘要一致"


def now_iso() -> str:
    """当前 UTC 时间（微秒精度，带时区）。

    用 UTC 而非本地时间：工件要跨机器、跨时区比对，本地时间会让「谁先谁后」
    随部署地点变化。
    """
    return datetime.now(UTC).isoformat(timespec="microseconds")


def completeness(cards: Iterable[Mapping[str, Any]]) -> CompletenessReport:
    """统计五元组完整率（按卡片，不按字段）。"""
    total = 0
    complete = 0
    incomplete: list[dict[str, Any]] = []
    for card in cards:
        total += 1
        missing = [f for f in REQUIRED_FIELDS if card.get(f) is None]
        if missing:
            incomplete.append({"card_id": card.get("card_id"), "missing": missing})
        else:
            complete += 1
    ratio = 1.0 if total == 0 else complete / total
    return CompletenessReport(
        n_cards=total,
        n_complete=complete,
        ratio=ratio,
        incomplete=tuple(incomplete),
    )
