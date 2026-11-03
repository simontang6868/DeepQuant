"""监控告警：五类异常的检测与落盘。

这个模块只做两件事：**判**（纯函数，返回 ``Alert`` 列表）和**落**（sink）。
判定与投递分开，是因为判错要能单测、投递错不该影响判。

五类告警（2.7-a）
---------------
心跳缺失｜数据不新鲜｜订单异常｜对账差异｜回撤接近熔断线。

三条设计取舍，都是"反直觉但正确"的
--------------------------------
1. ⭐⭐ **不漏报优先于不刷屏**。没有做去重窗口：同一个异常连续 10 轮就报 10 条。
   去重能让人清爽，也能让"这个告警一直在响"变成背景噪声——而预警系统的
   唯一价值就是被看见。宁可污染日志，不可静默。
2. ⭐ **每个告警必须带可定位的证据**（``code``/``evidence``）。只说"订单异常"
   等于没说：五类里哪一类、哪只票、哪个原因码，决定了要不要立刻停机。
3. ⭐⭐ **不发送邮件**（2.7-b）。D9 邮箱已定，但发送需要逐次确认，本阶段
   只落盘 + 打印。这条不只是"没写发信代码"——``test_alerts_module_has_no_mail_capability``
   用 AST 扫 import 与调用，任何人把 ``smtplib`` 加进来都会红。
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, Sequence

from deepquant.broker.models import BrokerOrder, BrokerOrderStatus

__all__ = [
    "AlertKind",
    "Severity",
    "Alert",
    "AlertSink",
    "MemorySink",
    "FileSink",
    "PrintSink",
    "MultiSink",
    "HeartbeatState",
    "check_heartbeat",
    "check_data_freshness",
    "check_order_anomalies",
    "check_reconcile",
    "check_drawdown",
    "run_all_checks",
]


class AlertKind(str, Enum):
    HEARTBEAT_MISSING = "heartbeat_missing"
    DATA_STALE = "data_stale"
    ORDER_ANOMALY = "order_anomaly"
    RECONCILE_DIFF = "reconcile_diff"
    DRAWDOWN_NEAR_BREAKER = "drawdown_near_breaker"


class Severity(str, Enum):
    WARN = "warn"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    """一条告警。``code`` 与 ``evidence`` 是为了**可定位**，不是装饰。"""

    kind: AlertKind
    severity: Severity
    ts: int
    message: str
    code: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("告警必须有 message——空的告警比没有更让人困惑")
        if not isinstance(self.kind, AlertKind):
            raise TypeError(f"kind 必须是 AlertKind，收到 {type(self.kind).__name__}")
        if not isinstance(self.severity, Severity):
            raise TypeError(f"severity 必须是 Severity，收到 {type(self.severity).__name__}")

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "ts": self.ts,
            "code": self.code,
            "message": self.message,
            "evidence": dict(self.evidence),
        }

    def to_line(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"))

    def __str__(self) -> str:
        tag = f"[{self.severity.value.upper()}] {self.kind.value}"
        where = f" {self.code}" if self.code else ""
        return f"{tag}{where}: {self.message}"


# --------------------------------------------------------------------------- 投递


class AlertSink(Protocol):
    def emit(self, alert: Alert) -> None: ...


class MemorySink:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def emit(self, alert: Alert) -> None:
        self.alerts.append(alert)

    @property
    def n_critical(self) -> int:
        return sum(1 for a in self.alerts if a.severity is Severity.CRITICAL)


class FileSink:
    """JSONL，**只追加**。告警历史被改写比丢告警更糟。"""

    def __init__(self, path: str | pathlib.Path) -> None:
        self._path = pathlib.Path(path)

    def emit(self, alert: Alert) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(alert.to_line() + "\n")
            fh.flush()

    def read(self) -> tuple[dict[str, Any], ...]:
        if not self._path.exists():
            return ()
        with self._path.open("r", encoding="utf-8", newline="") as fh:
            return tuple(json.loads(l) for l in fh if l.strip())


class PrintSink:
    def __init__(self, stream=None) -> None:
        self._stream = stream

    def emit(self, alert: Alert) -> None:
        print(str(alert), file=self._stream)


class MultiSink:
    def __init__(self, *sinks: AlertSink) -> None:
        self._sinks = list(sinks)

    def emit(self, alert: Alert) -> None:
        for s in self._sinks:
            s.emit(alert)


# --------------------------------------------------------------------------- 1. 心跳


@dataclass(frozen=True)
class HeartbeatState:
    last_success_day: int | None = None
    #: 连续漏跑的交易日数。由调用方按交易日历累计——本模块不知道日历长什么样。
    consecutive_missed: int = 0

    def __post_init__(self) -> None:
        if self.consecutive_missed < 0:
            raise ValueError("consecutive_missed 不得为负")


def check_heartbeat(state: HeartbeatState, *, ts: int,
                    warn_at: int = 1, critical_at: int = 3) -> list[Alert]:
    """心跳缺失。

    ``critical_at`` 默认 3，与前瞻台账的补跑容忍 ``MAX_LATE_RUN_DAYS=3`` 对齐：
    漏到第 3 个交易日就已经踩线，再漏这条证据期记录就没了。
    """
    if state.last_success_day is None:
        return [Alert(AlertKind.HEARTBEAT_MISSING, Severity.CRITICAL, ts,
                      "从未成功运行过：台账里没有任何一次成功记录",
                      evidence={"consecutive_missed": None})]
    n = state.consecutive_missed
    if n <= 0:
        return []
    sev = Severity.CRITICAL if n >= critical_at else Severity.WARN
    tail = ("已超出补跑容忍，该期证据作废" if n >= critical_at
            else "仍在补跑容忍内")
    return [Alert(AlertKind.HEARTBEAT_MISSING, sev, ts,
                  f"连续漏跑 {n} 个交易日（{tail}）",
                  evidence={"consecutive_missed": n,
                            "last_success_day": state.last_success_day,
                            "warn_at": warn_at, "critical_at": critical_at})]


# --------------------------------------------------------------------------- 2. 数据新鲜度


def check_data_freshness(*, data_last_day: int, asof_day: int,
                         trading_days: Sequence[int],
                         max_lag_trading_days: int = 1, ts: int = 0) -> list[Alert]:
    """数据不新鲜。

    ⭐ 用**交易日**而不是自然日：春节休 9 天，按自然日判会把正常情况报成故障；
    而"数据停在 10-16、今天 11-02"这种真问题，按交易日算是 12 个交易日，
    按自然日算 17 天——两个都对，但只有交易日那个能对上「漏了几次运行」。
    """
    if data_last_day >= asof_day:
        return []
    lagging = [d for d in trading_days if data_last_day < d <= asof_day]
    lag = len(lagging)
    if lag <= max_lag_trading_days:
        return []
    sev = Severity.CRITICAL if lag > max_lag_trading_days * 3 else Severity.WARN
    return [Alert(AlertKind.DATA_STALE, sev, ts,
                  f"数据止于 {data_last_day}，落后 {lag} 个交易日"
                  f"（容忍 {max_lag_trading_days}）",
                  evidence={"data_last_day": data_last_day, "asof_day": asof_day,
                            "lag_trading_days": lag,
                            "max_lag_trading_days": max_lag_trading_days})]


# --------------------------------------------------------------------------- 3. 订单异常


def check_order_anomalies(orders: Iterable[BrokerOrder], *, ts: int,
                          stale_order_days: int = 0,
                          current_day: int | None = None) -> list[Alert]:
    """订单异常：拒单、状态未知、长期挂单未终态。

    ⭐ ``UNKNOWN`` 判 CRITICAL 而不是 WARN：它会让闸门拒绝**所有**新单
    （2.4-a），系统实际上已经停摆。报 WARN 会让人以为"还能跑"。
    """
    out: list[Alert] = []
    for o in orders:
        if o.status is BrokerOrderStatus.REJECTED:
            out.append(Alert(
                AlertKind.ORDER_ANOMALY, Severity.WARN, ts,
                f"委托被拒：{o.reject_code or '未给原因'}",
                code=o.code,
                evidence={"client_order_id": o.client_order_id,
                          "reject_code": o.reject_code,
                          "reject_text": o.reject_text}))
        elif o.status is BrokerOrderStatus.UNKNOWN:
            out.append(Alert(
                AlertKind.ORDER_ANOMALY, Severity.CRITICAL, ts,
                "委托状态未知：新单已被闸门阻断，须以券商为准重建",
                code=o.code,
                evidence={"client_order_id": o.client_order_id,
                          "filled_shares": o.filled_shares}))
        elif (current_day is not None and stale_order_days > 0
                and o.status in (BrokerOrderStatus.PENDING_NEW,
                                 BrokerOrderStatus.ACCEPTED)
                and o.updated_ts and current_day - o.updated_ts >= stale_order_days):
            out.append(Alert(
                AlertKind.ORDER_ANOMALY, Severity.WARN, ts,
                f"委托挂 {current_day - o.updated_ts} 个交易日仍未终态",
                code=o.code,
                evidence={"client_order_id": o.client_order_id,
                          "status": o.status.value,
                          "updated_ts": o.updated_ts}))
    return out


# --------------------------------------------------------------------------- 4. 对账差异


def check_reconcile(result, *, ts: int) -> list[Alert]:
    """对账差异。任一条差异即 CRITICAL——``RiskGate`` 会因此拒绝所有新单。

    重复回报单独报一条 WARN：它不是账实不符（去重后一致），但"回报重复"
    本身是故障信号，静默掉就等于放过。
    """
    out: list[Alert] = []
    for d in result.diffs:
        out.append(Alert(
            AlertKind.RECONCILE_DIFF, Severity.CRITICAL, ts,
            f"{d.object}.{d.field} 不一致：本地={d.local!r} 券商={d.broker!r}",
            code=d.code,
            evidence={"kind": d.kind.value, "field": d.field,
                      "local": d.local, "broker": d.broker,
                      "delta": d.delta, "detail": d.detail}))
    for fid in getattr(result, "duplicate_fill_ids", ()):
        out.append(Alert(
            AlertKind.ORDER_ANOMALY, Severity.WARN, ts,
            "回报重复到达（同一 fill_id 出现多次，已去重但需查因）",
            evidence={"fill_id": fid}))
    return out


# --------------------------------------------------------------------------- 5. 回撤接近熔断线


def check_drawdown(*, equity: float, peak_equity: float, breaker_dd: float,
                   warn_ratio: float = 0.8, ts: int = 0) -> list[Alert]:
    """回撤接近熔断线。

    "接近"的定义必须写死：``当前回撤 >= warn_ratio × 熔断线`` 且**尚未触发**。
    没有这个常量，预警就只能是"我觉得快到了"。

    ⚠️ 用**相对峰值**的回撤，不是"相对昨天"。峰值丢失会让回撤重算成 0
    （缺陷预登记 P5），所以 ``peak_equity`` 必须来自持久化的 ``GateState``。
    """
    if peak_equity <= 0:
        return [Alert(AlertKind.DRAWDOWN_NEAR_BREAKER, Severity.CRITICAL, ts,
                      "峰值权益缺失或非正：回撤无法计算，熔断等于失效",
                      evidence={"peak_equity": peak_equity, "equity": equity})]
    dd = 1.0 - equity / peak_equity
    limit = breaker_dd * warn_ratio
    if dd >= breaker_dd:
        return [Alert(AlertKind.DRAWDOWN_NEAR_BREAKER, Severity.CRITICAL, ts,
                      f"回撤 {dd:.4%} 已达熔断线 {breaker_dd:.4%}",
                      evidence={"drawdown": dd, "breaker_dd": breaker_dd,
                                "equity": equity, "peak_equity": peak_equity})]
    if dd >= limit:
        return [Alert(AlertKind.DRAWDOWN_NEAR_BREAKER, Severity.WARN, ts,
                      f"回撤 {dd:.4%} 已达预警线 {limit:.4%}"
                      f"（熔断线 {breaker_dd:.4%} 的 {warn_ratio:.0%}）",
                      evidence={"drawdown": dd, "warn_line": limit,
                                "breaker_dd": breaker_dd,
                                "equity": equity, "peak_equity": peak_equity})]
    return []


# --------------------------------------------------------------------------- 汇总


def run_all_checks(*, ts: int,
                   heartbeat: HeartbeatState | None = None,
                   data: Mapping[str, Any] | None = None,
                   orders: Iterable[BrokerOrder] = (),
                   reconcile_result=None,
                   drawdown: Mapping[str, Any] | None = None,
                   sink: AlertSink | None = None) -> list[Alert]:
    """一次跑完五类检查并投递。

    参数为 ``None`` 表示**不检查这一类**，而不是"这一类没问题"——两者的区别
    必须由调用方自己承担，因为本模块不知道某项输入是否本该存在。
    """
    alerts: list[Alert] = []
    if heartbeat is not None:
        alerts += check_heartbeat(heartbeat, ts=ts)
    if data is not None:
        alerts += check_data_freshness(ts=ts, **data)
    alert_orders = check_order_anomalies(orders, ts=ts)
    alerts += alert_orders
    if reconcile_result is not None:
        alerts += check_reconcile(reconcile_result, ts=ts)
    if drawdown is not None:
        alerts += check_drawdown(ts=ts, **drawdown)

    if sink is not None:
        for a in alerts:
            sink.emit(a)
    return alerts
