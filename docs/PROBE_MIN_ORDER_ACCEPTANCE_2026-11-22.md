# 最小下单探针验收标准（PA-GM-PROBE-3 增补，冻结 2026-11-22 21:48 CST）

> 性质：H1 授权的**验收标准**，不是新纪律、不是新因子。原预登记 `PROBE_MIN_ORDER_PREREG_2026-11-23.md`（sha `ccbc420d…`）**保留不改写**；本文件是独立冻结增补，脚本同时校两个哈希。
> 来源：账户持有人 2026-11-22 21:48 书面消息 §五；同日 23:59 施工单 v2 P0-2 修正 X-1：
> A 股主板 T+1，“立即反向卖出”在成交当日物理不可执行，改为 D0 只记录 / D1 次日卖出。
> 原 X-1 授权中的“反向卖出”意图保留，但执行时点改为 T+1 最早可卖时点；其余限制不变。

---

## 验收标准（全满足 = PASS，任一不符 = FAIL）

```
最小下单探针 验收标准（全满足=PASS，任一不符=FAIL）

前置：
  P-1  前收价取自 11-21 收盘（上一交易日），非 11-22
  P-2  601398/600000 均为主板 ⇒ 跌停 = 前收 × 0.90（ST 为 ×0.95，非主板不同）
  P-3  跌停价已按 A 股价格最小变动单位（0.01）向下取整

执行：
  E-1  place_limit_order 返回 order_id（非空、非异常）
  E-2  记录委托状态（pending / filled / rejected）
  E-3  30 秒内 cancel_order，记录撤单返回

验收：
  V-1  positions == 0        ← 未成交
  V-2  available 恢复至 100000  ← 冻结资金释放
  V-3  订单列表无残留 pending
  V-4  产物落盘 forward/probe_min_order_2026-11-23.json

异常分支（FAIL 之外，必须单独记录）：
  X-1  D0（11-23 成交当日）：A 股主板 T+1，当日不可卖出。只记录
       symbol / qty / fill_price / order_id / filled_at，probe_carry=true，
       V-1 置 SKIP（不判 FAIL），单独落 X-FILLED-D0 分类。
  X-1  D1（11-24 09:35 后，T+1 最早可卖时点）：由 probe_automation
       liquidate-probe-carry 卖出全部 probe_carry；成功 probe_carry=false，
       失败（跌停/停牌/拒单）保持 true 并升 P0；11-24 收盘仍 true ⇒ 11-25 PROBE 顺延。
  X-2  若 cancel 返回失败 ⇒ 记录为 X-CANCEL-FAIL，人工核委托
  X-3  若 available 未恢复 ⇒ 记录为 X-FREEZE，人工核资金冻结
```

## P-1 的事实校正（照实登记，不偷偷改字面）

`2026-11-21` 是**周六**，不是交易日；执行机独立数据 `data/exec_all/601398.SH.exec.csv` 里最新交易日是 `2026-11-20`（收盘 `9.400`）。故脚本按“上一交易日”取 **11-20**，并在产物中同时记录 `requested_prev_close_date=20261121`（字面值）与 `prev_close_date=20261120`（独立数据推导值）及冲突说明。若 11-21 另有交易数据出现，以独立数据为准并重新冻结本增补。

## 验收边界（不得过度解读）

探针验证：权限 / 白名单 / 额度 / 下单链路 / 撤单链路。
探针**不验证**：11-25 最新价参数能否被接受、预登记标的成交概率、撤单后成交回报字段完整性。

⇒ 正确表述是 **“探针 PASS ⇒ 11-25 的失败只可能来自价格或市场”**，不是“11-25 一定成”。

## 产物字段

`forward/probe_min_order_2026-11-23.json` 必须含 `acceptance` 对象：逐条列出 P-1/P-2/P-3/E-1/E-2/E-3/V-1/V-2/V-3/V-4 的 `status`（PASS/FAIL/SKIP）与 `observed`，以及 `exceptions` 列表（X-1/X-2/X-3）和 `decision`。
