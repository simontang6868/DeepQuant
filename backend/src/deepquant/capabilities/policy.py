"""能力准入策略：这个能力是否开放、这个人是否被授权、版本是否兼容。

三种模式，与 T2 同构
--------------------
``off``（默认）/ ``observe`` / ``enforce``。为什么不用一个布尔开关：能力关闭与
授权收紧都是会影响线上调用的动作，需要一段"只记录不拦截"的观察期来确认
「哪些调用方会被挡」。塞进一个布尔开关的结果，一定是有人为了跑通业务把它关掉
然后忘开——而"以为开了其实没开"是这类系统最危险的失败态。

- ``off``：完全不介入，不加响应头、不读环境配置（默认值，既有行为零变化）；
- ``observe``：判定逻辑与 ``enforce`` **完全相同**，只是不拦，响应头给 ``would-reject``；
- ``enforce``：拒绝。

观察期的判定必须与强制期一致，否则观察期采到的数字是假的——这一点由测试钉住
（见 ``tests/capabilities/test_cap_policy.py``）。

配置写错就拒绝，不静默降级
--------------------------
``DEEPQUANT_CAPABILITY_DISABLED`` 里写了一个不存在的 cap_id、``GRANTS`` 里写了
不存在的能力、模式值拼错——一律抛 ``CapabilityConfigError``，中间件把它变成
``503 capability_internal_error``。理由很直接：写错的后果是"以为禁用了其实没禁用"，
而它和"禁用了"在日志里长得一样。

授权格式
--------
``cap_id=alice,bob;other.cap=*``：分号分隔条目，等号右边是允许的操作人列表，
``*`` 表示所有人。**未配置的能力默认对所有已识别操作人开放**——否则一上线就会
把现有前端全部挡住，而那不该是"引入契约化"的副作用。
"""

from __future__ import annotations

import fnmatch
import logging
import os
from dataclasses import dataclass
from typing import Mapping

from deepquant.capabilities.codes import CAPABILITY_CODES, capability_reason
from deepquant.capabilities.registry import get as get_capability
from deepquant.capabilities.registry import validate_registry
from deepquant.capabilities.registry import CAPABILITIES
from deepquant.capabilities.spec import CapabilitySpec, same_major

logger = logging.getLogger(__name__)

#: 能力闸门模式。
MODE_ENV = "DEEPQUANT_CAPABILITY_MODE"

#: 显式关闭的能力：逗号分隔，支持 ``*`` 结尾的前缀通配（如 ``backtest.*``）。
DISABLED_ENV = "DEEPQUANT_CAPABILITY_DISABLED"

#: 按能力授权：``cap_id=alice,bob;other.cap=*``。
GRANTS_ENV = "DEEPQUANT_CAPABILITY_GRANTS"

MODE_OFF = "off"
MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
VALID_MODES: tuple[str, ...] = (MODE_OFF, MODE_OBSERVE, MODE_ENFORCE)

#: 调用方钉契约版本用的请求头。
VERSION_HEADER = "X-Capability-Version"
#: 响应头：告诉调用方它实际用的是哪个能力、哪个版本。
RESPONSE_ID_HEADER = "X-Capability-Id"
RESPONSE_VERSION_HEADER = "X-Capability-Version"
#: 观察期提示头：值为被判定为"本应拒绝"的原因码。
WOULD_REJECT_HEADER = "X-Capability-Would-Reject"
#: 弃用提示头。
DEPRECATED_HEADER = "X-Capability-Deprecated"


class CapabilityConfigError(RuntimeError):
    """能力配置不合法。**必须**导致拒绝，不得静默降级。"""


class CapabilityDenied(RuntimeError):
    """服务层能力被拒绝（HTTP 面由中间件返回，不会抛到这里）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        spec = capability_reason(code)
        self.code = code
        self.status_code = spec.status_code
        self.detail = detail or spec.summary
        super().__init__(f"[{code}] {self.detail}")


@dataclass(frozen=True)
class Decision:
    """一次能力判定的结果。"""

    capability: str
    allowed: bool
    code: str | None = None
    detail: str = ""
    #: 固定为 ``"capability"``：写进 T1 工件后，可与风控检查点名区分开。
    checkpoint: str = "capability"

    @property
    def status_code(self) -> int | None:
        return None if self.code is None else capability_reason(self.code).status_code

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "capability": self.capability,
            "allowed": self.allowed,
            "checkpoint": self.checkpoint,
        }
        if self.code is not None:
            payload["reason_code"] = self.code
            payload["status_code"] = self.status_code
            payload["detail"] = self.detail
        return payload


# --------------------------------------------------------------------------- #
# 配置读取：错就抛，不猜
# --------------------------------------------------------------------------- #


def mode() -> str:
    raw = os.environ.get(MODE_ENV, "").strip().lower()
    if not raw:
        return MODE_OFF
    if raw not in VALID_MODES:
        raise CapabilityConfigError(
            f"{MODE_ENV}={raw!r} 非法，合法值：{', '.join(VALID_MODES)}"
        )
    return raw


def enabled() -> bool:
    """是否介入（``observe`` 也算介入：它要判定、要留痕）。模式非法时抛错。"""
    return mode() != MODE_OFF


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def disabled() -> frozenset[str]:
    """被显式关闭的能力集合（已展开通配）。

    通配在解析期就展开成具体 cap_id，而不是留给运行期匹配：
    这样"到底关了哪几个"永远是确定的，日志与报表不必再解释通配语义。
    """
    raw = os.environ.get(DISABLED_ENV, "").strip()
    if not raw:
        return frozenset()

    known = [cap.cap_id for cap in CAPABILITIES]
    resolved: set[str] = set()
    problems: list[str] = []
    for pattern in _parse_csv(raw):
        if "*" in pattern:
            hits = [cap_id for cap_id in known if fnmatch.fnmatchcase(cap_id, pattern)]
            if not hits:
                problems.append(f"通配 {pattern!r} 未匹配任何能力")
            resolved.update(hits)
            continue
        if get_capability(pattern) is None:
            problems.append(f"能力 {pattern!r} 不存在")
            continue
        resolved.add(pattern)

    if problems:
        raise CapabilityConfigError(
            f"{DISABLED_ENV} 配置不合法：" + "；".join(problems)
        )
    return frozenset(resolved)


def grants() -> dict[str, frozenset[str]]:
    """按能力的授权表。``*`` 表示该能力对所有操作人开放。"""
    raw = os.environ.get(GRANTS_ENV, "").strip()
    if not raw:
        return {}

    table: dict[str, frozenset[str]] = {}
    problems: list[str] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            problems.append(f"条目 {chunk!r} 缺少 '='（应为 cap_id=alice,bob）")
            continue
        cap_id, _, people = chunk.partition("=")
        cap_id = cap_id.strip()
        if get_capability(cap_id) is None:
            problems.append(f"能力 {cap_id!r} 不存在")
            continue
        if cap_id in table:
            problems.append(f"能力 {cap_id!r} 重复配置（后一条会静默覆盖前一条）")
            continue
        names = frozenset(_parse_csv(people))
        if not names:
            problems.append(f"能力 {cap_id!r} 的授权名单为空（要开放所有人请写 *）")
            continue
        table[cap_id] = names

    if problems:
        raise CapabilityConfigError(f"{GRANTS_ENV} 配置不合法：" + "；".join(problems))
    return table


def reset_cache() -> None:
    """兼容用：本模块不缓存环境变量，保留该名字便于测试与运维脚本统一调用。"""
    return None


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #


def evaluate(cap: CapabilitySpec, operator: str) -> Decision:
    """判定一次调用是否放行。

    顺序：先看是否被关闭（运维意图），再看是否被授权（人是哪一位），
    最后放行。版本兼容由 :func:`check_version` 单独判定——它需要请求头，
    而 ``evaluate`` 只关心配置与身份。
    """
    closed = disabled()
    if cap.cap_id in closed:
        return Decision(
            capability=cap.cap_id,
            allowed=False,
            code="capability_disabled",
            detail=f"能力 {cap.cap_id} 已被运维关闭",
        )

    table = grants()
    allowed_people = table.get(cap.cap_id)
    if allowed_people is not None and "*" not in allowed_people:
        if operator not in allowed_people:
            return Decision(
                capability=cap.cap_id,
                allowed=False,
                code="capability_not_permitted",
                detail=(
                    f"操作人 {operator!r} 未被授予 {cap.cap_id}；"
                    f"当前授权名单 {sorted(allowed_people)}"
                ),
            )

    return Decision(capability=cap.cap_id, allowed=True)


def check_version(cap: CapabilitySpec, requested: str | None) -> Decision | None:
    """校验调用方钉的版本。未钉版本（``None``）返回 ``None`` 表示"不判定"。

    同一 major 内向后兼容，直接放行；跨 major 一律拒绝。不做"尽力兼容"——
    调用方按 2.0 写代码、服务端按 1.x 返回，缺字段时它会在自己的解析层炸掉，
    而错误现场与真正原因（版本不匹配）隔了好几层。
    """
    if not requested:
        return None
    requested = requested.strip()
    if same_major(requested, cap.version):
        return None
    return Decision(
        capability=cap.cap_id,
        allowed=False,
        code="capability_version_unsupported",
        detail=(
            f"调用方钉的版本 {requested} 与本服务 {cap.cap_id}@{cap.version} "
            "major 不兼容（同一 major 内才承诺向后兼容）"
        ),
    )


def guard(cap_id: str, operator: str) -> Decision:
    """服务层能力闸门：非 HTTP 面（如 ``audit.replay``）在入口处调用。

    返回判定结果；``enforce`` 下不通过直接抛 :class:`CapabilityDenied`，
    ``observe`` 下只记警告（与 HTTP 面语义一致），``off`` 下不做任何判定。
    """
    current = mode()
    if current == MODE_OFF:
        # off 下连配置都不读：这样"关着"的系统不会因为配置写错而拒绝业务，
        # 也不会因为读到坏配置而在日志里制造噪声。
        return Decision(capability=cap_id, allowed=True)

    cap = get_capability(cap_id)
    if cap is None:
        decision = Decision(
            capability=cap_id,
            allowed=False,
            code="capability_unknown",
            detail=f"能力 {cap_id!r} 未登记",
        )
    else:
        decision = evaluate(cap, operator)

    if decision.allowed:
        return decision
    if current == MODE_ENFORCE:
        raise CapabilityDenied(decision.code or "capability_internal_error", decision.detail)
    logger.warning(
        "能力观察期：%s 本应被拒绝（%s：%s），当前放行", cap_id, decision.code, decision.detail
    )
    return decision


def assert_config_valid() -> None:
    """启动自检：注册表与三份配置一起校验，任一不合法即抛错。"""
    validate_registry()
    mode_error: Exception | None = None
    try:
        mode()
        disabled()
        grants()
    except CapabilityConfigError as exc:  # 保留原始异常给调用方决定如何处理
        mode_error = exc
    if mode_error is not None:
        raise mode_error


def policy_snapshot() -> dict[str, object]:
    """当前策略快照（运维查询用；配置不合法时 ``error`` 字段如实给出）。"""
    snapshot: dict[str, object] = {
        "mode": None,
        "disabled": None,
        "grants": None,
        "error": None,
        "n_capabilities": len(CAPABILITIES),
    }
    try:
        snapshot["mode"] = mode()
        snapshot["disabled"] = sorted(disabled())
        snapshot["grants"] = {k: sorted(v) for k, v in grants().items()}
    except CapabilityConfigError as exc:
        snapshot["error"] = str(exc)
    return snapshot


__all__ = [
    "DEPRECATED_HEADER",
    "DISABLED_ENV",
    "Decision",
    "GRANTS_ENV",
    "MODE_ENFORCE",
    "MODE_ENV",
    "MODE_OBSERVE",
    "MODE_OFF",
    "RESPONSE_ID_HEADER",
    "RESPONSE_VERSION_HEADER",
    "VALID_MODES",
    "VERSION_HEADER",
    "WOULD_REJECT_HEADER",
    "CapabilityConfigError",
    "CapabilityDenied",
    "assert_config_valid",
    "check_version",
    "disabled",
    "enabled",
    "evaluate",
    "grants",
    "guard",
    "mode",
    "policy_snapshot",
    "reset_cache",
]
