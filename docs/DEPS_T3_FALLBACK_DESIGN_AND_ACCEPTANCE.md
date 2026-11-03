# T3 外部依赖降级链：设计与验收记录

> 对应《Vibe-Trading 调研结论落地推进计划 v1.1》§5.2 **T3（P1）**，含 v1.1 增补的
> 「延迟分布采集」。编制日期 2026-10-27｜基线 `5cfed94`｜依赖 T1/T2 已完成。

## 0. 结论速览

**四条硬性验收标准全部达成**（`scripts/deps_report.py evidence` → `reports/DEPS_T3_EVIDENCE.json`）

| 验收标准 | 实测 | 判定 |
|---|---|---|
| 核心外部依赖 100% 纳入注册表 | `market_data` 2 条通道（eastmoney/tencent）全部登记，含端点与优先级；`notification`/`downstream` **如实报告为空** | PASS |
| 模拟主源故障切换成功且业务无感知中断 | 主源抛 `ConnectionError` → 备用源返回 2 行数据，调用方拿到正常返回值 | PASS |
| 每次切换生成事件记录 | 事件序列 `['failover', 'recover']`，每条带 `src/dst/direction/reason/attempts`（含每次尝试耗时与错误） | PASS |
| 支持人工强制锁定某一通道 | `DEEPQUANT_DEP_LOCK=market_data=tencent` → 只调用 tencent，`pinned=True`，不产出切换事件 | PASS |
| 延迟分布可按日回溯、P50/P90/P99 可导出 | 两天档案，`export()` 输出 `{day: {channel: {n,n_failed,p50,p90,p99}}}`，方法 nearest-rank | PASS |

全量回归：**2,191 passed / 1 skipped**（T2 后为 2,132，本阶段新增 59 项）。

**真实网络探活（本机，2026-10-27 11:17）**：

```
[FAIL] market_data.eastmoney   2994.8ms  AkshareNetworkError: ... Unable to connect to proxy
[OK  ] market_data.tencent     1200.7ms
健康 1/2
```

东财被出口代理拦截是 2026-10-17 就记录过的真实故障；这条链路正是因此才有备用源。
本次探活再次确认：**没有降级链时，这条取数链路在本机直接不可用**。

## 1. 落地前现状（决定"改造"还是"新建"）

| 能力 | 落地前状态 | T3 的处理 |
|---|---|---|
| 多源切换 | **已有**：`akshare_loader` 手写「东财失败→腾讯」循环，`FetchConfig.used_source` 记录实际源 | **保留行为**，改为走注册表 + 降级链 |
| 依赖注册表 | 无（源顺序写死在 `_SOURCE_ORDER`） | 新建 |
| 健康探测 | 无 | 新建（按需，不在请求链路里） |
| 切换事件 | 无 | 新建（append-only JSONL） |
| 恢复回切 | 隐式（每次调用都从主源开始） | 保留隐式回切，**新增回切事件** |
| 人工锁定 | 部分（`source="eastmoney"` 钉死单源） | 提升为注册表级锁定（环境变量） |
| 延迟档案 | 无 | 新建（按日 × 按通道，P50/P90/P99） |

改造原则：**不改变既有失败语义**，只增加可观测性。既有 164 项数据层测试全部通过，
包括「东财失败必试腾讯」「显式指定源不 fallback」「字段变更不 fallback」这三条。

## 2. 架构

```
fetch_daily_a_share(...)
        │
        ▼
execute_chain(kind="market_data")
        │  1. registry.channels()     优先级顺序（priority 小的先试）
        │  2. lock / pinned           人工锁定 → 只试一条
        │  3. 逐条 call()
        │        ├─ 成功 → 记延迟样本 → 判定是否写切换事件 → 返回
        │        └─ 失败 → 记失败延迟 → is_retryable? → 换下一条 : 原样抛出
        │  4. 全部失败 → DependencyUnavailableError（带每次尝试明细）
        ▼
  deps/events.jsonl          切换事件（append-only）
  deps/latency/latency-YYYYMMDD.jsonl   延迟样本（按日分片）
```

**关键：切换对调用方不可见。** 调用方写 `df = fetch(...)`，不需要处理"主源挂了但备用源可用"。
只有全部通道失败才抛异常，且**绝不返回空 DataFrame 冒充"这段时间没数据"**。

## 3. 三个设计决定

**① 不做"一次失败就跳过主源"的熔断（刻意保留每次真实尝试）**
跳过会把偶发超时放大成持续降级；一旦健康状态陈旧（主源已恢复但计数还在），
跳过会掩盖恢复过程。当前策略是"每次都按优先级真实尝试"，用真实调用结果收敛。
`HealthTracker` 只记录连续失败次数供运维观察，**不参与跳过判断**——这一点在模块文档里写死，
避免以后有人"顺手"把它接进链路。

代价：主源持续不可用时，每次调用都要付一次失败的开销（本机实测约 3 秒）。
这是**已知且被接受**的取舍；若将来延迟档案显示主源长期不可用，再引入带冷却的熔断，
届时有真实数据支撑阈值，而不是凭感觉设。

**② 事件与 T1 工件分开记**
T1 工件是"接口请求"级（一次请求一张卡），而切换发生在一次调用内部，一次请求可能切换多次。
塞进请求级工件会被折叠成一条"这条请求里发生过切换"，细节全丢。两者用时间戳与通道名关联：
事件记原子事实，工件记请求级归档。

**③ 首次降级也要留痕**
若只在"上次成功通道 ≠ 本次通道"时才写事件，新启动的进程在完全降级状态下运行，
事件日志会是空的——事后完全看不出"那天数据其实来自备用源"。因此"本次调用首个通道就失败"
同样记为 `failover`。

## 4. 延迟档案：口径写死

- **nearest-rank**：排序后取第 `ceil(p/100*n)` 个（1-based）。不用插值——插值会给出
  **实际没观测到的值**，而"99% 的调用不超过 X 毫秒"里的 X 最好真的被观测到过。
- **副作用**：`n < 100` 时 p99 恒等于最大值。这是如实反映"样本不够"，不是缺陷。
- **失败样本计入**（默认）：一次 30 秒超时也是这条通道真实的延迟表现，剔除会让档案显得更可靠。
- **采样限流**：同一通道每 1 秒最多一个样本。代价是样本在时间上不均匀——
  若将来要做日内节律分析，应关掉限流（`min_interval_s=0`）并按时段专门采样。

## 5. 运维入口

```bash
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

PYTHONPATH=backend/src $PY scripts/deps_report.py health          # 探活（会发真实请求）
PYTHONPATH=backend/src $PY scripts/deps_report.py events --limit 20   # 最近切换
PYTHONPATH=backend/src $PY scripts/deps_report.py latency --day 20261027
PYTHONPATH=backend/src $PY scripts/deps_report.py export --out reports/DEPS_LATENCY.json
PYTHONPATH=backend/src $PY scripts/deps_report.py evidence        # 验收取证（不依赖网络）
```

`export` 的输出即为计划 §4.4 所说的「阶段二 T3 完成依赖注册后，以其采集的真实延迟数据
为输入，建立分通道的延迟分布档案」，也是第 6 章 L4 闸门「滑点与成交率偏差」判定基准的数据来源。

## 6. 与后续任务的接口

| 后续任务 | 关系 |
|---|---|
| **闸门 L4（限额模拟盘）** | 用 `export()` 的 P50/P90/P99 作为偏差阈值，把闸门标准从经验取值改为数据取值 |
| **T5 退出清理** | 同样可用 `EventLog` 记录清理动作的原子事实 |
| **T2 风控** | 二者互不依赖；`StateCheckpoint` 查的是本地状态，不查外部通道 |

## 7. 已知限制（必须如实列出）

1. **只有一类依赖真实存在**：`market_data`。`notification`（通讯通道）与 `downstream`（下游系统）
   在本仓库不存在，报告为 0 而不是塞占位通道。
2. **脚本层取数不经过注册表**：`scripts/` 下直接调 akshare 抓分红/股本的抓取器不在 API 进程内，
   没有走降级链。若将来这些脚本进生产，需要迁移到 `deps.chain`。
3. **探活无法真正中断**：`timeout_s` 用线程 `join` 实现，超时只是"不再等"，后台线程可能仍在跑。
   因此探活只适合轻量接口（拉一行行情），不适合重查询。
4. **默认状态目录在仓库内**（`backend/data/deps`，已加入 `.gitignore`）。运行测试会写入少量
   真实事件/样本——这是机制真的在跑的副产品，不是脏数据；部署时建议用
   `DEEPQUANT_DEPS_DIR` 指到独立目录。
5. **主源持续不可用时每次调用都付一次超时开销**（见 §3①）。这是刻意取舍，不是遗漏。
6. **未做延迟的日内节律分析**：限流使样本在时间上不均匀。

## 8. 复现

```bash
cd "<repo>"
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

cd backend && PYTHONPATH=src $PY -m pytest tests/deps -q          # 59 项
cd .. && PYTHONPATH=backend/src $PY scripts/deps_report.py evidence
cd backend && PYTHONPATH=src $PY -m pytest tests -q               # 全量回归
```

## 9. 变更清单

新增：

- `backend/src/deepquant/deps/{__init__,registry,chain,events,latency,health,market_data}.py`
- `backend/tests/deps/test_deps_{registry,chain,events,latency,loader_integration}.py`（59 项）
- `scripts/deps_report.py`、`reports/DEPS_T3_EVIDENCE.json`

修改：

- `backend/src/deepquant/data/loaders/akshare_loader.py`：手写循环 → `execute_chain`；
  新增 `probe_channel()`（刻意不复用 `fetch_daily_a_share`，否则探活测的是整条链而非单通道）
- `.gitignore`：新增 `/backend/data/deps/`

未修改：任何既有失败语义、异常类型、`FetchConfig` 字段与 metadata 结构、API 契约。
