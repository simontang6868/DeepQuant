# U16-EXEC-2 更正记录：H9 `created_at` 服务端语义闭合（2026-11-19）

> 性质：**实现 bug 修复 + 证据闭合**，非判据变更。验收第 9 条的阈值（±10min）、
> 字段（`execution_reports.created_at`）、失败关闭语义**一律未改**。
> 按项目纪律：原文不改写，更正另立记录。被更正值见 `reports/U16_EXEC2_DELIVERY_2026-11-19.md` §3 第 5 条与 §3“实施前待确认”。

## 1. 被更正的原文（逐字引用，不改）

交付报告 §3 边界第 5 条原文：

> `ExecRpt.created_at` 的服务端语义**须上线后核对**；若为客户端时点则验收第 9 条失效并出更正（H9）。

## 2. 根因（实机逐层追踪，非推测）

| 层 | 事实 | 证据 |
|---|---|---|
| SDK 字段类型 | `ExecRpt.created_at` 是 `google.protobuf.Timestamp`（message，type=11/cpp=10，子字段 `seconds`+`nanos`） | 云端 `.venv-gm` 读 `ExecRpt.DESCRIPTOR.fields` 实测 |
| 服务端语义 | `get_execution_reports()` 返回 `ExecRpt` protobuf 对象，时间戳由掘金柜台打点；SDK `pb_to_dict.py` 明确按 UTC 秒 + `is_utc_time=True` 转北京时间处理 | `gmtrade/api/trade.py:516`、`pb_to_dict.py:64-70`、`utils.py:168` 源码实读 |
| 落盘缺陷 | `paper_account_adapter._plain_value` 对非 `dt.datetime` 走 `str(v)`；protobuf Timestamp 的 `str()` = `'seconds: 1796088912\n'`（非 ISO） | 云端真实 `Timestamp` 实测，旧行为输出逐字如上 |
| 判据失效 | `sim_reconcile.check_exec_timestamps` 用 `datetime.fromisoformat` 解析 → 抛异常 → 每条报 `created_at:UNPARSEABLE` → 验收第 9 条**真实报单后永远 FAIL** | 本地 stub + 云端真实 pb 双向复现 |

**结论**：这不是"数据时点是否服务端"的问题（服务端语义已由 SDK 源码证实），而是**序列化格式与解析器不匹配**——又一起"判据写错导致恒判失败/恒判通过"的实例，与项目铁律「没检出 ≠ 没问题」「闸门禁止恒真/恒假」同类。

## 3. 修复内容（最小侵入）

- `scripts/paper_account_adapter.py`：新增 `_proto_timestamp_to_iso()`，按 `DESCRIPTOR.full_name == "google.protobuf.Timestamp"` **精确识别**类型（不猜字段），用 `seconds + nanos//1000` 转 UTC 再转 `+08:00` 北京时间 ISO-8601；`_plain_value` 优先调用它。识别失败/读不出秒数 ⇒ 返回 `None` ⇒ 仍走 `str(v)` 失败关闭，不伪造时间。
- `scripts/sim_reconcile.py`：`check_exec_timestamps` **未改**（阈值、字段、解析失败关闭全部保留）。
- 向后兼容：旧版已落盘的 ISO 字符串仍可解析；非 Timestamp 未知对象仍转 str 并判 UNPARSEABLE。

**未改**：验收第 9 条任何判据；U16-EXEC-1/EXEC-2 预登记正文；授权文件；策略参数；定时器。

## 4. 验证证据

| 检查 | 结果 |
|---|---|
| 本地 stub 全链路（6 项测试） | 6 passed：Timestamp→ISO、nanos 舍入、准点 PASS、超窗 FAIL、未知对象失败关闭、旧 ISO 兼容 |
| 云端真实 protobuf 全链路 | `str(v)='seconds: ...\n'` → 修复后 `2026-12-01T09:35:12+08:00`；真实 `ExecRpt` 走 `_as_plain` 正确；验收第 9 条 PASS（`CLOUD_PB_ALL_OK`） |
| 判据未被放宽 | 超窗样本（北京 10:00）判 `SKEW:25.00min` FAIL |
| 真变异 | 把 Timestamp 转换退回 `str(v)` ⇒ 探针由绿转红（DETECTED）；基线绿；恢复后 sha 一致（`3901edca`） |
| 回归四连 | 3651/1｜9/9｜**759/1**（原 753 + 新 6）｜verify PASS |
| manifest | 63 文件 `sha_of_files=47d98599`；本地 PASS、云端 63/63 PASS；云端 json 已同步归主 |
| 文件一致性 | adapter `3901edca`、sim_reconcile `6f1d39fa` 本地/云端逐位一致 |

**时间**：修复取证 2026-11-19 23:37 CST（shell 实测，非推算）。

## 5. 过程中的自我纠错（须记录）

首次运行真变异时 **MISSED**——变异未让测试变红。根因：测试文件顶部 `sys.path.insert(0, "scripts")` 使 pytest 在收集时重新 import 磁盘原文件，**覆盖了注入的变异模块**，探针实际测的是原始实现（假变异）。

修正：变异探针不再经 pytest 收集，改为直接调用被变异模块的函数做断言（`_probe(mod)`），使"被测对象 == 变异对象"。修正后 DETECTED。这是「变异必须真改行为、须确认被测对象就是变异体」的新变种，记入以警示后来者。

## 6. 闭合声明

H9 由"上线后核对"**闭合**为"源码+实机双层证实服务端语义，且序列化缺陷已修复并验证"。
`reports/U16_EXEC2_DELIVERY_2026-11-19.md` §3 第 5 条的"待核对"状态自此失效，以本记录为准；原报告原文保留不改。

引用口径：`StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`，`MaturityLevel=BUGFIX_VERIFIED`；本修复不构成样本外证据，不进可引集合。

## 7. 专家裁决落地（2026-11-19，追加不改写）

- **Q2（bugfix 分类）**：本修复按「三零一不变」判别式成立，判别过程见
  `docs/CHECKER_AND_MUTATION_NORM_2026-11-19.md` §1。该判别式已纳入规范，
  作为未来"判据邻接 bugfix"的分类标准，防豁免被惯性引用。
- **Q3（变异框架）**：§5 的假变异 MISSED 已按规范重分类为 **VOID**（实验无效，
  非覆盖不足）；金丝雀前置 + 直接调用被测单元 + 阴性对照三条强制规则见规范 §2/§3。
- **Q1（H9 语义）**：专家接受 SDK 三层源码为强指示性证据，不要求 12-01 前单独
  语义确认；若采纳 PROBE 则首笔回报双记录原始 `seconds/nanos` + ISO 闭合语义。
- **Q4（PROBE）**：被列为 12-01 go/no-go 前置，但需**独立预登记 + 账户持有人
  书面授权**，本轮未授权 ⇒ 未实施，标注为待决项（见阶段性报告 §5）。
- **期初现金口径核对答复**：U-16 预登记 V2 未钉死期初现金 100000（仅
  `notional=NAV×0.25/K`）；PROBE 预登记 `nav=100000` 是基线、通过标准是
  "零未授权/重复/错误订单计数"而非现金相等 ⇒ 往返手续费致现金略≠100000
  不构成矛盾，无需更正。
