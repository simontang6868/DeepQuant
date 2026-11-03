# T4 能力契约化：设计与验收记录

> 计划依据：《Vibe-Trading 调研结论落地推进计划 v1.1》§5.2 T4（P1）
> 目标：业务动作从硬编码分支逻辑转为统一 schema 的技能注册表，**可枚举、可测试、可授权**。
> 范围：技能 schema 规范（输入/输出/前置条件/风控标签）、注册表与路由、版本管理。
> 落地日期：2026-10-27｜基线提交 `5cfed94`｜全量回归 **2,332 passed / 1 skipped**

---

## 0. 结论与验收实测

| 计划验收标准 | 实测证据 | 判定 |
|---|---|---|
| 核心业务动作 100% 完成 schema 化 | T1 核心操作 **6 项 → 能力 6 项**（ratio 1.0）；HTTP 5 + 服务层 1；未覆盖路由 **0** | **PASS** |
| 新技能上线必经契约校验 | 声明↔实现双向对账 **0 问题**；未登记路由实测 `403 capability_unregistered`；同版本改契约时 lock **拒绝落盘** | **PASS** |
| 任一技能可审计 | 拒绝原因码 `capability_disabled` 在 T1 工件中按等值检索命中 1 张，含 `capability` 块与操作人 | **PASS** |
| 任一技能可禁用 / 可授权 | 真实请求：关闭 → `403`（业务未执行）、未授权 → `403`、跨 major → `409`、观察期 → 业务执行 + `would-reject` 头 | **PASS** |

证据文件：`reports/CAPABILITIES_T4_EVIDENCE.json`（由 `scripts/capabilities_evidence.py` 生成，全部为真实请求实测）。

---

## 1. 边界：为什么不是第四套"清单"

落地前本仓库已有三份清单，各自回答不同问题；T4 补的是它们都不回答的那一层。

| 既有清单 | 回答的问题 | 管不到什么 |
|---|---|---|
| `api/registry.py` | 有哪些**策略插件**、参数类型/默认值 | 与 HTTP 无关，管不到接口 |
| `audit/operations.py` | 有哪些**核心操作**要留痕（覆盖率分母） | 只管记不记，不管能不能调 |
| `risk/checkpoints.py` | 一次请求**能不能做**（身份/限额/状态/字段） | 不管"该功能是否对外开放" |
| **`capabilities/`（本任务）** | **一个业务动作对外承诺什么、谁能调、能不能关** | 不做参数校验、不做字段风控 |

刻意**不合并**的理由：

1. 三份清单的失效方式不同。合并后一处改动会同时改变留痕覆盖率与风控分母，谁都不敢动。
2. 拒绝原因码必须分表。T2 的验收标准是「四类检查点覆盖率 100%」，分母是那四个类别；把能力码塞进同一张表，覆盖率报表就会混入不属于风控四类的条目，读报表的人很快不再信任那四个数字。
3. `api/registry.py` 的 `StrategySpec` 是**插件实例化契约**（factory + 参数默认值），能力层的是**对外数据契约**（请求/响应模型）。两者的"版本"含义都不同：前者跟着策略类版本走，后者是接口兼容性承诺。

---

## 2. 一个能力声明长什么样

`CapabilitySpec` 的字段按计划四项要求分组：

| 分组 | 字段 | 说明 |
|---|---|---|
| 可枚举 | `cap_id` / `title` / `summary` / `transport` / `method` / `path` | `transport` 取 `http` 或 `service`（服务层能力没有传输面，如实留空） |
| 可测试 | `input_model` / `output_model` / `required_fields` / `preconditions` | 模型是 pydantic 类对象，摘要直接由 `model_json_schema()` 算出 |
| 可授权/可禁用 | `risk_tags` / `version` / `deprecated` / `successor` | 标签参与准入判断，因此是受控词表 |
| 可审计 | `operation_id` | **必须**等于 T1 操作类型，这是与工件的接缝 |

### 2.1 副作用必须显式二选一

`risk_tags` 必须且只能包含 `mutating` / `read_only` 之一。不允许"不声明"，因为不声明时准入判断只能退回按 HTTP 方法猜，而 `POST` 既可能是真写入（回测落库），也可能只是用请求体传查询条件（结果比较 `POST /backtests/compare`）——按方法猜必然错一半。

反过来，声明 `mutating` 而方法是 `GET` 是硬错误：HTTP 语义、缓存、代理重试都会因此出错。

### 2.2 必填字段故意写两遍

`required_fields` 与风控的 `REQUIRED_FIELDS_BY_OPERATION` 内容重复，由对账保证一致（不一致即 `required_fields_drift`）。这是**校验和式的重复**：如果只在一边写，另一边改了没人知道；写两遍并强制对账，改动必须同时发生。

### 2.3 当前清单（6 项）

| cap_id | 传输 | 路由 | 版本 | 副作用 | 契约摘要（前 8 位） |
|---|---|---|---|---|---|
| `backtest.run` | http | `POST /backtests` | 1.0.0 | mutating | `c7d898e1` |
| `backtest.compare` | http | `POST /backtests/compare` | 1.0.0 | read_only | `a0f0d272` |
| `portfolio.backtest` | http | `POST /portfolio-backtests` | 1.0.0 | mutating | `767516fa` |
| `dataset.list` | http | `GET /datasets` | 1.0.0 | read_only | `06454a02` |
| `strategy.list` | http | `GET /strategies` | 1.0.0 | read_only | `4dbc8fa3` |
| `audit.replay` | service | —（`audit.operations.replay`） | 1.0.0 | read_only | `8ad369c6` |

范围与 T1 同分母：不把 `/health`、`GET /backtests` 这类只读查询端点纳入"核心"，那会把分母做大、让 100% 变容易——这正是覆盖率指标最常见的自欺方式。这些端点被显式列入 `EXEMPT_ROUTES` 并写明理由（"已知不需要"而不是"忘了"），且豁免清单本身也参与对账（`stale_exemption`）。

---

## 3. 对账：19 类失败项，两个方向都查

`contract.verify_app()` / `verify_all()` 做的是**双向对账**，不是"声明写好了就行"。

| 方向 | 失败类型 | 抓的是哪种事故 |
|---|---|---|
| 声明 → 实现 | `route_missing` | 声明了接口，应用里没有（或改了路径） |
| | `input_model_drift` / `output_model_drift` | 请求/响应模型换了，契约没跟着改 |
| | `operation_mapping_drift` | 能力与 T1 操作映射不一致（拒绝会记到别的能力名下） |
| | `operation_unregistered` / `operation_out_of_scope` | 服务层能力引用了未登记或不在范围的操作 |
| | `precondition_unimplemented` | 声明了 `field` 前置条件，风控却没有该操作的必填表 |
| | `required_fields_drift` | 必填字段两边不一致 |
| | `registry_invalid` | 注册表自身不自洽（重复 id/路由/操作） |
| 实现 → 声明 | `uncovered_route` | 新增会改变状态的路由没登记 → **T4 要拦的核心事故** |
| | `uncovered_operation` | T1 核心操作没有对应能力 |
| 豁免清单 | `exemption_without_reason` / `stale_exemption` | 豁免无理由；或路由改名后豁免残留成隐形洞 |
| lock | `lock_missing` / `lock_version_mismatch` / `lock_no_digest` / `contract_changed_without_version_bump` / `lock_stale` / `lock_unreadable` | 契约变了没升版本、版本升了没更新锁、锁里有已删能力等 |

只查一个方向会漏掉两种最典型的事故：**改了路由名**（声明指向空气）和**加了新接口**（新接口无人管）。前者靠方向 1，后者靠方向 2。

### 3.1 一个实现细节：路由怎么数才不错

新版 FastAPI（0.143）把 `include_router` 包成惰性的 `_IncludedRouter`，直接读 `app.routes` 只会看到 4 条框架路由。T2 已经踩过（检测器瞎了却报告通过），这里复用 `risk.bypass._iter_routes` 沿 `original_router` 递归展开，避免两处各修一遍。

---

## 4. 版本与 lock：让"改契约"必须是一次显式动作

`backend/contracts/capabilities.lock.json` 按 `(cap_id, version)` 记录契约摘要：

```json
"backtest.run": {
  "current": "1.0.0",
  "history": [
    { "version": "1.0.0", "digest": "c7d898e141c2e5dce3d7be4a04cf02c6",
      "schema_digest": "…", "recorded_at": "2026-10-27" }
  ]
}
```

规则与牙齿：

1. **同版本必须同摘要**。摘要变了而版本没变 → `scripts/capabilities.py lock` **拒绝写入**并指出该升哪个号（不兼容改 major、兼容新增改 minor、纯修复改 patch）。
2. **历史只追加不覆盖**，因此"这份契约什么时候变过"可查。
3. 摘要只覆盖**行为相关**字段（传输、方法、路径、操作、输入输出 schema、必填字段、前置条件、风控标签）；标题/说明/版本号不进摘要——改错别字不该逼人升版本，而**版本号本身是标签不是内容**，把它塞进摘要会导致"升版本就改摘要"，永远对不上。

实测（`criterion_2_contract_enforced.lock_teeth`）：

```
refused_same_version_change = True
message = "backtest.run@1.0.0 的契约摘要已变化（c7d898e1… → 33dafe75…），但版本号没变；
           请先升版本…"
accepts_after_version_bump = True
history_versions = ["1.0.0", "1.1.0"]
```

**这一步刻意不自动化**：工具不会替人决定该升 major 还是 minor。它只说"必须升"，决定权留给人。

---

## 5. 准入：三模式与配置

| 模式 | 行为 |
|---|---|
| `off`（默认） | 完全不介入：不加响应头、不读配置。既有行为零变化 |
| `observe` | 判定逻辑与 `enforce` **完全相同**，只是不拦，响应头给 `X-Capability-Would-Reject` |
| `enforce` | 拒绝 |

配置：

```bash
DEEPQUANT_CAPABILITY_MODE=observe            # off / observe / enforce
DEEPQUANT_CAPABILITY_DISABLED=backtest.run   # 逗号分隔，支持 backtest.* 前缀通配
DEEPQUANT_CAPABILITY_GRANTS="backtest.run=alice,bob;portfolio.backtest=*"
```

三条设计决定：

1. **观察期判定与强制期一致**，由测试钉住（`test_cap_policy.py`）。否则观察期采到的数字是假的，而观察期的全部价值就是那个数字。
2. **配置写错就拒绝，不静默降级**：cap_id 拼错、通配没匹配、授权名单为空、模式值拼错 → `503 capability_internal_error`。理由很直接：写错的后果是"以为禁用了其实没禁用"，而它和"禁用了"在日志里长得一样。
3. **未配置授权的能力默认对所有已识别操作人开放**。否则引入契约化的第一天就会把现有前端全部挡住——那不该是这次改造的副作用。

服务层能力（`audit.replay`）没有 HTTP 面，闸门装在唯一入口 `audit.operations.replay`（`policy.guard`），语义与 HTTP 面一致。

### 5.1 装配顺序

由内到外：`路由 → 风控 → 能力 → 审计`（`app.add_middleware` 后添加者在更外层）。

- 能力在风控**外层**：能力关闭/未授权比"请求本身不合法"更前置，被关掉的功能不该先跑一遍数据库查询再拒绝；
- 能力在审计**内层**：拒绝必须留痕。反过来则能力层的 403 不经过审计，工件里留下一片空白，而"没有记录"和"没人操作过"一模一样。

顺序由 `test_cap_middleware.py::test_middleware_order_audit_outermost` 断言，不靠注释保证。

---

## 6. 新增一个业务动作的正确顺序

1. 在 `api/routes/*.py` 写路由（含请求/响应 pydantic 模型）；
2. 在 `capabilities/registry.py` 的 `CAPABILITIES` 加声明（模型类、必填字段、前置条件、`mutating`/`read_only`、版本 1.0.0）；
3. 在 `audit/middleware.py` 的 `OPERATION_ROUTES` 加映射，并在 `audit/operations.py` 的 `CORE_OPERATIONS` 登记；
4. 在 `risk/checkpoints.py` 的 `REQUIRED_FIELDS_BY_OPERATION` 加必填字段（与第 2 步一致）；
5. `python scripts/capabilities.py lock` 记录初始契约；
6. `python scripts/capabilities.py verify` 必须 PASS。

漏掉任一步都会被对账抓出：漏 1 → `route_missing`；漏 2 → `uncovered_route`；漏 3 → `operation_mapping_drift`；漏 4 → `precondition_unimplemented`；漏 5 → `lock_missing`。

---

## 7. 落地清单

| 文件 | 行数 | 职责 |
|---|---:|---|
| `backend/src/deepquant/capabilities/spec.py` | 336 | 契约结构、词表、校验、摘要 |
| `capabilities/registry.py` | 248 | 6 项显式清单、查询、跨条目自检 |
| `capabilities/contract.py` | 587 | 双向对账 + lock 读写与版本约束 |
| `capabilities/policy.py` | 363 | 模式/禁用/授权/版本兼容、服务层 `guard` |
| `capabilities/codes.py` | 147 | 6 个能力原因码（独立表） |
| `capabilities/middleware.py` | 253 | HTTP 闸门、未登记路由补工件、响应头 |
| `capabilities/__init__.py` | 154 | 公开 API、`describe()`、`audit_surface()` |
| `backend/contracts/capabilities.lock.json` | 1,996 B | 契约锁（首次记录 2026-10-27） |
| `backend/tests/capabilities/*.py` | 5 文件 / 1,265 行 / 141 项 | 结构、注册表、策略、对账、中间件 |
| `scripts/capabilities.py` | 141 | `list / show / verify / lock / policy / codes` |
| `scripts/capabilities_evidence.py` | 290 | 生成验收取证 JSON |

改动既有文件的全部内容：

- `api/app.py`：+3 行装配（一个 import、一个 `add_middleware`、文档串改写）；
- `audit/middleware.py`：新增 `CAPABILITY_SCOPE_KEY` 常量与 `_capability_annotation()`，两处 dispatch 各 +5 行把能力结论写进工件；
- `audit/operations.py`：`replay()` 加一行 `guard("audit.replay", operator)`（延迟导入，避免循环）。

---

## 8. 限制与未决（如实列出）

1. **只覆盖 6 项核心操作**。`/health`、`GET /backtests`、详情/完整性/可复现性 4 个只读端点与 `/health` 在豁免清单里写明理由。若将来要"每个端点都能禁用"，需要先把它们纳入 T1 的核心操作分母——那是一次口径变更，不该顺手做。
2. **没有启动自检**。刻意不加：若在启动时跑全量对账并拒绝启动，"新路由忘了登记"会表现成整站 503，比现在的"只有那条路由 403"更难定位。因此契约校验放在测试里（CI 必跑）+ 运行期只对未登记路由 403，两道都有。
3. **lock 必须随代码发布**。缺 lock 时 `verify` 失败（"没锁＝没约束"），但运行期**不因缺 lock 拒绝业务**——那会把一次发布遗漏放大成全站不可用。这是一处刻意的宽严不一致，写在文档里而不是藏在代码里。
4. **不做多版本并存**。版本只用于判定"是否兼容"（同 major 放行、跨 major 拒绝），不提供 `/v1/` `/v2/` 并行路由，也不做字段级兼容映射。真要并行版本时是另一次设计。
5. **授权只有"按能力的操作人名单"**，没有角色、没有继承、没有租户隔离。`*` 表示所有人。够用于"谁能调哪个功能"，不够用于复杂组织。
6. **能力层不校验请求体内容**。那仍是 pydantic（结构）+ 风控字段检查（必填/空值）的职责；把内容校验搬进来会造成两套规则各自演化。
7. **`observe` 期的 `would-reject` 只进响应头与工件**，没有聚合报表。要统计"上线会拦多少"，需要按原因码查 T1 工件（`store.query(reason_code=...)`），暂无汇总脚本。

---

## 9. 复现

```bash
cd backend
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

# 能力层测试（141 项）
PYTHONPATH=src $PY -m pytest tests/capabilities -q

# 全量回归（2,332 passed / 1 skipped）
PYTHONPATH=src $PY -m pytest tests -q

cd ..
# 枚举 / 对账 / 落锁 / 策略
PYTHONPATH=backend/src $PY scripts/capabilities.py list
PYTHONPATH=backend/src $PY scripts/capabilities.py verify
PYTHONPATH=backend/src $PY scripts/capabilities.py policy

# 重新生成验收取证
PYTHONPATH=backend/src $PY scripts/capabilities_evidence.py
```

预期：`verify` 输出 `能力契约对账：PASS`（6 能力 / 14 路由 / lock 路径）。
