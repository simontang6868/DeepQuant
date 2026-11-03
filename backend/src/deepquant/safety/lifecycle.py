"""退出清理不变量：四步串行、证据可检索、未确认清空则拒绝启动。

计划原文针对交易平台：停止策略 → 撤销全部未成交委托 → 验证清空 → 留存清理日志。
本仓库**没有委托域**（无券商连接、无订单状态机），所以"撤单"这一步没有对应物。
处理方式与 T1 登记委托类操作一致：**显式登记为 not_applicable 并写明理由**，
既不伪造实现，也不把它算进完成度；同时把四步中真实存在的部分落到进程级资源上。

四步在本仓库的映射
------------------
============================  ==================================================
计划步骤                        本仓库落地
============================  ==================================================
停止策略                        置 draining 标记：清理开始后拒绝新任务
撤销全部未成交委托                not_applicable（无委托域）
                               → 替代动作：drain 在飞请求，超时窗内未结束即失败
验证清空                        快照比对：在飞=0、缓存=0、安全目录无临时残留
留存清理日志                    append-only JSONL + 每行 sha256 链 + 前后快照
============================  ==================================================

为什么"拒绝启动"比"启动后报警"更对
----------------------------------
清理失败意味着上次退出时资源可能没释放（半写的文件、没关的连接、没落盘的工件）。
这种状态下继续启动，新进程会基于一个未知状态继续写，把一次可诊断的失败变成
一次不可诊断的数据污染。所以：只要 ``cleanup_required.json`` 还在，就拒绝启动，
必须有人看过证据、显式执行一次成功清理（或显式声明知情）才能继续。

证据必须有"前后对比"，而不是一句"已完成"
----------------------------------------
计划要求"验证清空 + 快照比对，而非仅打印一句完成"。因此每次清理记录：

- ``before`` / ``after`` 两个快照（在飞数、缓存条目、安全目录文件清单）；
- 每一步的 ``ok`` 与 ``detail``；
- ``not_applicable`` 的步骤与理由（让"缺的那块"永远可见）；
- 每条记录带 ``prev_sha256``，形成可校验的链——日志被改过就对不上。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from deepquant.audit.run_card import now_iso
from deepquant.safety.breaker import SAFETY_DIR_ENV, safety_root

logger = logging.getLogger(__name__)

#: 在飞请求排空超时（秒）。超时即清理失败——计划的"超时窗内完成"是硬要求。
DRAIN_TIMEOUT_ENV = "DEEPQUANT_DRAIN_TIMEOUT_S"
DEFAULT_DRAIN_TIMEOUT_S = 5.0

#: 允许"知情后仍然启动"。置 1 时启动不被拒绝，但会写一条含理由的取证记录。
ALLOW_DIRTY_START_ENV = "DEEPQUANT_ALLOW_DIRTY_START"

MARKER_FILE = "cleanup_required.json"
SESSION_FILE = "session.json"
CLEANUP_LOG = "cleanup_log.jsonl"

#: 无对应业务面的步骤：登记理由，永不计入完成度。
NOT_APPLICABLE_STEPS: dict[str, str] = {
    "cancel_open_orders": (
        "本仓库无委托域（无券商连接、无订单状态机），不存在未成交委托可撤；"
        "待业务形态确认（计划 §10 第 1 项）后启用"
    ),
    "liquidate_positions": (
        "本仓库不做真实持仓管理（回测持仓只在内存/结果文档中），无实盘仓位可平"
    ),
}


def safety_state_enabled() -> bool:
    """本进程是否应该读写安全状态。

    规则：**显式配置了安全目录就启用**；否则在 pytest 进程里不启用。

    为什么：测试进程没有"退出"要保护，逐用例写清理证据既无意义又会污染仓库默认
    目录。更糟的是它会制造跨套件干扰——某个测试连续 3 次 5xx 就会在默认目录把
    熔断器打开，后续不相关的测试全部被 503 拦掉，而原因看起来毫无关系。

    安全层自己的测试都显式设置 ``DEEPQUANT_SAFETY_DIR``，因此仍然完整覆盖真实行为。
    """
    if os.environ.get(SAFETY_DIR_ENV, "").strip():
        return True
    return "PYTEST_CURRENT_TEST" not in os.environ


class DirtyShutdown(RuntimeError):
    """上次退出未确认清理完成。拒绝启动直到人工处理。"""
    def __init__(self, marker: dict[str, Any], path: Path) -> None:
        self.marker = marker
        self.path = path
        started = marker.get("started_at", "未知时间")
        reason = marker.get("reason", "未记录")
        super().__init__(
            f"检测到未完成的退出清理（{path}）：开始于 {started}，原因 {reason}。"
            f"请执行 `scripts/safety.py cleanup` 并检视证据，或设置 "
            f"{ALLOW_DIRTY_START_ENV}=1 显式知情后启动"
        )


# --------------------------------------------------------------------------- #
# 在飞请求计数
# --------------------------------------------------------------------------- #


class InFlight:
    """在飞的业务请求计数。线程安全（FastAPI 的同步端点跑在线程池里）。

    只做一件事：让"是否还有人在写"这个问题有确定答案。
    它是"验证清空"能成立的前提——没有计数就只能靠"等几秒应该差不多了"，
    而那种判断无法留下证据。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._draining = False

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    @property
    def draining(self) -> bool:
        with self._lock:
            return self._draining

    def begin_drain(self) -> None:
        with self._lock:
            self._draining = True

    @contextmanager
    def track(self) -> Iterator[None]:
        with self._lock:
            self._count += 1
        try:
            yield
        finally:
            with self._lock:
                self._count -= 1

    def wait_idle(self, timeout_s: float) -> tuple[bool, int]:
        """等到计数归零；返回 ``(是否归零, 剩余计数)``。

        用轮询而不是 Condition：清理路径上不需要最短响应时间，
        需要的是"即使有人漏了 decrement 也能在超时后给出确定结论"。
        """
        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline:
            if self.count == 0:
                return True, 0
            time.sleep(0.01)
        return self.count == 0, self.count

    def reset(self) -> None:
        """仅供测试复位。生产中不存在"把在飞数清掉"的合法场景。"""
        with self._lock:
            self._count = 0
            self._draining = False


#: 进程级单例：中间件与清理步骤必须看到同一个计数器。
INFLIGHT = InFlight()


# --------------------------------------------------------------------------- #
# 快照
# --------------------------------------------------------------------------- #


def _deps_cache_size() -> int | str:
    """依赖运行期缓存条目数。取不到时返回原因字符串，而不是 0。

    返回 0 会让"缓存未清"看起来像"已经清了"——这正是「没检出 ≠ 没问题」。
    """
    try:
        from deepquant.deps import market_data

        return len(market_data._CACHE)  # noqa: SLF001 - 清理验证需要看真实内部状态
    except Exception as exc:  # noqa: BLE001
        return f"unavailable:{type(exc).__name__}"


def snapshot() -> dict[str, Any]:
    """当前进程与安全目录的状态快照（清理前后各取一次，用于比对）。"""
    root = safety_root()
    files: list[str] = []
    if root.exists():
        files = sorted(p.name for p in root.iterdir() if p.is_file())
    return {
        "taken_at": now_iso(),
        "pid": os.getpid(),
        "in_flight": INFLIGHT.count,
        "draining": INFLIGHT.draining,
        "deps_runtime_cache": _deps_cache_size(),
        "safety_dir": str(root),
        "safety_files": files,
        "not_applicable": sorted(NOT_APPLICABLE_STEPS),
    }


# --------------------------------------------------------------------------- #
# 步骤与执行
# --------------------------------------------------------------------------- #


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    critical: bool = True
    status: str = "ran"  # ran / skipped_not_applicable

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "critical": self.critical,
            "status": self.status,
        }


@dataclass
class CleanupStep:
    """一个清理步骤：动作 + 验证。

    动作与验证分开，是为了避免"执行了"被当成"清空了"：动作只负责做事，
    验证必须独立检查结果。只写 ``done = True`` 的步骤在事故里毫无价值。
    """

    name: str
    action: Callable[[], None]
    verify: Callable[[], tuple[bool, str]]
    critical: bool = True


@dataclass
class CleanupReport:
    ok: bool
    steps: list[StepResult] = field(default_factory=list)
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    log_path: str | None = None
    marker_cleared: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "log_path": self.log_path,
            "marker_cleared": self.marker_cleared,
            "steps": [s.to_dict() for s in self.steps],
            "before": self.before,
            "after": self.after,
        }


def _drain_timeout() -> float:
    raw = os.environ.get(DRAIN_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_DRAIN_TIMEOUT_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_DRAIN_TIMEOUT_S


def default_steps() -> list[CleanupStep]:
    """默认四步（第三、四步由 :func:`run_cleanup` 统一执行，见注释）。"""
    timeout = _drain_timeout()

    def stop_new() -> None:
        INFLIGHT.begin_drain()

    def verify_stopped() -> tuple[bool, str]:
        return INFLIGHT.draining, "draining 标记已置" if INFLIGHT.draining else "draining 标记未置"

    def drain() -> None:
        INFLIGHT.wait_idle(timeout)

    def verify_drained() -> tuple[bool, str]:
        remaining = INFLIGHT.count
        if remaining == 0:
            return True, "在飞请求已归零"
        return False, f"超时窗 {timeout}s 内仍有 {remaining} 个请求未结束"

    def release_caches() -> None:
        try:
            from deepquant.deps import market_data

            market_data.reset_runtime()
        except Exception as exc:  # noqa: BLE001 - 记录原因，由验证步骤判定
            logger.warning("清理依赖运行期缓存时出错：%s", exc)

    def verify_caches() -> tuple[bool, str]:
        size = _deps_cache_size()
        if size == 0:
            return True, "依赖运行期缓存已清空"
        return False, f"依赖运行期缓存仍有 {size} 条（或不可判）"

    return [
        CleanupStep("stop_new_tasks", stop_new, verify_stopped),
        CleanupStep("drain_in_flight", drain, verify_drained),
        CleanupStep("release_caches", release_caches, verify_caches),
    ]


def _append_log(entry: dict[str, Any], root: Path) -> str:
    """追加一条清理记录，带 prev_sha256 链。返回本条摘要。"""
    path = root / CLEANUP_LOG
    prev = ""
    if path.exists():
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                prev = json.loads(lines[-1]).get("entry_sha256", "")
        except (json.JSONDecodeError, OSError):
            prev = ""  # 前一条读不出来也照写，但 prev 留空会断开链——由 CLI 校验报告
    body = dict(entry)
    body["prev_sha256"] = prev
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    body["entry_sha256"] = digest
    root.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n")
    return digest


def verify_log_chain(root: Path | None = None) -> dict[str, Any]:
    """校验清理日志的哈希链。断链即说明日志被改动或截断。"""
    path = (root or safety_root()) / CLEANUP_LOG
    if not path.exists():
        return {"ok": True, "entries": 0, "broken_at": None}
    entries = 0
    prev = ""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return {"ok": False, "entries": entries, "broken_at": number, "reason": "无法解析"}
        # 摘要覆盖 prev_sha256，所以只弹出 entry_sha256 再重算；两个都弹掉会永远对不上。
        recorded_digest = payload.pop("entry_sha256", "")
        recorded_prev = str(payload.get("prev_sha256", ""))
        if entries == 0:
            # 首条的 prev 必须为空（链的起点）。非空说明前面的记录被删掉了——
            # 只校验"本条摘要对不对"是发现不了删头的。
            if recorded_prev:
                return {
                    "ok": False,
                    "entries": 0,
                    "broken_at": number,
                    "reason": "首条 prev 非空（链头被删）",
                }
        elif recorded_prev != prev:
            return {"ok": False, "entries": entries, "broken_at": number, "reason": "prev 不匹配"}
        recomputed = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if recomputed != recorded_digest:
            return {"ok": False, "entries": entries, "broken_at": number, "reason": "摘要不匹配"}
        prev = recorded_digest
        entries += 1
    return {"ok": True, "entries": entries, "broken_at": None}


def run_cleanup(
    reason: str = "manual",
    *,
    steps: list[CleanupStep] | None = None,
    root: Path | None = None,
    clear_session: bool | None = None,
) -> CleanupReport:
    """执行清理并留证。任一步关键步骤失败 → 保留标记（阻止下次启动）。

    ``clear_session``：清理成功后是否删除会话标记。

    - ``None``（默认）：只在会话标记属于**别的进程**时删除。这样崩溃恢复能自动解除
      阻断，而运行中的进程手动跑一次清理不会把"当前这次运行"的崩溃证据删掉；
    - ``True``：无论如何删除。运维在 CLI 里显式恢复时使用——此时"上一个进程是死是活"
      已经由人确认过，工具不必再猜。
    """
    base = Path(root or safety_root())
    base.mkdir(parents=True, exist_ok=True)
    before = snapshot()

    marker = base / MARKER_FILE
    started_at = now_iso()
    marker.write_text(
        json.dumps(
            {"started_at": started_at, "reason": reason, "pid": os.getpid(), "before": before},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    results: list[StepResult] = []
    for step in steps if steps is not None else default_steps():
        try:
            step.action()
        except Exception as exc:  # noqa: BLE001 - 动作失败也要走到验证，让证据完整
            logger.error("清理步骤 %s 执行异常：%s", step.name, exc)
            results.append(
                StepResult(step.name, False, f"执行异常：{type(exc).__name__}: {exc}", step.critical)
            )
            continue
        try:
            ok, detail = step.verify()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"验证异常：{type(exc).__name__}: {exc}"
        results.append(StepResult(step.name, ok, detail, step.critical))

    # not_applicable 步骤如实记录，不计入 ok，也不隐瞒
    for name, why in sorted(NOT_APPLICABLE_STEPS.items()):
        results.append(StepResult(name, True, why, critical=False, status="skipped_not_applicable"))

    after = snapshot()
    ok = all(r.ok for r in results if r.critical and r.status == "ran")

    digest = _append_log(
        {
            "event": "cleanup",
            "reason": reason,
            "ok": ok,
            "started_at": started_at,
            "finished_at": now_iso(),
            "steps": [r.to_dict() for r in results],
            "before": before,
            "after": after,
            "marker_cleared": ok,
        },
        base,
    )

    if ok:
        marker.unlink(missing_ok=True)
        # 清理成功 = 上一个进程的残留已结算，因此**别人**留下的会话标记也要清掉，
        # 否则崩溃恢复后仍然启动不了（会出现"清理成功了但服务还是起不来"）。
        # 本进程自己的会话标记默认保留：它是"当前这次运行"的崩溃证据，
        # 只有正常退出（end_session）或运维显式要求（clear_session=True）才删。
        session = stale_session(base)
        if session is not None and (
            clear_session is True or session.get("pid") != os.getpid()
        ):
            (base / SESSION_FILE).unlink(missing_ok=True)

    return CleanupReport(
        ok=ok,
        steps=results,
        before=before,
        after=after,
        reason=reason,
        log_path=str(base / CLEANUP_LOG),
        marker_cleared=ok,
    )


# --------------------------------------------------------------------------- #
# 启动门
# --------------------------------------------------------------------------- #


def pending_cleanup(root: Path | None = None) -> dict[str, Any] | None:
    """读取未完成的清理标记；没有则返回 ``None``。"""
    path = Path(root or safety_root()) / MARKER_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 标记文件损坏同样视为"有未完成清理"——不能因为读不出内容就当它不存在
        return {"started_at": "未知（标记文件损坏）", "reason": "无法解析"}


def begin_session(root: Path | None = None) -> dict[str, Any]:
    """启动时写会话标记。

    崩溃检测靠的就是它：**正常退出会删掉它**，所以启动时看到它就说明上次没走进
    退出钩子（被杀、崩溃、断电）。只靠"清理开始才写标记"是检测不到崩溃的——
    崩溃发生时清理还没开始，标记也就没写，下次启动会以为一切正常。

    单进程假设：本仓库以单进程方式运行（``uvicorn deepquant.api.app:app``）。
    多 worker 部署时每个进程会互相覆盖该文件，届时需改为按 pid 分文件。
    """
    base = Path(root or safety_root())
    base.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": now_iso(),
        "host": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
        "note": "正常退出时会删除此文件；看到它就说明上次未走退出钩子",
    }
    path = base / SESSION_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return payload


def end_session(root: Path | None = None) -> None:
    """正常退出时删除会话标记。**只有清理成功后才允许调用。**"""
    (Path(root or safety_root()) / SESSION_FILE).unlink(missing_ok=True)


def stale_session(root: Path | None = None) -> dict[str, Any] | None:
    """残留的会话标记（= 上次未正常退出）；没有则 ``None``。"""
    path = Path(root or safety_root()) / SESSION_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"pid": None, "started_at": "未知（会话文件损坏）"}


def assert_clean_start(
    *, root: Path | None = None, allow_dirty: bool | None = None
) -> dict[str, Any]:
    """启动门：未确认清空则拒绝启动。

    两种拒绝理由，都对应用户能采取的动作：

    1. ``cleanup_required.json`` 存在 → 上次清理失败，需检视证据后重跑清理；
    2. ``session.json`` 存在 → 上次进程没走退出钩子（崩溃/被杀），资源状态未知。

    ``allow_dirty=None`` 时读环境变量 ``DEEPQUANT_ALLOW_DIRTY_START``。
    显式知情放行会写一条取证记录——"我知道它是脏的"必须留下痕迹，
    否则这个开关就是下一次事故的免罪符。
    """
    base = Path(root or safety_root())
    marker = pending_cleanup(root)
    session = stale_session(root)

    if marker is None and session is None:
        return {"clean": True, "overridden": False}

    flag = allow_dirty
    if flag is None:
        flag = os.environ.get(ALLOW_DIRTY_START_ENV, "").strip() in {"1", "true", "True", "yes"}
    if not flag:
        if marker is not None:
            raise DirtyShutdown(marker, base / MARKER_FILE)
        assert session is not None
        raise DirtyShutdown(
            {
                "started_at": session.get("started_at", "未知"),
                "reason": (
                    f"上次进程（pid={session.get('pid')}）未走退出钩子，"
                    "资源可能未释放；需执行 `scripts/safety.py cleanup` 后重启"
                ),
            },
            base / SESSION_FILE,
        )

    _append_log(
        {
            "event": "dirty_start_override",
            "at": now_iso(),
            "marker": marker,
            "stale_session": session,
            "note": f"经 {ALLOW_DIRTY_START_ENV} 显式知情后启动，未完成清理仍然存在",
        },
        base,
    )
    logger.warning("检测到未完成的退出清理/上次异常退出，但经 %s 显式放行启动", ALLOW_DIRTY_START_ENV)
    return {
        "clean": False,
        "overridden": True,
        "marker": marker,
        "stale_session": session,
    }


# --------------------------------------------------------------------------- #
# 钩子
# --------------------------------------------------------------------------- #


def install_hooks(*, root: Path | None = None, register_signal: bool = True) -> dict[str, Any]:
    """注册退出钩子：``atexit`` + ``SIGTERM``/``SIGINT``。

    为什么不能只靠"记得手动跑清理"：那正是计划里点名要消灭的做法。
    信号处理器把 SIGTERM 转成正常退出，让 ``atexit`` 有机会跑完整流程——
    否则 ``kill`` 之后什么都不留，下次启动无从判断上次是否干净。
    """
    import atexit

    state: dict[str, Any] = {"atexit": False, "signal": [], "errors": []}

    def _run(reason: str) -> None:
        try:
            report = run_cleanup(reason=reason, root=root)
            if not report.ok:
                logger.error("退出清理未通过，保留标记以阻止下次启动：%s", report.log_path)
        except Exception as exc:  # noqa: BLE001 - 退出路径不得再抛
            logger.error("退出清理执行失败：%s", exc)

    atexit.register(_run, "atexit")
    state["atexit"] = True

    if register_signal:
        def _handler(signum, _frame):  # type: ignore[no-untyped-def]
            _run(f"signal:{signal.Signals(signum).name}")
            raise SystemExit(0)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handler)
                state["signal"].append(sig.name)
            except (ValueError, OSError) as exc:
                # 非主线程 / 已被占用：如实记录，不假装装上了
                state["errors"].append(f"{getattr(sig, 'name', sig)}: {exc}")
    return state


def cleanup_log_entries(root: Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """读取最近若干条清理记录（运维查询）。"""
    path = (root or safety_root()) / CLEANUP_LOG
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"error": "无法解析的行", "raw": line[:200]})
    return out


__all__ = [
    "ALLOW_DIRTY_START_ENV",
    "CLEANUP_LOG",
    "CleanupReport",
    "CleanupStep",
    "DEFAULT_DRAIN_TIMEOUT_S",
    "DRAIN_TIMEOUT_ENV",
    "DirtyShutdown",
    "INFLIGHT",
    "InFlight",
    "MARKER_FILE",
    "NOT_APPLICABLE_STEPS",
    "SAFETY_DIR_ENV",
    "SESSION_FILE",
    "StepResult",
    "assert_clean_start",
    "begin_session",
    "cleanup_log_entries",
    "default_steps",
    "end_session",
    "install_hooks",
    "pending_cleanup",
    "run_cleanup",
    "safety_state_enabled",
    "snapshot",
    "stale_session",
    "verify_log_chain",
]
