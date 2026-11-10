# DeepQuant 阶段性综合审阅报告（截至 2026-11-10）

**供外部专家仔细审阅｜含 11-09～11-10 仿真账户（模拟盘）一手数据**

| 元信息 | 值 |
| --- | --- |
| 版本 / 生成时间 | **v1.0｜2026-11-10 20:35–21:00 CST** |
| 云端数据截止 | **2026-11-10 20:34:24 CST**（只读核查时刻） |
| 课题阶段 | 阶段总结 + 持续核验（R6-7：阶段总结可长，但**结论先行**） |
| 研究类型判定 | **描述性汇总 / 运行与证据链核验，不是预测建模** ⇒ 不训练模型、不新增因子 |
| 作者与性质 | 执行 Agent **自审**（作者＝执行者本身）。⚠️ **本报告不构成独立审查**；审阅者请按 `docs/REVIEWER_GUIDE_2026-11-05.md` **自行复算**，不得以本报告的自审结论为依据（R5） |
| 引用规范 | `.workbuddy/memory/RESULTS_INDEX.md`（引用前必读）：任何数字须带「判定 + 统计口径 + 成熟度 + 预登记哈希 + 产物目录」 |
| 数据来源 | 云主机 `/opt/quant` 一手日志/产物、`journalctl`、掘金仿真账户只读探针、QQ 邮箱收件箱、本地 git 仓库与产物目录；**全部只读** |
| 本轮边界 | **未改任何运行脚本、未补写快照、未向任何账户报单、`LIVE_ENABLED=false` 未变**；11-09/11-10 已产出结果原文保留 |

---

## 0. 结论先行（10 条，每条标证据等级）

> 证据等级：**[一手]**＝本次云端/账户/邮件实测；**[产物]**＝仓库内冻结产物；**[既有]**＝`RESULTS_INDEX.md` 已登记结论；**[推断]**＝由代码逻辑推演，需修复后验证。

1. ⭐⭐ **[一手+产物] 发现一个此前未登记的 P0 阻塞性缺陷**：11-10 起 `paper_snapshot` 每天返回 `rc=4「当日快照已存在且内容不同，拒绝覆盖」`，`account_snapshot.json` 永久停在 `asof=20261109`。SHADOW 的 `load_account_snapshot()` 硬要求 `asof==day` ⇒ **11-30 之后即便有 d4 台账，G2 也永远 0/20**，「最早 12-25 判 G2」时间线**失效**。详见第 4 节。
2. **[一手] 接入完成，但证据尚未开始积累**：G1 **6/10**、G2 **0/20**、S4 **0/7**；四道实盘门全未过，实盘无排期。
3. **[一手] 两天日更链均报 `mode=NOT_DUE ok=1`**，但 11-10 的 `ok=1` 掩盖了 3a 的 `rc=4`；17:40 告警仍报「心跳正常 / 日更链正常」，**告警漏报**。
4. **[一手] 仿真账户开户至今零委托、零成交**：`cum_trade=0`、`cum_commission=0`、`cum_pnl=0`、`n_positions=0`、`nav=100000` 未动 ⇒ **谈不上盈亏**。日志里的「5 笔订单」是规划输出，`orders_sent=false`。
5. **[一手] 11-09 首次 `REAL_PAPER`，11-10 退回 `SELF_CHECK_ONLY`**：这不是链路回退到「没接」，而是快照新鲜度闸门**正确拒绝了陈旧快照**（`account_snapshot_stale`）。检查器有效，问题在上游快照产出。
6. **[产物] 2026-10 下跌月描述性归因**：现行 v2（20% 仓）组合月毛 **−2.3110%**，是全池同区间 −11.5648% 的 **0.1998 倍**；机制是仓位线性缩放，**持股端 −12.3420% 反而跑输全池 0.78pp，不提供防御性**。结论标 `DESCRIPTIVE_SINGLE_MONTH`／`NOT_TESTED_SINGLE_PATH`／`EXPLORATORY_ONLY`，**不可引、不进证据分级**。详见第 6 节。
7. **[既有] 经济性仍未过门槛**：现行规则年化 **7.50%–7.66% < 8%**；8.02% 是**未上线**的整手追加规则、仅超门槛 **0.02pp** 且为样本内估算。
8. **[既有] 策略有效性数年内得不出结论、没有「验证完成日」**（`GATE1_CLOSURE_STATEMENT`；把效应压到 0.5pp/月需 ≈676 期 ≈56 年）。⭐ **「功效不足」是尺子分辨率问题，不得写成「策略没用」**。
9. **[一手] 部署可证性与通道隔离完好**：manifest 本地/云端 **45/45 PASS**、隔离 **FAIL 0 项**、`paper_authorization.json` 命中 **0**（Agent 未创建）；容量宽裕（云端剩 40G）。
10. **[推断] 最紧急的不是等 11-30，而是先修 P0**：修复需用户书面授权，另立变更记录 + 变异测试 + 云端实跑。用户待办另有 U-14 token 轮换、U-15 授权文件本人创建、U-16 演练放行、U-12 QMT。

---

## 1. 报告范围、方法与「这几天」的定义

### 1.1 方法

| 要素 | 设定 |
| --- | --- |
| 研究对象 | DeepQuant 的证据链（日更链 → 账户快照 → SHADOW 对账 → 备份/告警）与阶段性研究结论 |
| 待估计量 | 运行稳定性、账户状态、G1/G2/S4 计数、规划器一致性、证据强弱分层；**不含策略有效性检验** |
| 数据 | 云端 `/opt/quant` 日志与 JSON、掘金仿真账户只读回报、`journalctl`、QQ 邮箱、本地 git 产物 |
| 验证方式 | ① 一手日志与产物直接读；② **外部真值**交叉（账户累计成交字段 / 收件箱邮件 / systemd journal）；③ 独立算术复算；④ 双数据口径（qfq vs exec） |
| 主指标 | G1 连续天数、G2 合格天数、账户净值与累计成交、未解释差异数、部署一致性 |
| 交付物 | 本报告（md + docx）、`STATUS.md` 更新、`corrections.jsonl` 第 44 行 |
| 按专家工作流跳过的阶段 | 任务设计／特征／建模／评估已有合格产物（`OCT2026_DOWNTURN_RESEARCH_DESIGN_2026-11-09.md`、PA-OCT2026-1、SP-5 44 月回测），本轮是**阶段 5 持续核验 + 阶段汇总**，不重跑建模 |

### 1.2 「这几天的模拟盘数据」精确定义

| 日期 | 有无链路 | 有无真实账户快照 | 性质 |
| --- | --- | --- | --- |
| 11-03～11-06（4 个交易日） | ✅ 链路+心跳 ok | ❌ 无 | 仅链路稳定性，仿真账户 11-08 才接通 |
| 11-07～11-08（周末） | — 不跑 | ❌ | 工作日定时器，周末不产出、未补记 |
| **11-09（周一）** | ✅ 首次完整运行 | ✅ **首产** `asof=20261109` | 3b 首次 `REAL_PAPER` |
| **11-10（周二）** | ✅ 第二天 | ⛔ **产出失败 rc=4**，文件仍是 11-09 | 3b 退回 `SELF_CHECK_ONLY` |

⇒ **真正带仿真账户数据只有 11-09、11-10 两天，且第二天快照已停滞**。报告中所有「账户零成交」等结论的**已核验样本量 = 2 天**（按监测技能要求注明）。

### 1.3 未做的事（逐条可核）

未改 `sp4_config_v1/v2` 参数、未改 `p13_forward.py`、未改 `daily_chain.sh` 或适配器、未补写/覆盖任何快照、未接日更链、未创建任何授权文件、未向任何账户报单、未改 `RESULTS_INDEX.md` 数字、未删任何文件。

---

## 2. 阶段定位：四道实盘门

| 门 | 判据 | 当前 | 一手依据 |
| --- | --- | --- | --- |
| **证据·G1** | 连续 10 个交易日「链路 ok + 心跳 ok」同日成立 | ❌ **6/10** | `g1_progress.py`（数据源＝云端 journal）；11-10 云端 LAST/NEXT 复核 |
| **证据·G2** | 连续 20 个交易日 SHADOW 无未解释差异 | ❌ **0/20** | `shadow_daily_reconcile.py --summary`，断点 20261110 |
| **证据·S4** | 7 项样本外证据 | ❌ **0/7** | `STATUS.md`：S1 7/7｜S2 2/7｜S3 1/7｜S4 0/7 |
| **经济性** | 年化净收益 > 8% | ❌ **7.50%–7.66%** | 既有结论，样本内估算 |
| **通道** | U-12 QMT 开通 | ❌ 未确认、未开通 | 用户侧待办 |
| **授权** | `LIVE_ENABLED=false` + `live_authorization.json` + 书面 | ❌ 仓库与云端均无该文件 | 云端 `find` 命中 0 |

⭐ **G1 还须连续 4 个交易日**：11-11／11-12／11-13／11-16 ⇒ **11-16 收盘后判**（独立交易日历确认 11-10 在册、后续交易日序列无误）。

⭐⭐ **G2 时间线必须撤回重估**：项目此前已撤回「约 12-01 可判」（把 G2 当纯时间闸门），并改口「11-30 起含 d4 台账 + 有真实账户快照后的第 20 个交易日，最早 12-25」。**本次 P0 缺陷使后半句也失效**：快照不修复 ⇒ 11-30 后每天仍是 `SELF_CHECK_ONLY` ⇒ G2 永远 0/20。

---

## 3. 这几天模拟盘的一手数据

### 3.1 逐日总表

| 日期 | 日更链 | 行情增量 | 宇宙快照 | 决策日 | 账户快照 | SHADOW | 备份 | 峰值内存 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **11-09** | `NOT_DUE ok=1`，11 环节全 rc=0 | 5,472 只；READY 5,215 / NO_NEW_ROWS 257；追加 5,215 行；异常标记 **0**；165.8s | 5,444 只、有报价 5,207（95.6%）；ST 269／退市 141／无报价 237／未知板块 0；GEM 1,443 | `is_due NOT_DUE`（20261030 台账已存在，非月末） | ✅ **首产** `asof=20261109` | ✅ **首次 `REAL_PAPER`**：diff 1 条 `match`、`n_unexplained=0`、5 笔计划单全部 `orders_sent=false`；`counts_toward_g2=false`（仅因 `LEGACY_NO_D4`） | 27 文件 / 44,666 B | 607 MB |
| **11-10** | `NOT_DUE ok=1`，但 **`PAPER_SNAPSHOT_RC=4`** | READY 5,214 / NO_NEW_ROWS 258；追加 5,214 行；异常标记 **0**；169.5s | 5,444 只、失败批次 **0**、11.8s；有报价 5,207（95.6%）；`n_no_data=0`／`n_unknown_board=0`；board GEM 1,443 / SH_MAIN 1,788 / STAR 618 / SZ_MAIN 1,594 / SZ_B 1；快照 sha `8f3d92e8e7fefb7b` | `is_due NOT_DUE`（同上） | ⛔ **rc=4 拒绝覆盖**，文件仍 `asof=20261109` | ⛔ **`SELF_CHECK_ONLY`**：`account_snapshot_stale:asof=20261109`、`inputs.account=null`、diff 1 条 `match`、`n_unexplained=0`、5 笔计划单全部未发送 | 28 文件 / 44,824 B | 786 MB |

### 3.2 11-10 的日更链文字证据（一手日志原文摘录）

```
--- [paper_snapshot] ... paper_account_adapter.py --date 20261110
[paper] ✗ 当日快照已存在且内容不同，拒绝覆盖
--- [paper_snapshot] rc=4
[paper] ⚠️ 快照 rc=4（对账将退回 SELF_CHECK_ONLY，不计 G2）
--- [shadow_reconcile] 20261110 管道自检｜diff 1 条｜未解释 0 条｜计入G2=否｜本应下单 5 笔（未发送）｜rc=0
[shadow] note: LEGACY_NO_D4 ... 管道自检（account_snapshot_stale:asof=20261109）：没有真实仿真账户快照
[heartbeat] NOT_DUE ok=True decision=None
[mail] 已发送
=== daily_chain 结束 mode=NOT_DUE ok=1 PAPER_SNAPSHOT_RC=4 ===
```

`heartbeat.json`：`{"ts":"2026-11-10T17:15:05","mode":"NOT_DUE","ok":true,"stage_note":"PAPER_SNAPSHOT_RC=4",...}` —— **stage_note 记了 rc=4，但 `ok=true`**，17:40 告警据此报「心跳正常」。

### 3.3 仿真账户外部真值（11-10 20:34 只读探针，不写快照、不报单）

| 字段 | 值 | 含义 |
| --- | --- | --- |
| `nav` / `available` | **100000.0 / 100000.0** | 净值未变 |
| `frozen` / `order_frozen` | 0.0 / 0.0 | 无冻结资金、无挂单 |
| **`cum_trade`** | **0.0** | **开户至今零成交** |
| **`cum_commission`** | **0.0** | 零佣金 |
| `cum_pnl` / `last_trade` | 0.0 / 0.0 | 无已实现盈亏、无成交记录 |
| `cum_inout` | 100000.0 | 仅初始入金 |
| `n_positions` | **0** | 空仓 |

⚠️ **「有行情」≠「有交易」**：11-10 是独立交易日历确认的交易日，行情增量把 5,214 只追加到 11-10；但策略层面**一笔都没下**——不是月末决策日、老台账只做规划、且用户尚未给演练放行。SHADOW 的 5 笔（600990／603977／002012／301329／600191）是**规划输出**。

⚠️ 手工探针需先 `set -a; . /opt/quant/env; set +a`（`EnvironmentFile` 只由 systemd 注入）；root 交互会话直接跑会报「缺少环境变量」，**不是凭证失效**。全程不回显凭证值。

### 3.4 外部真值交叉核验（不拿自己的输出自证）

| 核验 | 一手结果 |
| --- | --- |
| systemd 定时器 | `daily` LAST **Tue 11-10 17:12:01**、NEXT **Wed 11-11 17:10:31**；`alert` LAST 11-10 17:40:01、NEXT 11-11 17:40；`calendar` NEXT 12-01 09:00 |
| journal（G1 判据） | 11-10 17:15:05 heartbeat／17:15:07 `Finished deepquant-daily.service`；11-09 17:15:03 heartbeat ok=True、17:15:05 ok=1 |
| 收件箱（QQ 邮箱检索） | 11-09：证据备份 27 文件（带附件）、【正常】、每周异地包（bundle 26M + evidence zip）；11-10：证据备份 **28 文件**（带附件）、【正常】。共 5 封**实证到达** |
| 告警日志 | 11-10 17:40:02 `[ok] 心跳正常：2026-11-10T17:15:05 mode=NOT_DUE decision=None` + 状态邮件 ⇒ **未报 rc=4** |
| 部署清单 | 本地与云端 `/opt/quant` **45/45 文件逐位一致 PASS**（记录 commit `cc82b7fd`） |
| 通道隔离 | 云端 **FAIL 0 项**；只读适配器 `order_*` 计数 0；`paper_authorization.json` 不存在（Agent 未创建） |

⚠️ 日志中「`SMTP 会话超时 180s`」是 `notify_mail.py` 按附件大小**推算后打印的配置值**，不是失败；随后「已发送」且收件箱实证到达。这是本项目反复提醒的「按字面读会误判」之处。

### 3.5 容量（11-09 清理后）

云端 `/dev/vda1` 50G 用 **11G / 剩 40G（21%）**；`/opt/quant/var` 25M、`data` 1.7G。本地 C 剩 9.1G（93%）、D 剩 14G（91%）。日增约 3.2 MiB/交易日，云端可撑数十年。⚠️ 唯一持续累积的外部存储是**每周异地包邮件**（每封 bundle 26M），建议只留最近 4–8 封。

---

## 4. ⭐⭐ P0 缺陷专题：`PAPER_SNAPSHOT_STALE_FROM_DAY2`

> 已登记 `research/stock_pick_2026-10-25/forward/corrections.jsonl` **第 44 行**（44 行全部合法 JSON）；**11-09/11-10 原始产物原文保留、未改写**。

### 4.1 现象与证据链

1. 11-10 17:12，3a `paper_snapshot` 返回 **`rc=4`**（`EXIT_CONFLICT`），日志「当日快照已存在且内容不同，拒绝覆盖」。
2. 全库 `find` 只有 **1 个** `account_snapshot.json`（142 B），内容 `asof=20261109`；11-10 备份包里打包的**也是这份 11-09 陈旧快照**。
3. 3b SHADOW：`account_snapshot_reason="account_snapshot_stale:asof=20261109"`、`inputs.account=null`、`mode=SELF_CHECK_ONLY`、`counts_toward_g2=false`。11-09 则是 `mode=REAL_PAPER`、`account_snapshot_reason=ok`。
4. 20:34 只读探针：账户 `nav=100000`、零成交、零持仓；用 `build_snapshot('20261110', cash, poses)` 构造出**本应写入的 11-10 快照**＝`{asof:20261110, cash 同值, positions:[]}` ⇒ **与 11-09 文件只差 `asof` 一个字段**。

⇒ **账户完全没变，只有日期推进；「内容不同」纯粹由 `asof` 造成。**

### 4.2 根因（代码位置）

| 位置 | 代码/常量 | 说明 |
| --- | --- | --- |
| `scripts/paper_account_adapter.py:43` | `OUT = FWD / "account_snapshot.json"` | **单文件**，按日期覆盖式设计 |
| `:156` | `build_snapshot()` 把 `asof=day` 写进内容 | 跨日内容必然不同 |
| `:160-170` | `write_snapshot()`：`if path.is_file() and read_text() != body: return EXIT_CONFLICT` | 为**同日重跑幂等**设计（同日内容相同才允许），但被**跨日 asof 推进**必然触发 |
| `:49` | `EXIT_CONFLICT = 4  # 同日已有不同内容` | 注释本身写的是「同日」，实现却没有判断「是否同日」 |
| `scripts/tests/test_paper_account_adapter.py:234` | `assert paa.write_snapshot(rec2, p) == paa.EXIT_CONFLICT` | 测试只覆盖**同日冲突**，**未覆盖跨日推进** ⇒ 又一起「测试全绿≠验收项覆盖」 |
| `deploy/bin/daily_chain.sh:166-171` | 3a 非零 rc 只写 `stage_note=PAPER_SNAPSHOT_RC=$pa_rc`，不阻断、不告警 | 日更链仍 `ok=1`、心跳仍 `ok=true` |
| `scripts/shadow_daily_reconcile.py:143-152` | `load_account_snapshot()` 硬要求 `asof == day`，否则 `account_snapshot_stale` | **检查器是对的**，问题在上游产出 |

### 4.3 影响推演

| 层面 | 影响 |
| --- | --- |
| 短期计数 | **不改变** 11-09/11-10 的结果：这两天本来就因 `LEGACY_NO_D4` 不计 G2；G1 6/10 不受影响 |
| **长期（阻塞）** | 11-30 起 FORWARD 台账有 d4 字段后，SHADOW 仍会因 `account_snapshot_stale` 判 `SELF_CHECK_ONLY` ⇒ **G2 永远 0/20** ⇒ 「最早 12-25 判 G2」**失效**，四道实盘门的证据门无法推进 |
| 告警 | 日更链与 17:40 告警均报「正常」，**运维不可见**；备份包持续归档陈旧快照，可能被误当作当日证据 |
| 证据完整性 | 若有人只看 `ok=1` 与备份包，会以为「每天都有真实账户快照」——正是本项目铁律「**没检出≠没问题**」的新变体：**检出了，却被降级容忍** |

### 4.4 修复建议（**待用户书面授权，本轮未改任何运行脚本**）

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| **A（推荐）** | 改为按日期分文件 `account_snapshot_YYYYMMDD.json`；SHADOW `load_account_snapshot(day)` 按 day 取；保留单文件为「最新」软链或不动 | 与项目「每日证据文件」惯例一致，天然幂等、可回溯，不需放宽防覆盖 |
| B | 允许 `asof` 单调推进覆盖：仅当新 `asof > 旧 asof` 才写，旧件留 `.bak.<日期>` | 改动小，但要新增加权判据与变异测试 |
| C | 3a rc≠0 升级为告警（独立于 A/B，建议一并做） | 保证任何降级都对运维可见 |

**修复必须包含**：另立变更记录（预登记或工程变更单）、**真改行为的变异测试**（至少覆盖「跨日推进必须写成功」「同日不同内容仍拒绝」「陈旧快照必须拒绝」三条）、云端 `scp` 后**实跑验证**（⚠️ `/opt/quant` 是部署目录非 git 工作副本，push 只做异地备份）、并**保留 11-09/11-10 原产物**。修复前不要对外宣称 G2 有任何预计完成日。

---

## 5. SHADOW 对账与「接入管道 ≠ 开始计数」

G2 计入一天需**同时**满足：`mode=REAL_PAPER`（当日真实账户快照）＋ `n_unexplained=0` ＋ 非 `is_backfill`（生成日＝交易日）＋ 非 `LEGACY_NO_D4`（台账含 `target_holds_d4`）。

| 日期 | mode | 快照新鲜度 | 未解释差异 | legacy | 计入 G2 | 阻挡原因 |
| --- | --- | --- | --- | --- | --- | --- |
| 11-09 | `REAL_PAPER` | `ok` (asof=当日) | 0 | ✅ 是 | ❌ | `LEGACY_NO_D4`（老台账） |
| 11-10 | `SELF_CHECK_ONLY` | `stale:asof=20261109` | 0 | ✅ 是 | ❌ | `LEGACY_NO_D4` **＋ 快照陈旧** |

两层 diff 都是 **1 条 `reason=match`（「台账目标与计划完全一致」、`blocks_g2=false`）**——特意展开核对过，**不是被「已解释」吞掉的真差异**。

⭐ 项目已因此反复更正口径：11-08 曾把「约 12-01 可判 G2」当纯时间闸门（**已撤回**）；本次又发现「有 d4 台账」也不够，还需**当日**账户快照。三层阻挡：**时间 → d4 字段 → 快照新鲜度**。

---

## 6. 研究成果与引用规范

### 6.1 2026-10 下跌月描述性归因（PA-OCT2026-1）

| 引用要素 | 值 |
| --- | --- |
| 预登记 | `research/stock_pick_2026-10-25/SP5_OCT2026_DOWNTURN_PREREG.md`，sha256 **`cd5d016b8c9f407847a217e3b327cb4a83d6c391975bfa5bc4bc83d05ced49a0`**（`.md.sha256` 冻结，三处逐位一致） |
| 产物目录 | `research/stock_pick_2026-10-25/oct2026_out/cd5d016b8c9f/` |
| `StatisticalVerdict` | **`NOT_TESTED_SINGLE_PATH`** |
| `MaturityLevel` | **`EXPLORATORY_ONLY`** |
| 结论标签 | **`DESCRIPTIVE_SINGLE_MONTH`**；不进证据分级、不改 S4 0/7 |

| 量 | 值 |
| --- | --- |
| 全池等权（10-08→10-30，17 交易日） | **−11.5648%**（5,190 只有效／254 缺端点）；`exec_all` 独立 −11.6670%，8 只权重股两口径 **0.00pp** |
| 持股端 5 只等权 / 相对全池 α | **−12.3420% / −0.7772pp** |
| A `V1_HISTORICAL_REPLAY`（w=0.25） | 月毛 −2.9380%／成本 0.0255pp／净 −2.9635%／MDD −3.6884% |
| B `V2_EX_POST_REPLAY`（w=0.20） | 月毛 **−2.3110%**／成本 0.0204pp／净 −2.3314%／MDD −2.9210% |
| `\|R_B\|/\|R_pool\|` | **0.1998 ≈ w=0.20** ⇒ 仓位线性缩放，非选股 |
| 5 元最低佣金 | 25 元 vs 按比例 5.24 元＝**4.77 倍**；既有 `20260930_nav.csv` 7 行独立复现最大差 **0.0179 元** |
| MDE | **本次不可估**（n=1）；只报 `BORROWED_NOT_ESTIMATED` 0.926／0.087–0.955／0.140pp（不同信号、仅尺子量级）与 `DAILY_NOISE_NOT_MDE` 1.3432pp |

⭐ **P3 按预登记字面被反驳**：§4 原文「`MDD(B) < MDD(A)` 且 `MDD(B) > −5%`」用负值约定 ⇒ `−2.9210 < −3.6884` 为假。按 **L3ac D1「预登记有缺陷也不改」**：不改原文、不放宽判据、另立缺陷记录段如实报 `P3_REFUTED_BY_LITERAL_SIGN`；仅补记「按幅度口径 2.9210% < 3.6884% 机制成立」。P1 恒等式 `Δ = +0.6269pp` 严格成立、P2/P5 支持、P4 不可判。

⚠️ **口径缺陷必须随数字同行出现**：A 还原的是 9-30 台账（`SELFTEST` 事后补跑、`LEGACY_NO_D4`、`nav/cash` 未回填），B 是**用 11-03 才冻结的 v2 后验配置重演 9-30 历史，非样本外**。9-30→10-08 组合按执行规则仍是 100% 现金，这段跌幅未承受是**执行时点机械结果，不是择时**。

### 6.2 既有可引结论（严格带口径）

- **SP-5（2023-01 起 44 月）** `RETURN_BELOW_8PCT` + `EXPLORATORY_ONLY`：25% 仓 +7.15%(t=2.24, MDD −8.60%)、**20% 仓 +6.29%(t=2.51, MDD −6.61%)**；⭐ **MDD 几乎全由 2024-01 微盘急跌贡献 ⇒ 抗跌来自仓位（80% 现金）非选股**。
- **8.4／8.5**：稳的是相对差距不是绝对收益；选择效应测不出来是**尺子分辨率**问题（压到 0.5pp/月需 ≈676 期 ≈56 年），**不得写「没用」**。
- **GATE1_CLOSURE_STATEMENT**：样本外 <100 月且效应 <0.5pp/月 ⇒ 证据不足。结合 exQ5 功效，**没有「策略验证完成日」**。
- **已关闭的路**：风控择时、518/532 池因子、单边反转、MAX5 单边、期指对冲、降换手、纯价量新信号、SURGE、exQ5 现代段、SP-5 F3 买入端、加长样本 ⇒ 项目**没有**下跌行情防御模块。

### 6.3 不可引 / 已撤回清单

| 类别 | 内容 |
| --- | --- |
| ❌ 已撤回 | 「约 12-01 可判 G2」（把 G2 当纯时间闸门）；**「最早 12-25 判 G2」（本次 P0 缺陷后亦撤回）** |
| ❌ 禁用表述 | 「策略抗跌」「策略跑输」「策略没用」「无效应」「exQ5 现代段可用」「反转 2016 后消失」「选择没有正贡献」 |
| ⚠️ 单窗口 | 本报告 6.1 全部数字：`DESCRIPTIVE_SINGLE_MONTH`，**不得**用于经济性判断、不得与 44 月窗口并列比较 |
| ⚠️ 仿真 | 仿真账户成交与净值（**目前为零**）**不得**用于经济性判断；演练方案亦明确此点 |
| ⚠️ 估计量 | Gate 1~6 头条 t 是 **iid**，C 阶段起才是 NW(3)；`d = 0.5×long − 0.5×short` 与文献 `long−short` 差 2 倍；`cost_of()` 入参必须小数 |
| ⚠️ 引用格式 | `QUOTABLE` 判定、`MDE`、多重检验校正口径见 `RESULTS_INDEX.md`；「解封后 QUOTABLE=1」，但 `NOT_GRADED` 6 条仍不得对外引用 |

### 6.4 证据强弱分层（本报告核心判断）

| 层 | 内容 | 能支持什么 | 不能支持什么 |
| --- | --- | --- | --- |
| **A 一手运行事实** | 日更链日志、账户真值、邮件、journal、定时器 | 运行稳定性、零成交、部署一致性 | 策略有效性 |
| **B 规划器一致性** | SHADOW diff=match、11-09 `REAL_PAPER` | 规划与台账一致 | 真实成交、经济性 |
| **C 描述性单窗口** | 10 月回测 | 「那一个月发生了什么」、机制归因 | 显著性、样本外、可推广性 |
| **D 样本内窗口统计** | SP-5 44 月、各阶段实验 | 有条件的、带 MDE 的参考结论 | 前瞻业绩承诺 |
| **E 前瞻样本外** | **S4 = 0/7，目前为空** | —— | 任何实盘结论 |

⭐ **A/B/C 三层全部不是策略有效性证据**；这是审阅时最容易混淆的一点。

---

## 7. 缺陷与更正记录汇总

`corrections.jsonl` 共 **44 行**（全合法），近期关键条目：

| 事件 | 要点 | 纪律教训 |
| --- | --- | --- |
| **`PAPER_SNAPSHOT_STALE_FROM_DAY2`**（第 44 行，本次新增） | 跨日 asof 推进触发防覆盖 ⇒ G2 时间线失效 | 「测试全绿≠验收项覆盖」＋「检出了却被降级容忍」 |
| `CONFIG_AUTH_PATH_MISMATCH` | 隔离检查器查 `/opt/quant/forward/`（云端 MISSING），实际路径在 `research/.../forward/` | 采集层路径分叉，27 条测试全绿漏检 |
| `PA_OCT2026_1_FROZEN_AND_RUN` | P3 按字面被反驳、MDE 不可估 | 预登记有缺陷也不改；原文不改写 |
| `U15_STATEMENT_RECEIVED_AND_GUARD_IMPL` | 书面授权按字面登记 `max_notional=100000`（模板曾建议 2–3 万，未擅自压低） | 不替授权添加性质；Agent 不创建授权文件 |
| 历史坑（散见日志） | `p03e --export-navs` 曾覆盖云端台账 73,395→3,823 行（已按 git 对象恢复）；并发跑 pytest 与变异脚本产生假 FAIL；`qfq_all` 列序/日期格式坑；容差按 6 位小数存储精度定 | 「没检出≠没问题」累计 12+ 次；**变异脚本原地改源码，期间禁止任何其他 pytest/提交**；报 FAIL 先怀疑检查器 |

---

## 8. 验收与部署可证性

| 项 | 结果 |
| --- | --- |
| 回归四连 | backend **3,651 passed / 1 skipped**；`property_checks` **9/9**；`scripts/tests` **615 passed / 1 skipped**；`verify_exec_all` **PASS** |
| 变异检出 | as-of **12/12**、channel 10/10、manifest 5/5、calendar 5/5、shadow **11/11**、paper_adapter 全检出、guard 10/10、session 全检出（**全部真改行为**） |
| 部署清单 | 本地与云端 **45/45 PASS**，`.prev.json` 保留上一版可回滚 |
| 通道隔离 | 云端 **FAIL 0 项**；真实通道凭证/适配器命中 0；`LIVE_ENABLED` 未设置；只读适配器 `order_*` 计数 0 |
| 预登记完整性 | PA-OCT2026-1 工作区／git blob／`.md.sha256` 三处 sha256 逐位一致，CRLF=0 |
| 最近提交 | `41938c9`（本报告）：**cloud 裸仓库 `45851e6..41938c9` 与 origin(GitHub) `519ea37..41938c9` 均推送成功**，含此前积压的 `45851e6`。11-10 凌晨曾因代理 502 + 备用 API 通道缺 `github-pat` 配置导致 origin 落后，20:45 代理恢复后一次 git 直连补齐（**未使用 `reset --hard`**） |

⚠️ 云端 `/opt/quant` **不是 git 工作副本**（无 `.git`），裸仓库在 `/opt/quant-backup/repo.git` ⇒ **push cloud 只做异地备份**；改 `deploy/bin/` 运行脚本必须另行 `scp` 并云端实跑（11-08 已踩）。

---

## 9. 审阅者复算清单（**建议逐条执行，不依赖本报告的断言**）

> 环境：Python `C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe`（下文写 `PY`）；仓库根 `D:/DeepQuant power by txm/DeepQuant-main`；云端密钥 `var/inbox/dq_deploy_ed25519`。

| # | 复算项 | 命令 / 位置 | 预期 |
| --- | --- | --- | --- |
| 1 | G1 计数 | `PY scripts/g1_progress.py` | `6/10`，链路/心跳 ok 日 = 11-03/04/05/06/09/10，还须 11-11/12/13/16 |
| 2 | G2 计数 | `PY scripts/shadow_daily_reconcile.py --summary` | `0/20`，断点 20261110 |
| 3 | **P0 现象** | `ssh ... 'cat /opt/quant/research/stock_pick_2026-10-25/forward/account_snapshot.json'` | `asof=20261109`（在 11-10 之后仍是 11-09） |
| 4 | **P0 根因** | 读 `scripts/paper_account_adapter.py:43,49,156,160-170` 与 `deploy/bin/daily_chain.sh:166-171` | 单文件 + asof 入内容 + 内容不同即 EXIT_CONFLICT + 3a 非零 rc 只记 stage_note |
| 5 | **跨日覆盖未被测试** | `PY -m pytest scripts/tests/test_paper_account_adapter.py -q`；看 `:234` | 全绿，但没有「跨日推进必须写成功」用例 |
| 6 | 账户外部真值 | 云端 `set -a; . /opt/quant/env; set +a` 后跑 `.venv-gm/bin/python` 调 `paper_account_adapter.read_account()` | `cum_trade=0`、`cum_commission=0`、`n_positions=0`、`nav=100000` |
| 7 | SHADOW 两日原因码 | `ssh ... 'cat .../shadow_reconcile/202611{09,10}.json'` | 11-09 `REAL_PAPER/ok`；11-10 `SELF_CHECK_ONLY/stale:asof=20261109` |
| 8 | 告警漏报 | `journalctl -u deepquant-alert --since "2026-11-10 17:35" --until "2026-11-10 17:45"` | `[ok] 心跳正常`，无 rc=4 异常 |
| 9 | 邮件真值 | QQ 邮箱检索主题含 `DeepQuant`、`after=2026-11-09` | 11-09 三封、11-10 两封，均到达 |
| 10 | 部署一致性 | `PY scripts/deploy_manifest.py --verify` 与 `--verify --remote` | 本地/云端 45/45 PASS |
| 11 | 通道隔离 | `PY scripts/check_channel_isolation.py --remote` | `FAIL 0 项`、`paper_authorization.json` 不存在 |
| 12 | 预登记哈希 | `sha256sum research/.../SP5_OCT2026_DOWNTURN_PREREG.md` 与 `.md.sha256` | `cd5d016b8c9f…`，CRLF=0 |
| 13 | 10 月回测独立复算 | `PY -m pytest scripts/tests/test_oct2026_replay.py scripts/tests/test_oct2026_asof_guard.py -q` | 24 passed；含 P1 恒等式、成本公式、P3 字面反驳保留 |
| 14 | as-of 守卫变异 | `PY scripts/oct2026_asof_mutation_test.py` | `12/12 检出`（须串行，勿与其他 pytest 并发） |
| 15 | 更正台账 | `research/stock_pick_2026-10-25/forward/corrections.jsonl` 第 44 行 | `PAPER_SNAPSHOT_STALE_FROM_DAY2` |

**重点请审阅者判断的 7 个问题**：① P0 根因判断与影响推演是否成立、有无更隐蔽的第二条路径；② 方案 A（按日期分文件）是否会破坏现有幂等/备份/回滚语义；③ 3a rc≠0 是否应直接判定日更链失败（涉及 G1 定义）；④ G2 时间线撤回是否过度／不足；⑤ 10 月回测的「v2 后验重演」标注是否足够清晰；⑥ P3「按字面被反驳」的处理是否符合预登记纪律；⑦ 四道门与证据分层是否还有遗漏的口径混用。

---

## 10. 待办、修订时间线与边界

### 10.1 待用户（Agent 不代办）

| # | 事项 | 状态 / 说明 |
| --- | --- | --- |
| **P0 修复授权**（本次新增，最紧急） | 书面授权改 `paper_account_adapter.py` / `shadow_daily_reconcile.py` / `daily_chain.sh` | 需另立变更记录 + 变异 + 云端 scp 实跑；本轮**未动** |
| U-14 | token 轮换 | 用户本人在掘金官网重新生成，只更新 `/opt/quant/env`，**不要贴对话**；工具 `verify_token_rotation.py` 已就绪 |
| U-15 | `paper_authorization.json` | **书面声明已收到**（11-09，8 条，sha `3207bff41a58…`）；**文件仍待账户持有人本人创建**，Agent 不创建/不代填 |
| U-16 | 12-01 演练放行 | 须在 11-30 之后给出；成交回报对账器与演练驱动**至今未实现**（刻意不假接入） |
| U-12 | QMT / 券商通道 | 未确认、未开通 |
| F3 方案选择 | 配置对齐两方案 | 默认不动 |

### 10.2 修订时间线

| 时点 | 事项 | 备注 |
| --- | --- | --- |
| 11-11／12／13／16 | G1 剩余 4 个交易日 | **11-16 收盘后判 G1**（现 6/10） |
| 11-27 17:15 | 月末 DRYRUN 演练 | 含镜像、预检 C11、`--verify --remote`、通道隔离、日历缺口、SHADOW |
| 11-30 | 首个含 `target_holds_d4` 的 FORWARD 台账；19:00 整手一页决策材料 | 生成器 `--publish` 有 `--today>=20261130` 硬闸门 |
| 12-01 09:00 | 交易日历刷新（缺 2027 只记录不阻塞）；12-15／12-31 09:05 补刷 | MANUAL 须国务院通知＋沪深公告双源交叉核对 |
| 12-01 | 仿真月度调仓演练 | **需 U-16 放行**；11-30 前不报单 |
| ~~最早 12-25 判 G2~~ | ⛔ **已撤回** | 须先修 P0，再按「修复后首个含 d4 台账 + 当日真实快照」起算 20 个交易日重新推 |
| 实盘 | **无排期** | 四道门全未过；`LIVE_ENABLED=false` |

### 10.3 边界

11-30 前不向任何账户报单、不改策略参数；`LIVE_ENABLED=false`；未新增因子、未启动实盘。报告中的所有「结论」按第 6.4 节分层使用，**不得**外推为策略有效性判断。

---

## 11. 限制与免责

1. **样本量**：仿真账户已核验样本仅 **2 天**（11-09、11-10），零成交；任何关于盈亏、容量、滑点、最低佣金实感的结论都**没有实证基础**。
2. **单窗口**：10 月回测是单一路径、17 个交易日，`MDE` 本次不可估；借用 MDE 来自不同信号/窗口，**不得**与本次 α 并列判显著。
3. **样本内**：SP-5 44 月与经济性估算均为样本内；`EXPLORATORY_ONLY`。
4. **配置后验**：v2 是 11-03 冻结，用它重演 9-30 属后验重演，**不是样本外**。
5. **台账非真实记录**：11-30 前老台账 `LEGACY_NO_D4`、`SELFTEST`/`LATE_RUN` 事后补跑、纸面 `nav/cash` 未回填，项目已明确不作证据。
6. **自审性质**：本报告由执行 Agent 编写，**不是独立审查**；审阅者请按第 9 节自行复算。
7. **未修复项**：P0 缺陷已登记但未修，G2 时间线不确定；11-10 之后若链路继续按现状运行，快照将一直停在 11-09。
8. **外部通道**：cloud 裸仓库与 origin(GitHub) 均已与本地 `41938c9` 对齐；凌晨的代理 502 已恢复，备用 API 通道仍缺 `github-pat` 配置（不影响本次交付）。

---

## 附录 A：本报告引用的主要文件

| 类别 | 路径 |
| --- | --- |
| 云端日志 | `/opt/quant/var/log/daily_20261109.log`、`daily_20261110.log` |
| 账户快照 | `/opt/quant/research/stock_pick_2026-10-25/forward/account_snapshot.json` |
| SHADOW | `.../forward/shadow_reconcile/20261109.json`、`20261110.json` |
| 心跳 | `.../forward/heartbeat.json` |
| 备份 | `/opt/quant/var/backup/deepquant_evidence_202611{09,10}T*.tar.gz` |
| 预登记 | `research/stock_pick_2026-10-25/SP5_OCT2026_DOWNTURN_PREREG.md`（+`.sha256`） |
| 回测产物 | `research/stock_pick_2026-10-25/oct2026_out/cd5d016b8c9f/`（`metrics.json`、`nav_daily_A/B.csv`、`conclusion_card.md`、`asof_guard.json`） |
| 结论卡 | `reports/OCT2026_DOWNTURN_CONCLUSION_CARD_2026-11-10.docx` |
| 更正台账 | `research/stock_pick_2026-10-25/forward/corrections.jsonl`（44 行） |
| 审阅者指引 | `docs/REVIEWER_GUIDE_2026-11-05.md` |
| 结果索引 | `.workbuddy/memory/RESULTS_INDEX.md` |
| 状态总表 | `STATUS.md` |

## 附录 B：术语

`G1` 链路稳定性门（10 日）｜`G2` SHADOW 规划器一致性门（20 日）｜`S4` 样本外证据（0/7）｜`SHADOW` 影子对账（不接真实下单）｜`REAL_PAPER` 有当日真实账户快照的对账档｜`SELF_CHECK_ONLY` 仅管道自检、`diff` 只记录不判定｜`LEGACY_NO_D4` 11-30 前老台账缺 `target_holds_d4`｜`FORWARD` 11-30 起的前瞻台账｜`MDE` 最小可检测效应（本项目 `2.8016×se`）｜`DESCRIPTIVE_SINGLE_MONTH` 单窗口描述性结论，不进证据分级。
