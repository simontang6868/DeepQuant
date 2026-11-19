# PROBE 执行预登记（PA-GM-PROBE-1 执行版，冻结于任何解锁代码之前）

> 编号 `PA-GM-PROBE-2`｜冻结时间 2026-11-20 00:30 CST（先于任何实现代码）
> 上游：`docs/GM_PAPER_PROBE_PREREG_2026-11-15.md`（`PA-GM-PROBE-1`，设计冻结）、
> 专家复审 Q4（go/no-go 前置 + 五项约束）、专家答复 §二（现金记录字段）、
> 授权 `FULLAUTO_GO_statement_2026-11-20.md`（sha `463bd735…`）
> 本预登记**不写自身哈希**；侧车 `.sha256` 独立冻结。

---

## 1. 研究类型与边界（照 PA-GM-PROBE-1，不扩写）

- **工程连通性验证（PROBE）**，非预测／非归因／非经济性评估。
- **PROBE 排除出 G1/G2/S4 与一切经济性判断**；不进 `RESULTS_INDEX.md` 可引集合；不写周报结论。
- `LIVE_ENABLED=false`；不碰任何授权文件原文；不改策略参数；不新增因子。
- `StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`；`MaturityLevel=PROBE_CONNECTIVITY_ONLY`。

## 2. 目的与预期方向（可算量，非收益）

**目的**：打通"真实下单 → 服务端成交回报 → 对账"端到端路径，暴露只有真实数据流过才暴露的 bug（H9 同类）。

**预期方向与机制**（落在可算量）：
| 可算量 | 预期方向 | 机制 |
|---|---|---|
| 买腿+卖腿委托笔数 | 各恰好 2 笔（2 标的×1 手×买卖） | `N_SYMBOLS=2`、`LOTS_PER_SYMBOL=1` 硬编码 |
| 未授权/重复/错误订单 | **0** | 授权 ALLOW + 本地 cid 去重 |
| 服务端成交回报 `created_at` | 可解析为 ISO-8601；落在各自下单时点附近 | H9 修复后 `_plain_value` 转 Timestamp→ISO |
| 21 日终 `positions` | `[]`（去污硬标准） | T+1 卖出全部平仓 |
| `n_unexplained`（cid 双向差集） | **0** | 本地 cid ↔ 服务端回报逐条可解释 |

⚠️ 不承诺"一定成交""一定不亏"——照 PA-GM-PROBE-1「不得替规则添加它没有的性质」。

## 3. 参数硬编码（启动校验，任何变更 = 新预登记）

| 参数 | 值 | 来源 |
|---|---|---|
| `N_SYMBOLS` | 2 | PA-GM-PROBE-1 |
| `LOTS_PER_SYMBOL` | 1 | PA-GM-PROBE-1 |
| `BUDGET_PER_SYMBOL` | 4000 元 | PA-GM-PROBE-1 |
| `MAX_NOTIONAL` | 100000 元 | PA-GM-PROBE-1 |
| `PROBE_TAG` | `PROBE` | PA-GM-PROBE-1 |
| `AUTH_ACTION` | `sim_rebalance_order` | PA-GM-PROBE-1 |
| `HOLD_DAYS` | 1（买入次日卖出） | PA-GM-PROBE-1 |
| 买入日 | **2026-11-25**（最晚，T+1 需 11-26 卖） | 专家 Q4 时间红线 |
| 卖出日 | **2026-11-26** | T+1 |
| 去污验证 | 2026-11-26 日终 | 专家 Q4 |
| 类型 | 限价单（禁市价） | PA-GM-PROBE-1 |
| cid 前缀 | `PROBE` | PA-GM-PROBE-1 |

## 4. 判定量、判据与失败关闭

| # | 判定量 | 硬判据 | 失败动作 |
|---|---|---|---|
| P1 | 授权 | 探针授权 `ALLOW 6/6`、action=`sim_rebalance_order`、有效期覆盖 11-25~11-26 | rc=4，零报单 |
| P2 | 委托笔数 | 买腿恰好 2、卖腿恰好 2；每笔 1 手限价；单笔 ≤4000 元 | 停机 + 告警 |
| P3 | 未授权/重复/错误订单 | `n_unauthorized=0 ∧ n_duplicate=0 ∧ n_error=0` | 停机 + 告警 |
| P4 | 服务端回报 `created_at` | 可解析 ISO-8601；原始 `seconds/nanos` + ISO **双记录**于产物 | 停机 + 告警 |
| P5 | cid 双向差集 | `n_unexplained=0`（硬失败，不产出部分解释对账文件） | 停机 + 告警 |
| P6 | 去污 | 11-26 日终 `positions=[]`（**唯一硬标准**） | 停机 + 升级账户持有人（接受残仓+更正 or 顺延），执行方不自行裁定 |
| P7 | 现金记录 | `cash_before`/`cash_after`/费用合计三个**记录型**字段；现金变动可被费用完全归因（相对容差 `4e-6`、绝对 `0.01 元`） | 记录归因，不设通过门；归因不成立 ⇒ 在产物标 `ATTRIBUTION_UNEXPLAINED` 并告警 |

⭐ **现金口径**（专家答复 §二）：`positions=[]` 是去污唯一硬标准；现金因手续费略 ≠ 期初 100000 不构成失败——E3/额度门用实时 `available`，功能自洽。记录字段价值在审计可解释，**不增设会假 FAIL 的门**。

## 5. 与 H9 修复的衔接

- 服务端成交回报的 `created_at` 经 `paper_account_adapter._proto_timestamp_to_iso`（本次授权前已修）转 ISO-8601(+08:00)。
- PROBE 产物**双记录**：原始 `seconds`/`nanos` 原值 + 转换后 ISO。此为专家 Q1 的"实盘首笔回报事后确认语义"闭合方式，零额外成本。

## 6. 产物与归属

| 产物 | 路径 | 归属 |
|---|---|---|
| 委托台账 | `forward/sim_orders/PROBE_*.jsonl` | `mode="PROBE"`，cid 带 `PROBE` 前缀 |
| 回报对账 | `forward/probe_reconcile/2026112[56].json` | 含 P4 双记录 + P7 现金字段 |
| 去污验证 | `forward/probe_reconcile/20261126_decontam.json` | `positions=[]` 判定 |
| 授权执行记录 | `forward/authorization_statements/` | 探针授权原文 + 时间戳 + 侧车 |

所有产物 `mode="PROBE"`，不计 G1/G2/S4。

## 7. go/no-go 检查点（专家裁决）

- **11-27 17:00 前**：PROBE 完成 + 去污 `positions=[]` + 无未闭合 H 类问题 ⇒ 才 enable DRYRUN prep timer。任一不满足 ⇒ 按条件项 C 顺延 U-16。
- 这是两个独立检查点之一（另一个是 11-30 17:20 前断链 A 完成才出证），非彼此前置。

## 8. 已知限制（引用必带）

- SDK 无 `client_order_id` 入参 ⇒ 幂等仅本地 cid 去重，不构成券商侧幂等。
- SDK 无行情接口 ⇒ PROBE 用限价单次下单，不重定价（与 U-16 CARRY 顺延同口径）。
- 报单路径首次真实执行；若暴露新 bug，按纪律保留原始产物 + 出更正，不改写。
