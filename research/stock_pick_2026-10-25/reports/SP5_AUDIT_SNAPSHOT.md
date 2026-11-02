# SP-5 后续审计：执行前快照

> 生成：2026-10-26（专家任务下达后、任何新代码/产物落地前）
> 用途：本快照是专家任务书第十节要求的"开始前先记录 Git commit / 工作区状态 / 配置哈希 / 原始产物清单"。
> 本文件本身**只读**，后续审计不得修改它（规则 4：不改写历史台账与 SELFTEST 记录）。

---

## 1. Git 状态

- **当前 HEAD commit**：`5cfed94a4283defe5adf68f8e963262bda93286d`
- **最近 3 条提交**：
  - `5cfed94` 阶段 U 结题：NW 滞后稳健性全项目审计 —— NW_LAG_SENSITIVITY_CONFIRMED
  - `aa4266e` 阶段 T 结题：exQ5 现代段显著性不承重 —— T_FRAGILE
  - `a768937` 阶段 S 结题：exQ5 现代段（2016-2026）成本后 alpha 可检出 —— MODERN_ERA_ALPHA_DETECTED
- **工作区状态（摘要）**：
  - 已修改（未提交）：`scripts/md_to_docx.py`（历史遗留修改，**非本轮**）
  - 大量未跟踪：本 SP-5 工作区 `research/stock_pick_2026-10-25/` 下全部产物均**未提交**；另有 `data/`、`docs/` 大量未跟踪文件（属其他并行课题，与 SP-5 无关）
  - 结论：**SP-5 全部产物（任务书 / 预登记 / 报告 / 脚本 / 台账）均未纳入 git 版本控制** → 其可信性依赖下方文件级字节哈希，而非 git 历史

---

## 2. 配置 / 冻结产物字节级哈希（sha256 前 16 位）

| 文件 | 哈希(前16) | 大小 | 备注 |
|---|---|---:|---|
| `05_SP5_任务书.md` | `006fdee753376971` | 22643 | 与机制阶段结题报告记录**一致** |
| `SP5_预登记.md` | `32ecc9a6794948f0` | 9948 | = 预登记 v1.2（窗口切 2023-01），与结题报告**一致** |
| `forward/sp4_config_v1.json` | `d4dacb7113cc72ee` | 2116 | **`price_filter: [1.0, 50.0]`**（冻结配置，P0-1 焦点）|
| `forward/ledger/20260930.json` | `01e19ea391d9750c` | 2360 | SELFTEST 决策台账（不可变）|
| `sp5_out/MANIFEST_SP5.json` | `8522dcc43732a228` | 7235 | 14 项产物清单 |
| `data/st_sz_monthly.parquet` | （见 §4 清单） | — | 深市 ST 月频表（P0-2c 用）|

> 注：`SP5_预登记.md` 一个文件承载 v1.0→v1.1→v1.2 三次修订，仅记录当前文件哈希；各版本内嵌哈希见任务书与结题报告（v1.0 `163c5726`/v1.1 `dc6530a3`/v1.2 `32ecc9a6`）。

## 3. 关键代码字节级哈希（用于 P0-1/P0-2/P0-3 溯源）

| 文件 | 哈希(前16) | 用途 |
|---|---|---|
| `scripts/sp5/p04_position_dd.py` | `de47e75cfbde3cf8` | P0-4 真实组合账本（待 P0-2 独立重建比对）|
| `scripts/sp5/p13_forward.py` | `c298552241d38079` | 前瞻决策记录（含 `CLOSE_HI_MAIN=200.0` 主口径）|
| `scripts/sp5/p13b_forward_track.py` | `4fc4e4a1a226f37d` | 成交回填 + 逐日盯市 |
| `scripts/sp5/fill_engine.py` | `2e7bdbd30426c8ea` | 成交位图判定 |

## 4. 原始产物清单（审计开始时已存在）

**报告（reports/）**：
- `SP5_TRADABILITY_CONSTRAINT_AUDIT.md`（P0-3 成交约束）
- `SP5_ST_EFFECT_AUDIT.md`（P0-2c ST）
- `SP5_POSITION_DD_ROBUSTNESS.md`（P0-4 回撤）
- `SP5_EXECUTABLE_WINDOW_AUDIT.md`（P1-1 主口径窗口）
- `SP5_INDUSTRY_EXPOSURE_AUDIT.md`（P1-2 行业）
- `SP5_FORWARD_READY.md`（P1-3 前瞻就绪）
- `SP5_PROGRESS.md`（滚动进度）
- `SP5_结题报告_机制阶段.md`（结题整合）
- `SP5_结题报告_机制阶段_专家审查版.docx`（Word 交付物）

**脚本（scripts/sp5/）**：`wording_audit.py` `st_sz_build.py` `st_exposure_probe.py` `fill_engine.py` `p02c_st_effect.py` `p03_tradability.py` `p04_position_dd.py` `p11_exec_window.py` `p12_industry.py` `p12_industry_map.py` `p13_forward.py` `p13b_forward_track.py` `freeze_manifest.py` `tests/`（59 项测试）

**产物（sp5_out/）**：`sp5_tradability.json` `sp5_st_effect.json` `sp5_position_dd.json` `sp5_exec_window.json` `sp5_industry.json` `sp5_fill_ledger.csv` `sp5_exec_monthly.csv` `prereg_hash_history.json` `MANIFEST_SP5.json` `SP5_TASK_BACKLOG.csv`

**前瞻（forward/）**：`sp4_config_v1.json` `ledger/20260930.json` `ledger/20260930_targets.csv` `nav/20260930_nav.csv` `nav/20260930_track.json`

---

## 5. 已记录的冲突（供 P0-1 直接引用）

- `forward/sp4_config_v1.json` 的 `strategy.price_filter = [1.0, 50.0]`
- `scripts/sp5/p13_forward.py:65` `CLOSE_HI_MAIN = 200.0  # 回测实际口径`；同文件 `:20` 注释："⚠️ 冻结配置的 `price_filter [1.0, 50.0]` 与回测实现 `between(1.0, 200.0)` 不一致"
- `p13_forward.py:218-219` 实际执行：`s_main` 用 `between(1.0, CLOSE_HI_MAIN=200)`，`s_cfg` 用 `between(1.0, CLOSE_HI_CFG=50)`（对照列）
- 前瞻主口径 = 1-200，1-50 仅作对照列（结题报告 §5 已登记为 `CONFIG_PRICE_FILTER_MISMATCH`）

> 本快照仅记录，不判定。P0-1 将据此展开逐项差异表与 `RULE_VERSION_UNRESOLVED` 评估。

---

## ⚠️ 快照自审更正（2026-10-26 23:4x 追加，原文第 30 行不改写）

审计自身也须可复核。本快照第 30 行记录的 `forward/ledger/20260930.json` = `01e19ea391d9750c` **无法用任何常规算法复现**：

| 算法 | 结果 |
|---|---|
| `sha256(原始字节)` | `41952de4403242af`（文件 2,360 字节、102 行 CRLF） |
| `sha256(UTF-8 文本归一为 LF)` | `83f5bb5e79ea88f6` |
| `sha256(json.dumps(indent=2, ensure_ascii=False))` | `83f5bb5e79ea88f6` |
| `sha256(json.dumps(sort_keys=True))` | `c0a4259a24cc61f3`（含 `ledger_sha256` 字段） |
| 台账内部自校验字段 `ledger_sha256` | `512d178698c5230d` = `sha256(sort_keys, 去掉 ledger_sha256)` ✓ 自洽 |
| `sha1(原始字节)` / `sha1(文本)` | `cd1663ab517704a0` / `3deadfee27f463e6` |

**判定**：记 `S1`（快照缺陷）——不是台账被改动。依据：文件 mtime = `2026-10-26 16:34:01`，早于本快照生成时间，且大小与快照记录一致（2,360 字节）。

**教训（写入登记）**：记录哈希**必须同时写明算法与归一化方式**（原始字节 / 文本 LF）。本项目已两次踩同一坑（`*.sha256` 因 CRLF 不归一而不可复核）。本审计后续一律使用 **`sha256(原始字节)`**，并在报告中注明。

**权威值（以此为准）**：`forward/ledger/20260930.json` = `41952de4403242af`（raw bytes）。

**补充（同一份更正，追加证据）**：`reports/SP5_FORWARD_READY.md`（本任务之前的产物）为**同一文件**记录的哈希是 `ae32f5961dd2e593`，与本快照的 `01e19ea3…`、实际字节 `41952de4…` **三者互不相同**。同一文件、同一 mtime 下出现三个不同哈希记录 ⇒ 项目内**哈希记录方式未统一**（是否含 CRLF、是否先 `json` 重排、是否取内部字段），这是 `git_provenance` 可追溯性缺陷的直接证据。今后统一：`sha256(原始字节)` + 在记录中写明算法。
