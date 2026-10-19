# DeepQuant 云端工作手册（换终端 / 换机器 15 分钟复现）

> 生效：2026-10-20 ｜ 负责人：单线（Agent 协调层）
> 用途：**在一台全新的机器上，从零把 DeepQuant 跑起来**。
> 相关：`README.md`、`sandbox_verify/README_FOR_SANDBOX.md`、`docs/PROGRAM_REVIEW_PACKAGE_2026-10-19.md`

---

## 0. 先说清楚：云上有什么、没有什么

| 内容 | 在哪 | 怎么拿 |
|---|---|---|
| **代码 + 文档 + 测试 + 脚本** | GitHub `simontang6868/DeepQuant` | `git clone`（公开仓库） |
| **行情库**（51 只 × 8 年，测试与回测的输入） | 云端资料库（Library） | 下载 `DeepQuant-data-core-2026-10-20.zip` |
| **沙箱交付包**（代码+数据+自检脚本，一个 zip 全包） | 云端资料库（Library） | 下载 `DeepQuant-main_sandbox_2026-10-19.zip` |
| **重型原始数据**（`qfq_mult` 52M / `stage0` 47M / `delist` 27M） | **未上云** | 仅历史研究脚本用得到；跑 8 条清单不需要 |

**为什么数据不进 Git**：仓库 `.gitignore` 明确写着「本地行情数据不入 Git」（保留目录结构与 `.gitkeep`）。
这是项目既定策略，不是疏漏。数据通过**云端资料库**单独分发，两条链路互不污染。

| 云端条目 | 链接 | 体积 | sha256 |
|---|---|---:|---|
| `DeepQuant-data-core-2026-10-20.zip` | https://www.workbuddy.cn/space/d/tFnFW1senk9uORKPRcXcN3 | 3.7 MiB | `520eedbd…d5fbe` |
| `DeepQuant-main_sandbox_2026-10-19.zip` | https://www.workbuddy.cn/space/d/0f2BZBSCU34jMtXPSWa93b | 6.8 MiB | 见包内 `PACKAGE_INFO.json` |

---

## 1. 新终端：两种方式（选一种）

### 方式 A：只要代码（推荐，走 Git）

```bash
# 1) 取代码（分支 = 当前工作线；main 只在合并后才更新）
git clone -b workbuddy/2026-10-20-consolidated \
  https://github.com/simontang6868/DeepQuant.git DeepQuant
cd DeepQuant

# 2) 取数据：从云端资料库下载 DeepQuant-data-core-2026-10-20.zip，
#    解压后把里面的 data/ 覆盖到仓库根（层级必须是 <repo>/data/...）
unzip -q DeepQuant-data-core-2026-10-20.zip -d .
ls data/processed | wc -l        # 期望 51
ls data/metadata  | wc -l        # 期望 51
ls data/step75    | head         # 期望有 qfq CSV + research.py + universe/

# 3) 建环境
python -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
```

### 方式 B：一步到位（沙箱包，代码+数据+脚本全在里面）

```bash
# 从云端资料库下载 DeepQuant-main_sandbox_2026-10-19.zip 后：
unzip -q DeepQuant-main_sandbox_2026-10-19.zip -d .
cd DeepQuant-main
python sandbox_verify/verify_package.py    # 打印 package_id + 705/705 校验
```

> 两个包**内容等价**，沙箱包多一层 `DeepQuant-main/` 前缀与自检脚本。
> 想改代码走 A；只想验证程序能不能跑，走 B 更省事。

---

## 2. 三条只读自检（装完先跑，30 秒）

```bash
cd <仓库根>
python sandbox_verify/check_deps.py            # 依赖齐不齐、版本与 pin 一不一致
python sandbox_verify/check_env.py --port 8123 # 数据根命中哪条规则、51 条在不在、端口占没占
python sandbox_verify/verify_package.py        # 仅方式 B：包完整性与 package_id
```

三条都是**只读**：不装包、不改文件、不起服务。任一条 FAIL 先解决它，别往下跑。

---

## 3. 跑测试与起服务

```bash
# 全量测试（必须在 backend/ 下跑，pytest.ini 在那里）
cd backend && PYTHONPATH=src python -m pytest tests/ -q
```

| 环境 | 期望 |
|---|---|
| Windows + 全量依赖 + 有本机 b50 产物 | `1997 passed / 1 skipped / 0 failed` |
| Linux + 全量依赖 | `1994 / 4 / 0` |
| Linux + 不装 akshare | `1989 / 9 / 0` |

收集数恒为 **1998**（`passed + skipped`），可交叉验算。**这三个数字不要跨环境对表。**

```bash
# 起服务（单端口：API + 界面都在 8123）
cd <仓库根>
python scripts/start_deepquant.py --no-build --no-browser --port 8123
#  → 界面 http://127.0.0.1:8123   API 文档 /docs   健康检查 /health
```

- `--no-build` 必须加：`frontend/dist` 已构建，加了不会去找 npm。
- **不设 `DEEPQUANT_DATA_ROOT` 也能跑**：启动器会自动发现仓库 `data/`；只有绕开启动器直连
  `uvicorn deepquant.api.app:app` 时才必须显式设。
- 后台起服务**不要用 `| head`** → 管道关闭 → SIGPIPE → uvicorn 直接死。用 `> log 2>&1 &`。

一条命令跑完 #2~#5 #8：

```bash
python sandbox_verify/api_checks.py --base http://127.0.0.1:8123
```

---

## 4. 已知坑（换机器必看）

| 现象 | 原因 | 处理 |
|---|---|---|
| 15 个 collection error，`No module named 'deepquant'` | 漏 `PYTHONPATH=src` | 加上 |
| 数据集 0 条 | 直连 uvicorn 且没设 `DEEPQUANT_DATA_ROOT` | 走启动器，或显式设；先跑 `check_env.py` |
| 31 skipped + 1 failed | 缺 `data/step75/` | 它是测试硬依赖，从数据包补 |
| 组合接口 422 | 字段名错 | 是 `dataset_symbols` / `top_fraction`，不是 `symbols` / `top_pct` |
| 自检脚本 502 / 连接拒绝 | 环境设了 `HTTP_PROXY`，urllib 把 127.0.0.1 也送进代理 | `api_checks.py` 已内置 `ProxyHandler({})`；curl 加 `--noproxy '*'` |
| `git log` 报 not a repository | 旧工作副本 `DeepQuant-main` 没有 `.git` | 现在有了；老目录请按 §5 处置 |

---

## 5. 仓库现状与纪律（2026-10-20）

| 项 | 状态 |
|---|---|
| 权威远端 | `https://github.com/simontang6868/DeepQuant.git` |
| 当前工作分支 | `workbuddy/2026-10-20-consolidated`（从 `origin/main` 拉出，**有共同祖先**，可正常合并） |
| **不自动合并到 main** | 项目规则 R8：禁止 push main / 禁止自动 merge。合并在人工确认后由人工执行 |
| 旧仓库 `D:\DeepQuant` | 仅作历史对照（HEAD 停在 2026-10-16），**不要再从它开发** |
| 旧工作副本 | 若本地仍有 `DeepQuant-main` 无 `.git` 的副本，请以 clone 出来的新版为准，避免两套并存 |

**11 个待合并 PR**：状态在变，合并前请先 `gh pr list` 或到 GitHub 页面核对实际状态，不要照抄旧快照。

---

## 6. 换回来的顺序（我这边的标准动作）

1. `git status` 确认分支与工作区干净；
2. `python sandbox_verify/check_deps.py` 与 `check_env.py`；
3. 改代码 → `cd backend && PYTHONPATH=src python -m pytest tests/ -q`；
4. 提交到**新分支**（不直接动 main），推送，必要时开 PR；
5. 更新 `docs/` 下对应文档与 `.workbuddy/memory/YYYY-MM-DD.md` 日志。
