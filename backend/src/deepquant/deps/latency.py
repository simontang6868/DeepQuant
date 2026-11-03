"""分通道延迟档案：按日采样、可直接导出 P50/P90/P99。

为什么延迟要单独建档，而不是记在日志里
--------------------------------------
计划 4.4 的三条结论（队列位置决定成交概率、行情延迟与委托延迟是两条分布、
延迟是分布问题而非参数问题）都指向同一件事：**执行成本模型需要分通道的延迟分布**，
而不是一个滑点常数。日志里的一行"耗时 1.3s"无法回答"P99 是多少"，
而 P99 才是决定尾部风险的那个数。

因此本模块做三件事：
1. 按 (kind, channel) 采样并**按日落盘**（可回溯到某一天某通道的表现）；
2. 提供分位数计算（**nearest-rank**，口径写死在文档与测试里）；
3. 导出可直接被成本模型消费的结构。

采样限流：准确性与磁盘的折中
----------------------------
逐次采样在批量拉取（数千标的）时会写出几十万行，收益极低——延迟分布由量级决定，
不需要每一次都记。因此同一通道在 ``min_interval_s`` 内只采一个样本。
**代价必须说明**：限流会让样本在时间上不均匀，若延迟存在强日内节律，
限流后的分位数会偏向被采到的时段。当前用途（设定模拟盘偏差阈值）对这一偏差不敏感；
若将来要做日内节律分析，应关掉限流（``min_interval_s=0``）并对特定时段专门采样。

分位数的口径
------------
采用 nearest-rank：排序后取第 ``ceil(p/100 * n)`` 个（1-based）。
不用线性插值，理由是插值会给出**实际没观测到的值**，而"这条通道 99% 的调用
不超过 X 毫秒"这句话里的 X 最好真的被观测到过。
副作用：``n < 100`` 时 p99 恒等于最大值——如实反映"样本不够，别拿 p99 当结论"。
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

#: 采样目录（相对依赖状态目录）。
LATENCY_DIR = "latency"

#: 默认限流间隔：同一通道每秒最多一个样本。
DEFAULT_MIN_INTERVAL_S = 1.0


def _day_of(occurred_at: str) -> str:
    """从 ISO 时间取 UTC 日期（YYYYMMDD）。解析失败退化为当天，不抛异常。"""
    try:
        dt = datetime.fromisoformat(occurred_at)
    except (TypeError, ValueError):
        dt = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y%m%d")


@dataclass(frozen=True)
class LatencySample:
    kind: str
    channel: str
    latency_ms: float
    ok: bool
    occurred_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "channel": self.channel,
            "latency_ms": self.latency_ms,
            "ok": self.ok,
            "occurred_at": self.occurred_at,
        }


def percentile(values: Sequence[float], p: float) -> float | None:
    """nearest-rank 分位数。空序列返回 ``None``（不返回 0 冒充"很快"）。"""
    if not values:
        return None
    if not 0 < p <= 100:
        raise ValueError(f"分位数必须在 (0, 100] 内，收到 {p}")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return float(ordered[rank - 1])


class LatencyArchive:
    """分通道延迟采样与分位数导出。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
    ) -> None:
        self.root = None if root is None or str(root).strip() == "" else Path(root)
        self.min_interval_s = float(min_interval_s)
        self._last: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------------ #
    # 采样
    # ------------------------------------------------------------------ #
    def _should_sample(self, kind: str, channel: str) -> bool:
        if self.min_interval_s <= 0:
            return True
        now = self._now()
        last = self._last.get((kind, channel))
        if last is not None and (now - last) < self.min_interval_s:
            return False
        self._last[(kind, channel)] = now
        return True

    @staticmethod
    def _now() -> float:
        import time

        return time.monotonic()

    def record(
        self,
        *,
        kind: str,
        channel: str,
        latency_ms: float,
        ok: bool,
        occurred_at: str | None = None,
        force: bool = False,
    ) -> bool:
        """记录一个样本。返回是否真的记录（被限流则返回 False）。

        失败样本同样记录（``ok=False``）：只统计成功的调用会系统性低估延迟，
        而"失败有多慢"恰恰是判断该切换还是该等待的关键。
        """
        if not force and not self._should_sample(kind, channel):
            return False
        occurred_at = occurred_at or datetime.now(UTC).isoformat(timespec="microseconds")
        sample = LatencySample(
            kind=kind,
            channel=channel,
            latency_ms=round(float(latency_ms), 3),
            ok=bool(ok),
            occurred_at=occurred_at,
        )
        self._store(sample)
        return True

    def _store(self, sample: LatencySample) -> None:
        if self.root is None:
            return
        path = self.root / LATENCY_DIR / f"latency-{_day_of(sample.occurred_at)}.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(sample.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            # 延迟档案写不进去不影响业务：它服务于未来的成本模型，不是当前请求的前提
            logger.error("延迟样本落盘失败：%s", exc)

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def days(self) -> list[str]:
        """已有档案的日期列表（升序）。"""
        if self.root is None:
            return []
        directory = self.root / LATENCY_DIR
        if not directory.is_dir():
            return []
        return sorted(
            p.stem.replace("latency-", "") for p in directory.glob("latency-*.jsonl")
        )

    def samples(
        self,
        *,
        day: str | None = None,
        kind: str | None = None,
        channel: str | None = None,
    ) -> list[dict[str, Any]]:
        """读取样本。``day=None`` 表示读取全部日期。"""
        if self.root is None:
            return []
        directory = self.root / LATENCY_DIR
        if not directory.is_dir():
            return []
        files = (
            [directory / f"latency-{day}.jsonl"]
            if day
            else sorted(directory.glob("latency-*.jsonl"))
        )
        out: list[dict[str, Any]] = []
        for path in files:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8", newline="") as fh:
                for line_no, line in enumerate(fh, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        item = json.loads(text)
                    except json.JSONDecodeError:
                        logger.error("延迟档案 %s 第 %d 行损坏，已跳过", path.name, line_no)
                        continue
                    if kind is not None and item.get("kind") != kind:
                        continue
                    if channel is not None and item.get("channel") != channel:
                        continue
                    out.append(item)
        return out

    # ------------------------------------------------------------------ #
    # 分位数与导出
    # ------------------------------------------------------------------ #
    def percentiles(
        self,
        *,
        day: str | None = None,
        kind: str | None = None,
        channel: str | None = None,
        ok_only: bool = False,
    ) -> dict[str, Any]:
        """计算 P50/P90/P99。

        默认**包含失败样本的耗时**（``ok_only=False``）：一次 30 秒超时也是这条通道
        真实的延迟表现，把它剔除会让档案显得比实际更可靠。
        """
        items = self.samples(day=day, kind=kind, channel=channel)
        if ok_only:
            items = [i for i in items if i.get("ok")]
        values = [float(i["latency_ms"]) for i in items if isinstance(i.get("latency_ms"), (int, float))]
        return {
            "n": len(values),
            "p50": percentile(values, 50),
            "p90": percentile(values, 90),
            "p99": percentile(values, 99),
            "max": max(values) if values else None,
            "min": min(values) if values else None,
            "ok_only": ok_only,
            "day": day,
            "kind": kind,
            "channel": channel,
        }

    def export(self, *, days: Iterable[str] | None = None) -> dict[str, Any]:
        """导出「按日 × 按通道」的分位数表，供成本模型直接消费。"""
        target_days = list(days) if days is not None else self.days()
        table: dict[str, dict[str, dict[str, Any]]] = {}
        for day in target_days:
            items = self.samples(day=day)
            channels: dict[str, dict[str, Any]] = {}
            keys = sorted({(i.get("kind", ""), i.get("channel", "")) for i in items})
            for kind, channel in keys:
                subset = [i for i in items if i.get("kind") == kind and i.get("channel") == channel]
                values = [float(i["latency_ms"]) for i in subset if isinstance(i.get("latency_ms"), (int, float))]
                channels[f"{kind}.{channel}"] = {
                    "n": len(values),
                    "n_failed": sum(1 for i in subset if not i.get("ok")),
                    "p50": percentile(values, 50),
                    "p90": percentile(values, 90),
                    "p99": percentile(values, 99),
                }
            table[day] = channels
        return {
            "method": "nearest-rank",
            "note": "n<100 时 p99 等于最大值；失败调用计入延迟分布",
            "days": target_days,
            "table": table,
        }


__all__ = [
    "DEFAULT_MIN_INTERVAL_S",
    "LATENCY_DIR",
    "LatencyArchive",
    "LatencySample",
    "percentile",
]
