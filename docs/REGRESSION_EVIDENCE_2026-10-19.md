# 回归验证证据（A-P4，8 项）

> **任务 A-P4 / 交付物：测试证据**
> 执行时间：2026-10-19 01:50 ~ 02:35 GMT+8（系统时钟）
> 执行前提：本轮**不改 `src` 一行**、不覆盖 `gate1_out/`、不 merge / 不 push。
> 判定原则：**每一项都要有可复现的原始输出**；拿不到就写「未验证」，不写「应该没问题」。

---

## 0. 八项结果一览

| # | 验证项 | 结果 | 关键数字 |
|---|---|:--:|---|
| 1 | 单元测试（分模块聚焦） | ✅ | `research_fixes` 26 passed；`backtests_routes_contracts` 29 passed |
| 2 | 全量套件 | ✅ | **1780 passed / 1 skipped / 0 xfailed / 0 failed**（102.98s） |
| 3 | `npm run build` | ✅ | 679 modules，JS 670.30 kB / CSS 12.10 kB，6.30s |
| 4 | 后端启动 + `/health` | ✅ | `{"status":"ok"}`（127.0.0.1:8231） |
| 5 | 历史数据回测 | ✅ | 000858.SZ × ma_cross(10,60)，期末 **317,830.83**，累计 **+58.92%** |
| 6 | 数据指纹 | ✅ | 预登记 `8b011551…` 未变；`gate1_out` 聚合指纹未变 |
| 7 | Gate 1 原始输出未被覆盖 | ✅ | `tree_sha256 = 09cef004…26ad` **逐字符相同** |
| 8 | 失败 / 跳过 / xfail 全登记 | ✅ | 0 failed、1 skipped（已定位）、**0 xfailed** |

**总结论**：8 项全过，无回归。唯一需要人工留意的是第 4 项暴露的一个**使用姿势差异**（见 §4.2），
不是缺陷。

---

## 1. 单元测试（分模块聚焦）

全量套件之外，单独跑本轮改动最集中的两个目录，确认不是「被其他测试带绿」：

```
tests/research_fixes/                       26 passed
tests/api/test_backtests_routes_contracts.py 29 passed（28 passed + 1 已转正）
```

`tests/research_fixes/` 覆盖：`generate_windows` 上界 9 条、`price_jump` 计数 9 条、
D1 对照组输入隔离 4 条、D2 返回字段分离 4 条。

---

## 2. 全量套件

```bash
cd backend
PYTHONPATH=src "D:/DeepQuant power by txm/venv/Scripts/python.exe" \
  -m pytest tests/ -q --ignore=tests/research -rxX -p no:cacheprovider
```

```
1780 passed, 1 skipped, 1 warning in 102.98s (0:01:42)
```

### 与历史基线对账

| 时点 | passed | skipped | xfailed | failed |
|---|---:|---:|---:|---:|
| 2026-10-18 23:45（收官基线） | 1,718 | 1 | 7 | 0 |
| 2026-10-19 01:32（A-P0 快照） | 1,746 | 1 | 8 | 0 |
| **2026-10-19 02:35（本次）** | **1,780** | **1** | **0** | **0** |

`1,746 + 8（xfail 转正）+ 26（A-P1 新增）= 1,780` ✅ 逐项对得上。
**xfailed 从 8 归零不是删测试**，是 8 条缺陷真被修好后摘掉了 `strict` 标记（断言一条未改）。

---

## 3. 前端构建

```
npm run build   →  tsc --noEmit && vite build
✓ 679 modules transformed.
dist/index.html                   0.53 kB │ gzip:   0.35 kB
dist/assets/index-BVoqJchx.css   12.10 kB │ gzip:   3.09 kB
dist/assets/index-x4HfI-RM.js   670.30 kB │ gzip: 192.07 kB
✓ built in 6.30s
```

产物落盘时间 `2026-10-19 01:58`，与本次构建一致。
**唯一告警**：单 chunk > 500 kB（`670.30 kB`）—— 属既有状况，非本次引入，不阻断构建。

---

## 4. 后端启动 + `/health`

### 4.1 实测

```bash
DEEPQUANT_DATA_ROOT="D:\DeepQuant power by txm\DeepQuant-main\data" PYTHONPATH=src \
  python -m uvicorn deepquant.api.app:create_app --factory --host 127.0.0.1 --port 8231
```

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8231

GET /health  →  200  {"status":"ok"}
```

显式设 `DEEPQUANT_DATA_ROOT` 是必须的：默认指向 `backend/data`（不存在），
不设就看不到任何已登记数据集。

### 4.2 ⚠️ 一个容易被误判为「API 坏了」的差异

直接用 `uvicorn` 起 app 时，`/api/health`、`/api/datasets` 返回 **404**：

```
GET /api/health    →  404 {"detail":"Not Found"}
GET /api/datasets  →  404 {"detail":"Not Found"}
```

原因：**`/api` 前缀是启动器（`scripts/start_deepquant.py`）的 `StripApiPrefix` 中间件剥掉的**，
裸 app 的路由本来就不带前缀（`/health`、`/datasets`、`/backtests` …，见 `/openapi.json`）。

**这不是缺陷**，是两条启动路径的差异。但如果有人照 `START_HERE.md` 的 URL 去测裸 uvicorn，
会得到「接口全 404」的错误结论。**验证时要么用启动器、要么用无前缀路径。**

本次同时确认了 app 的完整路由表（9 条）：

```
/backtests  /backtests/compare  /backtests/{run_id}
/backtests/{run_id}/integrity  /backtests/{run_id}/reproducibility
/datasets  /health  /portfolio-backtests  /strategies
```

---

## 5. 历史数据回测（真实 HTTP，不走 TestClient）

```
POST /backtests
{
  "dataset_symbol": "000858.SZ",
  "strategy_id": "ma_cross",
  "strategy_params": {"fast_window": 10, "slow_window": 60},
  "initial_cash": 200000,
  "start_date": "2020-01-01",
  "end_date": "2024-12-31"
}
```

| 字段 | 值 |
|---|---|
| `run_id` | `434eea0d0b22408499e64ad4e64775d6` |
| `initial_equity` | 200,000.00 |
| `final_equity` | **317,830.83** |
| `total_pnl` | 117,830.83 |
| `cumulative_return` | **58.92%** |
| `annualized_return` | **10.11%** |
| `annualized_volatility` | 27.89% |
| `max_drawdown_ratio` | 43.51%（峰 2021-02-19 → 谷 2024-10-17） |
| `drawdown_observation_count` | 1,212（账户状态点） |
| `is_complete` | **true**（`incomplete_reasons` 为空） |
| `config.fee_source` | `simulation_placeholder`（模拟占位，非真实券商费率） |

**R4 自检**：响应里 `fee_source` 明确写 `simulation_placeholder`，没有把模拟费率说成真实费率。

---

## 6. 数据指纹

### 6.1 预登记：未改动 ✅

```
docs/GATE1_PREREGISTRATION.md
actual sha256 : 8b0115517a7ae35df20250c0fdf04736cfa99d9bfd570b89f0d93bc29ec88d63
与 docs/GATE1_PREREGISTRATION.sha256 第一行逐字符一致 → v1.3 冻结仍然有效
```

### 6.2 `gate1_out/`：未改动 ✅（见 §7）

### 6.3 `docs/`：**按预期变化**

| 时点 | 文件数 | 说明 |
|---|---:|---|
| A-P0 快照 | 47 | 基准 |
| **本次** | **51** | 新增 A 线 2 份（`PRICE_JUMP_IMPACT_AUDIT.md`、`DATA_FEASIBILITY_REPORT.md`）+ B 线并行产出 |

`docs/` 的聚合指纹因此**必然变化** —— 这是新增文档导致的，不是数据被改写。
**判断「有没有被覆盖」要看 `gate1_out/`，不能看 `docs/`。**

---

## 7. Gate 1 原始输出未被覆盖（第 7 项，最关键）

用 A-P0 快照里记录的聚合方式（按「相对路径 + 文件 sha256」排序串联再哈希）重算：

```
gate1_out/  12 files
tree_sha256 now      = 09cef0040bd11c5454145e327339bb64f592e95bb1418ce9a2b8ff47392e26ad
tree_sha256 baseline = 09cef0040bd11c5454145e327339bb64f592e95bb1418ce9a2b8ff47392e26ad
MATCH                = True
```

两个 run 的独立指纹也逐一复核：

| run | 基线 | 现在 | 一致 |
|---|---|---|---|
| `032a11f591553e25`（主实验） | `6ba955be…` | `6ba955bef57e7674` | ✅ |
| `a027865f26ea237a`（对照） | `670fa339…` | `670fa339638469a5` | ✅ |

**结论**：Gate 1 的 12 个原始产物**逐字节未变**。本轮所有操作（读 CSV 重算跳变、
跑回测、起后端）都没有写入 `gate1_out/`。

---

## 8. 失败 / 跳过 / xfail 全登记

### 8.1 守卫口径（`--ignore=tests/research`）

| 类别 | 数量 | 明细 |
|---|---:|---|
| passed | 1,780 | — |
| **failed** | **0** | 无 |
| **xfailed** | **0** | 8 条缺陷已修，标记已摘除（非删除测试） |
| **skipped** | **1** | `tests/data/test_akshare_live.py:22` —— 原因：`未设 DEEPQUANT_LIVE，跳过真实网络` |

唯一条 skip 已单独复跑确认（`-rs` 输出原文）：

```
SKIPPED [1] tests\data\test_akshare_live.py:22: 未设 DEEPQUANT_LIVE，跳过真实网络
```

这是**刻意的环境开关**，不是遗漏：它要打真实网络，默认关闭。

### 8.2 `tests/research/`（守卫排除，属在途工作）

```
159 passed, 1 warning in 20.86s
```

与 A-P0 时的 `158 passed / 1 failed` 相比，那条 flaky（`test_render_report_is_deterministic`，
挂钟秒级时间戳）**本次通过** —— 符合它的间歇性特征，不是被修好了。
**该目录不计入 A 线验收口径**，此处只登记状态。

### 8.3 唯一告警

`StarletteDeprecationWarning: Using 'httpx' with 'starlette.testclient' is deprecated; install 'httpx2' instead.`
—— 依赖提示，不影响任何断言。

---

## 9. 本轮测过但**没有**覆盖的

为免读者高估这份证据，明确列出：

| 项 | 状态 |
|---|---|
| 生产部署形态（多进程 / 反向代理 / HTTPS） | **未验证** |
| 前端交互（点击、图表渲染） | **未验证**：只验了 `tsc --noEmit` + `vite build` 通过 |
| 真实券商接口 / 实盘下单 | **从未接入**（产品本身也不提供） |
| 500 只以上股票池的端到端回测 | **未验证**：本次只跑单标的 |
| Gate 1 实验重跑 | **刻意不做**：会覆盖原始产物，违反保护条款 |
| `DEEPQUANT_LIVE=1` 的真实网络用例 | **未执行**：会引入外部不确定性 |

---

## 10. 复现命令汇总

```bash
PY="D:/DeepQuant power by txm/venv/Scripts/python.exe"

# 2. 全量套件
cd "D:/DeepQuant power by txm/DeepQuant-main/backend"
PYTHONPATH=src "$PY" -m pytest tests/ -q --ignore=tests/research -rxX -p no:cacheprovider

# 3. 前端构建
cd ../frontend && npm run build

# 4. 后端 + 健康检查（注意：裸 app 无 /api 前缀）
cd ../backend
DEEPQUANT_DATA_ROOT="D:\DeepQuant power by txm\DeepQuant-main\data" PYTHONPATH=src \
  "$PY" -m uvicorn deepquant.api.app:create_app --factory --host 127.0.0.1 --port 8231 &
curl -s http://127.0.0.1:8231/health          # {"status":"ok"}

# 5. 历史回测
curl -s -X POST http://127.0.0.1:8231/backtests -H "Content-Type: application/json" \
  -d '{"dataset_symbol":"000858.SZ","strategy_id":"ma_cross",
       "strategy_params":{"fast_window":10,"slow_window":60},
       "initial_cash":200000,"start_date":"2020-01-01","end_date":"2024-12-31"}'

# 7. gate1_out 指纹比对
"C:/Users/77436/.workbuddy/binaries/python/envs/dq_demo/Scripts/python.exe" probe_out/ap0_sha256.py
```

原始输出留档：`probe_out/ap4_full_run.txt`、`probe_out/ap4_npm_build.txt`、
`probe_out/ap4_backend.log`、`probe_out/ap4_backtest.json`、`probe_out/ap4_research.txt`。

---

## 11. 边界声明

1. **本文件只登记实测**。任何未跑的项目都在 §9 明写「未验证」，不写「应该正常」。
2. **`is_complete: true` 不等于「策略有效」**，只表示回测链路走完、没有吞掉中间态。
3. 回测收益率是**模拟费率**下的结果（`fee_source = simulation_placeholder`），不是可交易收益。
4. 本轮**未改 `src`**、未覆盖 `gate1_out/`、未 commit / push / PR。
