# exQ5 冻结证据索引（A0，阶段二）

- 课题：exQ5 收益来源与可执行性审计 · 阶段二（冻结证据索引）
- 生成时间：2026-10-23
- 执行脚本：`scripts/a0_evidence_index.py`（只读，不读取任何 summary 的结论字段）
- 机器可读清单：`out/a0_evidence/evidence_index.csv`（79 行）、`evidence_index.json`（含 git 状态与数据规模）
- 校验结果：**79 项、0 缺失、0 哈希不符**

## 一、方法

1. 对预登记文档、冻结产物、生产脚本、结题报告、审计产物逐项计算 **sha256**（分块读取，不装载进内存）。
2. 读取预登记 `.sha256` 旁注（兼容「hash + 文件名」与「裸 hash」两种格式），与实算值比对。
3. 校验**溯源链**：运行目录名应等于预登记哈希的前 12 位——这是本课题「跑前冻结、跑后校验」的物证。
4. 记录 git 基线（HEAD/分支/tracked 修改）与数据存档规模。

**不做的**：不重算 `summary.json` 的结论、不修改任何文件、不依赖文件修改时间。

## 二、环境基线

| 项 | 值 |
|---|---|
| HEAD | `5cfed94a4283` |
| 分支 | `workbuddy/2026-10-20-consolidated` |
| tracked 修改 | **无**（`git diff --name-only` 为空） |
| 暂存区 | 空 |

审计全程未修改任何 tracked 文件；新增仅限 `research/exq5_audit_2026-10-22/`。

## 三、溯源链：预登记 ↔ 运行目录（六阶段全部一致）

| 阶段 | 预登记文档 | sha256 前 12 | 旁注 | 运行目录 | 一致性 |
|---|---|---|---|---:|---|
| I | `docs/PHASE_I_EXQ5_PREREG_2026-10-21.md` | `8f5c35121fdb` | 一致 | `phasei_out/8f5c35121fdb` | ✅ |
| J | `docs/PHASE_J_IMPACT_PREREG_2026-10-21.md` | `f76de8a277f6` | 一致 | `phasej_out/f76de8a277f6` | ✅ |
| O | `docs/PHASE_O_FEE_CONVENTION_PREREG_2026-10-21.md` | `ac7aa2830ab0` | 一致 | `phaseo_out/ac7aa2830ab0` | ✅ |
| S | `docs/PHASE_S_EXQ5_MODERN_PREREG_2026-10-22.md` | `f098a59fb1b5` | 一致 | `phases_out/f098a59fb1b5` | ✅ |
| T | `docs/PHASE_T_EXQ5_ROBUSTNESS_PREREG_2026-10-22.md` | `ef29a743997b` | 一致 | `phaset_out/ef29a743997b` | ✅ |
| U | `docs/PHASE_U_NW_LAG_AUDIT_PREREG_2026-10-22.md` | `b97d66d91989` | 一致 | `phaseu_out/b97d66d91989` | ✅ |

**意义**：目录名不是随手起的，而是预登记文件内容的哈希前缀——任何人拿到任一阶段产物，都能反查它对应的预登记版本，无法事后替换规则。

## 四、冻结产物哈希

| 阶段 | 文件 | 字节 | sha256 前 16 |
|---|---|---:|---|
| I | `monthly.csv` | 17,445 | `aa1404cf3b68da30` |
| I | `summary.json` | 7,529 | `fcf36b7a64331251` |
| I | `manifest.json` | 756 | `d8f4c2e3cd41a299` |
| J | `monthly.csv` | 21,560 | `6f1961bdc2709c98` |
| J | `summary.json` | 4,442 | `24391a20a5e4e82e` |
| O | `monthly.csv` | 29,949 | `768e49a9462cd7c0` |
| O | `summary.json` | 3,844 | `05bdc615bece3c2f` |
| S | `monthly.csv` | 42,100 | `3333efe96ac0db29` |
| S | `summary.json` | 12,352 | `ee182b59ca44e4a7` |
| T | `monthly.csv` | 7,126 | `e9ef1d8b0f5dde81` |
| T | `summary.json` | 14,221 | `2e59832d701a278d` |
| U | `profiles.csv` | 8,767 | `03f335a456d328b0` |
| U | `summary.json` | 38,318 | `860ff22fceb0b74b` |

（O/J/S 的 `monthly.csv` 哈希与预登记引用的记录一致：`768e49a9…`、`6f1961bd…`、`3333efe9…`。）

## 五、数据存档（未入 git，单独记指纹）

| 目录 | 文件数 | 大小 | 记忆指纹 |
|---|---:|---:|---|
| `data/exec_all`（真不复权，交易层） | 5,478 | 0.75 GB | `b52ef0b3` |
| `data/qfq_all`（ADJ，研究层） | 5,447 | 0.96 GB | `53f43f39` |

⚠️ `data/` 为 untracked 目录：**无法用 git 保证其未被改动**，只能靠指纹（记忆记录）+ 文件数。本审计全程只读，未写入。

## 六、审计自身产物哈希（自证）

| 文件 | 字节 | sha256 前 16 |
|---|---:|---|
| `EXQ5_BASELINE_REPRODUCTION.md` | 6,682 | `fd319bb95e76594a` |
| `EXQ5_TIME_CONCENTRATION_AUDIT.md` | 4,929 | `d1d6601e477bb1d3` |
| `EXQ5_STOCK_AND_LIQUIDITY_AUDIT.md` | 7,118 | `fd48d82616e98785` |
| `EXQ5_CAPACITY_AND_COST_AUDIT.md` | 6,275 | `3dd1f0cd02635ed7` |
| `EXQ5_EXECUTION_ASSUMPTIONS_AUDIT.md` | 5,807 | `c726d37027b3416f` |
| `EXQ5_STATISTICAL_ROBUSTNESS_AUDIT.md` | 5,763 | `ae36a24f3c5fa0c9` |
| `EXQ5_FORWARD_VALIDATION_PROTOCOL.md` | 6,296 | `75d5591abaac4343` |
| `EXQ5_AUDIT_FINAL_REPORT.md` | 8,730 | `3eb56f71238e9a6d` |
| `EXQ5_LEDGER_RECONCILIATION.md` | 5,266 | `bc084b3b7e45f356` |
| `FWD_LEDGER_BUILD_NOTE.md` | 5,173 | `8710e876c023d3af` |
| `VIBE_TRADING_REPO_ASSESSMENT.md` | 9,517 | `918e57b77854a75c` |

脚本：`audit_common.py` `13c2c96ef442d987`｜`a1` `3f4b7a122dbea1e4`｜`a2` `906800486c0d1174`｜`a3` `db6e3051617c12a8`｜`a4` `a2ae459efb5523a5`｜`a6` `f31427617c6e54eb`｜`fwd_ledger.py` `4146c0fb555c3089`｜`a5_ledger_recon.py` `6dbea7b8cbda26d8`｜`tests/test_audit.py` `b2b5bbe13e5cc8c3`｜`tests/test_fwd_ledger.py` `fe4550528359b517`。

关键输出：`a3/keep_lists.csv` `9501013f4ddba805`｜`a5/summary.json` `7f3fa14a4a242303`｜`a1/repro_table.csv` `425bcd3d5ace6938`｜`a2/concentration.json` `7cf8fb479a35a468`｜`a3/summary.json` `17afd0d17e778c83`｜`a4/summary.json` `22081e6d72900f50`｜`a6/summary.json` `c84e4587f5d94f1e`。

## 七、复现步骤

```bash
# 1) 环境
C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe

# 2) 证据索引（应输出 0 缺失 0 问题）
python research/exq5_audit_2026-10-22/scripts/a0_evidence_index.py

# 3) 单测（应 29/29：审计 15 + 台账 14）
python -m pytest research/exq5_audit_2026-10-22/scripts/tests -q

# 3b) 台账机制干跑（应输出往返费率 ~0.101%；CONS 在 1000 万档放大到 ~0.26%）
python research/exq5_audit_2026-10-22/scripts/fwd_ledger.py --dry-run --month 2026-08 --n 50

# 4) 复算（A1 约 1 分钟；A3 约 4 分钟）
python research/exq5_audit_2026-10-22/scripts/a1_baseline_repro.py
python research/exq5_audit_2026-10-22/scripts/a2_time_concentration.py
python research/exq5_audit_2026-10-22/scripts/a3_panel_rebuild.py
python research/exq5_audit_2026-10-22/scripts/a4_capacity_cost.py
python research/exq5_audit_2026-10-22/scripts/a6_stat_robustness.py
```

## 八、限制

1. `data/` 未入 git，只能靠指纹与文件数核对，**不能证明审计期间未被改动**（本审计只读）。
2. 本索引不覆盖历史阶段（C/E/F/G/K/L/M/N/P/Q/R）的全部产物，只覆盖 exQ5 审计直接引用的 I/J/O/S/T/U。
3. 索引文件自身的哈希不在表内（自引用问题），以 `evidence_index.csv` 为准。

## 九、证据分级

A 级：§二、§三、§四、§六（全部可复算、可核对）；B 级：§五 的数据指纹（依赖记忆记录，未重算全量）。
