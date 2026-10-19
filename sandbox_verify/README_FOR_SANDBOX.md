# DeepQuant 交付包 —— 给沙箱实测用

> 生成时间：2026-10-20 00:40 GMT+8
> 对应文档：`DeepQuant-main/docs/PROGRAM_REVIEW_PACKAGE_2026-10-19.md`（§4 的 8 条自检清单）
> 本文件只讲"怎么在你的沙箱里把 8 条跑通"，不讲结论。结论在包里 `docs/` 下，自己去核。
>
> **包身份**：根目录 `PACKAGE_INFO.json` 里的 `package_id`（= `MANIFEST.sha256` 的 sha256）。
> **请在复核报告里引用它** —— 这个包被重建过多次，文件名没变、内容变过。上一轮就发生过
> "审的是旧包、评的是新包"，双方都没说谎，只是没有可引用的身份。一条命令可自证：
> `python sandbox_verify/verify_package.py`。
>
> **本包已在一个干净解压副本上自检过**（Windows + Python 3.13.14）：
> 全部受校验文件逐文件 sha256 通过（条数以 `MANIFEST.sha256` 为准）· 全量测试 `1997 passed, 1 skipped, 0 failed`（93.64 s）·
> 单端口起服务 + `sandbox_verify/api_checks.py` 全 PASS · 两个 P0 脚本全 PASS。
> **但请注意：`1997/1/0` 是环境绑定值，不是常量** —— 且该次自检**不是 hermetic** 的
> （有 2 条用例读的是包外的本机产物）。详见 §5.4 与 §5.5，别照抄这个数字对表。

---

## 0. 解压后长这样（目录层级不要改）

```
<你的可写目录>/
├── README_FOR_SANDBOX.md          ← 本文件
├── MANIFEST.sha256                ← 逐文件校验和（sha256sum -c 兼容）
├── PACKAGE_INFO.json              ← 包 ID / 构建时间 / 文件数（**引用这个**）
└── DeepQuant-main/
    ├── backend/src/deepquant/     ← 包源码
    ├── backend/tests/             ← 123 个测试文件
    ├── backend/{requirements.txt,requirements-dev.txt,pyproject.toml,pytest.ini}
    ├── scripts/                   ← 含 start_deepquant.py
    ├── frontend/dist/             ← 已构建产物（不用 npm build）
    ├── data/metadata/ (51)  data/processed/ (51)  data/step75/ (6 CSV+脚本)  data/step75_ext/ (7)
    ├── docs/ (107)  gate1_out/  probe_out/
    ├── sandbox_verify/            ← 核对脚本（见下）
    └── *.md                       ← HANDOFF_2026-10-19.md、START_HERE.md 等
```

`sandbox_verify/` 里能直接跑的六个，各有各的用处：

| 脚本 | 回答什么问题 | 何时用 |
|---|---|---|
| `verify_package.py` | **我手上这份是不是完整包？包 ID 是多少？** | 解压后第一件事 |
| `check_deps.py` | 依赖装齐了吗？版本和 pin 一致吗？ | 装完依赖、跑测试之前 |
| `check_env.py` | 数据根命中哪条规则？51 条数据集在哪？端口占用吗？ | 起服务前 |
| `p0_1_windows.py` | `end_date` 还被忽略吗？ | 清单 #6 |
| `p0_2_price_jump.py` | 平盘还会误报跳变吗？ | 清单 #7 |
| `api_checks.py` | #2~#5 #8 一次跑完 | 服务起来后 |

**两层硬约束**（改了就必然对不上，不是"可能"）：

1. `backend/` 必须直接位于 `DeepQuant-main/` 下。多个测试用
   `Path(__file__).resolve().parents[2]` 反推仓库根，再去读 `scripts/`、`docs/`；
   层级一改，这些用例会红。
2. `pytest` 必须在 `backend/` 目录里跑（`pytest.ini` 在那里，提供 `testpaths` 与 markers）。

---

## 0.5 先做三件事（30 秒，能省掉后面 80% 的"对不上"）

```bash
cd DeepQuant-main

python sandbox_verify/verify_package.py      # ① 包完整吗？ID 是多少？
python sandbox_verify/check_deps.py          # ② 依赖齐吗？版本对吗？
python sandbox_verify/check_env.py --port 8123   # ③ 数据根/数据集/端口
```

三条都是**只读**的：不装包、不改文件、不启动服务。
- ① 若报 `[FAIL]`，**立刻停下**，先重新取包 —— 用不完整的包跑出来的数字没有意义。
- ② 若缺依赖，按 §1.1 装；若版本不符，你的通过数不能和我这边直接对表。
- ③ 会明确打印「启动器解析到哪」与「直连 uvicorn 的默认值」——两者不同时，直连必须设
  `DEEPQUANT_DATA_ROOT`。

---

## 1. 环境


| 项 | 我实测用的 | 你的沙箱 | 说明 |
|---|---|---|---|
| Python | **3.13.14** | 3.14.7 | 见 §1.2，这是本包唯一的高风险项 |
| OS | Windows 11 | Linux | 代码里没有 Windows 专属路径（`start_deepquant.py` 用 `pathlib`） |
| 关键包 | pandas 3.0.6 / numpy 2.5.4 / pyarrow 26.0.0 / fastapi 0.143.1 / pytest 9.1.1 / uvicorn 0.54.0 / httpx 0.28.1 | 需联网装 | 全部 pin 在 `backend/requirements.txt` |

### 1.1 装依赖

```bash
cd DeepQuant-main
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

- `requirements-dev.txt`（`pytest-cov`、`pip-audit`）**不是必需的**，8 条清单不用覆盖率。
- `akshare==1.19.1` 只服务于"真实行情拉取"链路，**这 8 条完全不需要它**。
  如果它在 3.14 上装不动，可以临时用
  `pip install fastapi==0.143.1 uvicorn==0.54.0 pytest==9.1.1 httpx==0.28.1 pandas==3.0.6 numpy==2.5.4 pyarrow==26.0.0 tabulate==0.10.0`
  先把 8 条跑完，再单独报告 akshare 的失败 —— 那是依赖问题，不是程序问题。
  ⚠️ **`tabulate==0.10.0` 不能省**：它以前只作为 akshare 的传递依赖被动装上，
  省掉 akshare 就一起没了，`research/equity_bond_spread/report.py` 的 `to_markdown()`
  会抛 `ModuleNotFoundError`，直接红 2 条。本版已把它写进 `requirements.txt`。

### 1.2 Python 3.14 的风险（提前说，不事后圆场）

我**没有**在 3.14 上验证过。具体风险按大小排：

1. `pandas==3.0.6` / `numpy==2.5.4` / `pyarrow==26.0.0` 是否有 cp314 wheel。
   没有 wheel 会走源码编译，大概率失败或缺编译器。
2. `pytest 9.1.1` 对 3.14 的兼容性。
3. 代码本身用了 `from __future__ import annotations` + 类型标注，没有用到 3.13/3.14 的差异特性，
   **业务代码层面我不预期有 3.14 语法问题**。

如果 3.14 装不上：**优先报告**，不要为了跑通去改 `requirements.txt` 的 pin。
若必须降/升版本才能跑，请在报告里写明"哪些包换了版本、换的理由"，那条结论就从
"1997 passed（原环境）"降级为"在改过依赖的环境下 passed"。

---

## 2. 八条清单（命令 + 期望 + 我这边的实测值 + 陷阱）

> 全部命令都在 `DeepQuant-main/` 下执行。`PYTHONPATH=src` 指 `backend/src`。
> 我另外把 #6 #7 写成了脚本，避免你手抄代码出错；#2~#5 #8 也写成了一个 HTTP 自检脚本。

### 2.1 逐条

| # | 命令 | 期望 | 我的实测值 |
|---|---|---|---|
| 1 | `cd backend && PYTHONPATH=src python -m pytest tests/ -q` | **见 §2.1.1 分环境表**（收集数恒 1998） | 本机 117.59 s：1997/1/0；干净 Linux 副本按装法不同为 1994/4、1989/9 |
| 2 | 起服务后 `curl -s localhost:PORT/health` | `{"status":"ok"}` | `{"status":"ok"}` |
| 3 | `curl -s localhost:PORT/api/datasets` | `datasets` **51** 条 | 51 条，`invalid_datasets` 0 条 |
| 4 | POST `/api/backtests`（见 2.2） | `final_equity=1413448.06`、`fills=109` | 1413448.06 / 109 / cum=+41.34% |
| 5 | POST `/api/portfolio-backtests`（见 2.3） | `cumulative_return=-0.4272`、`fills=120` | -0.42720003 / 120 / final=572799.97 |
| 6 | `PYTHONPATH=backend/src python sandbox_verify/p0_1_windows.py` | 19/30 窗、越界 0（另含 9/44、15/26 两组） | 全 PASS（详见 §3） |
| 7 | `PYTHONPATH=backend/src python sandbox_verify/p0_2_price_jump.py` | 平盘 100 行**无** issue | 全 PASS（详见 §3） |
| 8 | 同参数再 POST 一次 `/api/backtests` | 与 #4 逐位一致 | `final_equity` 与 `fills` 完全相同 |

### 2.1.1 #1 的期望值必须分环境看（**不要对死数字**）

收集数恒为 **1998**，但 `passed / skipped` 的分配取决于三件事：平台、是否装 akshare、本机有没有 `b50_v3` / `b50_dist` 产物。

| 环境 | passed | skipped | failed |
|---|---:|---:|---:|
| Windows 11 + Py3.13.14 + 全量 `requirements.txt`（含 akshare）+ 本机有 b50 产物 | **1997** | 1 | 0 |
| Linux + 全量 `requirements.txt`（含 akshare），无 b50 产物 | **1994** | 4 | 0 |
| Linux + **不装 akshare**（§1.1 的降级装法） | **1989** | 9 | 0 |
| 以上任一，但 **tabulate 缺失** | 各减 2 | — | **2** |

恒等式：`passed + skipped = 1998`。对不上这个和，说明是收集阶段出了问题，先查 `PYTHONPATH`。

那 4 个 skip 的身份（Linux 全量装）：

| 位置 | 原因 | 能否消除 |
|---|---|---|
| `tests/api/test_security.py` | 符号链接测试只在 Windows 跑 | 需 Windows |
| `tests/backtest/test_cross_section.py` | 真实行情样本 `b50_v3/600809.SH.qfq.csv` 不在包内（可用 `DEEPQUANT_REAL_600809` 指过去） | 需该文件 |
| `tests/backtest/test_history_audit.py` | 依赖 `b50_v3` + `b50_dist` 本机产物（可用 `DEEPQUANT_HIST_V3_ROOT` / `DEEPQUANT_HIST_SUMMARY` 指定） | 需该产物 |
| `tests/data/test_akshare_live.py` | 未设 `DEEPQUANT_LIVE`，属安全设计 | 设计如此 |

未装 akshare 时多出的 5 条 skip 来自 `tests/test_research_script_contract.py`（脚本契约要跑通 akshare 导入链）。

> **本版已修**：`backend/requirements.txt` 新增 `tabulate==0.10.0`。
> 此前它只作为 `akshare` 的传递依赖存在（`akshare` 声明 `tabulate>=0.8.6`），
> 一旦按 §1.1 的"不装 akshare"降级装法走，`research/equity_bond_spread/report.py:56`
> 的 `df.to_markdown()` 就会抛 `ModuleNotFoundError`，红 2 条。
> **依赖传递链不能当声明用** —— 这是被独立实测抓出来的真问题。

### 2.2 #4 请求体（字段名不许改）

```json
{"dataset_symbol":"600519.SH","strategy_id":"ma_cross",
 "strategy_params":{"fast_window":5,"slow_window":20},
 "initial_cash":1000000,"start_date":"2018-01-01","end_date":"2024-12-31",
 "signal_lag":1,"save":true}
```

取值路径：`result.performance.final_equity`、`len(result.fills)`、`result.performance.cumulative_return`。

### 2.3 #5 请求体

```json
{"dataset_symbols":["600519.SH","000858.SZ","000001.SZ","601398.SH","601318.SH"],
 "initial_cash":1000000,"selection":"momentum_top","top_fraction":0.4,
 "lookback":60,"skip":5,"min_assets":2,
 "start_date":"2021-01-01","end_date":"2024-12-31"}
```

取值路径：`result.performance.cumulative_return`、`result.performance.final_value`、`len(result.fills)`。
⚠️ 字段名是 `dataset_symbols` / `top_fraction`；写成 `symbols` / `top_pct` 会被 `extra="forbid"` 判 **422**。
这是设计如此，不是缺陷。

### 2.4 起服务（单端口，最容易的一条）

```bash
cd DeepQuant-main
DEEPQUANT_DATA_ROOT="$PWD/data" python scripts/start_deepquant.py --no-build --no-browser --port 8123
```

- 端口默认 8000，我这边一直用 8123，你随意；下文用 `PORT` 占位。
- `--no-build` 必须加：`frontend/dist` 已经打好，加了这个参数就不会去找 npm。
- `--no-browser` 建议加：无头环境里自动开浏览器没有意义，还可能报 xdg-open 噪音。
- 不加 `DEEPQUANT_DATA_ROOT` 也能跑 —— 启动器会自己发现 `仓库/data/metadata/*.meta.json` 并选用它
  （`scripts/start_deepquant.py:resolve_data_root`）。显式设上更稳，**两者结果应一致**，不一致就是 bug。
- 后台起服务**不要用 `| head`**：管道被关 → SIGPIPE → uvicorn 直接死。
  用 `> /tmp/dq.log 2>&1 &`。

一条命令跑完 #2~#5 #8：

```bash
python sandbox_verify/api_checks.py --base http://127.0.0.1:PORT
```

它逐条打印"期望 / 实际 / PASS|FAIL"，退出码 0/1，不做静默断言。

---

## 3. §7.2 "9 vs 19" 矛盾 —— 已解开，结论如下

这是你点名要顺带解决的那条。**两个数都对，因为它们不是同一组输入。**

`generate_windows` 的窗口个数 = `f(index 长度, train_window, oos_window, step)`。
9 / 15 / 19 / 30 / 44 全部来自同一个函数、同一份修复后代码，差别只在索引和参数：

| 口径 | index | train/oos/step | end_date | 窗口数 | 末窗 oos_end | 越界窗 |
|---|---|---|---:|---:|---:|---:|
| **B 线 fix_evidence**（9 的来源） | `bdate_range('2020-01-01', periods=1000)` | 80/60/20（train+oos=140） | 2021-02-24 | **9** | 2021-02-23 | 0 |
| 同上，不设边界 | 同上 | 同上 | None | 44 | 2023-10-31 | — |
| **文档 §5.4 / 本次核对** | `bdate_range('2017-01-03','2024-12-31')`（2086 个工作日） | 250/60/60 | 2022-06-30 | **19** | 2022-05-02 | 0 |
| 同上，不设边界 | 同上 | 同上 | None | **30** | 2024-11-11 | — |
| **A 线 FIX_DIFF §2.1** | `bdate_range('2018-01-01','2024-12-31')`（1827 个工作日） | 250/60/60 | 2022-06-30 | **15** | 2022-05-27 | 0 |
| 同上，不设边界 | 同上 | 同上 | None | 26 | 2024-12-06 | — |
| **真实行情** | `data/processed/600519.SH.daily.parquet`（1943 个交易日） | 250/60/60 | 2022-06-30 | 18 | 2022-06-24 | 0 |
| 同上，不设边界 | 同上 | 同上 | None | 28 | 2024-12-12 | — |

三条可核验的事实：

1. **B 线那组复现成功**：`bdate_range('2020-01-01', periods=1000)` + `train+oos=140` + `step=20`
   恰好给出"修复前 44 窗/35 越界 → 修复后 9 窗/0 越界"，与 `docs/RESEARCH_FIXES_REPORT.md` §1 逐字对上
   （44−9=35，越界数吻合）。B 线的 `fix_evidence/` 没入库，所以参数是我反解出来的；
   **反解成功后两组数字完全自洽，说明不是两次不同的修复**。
2. **语义不变量与参数无关**：任何一组里，`oos_end > end_date` 的窗口数都是 0。
   `p0_1_windows.py` 里还有一个参数扫描段（250/60/60、250/60/20、120/80/60、60/20/20、500/120/120），
   窗口数从 7 变到 68，越界数恒为 0。
3. **索引选真实交易日还是 `bdate_range` 会改变日期与计数**：`2022-05-02` 在 A 股是休市日
   （当年劳动节调休），只有"工作日日历"才会产出这个日期；真实行情给出的是 2022-06-24。
   所以引用窗口日期时必须写明索引口径。

> 我在上一份交接包里写"19 窗/末窗 2022-05-02"时**没写清索引是 `bdate_range`**，这是文档缺陷，不是代码缺陷。
> 本次补上；`sandbox_verify/p0_1_windows.py` 把四组口径全部固化成期望值，跑一遍就能看到差异。

---

## 4. §7.2 之外的另一个"对不上"：600519 的跳变个数

`docs/FIX_DIFF_REPORT_2026-10-19.md` §3.2 说 `600519.SH` 修复后是 **1** 次跳变；
我这次跑 `data/processed/600519.SH.daily.parquet` 全区间（1943 行）得到 **5**。

原因不是计数又坏了，是**窗口不同**：那段是 2018-01-01~2024-12-31（1699 行），
全区间多出的 2017 年有 4 次跳变。我已验证：

| 窗口 | 行数 | count |
|---|---:|---:|
| 2018-01-01 ~ 2024-12-31 | 1699 | **1**（与 §3.2 一致） |
| 2017-01-03 ~ 2024-12-31 | 1943 | 5 |

`p0_2_price_jump.py` 把 1699 行那条作为 PASS 判定，1943 行那条只打 INFO，不当失败。
**跳变数随窗口变是正常现象；缺陷的特征是"恒等于 行数−1"（1942），不是"数值大小"。**

---

## 5. 包里有什么、没有什么

### 5.1 有（你点的四样 + 两样附加）

| 内容 | 对应你的要求 | 备注 |
|---|---|---|
| `backend/src`、`backend/tests`、`backend/tools`、`scripts/`、`frontend/dist`、根 `*.md` | ① 代码树 | `backend/pytest.ini`、`backend/pyproject.toml` 一并给了（缺了 pytest 行为会变） |
| `backend/requirements.txt`、`requirements-dev.txt`、`pyproject.toml` | ② 依赖清单 | 版本全 pin；`requires-python = ">=3.12"`；**本版新增 `tabulate==0.10.0`**（此前是 akshare 的隐式传递依赖） |
| `data/metadata`(51) + `data/processed`(51) + `data/step75_ext`(7) | ③ 行情库 | 回测出数的前提，逐文件校验和在 `MANIFEST.sha256` |
| **`data/step75`（6 个 qfq CSV + `research.py` + `build_universe.py` + `universe/*.json`）** | ③ 附加（**必需**） | 见下方"踩到的坑"：不传它，测试从 1997/1/0 掉到 1966/31/1 |
| `docs/`(107) + `gate1_out/` | ④ 报告与只读原始产物 | Q7"有没有粉饰"用，`gate1_out/*/summary.json` 与 `GATE1_EXPERIMENT_REPORT.md` 对得上 |
| `probe_out/` | 附加 | 8.1 MB 的原始探针产物（含 `ap0_sha256.json` 等），核对"有没有事后改数"用 |
| `sandbox_verify/` | 附加 | 本次新增：`p0_1_windows.py`、`p0_2_price_jump.py`、`api_checks.py` |

### 5.2 没有（附原因）

| 没传 | 原因 |
|---|---|
| `venv/` | 你已说明；Windows 的 `Scripts/*.exe` 在 Linux 不可用 |
| `*.pyc`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache` | 缓存 |
| `frontend/node_modules/`（111 MB） | 用不到，`dist` 已构建 |
| `backend/data/backtests.db`（9.7 MB） | 我本机的历史运行库。**程序会自己建库建表**（`persistence/connection.py:81` 会 `mkdir` 父目录），你跑出来的记录才是你的 |
| `data/qfq_mult`、`data/stage0`、`data/delist`、`data/raw`、`data/benchmark`、`data/oos_recalc_pool*` | 合计约 105 MB，**这 8 条不依赖**；研究脚本（`scripts/` 里那些）才需要。你要跑 Gate 1 复现才需要再找我要 |

### 5.3 踩到的坑（我自己的，登记在这）

我第一版包**没放 `data/step75`**，理由是"1 MB 的旧研究数据，8 条清单用不到"。
在干净解压副本上一跑，实际结果是：

```
1 failed, 1966 passed, 31 skipped
FAILED tests/backtest/test_history_audit.py::test_audit_does_not_compute_returns
  FileNotFoundError: data/step75/universe/csi300_50.json
SKIPPED tests/test_research_script_contract.py ...  "data/step75/research.py 不存在（裁剪检出）" ×30
```

也就是说：**1997/1/0 这个数字本身就依赖 `data/step75`**。
补上 `data/step75` 后重跑，得到 `1997 passed, 1 skipped, 0 failed`，与文档逐字一致。

两个可以直接抄走的结论：
1. 跑测试时若看到 31 skipped + 1 failed，先检查 `data/step75/` 在不在，别急着报"测试坏了"；
2. 那个 `1 skipped` 的**身份随环境变**：在我这台 Windows 上是 `test_akshare_live`
   （未设 `DEEPQUANT_LIVE`），`test_history_audit` 在这台机上是**通过**的；
   换到没有 `b50_v3` / `b50_dist` 的机器上，`test_history_audit` 就会以"无审计对象"身份
   **出现在 skip 里**。这两条路径不要混：
   - `data/step75` 缺失 → `test_history_audit` **fail**（它要 `universe/csi300_50.json`）；
   - `b50_v3`/`b50_dist` 缺失 → 同一个文件里的另一条用例 **skip**。
   所以"看到 skip 就说是私有产物导致的"和"看到 skip 就说不是它"都是错的，得看 `-rs` 的实际身份。

### 5.4 我做的"干净副本自检"——它不是 hermetic 的（重要）

把 zip 解到一个空目录，用包里的东西跑，结果如下：

| 步骤 | 命令 | 结果 |
|---|---|---|
| 完整性 | `zipfile.testzip()` + 逐文件 sha256 | 全部通过（条数随包版本变，以 MANIFEST 为准），0 缺失 0 不匹配 |
| 全量测试 | `cd DeepQuant-main/backend && PYTHONPATH=src python -m pytest tests/ -q` | **1997 passed, 1 skipped, 0 failed, 93.64 s** |
| 起服务 | `python scripts/start_deepquant.py --no-build --no-browser --port 8124` | `Application startup complete`，`/health` 200 |
| HTTP 自检 | `python sandbox_verify/api_checks.py --base http://127.0.0.1:8124` | 6 项全 PASS（含 #4=1413448.06/109、#5=-0.4272/120） |
| P0-1 | `PYTHONPATH=backend/src python sandbox_verify/p0_1_windows.py --data data/processed/600519.SH.daily.parquet` | PASS（19/30、9/44、15/26、18/28 四组） |
| P0-2 | `PYTHONPATH=backend/src python sandbox_verify/p0_2_price_jump.py --data data/processed/600519.SH.daily.parquet` | PASS（平盘无 issue，count=1/3 准确，DataFrame 抛 TypeError） |

**上一版我把这张表写成"包自身即可复现 1997/1/0"，那是过强的结论，已更正。**
独立复核指出了关键一点，我核实后确认成立：解压目录虽然在包外，但**代码里有两条用例走的是
包外的绝对路径**，所以它们在我这台机上能跑、换台机就 skip：

| 用例 | 包外依赖（默认值写死在代码里） | 可否覆盖 |
|---|---|---|
| `tests/backtest/test_cross_section.py` | `D:/DeepQuant power by txm/b50_v3/600809.SH.qfq.csv` | ✅ 本版已支持 `DEEPQUANT_REAL_600809`（原行为不变） |
| `tests/backtest/test_history_audit.py` | `scripts/history_audit.py:38-39` 的 `D:\...\b50_v3`、`D:\...\b50_dist\step75_research_summary.json` | ✅ `DEEPQUANT_HIST_V3_ROOT` / `DEEPQUANT_HIST_SUMMARY` |

因此在**没有 b50 产物的机器**上，`1997/1/0` 会变成 `1994/4/0`（Linux）——这是 §2.1.1 那张表的由来。
`1997/1/0` 的准确表述是：**"作者机（Windows + 有 b50 产物）+ 本包"**，不是"仅凭本包"。

注：全量测试在副本里比在原始工作副本快（94 s vs 118–143 s），差异在磁盘缓存与首次 import，
不是测试被裁掉 —— 收集数都是 1998。

### 5.5 对独立复核报告的回应（采纳 / 更正，留档给下一个审查方）

| 编号 | 复核意见 | 我的处置 | 依据 |
|---|---|---|---|
| D1 | `tabulate` 未声明 → 净装红 2 条 | **采纳修法，但改定级与根因**：已写入 `requirements.txt`；其为 P2 而非 P1 | `pip show tabulate` → `Required-by: akshare`；`akshare` 元数据声明 `tabulate>=0.8.6`。故 `pip install -r requirements.txt` 会装上它；红 2 条只发生在"按 §1.1 不装 akshare"的降级装法。复核执行的是 7 包清单，不是文档里的 `-r requirements.txt` |
| D2 | 1997/1/0 写死，不可跨环境对表 | **完全采纳** | 见 §2.1.1 |
| D3 | §5.4"干净副本自检"口径不实 | **采纳，且定级更高**：这是测试侧真缺陷，不只是文档口径 | 两条用例硬编码绝对路径；本版已让 `test_cross_section` 可覆盖 |
| D4 | §5.3 skip 归属"误导" | **部分采纳**：结论对、事实前提不对 | 本机 `-rs` 实测唯一 skip 是 `test_akshare_live`；`test_history_audit` 在本机通过。已按"skip 身份随环境变"重写 |
| §7.5 | 100k 横线其实有拒单归因 | **采纳**：是我文档缺字段说明 | 单标的归因在 `orders[].reason`，组合在 `blocked[]`；已补进 §6 |
| §1.2 | README 写 699，manifest 700 | **采纳**：我加脚本后漏改数字 | 已改 700 |
| §6 | `docs/PROGRAM_REVIEW_PACKAGE` 在 GitHub main 上 404 | **采纳为交付缺口** | 该文件是本轮为审查生成的本地文件，未推 main（本轮遵守"不发 PR"约束）。以本机工作副本为准 |

### 5.6 对「综合裁决报告」的回应（2026-10-20）

裁决结论我接受：**D1 上我对、A 错**（`tabulate` 是 akshare 的声明依赖，`pip install -r requirements.txt`
会装上它；红 2 条只发生在不装 akshare 的降级装法）。它指出的几处我做得不够的地方，也照改。
但有一条**事实前提**必须先纠正，否则后面所有结论都会挂错版本。

#### 5.6.1 关键事实：裁决所依据的包，不是当前包

裁决 §6 的 T2 写「`DEEPQUANT_REAL_600809` 在**本包中不存在**，`test_cross_section.py:38` 仍是裸 `Path`」。
在当前包里逐字节核过：

```bash
python - <<'PY'
import zipfile
z = zipfile.ZipFile("DeepQuant-main_sandbox_2026-10-19.zip")
t = z.read("DeepQuant-main/backend/tests/backtest/test_cross_section.py").decode()
print("DEEPQUANT_REAL_600809 in file:", "DEEPQUANT_REAL_600809" in t)
print("tabulate in requirements   :", "tabulate==" in z.read("DeepQuant-main/backend/requirements.txt").decode())
PY
# → True / True
```

| 项 | 裁决所依据的包 | 当前包 |
|---|---|---|
| zip sha256 | `6faded1f…730075`（第一版） | `b45a18fc…efc995` → 本版又重建，ID 见 `PACKAGE_INFO.json` |
| `DEEPQUANT_REAL_600809` | **不存在** | **存在** |
| `tabulate==0.10.0` | **未声明** | **已声明** |
| §3.2 数据根 | 写「必须显式设」 | 已改「两种起法分开讲」 |
| README §1.1 降级装法 | 不含 tabulate | 已含，并加"不能省"警示 |

**这不是谁在撒谎，是包没有身份**：文件名一直叫 `DeepQuant-main_sandbox_2026-10-19.zip`，
内容换过三次。所以本版加了 `PACKAGE_INFO.json` + `verify_package.py`，把"审的是哪一版"
变成一条命令能回答的事。**这是本裁决暴露出的、比 D1 更值得修的问题。**

#### 5.6.2 数据根那条，我也实测了（裁决这点是对的）

裁决说"经启动器会自动发现，原文档'必须显式设'过严"。2026-10-20 00:45 在本机复现：

```bash
unset DEEPQUANT_DATA_ROOT
python scripts/start_deepquant.py --no-build --no-browser --port 8126
# 日志：数据集    D:\...\DeepQuant-main\data
# GET /api/datasets → n_datasets = 51, invalid = 0
```

**结论**：启动器路径**不需要**设环境变量；只有直连 `uvicorn deepquant.api.app:app` 才必须设。
原文档那句话是错的（过严），已改为两段式，并做成 `check_env.py` 可直接核对。

#### 5.6.2 逐条处置

| # | 裁决意见 | 我的处置 | 说明 |
|---|---|---|---|
| 1 | D1 归因：B 对 | **接受** | 已在 `requirements.txt` 显式声明并加注释；降级装法同步补 `tabulate` |
| 2 | §1.1 降级装法漏 tabulate（A 漏、B 补） | **已在上一版补上** | 见 §1.1 |
| 3 | T2 `DEEPQUANT_REAL_600809` 待复核 | **当前包已含，无需改代码** | 见 §5.6.1；请以 `verify_package.py` 的标记检查为准 |
| 4 | §3.2 数据根"必须显式设"过严 | **接受，且已做成可执行检查** | 新增 `check_env.py`，直接打印"启动器解析到哪 / 直连默认值是什么" |
| 5 | §4 #6/#7 应指向可跑脚本 | **接受** | `PROGRAM_REVIEW_PACKAGE` 的 #6/#7 已改为指向 `sandbox_verify/p0_*.py` |
| 6 | §4 #1 命令是 Windows 专属路径 | **接受** | 已改为 `<你的 venv>/python`，Windows 写法降为括号内示例 |
| 7 | §0 结论要标环境 | **接受** | 已加"作者环境 / Linux 分环境"限定 |
| 8 | T3 补依赖完备性 CI 冒烟 | **接受，已落地为脚本** | `check_deps.py`：解析 pin、查 `importlib.metadata`、展开 `uvicorn[standard]` extras、缺失/漂版分列 |
| 9 | T5 合并两版文档 | **已按"取长补短"合并** | 采纳 A 的命令可移植性与 #6/#7 指向；保留 B 的 D1/D3/§3.2/§1.1 |

#### 5.6.3 我不接受的一条：定级

裁决把 D1 从 P1 降到 P2，我同意；但它同时把「A 的"现象对、机理错"」写成只是标签问题。
我的判断更重一点：**"靠传递依赖当声明"这个做法本身是 P2，但它导致的后果是"换一种装法就红 2 条，
而文档没告诉你为什么"** —— 这属于**可维护性缺陷**，不是标签错误。所以修法不只是补一行 pin，
而是：显式声明 + 降级装法同步 + 依赖冒烟脚本三件一起做。本版三件都做了。

---

## 6. 已知会"看起来像坏了"的现象（提前登记，避免误判）

| 现象 | 真实原因 | 处理 |
|---|---|---|
| 15 个 collection error，`No module named 'deepquant'` | 漏 `PYTHONPATH=src` | 加上即可 |
| 数据集 0 条 | 没设 `DEEPQUANT_DATA_ROOT` 且 `data/` 不在预期位置 | 显式设，或从仓库根启动 |
| 回测出来一条横线 | 本金买不起 1 手 | 调大本金；单标的拒单归因看 `result.orders[].reason`（如 `insufficient_cash` / `insufficient_position`），**组合**才看 `blocked[]` |
| 出现 `skip` | **身份随环境变**，别猜 | `-rs` 看实际身份：`test_akshare_live`（未设 `DEEPQUANT_LIVE`，设计如此）/ `test_history_audit`（无 `b50_v3`+`b50_dist`）/ `test_cross_section`（无 `b50_v3/600809.SH.qfq.csv`）/ `test_security`（非 Windows）。四种都不是失败 |
| 服务跑一会儿自己死 | 用了 `\| head` → SIGPIPE | 日志重定向 |
| 组合接口 422 | 字段名写错（见 2.3） | 按 2.3 的 body |
| `git log` 报 not a repository | 工作副本没有 `.git` | 版本信息看 GitHub |
| 自检脚本报 `HTTP 502 upstream connect failed` 或 `WinError 10061` | 环境里设了 `HTTP_PROXY`，urllib 把 127.0.0.1 也送进代理（curl 会自动绕过，urllib 不会） | `api_checks.py` 已内置 `ProxyHandler({})` 绕过；若用 curl 请加 `--noproxy '*'` |
| 31 skipped + 1 failed | 少 `data/step75/` | 见 §5.3 |

---

## 7. 我建议你的记录格式（一条对一条，便于回传）

```
#0 包身份      : package_id=<前16位>   verify_package.py=<PASS/FAIL>
#0 依赖        : check_deps.py=<PASS/WARN/FAIL>  缺失=<n> 漂版=<n>
#0 环境        : 数据根=<路径>（命中规则 <1/2/3>） 数据集=<n> 端口=<空闲/占用>
#1 pytest        : <passed/skipped/failed 三数> 耗时 <s>   与 1997/1/0 是否一致
#2 /health       : <原始 JSON>
#3 /api/datasets : datasets=<n> invalid=<n>
#4 单标的        : final_equity=<v> fills=<n> run_id=<v>
#5 组合          : cumulative_return=<v> fills=<n>
#6 P0-1          : <19/30? 越界数?>   [脚本另给 9/44、15/26、18/28]
#7 P0-2          : 平盘 100 行 <有/无 issue>  count=<v>
#8 二次请求      : final_equity 与 fills 是否逐位一致
差异项           : <逐条列出，含 python/pandas 版本变化带来的影响>
```

**不要替我圆场**：只要有一条对不上，就写对不上，并附实际输出。我这边收到差异会去查代码，
而不是改期望值。

---

## 8. 校验和与包身份

`MANIFEST.sha256` 为 `sha256sum -c` 兼容格式（`<hash>  <相对路径>`），在解压出的
`DeepQuant-main/` 目录内执行即可核对。改动过的文件（比如你自己打过补丁的）自然会对不上，
那正好说明它被改过。

**更省事的一条**（顺带告诉你包 ID）：

```bash
cd DeepQuant-main
python sandbox_verify/verify_package.py
```

它会打印 `package_id`（= MANIFEST 的 sha256）、构建时间、逐文件校验结果，以及 9 个关键标记
（`tabulate` 声明、`DEEPQUANT_REAL_600809`、`data/step75`、`frontend/dist`、两个 P0 脚本、
依赖冒烟、预登记原件、Gate 1 原始结果）。

> **请在回传报告里写上 `package_id` 的前 16 位。**
> 这个包重建过多次，文件名始终没变——上一轮复核就因为对不上版本，把"旧包没有的东西"
> 写成了"新包没有"。有了 ID，这种争论不会再发生。

