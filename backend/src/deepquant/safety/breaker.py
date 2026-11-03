"""熔断开关：状态持久化、半开探测、两条强制路径。

为什么这个熔断器必须落盘
------------------------
内存熔断器有一个致命漏洞：**重启即清零**。而"重启"恰恰是最容易被用来绕过保护
的动作——跑不动了重启一下、被拒了重启一下，熔断器就永远停在闭合态，像装了个
只会亮灯的开关。所以状态写在 ``breaker.json`` 里，进程重启后仍然有效。

状态机
------
``closed`` --(连续失败 ≥ 阈值)--> ``open``
``open`` --(超过冷却时间)--> ``half_open``（只放少量探测）
``half_open`` --(探测成功)--> ``closed``
``half_open`` --(探测失败)--> ``open``（冷却重新计时）

半开只放**少量**探测是重点：冷却一过就全量放行，等于让刚挂掉的下游再被雪崩一次。

两条路径
--------
计划要求"异步与同步双路径"。本仓库没有 broker 的异步连接，两条路径是：

1. **HTTP 请求路径**：``SafetyMiddleware`` 在执行前查熔断、执行后记结果；
2. **CLI/脚本路径**：``breaker_scope`` 上下文管理器，语义完全一致。

两处共用同一个状态文件与判定函数，不允许各写一套阈值——否则会出现
"接口被熔断但脚本仍能跑"这种只在事故现场才会发现的分裂。

失败要"算不出也算失败"
----------------------
状态文件损坏时不能当作"没有熔断记录"（那等于清零）。读不出来就拒绝，并报告
``breaker_state_unreadable``——与 T2/T4 同一条原则。
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from deepquant.audit.run_card import now_iso
from deepquant.safety.codes import safety_reason

logger = logging.getLogger(__name__)

#: 安全状态目录（熔断状态、清理证据都在这里）。
SAFETY_DIR_ENV = "DEEPQUANT_SAFETY_DIR"

#: 熔断阈值：连续失败多少次后打开。
THRESHOLD_ENV = "DEEPQUANT_BREAKER_THRESHOLD"

#: 冷却秒数：打开后多久进入半开。
COOLDOWN_ENV = "DEEPQUANT_BREAKER_COOLDOWN_S"

#: 半开放行的探测数。
HALF_OPEN_ENV = "DEEPQUANT_BREAKER_HALF_OPEN_CALLS"

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"

DEFAULT_THRESHOLD = 3
DEFAULT_COOLDOWN_S = 60.0
DEFAULT_HALF_OPEN_CALLS = 1

BREAKER_FILE = "breaker.json"


class BreakerStateError(RuntimeError):
    """熔断状态不可读/不可写。必须按不通过处理，不能当成"没有熔断"。"""


def safety_root() -> Path:
    """安全状态目录。默认 ``backend/data/safety``（已 gitignore）。"""
    raw = os.environ.get(SAFETY_DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[3] / "data" / "safety"


def _env_number(name: str, default: float, cast=float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError as exc:
        raise BreakerStateError(f"{name}={raw!r} 不是合法数值") from exc
    if value <= 0:
        raise BreakerStateError(f"{name}={raw!r} 必须为正数")
    return value


def config() -> dict[str, float]:
    return {
        "threshold": _env_number(THRESHOLD_ENV, DEFAULT_THRESHOLD, int),
        "cooldown_s": _env_number(COOLDOWN_ENV, DEFAULT_COOLDOWN_S),
        "half_open_calls": _env_number(HALF_OPEN_ENV, DEFAULT_HALF_OPEN_CALLS, int),
    }


@dataclass
class BreakerState:
    """某个作用域的熔断状态。"""

    scope: str
    state: str = STATE_CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    opened_until: float = 0.0
    half_open_calls: int = 0
    trips: int = 0
    last_reason: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "opened_at": self.opened_at,
            "opened_until": self.opened_until,
            "half_open_calls": self.half_open_calls,
            "trips": self.trips,
            "last_reason": self.last_reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BreakerState":
        return cls(
            scope=str(payload.get("scope", "")),
            state=str(payload.get("state", STATE_CLOSED)),
            consecutive_failures=int(payload.get("consecutive_failures", 0)),
            opened_at=float(payload.get("opened_at", 0.0)),
            opened_until=float(payload.get("opened_until", 0.0)),
            half_open_calls=int(payload.get("half_open_calls", 0)),
            trips=int(payload.get("trips", 0)),
            last_reason=str(payload.get("last_reason", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


@dataclass(frozen=True)
class BreakerDecision:
    """一次熔断判定。"""

    allowed: bool
    scope: str
    state: str
    code: str | None = None
    detail: str = ""
    retry_after_s: int = 0
    checkpoint: str = "breaker"

    @property
    def status_code(self) -> int | None:
        return None if self.code is None else safety_reason(self.code).status_code

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "allowed": self.allowed,
            "scope": self.scope,
            "state": self.state,
            "checkpoint": self.checkpoint,
        }
        if self.code is not None:
            payload["reason_code"] = self.code
            payload["status_code"] = self.status_code
            payload["detail"] = self.detail
            payload["retry_after_s"] = self.retry_after_s
        return payload


# --------------------------------------------------------------------------- #
# 状态读写
# --------------------------------------------------------------------------- #


def _state_path(root: Path | None = None) -> Path:
    return Path(root or safety_root()) / BREAKER_FILE


def _read_all(root: Path | None = None) -> dict[str, dict[str, Any]]:
    path = _state_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise BreakerStateError(f"熔断状态文件不可读：{path}（{type(exc).__name__}）") from exc
    if not isinstance(payload, dict):
        raise BreakerStateError(f"熔断状态文件结构异常（顶层不是对象）：{path}")
    scopes = payload.get("scopes")
    if not isinstance(scopes, dict):
        raise BreakerStateError(f"熔断状态文件结构异常：{path}")
    return scopes


def _write_all(scopes: dict[str, dict[str, Any]], root: Path | None = None) -> None:
    """原子写：先写临时文件再 replace，避免半个 JSON 让下次启动读不出来。"""
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps({"version": 1, "scopes": scopes}, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def load(scope: str, root: Path | None = None) -> BreakerState:
    scopes = _read_all(root)
    raw = scopes.get(scope)
    if raw is None:
        return BreakerState(scope=scope)
    state = BreakerState.from_dict(raw)
    state.scope = scope
    return state


def save(state: BreakerState, root: Path | None = None) -> None:
    scopes = _read_all(root)
    state.updated_at = now_iso()
    scopes[state.scope] = state.to_dict()
    _write_all(scopes, root)


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #


def _effective_state(state: BreakerState, now: float, cooldown_s: float) -> str:
    """把"到点该变半开"这件事在读取时算出来，而不是靠定时器。

    没有后台定时器 = 没有"定时器没跑起来导致永远不会半开"这种故障模式。
    """
    if state.state == STATE_OPEN and now >= state.opened_until:
        return STATE_HALF_OPEN
    return state.state


def allow(scope: str, *, now: float | None = None, root: Path | None = None) -> BreakerDecision:
    """判定该作用域当前是否放行。读取时不修改状态（判定应与写入分离）。"""
    moment = time.time() if now is None else now
    cfg = config()
    try:
        state = load(scope, root)
    except BreakerStateError as exc:
        return BreakerDecision(
            False, scope, "unreadable", code="breaker_state_unreadable", detail=str(exc)
        )

    effective = _effective_state(state, moment, float(cfg["cooldown_s"]))
    if effective == STATE_CLOSED:
        return BreakerDecision(True, scope, STATE_CLOSED)
    if effective == STATE_OPEN:
        remaining = max(0, int(round(state.opened_until - moment)))
        return BreakerDecision(
            False,
            scope,
            STATE_OPEN,
            code="breaker_open",
            detail=f"{scope} 已熔断，冷却剩余 {remaining}s（上次原因：{state.last_reason or '未记录'}）",
            retry_after_s=remaining,
        )

    # half_open：只放少量探测
    if state.half_open_calls < int(cfg["half_open_calls"]):
        return BreakerDecision(True, scope, STATE_HALF_OPEN)
    return BreakerDecision(
        False,
        scope,
        STATE_HALF_OPEN,
        code="breaker_open",
        detail=f"{scope} 处于半开且探测名额已用完，等待探测结果",
        retry_after_s=int(cfg["cooldown_s"]),
    )


def record_success(scope: str, *, now: float | None = None, root: Path | None = None) -> BreakerState:
    """记一次成功：半开探测成功即闭合；闭合态下清零失败计数。

    状态没有任何变化时**不写盘**：每个成功请求都写一次状态文件，会让一次回测的
    磁盘写入量翻几倍，却换不来任何额外信息。只在状态真正改变时落盘。
    """
    moment = time.time() if now is None else now
    cfg = config()
    state = load(scope, root)
    effective = _effective_state(state, moment, float(cfg["cooldown_s"]))

    changed = state.consecutive_failures != 0
    if effective == STATE_HALF_OPEN:
        state.state = STATE_CLOSED
        state.half_open_calls = 0
        state.opened_until = 0.0
        state.last_reason = ""
        changed = True
    elif effective == STATE_CLOSED:
        if state.state != STATE_CLOSED or state.half_open_calls:
            state.state = STATE_CLOSED
            state.half_open_calls = 0
            changed = True
    else:
        # 冷却期内成功一次不代表恢复（可能只是被允许的探测之外的旁路结果），
        # 保持打开，避免"偶发成功"把真正的故障掩盖掉。
        state.state = STATE_OPEN
        changed = True
    state.consecutive_failures = 0
    if changed:
        save(state, root)
    return state


def record_failure(
    scope: str,
    reason: str = "",
    *,
    now: float | None = None,
    root: Path | None = None,
) -> BreakerState:
    """记一次失败：达阈值即打开；半开探测失败立即重新打开。"""
    moment = time.time() if now is None else now
    cfg = config()
    state = load(scope, root)
    effective = _effective_state(state, moment, float(cfg["cooldown_s"]))

    state.consecutive_failures += 1
    state.last_reason = reason[:300]

    if effective == STATE_HALF_OPEN:
        state.state = STATE_OPEN
        state.opened_at = moment
        state.opened_until = moment + float(cfg["cooldown_s"])
        state.half_open_calls = 0
        state.trips += 1
    elif state.consecutive_failures >= int(cfg["threshold"]):
        state.state = STATE_OPEN
        state.opened_at = moment
        state.opened_until = moment + float(cfg["cooldown_s"])
        state.half_open_calls = 0
        if effective != STATE_OPEN:
            state.trips += 1
    save(state, root)
    return state


def trip(scope: str, reason: str, *, now: float | None = None, root: Path | None = None) -> BreakerState:
    """人工/演练强制熔断。理由必填——一个没有理由的熔断没人敢解除。"""
    if not (reason or "").strip():
        raise ValueError("强制熔断必须写明理由")
    moment = time.time() if now is None else now
    cfg = config()
    state = load(scope, root)
    state.state = STATE_OPEN
    state.opened_at = moment
    state.opened_until = moment + float(cfg["cooldown_s"])
    state.half_open_calls = 0
    state.trips += 1
    state.last_reason = reason.strip()[:300]
    save(state, root)
    return state


def reset(scope: str, *, root: Path | None = None) -> BreakerState:
    """人工解除熔断（保留 trips 计数作为历史证据）。"""
    state = load(scope, root)
    state.state = STATE_CLOSED
    state.consecutive_failures = 0
    state.opened_at = 0.0
    state.opened_until = 0.0
    state.half_open_calls = 0
    state.last_reason = ""
    save(state, root)
    return state


def snapshot(root: Path | None = None) -> dict[str, Any]:
    """全部作用域的状态快照（运维查询用）。"""
    try:
        scopes = _read_all(root)
    except BreakerStateError as exc:
        return {"error": str(exc), "scopes": {}}
    return {
        "error": None,
        "config": config(),
        "scopes": {k: BreakerState.from_dict(v).to_dict() for k, v in sorted(scopes.items())},
    }


@contextmanager
def breaker_scope(scope: str, *, root: Path | None = None) -> Iterator[None]:
    """CLI/脚本路径的熔断闸：进作用域先判定，退出按结果记录。

    与 HTTP 中间件共用同一份状态与阈值。异常向上抛（业务需要看到真实错误），
    但会被记为失败——失败计数不依赖调用方"记得上报"。
    """
    decision = allow(scope, root=root)
    if not decision.allowed:
        raise BreakerStateError(f"[{decision.code}] {decision.detail}")
    try:
        yield
    except BaseException:
        record_failure(scope, "作用域内抛出异常", root=root)
        raise
    else:
        record_success(scope, root=root)


__all__ = [
    "BREAKER_FILE",
    "BreakerDecision",
    "BreakerState",
    "BreakerStateError",
    "COOLDOWN_ENV",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_HALF_OPEN_CALLS",
    "DEFAULT_THRESHOLD",
    "HALF_OPEN_ENV",
    "SAFETY_DIR_ENV",
    "STATE_CLOSED",
    "STATE_HALF_OPEN",
    "STATE_OPEN",
    "THRESHOLD_ENV",
    "allow",
    "breaker_scope",
    "config",
    "load",
    "record_failure",
    "record_success",
    "reset",
    "safety_root",
    "save",
    "snapshot",
    "trip",
]
