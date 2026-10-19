# DeepQuant 程序审查交接包

> **本文件的用途**：交给另一个 AI Agent，用于独立判断「这个程序能不能跑」「是否满足委托人的需要」。
> **生成时间**：2026-10-19 22:55 GMT+8
> **生成方式**：所有 §5 的实测量数字均由本机在当前时间实际执行得到，不是从文档转抄。
> **阅读顺序建议**：§0 结论 → §1 程序在哪 → §4 自检清单（照着跑）→ §6 能力边界 → §7 陷阱 → §9 判定标准。

> **更正记录（2026-10-19 23:55，依据独立沙箱复核 `SANDBOX_VERIFICATION_REPORT.md`）**
> 本文件有三处口径已改，改动都指向"数字不可跨环境照抄"：
> 1. §4 #1 的测试期望值改为**分环境**（Windows 1997/1/0 · Linux 1994/4/0 · 无 akshare 1989/9/0）；
> 2. §3.2 的"必须显式设 `DEEPQUANT_DATA_ROOT`"改为**两种起法分开讲**（经启动器会自动发现）；
> 3. §5.1 补上 `tabulate`（原先只靠 akshare 传递依赖，已改为显式声明）。
> 另外登记一条独立复核**判断有误**的地方：它称"akshare 依赖链里没有 tabulate"，
> 实测 `akshare` 声明 `tabulate>=0.8.6`、`pip show tabulate` 显示 `Required-by: akshare`；
> 它之所以看到红 2 条，是因为装的是 7 包清单（不含 akshare），不是 `-r requirements.txt`。

> **第二轮更正（2026-10-20 00:40，依据 `CONSOLIDATED_REVIEW_ADJUDICATION_2026-10-19.md`）**
> 裁决确认 D1 归因在我这边；同时指出三处我做得不够的地方，全部采纳：
> 1. §3.2 与 §4 #1 的命令还是 Windows 专属路径 → 改为 `<你的 venv>/python`，平台写法降为注释；
> 2. §4 #6/#7 指向"见 §5.4/§5.5"（那是说明段）→ 改为直接指向可跑的 `sandbox_verify/p0_*.py`；
> 3. §0 结论缺环境限定 → 已加"Windows + 3.13.14 + 本机产物"限定块。
> 另新增三条只读自检脚本（`verify_package.py` / `check_deps.py` / `check_env.py`）与包身份
> `PACKAGE_INFO.json`。**裁决所依据的包是上一版（`6faded1f…`），不是当前包**：当前包已含
> `DEEPQUANT_REAL_600809` 与 `tabulate==0.10.0`，详见 `sandbox_verify/README_FOR_SANDBOX.md` §5.6.1。

---

## 0. 一句话结论

**程序能跑，而且跑得相当完整；但「能跑」和「能满足需要」是两件事——它满足「量化研究工具」这个需要，不满足「稳定盈利」和「实盘交易」这两个需要。**

| 问题 | 答案 | 依据 |
|---|---|---|
| 程序能不能启动 | **能** | 22:45 实测：单端口启动成功，`/health` 返回 `{"status":"ok"}`，界面 200 |
| 回测能不能出结果 | **能** | 贵州茅台 `ma_cross(5,20)`：期末 1,413,448.06，+41.34%，109 笔成交 |
| 组合回测能不能跑 | **能** | 5 只池、月度动量 top 40%：累计 −42.72%，120 笔成交，含被阻挡订单分类 |
| 测试是否健康 | **健康** | **1997 passed / 1 skipped / 0 failed**，142.65 秒 |
| 结果可不可复现 | **可** | 同参数两次回测 `final_equity` 与成交笔数完全一致 |
| 策略是否有效 | **未显示优势** | Gate 1 机械判定 `failure`；样本外检验 `R4_INCONCLUSIVE` |
| 能不能实盘 | **不能** | 无券商接口、无实时行情、费率为占位参数；且第 ③ 层未通过，第 ④ 层从未检验 |

> **以上结论的环境限定（必须连带引用）**：Windows 11 + Python **3.13.14** + 本机 `..\venv`
> （含 `akshare` 与 `b50_v3`/`b50_dist` 产物）。换到 Linux / 无 akshare / 无 b50 产物时，
> 测试通过数不同（见 §4 #1 分环境表），`test_cross_section` 与 `test_history_audit` 会转为 skip。
> **本文件里的数字是"这台机器 + 这份包"的实测值，不是跨环境常量。**

> **包身份**：本文件随交付包分发。包根目录有 `PACKAGE_INFO.json`（`package_id` = `MANIFEST.sha256`
> 的 sha256）。复核前先跑 `python sandbox_verify/verify_package.py`，并在报告里引用 `package_id`
> 前 16 位 —— 交付包被重建过多次，文件名始终没变，没有 ID 就无法判断"审的是哪一版"。

---

## 1. 程序在哪（四个位置，先分清再动手）

| # | 位置 | 是什么 | 能不能直接用 |
|---|---|---|---|
| ① | `D:\DeepQuant power by txm\DeepQuant-main` | **当前唯一可运行的工作副本**。有完整代码、数据、`frontend/dist`、venv（在上一级 `..\venv`） | ✅ 可运行。⚠️ **它不是 git 仓库**（`git rev-parse` 报 `not a git repository`），不能在这里 `git log` / `git status` |
| ② | `https://github.com/simontang6868/DeepQuant`（main） | 权威代码源。含 7 条装置修复、前端 Gate 0、1976+ 测试、`data/step75_ext/` 冻结数据 | ✅ 克隆即可。**但不含行情库 `data/` 其余部分**，需单独拷 |
| ③ | `D:\DeepQuant` | 老仓库，有 `.git`，但 HEAD 停在 **2026-10-16**，落后整个 Gate 0 | ❌ **不能当代码来源**，只作历史对照 |
| ④ | `data/`（在位置①下） | 行情与元数据：`data/metadata`（51 个 meta）、`data/processed`（51 个 parquet）、`data/step75_ext`（延伸至 2026-10-16 的 7 个 qfq） | ✅ 已在位。换机必须单独拷，否则回测全废 |

**两个必读交接文档**（都不是本报告替代品）：
- `HANDOFF_2026-10-19.md` —— 换机与 PR 合并记录、7 条修复证据、已知坑
- `START_HERE.md` —— 使用者视角的上手与排障

---

## 2. 这是什么程序

**一句话**：本地量化研究工具。导入 A 股日线 → 跑单标的或组合策略回测 → 看净值/回撤/成交 → 留历史记录 → 校验可复现 → 导出数据。

| 维度 | 规模（本机实测） |
|---|---|
| 后端 | 97 个 `.py`，21,398 行；FastAPI + pandas |
| 测试 | 123 个文件，28,539 行，**1997 条用例** |
| 前端 | 61 个源文件；React + TypeScript + Vite，`dist` 已构建 |
| 数据集 | **51 只**（48 只研究池 + 3 只手挑），2017-01-03 → 2024-12-31 |
| 延伸数据 | `data/step75_ext`：7 只，到 **2026-10-16**（样本外检验用） |
| API | 9 个端点（见 §3.3） |

**分层结构**：`data/`（加载与校验）→ `backtest/`（引擎、撮合、组合）→ `performance/`（绩效、基准、曲线）→ `api/`（FastAPI 路由）→ `frontend/`（React 界面）。

---

## 3. 怎么跑（三种方式，任选）

### 3.1 一键（推荐，给使用者）

```bat
双击 start_deepquant.bat
```
或
```bash
bash start_dev.sh          # 前后端分离（5173 + 8123）
bash start_dev.sh status   # 查看状态
bash start_dev.sh stop
```

### 3.2 直接起单端口（给审查 Agent）

```bash
cd <仓库根>          # Windows 例：D:/DeepQuant power by txm/DeepQuant-main
"<你的 venv>/python" scripts/start_deepquant.py --no-build --no-browser --port 8123
#   Windows 的 venv 解释器：..\venv\Scripts\python.exe
#   Linux/macOS 的 venv  ：../venv/bin/python
#   没有 venv 就是系统 python3（确保已 pip install -r backend/requirements.txt）
```
期望输出：
```
  DeepQuant 已启动
  界面      http://127.0.0.1:8123
  API 文档  http://127.0.0.1:8123/docs
  健康检查  http://127.0.0.1:8123/health
  数据集    ...\data
```
⚠️ **数据根目录的两种起法不同**：经 `scripts/start_deepquant.py` 启动时会**自动发现**仓库 `data/`
（`resolve_data_root`），不设也能得到 51 个数据集；只有绕开启动器、直接用 `uvicorn deepquant.api.app:app`
起服务时，才必须显式设 `DEEPQUANT_DATA_ROOT`，否则默认指向不存在的 `backend/data`、界面 0 个数据集。
⚠️ 后台起服务**不要用 `| head`**——输出满行后管道关闭，uvicorn/vite 会收 SIGPIPE 直接死。用 `> log 2>&1`。

### 3.3 API 端点（实测存在）

```
GET  /health                              GET  /datasets
GET  /strategies                          POST /backtests          GET /backtests
GET  /backtests/{run_id}                  GET  /backtests/{run_id}/integrity
GET  /backtests/{run_id}/reproducibility  POST /backtests/compare
POST /portfolio-backtests
```

---

## 4. 自检清单（复制即跑，含我实测的期望值）

按顺序跑，任何一条对不上再往下查 §7。

**开跑前先做三条只读自检（30 秒，不装包、不改文件、不起服务）**：

```bash
python sandbox_verify/verify_package.py          # 包完整吗？package_id 是多少？
python sandbox_verify/check_deps.py              # 依赖齐吗？版本与 pin 一致吗？
python sandbox_verify/check_env.py --port 8123   # 数据根命中哪条规则？51 条在不在？
```

第 1 条若 FAIL，**先重新取包**——用不完整的包跑出来的数字没有意义（上一轮就审错过版本）。

| # | 命令 | 期望 |
|---|---|---|
| 1 | `cd backend && PYTHONPATH=src "<你的 venv>/python" -m pytest tests/ -q`<br>（Windows：`..\venv\Scripts\python.exe`；Linux：`../venv/bin/python`） | **分环境**（收集数恒 1998）：<br>· Windows + 全量依赖 + 本机有 b50 产物 → `1997 passed / 1 skipped / 0 failed`<br>· Linux + 全量依赖 → `1994 / 4 / 0`<br>· Linux + 不装 akshare → `1989 / 9 / 0` |
| 2 | 起服务后 `curl http://127.0.0.1:8123/health` | `{"status":"ok"}` |
| 3 | `curl http://127.0.0.1:8123/api/datasets` | 数组，**51** 条 |
| 4 | POST `/api/backtests`（参数见下） | `final_equity = 1413448.06`，`fills` = **109** |
| 5 | POST `/api/portfolio-backtests`（参数见下） | `cumulative_return = -0.4272`，`fills` = **120** |
| 6 | `PYTHONPATH=backend/src "<venv python>" sandbox_verify/p0_1_windows.py` | `end_date=2022-06-30` → **19** 窗口、越界 **0**；`None` → **30** 窗口（另含 9/44、15/26、18/28 三组） |
| 7 | `PYTHONPATH=backend/src "<venv python>" sandbox_verify/p0_2_price_jump.py` | 平盘 100 行 → **无 issue**（修复前是 99）；`count=1/3` 准确；传 DataFrame 抛 `TypeError` |
| 8 | 同参数连跑两次 #4 | `final_equity` 与成交笔数**完全一致** |

**#4 请求体**
```json
{"dataset_symbol":"600519.SH","strategy_id":"ma_cross",
 "strategy_params":{"fast_window":5,"slow_window":20},
 "initial_cash":1000000,"start_date":"2018-01-01","end_date":"2024-12-31",
 "signal_lag":1,"save":true}
```

**#5 请求体**（注意字段名是 `dataset_symbols` / `top_fraction`，不是 `symbols` / `top_pct`）
```json
{"dataset_symbols":["600519.SH","000858.SZ","000001.SZ","601398.SH","601318.SH"],
 "initial_cash":1000000,"selection":"momentum_top","top_fraction":0.4,
 "lookback":60,"skip":5,"min_assets":2,
 "start_date":"2021-01-01","end_date":"2024-12-31"}
```

---

## 5. 本机实测记录（2026-10-19 22:44–22:55 GMT+8）

### 5.1 环境

| 项 | 值 |
|---|---|
| Python | 3.13.14（`D:\DeepQuant power by txm\venv`） |
| 关键包 | pandas 3.0.6；tabulate 0.10.0（**已显式声明**，此前只作为 akshare 的传递依赖） |
| 数据 | `data/metadata` 51、`data/processed` 51、`data/step75` 6 个 CSV |
| 前端产物 | `frontend/dist` 已构建（`index.html` + `assets`） |
| 服务状态（审查开始时） | **三个端口全部 502，无任何进程在跑** → 由我本轮拉起 |

### 5.2 启动与界面

```
[启动日志]
  DeepQuant 已启动
  界面      http://127.0.0.1:8123
  数据集    D:\DeepQuant power by txm\DeepQuant-main\data
  历史记录  ...\backend\data\backtests.db
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8123
GET /health 200 / GET / 200 / GET /assets/index-*.js 200 / GET /api/datasets 200
```
- `/health` → `{"status":"ok"}`
- 首页标题 → `<title>DeepQuant 回测工作台</title>`
- 启动器自动打开了浏览器（日志里有真实静态资源请求）

### 5.3 单标的回测（端到端）

| 项 | 值 |
|---|---|
| 标的 / 策略 | `600519.SH` / `ma_cross(5,20)` |
| 区间 / 本金 | 2018-01-01 → 2024-12-31 / 1,000,000 |
| 期末权益 | **1,413,448.06** |
| 累计 / 年化 | **+41.34%** / +5.27% |
| 成交 / 委托 | **109** / 110 |
| 可复现 | `inputs_identifiable` + `process_reproducible` 均存在 |
| `run_id` | `d70e7a54883043f68b5bb593ed63b33a`（`save=true` 时返回） |

> 同一参数第二次请求：`final_equity` 与成交笔数**逐位相同**。`save=false` 时 `run_id` 为 `null`（设计如此，不是缺陷）。

### 5.4 P0-1 修复验证（`end_date` 不再被忽略）

```python
# cd backend && PYTHONPATH=src ../venv/Scripts/python.exe -c "..."
from deepquant.walkforward import generate_windows, WalkForwardConfig
```
| 输入 | 窗口数 | 越界窗口 | 末窗 oos_end |
|---|---|---|---|
| `end_date=2022-06-30` | **19** | **0** | 2022-05-02 |
| `end_date=None` | **30** | 0 | 2024-11-11 |

> 修复前：两者都返回同样窗口、末窗 `oos_end=2024-11-12`，`end_date` 完全无效。

### 5.5 P0-2 修复验证（`price_jump` 计数）

| 输入 | 结果 |
|---|---|
| 平盘 100 行 | **无 `price_jump` issue**（修复前：`count=99`） |
| 传 Series、单跳变 | `count=1` ✅ |
| 传 Series、多跳变 | `count=36` ✅（构造值实算一致） |

### 5.6 组合回测

| 项 | 值 |
|---|---|
| 池 / 口径 | 5 只 / `momentum_top`、`top_fraction=0.4`、lookback 60、skip 5 |
| 区间 / 本金 | 2021-01-01 → 2024-12-31 / 1,000,000 |
| 累计 | **−42.72%** |
| 成交 | **120** 笔 |
| 附带输出 | `blocked`（被阻挡订单）、`targets`（目标权重）、`states`（账户逐日）、`limitations`（口径声明）、`data_quality_issues` |

> 该结果**不是策略有效性证据**：池只有 5 只、区间只 4 年，且 Gate 1 已用 50 只池做过正式检验并判 `failure`。

### 5.7 无法独立核验的项（如实标注）

| 项 | 状态 |
|---|---|
| GitHub main 的 CI 三 job 全绿 | **引自 `HANDOFF_2026-10-19.md`，本轮未独立核验**（GitHub 连接器当前状态 unavailable） |
| 前端 `typecheck + vitest + build` | 同上（`dist` 产物存在且能正常加载，但未重跑构建） |
| 2026-10-16 之后的行情 | 只有 `data/step75_ext` 的 7 只，**不是全池** |

---

## 6. 能力边界：能做什么 / 不能做什么

### 6.1 已实现且实测可用

| 能力 | 说明 |
|---|---|
| 单标的日线回测 | 支持起止日期、初始资金、`signal_lag`、参数校验 |
| 组合回测 | **多只共享同一笔现金**（卖 A 才能买 B），月度调仓、截面动量或全池等权 |
| A 股交易约束 | T+1、涨跌停、100 股整手、先卖后买资金释放、停牌近似 |
| 成本模型 | 佣金 / 印花税 / 过户费 / 滑点（**占位参数**，见 §7.3） |
| 绩效 | 累计/年化/波动/最大回撤/Sharpe/换手/成本占比 |
| 被阻挡订单归因 | 涨停买不进、跌停卖不出、资金不足、不足一手，**逐条列出** |
| 历史记录 | 单标的回测入库，可回看、可两两比较 |
| 可复现校验 | 输入可识别 + 过程可复现，含数据指纹 |
| 导出 | CSV（数据表）/ PNG（曲线 2×）/ JSON（**含口径声明**） |
| 研究装置 | walk-forward、IS 选参、敏感性、过拟合检查、基准（B&H / 同池等权） |

### 6.2 明确没有的（不是缺陷，是范围外）

| 没有 | 影响 |
|---|---|
| 券商接口 / 下单 / 实时行情 | **完全不能实盘** |
| 真实费率 | 佣金万 3 是占位值，滑点单边 0.1% 是研究假设；响应里 `fee_source=simulation_placeholder` |
| 做空 / 融资融券 / 期货 | 只能做多现货 |
| 分钟级 / tick | 只有日线 |
| 港股 / 美股 / 可转债 | 只有 A 股 |
| 历史时点成分股（PIT） | 51 只池是 **2026-10 快照**，幸存者偏差不可消除 |
| 指数数据 | 本机无，基准只能用「同池等权」，回答不了「相对沪深 300」 |
| 行业中性 / 风格归因 | 无 industry 字段（50/50 全空） |
| 登录 / 权限 | 无；默认只监听本机，`--host 0.0.0.0` 会给出警告 |

### 6.3 三个「需要」分别能不能满足

| 委托人的需要（按我的理解排序） | 满足度 | 理由 |
|---|---|---|
| ① 一个能跑 A 股回测的研究工具 | **满足** | 端到端跑通，1997 测试，可导出可复现 |
| ② 找到能赚钱的交易策略 | **未满足** | 四个方向全无正面证据：双均线（288 组全负）、均值回归（噪声区间）、股债利差（构造方向错）、横截面动量（`failure`）；样本外 `R4_INCONCLUSIVE` |
| ③ 实盘交易 | **不满足** | 无通道、无真实成本、第 ④ 层从未检验 |

---

## 7. 陷阱清单（对审查 Agent 最重要的一节）

### 7.1 会让人**误判成「程序坏了」**的现象

| 现象 | 真实原因 | 处理 |
|---|---|---|
| 15 个 collection error，`No module named 'deepquant'` | 漏了 `PYTHONPATH=src` | 加上即可，**不是代码坏** |
| 数据集下拉框 0 个 | **直连 uvicorn** 且没设 `DEEPQUANT_DATA_ROOT`（默认 `backend/data`，不存在）；或 `data/` 不在仓库根 | 走启动器会自动发现；直连则显式设 `DEEPQUANT_DATA_ROOT`。先跑 `check_env.py` 看命中哪条规则 |
| 回测出来一条横线 | 本金不够买 1 手（10 万买不起茅台） | 调大本金或换低价标的；界面有黄色拒单提示 |
| 池里没有 `600809.SH` | 前复权后有 131 行负价，被数据闸门拒绝 | **不是漏导入**，池下方会列拒绝原因 |
| 某个测试 `skip` | `test_history_audit` 依赖本机私有产物 `b50_dist/` | 是前置声明，不是失败 |
| 服务跑一会儿自己死 | 启动时用了 `\| head`，SIGPIPE | 用日志重定向 |
| `git log` 报 not a repository | 工作副本**没有 `.git`** | 版本信息去 GitHub 看，不要 `git init` |

### 7.2 已修复的 7 条装置缺陷（有验收证据）

| # | 缺陷 | 修复前 → 修复后 |
|---|---|---|
| P0-1 | `generate_windows` 忽略 `end_date` | 44 窗/35 越界 → **9 窗/0 越界**（本轮独立复测 19 vs 30、0 越界） |
| P0-2 | `price_jump` 计数恒为行数−1 | 平盘 100 行报 **99** → **0**（本轮独立复测通过） |
| P1-1 | OOS 构造失败崩 `UnboundLocalError` | → 返回 `oos_success=False` |
| P1-2 | `_num()` 用 `v is not bool` | → `not isinstance(v, bool)`（代码已确认） |
| D1 | 对照组退化（两 CSV sha256 相同） | 对照改回全池独立重跑 + 池指纹守卫 |
| D2 | 累计收益全程口径冒充 OOS | 拆 `*_eval` / `*_full` |
| #9 | `make_run_id` 只哈希形状 | → `_frame_digest` 逐值哈希 |

### 7.3 仍然存在、且**不可用代码解决**的限制

| # | 限制 | 后果 |
|---|---|---|
| L1 | 费率是占位参数 | **不得**称「按真实成本回测」 |
| L2 | 涨跌停撮合层是 flat 10%（无双创 20%） | 2020-08-24 后对双创**偏严（保守）**；研究脚本层有 board 模式，撮合层没有 |
| L3 | 停牌无标记 | 以「当日无行情行」近似 |
| L4 | 腾讯 `qfq` 是**加性**复权，会产生负价 | **不得用原始 qfq 做跨年份对比**，需先转 `qfq_mult` |
| L5 | 无 PIT 成分股 | 幸存者偏差**单向**存在，绝对收益系统性高估；已测出中位差 −1.67pp |
| L6 | 行情止于 2024-12-31（延伸仅 7 只到 2026-10-16） | 回答不了近期市场 |
| L7 | 工作副本无版本控制 | 代码来源以 GitHub main 为准 |

### 7.4 引用结论时的硬规则

- Gate 1 判定 `failure`，正确表述是「**未显示符合预登记门限的统计优势**」；
  **禁止**表述为「已证明显著跑输」「策略有效」「可以投入使用」。
- 样本外检验判定 `R4_INCONCLUSIVE`（既未证实也未否证），且**该批数据盲测资格已用完，不得二次使用**。
- `gate1_out/**` 两个原始 run 目录、`docs/GATE1_PREREGISTRATION.md`（sha256 前缀 `8b011551`）、
  `docs/OOS_RESULTS_2026-10-19.json` 均**只读**，不得覆盖或重生成。

---

## 8. 给审查 Agent 的具体审查题目（附我的答案，供你核对而非重做）

| # | 审查题 | 我的答案 | 你怎么验 |
|---|---|---|---|
| Q1 | 能不能启动？ | 能 | §4 #2，看 `/health` |
| Q2 | 主流程能不能跑通？ | 能，且结果确定 | §4 #4 + #8 |
| Q3 | 测试是否可信？ | 1997 passed，0 failed；但**「测试多」不等于「结论对」** | §4 #1，并抽查 3 条断言是否真有语义 |
| Q4 | 回测计算是否可信？ | **基本可信，但有边界**：T+1/整手/涨跌停/先卖后买已实现；费率为占位值；停牌靠近似 | 看 `result.limitations` 与 `fee_source` |
| Q5 | 是否满足「找到能赚钱的策略」？ | **不满足**，四个方向全部无正面证据 | 读 `docs/GATE1_FINAL_CLOSEOUT_REPORT.md` |
| Q6 | 是否能实盘？ | **不能**，且第 ④ 层从未检验 | 看 §6.2 |
| Q7 | 有没有粉饰？ | 我的判断：没有。判 `failure` 就是失败，样本外写 `INCONCLUSIVE`，占位费率每次都标注 | 抽查 `docs/GATE1_EXPERIMENT_REPORT.md` 与 `gate1_out/*/summary.json` 是否一致 |
| Q8 | 代码质量如何？ | 21k 行源码 / 28k 行测试（测试多于源码），分层清晰；但**工作副本无版本控制**是真实风险 | 看目录结构与 `git` 状态 |

---

## 9. 判定标准（给结论时请用这三档，不要混）

| 档位 | 判据 | 当前 |
|---|---|---|
| **A. 能运行** | 服务起得来、回测出结果、测试零失败、结果可复现 | ✅ **达成** |
| **B. 能产出可信研究** | A + 数据无致命偏差 + 成本可核实 + 有独立基准 | ⚠️ **部分达成**：装置可信、数据有 PIT 缺口、费率占位 |
| **C. 可实盘** | B + 真实成本 + 容量/冲击/券源 + 实盘滑点验证 | ❌ **未开始** |

**这三档不能互相替代。** 「A 达成」不能推出「B 达成」，「B 达成」也不能推出「C 达成」——
这也是 `docs/STAGE_REPORT_FOR_REVIEW_2026-10-19.md` 里四层结论分离原则的同一要求。

---

## 10. 关键文件索引（按阅读顺序）

| 顺序 | 文件 | 为什么读它 |
|---|---|---|
| 1 | `START_HERE.md` | 使用者视角：三步跑出第一份回测、排障表 |
| 2 | `HANDOFF_2026-10-19.md` | 换机、PR 合并顺序、7 条修复证据、已知坑 |
| 3 | `docs/PROGRAM_REVIEW_PACKAGE_2026-10-19.md` | **本文件** |
| 4 | `docs/GATE1_EXPERIMENT_REPORT.md` | 机械生成的原始实验报告（**不要重新生成**） |
| 5 | `docs/GATE1_FINAL_CLOSEOUT_REPORT.md` | 结项归档：假设、统计、限制、不能回答的问题 |
| 6 | `docs/GATE1_PREREGISTRATION.md` | 预登记 v1.3（只读，sha256 `8b011551`） |
| 7 | `gate1_out/032a11f591553e25/` | 主实验逐窗原始数据 |
| 8 | `docs/OOS_RECALC_2026-10-19.md` | 样本外检验 `R4_INCONCLUSIVE` |
| 9 | `docs/STAGE0_SURVIVORSHIP_V1.2_2026-10-19.md` | 幸存者偏差已测出：中位 −1.67pp |
| 10 | `docs/RESEARCH_FIXES_REPORT.md` | 7 条装置修复的完整记录 |
| 11 | `docs/DATA_FEASIBILITY_REPORT.md` | 数据缺口与采购优先级（未采购） |
| 12 | `docs/KNOWN_ISSUES_SOFTWARE_2026-10-18.md` / `KNOWN_ISSUES_RESEARCH_2026-10-18.md` | 软件侧 / 研究侧已知问题（两份分开） |

---

## 11. 一句话给审查者

如果你想确认「这东西是不是个玩具」——**不是**：它有 1997 条测试、可复现指纹、真实 A 股约束、以及一份把自己判死的实验报告。

如果你想确认「它能不能给我赚钱」——**不能，而且它自己已经诚实地写出来了**。缺的不是代码，是历史时点数据、真实成本模型和足够的样本长度。
