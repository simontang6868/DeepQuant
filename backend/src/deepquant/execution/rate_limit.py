"""申报速率限制：令牌桶，**逻辑时钟驱动、可持久化**。

为什么申报表上的速率必须由代码产生
--------------------------------
2.10-b 要求「速率/笔数上限来自配置，不是编的」。如果系统里根本没有限速器，
那申报表上填的任何数字都是承诺而非事实——而程序化交易报告是监管事项，
把承诺当事实填进去，性质就不一样了。

所以这里实现一个能被测出上界的限速器：``orders_per_second_upper_bound()``
给出配置对应的理论上界，``test_measured_rate_never_exceeds_configured``
用实测流量验证它。

三个必须做对的地方
----------------
1. ⭐⭐ **逻辑时间，不读 wall clock**。``acquire(ts)`` 的 ``ts`` 由调用方给。
   读 ``time.time()`` 会让限速不可重放，也让单测必须 ``sleep``。
2. ⭐⭐ **状态可持久化**。令牌桶存在内存里，重启就满血复活——重启刷单是
   最容易被忽略的一条绕过路径，因为它只在崩溃后的那几秒出现。
3. ⭐ **时间倒退不清空也不补满**。时钟漂移导致 ``ts < last_ts`` 时按"不补充"
   处理，并把次数记下来：清空会让它可以被用来无限刷，补满等于送它一整桶。

⚠️ 当前接入状态（**必须与申报材料一致，不得夸大**）
------------------------------------------------
本模块已实现并有测试，但**尚未接入执行循环**——因为 Phase 2 根本没有会发单的
循环（SHADOW 不发送、LIVE 未启用）。接入位置是 Phase 3 的「取单 → 限速 → 发单」。
在材料里必须写「已实现、未接入运行时」，不能写成"运行时保证"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

__all__ = ["RateLimiter", "RateLimitExceeded", "PERMIT_EPS"]

#: 令牌比较容差。补满恰好 1.0 个令牌时浮点可能是 0.9999999999999999。
#: 1e-9 在 rate ≤ 1e6/s 时远大于舍入误差、远小于任何有意义的"差一个令牌"。
PERMIT_EPS = 1e-9


class RateLimitExceeded(RuntimeError):
    """速率超限。"""


@dataclass(frozen=True)
class _Snapshot:
    tokens: float
    last_ts: float
    granted: int
    denied: int
    regressions: int


class RateLimiter:
    """令牌桶。

    ``rate_per_second`` 是稳态速率，``burst`` 是桶容量（允许的瞬时突发）。
    桶初始为满：第一次调用不必等待——否则开盘第一笔会莫名被拒，
    而那时正是最需要发单的时刻。
    """

    def __init__(self, *, rate_per_second: float, burst: int) -> None:
        if not (rate_per_second > 0):
            raise ValueError(f"rate_per_second 必须为正，收到 {rate_per_second}")
        if not isinstance(burst, int) or burst < 1:
            raise ValueError(f"burst 必须是 >=1 的整数，收到 {burst!r}")
        self._rate = float(rate_per_second)
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._last_ts: float | None = None
        self._granted = 0
        self._denied = 0
        self._regressions = 0

    # ---------------------------------------------------------------- 只读
    @property
    def rate_per_second(self) -> float:
        return self._rate

    @property
    def burst(self) -> int:
        return int(self._capacity)

    @property
    def tokens(self) -> float:
        return self._tokens

    @property
    def granted(self) -> int:
        return self._granted

    @property
    def denied(self) -> int:
        return self._denied

    @property
    def clock_regressions(self) -> int:
        """逻辑时钟倒退被观测到的次数。**非零是要人看的信号**。"""
        return self._regressions

    def orders_per_second_upper_bound(self) -> float:
        """理论上界：任意一秒内最多 ``burst + rate×1`` 笔（含跨秒的突发）。

        申报表填的应该是**这个数**，而不是 ``rate_per_second``——填报"稳态速率"
        会低估瞬时能力，而监管关心的是瞬时。
        """
        return self._capacity + self._rate

    def steady_state_orders_per_second(self) -> float:
        """长期平均上限 = ``rate_per_second``。"""
        return self._rate

    # ---------------------------------------------------------------- 核心
    def _refill(self, ts: float) -> None:
        if self._last_ts is None:
            self._last_ts = ts
            return
        if ts < self._last_ts:
            # ⚠️ 倒退：不补充、不清空。清空 ⇒ 可被用来无限刷；补满 ⇒ 送一整桶。
            self._regressions += 1
            self._last_ts = ts
            return
        elapsed = ts - self._last_ts
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_ts = ts

    def try_acquire(self, ts: float, *, n: int = 1) -> bool:
        """尝试取 ``n`` 个令牌。取不到返回 ``False``（**不抛异常**）。"""
        if n < 1:
            raise ValueError(f"n 必须 >= 1，收到 {n}")
        if n > self._capacity:
            raise ValueError(
                f"单次请求 {n} 个令牌超过桶容量 {self._capacity:.0f}——"
                "这个请求永远不可能被满足，属于配置错误而非限速")
        self._refill(float(ts))
        if self._tokens + PERMIT_EPS >= n:
            self._tokens -= n
            # 防止浮点把 tokens 压成极小的负数
            if self._tokens < 0:
                self._tokens = 0.0
            self._granted += n
            return True
        self._denied += n
        return False

    def acquire(self, ts: float, *, n: int = 1) -> None:
        """取不到就抛。供"宁可报错也不要静默不发"的调用方使用。"""
        if not self.try_acquire(ts, n=n):
            raise RateLimitExceeded(
                f"速率超限：需要 {n} 个令牌，当前 {self._tokens:.3f}；"
                f"稳态 {self._rate}/s、容量 {self._capacity:.0f}")

    def retry_after(self, ts: float, *, n: int = 1) -> float:
        """还需要等多少秒才能取到 ``n`` 个令牌（0 表示现在就可以）。"""
        self._refill(float(ts))
        if self._tokens + PERMIT_EPS >= n:
            return 0.0
        need = n - self._tokens
        return max(0.0, need / self._rate)

    # ---------------------------------------------------------------- 持久化
    def to_dict(self) -> dict[str, Any]:
        return {
            "rate_per_second": self._rate,
            "burst": int(self._capacity),
            "tokens": self._tokens,
            "last_ts": self._last_ts,
            "granted": self._granted,
            "denied": self._denied,
            "regressions": self._regressions,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RateLimiter":
        """从落盘状态恢复。**桶不满，按落盘时的令牌数继续。**

        不恢复 ``tokens`` 就等于重启即满血——这是最容易被忽略的绕过路径。
        """
        obj = cls(rate_per_second=float(d["rate_per_second"]),
                  burst=int(d["burst"]))
        tokens = float(d.get("tokens", obj._capacity))
        # 落盘值超出容量说明配置被改小了：夹回容量，不报错（否则重启就起不来）
        obj._tokens = min(max(tokens, 0.0), obj._capacity)
        last = d.get("last_ts")
        obj._last_ts = None if last is None else float(last)
        obj._granted = int(d.get("granted", 0))
        obj._denied = int(d.get("denied", 0))
        obj._regressions = int(d.get("regressions", 0))
        return obj

    def snapshot(self) -> _Snapshot:
        return _Snapshot(self._tokens, self._last_ts or 0.0, self._granted,
                         self._denied, self._regressions)

    def __repr__(self) -> str:
        return (f"RateLimiter(rate={self._rate}/s, burst={int(self._capacity)}, "
                f"tokens={self._tokens:.3f})")


def orders_per_second_upper_bound(rate_per_second: float, burst: int) -> float:
    """便捷函数：由配置算出申报表要填的"每秒最大笔数"。"""
    return float(burst) + float(rate_per_second)


def max_orders_in_window(rate_per_second: float, burst: int, seconds: float) -> float:
    """任意长度 ``seconds`` 的窗口内最多能发多少笔。

    用于回答"开盘那一分钟最多发几笔"。``ceil`` 是因为令牌按需补充、
    最后一笔可能恰好卡在窗口末端。
    """
    if seconds < 0:
        raise ValueError("seconds 不得为负")
    return math.floor(float(burst) + float(rate_per_second) * seconds + PERMIT_EPS)
