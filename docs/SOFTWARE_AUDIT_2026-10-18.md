# 可用版复审报告（2026-10-18 20:35）

对象：上一轮交付的「单端口可用版」（`start_deepquant.bat` → `scripts/start_deepquant.py`
→ FastAPI 挂 `frontend/dist`）。
方法：**不读代码猜，起真实进程用 HTTP 打**。所有结论都能用本文件里的命令复现，
失败项都附了修复前后的实测差异。

---

## 0. 一句话结论

界面能开、回测能跑，但**按文档里写的三步上手，会得到一条全程 100,000 的横线**——
60 笔委托全部被拒，而界面只说「回测完成」。这是本轮最严重的问题，且与界面无关，
是默认参数把「买不起」伪装成了「没收益」。另外发现 10 处「看起来正常但语义错了」的问题，
共 11 处全部修复并补测试。

| 编号 | 问题 | 严重度 | 状态 |
|---|---|---|---|
| A1 | 默认引导参数 0 成交，界面无任何解释 | **P0** | 已修（默认资金 + 0 成交提示） |
| A2 | `/api`、`/api/` 返回 `index.html`（200 + HTML） | P1 | 已修（JSON 404） |
| A3 | `/api/backtests/` 被静态挂载吃成 404 | P1 | 已修（尾斜杠归一化） |
| A4 | 静态资源无缓存策略，重建后可能白屏 | P1 | 已修（assets 永久缓存 / 其余 no-cache） |
| A5 | 双击两次起两个实例，共用一个 SQLite | P1 | 已修（同版本复用） |
| A6 | 复用会把用户带回**旧版本**界面 | P1 | 已修（构建指纹比对，旧版判 stale） |
| A7 | `--host 0.0.0.0` 无任何警告 | P2 | 已修（启动横幅显式警告） |
| A8 | 所有端口占用时抛 uvicorn traceback | P2 | 已修（可读错误 + 退出码 1） |
| A9 | 无 favicon（每页 404） | P2 | 已修（SVG 图标） |
| A10 | `--check` 只验证 dist 文件存在，不验证真的挂上 | P3 | 已修（检查 Mount 与中间件） |
| A11 | 同版本但**没挂界面**的实例会被复用 → 用户打开只看到 JSON 404 | P1 | 已修（复用前校验 `/` 是否为 HTML） |

---

## 1. A1（P0）：默认引导参数跑出 0 成交

### 现象

按 `START_HERE.md` 的三步：`600519.SH` × `ma_cross(5,20)` × **100,000** × 2021-01-01~2024-12-31。

```
result.performance.trade_stats
  n_orders = 60   n_filled_orders = 0   n_fills = 0
orders: 30 × BUY  rejected  insufficient_cash
        30 × SELL rejected  insufficient_position
final_equity = 100000.0   cumulative_return = 0.0
```

净值曲线全程等于初始资金。界面顶部写着「回测完成（时间）。以下结果全部来自后端本次真实返回。」

### 为什么这不是「策略没信号」

信号产生了 60 次，每次都下了单，但**一笔都没成交**。原因很具体：本模型按 100 股整手买入，
茅台 2021 年 1 手约 **20 万元**，10 万本金连 1 手都买不起；没有持仓，卖出信号自然也变成
`insufficient_position`。

实测同参数换 100 万本金：

```
n_orders = 60   n_filled_orders = 59
final_equity = 814,728.07   cumulative_return = −18.53%
```

同样的信号、同样的区间，结果从「0% 横线」变成「−18.53% 有成交」。所以 0 成交纯粹是资金不足，
不是策略或数据的问题。

### 为什么危险

用户看到一条横线，合理的第一反应只有两个，两个都错：

- 「软件坏了」→ 弃用；
- 「这个策略没有收益」→ 得出一个**未被检验过的**研究结论。

这属于「不是报错，而是给出一个看起来正常的结论」，与本项目此前在 `--exclude-symbols`
上防的是同一类事故。

### 修法（两处，缺一不可）

1. **默认初始资金 100,000 → 1,000,000**（`frontend/src/utils/backtestPayload.ts`）。
   理由不是「显得有钱」，而是默认值必须让第一次点击就能看到真实成交；100 万下 1 手可买至
   1,000 元/股，覆盖池内绝大多数标的。
2. **新增「0 成交」提示**（`frontend/src/components/NoFillNotice.tsx`）。
   只在「有委托、零成交」时出现，内容包含：拒单原因分布、整手规则、以及资金不足时的
   可执行建议（提高资金 / 换低价标的 / 调参数）。文案明确写「不是策略没有产生信号」。

这条提示是**必要**的：即使默认值改了，用户仍可能把资金改回 10 万，或者选一只 1,000 元以上的股票。

### 复现

```bash
curl -s -X POST http://127.0.0.1:8123/api/backtests \
  -H "Content-Type: application/json" \
  -d '{"dataset_symbol":"600519.SH","strategy_id":"ma_cross",
       "strategy_params":{"fast_window":5,"slow_window":20},
       "initial_cash":100000,"start_date":"2021-01-01","end_date":"2024-12-31"}'
# → trade_stats.n_filled_orders = 0，final_equity = 100000.0
```

---

## 2. A2 / A3：`/api` 前缀的两个边界

`Mount("/")` 排在路由最后，但能匹配**任意**路径，于是「没有路由匹配」的请求不再走
Starlette 的默认处理，而是落到 `StaticFiles`：

| 请求 | 修复前 | 修复后 |
|---|---|---|
| `GET /api` | **200 `text/html`（index.html）** | 404 `application/json` |
| `GET /api/` | **200 `text/html`** | 404 `application/json` |
| `GET /api/backtests/` | 404（本该 307 或直接命中） | 200（尾斜杠归一化后直接命中） |

A2 的危害是「探活」：任何检查 `GET /api` 是否 200 的脚本都会得到「API 可用」，
而它拿到的其实是一张 HTML 页面。A3 的危害是「带尾斜杠就 404」——公开 API 客户端
带尾斜杠很常见，而这与 `redirect_slashes` 的预期行为不符，且原因藏在挂载顺序里。

修复集中在一个函数 `ui._normalize_api_path()`：`/api` 本身返回 `None`（由中间件直接回
JSON 404），其余去掉尾部斜杠再交给路由。

---

## 3. A4：静态资源的缓存语义

修复前 `GET /` 与 `GET /assets/*.js` **都没有 `Cache-Control`**。浏览器会对没有缓存头的
响应做启发式缓存，`index.html` 一旦被缓存，重建后它仍指向旧的哈希文件名——那些文件已被
`vite build` 删除，表现是「白屏，但服务器日志全是 200」。

现在：

```
GET /                  → cache-control: no-cache
GET /assets/index-*.js → cache-control: public, max-age=31536000, immutable
```

`/assets/*` 的文件名带内容哈希，改内容必然改名，永久缓存是安全的；
`index.html` 每次回源，重建立刻生效。

---

## 4. A5 / A6：双击两次与「复用到旧版本」

第一次双击后不关窗口，再双击一次，修复前会**静默换到 8124 再起一个进程**：
两个窗口、同一个 SQLite、两份互不可见的历史写入。

加「复用」之后立刻出现第二个问题，而且更危险：**旧进程还在跑时，复用会把用户带回旧界面**，
而用户以为看到的是刚修好的版本。所以复用不能只判断「端口上是本应用」，还要判断
「是不是这份代码」。

### 做法

- 后端在每个响应上加 `X-DeepQuant-Build`（`backend/src/deepquant/api/buildinfo.py`），
  值由 `backend/src/**/*.py` + `dist/index.html` + 启动脚本的 `size + mtime_ns` 算出，
  启动时算一次并缓存；
- 启动器探测 `/health` 的这个头，与本地指纹比对：
  - 相同 → `[REUSE]` 直接指向已有实例，退出码 0；
  - 不同 / 对方是旧版本（没有这个头）→ `[STALE]` 另起一个端口，并提示关闭旧窗口；
  - `--new-instance` 强制另起。

指纹用 `size + mtime_ns` 而不是文件内容：两百来个文件读内容会让启动慢一个量级，
而「文件被改写」这个事实已经足够区分版本。副作用是「touch 一下也算新版本」，
方向是保守的（宁可多起一个进程，也不让用户对着旧界面以为修复没生效）。

---

## 5. A7 / A8 / A9 / A10

- **A7**：`--host 0.0.0.0` 现在会在横幅里打三行警告：同网段任何人都能访问、
  本应用没有登录与权限控制、可读取全部回测记录并发起任意回测。
- **A8**：`8123~8142` 全被占用时，原来由 uvicorn 抛 `WinError 10048` 的 traceback；
  现在打印「端口 X~Y 全部被占用」+ 处理建议，退出码 1（`.bat` 会停留并 `pause`）。
- **A9**：新增 `frontend/public/favicon.svg` 并在 `index.html` 里声明；
  修复前浏览器每个页面都请求 `/favicon.ico` 拿 404。
- **A10**：`--check` 原来只判断 `frontend/dist/index.html` 存在，那离「界面真的挂上」
  还差一次 `mount_ui` 调用。现在检查 `Mount(name="ui")` 与 `StripApiPrefix` 中间件是否
  真的在栈里，并打印端口上是「空闲 / 已有 DeepQuant / 被其他程序占用」。

---

## 5.5 A11（P1）：复用到了一个「只有 API、没有界面」的实例

这条是复审过程中**实测撞出来的**，不是推演出来的：本机 8123 上有一个用
`python -m uvicorn deepquant.api.app:app` 起的实例（不是启动器起的）。它的
`X-DeepQuant-Build` 与当前代码**完全一致**——同一个仓库、同样编译好的 `dist`——
但 `DEEPQUANT_SERVE_UI` 没设，`GET /` 是 404。

于是「同版本就复用」把用户导向一个只有 JSON 的地址。用户看到的不是报错，而是
`{"detail":"Not Found"}`，最可能的结论是「软件坏了」。

修法：复用前多查一步——`GET /` 必须 200 且 `content-type` 是 `text/html`。
判定状态因此有四种：

| 状态 | 含义 | 处置 |
|---|---|---|
| `none` | 端口空着 | 正常启动 |
| `reuse` | 同版本 + 界面可用 | 指向已有实例，退出码 0 |
| `stale` | 本应用，但版本不同（或旧版无指纹） | 另起端口 + 提示关闭旧窗口 |
| `no-ui` | 同版本，但没挂界面 | 另起端口 + 提示该实例未启用界面 |

实测（8126 上起一个裸 uvicorn）：

```
[NOUI ] 8126 端口上的 DeepQuant 没有启用界面（可能由裸 uvicorn 启动），将另起一个实例。
[PORT ] 8126 已被占用，改用 8127
```

顺带修了一个观测问题：输出重定向到文件/管道时 Python 按块缓冲，启动提示会压到最后才出现，
排障时看到的是「什么都不打印」。现在 `main()` 开头把 stdout 改成行缓冲。

### 指纹不该包含启动脚本

第一版指纹把 `scripts/start_deepquant.py` 也算进去了。结果是：只改启动器的一句提示文案，
也会让正在跑的实例被判成旧版本，端口一路上涨（开发过程中实测到 8127）。启动器是每次双击
都新起的进程，改了它不影响已在运行的服务，所以已从指纹中移除；指纹只覆盖
`backend/src/**/*.py` + `frontend/dist/index.html`。

---

## 6. 复核过、没有问题的部分（附证据）

| 检查 | 结果 |
|---|---|
| 并发 4 个回测（各 ~1,200 个账户状态点） | 4×200，墙钟 0.88 s，无 SQLite 锁错误 |
| 路径逃逸（`/../backend/pyproject.toml`、`%2e%2e%2f`、`/assets/../../README.md`） | 均 4xx，未泄漏文件内容 |
| 错误结构一致性 | 未知数据集 404 `dataset_not_found`；未知策略 404；参数越界 422；非法区间 422 |
| 完整性与可复现接口 | 均 200，`integrity.status = passed` |
| 对比接口边界 | 1 条 → 422；6 条 → 422；重复 run_id → 422；2 条 → 200 |
| 日期越界 | 超出数据范围自动截断（1943 个交易日）；完全在数据外 → 400「回测区间内没有可用的行情数据」 |
| CORS 预检 | 405（未开 CORS，符合预期） |
| 默认关闭 | 不设 `DEEPQUANT_SERVE_UI=1` 时 `/` 仍 404，API 契约逐字节不变 |

---

## 7. 测试与构建

| 项 | 修复前 | 修复后 |
|---|---|---|
| 后端全量 | 1573 passed | **1611 passed / 1 skipped / 6 xfailed / 0 failed** |
| 前端单测 | 157 passed | **162 passed（16 个文件）** |
| 前端构建 | 629.6 kB | 631.2 kB（+1.6 kB：0 成交提示） |

后端 +25 条（`test_ui_single_port.py` +7、`test_start_launcher.py` +18），
另 +13 条来自并行 A 线 20:05 的 `test_persistence/test_store_edge_contracts.py`
（1573 + 25 + 13 = 1611，逐项对得上）。

新增测试覆盖的正是上面每一条修复，其中两条值得单独说：

- `test_bare_api_prefix_is_json_404_not_index_html`：把「`/api` 拿到 HTML」钉死；
- `test_check_existing_instance_reuse_only_on_equal_build`：认不出指纹时必须判 `stale`
  （保守方向），避免「静默复用旧版本」；
- `test_check_existing_instance_rejects_same_build_without_ui`：指纹相同但 `/` 不是 HTML
  时必须判 `no-ui`，避免「复用到一个只有 API 的实例」。

---

## 8. 仍然存在的限制（这一版没有解决，也不打算假装解决）

1. ~~**界面只能选已登记的 5 只数据集**，不能上传文件（API 刻意不接受客户端路径）。~~
   **已于 2026-10-18 21:0x 解决**：`scripts/import_pool.py` 把 `b50_v3` 的 50 只导入，
   界面现有 51 个数据集（池内 48 + 手挑 3），并带名称与筛选。
   详见 `docs/DATASET_POOL_2026-10-18.md`。仍然不能上传文件（这是安全设计，不改）。
2. ~~**只有单标的回测**。组合/多标的的引擎与 API 都在，但界面没有入口。~~
   **2026-10-18 23:0x 已解决**：新增 `POST /portfolio-backtests` 与「组合回测」标签页，
   复用 `backtest/portfolio.py` 的记账循环（不重写撮合）。界面可多选标的、选选股口径
   （动量 top N% / 全池等权）、记账方式（权重 / 整手），结果含被阻挡订单表。
   详见 `docs/PORTFOLIO_BACKTEST_2026-10-18.md`。仍存的两条：组合结果**不写历史记录**
   （历史库是单标的 schema）、单标的与组合的口径不完全对称（10% 涨跌停 / 整手）。
3. **费率是模拟占位参数**（佣金万 3），`fee_source=simulation_placeholder`，不代表真实券商费率。
4. **没有认证**。默认只听 `127.0.0.1`；改成 `0.0.0.0` 会打印警告，但不会阻断。
5. **0 成交提示只处理「全拒」**。部分成交但拒单率很高（例如 90%）目前不提示。
6. 前端是单包 631 kB（gzip 180 kB），本地使用无感，未做代码分割。

---

## 9. 复现本报告的全部检查

```bash
# 1) 环境自检（产物、挂载、前缀剥离、数据根、数据集数、端口、路由）
python scripts/start_deepquant.py --check

# 2) 起服务（不自动开浏览器）
python scripts/start_deepquant.py --no-browser

# 3) 边界探针
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://127.0.0.1:8123/api
curl -s -D - -o /dev/null http://127.0.0.1:8123/ | grep -i cache-control
curl -s -D - -o /dev/null http://127.0.0.1:8123/assets/$(ls frontend/dist/assets | grep '\.js$' | head -1) | grep -i cache-control
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8123/api/backtests/

# 4) 重复启动（应打印 [REUSE] 并退出 0）
python scripts/start_deepquant.py --no-browser

# 5) 测试
cd backend && PYTHONPATH=src python -m pytest tests/ --ignore=tests/research -q
cd ../frontend && npm test
```
