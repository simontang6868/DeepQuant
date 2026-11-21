# sp5 历史遗留失败修复交付（2026-11-21 21:10 CST）

> 触发：用户「继续工作，全面且不影响工程功能地修复。修复完专家再检查一遍」
> 前置：`reports/FULL_ENGINEERING_TEST_2026-11-21.md`（全工程测试，6 条 sp5 失败）
> 基线：HEAD `453d5ea` → 本次提交 `537fb4c`
> 纪律：TDD 红→绿，seam 先约定；期望值取独立真相源；失败不得为变绿而掩盖

---

## 0. 结论

**6 条 sp5 失败修好 5 条（215 passed / 1 failed），第 6 条（T6 p04 ffill）按既有专家定性保留为缺陷证据、不修。**

修复全部落在测试侧与一处实现排序错误上，**未触碰任何交易/执行/回测行为**：执行器、E1 门、PROBE、timer、选股口径、成本口径零改动。

---

## 1. 6 条失败的定性与处置

| # | 测试 | 真实根因 | 处置 | 依据 |
|---|---|---|---|---|
| 1 | `test_ledger_hash_is_content_dependent` | **实现缺陷**：`ledger_sha256` 在 `d4_stop_check`/`notes.append` 之前计算 ⇒ 声明 hash ≠ 最终 body 重算 | **改代码**：hash 计算移到所有字段写入之后 | 属排序 bug，非口径变更 |
| 2 | `test_verify_is_readonly` | **同一根因的连带**：verify 用最终 body 重算 ⇒ 必然不符 ⇒ rc=1 | **随 #1 自动转绿** | 一因两果 |
| 3 | `test_provenance_records_code_hash_and_git_truth` | **测试假设过期**：断言 `code_in_git is False`，但 research/ 早已入 git；代码如实返回 True 是正确行为 | **改测试**：期望值改为现场 `git rev-parse`/`git ls-files` | 代码行为正确，断言追现状 |
| 4 | `test_record_carries_provenance_and_data_last_date` | 同上（`git_commit == NOT_IN_GIT`） | **改测试**：同上，`_git_truth()` 共用 | 同上 |
| 5 | `test_hardcoded_sites_match_baseline` | **基线过期**：p03d_d4_strict / p03e_afford 为后加的历史脚本，各含 K/THRESH/LIQ_MAX | **改测试**：基线补两站点 + 注释登记 | `scan_hardcoded` 实测值与配置一致（drift 测试仍绿） |
| 6 | `test_T6_no_silent_fill_in_backtest` | **真实回测缺陷**：p04 用 `pd.Series(arr).ffill()` 静默填充缺失行情 | **不修**，保留为缺陷证据 | `docs/EXPERT_REVIEW_STAGE_REPORT_2026-11-03.md:271`：「唯一红是 p04 的 ffill，属回测侧，**改它会动历史结果，须新预登记**」；该测试文件 docstring 亦写明「失败即缺陷证据，禁止为变绿而改被测代码」 |

**为什么 T6 不修**：用户约束「不影响工程功能」。ffill 改动会改变历史回测净值（专家报告已量化：W6 −40.69% → ffill 修正后 −42.99%），属回测行为变更，须新预登记后才能动。保持红即保持拦截力，不弱化为 xfail。

---

## 2. 改动明细（3 文件）

### 2.1 `p13_forward.py`（实现修复）

- **改前**：`rec["ledger_sha256"] = sha256(body)` 在 `rec` 构造后立即执行，随后才写 `rec["d4_stop_check"]` 与 `rec["notes"].append(...)` ⇒ 落盘 body 比 hash 时多两处内容 ⇒ 自校验恒不自洽。
- **改后**：把 hash 计算移到 `d4_stop_check` / `notes.append` 之后、`obs_row` 构造之前（即 rec 全部字段定稿后）。
- **影响面**：
  - 只影响**本次新生成**台账的 `ledger_sha256` 值（语义正确化）；历史台账文件**未重新生成**，其声明 hash 经逐条验证仍自洽（`20260930`/`20261030` 均 `self_consistent=True`）。
  - 下游零影响：`grep` 证实执行器/E1 门/旁证/manifest **均不引用** `ledger_sha256`；E1 只读 `mode`/`target_holds_d4`/价格映射。

### 2.2 `test_phase1_forward_hardening.py`（测试追现状）

- 新增 `_git_truth()`：用 `subprocess` 现场跑 `git rev-parse HEAD` 与 `git ls-files --error-unmatch`，返回 `(tracked, head)`。
- 两处断言改为「断言代码 == 现场 git 真相」：`tracked=True` 时断真实 HEAD、`False` 时断 `NOT_IN_GIT`。两种情形都成立，不是把 `False` 换成 `True` 硬编码。
- 仓库根定位 `parents[4]`（实测正确；`parents[5]` 会是上层目录，已修正）。

### 2.3 `test_strategy_params.py`（基线更新）

- `EXPECTED_SITES` 增 `p03d_d4_strict.py`、`p03e_afford.py` 各 `{K, THRESH, LIQ_MAX}`，并加注释说明为 D4 严格口径历史脚本、值与配置一致。

---

## 3. 验收结果

### 3.1 sp5 测试集

| 项 | 改前 | 改后 |
|---|---|---|
| sp5/tests | **6 failed / 210 passed** | **1 failed / 215 passed** |
| 剩余 1 failed | — | `test_T6_no_silent_fill_in_backtest`（刻意保留的缺陷证据） |

### 3.2 回归四连（改动后重跑，串行 + 固定绝对 basetemp）

| 套件 | 结果 |
|---|---|
| backend/tests | **3651 passed / 1 skipped**（rc=0） |
| property_checks | **9/9 PASS** |
| scripts/tests | **761 passed / 1 skipped**（rc=0） |
| verify_exec_all | **PASS**（V2 零不一致） |

### 3.3 其余测试目录（确认无外溢）

| 目录 | 结果 |
|---|---|
| research/audit/tests | 21 passed |
| research/exq5_audit/tests | 29 passed |
| scripts/ma_cross_audit/tests | 11 passed |

### 3.4 真变异验证（证明修复有检出力）

**M-HASH**：把 hash 计算移回 `d4_stop_check` 之前（回退旧行为）⇒ `test_ledger_hash_is_content_dependent` 立即 **FAILED（DETECTED）**；恢复后源码 sha 回到 `c09d8128`、8 passed。证明该测试能真正识别此缺陷，不是恒绿。

---

## 4. 部署同步

- manifest 重生成：`sha_of_files=ad43c9c4b07a`、commit `537fb4c3`、65 文件；p13 新哈希 `c09d8128` 入册。
- 执行机 `1.92.141.140`：p13 + manifest 已 scp，属主 `deepquant`，哈希 `c09d8128` 一致。
- `deploy_manifest --verify --remote`：**云端 65/65 逐位 PASS**。
- 执行机生产环境 `.venv` 下 `import p13_forward` 成功（`.venv-gm` 无 numpy，仅用于 gmtrade 适配器，非 p13 运行环境）。
- **4 个 sim timer 仍 `disabled`**，未提前武装；未下单、未改实盘。

---

## 5. 独立专家复查（6 点全通过）

子代理独立核查结论：
1. hash 修复正确：verify 重算与生成逐字一致，L1095 之后无 rec 字段再写入；历史台账自洽。
2. 不影响工程功能：`git diff` 仅 3 文件，执行器/E1/PROBE/timer 零改动。
3. git truth 测试非 tautological，`parents[4]`=仓库根，两情形断言均成立。
4. 硬编码基线：22 站点与基线一字不差，p03d/p03e 值与配置全 MATCH。
5. 遗漏检查：215 passed/1 failed 仅 T6，保留决策与专家报告原文一致。
6. 无反模式：期望值来自外部 git，断言未弱化。

复查同时提示：工作区 `heartbeat.json` 是跑测试的副作用（decision=29991231），提交前已剔除。

---

## 6. 剩余事项

1. **T6（p04 ffill）**：保留为缺陷证据，需新预登记才能修（会改历史回测结果）。建议在 12-01 之后单独排期。
2. **sp5 纳入日常回归**：本次修复后 sp5 从 6 红降到 1 红（且该红是登记在案的缺陷），可考虑纳入四连的第五连；是否纳入待用户定。
3. 11-30 出证将用**修复后的 p13**（现已同步执行机），新台账 `ledger_sha256` 自洽，verify 可正常通过。

---

## 7. 引用口径

本件为工程修复记录：`StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`；`MaturityLevel=SIM_DRILL`。不构成统计结论或样本外证据。

署名：simontang
