# T1 全流程留痕改造：设计与验收记录

> 对应《Vibe-Trading 调研结论落地推进计划 v1.1》§5.2 **T1（P0）**。
> 编制日期 2026-10-27｜基线提交 `5cfed94`｜本阶段只做加法，未修改任何既有业务语义。

## 0. 结论速览

**四项硬性验收标准全部达成（实测数字，非估计）**

| 验收标准 | 实测 | 判定 |
|---|---|---|
| 核心操作工件覆盖率 100% | **6 / 6 = 100%** | PASS |
| 五元组字段完整率 100% | **5,000 / 5,000 = 100%** | PASS |
| 任一历史工件回放 < 5 秒 | p50 **14.5 ms** / p95 16.5 ms / max **21.0 ms**（5,000 张卡规模） | PASS |
| 工件不可篡改（追加式 + 哈希校验） | 全链校验 **ok，0 失败项**；篡改/删卡/删索引/截断 4 类攻击全部可检出 | PASS |
| 不存储敏感凭证明文 | 构造期递归脱敏，只留字段路径 | PASS |

补充规模数据：5,000 张卡（2 个分片），写入 **75.26 s**，全链校验 **0.45 s**。
既有测试基线 **1,997 passed / 1 skipped** 未被破坏（本阶段新增 53 项，合跑 2,050 passed）。

## 1. 落地前的能力盘点（决定「新建还是扩展」）

计划第 10 节把「现有底座」列为待决策项。盘点结果如下，这是 T1 设计的前提：

| 能力 | 落地前状态 | T1 的处理 |
|---|---|---|
| 数据内容摘要 | `performance.reproducibility.dataset_summary`（确定性标准化的 sha256） | **复用**，不重造 |
| 结果文档摘要 | `persistence.store.result_digest` + `audit_run_integrity` | **复用**其语义分层（三摘要互不替代） |
| 篡改可检测 | `performance.window_records_integrity`（manifest + `windows.csv` 摘要） | **复用范式**，扩展到「操作」而非「实验」 |
| 统一审计日志 / 事件总线 | **不存在**（仅 `strategy_runs` 表记录回测结果，允许 UPDATE） | **新建**（T1 主工作量） |
| 委托域（录入/审核/改单/撤单） | **不存在**（`api/routes` 只有 backtests/datasets/strategies/portfolio，无券商连接） | 登记为 `not_applicable`，不伪造实现 |
| 业务动作 schema 注册表 | 仅 `api.registry` 的策略注册表（非业务动作） | 留给 **T4** |

结论：T1 是**新建**而非扩展，且不存在与既有审计体系的冲突（计划风险表第 2 条的实际答案：无重复建设风险）。

## 2. 架构与数据流

```
接口请求 ──► AuditMiddleware（默认关闭）
                │  1. 查 OPERATION_ROUTES（精确路径匹配，不做前缀匹配）
                │  2. 记录请求摘要：方法/路径/查询/请求体摘要（>64KB 只留摘要）
                │  3. 执行原业务（不改变任何语义）
                │  4. 按状态码分类 ok / rejected / failed
                ▼
        AuditStore.append()
                │  锁内：读链尾 → 构卡 → 封章 → 写分片 → 写索引
                ▼
  audit_dir/cards/cards-YYYYMM.jsonl   （完整卡片，append-only）
  audit_dir/index.jsonl                （检索索引，append-only）
  audit_dir/self_failures.jsonl        （写入失败自检日志，append-only）

业务代码（非接口层）可直接用：
  with record_operation("xxx", operator=..., basis=...) as draft:
      draft.outcome[...] = ...
      draft.note("...")
```

**开关**：`DEEPQUANT_AUDIT_DIR=/path` 才启用。未设置时中间件立刻放行——不读请求体、不建目录、不加响应头。这是既有 1,997 项测试行为不变的原因，而不是「写了空目录反正没影响」。

## 3. 工件结构：五元组 + 四个结构字段

| 字段 | 类别 | 内容 | 为什么必须有 |
|---|---|---|---|
| `operator` | **五元组** | 操作人（接口请求头 `X-Operator`，缺省 `anonymous` 并标注不具追责效力） | 没有责任主体的记录不是审计 |
| `occurred_at` | **五元组** | UTC 微秒精度 | 工件跨机器比对，本地时间会让「谁先谁后」随部署地点变化 |
| `basis` | **五元组** | 依据规则 + 请求摘要（含请求体 JSON 摘要） | 「凭什么做」比「做了什么」更容易被省略 |
| `outcome` | **五元组** | `status`(ok/rejected/failed) + 状态码 + 耗时 + 错误类型 | 失败操作同样要留痕 |
| `caveats` | **五元组** | 注意事项（字符串列表，允许空但**不允许 `None`**） | 本研究线引用结论必带限定；把「没写」与「写了没有」分开 |
| `action` / `subject` | 结构 | 操作类型 / 对象 | 回放检索维度 |
| `seq` / `prev_sha256` / `card_sha256` | 结构 | 序号 / 前卡摘要 / 自身摘要 | 链式防篡改的锚点 |

`card_digest` **排除** `card_sha256` 自身（否则摘要无法自洽）；`canonical_json` 用键排序 + 无空白，避免字典插入顺序不同导致摘要无故变化。

## 4. 关键设计与取舍（含被否掉的方案）

**① 为什么是文本文件 + 哈希链，而不是 SQLite 表**
`persistence.store` 允许 `UPDATE`（补写派生字段），语义是「当前状态」；审计工件的语义是「发生过什么」，一旦可改写就不能当证据。追加式文件的写路径只有一条，代码里不存在「某处悄悄改一行」的可能。代价：不支持事务、需要自己处理并发（已用文件锁 + 链尾校验解决）。

**② 为什么用中间件，而不是逐路由插代码**
逐处插 `with` 有两个问题：新加路由没人会记得补；改动核心链路会让既有测试基线变得可疑。中间件把「记录」变成横切关注点，新增路由只需在 `OPERATION_ROUTES` 加一行，覆盖率检查会提醒。

**③ 为什么写入失败不阻断业务**
中间件在业务**执行完之后**才写工件。此时若因磁盘/权限失败就抛错，客户端会收到 500，而回测或交易其实已经完成——那是在制造新的不确定性。本层策略：`self_failures.jsonl` + 响应头 `X-DeepQuant-Audit: failed` + 错误日志，三处留痕。
**「拒绝执行」属于 T2**：它必须在执行**之前**判断，不能靠审计层事后补救。

**④ 链式校验能发现什么、不能发现什么**
能发现：字段被改、卡片被删、索引被删、尾部被截断、索引与卡片不一致。
**不能发现**：整条链被从头重算并整体替换（攻击者持有全部内容时，任何单向哈希链都挡不住）。这一点写进模块文档，不假装能防。防它需要外部锚定——`chain_fingerprint()` 导出「链尾摘要 + 各分片文件摘要」，供调用方记到不可改写处（发布单、git tag、邮件）。

**⑤ 被否掉的方案**：把 `caveats` 设为可选（`None` 时自动填「无」）。否掉的理由：本项目已经反复踩过「没检出 ≠ 没问题」的坑，自动填空值会让「没人写限定」看起来像「已确认无限定」。

**⑥ 分片内行序不参与校验**（顺序由索引定义）。若把文件行序当链条顺序，任何按内容重排文件的工具（备份还原、去重脚本）都会让审计无辜报警，久而久之运维就习惯性忽略报警——那比没有校验更糟。

## 5. 核心操作清单与「委托域」的处理

| op_id | 说明 | 在范围 | 实现位置 |
|---|---|---|---|
| `backtest.run` | 单标的回测执行 | ✅ | `audit.middleware:OPERATION_ROUTES` |
| `backtest.compare` | 历史结果比较（只读） | ✅ | 同上 |
| `portfolio.backtest` | 多标的组合回测 | ✅ | 同上 |
| `dataset.list` | 数据集枚举读取 | ✅ | 同上 |
| `strategy.list` | 策略注册表读取 | ✅ | 同上 |
| `audit.replay` | 审计回放查询（查审计也要留痕） | ✅ | `audit.operations:replay` |
| `order.entry` / `order.review` / `order.amend` / `order.cancel` | 委托录入/审核/改单/撤单 | ❌ `not_applicable` | 无（见下） |

**委托域为什么不在范围**：计划 T1 原文针对「电话交易平台」的委托动作，而本仓库是研究回测平台，没有委托域（无券商连接、无订单状态机）。这属于计划第 10 节第 1 项「业务形态确认」未决事项，不是实现缺失。
处理方式：登记为 `in_scope=False` 并写明原因，**既不伪造实现，也不计入覆盖率分母**。等业务形态确认，只需把 `in_scope` 改 `True` 并填 `instrumented_at`，机制层不改。

## 6. 验收证据（可复现）

```bash
cd "<repo>"
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

# ① 新增 53 项行为测试
cd backend && PYTHONPATH=src $PY -m pytest tests/audit -q

# ② 四项验收取证（输出 JSON 到 reports/AUDIT_T1_EVIDENCE.json）
cd .. && PYTHONPATH=backend/src $PY scripts/audit_t1_evidence.py

# ③ 全量回归（应为 2050 passed / 1 skipped）
cd backend && PYTHONPATH=src $PY -m pytest tests -q
```

测试覆盖的负向断言（证明"行为正确"而非"程序能跑"）：

| 测试 | 断言的行为 |
|---|---|
| `test_missing_required_field_raises`（5 参数） | 五元组缺任一项必须报错，不能补空值 |
| `test_caveats_must_be_list_not_string` | 误传字符串不得被拆成单字符列表蒙混过关 |
| `test_basis_must_be_mapping` | 类型错误在转换前拦截 |
| `test_card_digest_excludes_self_field` | 摘要自洽性 |
| `test_verify_detects_field_tampering` | 改一个字段即可检出（含 seq 定位） |
| `test_detects_deleted_middle_card` | 删中间卡可检出 |
| `test_detects_orphan_card_when_index_line_removed` | 删索引行可检出（不当作没发生） |
| `test_detects_truncated_shard_line` | 截断可检出 |
| `test_default_off_adds_no_header_and_creates_nothing` | 未启用时零行为变化 |
| `test_unregistered_route_is_not_recorded` | 不在清单的接口不产生噪声工件 |
| `test_missing_operator_header_is_flagged` | 缺操作人不得假装有人负责 |
| `test_out_of_scope_operations_have_reason` | 不在范围必须写原因，禁止"没做到"冒充"不需要" |

## 7. 已知限制（写清楚，避免被当成已解决）

1. **单卡写入约 15 ms**（2 次 `fsync`：分片 + 索引）。5,000 张卡写入 75 s。对接口级审计可接受（一次回测耗时远大于此），但**高频委托场景需要批量提交**——这是 T2 之前必须解决的前置项。
2. **无外部锚定**：链尾摘要目前只落在本地，需由发布流程或定时任务记到外部不可改写处才算完整。
3. **无保留期与归档策略**：分片按月增长，尚未定义保留年限与冷归档。
4. **无查询 UI**：回放目前是 Python API（`AuditStore.query` / `operations.replay`），没有前端。
5. **中间件层工件不含业务内部判据**：只覆盖请求摘要与响应状态；更细的依据需在业务层用 `record_operation` 补充（该接口已就绪）。
6. **请求体 > 64 KB 只记摘要**：这是防膨胀与防敏感信息扩散的取舍，不是遗漏。

## 8. 与后续任务的接口

| 后续任务 | 与本模块的关系 |
|---|---|
| **T2 Fail-closed 网关** | 拒绝原因码写入 `outcome.reason_code`；拒绝事件与本工件天然关联（同一次请求同一张卡） |
| **T3 依赖降级链** | 切换事件可用 `record_operation("dep.switch", ...)`，延迟样本写入 `extra` |
| **T5 退出清理不变量** | 四步清理各自落卡，"已验证清空"须有可检索证据（清理日志 + 快照比对） |
| **T6 影子复盘** | 直接消费 `index.jsonl` 的操作序列做规则提取 |

## 9. 复现与变更清单

新增文件：

- `backend/src/deepquant/audit/{__init__,run_card,store,operations,middleware}.py`
- `backend/tests/audit/{conftest,test_audit_run_card,test_audit_store,test_audit_operations,test_audit_middleware_api}.py`
- `scripts/audit_t1_evidence.py`
- `reports/AUDIT_T1_EVIDENCE.json`（取证输出）

修改文件（各一处，均为追加式）：

- `backend/src/deepquant/api/app.py`：`add_middleware(AuditMiddleware)` + 注释说明默认关闭。

未修改：任何既有模块的业务语义、数据库 schema、API 契约、冻结配置。
