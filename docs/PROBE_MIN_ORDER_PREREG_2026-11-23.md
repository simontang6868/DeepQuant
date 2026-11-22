# PROBE 最小下单探针预登记（PA-GM-PROBE-3，冻结于任何实现代码之前）

> 冻结时间：2026-11-22 20:20 CST｜编号 `PA-GM-PROBE-3`｜执行日 **2026-11-23**（仅此一天）
> 来源：外部专家第 11 轮 §一 P0 —— `CONNECT_OK` 只证明 login/get_cash/get_positions，**不证明 place_limit_order 会被接受**。
> 本预登记**不写自身哈希**；侧车 `.sha256` 独立冻结。任何参数变更 = 新预登记，不在本文件上改字。
> 性质：**仅工程验证**，`StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`、`MaturityLevel=PROBE_ORDER_PATH_ONLY`；不计 G1/G2/S4，不进可引集合，不产生任何策略/收益结论。

---

## 1. 目的与预期方向（可算量，不是收益）

**目的**：在 11-25 主 PROBE 之前，用一笔必然不成交的跌停价买单**真实穿过**下单链路，验证权限/白名单/额度/数量下限，把 11-25 09:35 的失败面收敛到市场与人。

| 可算量 | 预期方向 | 机制 |
|---|---|---|
| `place_limit_order` 返回 | `order_id` 非空、无异常 | 权限/白名单/额度/数量门在 login 之后、下单时才校验 |
| 下单前 `get_orders()` | `[]` | 账户不应有别人的在途单；非空即 HALT，**不撤他人单** |
| 撤单后 `get_orders()` | `[]` | `gm.order_cancel` 真撤掉本单 |
| 撤单后 `get_positions()` | 与下单前相同（本探针预期仍为 `[]`） | 跌停价买单当日不会成交 |
| 撤单后 `available` | 回到基线（记录型，不设硬门） | 冻结资金释放 |
| `n_unexplained` | 0 | 只发 1 单、只撤 1 单 |

⚠️ **不承诺一定不成交**：若极端情况下跌停价买单成交，按 `UNEXPECTED_FILL` 升级账户持有人，**不自动卖出**（无此授权）。这是预登记允许的产出之一，不是失败后现议。

## 2. 参数硬编码（启动校验，任何变更 = 新预登记）

| 参数 | 值 | 来源 |
|---|---|---|
| 执行日 | `2026-11-23`（日期门，仅此一天） | 专家 §一 |
| 标的 | `601398`（沪市主板，11-25 主 PROBE 同标的） | 复用 PA-GM-PROBE-2 |
| 方向/数量 | `BUY` / **100 股（1 手）** | 专家 §一「100 股起买则买 100 股」 |
| 价格 | `Decimal(prev_close) × 0.9`，**ROUND_HALF_UP 保留 2 位**，由 `--prev-close` 传入后脚本自算 | 跌停价；不手填、不猜价 |
| 价格下限 | `> 0` 且 ≤ `prev_close × 0.9` 的 2 位结果 | 失败关闭 |
| 单笔名义 | `price × 100`，须 ≤ 4000 元且 ≤ `max_notional=100000` | PA-GM-PROBE-2 预算 |
| 授权动作 | `sim_rebalance_order`（复用既有 `paper_authorization.json`，**不创建/不修改**） | 书面签署件 |
| 额度门 | guard 六项 + **实时 `available`**（禁止写死 100000）+ `cumulative=0` | `PAPER_AUTH_GUARD_PREREG` §5 |
| 账户锚 | `DQ_GM_ACCOUNT_ID` 掩码 `0a47****************************2aa6`（前4+后4，中间 28 星）；`get_cash().account_id` 必须等于 env 值；授权 `account_ref` 标签 `虚拟盘测试202611` | 执行机 11-22 现场字段核对 |
| 通道 | `gm-paper`；`LIVE_ENABLED=false`；禁止实盘 | 签署件 |
| 解释器/env | `/opt/quant/.venv-gm/bin/python` + `set -a; source /opt/quant/env; set +a` + `cd /opt/quant` | 11-22 三个真坑 |
| cid | `PROBE-MIN-20261123-601398-BUY-100`（本地标识，SDK 不透传，见限制） | 沿用本地幂等口径 |

## 3. 步骤与硬门（顺序不可调换）

1. **前置**：校验预登记 sha、日期门、`--prev-close` 正数十进制、算跌停价；`dryrun` 只走门控、不建连。
2. **身份锚**：login 后比对 `get_cash().account_id == DQ_GM_ACCOUNT_ID`，落盘掩码值；不等 ⇒ `ACCOUNT_MISMATCH`、零报单。
3. **账户快照**：记录 `nav/available/positions`；`get_orders()` 非空 ⇒ `PREEXISTING_ORDERS`、零报单（不撤他人单）。
4. **授权门**：用实时 `available` 调 guard；非 `ALLOW 6/6` ⇒ `NOT_AUTHORIZED`、零报单。
5. **下单**：`order_volume(symbol, 100, BUY, LIMIT, OPEN, price=跌停价)`；异常/拒绝 ⇒ `ORDER_REJECTED`、记录原因、**不发第二单**。
6. **撤单**：取返回的 `cl_ord_id`/`order_id` 调 `gm.order_cancel([{cl_ord_id, account_id}])`；若返回结构取不到 id 且第 3 步确认原本无单，才允许 `order_cancel_all()` 兜底（留痕 `CANCEL_FALLBACK=ORDER_CANCEL_ALL`）。
7. **后置**：`get_orders()==[]` 且 `positions` 与下单前一致 ⇒ `MIN_ORDER_OK`；否则 `CANCEL_FAILED`/`UNEXPECTED_FILL`，**升级持有人，不自动补单**。
8. **落盘**：`forward/probe_min_order_2026-11-23.json`，原始返回字段 + ISO 时间 + 全部原因码；任何失败保留原始产物、出更正，不改写。

## 4. 判据与失败动作

| # | 判定量 | 硬判据 | 失败动作 |
|---|---|---|---|
| Q1 | 预登记/日期/参数 | sha 一致、日期=20261123、跌停价可算 | `HALT` rc=3 |
| Q2 | 账户锚 | env 与 cash 账户一致、掩码=冻结值 | `ACCOUNT_MISMATCH` rc=4，零报单 |
| Q3 | 在途单 | 下单前 `get_orders()==[]` | `PREEXISTING_ORDERS` rc=4，零报单 |
| Q4 | 授权 | `ALLOW 6/6`、实时 available | `NOT_AUTHORIZED` rc=4，零报单 |
| Q5 | 下单 | 返回 order_id 非空 | `ORDER_REJECTED` rc=4，不发第二单 |
| Q6 | 撤单+去污 | 撤后 `get_orders()==[]`、positions 未变 | `CANCEL_FAILED`/`UNEXPECTED_FILL` rc=4，升级持有人 |

## 5. 已知限制（引用必带）

- SDK `order_volume` **无 `client_order_id` 入参** ⇒ 本地 cid 不构成券商侧幂等；本探针靠第 3 步"原本无单"兜底。
- 本探针**不验证成交回报 `created_at`**（那是 11-25 主 PROBE 的 P4）。
- 跌停价买单**理论上可成交**；`UNEXPECTED_FILL` 为预登记允许的产出，处置权归账户持有人。
- 不得把 `MIN_ORDER_OK` 写成"策略可执行""alpha 已验证"或"12-01 主演练通过"。

## 6. 产物与归属

| 产物 | 路径 |
|---|---|
| 最小下单探针结果 | `research/stock_pick_2026-10-25/forward/probe_min_order_2026-11-23.json` |
| 本地 dryrun 自检 | `research/stock_pick_2026-10-25/forward/probe_min_order_dryrun.json` |

所有产物 `mode="PROBE"`，不进 `forward/ledger/` 正式台账。
