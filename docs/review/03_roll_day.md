# 审查包 3 · `roll_day` 日切

> 单列一个包，因为它是本轮**唯一被查出两条真缺陷**的函数，且缺陷类型是"静默失效"。

## 1. 源码

`backend/src/deepquant/execution/risk_gate.py:239` `GateState.roll_day(day: int)`

```python
def roll_day(self, day: int) -> None:
    if day < self.trading_day:
        raise ClockWentBackwards(...)
    if day == self.trading_day:
        return
    self.trading_day = day
    self.daily_order_count = 0
    # has_unknown_orders 不清：它是券商侧事实，不是当日状态
```

## 2. 修掉的两条缺陷

| # | 原实现 | 后果 | 现实现 |
| --- | --- | --- | --- |
| 1 | `if day != self.trading_day:` | **日期倒退也被当成跨日** ⇒ 当日委托笔数清零 ⇒ 第 3 条硬限（单日笔数）当场失效。时钟漂移（10-30 读成 10-29）就能触发 | 倒退抛 `ClockWentBackwards`；同日 no-op |
| 2 | 跨日 `has_unknown_orders = False` | 昨天丢了一笔回报，今天系统"自动痊愈"继续下单，而账实从那一刻起就错了 | **不清**。只有券商数据能解封 |

第 2 条的危险在于它**看起来像是清理**——"新的一天，把所有当日状态重置"是几乎所有人的直觉。

## 3. 测试

| 测试 | 断言 |
| --- | --- |
| `test_new_day_resets_counter` | 跨日清零笔数 |
| `test_10c_same_day_roll_is_noop` | 同日调用不清零 |
| `test_10b_rolling_to_next_day_keeps_unknown_orders` | 跨日**不**清 `UNKNOWN` |
| `test_10_clock_drift` | 日期倒退抛 `ClockWentBackwards`，笔数不变，`UNKNOWN` 不变 |
| `test_09b_restart_does_not_reset_daily_order_count_within_same_day` | 重启不等于跨日 |

变异体：`test_M7_daily_count_gt_instead_of_ge_is_caught`（笔数边界）、M8（熔断边界）。

## 4. 已知缺陷 / 未覆盖

| # | 内容 | 影响 |
| --- | --- | --- |
| D-1 | `ClockWentBackwards` 目前**只抛异常**，没有"记录到台账 + 告警"的联动 | 中。异常被上层吞掉就没人知道发生过 |
| D-2 | 没有"跨月"语义：月度调仓依赖日期，但 `roll_day` 只认交易日整数 | 低。月末判定在 `p13_forward` 侧做 |
| D-3 | 未处理**时区**：`trading_day` 是整数日期，由调用方给；若服务器时区不是 Asia/Shanghai，给进来的日期会错一天 | 中。部署清单已强制时区，但这属于"靠部署正确"而非"代码保证" |

## 5. 建议重点质疑

1. 日期倒退是否应该**直接停机**（置 `halt`），而不是只抛异常让调用方决定？
2. 时间源只有一个（系统时钟）。若时钟被 NTP 回拨，是否会连续触发 `ClockWentBackwards` 而把当天卡死？
