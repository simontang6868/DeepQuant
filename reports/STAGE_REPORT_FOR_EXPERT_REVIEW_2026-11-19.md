# DeepQuant 阶段性报告（供外部专家审查）

**报告日期**：2026-11-19 23:37 CST｜**范围**：U-16 仿真调仓工程链（11-15 ~ 11-19）
**性质**：工程自证（`StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`），不含样本外统计结论，不进可引集合
**当前提交**：`2054dcd`（本地＝origin＝110 备份＝新机备份一致）；本轮修复待入库
**配套文档**：`U16_EXEC2_DELIVERY_2026-11-19.md`（实施交付）、`U16_EXEC2_H9_CORRECTION_2026-11-19.md`（本次更正）、`SIM_FULL_AUTO_PROPOSAL_V3_FINAL_2026-11-19.md`（设计终稿）

---

## 0. 给专家的一句话摘要

服务器端全自动仿真调仓的**设计已获专家复审无保留通过、工程已实施并入库**；本轮在交付前自检中**发现并修复了一个会让验收第 9 条永远误判失败的端到端 bug（H9：`created_at` 序列化格式不匹配）**。修复是 `bugfix`，不是判据变更，阈值与字段未动。全部验证通过，报告如下，请审查。

---

## 1. 背景与主线进展（11-15 → 11-19）

| 阶段 | 交付 | 状态 |
|---|---|---|
| U-16 授权/预登记 | 版本 A 授权（写死 12-01 09:35）+ `U16-EXEC-1` 预登记，先于代码冻结 | 完成 |
| 报单适配器 | `gm_paper_order_adapter.py`：`--execute` 恒 NOT_AUTHORIZED；8/8 真变异 | 完成 |
| 调仓执行器 | `sim_rebalance_executor.py`：四道门＋幂等 cid＋限价 | 入库 `822fb97` |
| 全自动方案 | v1→v2→v3＋专家三轮审查，四项裁决全吸收 | 设计闭环 |
| **U16-EXEC-2 实施** | 六门＋旁证＋哨兵＋8 个 systemd 单元；书面授权＋新预登记先冻结 | 入库 `f837dc8` |
| **H9 修复（本轮）** | `created_at` 序列化 bug 修复＋更正记录 | 待入库 |

**设计三层裁决已闭合**：E0 时钟门（±10min＋NTP；授权时间窗实测 38h，E0 是唯一精度门控）｜旁证"冻结发布程序而非哈希值"（11-30 告警邮件外发 sha256，tamper-evidence）｜失败日志 `sim_exec_result/v1` 原子写＋哨兵四态｜不新增时间源（`execution_reports.created_at` 落在 09:35±10min 作验收第 9 条）。

---

## 2. 本轮核心：H9 bug 的发现与修复

### 2.1 为什么会在交付后还在查

上一轮交付报告 §3 留了一条未闭合项："`ExecRpt.created_at` 的服务端语义须上线后核对，若为客户端时点则验收第 9 条失效"。用户要求"修复好"，遂对该字段做了实机逐层追踪，结果发现问题比预想的严重——不是"语义待核对"，而是**判据根本跑不通**。

### 2.2 根因链（每层都是实机证据，非推测）

```
ExecRpt.created_at (google.protobuf.Timestamp, seconds+nanos, 服务端打点)
  │  get_execution_reports() 返回 protobuf ExecRpt 对象
  ▼
paper_account_adapter._as_plain → _plain_value(v)
  │  旧代码：不认识 Timestamp ⇒ return str(v)
  ▼  str(Timestamp) = 'seconds: 1796088912\n'   ← 非 ISO
sim_reconcile.check_exec_timestamps
  │  fromisoformat('seconds: ...') ⇒ 抛异常 ⇒ created_at:UNPARSEABLE
  ▼
验收第 9 条：真实报单后 100% FAIL（与数据时点无关）
```

这是项目复发过多次的"判据写错"模式的新实例：闸门恒假，看起来只是"没通过"，实际是永远不可能通过。

### 2.3 修复

- `paper_account_adapter.py` 新增 `_proto_timestamp_to_iso()`：用 `DESCRIPTOR.full_name == "google.protobuf.Timestamp"` **精确识别**类型（不猜字段），按 `seconds + nanos//1000` 转 UTC 再转 `+08:00` 北京时间 ISO-8601；识别/读取失败 ⇒ 返回 `None` ⇒ 仍走 `str(v)` 失败关闭。
- `sim_reconcile.py` 的判据函数**一行未改**。
- 向后兼容旧 ISO、非 Timestamp 仍失败关闭。

### 2.4 修复验证

| 检查 | 结果 |
|---|---|
| 本地 6 项专项测试 | 全 PASS（Timestamp→ISO、nanos 舍入、准点 PASS、超窗 FAIL、未知对象失败关闭、旧 ISO 兼容） |
| 云端**真实 protobuf**（非 stub） | 旧 `str(v)='seconds: ...\n'` → 修复后 `2026-12-01T09:35:12+08:00`；真实 `ExecRpt` 走 `_as_plain` 正确；验收第 9 条 PASS |
| 判据未放宽 | 超窗样本判 `SKEW:25min` FAIL |
| 真变异 | 退回 `str(v)` ⇒ 探针由绿转红（DETECTED），恢复后 sha 一致 |
| 回归四连 | 3651/1｜9/9｜**759/1**（原 753＋新 6）｜verify PASS |
| manifest | 63 文件 `47d98599`；本地 PASS、云端 63/63 PASS |
| 云端文件一致 | adapter `3901edca`、sim_reconcile `6f1d39fa` 逐位一致 |

### 2.5 一次自我纠错（请专家留意）

真变异首跑 **MISSED**：根因是测试文件顶部 `sys.path.insert` 令 pytest 收集时重新 import 磁盘原文件，**覆盖了注入的变异模块**，探针实际测的是原始实现——一个"假变异"变种。改为直接调用被变异模块函数做断言后 DETECTED。已写入更正记录 §5。

---

## 3. 模拟盘真实状态（截至 11-19，数据到 11-19 收盘）

| 指标 | 读数 | 含义 |
|---|---|---|
| 账户快照 | 11-09/11/12/13/16/17/18/19 逐交易日抓取；现金 10 万、持仓 0 | 通道通，但**无任何成交** |
| shadow 对账 | 8 天产物，`counts_toward_g2=true` **0 天** | G2 **0/20**，未开始计数 |
| G1 严格口径 | 云端 `--local` 曾测 5/10（11-12/13/16/17/18 全绿）；条件式判定日 11-25 | 本机因缺 stage_results 失败关闭（预期） |
| 执行器四门→六门 | E3 授权 ALLOW 6/6 真过；E0/E1/E2/E4 按当前日期拦截 | `--execute` 恒 rc=4，零报单 |
| systemd 定时器 | 8 个一次性单元已部署＋daemon-reload，**未 enable 任何一个** | 12-01 前无自动触发 |

**核心判断**：管道（抓取/对账/心跳/门控）很健康，但**第一笔真实委托尚未发生**。这是设计——12-01 09:35 前四道生效条件不成立，不放行。

### 3.1 12-01 前的剩余前置

1. **11-27 17:20**：DRYRUN timer（prep）产出；本机出证＋告警邮件外发旁证 sha256（一次性人工前置）。
2. **11-30 17:10**：日更链产出 `mode=FORWARD`＋`target_holds_d4` 台账；preflight 事件驱动（等 ledger 完整，非墙上时钟）。
3. **12-01 09:35**：exec timer 触发；09:45 哨兵；18:10＋结算后对账（含验收第 9 条 `created_at` 窗口——**本轮修复后它第一次真正可用**）。

---

## 4. 明确的边界与限制（引用必带）

1. 定时器已部署未启用；`Persistent=false`，错过不补跑（事后补跑会产生"格式合法、日期错误"记录，更危险）。
2. 11-30 前仍有一次性人工前置（本机出证＋邮件外发旁证 sha）；12-01 当天零人工。
3. 旁证锚定是**事后可检测（tamper-evidence）**，非事前预防；单主机内无机器可验的外部锚。
4. P1/P2 无法检验：SDK 无 `client_order_id` 入参⇒幂等仅本地；无行情接口⇒10:30/14:30 重定价不可实现（CARRY 顺延）。按预登记停机出更正，不改判据。
5. `SIM_DRILL`，不计 G1/G2/S4，不作收益判断，`LIVE_ENABLED=false`，不碰任何授权文件与 token。
6. H9 修复是 bugfix，不产生样本外证据；`MaturityLevel=BUGFIX_VERIFIED`。

---

## 5. 请专家裁决/复核的问题

1. **H9 定位是否认同**：本次把"服务端语义待核对"重新定性为"序列化格式 bug"，并以 SDK 源码（proto Timestamp＋pb_to_dict UTC→北京）作为服务端语义证据，是否足以闭合？还是仍需实盘首笔回报的 `created_at` 原值再确认一次语义？
2. **修正仅落在 adapter 序列化层、判据函数零改动**，是否符合"bugfix 不必走新预登记"的纪律？若专家认为仍属判据相关变更，我可补一份迷你预登记追认。
3. **真变异 MISSED→DETECTED 的教训**：pytest 重 import 覆盖注入模块导致假变异。建议团队变异框架一律改为"直接调被测函数"——是否纳入检查器规范？
4. **12-01 端到端仍未实弹验证**：所有报单路径都是离线 mock＋云端门控实跑，从无一笔真委托。是否要求在 12-01 前用 PROBE 探针（2 只×1 手）先打通一次真实下单→回报→对账闭环，再执行 U-16 主演练？

---

## 6. 交付物清单（本轮，含专家复审处置）

| 文件 | sha256(前16) | 说明 |
|---|---|---|
| `scripts/paper_account_adapter.py` | `3901edca` | Timestamp→ISO 修复（本地＝云端一致）【修改】 |
| `scripts/sim_reconcile.py` | `6f1d39fa` | 判据函数零改动，本轮**未修改此文件** |
| `scripts/sim_exec_result.py` | `cef2fa7b` | 哨兵新增 STALE 新鲜度态（专家发现 1）【修改】 |
| `scripts/tests/test_created_at_pb_fix.py` | `d3606a1b` | H9 6 项专项测试【新增】 |
| `scripts/created_at_fix_mutation_test.py` | `96511c25` | H9 真变异（DETECTED）【新增】 |
| `scripts/sentinel_freshness_mutation_test.py` | `235745cb` | 哨兵新鲜度真变异（DETECTED）【新增】 |
| `.gitattributes` | `38a523b5` | scripts/deploy/research/reports -text（专家发现 2）【修改】 |
| `docs/CHECKER_AND_MUTATION_NORM_2026-11-19.md` | `fd8236f9` | bugfix 判别式 + 变异规范【新增】 |
| `research/.../deploy_manifest.json`（及 `.prev.json`） | `e0a301be` | 清单重生成【修改】 |
| `reports/U16_EXEC2_H9_CORRECTION_2026-11-19.md` | 见 `.sha256` 侧车 | H9 更正记录（原文不改写，§7 追加裁决落地）【新增】 |
| 本文件 | 见 `.sha256` 侧车 | 阶段性报告【新增】 |

**入库状态**：用户已授权入库推送（本轮末执行）。清单 `sha_of_files` 最终 `7660e47c`（H9 修复后 `47d98599` → 加哨兵新鲜度后 `7660e47c`）。不含 6 个 `.bak` 测试产物；两份 `sim_exec/*.TEST_*.json` 为隔离的测试产物，不入库。

---

## 7. 专家复审处置（2026-11-19 23:49 后，追加不改写）

专家对 H9 报告给出四项裁决 + 两项新发现，全部处置：

### 7.1 四项裁决

- **Q1（H9 语义，认同）**：SDK 三层源码接受为强指示性语义证据，不要求 12-01 前单独确认。失效方向单向安全（只会假 FAIL 停机，不会假 PASS）。若采纳 Q4 PROBE，首笔回报双记录原始 `seconds/nanos` + ISO 闭合语义。
- **Q2（bugfix 分类，成立）**：落「三零一不变」判别式入 `docs/CHECKER_AND_MUTATION_NORM_2026-11-19.md` §1，显式记录判别过程防惯性滥用。
- **Q3（变异框架，纳入）**：金丝雀前置 + 直接调用被测单元 + 阴性对照三条强制规则入规范 §2/§3；本次假变异 MISSED 重分类为 **VOID**（实验无效，非覆盖不足）。
- **Q4（PROBE，要求为 go/no-go 前置）**：**未实施**——需独立预登记 + 账户持有人书面授权，本轮未授权。标注为待决项，12-01 前的 go/no-go 检查点 11-27 17:00。

### 7.2 两项新发现（入库前已处置）

| 发现 | 等级 | 处置 | 验证 |
|---|---|---|---|
| 生产命名过期结果文件占哨兵路径 | 中 | 哨兵新增 `started_at` ±10min 新鲜度校验（窗口外判 STALE）；当前 `20261201_result.json` 改名 `.TEST_stale_20261119.json` 隔离；生产路径清洁规则 | self_check 6 项含 `stale_detected`；隔离前判 STALE（检出 16439.8min）、隔离后判 MISSING；真变异 DETECTED |
| autocrlf 破坏 manifest 字节地基 | 高 | `.gitattributes` 增 `scripts/**`、`deploy/**`、`research/stock_pick_2026-10-25/**`、`reports/**` 的 `-text` | `git check-attr` 全部 `text: unset`；暂存区 blob 字节 == 工作树字节、CRLF=0（4 文件实测）；63/63 本地+云端 PASS |

**期初现金口径核对答复（专家 Q4-2 要求）**：U-16 预登记 V2 未钉死期初现金 100000（仅 `notional=NAV×0.25/K`）；PROBE 预登记 `nav=100000` 是基线、通过标准是"零未授权/重复/错误订单计数"而非现金相等 ⇒ 往返手续费致现金略≠100000 不构成矛盾，无需更正。

### 7.3 处置后验收

回归四连 **3651/1｜9/9｜759/1｜verify PASS**；哨兵 self_check 云端 PASS；manifest `7660e47c` 本地+云端 63/63 PASS；H9 真变异 + 哨兵新鲜度真变异均 DETECTED、sha 恢复。
