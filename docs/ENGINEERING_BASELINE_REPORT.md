# 工程环境基线固化报告（A-2）

> 生成时间：2026-10-18 15:35 GMT+8 ｜ 作者：A 线（纯加法 / pytest 自证）
> 归属任务：`NIGHT_PROGRESS.md` → 阶段 A–E 双线任务分配 → **A-2 环境基线固化**
> 本轮硬约束：**不 commit / 不 push / 不 merge / 不提 PR**，本报告仅落本地 `DeepQuant-main`。

---

## ⚠️ 前置：A1 已核实的关键事实（写报告前必须先说清）

| 项 | 事实 |
|---|---|
| 真 git 仓库 | `D:\DeepQuant`（branch `main`，HEAD `6e5e32d`，工作区 0 dirty）**不含 Gate 0 产物**（`backtest/portfolio.py` 不存在，`performance/` 缺 `benchmarks/curve/portfolio_metrics/window_distribution`）。 |
| 实际改代码处 | `D:\DeepQuant power by txm\DeepQuant-main` **无 `.git`**；Gate 0 产物齐全。历史经 GitHub API 直推远端，**从未同步回 `D:\DeepQuant`**。 |
| 结论 | **`D:\DeepQuant` 落后整个 Gate 0 工作量，不是同一份代码**。不得拿它做对比 / 合并。 |
| 网络 | `git ls-remote` 不可达；PR 状态一律用 GitHub API（token 走 `~/.workbuddy/mcp.json`）核，不依赖本地 git。 |
| 禁止 | 不在 `DeepQuant-main` 上 `git init`；不碰 `backtest/engine.py`、不改风险阈值、不调参（R2/R11）。 |

---

## 1. 环境构成（实测）

| 组件 | 版本 / 路径 | 备注 |
|---|---|---|
| Python | **3.13.14** | venv：`D:\DeepQuant power by txm\venv\Scripts\python.exe` |
| Node | **v22.22.2** | 前端构建与测试运行时 |
| 后端依赖 | `backend/requirements.txt` + `backend/requirements-dev.txt` | 两者均存在；dev 仅含 `pytest-cov` / `pip-audit` |
| 前端锁文件 | `frontend/package-lock.json` | 存在，`npm ci` 可复现安装 |
| 数据根 | `D:\DeepQuant power by txm\DeepQuant-main\data` | 须经 `DEEPQUANT_DATA_ROOT` 显式指定（默认指向不存在的 `backend/data`） |
| 数据集 | **5 只**（见 §3 ⑤） | `000001.SZ / 000858.SZ / 600519.SH / 600900.SH / 601318.SH`，0 个无效 |

---

## 2. 启动与测试命令（复现用）

```bash
# 后端（API，端口 8123）
cd D:/DeepQuant power by txm/DeepQuant-main/backend
set DEEPQUANT_DATA_ROOT=D:/DeepQuant power by txm/DeepQuant-main/data
set PYTHONPATH=src
D:/DeepQuant power by txm/venv/Scripts/python.exe -m uvicorn deepquant.api.app:app --port 8123

# 前端（开发服务器，端口 5173，/api 代理到 8123）
cd D:/DeepQuant power by txm/DeepQuant-main/frontend
npm install            # 或 npm ci（有 lockfile）
npm run dev            # vite dev
npm run build          # tsc --noEmit && vite build（生产构建）
npm test               # vitest run（157 passed，前轮已核）
npm run typecheck      # tsc --noEmit

# 后端测试（A 线每轮回归守卫）
cd D:/DeepQuant power by txm/DeepQuant-main/backend
set PYTHONPATH=src
D:/DeepQuant power by txm/venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/research
# 基线（2026-10-18 15:35）：1285 passed / 1 skipped / 6 xfailed / 1 warning / 0 failed
```

---

## 3. 七项可复现验证（A-2 核心交付）

> ①~⑥ 由协调者于 2026-10-18 15:07 实测通过；**⑦ 与前端 `npm run build` 由本 A 线接手于 15:35 实测补完**。
> ⑦ 的验证脚本落本地 `D:\DeepQuant power by txm\verify_a2_baseline.py`（仓库外，不进 PR），用 `fastapi.testclient.TestClient` 在进程内跑，免启独立服务器。

| # | 验证项 | 结果 | 证据 |
|---|---|---|---|
| ① | 后端可启动 | ✅ | `TestClient(app)` / `uvicorn deepquant.api.app:app` 正常导入并实例化；`GET /health` 可达 |
| ② | health 正常 | ✅ | `GET /health` → `200 {"status":"ok"}` |
| ③ | 前端可构建并启动 | ✅ | `npm run build`：`tsc --noEmit && vite build` 成功，672 模块转换、`dist/` 产出（5.16s，仅 chunk>500kB 提示，无害）；`npm run dev` 5473/5173 起服 |
| ④ | API 代理正常 | ✅ | `POST /backtests`（经 API 薄层：参数校验→数据集定位→引擎→序列化）返回 `200` 且 `result` 文档结构完整 |
| ⑤ | 数据集可被发现 | ✅ | `GET /datasets` → `200`，返回 **5 只**可用数据集、**0 个**无效；symbols：`000001.SZ, 000858.SZ, 600519.SH, 600900.SH, 601318.SH` |
| ⑥ | 真实历史数据完成回测 + 返回 reproducibility 指纹 | ✅ | `600519.SH` + `ma_cross(5,20)` + `initial_cash=100000`：`117` 笔订单 / `117` 笔成交，`performance` 字段齐全；`dataset.digest = sha256:1387b9e36b37a6c863d4f763b1f5d7124455ff9be299149e0f728d246e71983e`；`reproducibility = {inputs_identifiable:true, process_reproducible:true, missing:[], notes:[]}` |
| ⑦ | **结果可重复生成且保留可复现信息** | ✅ | 同请求（⑥ 完全相同）连跑 **两次**，`result` 文档**逐字节一致**（`full_result_byte_identical=true`）；两次 `dataset_digest` 完全相同、`reproducibility` 判定完全一致 → 输入可识别 + 过程可复现，复现信息被保留 |

### ⑦ 执行明细（可复现）

```bash
cd D:/DeepQuant power by txm/DeepQuant-main/backend
PYTHONPATH=src DEEPQUANT_DATA_ROOT="D:/DeepQuant power by txm/DeepQuant-main/data" \
  D:/DeepQuant power by txm/venv/Scripts/python.exe D:/DeepQuant power by txm/verify_a2_baseline.py
```

关键断言（脚本内 `checks`）：
- `①_backend_startable / ②_health_ok / ④_api_proxy_ok / ⑤_datasets_discoverable / ⑥_backtest_ran_with_repro_fingerprint / ⑦_repeat_run_consistent` 全部 `true` → `all_passed: true`。

---

## 4. 结论

- **环境基线已固化且可复现**：后端（Python 3.13.14 + FastAPI）、前端（Node v22.22.2 + Vite/TS）、数据根（5 只已登记数据集）三者均处于可启动、可构建、可回测状态。
- **可复现信息链路闭环**：数据集落盘即带 `sha256` 内容指纹 → 回测结果文档携带 `dataset.digest` 与 `reproducibility` 判定 → 相同输入两次运行产生逐字节相同输出。这是后续 A-3（W1 完整性测试）与 B 线（Gate 1 实验）可信性的地基。
- **待办依赖**：A-3（W1 结果完整性测试）须等 **B-1（W1 逐窗明细落盘）** 完成后才能写；B-1 属 B 线当前一轮任务。
- **本轮未做**：任何 git commit / push / merge / PR（遵守硬约束）；报告与验证脚本均留本地。

---
*附：A-1（仓库与 PR 核实）同步结论——经 GitHub API 核，当前开放 PR：A 线 #37~#42（A13/A15/A16#1~#4），B 线 #35(b18→main) / #36(b19→b18 叠序)。#29/#30/#31 已 closed。#35→#36 叠序仍成立。*
