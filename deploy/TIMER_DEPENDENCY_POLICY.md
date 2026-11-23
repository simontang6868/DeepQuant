# Timer 依赖顺序规则（施工单 v2 P0-1，冻结 2026-11-22）

## 规则（写死，不靠人记）

> **下游时点 > 上游时点 + 缓冲。** 缓冲至少 15 分钟；若上游实际耗时有观测值，取“观测上限 + 15 分钟”。
> 违反此规则的 timer 必须重排；禁止把“先后看起来对”当成依赖正确。
> 新增/改时点时，本文件表格必须同步更新，否则视为机制变更，需新预登记。

## 当前全部 timer 依赖核对表

| timer | OnCalendar | 依赖的上游产物 | 上游生成时点 | 是否满足 下游 > 上游 + 缓冲 |
|---|---|---|---|---|
| `deepquant-daily.timer` | Mon..Fri 17:10 | 行情源（外部） | — | 上游为外部数据，无链内依赖；OK |
| `deepquant-alert.timer` | Mon..Fri 17:40 | 日更心跳 `forward/heartbeat.json` | 17:10（观测 6–10 min） | OK（30 min > 观测上限 + 缓冲）；若日更真跑满 1h 会误报，这是设计上的早期信号，不掩盖 |
| `deepquant-calendar.timer` | 每月 1 日 09:00 | 交易所日历（外部） | — | OK |
| `deepquant-smoke.timer` | 2026-11-24 17:40 | 11-24 日更台账/最新快照 | 17:10（观测 6–10 min） | OK（30 min）；最坏 1h 会 race，已登记为限制，本单不改时点 |
| `deepquant-probe-connect.timer` | 2026-11-25 09:30 | env/venv/授权/11-24 台账 | 11-24 17:10 | OK |
| `deepquant-probe-buy.timer` | 2026-11-25 09:35 | 09:30 前置自检 + 本人 09:34:59 价格 | 09:30 / 09:34:59 | OK（自检 5 min；价格仅 1 s，属**有意的最晚读价**；缺价即 `MISSING_PRICE` 零报单，失败关闭） |
| `deepquant-probe-carry-liquidation.timer` | 2026-11-24 09:35 | 11-23 X-FILLED-D0 持仓记录（T+1） | 11-23 盘中 | OK（隔夜；09:35 是 T+1 最早可卖时点） |
| `deepquant-probe-min-order.timer` | **2026-11-24 09:35**（补跑新增） | 11-23 独立数据前收 9.150（已落盘）+ 冻结双哈希 | 11-23 收盘 / 11-23 21:30 | OK（隔夜；**本次补的核心缺口就是它——11-23 探针无此 timer 故从未执行**） |
| `deepquant-min-order-carry-1124.timer` | **2026-11-25 09:36**（补跑新增） | 11-24 补跑探针 X-FILLED-D0（T+1） | 11-24 09:35 | OK（隔夜；09:36 比主买腿 09:35 晚 1 min——先让 carry 门 HALT 买腿，再卖 carry，避免带仓执行） |
| `deepquant-probe-sell.timer` | 2026-11-26 10:00/14:00/14:55 | 11-25 买腿成交/持仓（T+1） | 11-25 09:35 | OK（隔夜；第三次留 5 min 日到 14:55） |
| `deepquant-sim-prep.timer` | 2026-11-27 17:20 | 11-27 日更（DRYRUN 用 mock 台账） | 17:10（观测 6–10 min） | OK（10 min）；最坏 1h race，已登记限制，本单不改 |
| `deepquant-sim-preflight.timer` | 2026-11-30 18:00 | 17:10 台账 + 17:40 旁证出证/邮件 | 17:10 / 17:40 | OK（20 min） |
| `deepquant-predict.timer` | **2026-11-30 18:15**（原 17:50） | 18:00 PREFLIGHT 产物 + 11-27 DRYRUN | 18:00 / 11-27 17:20 | **原不满足（早于上游 10 min ⇒ E2 必 FAIL）**；已重排为 18:15，15 min 缓冲 |
| `deepquant-sim-exec.timer` | 2026-12-01 09:35 | 11-30 18:15 六门预测 ARMED | 18:15 前夜 | OK（≈15 h） |
| `deepquant-sim-sentinel.timer` | 2026-12-01 09:45 | 09:35 执行结果文件 | 09:35（观测分钟级） | OK（10 min）；exec 最坏 30 min 会 race，已登记限制，本单不改 |
| `deepquant-sim-reconcile.timer` | 2026-12-01 18:10 | 09:35 执行 + 结算门 | 09:35 起 | OK（8 h+，且有结算门退避） |

## 重排前后对照

| timer | 重排前 | 重排后 | 原因 |
|---|---|---|---|
| `deepquant-predict.timer` | `2026-11-30 17:50:00` | `2026-11-30 18:15:00` | E2 门要求 PREFLIGHT_20261130 存在；原时点早于 18:00 PREFLIGHT 10 分钟，必 FAIL |
