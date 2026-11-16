# U16-EXEC-1 实施结果：真实 gm-paper 报单适配器（门控版）

**2026-11-16｜`ENGINEERING_SELF_CHECK_ONLY`｜`EXECUTION_READY_GATED`｜1 页**

## 结论

调仓执行器已实现并通过全部离线验收；**`--execute` 在当前条件下恒 `rc=4`（`NOT_AUTHORIZED`）**。
但它**不是**「可以报单」的许可——四道生效条件未满足，且有三处预登记缺口必须由账户持有人知情（见 §4）。

## 1. 交付物

| 文件 | 说明 | 规模 |
|---|---|---|
| `scripts/sim_rebalance_executor.py` | 执行器：四道门控 + 等权向下取整 + 幂等 cid + 限价 + 只读真值 | 29,471 B |
| `scripts/tests/test_sim_rebalance_executor.py` | 单测 **20 项** | 9,420 B |
| `scripts/sim_rebalance_executor_mutation_test.py` | 真变异 **8 条** | 8,058 B |

## 2. 四道生效条件（任一不过 ⇒ `--execute` rc=4）

| 门 | 判据 | 当前实测 |
|---|---|---|
| **E1 台账** | `ledger/20261130.json` 存在 + `mode=FORWARD` + `target_holds_d4` 非空 | ❌ `LEDGER_MISSING`（老台账不得顶替） |
| **E2 前置** | 11-27 DRYRUN 产物 + 11-30 preflight 产物 | ❌ 两者均缺 |
| **E3 授权** | `paper_authorization.json` 经守卫 `ALLOW 6/6` | ❌ 本地 `NO_AUTH`（云端有但不在本机路径） |
| **E4 隔离+清单** | 隔离 `FAIL 0` + 清单一致 | ✅ 云端 59/59 PASS｜FAIL 0 |

## 3. 验收（本地 + 云端各跑一遍）

| 项 | 本地 | 云端 |
|---|---|---|
| `--self-check` | PASS（10 项） | PASS |
| `--execute` | **rc=4** | **rc=4** |
| `--gates` | **rc=4** | **rc=4** |
| 真变异 | **8/8** | **8/8** |
| 单测 | 20 passed | 20 passed |
| 部署清单 | 59/59 PASS | 59/59 PASS |

回归四连：`backend 3,651 passed / 1 skipped`｜`property 9/9 PASS`｜`scripts/tests 739 passed / 1 skipped`（719 + 新增 20）｜`verify_exec_all PASS`；数据层 `_acceptance.json` / `_asof_manifest.json` 跑前跑后 sha **逐位一致**。清单 57 → **59 文件**（`commit b5f56083`）。

## 4. ⭐ 三个实现限制（预登记缺口，**不改预登记**，按「另立缺陷记录段」处理）

1. **无行情接口 ⇒ 重定价不可实现**。`gmtrade` 该版本 `get_*` 仅
   `cash / positions / orders / execution_reports / unfinished_orders / strerror / version`，**没有 tick/quote**。
   ⇒ 价格只能取台账 `ref_close`（11-30 收盘）；预登记 §2 的「10:30 / 14:30 重定价」**做不到**。
   实现固定为 **单次限价 + `CARRY` 顺延**，代码里 `REPRICE_IMPLEMENTABLE = False`，
   **不伪装成已实现**。⇒ 预登记 P2（重定价次数 ≤2）本次**无法被检验**。
2. **cid 无法透传 ⇒ 服务端幂等不可控**。`order_volume(symbol, volume, side, order_type,
   position_effect, price=0, order_duration=0, order_qualifier=0, account=None)` **没有
   `client_order_id` / `order_remark` 入参**。⇒ 幂等只在**本地**生效（`append_orders` 按 cid 去重 +
   发送前查重）；预登记 P1 假设的「服务端按 cl_ord_id 去重」**未经证实**，首次真实执行须专门验证。
3. **隔离检查器不覆盖执行器**。`check_channel_isolation.py` 的白名单只扫
   `backend/src/deepquant/broker/*.py`（5 个）与「只读适配器无 `order_*`」。
   ⇒ 执行器里的 `gm.order_volume(` **不在其覆盖内**，`FAIL 0` 不能读作「执行器已过隔离检查」。
   执行器的约束来自四道门控 + `--execute` 门控恒关，不来自该检查器。

## 5. 八条真变异（全部 DETECTED，恢复后 sha256 一致）

| # | 变异 | 被检出的方式 |
|---|---|---|
| M1 | 幂等键掺随机盐 | 同四元组两次 cid 不同 |
| M2 | 台账缺失也放行 | 不存在路径返回 ok=True |
| M3 | `NO_AUTH` 分支失效 | 无授权文件也 ok=True |
| M4 | 向下取整改向上 | 600036 从 100 股变 200 股（超预算） |
| M5 | 跳过清非目标 | `000001` 不被卖出 |
| M6 | 超预算静默 | 无 `OVER_BUDGET` 警告 |
| M7 | 门控不过返回 0 | `--execute` 假成功 |
| M8 | E4 整体恒通过 | 隔离/清单失败仍放行 |

## 6. 边界

未报单、未创建/修改任何授权文件、未改策略参数 / 预登记判据 / G2 定义 / `daily_chain.sh`、
未动 token、未发 ping、`LIVE_ENABLED` 保持未设置、**未提交或推送 git**（4 个新文件未跟踪，
`HEAD=origin=487fcc8`）。预登记正文未改；本文件只作缺陷记录与验收登记。

## 7. 引用规范

引用本单必带：`U16-EXEC-1` + 预登记 sha `53ace8c5…` + `StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`
+ `MaturityLevel=EXECUTION_READY_GATED` + `MDE_NOT_ESTIMABLE`。
`SIM_DRILL` 结果**不构成样本外证据**、不进 G1/G2/S4、不作经济性判断。
