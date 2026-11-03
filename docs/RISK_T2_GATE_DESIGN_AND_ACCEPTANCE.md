# T2 Fail-closed 风控网关：设计与验收记录

> 对应《Vibe-Trading 调研结论落地推进计划 v1.1》§5.2 **T2（P0）**。
> 编制日期 2026-10-27｜基线提交 `5cfed94`｜依赖 T1（已完成）。

## 0. 结论速览

**四条硬性验收标准全部达成（实测，见 `reports/RISK_T2_EVIDENCE.json`）**

| 验收标准 | 实测 | 判定 |
|---|---|---|
| 四类检查点覆盖率 100% | 4 / 4 类别，12 个原因码，无缺类别 | PASS |
| 每次拒绝均有原因码且可检索 | 4 类场景各产出原因码，且都能按 `reason_code` 从 T1 工件中查回 | PASS |
| 渗透测试未发现绕过路径 | 8 条路径变体（含 `/api` 前缀、双斜杠、大小写、尾斜杠）全部被拦或未命中路由 | PASS |
| 拒绝不依赖「人工记得查」 | 全局中间件 + 路由清单对账（未覆盖可变更接口 = 0） | PASS |

补充：`observe` 模式（灰度）实测 200 + `X-DeepQuant-Risk: would-reject`，且 T1 工件记录 `would_reject=true`。
既有的 2,050 项测试未被破坏（本阶段新增 82 项 → 全量 **2,132 passed / 1 skipped**）。

## 1. 与计划的一处结构性差异（必须先说）

计划的四类检查点是为「电话交易平台」写的：身份（坐席）、限额（单笔/日累计）、
状态（账户/通道）、字段（委托要素）。本仓库是**研究回测平台**，没有账户、没有委托域。
若照字面实现，会得到四个空壳检查点——"覆盖率 100%"但什么都没拦。

因此映射为**同语义、可执行的等价物**（该映射已经过确认）：

| 计划四类 | 本仓库实现 | 真实可拒绝条件 | 原因码 |
|---|---|---|---|
| 身份未核实 | `IdentityCheckpoint` | 未带 `X-Operator`；或不在允许名单 | `identity_missing` / `identity_not_allowed` |
| 限额超限 | `LimitCheckpoint` | 请求体 > 2 MB；初始资金超出 1e3~1e9；回测区间 > 30 年 | `limit_body_too_large` / `limit_cash_range` / `limit_date_span` |
| 状态异常 | `StateCheckpoint` | 数据集未登记/不可用；策略未登记；引用的 `run_id` 不存在；**查询失败（无法校验）** | `state_*` / `checkpoint_unverifiable` |
| 关键字段缺失 | `FieldCheckpoint` | 必需字段缺失或为空（空串/空列表） | `field_missing` / `field_empty` |

限额的取值刻意比正常研究请求宽一到两个数量级：**风控不该用限额否决研究设计**。

## 2. 三种模式：解决计划里两条互相拉扯的要求

计划要求「默认拒绝执行」，风险登记又要求「先告警不拦截两周再转强制」。
把两者塞进一个开关，结果一定是有人为了跑通业务关掉它然后忘了开。所以拆成三种：

| 模式 | 判定 | 是否拦截 | 用途 |
|---|---|---|---|
| `off`（默认） | 不判定 | 否 | 既有部署与测试零变化 |
| `observe` | **与 enforce 完全相同** | 否 | 灰度：数出"上线后会拦多少" |
| `enforce` | 与 observe 相同 | 是 | 正式生效 |

**关键不变量**：判定逻辑与模式无关，模式只影响"要不要拦"。若观察期顺手放宽判定，
灰度数字就是假的。该不变量由 `test_judgement_is_identical_across_observe_and_enforce` 钉住。

非法模式值（如 `semi`）→ **整体拒绝并返回 503**，不静默降级为 `off`：
"以为开了但其实没开"是这类系统最危险的失败态。

## 3. Fail-closed 的三处落地

计划的设计要点是「算不出就说算不出」。具体落到三个位置：

| 场景 | 处理 | 为什么不能放过 |
|---|---|---|
| 状态查询依赖不可用（数据集目录读不到、DB 连不上） | `checkpoint_unverifiable` → 503 拒绝 | 返回「不存在」会把基础设施故障伪装成用户输入错误，误导排查方向 |
| 检查点自身抛异常 | `gateway_internal_error` → 503 拒绝 | "跳过这一项继续"等于把这一项的风险静默清零 |
| 未注入查询能力 | `checkpoint_unverifiable` | 不放行：那等于"没检查"却显示"通过" |

对应地在装配层区分两件事：**"不存在"返回 `None`，"查不了"抛异常**。
`api/app.py::_risk_lookups` 里只有 `DatasetNotFoundError` / `DatasetUnavailableError`
被映射为 `None`，其余异常一律上抛。

## 4. 旁路检测：为什么不能只说"我们装了中间件"

真实绕过不是删中间件，而是**新加一条路由忘了登记操作类型**：网关按 `op_id` 决定
跑哪些检查点，未登记 → 全部 `applies_to` 为假 → 一路放行，而日志里看不出异常
（"没被拒绝"和"没被检查"长得一样）。

`risk/bypass.py` 做的是清单对账：

1. 展开真实路由表（含方法）→ 找出所有可变更接口（POST/PUT/PATCH/DELETE）；
2. 与 `OPERATION_ROUTES` 对账，未映射的即"可绕过面"，必须显式豁免并**写明理由**；
3. 反向核对：映射里登记的路径必须真实存在（防止路由改名而映射失效）；
4. 核对两个中间件都已装配、映射无重复。

**实施中发现的一个坑（值得记）**：新版 FastAPI（0.143 / Starlette 1.9）把
`include_router` 包成 `_IncludedRouter` 惰性对象，路径**不在** `app.routes` 里。
直接扫 `app.routes` 会得到"只有 4 条框架路由、零个可变更接口"的假结论——
检测器自己瞎了，报告却显示"通过"。修正方式是沿 `original_router` 递归展开，
并与 `openapi()` 的路径取并集（宁可多报也不漏报）。

## 5. 渗透测试：针对"路径归一化差异"的定点试探

| 变体 | 结果 | 说明 |
|---|---|---|
| `/backtests` | 403 `identity_missing` | 正常拦截 |
| `/backtests/` | 403 | 尾斜杠在 307 重定向**之前**已被拦 |
| `//backtests` | 404 | 未命中路由，不构成绕过 |
| `/BackTests` | 404 | 同上 |
| `/backtests?symbol=...` | 403 | 查询串不影响查表 |
| `/backtests/compare` | 403 | 兄弟路由各自受检 |
| `/portfolio-backtests` | 403 | 组合回测同样受检 |
| `/api/backtests`（UI 挂载） | 403 | **前缀剥离在网关外层**，改写后仍受检 |

最后一条是本系统唯一真实存在的绕过面：`StripApiPrefix` 是纯 ASGI 中间件，
直接改写 `scope["path"]` 再分发。若它被装配在网关**内层**，`/api/*` 就会跳过查表。
现在靠两件事保证：`mount_ui` 在 `create_app` 中晚于两个中间件调用（后加入者在外层），
以及 `test_api_prefix_is_gated_when_ui_is_mounted` 用独立 app 实例断言顺序。

**判定标准不是"返回非 200"**：请求可能被执行了、只是业务自己返回 4xx。
更硬的判据是 `test_no_successful_audit_card_for_blocked_attempts`——工件里不得出现
`status=ok` 的卡片。

## 6. 与 T1 的联动

- 网关把判定结果写入 ASGI scope（`deepquant.risk`），审计中间件读取后写进工件：
  `outcome.reason_code` + `outcome.risk{checkpoint,category,mode,would_reject,...}`；
- 审计索引新增 `status` / `reason_code` 两个字段，使「按原因码检索拒绝」走索引而非全表扫描；
- 装配顺序由 `test_audit_middleware_is_outer_than_risk_gate` 断言：审计必须在网关外层，
  否则被拒绝的请求不会留下任何工件，而"没有记录"与"没人操作过"无法区分。

**取证过程中发现并修掉一个真实缺陷**：`AuditStore` 的分片缓存原先只按文件名做键，
别的写入者追加后本实例仍返回旧快照，导致 `verify()` 报告"卡片不存在"，
而卡片好好地在磁盘上。已改为以「文件大小 + mtime」为版本键，并补了回归测试
（`test_cache_sees_cards_written_by_another_instance`）。这类假阴性会让校验被当成
"误报"而遭忽略，比没有校验更危险。

## 7. 已知限制

1. **`enforce` 模式要求所有调用方携带 `X-Operator`**。直接切换会让现有前端全部 403。
   上线路径必须是：`observe` 运行两周 → 按 `would_reject` 统计改造调用方 → `enforce`。
2. **`off` 是默认值**，即"未配置＝未启用"。生产部署必须显式设置 `DEEPQUANT_RISK_MODE`；
   本模块无法判断"你忘了配"，只能保证"配错了会拒绝服务而不是静默放行"。
3. **网关自身故障的响应不带构建指纹**（`X-DeepQuant-Build`）：`BuildHeaderMiddleware`
   在更内层，早退时不会经过它。影响仅限于启动器的旧实例识别，不影响业务。
4. **只覆盖 HTTP 接口**。脚本/CLI 直接调用引擎（如 `scripts/*.py`）不经过中间件——
   这类调用目前不在"核心操作"定义内，若将来纳入需另做入口校验。
5. **未做限流与鉴权**：本仓库无账号体系，允许名单是静态环境变量，不是真正的身份认证。
6. **前缀改写后工件记录的是改写后的路径**（`/backtests` 而非 `/api/backtests`），
   原始路径在 scope 改写时已丢失。

## 8. 复现

```bash
cd "<repo>"
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

# ① T2 全部测试（80 项：原因码 / 检查点 / 网关 / 接入 / 渗透）
cd backend && PYTHONPATH=src $PY -m pytest tests/risk -q

# ② 四条验收标准取证 → reports/RISK_T2_EVIDENCE.json
cd .. && PYTHONPATH=backend/src $PY scripts/risk_t2_evidence.py

# ③ 全量回归
cd backend && PYTHONPATH=src $PY -m pytest tests -q
```

## 9. 变更清单

新增：

- `backend/src/deepquant/risk/{__init__,reason_codes,checkpoints,gateway,middleware,bypass}.py`
- `backend/tests/risk/{test_risk_reason_codes,test_risk_checkpoints,test_risk_gateway,test_risk_gateway_api,test_risk_penetration}.py`
- `scripts/risk_t2_evidence.py`、`reports/RISK_T2_EVIDENCE.json`

修改：

- `backend/src/deepquant/api/app.py`：装配两个中间件（顺序有注释与测试保证）、注入查询能力；
- `backend/src/deepquant/api/audit/…`（T1）：scope 常量、工件写入风控原因码、索引新增两字段；
- `backend/tests/audit/test_audit_store.py`：补缓存失效与原因码检索两项回归。

未修改：任何既有业务语义、API 契约、数据库 schema、冻结配置。
