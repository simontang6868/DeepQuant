# PROBE 最小下单探针·补跑预登记（PA-GM-PROBE-3B，冻结于任何实现代码之前）

> 冻结时间：2026-11-23 21:30 CST｜编号 `PA-GM-PROBE-3B`｜执行日 **2026-11-24**（仅此一天）
> 来源：账户持有人 2026-11-23 21:27 书面授权「补跑探针」；自审 `reports/SELF_AUDIT_2026-11-23.md` 发现 11-23 探针 GO 后无触发器、窗口已过。
> **本预登记是 new experiment，不是 11-23 的续跑**：执行日、前收价、跌停价、产物路径全部改变；11-23 预登记 `ccbc420d…` 与验收 `0fcb4c39…` 一字不改、继续冻结。
> 本预登记**不写自身哈希**；侧车 `.sha256` 独立冻结。任何参数变更 = 再新预登记，不在本文件上改字。
> 性质：**仅工程验证**，`StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`、`MaturityLevel=PROBE_ORDER_PATH_ONLY`；不计 G1/G2/S4，不进可引集合，不产生任何策略/收益结论。

---

## 1. 目的、预期方向与可算量

**目的**：11-23 探针未执行 ⇒ 下单/撤单链路从未真实穿过。用一笔必然不成交的跌停价买单在 11-24 真实穿过链路，把 11-25 主 PROBE 的失败面收敛到市场与人。

| 可算量 | 预期方向 | 机制 |
|---|---|---|
| `place_limit_order` 返回 | `order_id` 非空、无异常 | 权限/白名单/额度/数量门在 login 之后、下单时才校验 |
| 下单前 `get_orders()` | `[]` | 账户不应有在途单；非空即 HALT，**不撤他人单** |
| 撤单后 `get_orders()` | `[]` | `gm.order_cancel` 真撤掉本单 |
| 撤单后 `get_positions()` | 与下单前相同（预期 `[]`） | 跌停价买单当日不成交 |
| 撤单后 `available` | 回到 100000（记录型，V-2 有 0.01 容差） | 冻结资金释放 |
| `n_unexplained` | 0 | 只发 1 单、只撤 1 单 |

⚠️ **不承诺一定不成交**：若跌停价买单成交，按 T+1 三段分支处置（§4），不自动当日卖出。

---

## 2. 参数硬编码（启动校验，任何变更 = 再新预登记）

| 参数 | 值 | 来源 |
|---|---|---|
| 执行日 | `2026-11-24`（日期门，仅此一天） | 本人 11-23 补跑授权；11-24 已过交易日历核对 |
| 标的 | `601398`（沪市主板，11-25 主 PROBE 同标的） | 复用 PA-GM-PROBE-2 |
| 方向/数量 | `BUY` / **100 股（1 手）** | 沿用 11-23 预登记 |
| 前收价 | **9.150**（取 11-23 收盘，上一交易日） | 执行机独立数据 `data/exec_all/601398.SH.exec.csv`，11-23 行 `close=9.150` |
| 价格 | `Decimal('9.150') × 0.90 = 8.235` → **ROUND_FLOOR(0.01) = `8.23`** | 主板跌停 ×0.90；按最小变动单位向下取整（不四舍五入） |
| 日期一致性 | 字面 previous-date = 实际 = `20261123`；**无 11-21 那类周末冲突** | 11-23 经 `trade_calendar.json` 确认为交易日 |
| 单笔名义 | `8.23 × 100 = 823.00 元` ≤ 4000 且 ≤ `max_notional=100000` | 沿用预算 |
| 授权动作 | `sim_rebalance_order`（复用既有 `paper_authorization.json`，**不创建/不修改**） | 书面签署件 |
| 额度门 | guard 六项 + **实时 `available`**（禁止写死）+ `cumulative=0` | `PAPER_AUTH_GUARD_PREREG` §5 |
| 账户锚 | `DQ_GM_ACCOUNT_ID` 掩码 `0a47****************************2aa6`；`get_cash().account_id` 必须等于 env；授权标签 `虚拟盘测试202611` | 11-22 现场核对 |
| 通道 | `gm-paper`；`LIVE_ENABLED=false`；禁止实盘 | 签署件 |
| 解释器/env | `/opt/quant/.venv-gm/bin/python` + `set -a; source /opt/quant/env; set +a` + `cd /opt/quant` | 11-22 三真坑 |
| cid | `PROBE-MIN-20261124-601398-BUY-100` | 本地标识，SDK 不透传 |
| 触发器 | `deepquant-probe-min-order.timer`，OnCalendar `2026-11-24 09:35:00`，**Persistent=false**，真实 systemd timer（**11-23 缺的就是这个**） | 本次补跑硬要求 |

---

## 3. 验收标准（全满足=PASS，任一不符=FAIL）

前置：
- P-1 前收价取自 **11-23 收盘**（上一交易日），字面=实际=`20261123`，无需周末回退
- P-2 `601398` 沪市主板 ⇒ 跌停 = 前收 × 0.90（ST 为 ×0.95，非主板不同）
- P-3 跌停价按 0.01 **向下取整**：`9.150×0.9=8.235 → 8.23`

执行：
- E-1 `place_limit_order` 返回 order_id（非空、非异常）
- E-2 记录委托状态（pending / filled / rejected）
- E-3 30 秒内 cancel_order，记录撤单返回

验收：
- V-1 positions == 0（未成交）
- V-2 available 恢复至 100000（±0.01）
- V-3 订单列表无残留 pending
- V-4 产物落盘 `forward/probe_min_order_2026-11-24.json`

异常分支（FAIL 之外，单独记录）：
- **X-1 D0（11-24 成交当日）**：T+1 禁止卖出；只记 symbol/qty/fill_price/order_id/filled_at，`probe_carry=true`，V-1 置 SKIP，落 `X-FILLED-D0`；**零卖单**
- **X-1 D1（11-25 09:36 之后）**：由 `deepquant-min-order-carry-1124.timer` 卖出全部 probe_carry；成功 carry=false，失败保持 carry=true 并升 P0
- X-2 cancel 失败 ⇒ `X-CANCEL-FAIL`，人工核委托
- X-3 available 未恢复 ⇒ `X-FREEZE`，人工核资金冻结

**顺延联动**：若 11-24 X-FILLED ⇒ 11-25 09:30 preflight 的 `GATE_HALT_PROBE_CARRY` 会拦下主 PROBE 买腿（不 credited、不带仓执行）；11-25 主 PROBE 顺延，11-24 探针的 carry 由 D1 timer 处理。

---

## 4. 边界（别过度解读）

探针验证：权限 / 白名单 / 额度 / 下单链路 / 撤单链路。
探针**不验证**：11-25 实际价格参数能否被接受（本探针用跌停价，11-25 用最新价）；预登记标的成交概率；撤单后成交回报字段完整性。
正确表述：**探针 PASS ⇒ 11-25 的失败只可能来自价格或市场**，不是「11-25 一定成」。

---

## 5. 交易日历核对声明

本文件所载日期 `20261124`（执行）、`20261123`（前收/上一交易日）、`20261125`（D1 顺延联动）已与独立交易日历 `research/stock_pick_2026-10-25/forward/trade_calendar.json` 逐个核对，核对表见 `forward/trading_day_check_20261122.json`（35 行、覆盖至 20261231）。三日均 `是否交易日=true`。

> 11-23 预登记的字面 `11-21`（周六）冲突已独立登记于自审报告，**未在本文件复现**；本文件前收日期字面=实际，无冲突。
