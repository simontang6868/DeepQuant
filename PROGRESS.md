> **本文件是历史报告，不反映当前状态。** 当前状态见仓库根目录 `STATUS.md`（2026-11-03 04:14）。

# PROGRESS

**最后更新**：2026-11-03 03:40（Phase 2.10 申报材料草稿完成；**Phase 2 代码侧全部交付**）
**当前阶段**：Phase 1 代码侧完成、G1 判 `NOT_PASSED`（缺 D8）；Phase 2 代码侧 2.1–2.8 完成、2.9 交付机制，**G2 预期 `NOT_PASSED`**（同缺 D8）
**活跃工作区**：`D:/DeepQuant power by txm/DeepQuant-main`，分支 `workbuddy/2026-10-20-consolidated`

---

## 阶段状态

| 阶段 | 状态 | 产物 |
| --- | --- | --- |
| Phase 0 现状盘点（只读） | ✅ 完成 | `reports/PHASE0_STATE_REPORT.md` |
| G0 用户确认 | ✅ 通过 | D3=中原证券、D9=774365830@qq.com；D8 待用户提供机器 |
| Phase 1.1 单一配置源 | ✅ 完成 | `scripts/sp5/strategy_params.py` + 15 条测试 |
| Phase 1.2 价格上限 amendment | ⚠️ 已报告，待决策 | `reports/SP4_AMENDMENT_A1_PRICE_CAP_2026-11-02.md`（含 §9 更正） |
| Phase 1.3 调度可靠性 | ✅ 完成 | `reports/PHASE1_PROGRESS_2026-11-02.md` |
| Phase 1.4 数据更新 | ✅ 完成 | 数据到 **2026-10-30** → `docs/QFQ_REBUILD_ACCEPTANCE_2026-11-02.md` |
| Phase 1.5 安全过滤 | ✅ 完成 | `reports/SP4_AMENDMENT_A2_UNIVERSE_FILTER_2026-11-02.md` |
| Phase 1.6 入场规则（验证性） | ✅ `PASS_NO_CHANGE` | 见 A2 报告 §13 |
| **Phase 1 阶段报告 / G1** | ✅ 报告完成，**G1 `NOT_PASSED`** | `reports/STAGE_REPORT_PHASE1_FINAL.md` |
| **Phase 2.1 BrokerAdapter + Mock** | ✅ 完成 | `backend/src/deepquant/broker/`（28 条测试） |
| **Phase 2.2 订单规划** | ⚠️ **`PASS_WITH_GAPS`**（已更正） | `execution/planner.py`（22 条）；⚠️ 09:35 时点/重定价/顺延/次数上限**未实现**，见 `reports/PHASE2_10_REPORTING_DRAFT_2026-11-03.md` §5 |
| **Phase 2.3 RiskGate 11 条硬限** | ✅ 完成 | `backend/src/deepquant/execution/risk_gate.py`（67 + 10 条测试）→ `reports/PHASE2_3_RISK_GATE_2026-11-02.md` |
| **Phase 2.4 状态机与恢复** | ✅ 完成 | `execution/state_machine.py`（41 条，含 9 个变异体） |
| **Phase 2.5 日终对账** | ✅ 完成 | `execution/reconciler.py`（30 条，双向 + 整数分容差） |
| **Phase 2.6 台账** | ✅ 完成 | `execution/ledger.py`（27 条，哈希链 + 只追加字节级证明） |
| **Phase 2.7 监控告警** | ✅ 完成 | `execution/alerts.py`（30 条，五类 + 不发邮件） |
| **Phase 2.8 故障注入 11 项** | ✅ 完成 | `tests/execution/test_fault_injection.py`（29 条）+ `execution/config_guard.py` |
| **Phase 2.9 SHADOW** | ⚠️ `PARTIAL` | `execution/shadow.py`（22 条）；20 个交易日需 D8 |
| 2.4–2.9 交付报告 | ✅ | `reports/PHASE2_4_TO_2_9_DELIVERY_2026-11-02.md` |
| **Phase 2.10 申报材料草稿** | ✅ `DRAFTED_NOT_SUBMITTABLE` | `docs/reporting/`（4 份）+ `execution/rate_limit.py` + `execution_config_v1.json` → `reports/PHASE2_10_REPORTING_DRAFT_2026-11-03.md` |
| **G2 验收** | ❌ 预期 `NOT_PASSED` | 缺 D8（SHADOW 20 日） |

---

## ⭐ 里程碑：首条真实前瞻记录

**`forward/ledger/20261030.json`**（sha256 `c074bc1b…`，**未被改写**）

| 字段 | 值 |
| --- | --- |
| 决策日 | 2026-10-30（数据末日） |
| 模式 | **LATE_RUN**（`late_calendar_days = 3`，`counts_toward_observation = true`） |
| 信号池 | 5,444 只 → 主口径 pool 800 |
| 目标持仓 | `600990, 603977, 002012, 301329, 600191`（amt20 降序 top5） |
| 前持仓 | `[]`（观察期内首次建仓） |
| 成交状态 | 5 只全部 `PENDING_NO_CALENDAR`，待 11-02 数据到位后由 `p13b` 回填 |
| D4 字段 | **无**（该记录生成于 1.5 之前，按纪律不覆盖；两本账从 20261130 起自动写入） |

## 1.5 的核心结论（务必带着限定引用）

**D4 严格口径（仅沪深主板）下 top5 一只不剩（0/5）。但主因不是 ST，是创业板 1,393 只（可买池 27%，是 ST 188 只的 7.4 倍）。**

| 账本 | ok_buy | pool | top5 |
| --- | --- | --- | --- |
| 主口径 | 5,153 | 800 | 600990 / 603977 / 002012 / 301329 / 600191 |
| D4 严格 | 3,037 | 459 | 000721 / 000659 / 601996 / 001209 / 001223 |
| D4+创业板 | 4,390 | 674 | 603237 / 000153 / 300183 / 300887 / 000777 |

⚠️ **「退市整理」与「停牌」单项剔除数都是 0**，正确表述是「既有过滤已覆盖、本次零增量」，**不是**「没必要」：
- 141 只含「退」的股票中**仅 1 只**真在退市整理期（002731，收盘 0.25 元被价格下限排除），其余 140 只早已退市
- 237 只当日无报价的股票，10-30 的 `stale_days` 中位 **1,229 天**，早已被 `stale_days≤15` 排除

## ⭐ 1.5 顺带查出的第三个配置/实现不一致

`forward/sp4_config_v1.json` 的 `band` 写「amt20 **全市场**分位 < 20%」，而实现在**子集内**重算分母（前瞻用 `ok_buy`，回测用 `ok_buy & f3.notna & fwd20.notna`）。登记为 **`CONFIG_LIQ_PCT_DENOMINATOR_MISMATCH`**。

影响：同一批 D4 过滤，

| 分位口径 | 主口径 vs D4 严格 | 主口径 vs D4+创业板 |
| --- | --- | --- |
| A 子集内（现行） | 0/5 | 0/5 |
| B 全市场固定分母（配置字面） | 3/5 | **5/5** |

⇒ **「D4 让持仓全换」是「子集内分母」这个实现选择的产物，不是过滤的必然。**
⇒ 已更正 A1 报告 §3.2「任何改变子集大小的操作都会翻转 top5」这一过强表述（A1 报告已追加 §9 更正，原文未改）。

---

## 1.3 已落地的行为（勿回退）

- `--dry-run` 零写入（含 abort 分支不写心跳）
- 台账不可覆盖；`--force` 先备份 `.bak.<UTC>` 并追加 `corrections.jsonl`
- `LATE_RUN` 仅覆盖 `0 < gap ≤ 3`；`> 3` 仍强制降级 SELFTEST（既有闸门，勿放宽）
- 决策日 = 数据末日，且要求「已进入下一自然月」且距今 ≤7 天
- `next_td` 未知 ⇒ `PENDING_NO_CALENDAR`
- 台账含 `code_sha` / `git_commit` / `code_in_git` / `data_last_date`
- `--verify` 只读自检；`p13b` 的 `selftest` 从台账 `counts_toward_observation` 推导

## 1.1 已落地的行为（勿回退）

- 策略参数**只从 `scripts/sp5/strategy_params.py` 取**
- 配置里的自然语言阈值由解析器提取，**解析失败即报错**，不回退默认值
- 配置只认 `forward/sp4_config_v1.json` 一处；`sp5_out/` 出现副本直接 `RuntimeError`

## 1.4 已落地的行为（勿回退）

- 增量更新只追加、默认 dry-run、**双源一致才写**、原子替换
- `volume` 单位**逐只由两源比对判定**（`share` / `hand`），不判出即拒写
- 复权重建必须 `--div-source merge`；提升前必须过 `scripts/verify_qfq_rebuild.py`

## 1.5 已落地的行为（勿回退）

- 板块 / ST / 退市 / 停牌全部为**纯函数**（`board_of` / `classify_name` / `judge_no_quote`），不依赖顺序与网络重试
- `UNKNOWN` 板块必须返回 `UNKNOWN` 并告警，**不得**兜底成主板（`200053` 是 B 股）
- 休市日抓取**必须报错退出**（判据是「有连续竞价成交的比例 < 30%」），不得把全市场标成停牌
- 拉到 ≠ 可交易：`no_data=True` 默认 `no_quote_today=True`
- 快照同日重抓先备份 `.bak.<UTC>` + 追加 `corrections.jsonl`，不静默替换
- 快照不可用（缺失/早于决策日/盘中抓取）时 D4 账本**必须是 `null` + reason**，不得回退成「不过滤」
- 主口径 `target_holds` / `rand5` **一字未改**；D4 账本只新增字段

## 测试基线

| 套件 | 结果 |
| --- | --- |
| `backend/ tests` | **3499 passed / 1 skipped**（3193 基线 + 28 broker + 278 execution） |
| `backend/tests/broker` | **28 passed**（2.1） |
| `backend/tests/execution` | **278 passed**（2.2 的 22 + 2.3 的 77 + 2.4 的 41 + 2.5 的 30 + 2.6 的 27 + 2.7 的 30 + 2.8 的 29 + 2.9 的 22） |
| `research/.../scripts/sp5/tests` | **176 passed / 1 failed** |
| 唯一红：T6 | `p04_position_dd.py` 的 ffill，属回测侧，改它会动历史结果，须新预登记 |
| `test_universe_filter.py` | 63 passed |
| `research/.../tests/test_strategy_params.py` | 15 passed |
| `scripts/tests` | `test_update_exec_incremental`(14) + `test_verify_qfq_rebuild`(17) |

⚠️ 后端测试**必须**带 `PYTHONPATH=<repo>/backend/src`，否则 19 个模块 `ModuleNotFoundError: No module named 'deepquant'`——那是环境问题，不是回归。

## Phase 2 已落地的行为（勿回退）

- **MockBroker 扮演券商，不做撮合**：撮合属 `eventloop.matching`（回测口径），两处各写一套必然分叉且无人发现。有测试扫代码禁词守住。
- **`UNKNOWN` 是独立状态**：回报丢失时持仓已变、回报不可见、状态 `UNKNOWN` 三者同时成立，否则对账发现不了差异。
- **`fill_id` 必填且重复回报复用同一 id**：重复的是回报，不是成交。
- **`client_order_id` 是纯函数派生**（`20261030-600990-B-200`），**不含时间戳/随机数/进程号**；重定价用 `-r1` 后缀，前缀相同可追溯。
- **买入预算不假定卖出回款**（`settled_cash_from_sales` 默认 0）⇒ 换仓是两步，planner 必须幂等可重入。
- **计划跳过必须记 `SkippedItem` 带原因**：静默跳过会让"该买没买/该减没减"永远无法归因（这是实现中真实踩到的缺陷）。
- **限价用最新价 ±0.3% 近似"对手价"**，再被涨跌停夹住；无盘口数据，须在台账注明，Phase 3 用真实成交价校准。

## Phase 2.3 RiskGate 已落地的行为（勿回退）

- **12 条检查顺序固定**（`halt → breaker → auth → reconciliation → unknown_orders → trading_hours → blacklist → quote → price_deviation → position_limit → order_notional → daily_count`），停在第一个失败。顺序本身就是规则：先问「该不该发单」再问「这笔单合不合规」。
- ⭐ **缺失输入一律拒**（`MISSING_INPUT`）：`OrderContext` 的 7 个可空字段逐个为 `None` 都被测过。加新可空字段必须同步加进参数化列表。
- ⭐ **峰值必须落盘**：`1 - 19500/20000 = 0.025000000000000022`，比较带 `1e-12` 容差。峰值丢失 ⇒ 回撤重算成 0 ⇒ 熔断永不触发。熔断状态本身也跨重启保留。
- **授权文件只读**：`Authorization` 只有 `load`，无任何写方法；缺字段直接报错不补默认值。
- **`quote` 与 `price_deviation` 双闸**在「缺行情」上互为备份（防御纵深，不是重复代码）。
- **变异测试 9 个 + 锚点自检**：锚点必须在源码出现恰好一次，否则变异会静默退化成「没抓到」而测试仍绿（假变异）。

---

## 待用户决策

| 编号 | 缺什么 | 影响 |
| --- | --- | --- |
| **D8** | **运行机器** | 决定 G1「连续 10 个交易日零漏跑」能否验证，也是 11-30 证据点的前提 |
| **D4 补充** | **创业板权限是否已开通** | 1,393 只 = 可买池 27%，是本次最大单项约束 |
| D4 补充 | 是否要求统一分位分母口径（§5） | 改它要重跑全部回测、作废历史结论。**建议先不动** |
| D3 补充 | 向中原证券确认 6 件事 | 见 `docs/PHASE1_DECISIONS_2026-11-02.md` §2.3 |

---

## 下一步

1. **明日（11-03）数据到位后**跑 `p13b` 回填 20261030 的成交状态（现全 `PENDING_NO_CALENDAR`）
2. **11-30 月末必须当天或次周一跑**，否则又强制 SELFTEST（证据期起点）
3. **2.4 状态机与恢复**（`UNKNOWN` 时禁新单、崩溃后以券商为准重建、重建只读）→ 2.5 日终对账（双向差异）→ 2.6 台账 → 2.7 告警 → 2.8 端到端故障注入 11 项
5. 待 D8 落实后可开始计「连续 10 个交易日零漏跑」→ G1；同时可开始 SHADOW 20 日 → G2
6. `exec_all` 补 `volume_unit` 元数据并重算全池 `amt20`（须新预登记）
7. 提交范围待用户定；仍未推送 origin/main。⚠️ `research/` 与新增的 `broker/`、`execution/` 均在 git 外

---

## 会话协议

- 每次会话开始：读任务书 `C:\Users\77436\Desktop\DeepQuant 实盘化执行任务书（给 WorkBuddy）.md` + 本文件。
- 每次会话结束：更新本文件。
- 铁律：不新增因子、不改冻结参数、不启动实盘、`LIVE_ENABLED=false`、不伪造数据。
