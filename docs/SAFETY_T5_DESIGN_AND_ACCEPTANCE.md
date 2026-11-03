# T5 安全边界编码：设计与验收记录

> 计划依据：《Vibe-Trading 调研结论落地推进计划 v1.1》§5.2 T5（P2，8–12 人日）
> 目标：安全策略从文档要求编码为产品逻辑，并保证任何异常退出后系统不残留风险敞口。
> 落地日期：2026-10-27｜基线提交 `5cfed94`｜全量回归 **2,451 passed / 1 skipped**

---

## 0. 一句话结论

四条验收标准全部达成，但**其中一条换了对象**：本仓库没有委托域，"撤销全部未成交
委托"没有对应物。替代动作是"排空在飞请求"，并把无对应业务面的步骤**显式登记为
`not_applicable`**（附理由），既不伪造实现，也不让它悄悄消失。

| 计划验收标准 | 实测证据 | 判定 |
|---|---|---|
| 非白名单回调 100% 拒绝（含内网穿透） | 26 条必须拦的用例 **26/26 拦下（100%）**，对照组 2 条全部放行，原因码 **0 处不符** | **PASS** |
| 权限默认最小化，提权需显式授予 | `minimal` 下：匿名 → `privilege_unidentified`；变更型无授予 → `privileged_operation_denied`；显式授予 → 放行；只读 → 放行 | **PASS** |
| 熔断开关演练一次通过 | 3 次失败 → `open`（Retry-After 59s）→ **重启后仍 open** → 半开放 1 个探测 → 探测成功 `closed`；HTTP `503` 且业务未执行；CLI 路径同样被拦 | **PASS** |
| 崩溃：超时窗内清空 + 日志完整 + 未确认清空拒绝重启 | 崩溃残留 → 启动被拒；清理成功 → 标记与旧会话清除；**排空超时 → 清理失败、标记保留、启动被拒**；日志链 3 条校验通过 | **PASS（对象已替换，见 §1.2）** |

证据文件：`reports/SAFETY_T5_EVIDENCE.json`（`scripts/safety_evidence.py` 生成，全部真实执行）。

---

## 1. 先说清落地边界（这一节比实现更重要）

### 1.1 落地前的真实状态

| 计划假设 | 本仓库实际 | 影响 |
|---|---|---|
| 有外部回调地址（webhook/notify） | **0 个**：无回调面、无 URL 配置 | 白名单校验没有"现成的洞"可修，只能先立边界 |
| 有出站 HTTP 调用点 | **0 个 URL 字面量**（行情经 akshare，URL 在其内部） | 同上；加一条静态绊线防止将来裸写 |
| 有认证体系 | **没有**：`X-Operator` 是调用方自称 | "权限"只能防误操作，防不了伪造身份 |
| 有异步 + 同步两条交易通道 | 只有 HTTP 请求与 CLI 脚本 | 熔断的"双路径"映射为 HTTP 中间件 + CLI 作用域 |
| 有委托/持仓 | **没有**：无券商连接、无订单状态机 | 撤单/平仓 → `not_applicable`，替代为排空在飞请求 |

### 1.2 换成"排空在飞请求"的依据

计划的四步是：停止策略 → 撤销全部未成交委托 → 验证清空 → 留存清理日志。
本仓库对应的四步：

1. **停止接受新任务**：置 `draining`，中间件对任何新请求返回 `503 cleanup_incomplete`；
2. **排空在飞请求**：`InFlight` 计数归零，超时窗内未归零即**清理失败**（计划的"超时窗内完成"是硬要求）；
3. **验证清空**：快照比对——在飞=0、依赖运行期缓存=0、安全目录无临时残留；
4. **留存日志**：append-only JSONL + 每行 sha256 链 + 前后快照。

**不能替换的那部分如实登记**（`NOT_APPLICABLE_STEPS`，永远出现在清理证据里）：

| 步骤 | 不适用的理由 |
|---|---|
| `cancel_open_orders` | 无委托域（无券商连接、无订单状态机），不存在未成交委托 |
| `liquidate_positions` | 不做真实持仓管理，回测持仓只在内存/结果文档中 |

---

## 2. 出站目标策略：为什么只做域名白名单不够

只做字符串白名单会被廉价绕过，而且**从日志上看不出来**：

| 写法 | 文本上像什么 | 实际连到哪 |
|---|---|---|
| `http://2130706433/` | 一个数字 | 127.0.0.1 |
| `http://0x7f.0.0.1/` | 怪域名 | 127.0.0.1 |
| `http://017700000001/` | 一个数字 | 127.0.0.1 |
| `http://127.1/` | 短域名 | 127.0.0.1 |
| `http://[::ffff:127.0.0.1]/` | IPv6 | 127.0.0.1 |
| `http://example.com@127.0.0.1/` | 看着像白名单域名 | 127.0.0.1 |
| `http://internal.example.com/`（解析到 10.0.0.5） | 完全合法 | 内网 |

因此判定分两层，缺一不可：

1. **名字层**：协议白名单（仅 http/https）、禁止 userinfo 与反斜杠、歧义数字写法归一成 IP、域名白名单（精确或 `*.suffix`，尾点归一）；
2. **地址层**：把 A/AAAA 记录**全部**分类，任一落在元数据/环回/链路本地/保留/私网即拒。

**必须检查全部记录**：只查第一条会被"前公网后私网"的解析绕过——取证里 `second_record_private` 用例正是这条。

**解析不出来就拒绝**（`outbound_dns_unresolvable`，503）：解析失败既可能是拼错，也可能是攻击者控制的 DNS 不可达，两种情况下放行都等于跳过检查。

### 2.1 静态绊线

`scan_url_literals()` 用 `ast` 扫源码里的**字符串常量**（跳过文档串），逐个过策略。为什么跳过文档串：本模块说明里就写满了反例 URL，按行正则会把说明当调用点，几次自报警之后大家就会把它加进忽略名单——那等于没有绊线。

已知盲区：`f"http://{host}/x"` 这类拼接串是 `JoinedStr`，扫不到。**它是绊线，不是完备检测**。

---

## 3. 最小权限：先承认它不是认证

当前形态下 `minimal` 能做到与不能做到：

| 能做到 | 不能做到 |
|---|---|
| 默认不允许任何人跑会写库的操作 | 阻止知道地址的人伪造已授权操作人 |
| 把"谁被允许做什么"变成可查询、可测试的配置 | 审计真实身份（工件里的 operator 仍是自称） |
| 为将来接真实身份预留唯一判定入口 | 抵抗 API 泄露后的越权 |

两种模式：`open`（默认，保持既有行为）/ `minimal`（变更型必须显式授予，且必须有身份）。**默认 open 的理由**与 T4 一致：引入安全机制第一天就挡住所有调用方，结局通常是有人关掉它然后忘开。切换前用 `scripts/safety.py permissions` 看清单。

判定表（`minimal`）：

| 操作人 | 能力类型 | 授权 | 结果 |
|---|---|---|---|
| anonymous | 任意 | — | `privilege_unidentified` |
| alice | 变更型 | 未配置 | `privileged_operation_denied` |
| alice | 变更型 | 含 alice | 放行 |
| bob | 变更型 | 只含 alice | `privileged_operation_denied` |
| alice | 只读 | 未配置 | 放行 |

**"未配置"不等于"所有人可用"**——否则忘配置就等于全开。

---

## 4. 熔断：为什么一定要落盘

内存熔断器有个致命漏洞：**重启即清零**。而"重启"恰恰是最容易被用来绕过保护的动作——跑不动了重启一下，熔断器就永远停在闭合态，像装了个只会亮灯的开关。

因此状态写 `breaker.json`（原子写：临时文件 + `os.replace`），进程重启后仍然有效。取证里 `survives_restart` 只从磁盘读，返回 `open`，这正是它的意义。

状态机：`closed` --(连续失败≥阈值)--> `open` --(冷却到)--> `half_open`（只放少量探测）--(成功)--> `closed` / --(失败)--> `open`。

**半开只放少量探测**是重点：冷却一过就全量放行，等于让刚挂掉的下游再被雪崩一次。

三条容易被"优化"掉的决定：

1. **4xx 不计入熔断**，只计 5xx。把用户手抖的错误参数计入，几个拼写错误就能把整个功能熔断掉，比不熔断更糟。
2. **冷却期内成功不闭合**。偶发成功可能只是被允许的探测之外的旁路结果，让它闭合会把真故障掩盖掉。
3. **状态文件损坏 → 拒绝**（`breaker_state_unreadable`）。读不出来当成"没有熔断记录"，等于清零。

两条路径：HTTP 中间件、`breaker_scope` 上下文管理器，共用同一份状态与阈值——否则会出现"接口熔断了但脚本还能跑"，而这种分裂只在事故现场才会被发现。

---

## 5. 退出清理：崩溃检测靠的是"会话标记"，不是"清理标记"

只写"清理开始才置标记"是**检测不到崩溃**的：崩溃发生时清理还没开始，标记也就没写，下次启动会以为一切正常。所以机制是两条：

| 文件 | 何时写 | 何时删 | 存在说明什么 |
|---|---|---|---|
| `session.json` | 启动（lifespan startup） | 正常退出（清理成功后）、或运维显式恢复 | **上次进程没走退出钩子**（崩溃/被杀/断电） |
| `cleanup_required.json` | 清理开始时 | 清理全部关键步骤通过后 | **上次清理失败**，资源状态未知 |

启动门 `assert_clean_start()`：两文件任一存在即拒绝启动，除非显式 `DEEPQUANT_ALLOW_DIRTY_START=1`——**显式放行也会写一条含理由的取证记录**，否则这个开关就是下一次事故的免罪符。

### 5.1 证据必须"前后对比"，而不是一句"已完成"

每条清理记录包含：`before` / `after` 两个快照（在飞数、依赖缓存条目、安全目录文件清单）、每步 `ok` 与 `detail`、`not_applicable` 步骤与理由、以及 `prev_sha256` 链。

日志链校验会抓三类篡改：改内容（摘要不符）、删中间（prev 不匹配）、**删开头**（首条 prev 非空）。第三条是第一版漏掉的——只校验"本条摘要对不对"发现不了删头。

### 5.2 一个语义细节

清理成功时，**只删除属于别的进程**的会话标记（按 pid 判断）：崩溃恢复能自动解除阻断，而运行中的进程手动跑一次清理不会删掉"当前这次运行"的崩溃证据。运维用 `scripts/safety.py cleanup` 显式恢复时传 `clear_session=True`——此时"上一个进程是死是活"已由人确认，工具不必再猜。

---

## 6. 装配

由内到外：`路由 → 风控(T2) → 能力(T4) → 安全(T5) → 审计(T1)`。

- 安全在能力外层：整个功能被熔断时不必再逐能力判定；
- 安全在审计内层：熔断拒绝也要落卡（可按 `reason_code=breaker_open` 检索）；
- lifespan：startup 走启动门 + 写会话标记；shutdown 走清理 + 按结果决定是否删会话标记。

顺序由 `tests/safety/test_safety_middleware.py::test_middleware_order` 与真实请求双重断言。

---

## 7. 落地清单

| 文件 | 行数 | 职责 |
|---|---:|---|
| `backend/src/deepquant/safety/outbound.py` | 588 | 白名单、地址分类、穿透防护、源码绊线 |
| `safety/breaker.py` | 442 | 熔断状态机、原子落盘、双路径 |
| `safety/lifecycle.py` | 660 | 在飞计数、四步清理、证据链、启动门 |
| `safety/least_privilege.py` | 211 | 最小权限判定与矩阵 |
| `safety/codes.py` | 190 | 16 个安全原因码（四类） |
| `safety/middleware.py` | 124 | 熔断闸 + 在飞计数 + 排空期拒绝 |
| `safety/__init__.py` | 148 | 公开 API 与 `describe()` |
| `backend/tests/safety/*.py` | 5 文件 / 1,113 行 / 119 项 | 穿透、熔断、清理、权限、装配 |
| `scripts/safety.py` | 164 | `status / check-url / scan / breaker / trip / reset / cleanup / permissions` |
| `scripts/safety_evidence.py` | 390 | 四条标准的取证脚本 |

改动既有文件：`api/app.py`（lifespan + 一个中间件 + 文档串）、`audit/middleware.py`（`SAFETY_SCOPE_KEY` + 一处注解 + 两条 dispatch 各 +4 行）、`.gitignore`（安全状态目录）、`tests/capabilities/test_cap_middleware.py`（顺序断言加一层）。

---

## 8. 限制与未决（如实列出）

1. **出站校验没有真实调用点**。本仓库当前 0 个出站 URL；这块是"先立边界"。验收证明的是**判据正确**（26 条穿透用例全拦），不是"线上拦过回调"。
2. **没有认证**，权限是防误操作。要成为真正的越权防护，需接入令牌/证书/OIDC，并替换 operator 来源（判定入口已收敛到 `least_privilege.check`，换来源不必改判定逻辑）。
3. **单进程假设**。`session.json` 单文件；多 worker 部署时每个进程会互相覆盖，需改为按 pid 分文件（uvicorn `--workers` 场景）。
4. **SIGKILL / 断电无法清理**——这正是会话标记存在的理由：下次启动会被拒绝，而不是假装干净。
5. **监控探活可能被排空期拒绝**。`/health` 也会被 `draining` 拦成 503；若负载均衡把 503 当"下线"信号，滚动重启会提前摘流。当前无 LB，属于部署时才需处理的细节。
6. **清理会清空依赖运行期缓存**（`deps.reset_runtime`）。这在本系统里是安全的（缓存可重建），但如果将来缓存承载不可重建的状态，这一步必须改。
7. **熔断是"按操作类型"的全局开关**，没有按调用方的差异化降级（例如只限制某个客户端）。
8. **静态绊线扫不到拼接 URL**，且只覆盖 `backend/src`（`scripts/` 未扫）。
9. **测试进程默认不读写安全状态**（`safety_state_enabled()`）：未设置 `DEEPQUANT_SAFETY_DIR` 且在 pytest 下时，中间件与 lifespan 整体旁路。理由见 §8.1——这既是隔离，也是一处**刻意的不一致**，必须知道。

### 8.1 为什么测试进程要旁路安全状态

第一版实现没有这条规则，结果全量回归在仓库默认目录 `backend/data/safety` 里留下了
30 条清理记录与一个 `breaker.json`。污染本身无害（已 gitignore），但它暴露了一个
真实的**跨套件干扰**风险：任何测试只要连续 3 次 5xx，就会在默认目录把熔断器打开，
随后不相关的测试全部被 `503` 拦掉，而失败原因看起来毫无关系。

规则因此定为"显式配置了安全目录就启用，否则 pytest 进程内不启用"。代价要说清：
**测试里不验证"默认路径下的生命周期行为"**。安全层自己的测试都显式设置
`DEEPQUANT_SAFETY_DIR`，所以真实行为仍有完整覆盖；未覆盖的只是"默认路径解析"这一行。

---

## 9. 复现

```bash
cd backend
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

# 安全层测试（119 项）
PYTHONPATH=src $PY -m pytest tests/safety -q

# 全量回归（2,451 passed / 1 skipped）
PYTHONPATH=src $PY -m pytest tests -q

cd ..
# 运维入口
PYTHONPATH=backend/src $PY scripts/safety.py status
PYTHONPATH=backend/src $PY scripts/safety.py check-url http://169.254.169.254/latest/meta-data/
PYTHONPATH=backend/src $PY scripts/safety.py scan
PYTHONPATH=backend/src $PY scripts/safety.py permissions

# 重新生成四条标准的取证
PYTHONPATH=backend/src $PY scripts/safety_evidence.py
```

**恢复流程**（服务因脏状态起不来时）：

```bash
PYTHONPATH=backend/src $PY scripts/safety.py status          # 看会话/标记/证据链
PYTHONPATH=backend/src $PY scripts/safety.py cleanup          # 执行清理并清掉旧会话
PYTHONPATH=backend/src $PY scripts/safety.py cleanup --verify # 校验日志链
```
