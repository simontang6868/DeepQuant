# 基线快照 2026-10-19（A-P0 交付物）

> **用途**：在执行「修装置 + 结项归档」阶段前，把**当前可验证的状态**固化成一份可对照的基线。
> 后续 A-P4 的「Gate 1 原始输出未被覆盖」比对、A-P5 的「修复前后差异」都以本文件的指纹为准。
>
> **本轮边界（用户 2026-10-19 01:10 明确要求）**：不 commit / 不 push / 不开 PR，产出只落本地；
> **A 线一行 `src` 都没碰**（本文件全部内容来自只读采集）。
>
> | 项 | 值 |
> |---|---|
> | 采集时间 | 2026-10-19 01:16 ~ 01:32 GMT+8 |
> | 采集者 | A 线（自动化轮次） |
> | 代码根目录 | `D:\DeepQuant power by txm\DeepQuant-main`（**无 `.git`**） |
> | 真实仓库 | `D:\DeepQuant`（**有 `.git`**，但内容停留在 Gate 0 之前） |
> | 解释器 | `D:\DeepQuant power by txm\venv\Scripts\python.exe`（Python 3.13.14） |
> | 原始数据 | `probe_out/ap0_full_run.txt`、`probe_out/ap0_sha256.json`、`probe_out/ap0_remote.json` |

---

## 1. 结论摘要（先看这 7 条）

1. **全量测试基线**：`1746 passed / 1 skipped / 8 xfailed / 0 failed`（107.96s）。**零 failure**。
2. **本地仓库与远端不是同一份东西**：`D:\DeepQuant` 的 HEAD = `6e5e32d`（2026-10-16），
   而 GitHub `main` = `c1919456`（2026-10-17T18:16:46Z）。**本地仓库落后整个 Gate 0**，
   且本地 `origin/main` 跟踪引用**已过期**（仍指向 `6e5e32d`，未 fetch）。
   → **不能把 `D:\DeepQuant` 当作当前代码来源**，代码来源是 `DeepQuant-main`（无 `.git`）。
3. **`gate1_out/` 有 2 个 run 目录 + 1 个索引，共 12 个文件**，目录级聚合指纹 `09cef004…`。
   两个 run 的指纹已分别登记，**后续任何改动都可用它们判定是否被覆盖**。
4. **预登记冻结有效**：`docs/GATE1_PREREGISTRATION.md` 实际 sha256 = `8b011551…`，
   与 `docs/GATE1_PREREGISTRATION.sha256` 第一行（v1.3）**逐字符一致**。
5. **8 条 xfail 对应 4 个缺陷**（1 + 5 + 1 + 1），已逐条登记到 `文件:行`。
6. **两条 P0 缺陷本轮已现场复现**（只读、未跑回测）：`end_date` 完全无效；100 行平盘报价报 **99 个跳变**。
7. **D1 本轮独立确认**：对照 run 里 `monthly_paired.csv` 与 `monthly_paired_excluded.csv`
   **sha256 完全相同、`cmp` 逐字节相同** → 「排除 600809 的对照」与主实验吃的是同一份输入。
   **凡「排除某标的后结论不变」的推断，在 D1 修好前都不成立。**

---

## 2. 仓库与远程状态

### 2.1 两个目录的关系（这是本轮最容易被搞错的一条）

| 目录 | 有无 `.git` | 内容状态 | 用途 |
|---|---|---|---|
| `D:\DeepQuant` | **有** | HEAD `6e5e32d0a0ba…`（2026-10-16 00:54，「Steps 5.5-5.8」） | 名义仓库，**内容落后** |
| `D:\DeepQuant power by txm\DeepQuant-main` | **无** | 全部 Gate 0 / Gate 1 / A–E 阶段产物都在这里 | **实际代码与文档来源** |

**处置（符合保护条款 1）**：**不 `git init`** 后者，不把前者当工作区。

### 2.2 本地仓库 `D:\DeepQuant` 实测

```
分支            main
HEAD            6e5e32d0a0ba93f577ce91ea4a3bfc2f0e01a3c7
HEAD 提交时间   2026-10-16 00:54:29 +0800  "feat: add backtest API and React frontend (Steps 5.5-5.8)"
远程 origin     https://github.com/simontang6868/DeepQuant.git
本地分支        main（仅此一个）
工作区          git status --porcelain 为空（无修改、无未跟踪）
```

⚠️ **`origin/main` 跟踪引用是过期的**：本地 `origin/main` = `6e5e32d`，
而 GitHub API 实测远端 `main` = `c1919456757b070e40e51e0642d430adb2ef8452`。
`git rev-list --count HEAD..origin/main` = 0 **只说明本地引用没更新**，不代表已同步。
→ 本文件所有远端数据一律**以 GitHub API 实测为准**，不用本地 `git` 推断。

### 2.3 远端 `simontang6868/DeepQuant`（GitHub API 实测）

| 项 | 值 |
|---|---|
| 默认分支 | `main` |
| `main` HEAD | `c1919456757b070e40e51e0642d430adb2ef8452` |
| 最近一次 push | 2026-10-18T17:01:22Z |
| 仓库体积 | 1503 KB（public） |
| 分支总数 | **47** |
| 开放 PR | **11** |

最近 5 次提交（全部为 PR 合并）：

| sha | 说明 | 时间 |
|---|---|---|
| `c191945675` | Merge PR #31 `workbuddy/b17-closure-report` | 2026-10-17T18:16:46Z |
| `b7cfbe5b40` | Merge PR #30 `workbuddy/b16-direction-verdict` | 2026-10-17T18:16:36Z |
| `0cec70b429` | Merge PR #34 `workbuddy/a10b-checks-extended-cov` | 2026-10-17T18:16:27Z |
| `d34d37920c` | Merge PR #33 `workbuddy/a11-parameter-selection-cov` | 2026-10-17T18:16:19Z |
| `692606b72a` | Merge PR #32 `workbuddy/a-serialization-contracts` | 2026-10-17T18:16:09Z |

### 2.4 11 个开放 PR（**只核对，不合并**）

| PR | head → base | mergeable | 状态 | 文件 | 改动 | 标题 |
|---:|---|---|---|---:|---:|---|
| #45 | `workbuddy/a13-routes-backtests` → main | ✅ true | `clean` | 1 | +453 / −0 | A13+ #1：`api/routes/backtests.py` 覆盖率 142/7 → 100% |
| #44 | `workbuddy/b-2026-10-18-final` → main | ✅ true | `unstable` | 90 | +21667 / −1057 | B 线收官：Gate 1 实验 + 研究报告 |
| #43 | `workbuddy/a-2026-10-18-final` → main | ✅ true | `unstable` | 62 | +13264 / −121 | A 线收官：测试报告 + 契约测试 + 覆盖率刷新 |
| #42 | `workbuddy/a16-signals-contracts` → main | ✅ true | `clean` | 1 | +128 / −0 | A16#4：`strategies/signals.py` 契约 |
| #41 | `workbuddy/a16-registry-contracts` → main | ✅ true | `clean` | 1 | +101 / −0 | A16#3：`api/registry.py` 契约 |
| #40 | `workbuddy/a16-models-contracts` → main | ✅ true | `clean` | 1 | +262 / −0 | A16#2：`backtest/models.py` 契约 |
| #39 | `workbuddy/a16-store-contracts` → main | ✅ true | `clean` | 1 | +288 / −0 | A16#1：`store.py` 契约 |
| #38 | `workbuddy/a15-r12-window-stats-defense` → main | ✅ true | `clean` | 1 | +159 / −0 | A15：R12 防线 |
| #37 | `workbuddy/a13-coverage-refresh` → main | ✅ true | `clean` | 1 | +59 / −0 | A13：覆盖率基线刷新 |
| #36 | `workbuddy/b19-a3-portfolio` → `workbuddy/b18-gate0-device` | ✅ true | `clean` | 13 | +3190 / −21 | B19/B20：A3 组合记账 + 窗口级 B0/B1 |
| #35 | `workbuddy/b18-gate0-device` → main | ✅ true | `clean` | 17 | +2338 / −16 | B18 Gate 0（一） |

**核对结论**：
- **11 个全部 `mergeable=true`，无一个冲突**。
- `unstable` 仅指 CI 状态未全绿（#43 / #44），**不是合并冲突**，`mergeable` 仍为 true。
- **#36 的 base 不是 main，而是 #35 的 head** —— 叠序 `#35 → #36` 仍然成立；
  合并必须**先 #35 后 #36**，否则 #36 的 diff 会失真。
- 47 个分支中其余 36 个为历史分支（已被合并或已废弃），**本轮不清理**（清理属判断类）。

---

## 3. 全量 pytest 基线

```bash
cd backend
PYTHONPATH=src "D:/DeepQuant power by txm/venv/Scripts/python.exe" \
  -m pytest tests/ -q --ignore=tests/research -rxX -p no:cacheprovider
```

| 指标 | 值 |
|---|---:|
| passed | **1746** |
| skipped | **1** |
| xfailed | **8** |
| **failed** | **0** |
| warnings | 1（`StarletteDeprecationWarning`，第三方提示，非缺陷） |
| 耗时 | 107.96 s |
| 合计用例 | 1755 |

**口径说明**：`--ignore=tests/research` 是既定守卫口径（研究线在途，单独跑见 §5）。
不得把 `tests/research` 的失败计入本基线。

---

## 4. 当前 8 条 xfail 逐条登记

**规则**：全部为 `xfail(strict=True)`。缺陷修好后对应测试会 **XPASS** → strict 使整条用例**报红**，
强制提醒移除标记。这是刻意的「缺陷不会悄悄溜走」机制，**不是测试坏了**。

| # | 文件:行 | 用例 | 对应缺陷 | 修好后的预期 | 本轮计划 |
|---:|---|---|---|---|---|
| 1 | `tests/walkforward/test_walkforward_contracts.py:143` | `test_generate_windows_end_date_respects_boundary` | **P0-1** `generate_windows` 忽略 `end_date` | XPASS | **P0，转正** |
| 2 | `tests/data/test_checks_extended.py:269` | `test_price_jump_count_should_equal_true_violations` | **P0-2** `price_jump` 计数 = 行数−1 | XPASS | **P0，转正** |
| 3 | `tests/data/test_checks_extended.py:288` | `test_flat_prices_should_not_report_price_jump` | **P0-2** 零跳变也误报 | XPASS | **P0，转正** |
| 4 | `tests/data/test_checks_extended_coverage.py:235` | `TestPriceJumps::test_public_path_reports_actual_jump_count` | **P0-2** 同上（60 行真实 3 跳变报 59） | XPASS | **P0，转正** |
| 5 | `tests/data/test_checks_extended_coverage.py:270` | `TestPriceJumps::test_below_threshold_reports_nothing` | **P0-2** 低于阈值仍报 | XPASS | **P0，转正** |
| 6 | `tests/data/test_checks_extended_coverage.py:302` | `TestPriceJumps::test_threshold_raising_keeps_check_silent` | **P0-2** 阈值 15% > 实际 10% 仍报 | XPASS | **P0，转正** |
| 7 | `tests/walkforward/test_parameter_selection_coverage.py:371` | `TestWindowFailurePaths::test_oos_strategy_construction_failure_returns_clean_result` | **P1-1** except 分支访问未绑定 `strategy` → `UnboundLocalError` | XPASS | **P1，转正** |
| 8 | `tests/api/test_backtests_routes_contracts.py:339` | `test_bool_metrics_are_rejected_not_coerced_to_one` | **P1-2** `_num()` 的 `v is not bool` 恒真 | XPASS | **P1，转正** |

### 4.1 ⚠️ 需要协调者裁决的一处口径冲突（A 线不猜）

任务书写「本轮目标是 **P0 两条 + P1 两条转正，其余 4 条保持**并写明原因」。
但实测是 **8 条 xfail ↔ 4 个缺陷**：

- P0-2（price_jump）一个缺陷对应 **5 条** xfail（#2~#6，`checks_extended` 主用例 2 条 + coverage 3 条）；
- 其余三个缺陷各 1 条。

因此**一旦 4 个缺陷全部修好，8 条会全部 XPASS**，strict 会让它们全部报红，**必须全部移除标记**。
「其余 4 条保持」若按字面执行（保留 4 条 xfail 不动），这 4 条在缺陷修好后会 XPASS→**报红**，
与「零 failure」冲突。

**两种可能读法，请协调者确认**：
- **(a) 指缺陷数**：4 个缺陷全修 → 8 条标记全删（`xfailed` 归零）。**这是技术上唯一自洽的执行方式。**
- **(b) 指条目数**：只修 2 个缺陷（覆盖 6 条），另 2 条保持 xfail。需明确保留哪 2 条、为什么。

A 线按 **(a)** 准备（A-P1 的测试规格覆盖全部 4 个缺陷），**未擅自决定删哪 4 条**。

---

## 5. 唯一 1 条 skipped 登记

| 项 | 值 |
|---|---|
| 文件 | `backend/tests/data/test_akshare_live.py:23` |
| 原因 | `pytest.mark.skipif` 联网门控（akshare 实盘接口，需外部网络） |
| 性质 | **刻意跳过**，非缺陷、非回归 |
| 处置 | 保持 |

---

## 6. `gate1_out/` 指纹清单（**禁止覆盖**）

**目录聚合指纹**（按「相对路径 + 文件 sha256」排序串联后再哈希，对重命名敏感）：

```
gate1_out/  12 files  tree_sha256 = 09cef0040bd11c54...
```

| 文件 | 字节 | sha256 |
|---|---:|---|
| `gate1_out/032a11f591553e25/GATE1_RESULT_REPORT.md` | 5,784 | `f0f1f962e6cf566a78bca4069e25d36e211f63e7a96b928163c4bd6f53c7af82` |
| `gate1_out/032a11f591553e25/manifest.json` | 538 | `722b08f784edbb771685e89fe4470a1dcf53f22d593b65b29507ad615b77f194` |
| `gate1_out/032a11f591553e25/monthly_paired.csv` | 3,623 | `54a432f6fd4e1acbad563c745556f92f042c465020303df596de1374bb7e1a47` |
| `gate1_out/032a11f591553e25/sensitivity.csv` | 2,290 | `c02be62fc80d0db6fd58752ee2891b2b4f211ca8ae4abf78c3efffaa3166ab59` |
| `gate1_out/032a11f591553e25/summary.json` | 700 | `b34d2cd1f8e567e2c2f647af25cad2a92769b62209888685381e02493ea94d89` |
| `gate1_out/INDEX.md` | 3,726 | `b17f38fb295db01e170d632afd70897bf9185b086be19d79096e3ddd5210fe94` |
| `gate1_out/a027865f26ea237a/GATE1_RESULT_REPORT.md` | 5,914 | `e5089e11f6b838cf6c6bc43ed182dac0ed863567be17c477c0b5421f08ca7c08` |
| `gate1_out/a027865f26ea237a/manifest.json` | 712 | `194af80219582ed057e4e113bbde1ee6323a5e3b54e3e471138fed4fea6b6842` |
| `gate1_out/a027865f26ea237a/monthly_paired.csv` | 3,622 | `74be84da8d315a8e89d1978aee5cff5b8c8b7e31afea74d0fae02eece95211d2` |
| `gate1_out/a027865f26ea237a/monthly_paired_excluded.csv` | 3,622 | `74be84da8d315a8e89d1978aee5cff5b8c8b7e31afea74d0fae02eece95211d2` |
| `gate1_out/a027865f26ea237a/sensitivity.csv` | 2,366 | `ecebcbb74e3e666c5e2d31c1d45e2a3c381e8dc7471ae14401fea33ed8ab8eb6` |
| `gate1_out/a027865f26ea237a/summary.json` | 1,165 | `1f9b7f404db1c4068bb26ed6141e45fd6452d57ee9e827b9f3b4ae62671e5948` |
| **合计 12 个文件** | 34,062 | 目录聚合 `09cef0040bd11c5454145e327339bb64f592e95bb1418ce9a2b8ff47392e26ad` |

**两个 run 的独立指纹**：

| run | 文件数 | tree_sha256 |
|---|---:|---|
| `gate1_out/032a11f591553e25`（主实验，全池 50 只） | 5 | `6ba955bef57e7674…` |
| `gate1_out/a027865f26ea237a`（对照，排除 600809.SH） | 6 | `670fa339638469a5…` |
| `gate1_out/INDEX.md`（索引，含全部 sha256 与判定） | 1 | 见上表 |

**判定信息（取自 `INDEX.md`，本文件不重算任何指标）**：

| run | 排除标的 | mean(d) | t | p | 胜率 | 判定 |
|---|---|---:|---:|---:|---:|---|
| `032a11f5…` | 无 | −0.003961 | −0.569 | 0.5723 | 41.7% | `failure`（12 组敏感性中 mean(d)>0 的仅 2 组） |
| `a027865f…` | 600809.SH | −0.003284 | −0.464 | 0.6445 | 47.9% | 见 INDEX.md |

> ⚠️ **保护条款 6**：需重跑必须用**新实验 ID + 新输出目录**，不得覆盖上述两个目录。
> A-P4 的「Gate 1 原始输出未被覆盖」检查，就是拿 §6 的指纹与届时实际指纹逐条比对。

---

## 7. `docs/` 指纹清单

**目录聚合指纹**：`docs/  47 files  tree_sha256 = 0e912e2558ae4fb8...`

（44 个 `.md` + 1 个 `.csv` + 1 个 `.sha256` + 1 个 `.gitkeep` = **47 个，均早于本文件**）

| 文件 | 字节 | sha256（前 16） |
|---|---:|---|
| 文件 | 字节 | sha256（前 16） |
|---|---:|---|
| `docs/.gitkeep` | 0 | `e3b0c44298fc1c14` |
| `docs/A1_PR_AUDIT_AND_A2_REVERIFY_2026-10-18.md` | 5,966 | `7af21bdccd7983eb` |
| `docs/A3_ACCOUNTING_SPEC.md` | 12,434 | `772ee7b1450f2ea6` |
| `docs/B15_8Y_30GROUPS_2026-10-18.md` | 16,772 | `b58cddebfe64bd8f` |
| `docs/B16_DIRECTION_VERDICT_2026-10-18.md` | 11,482 | `89c4559a554daed3` |
| `docs/B17_CLOSURE_REPORT_2026-10-18.md` | 23,313 | `24ceb4df05127721` |
| `docs/B18_GATE0_PROGRESS_2026-10-18.md` | 11,336 | `73527cefd448d707` |
| `docs/B18_RESEARCH_VERDICT_2026-10-18.md` | 38,776 | `5bc024a922c530b0` |
| `docs/B19_A3_DELIVERY_2026-10-18.md` | 6,335 | `85d6524300ccd088` |
| `docs/B20_WINDOW_BH_2026-10-18.md` | 14,898 | `f943c5d909bc658a` |
| `docs/B20_WINDOW_BH_2026-10-18_per_group.csv` | 33,658 | `c3a25bdf378e200b` |
| `docs/B3_STAGE1_IMPLEMENTATION_2026-10-18.md` | 9,971 | `f84b49b5ad338fa8` |
| `docs/COVERAGE_GAPS.md` | 25,763 | `f9054558f6113738` |
| `docs/DATASET_POOL_2026-10-18.md` | 7,375 | `ebf3cbcf43f75e8b` |
| `docs/DATA_GATE_FINDINGS_2026-10-17.md` | 6,332 | `f6f382573413376c` |
| `docs/DECISION_PROPOSAL_2026-10-17.md` | 21,516 | `f3c9ffb8e2edf4c7` |
| `docs/DECISION_RULING_2026-10-17.md` | 9,725 | `ffbb4682b3a5f7c4` |
| `docs/DESIGN_PROBE_2026-10-17.md` | 6,812 | `10a430dd9d7e1436` |
| `docs/DIAG_OOS_ZERO_MEDIAN.md` | 8,915 | `f53dead7f6ad1aa0` |
| `docs/ENGINEERING_BASELINE_REPORT.md` | 6,526 | `5df59aebca36ad24` |
| `docs/EQUITY_BOND_SPREAD.md` | 4,548 | `75e9f77cc900fa20` |
| `docs/EQUITY_BOND_SPREAD_US.md` | 4,222 | `040799b53d467297` |
| `docs/EVIDENCE_SYNTHESIS_2026-10-18.md` | 10,102 | `3fe8883d06c5481c` |
| `docs/EXPANSION_2026-10-17.md` | 6,231 | `aa6c81ec1fd3250d` |
| `docs/EXTERNAL_VALIDITY_2026-10-17.md` | 13,836 | `87e932a477ae50ce` |
| `docs/GATE1_EXPERIMENT_REPORT.md` | 5,784 | `f0f1f962e6cf566a` |
| `docs/GATE1_FINAL_ENGINEERING_2026-10-18.md` | 8,669 | `55a0ccce3c719f39` |
| `docs/GATE1_PREREGISTRATION.md` | 37,372 | `8b0115517a7ae35d` |
| `docs/GATE1_PREREGISTRATION.sha256` | 588 | `6ec5bfa0e54fb2a9` |
| `docs/GATE1_STAGE2_IMPLEMENTATION_2026-10-18.md` | 12,460 | `c296a7150328a94a` |
| `docs/GATE1_TRIGGER_REACHABILITY_2026-10-18.md` | 10,292 | `0d311dbb8a4275db` |
| `docs/HISTORICAL_CONCLUSION_AUDIT_2026-10-18.md` | 19,148 | `5000ef1c244c7f14` |
| `docs/KNOWN_ISSUES_RESEARCH_2026-10-18.md` | 10,369 | `0e7b1dcdad8c624a` |
| `docs/KNOWN_ISSUES_SOFTWARE_2026-10-18.md` | 15,175 | `155bbcafc5ce5a2d` |
| `docs/OOS75_BASELINE_2026-10-17.md` | 5,315 | `9557ede5c8a5ec76` |
| `docs/PORTFOLIO_BACKTEST_2026-10-18.md` | 12,483 | `5edbf4b9a620338a` |
| `docs/PRODUCT_READY_2026-10-18.md` | 18,519 | `6ee1f657d4df7eaa` |
| `docs/PROGRESS_SNAPSHOT_2026-10-18.md` | 4,983 | `15be1b1e3dc9b7c9` |
| `docs/PR_REVIEW_2026-10-17.md` | 6,824 | `ebeb3e4e1e2493f8` |
| `docs/RESULT_EXPORT_2026-10-18.md` | 6,577 | `2fe036e6df0190ef` |
| `docs/RETURN_SHAPE_2026-10-18.md` | 8,463 | `08fab244f09024fe` |
| `docs/SOFTWARE_AUDIT_2026-10-18.md` | 15,046 | `d9d7b1c5bd66ef31` |
| `docs/STAGE_REPORT_FOR_REVIEW_2026-10-19.md` | 21,073 | `7fec88031cadb470` |
| `docs/TEST_REPORT_2026-10-18.md` | 24,172 | `9eaac963e94f78f4` |
| `docs/W1_IMPLEMENTATION_REPORT.md` | 12,427 | `f68d373edfb85ae1` |
| `docs/WINDOW_SKEW_2026-10-18.md` | 5,210 | `6f960496e882dc28` |
| `docs/backtest_reference.md` | 6,280 | `81f8bd97d1e695c8` |

> 完整 64 位 sha256 见 `probe_out/ap0_sha256.json`（机器可读）。
> **本文件写入后 docs 指纹会变**；复算时应把 `BASELINE_SNAPSHOT_2026-10-19.md` 自身排除，或用 JSON 里记录的列表作为「变更前」基准。

---

## 8. 预登记冻结校验

| 项 | 值 |
|---|---|
| 文件 | `docs/GATE1_PREREGISTRATION.md` |
| 实际 sha256 | `8b0115517a7ae35df20250c0fdf04736cfa99d9bfd570b89f0d93bc29ec88d63` |
| 校验文件第一行（v1.3） | `8b0115517a7ae35df20250c0fdf04736cfa99d9bfd570b89f0d93bc29ec88d63 *docs/GATE1_PREREGISTRATION.md` |
| 结论 | ✅ **逐字符一致，冻结有效** |

修订历史（取自 `.sha256` 注释行）：

| 版本 | 时间 | sha256 前缀 | 内容 |
|---|---|---|---|
| v1 | 2026-10-18 16:05 | — | 首次冻结 |
| v1.1 | 2026-10-18 16:30 | `5bef0e3b…` | 修订 P1~P4（附录 A） |
| v1.2 | 2026-10-18 19:05 | `f2456912…` | 补记初始资金（附录 B） |
| v1.3 | 2026-10-18 19:30 | `8b011551…` | §6 实现与表述不一致 + 三档冲突优先级（附录 C） |

> **保护条款 5**：本阶段**不得修改**该文件。上表是「改没改」的唯一判据。

---

## 9. 两条 P0 缺陷的现场复现（本轮实测，只读）

以下结果由本轮**直接调用被测函数**得到，**未跑任何回测、未创建任何输出目录**。

### 9.1 P0-1：`generate_windows` 的 `end_date` 完全无效

```python
idx = pd.bdate_range("2018-01-01", "2024-12-31")          # 1827 个交易日
cfg = WalkForwardConfig(train_window=250, oos_window=60, step=60, end_date=...)
```

| `end_date` | 窗口数 | 末窗 `oos_end` |
|---|---:|---|
| `None` | 26 | **2024-12-06** |
| `2022-06-30` | **26** | **2024-12-06** |

**两者完全相同** —— `end_date` 被彻底忽略，末窗越过截断日 **2 年 5 个月**。

根因（源码位置，A 线只读不改）：

```python
# backend/src/deepquant/walkforward/__init__.py:216-225
if cfg.end_date is not None:
    mask = index <= cfg.end_date
    if mask.any():
        end_idx = int(mask.sum())                                    # 220 行：死代码，立刻被覆盖
        last_true_pos = len(index) - 1 - int((~mask)[::-1].argmax())  # 222 行：应为 mask[::-1]
        end_idx = last_true_pos + 1
```

`~mask` 在尾部（所有 `index > end_date` 的位置）恒为 `True`，
`[::-1].argmax()` 取**第一个** True 即 0 → `last_true_pos = len(index)-1` → `end_idx = len(index)`。
**影响**：任何依赖 `end_date` 做样本外截断的调用都会静默失效，属**未来数据泄漏**方向的风险。

### 9.2 P0-2：`price_jump` 计数恒等于「行数 − 1」

| 构造样本 | 真实跳变 | 报告 count | issue 数 |
|---|---:|---:|---:|
| 平盘 100 行（全部 10.00） | **0** | **99** | 1（误报） |
| 第 51 行 +20%，其余平盘 | **1** | **99** | 1 |
| 3 处 ±20% 跳变 | **3** | **99** | 1 |

平盘样本的实际输出（原文）：

```
存在 99 个相邻收盘价跳变 > 15%（最大 0.00%，可能是涨跌停 / 除权除息 / 数据源异常）
```

**「最大 0.00%」却报告 99 个 >15% 的跳变** —— 自相矛盾的告警，说明计数完全失真。

根因（源码位置，A 线只读不改）：

```python
# backend/src/deepquant/data/validation/checks_extended.py:73
price_df = df_sorted[["close"]].astype("float64").dropna()   # 双方括号 → DataFrame，不是 Series
```

`_check_price_jumps` 内部对 DataFrame 做 `pct_change() > threshold` 得到的是**同形布尔 DataFrame**，
再用它索引仍返回 DataFrame（NaN 填充、行数不变），`len(jumps)` 因此恒为 `行数-1`。
**注意 bug 在调用方，不在 `_check_price_jumps` 内部** —— 直接传 Series 时该函数行为正确（已有测试证明）。

**影响面（需 A-P2 专项审计）**：历史研究与实验记录里凡引用过「price_jump 计数」的结论，
其数字很可能都是「行数−1」。`gate1_out` 两个 run 的 `summary.json` 是否引用了该计数，
由 A-P2 逐条核对，本文件不下结论。

### 9.3 D1：对照组与主实验的输入**逐字节相同**（本轮独立确认）

对照 run `a027865f26ea237a` 里有两个逐月明细文件：

| 文件 | 字节 | sha256 |
|---|---:|---|
| `monthly_paired.csv` | 3,622 | `74be84da8d315a8e89d1978aee5cff5b8c8b7e31afea74d0fae02eece95211d2` |
| `monthly_paired_excluded.csv` | 3,622 | `74be84da8d315a8e89d1978aee5cff5b8c8b7e31afea74d0fae02eece95211d2` |

`cmp -s` 实测 **IDENTICAL（逐字节相同）**。

**含义**：所谓「排除 600809.SH 的对照跑」与「主实验」拿到的是**同一份输入**，
因此它的 `mean(d) = −0.003284` **不是**「排除后重算的结果」，而是同一批数据的另一种汇总。
**任何「排除某标的后结论不变 ⇒ 该标的不影响」的推断，在 D1 修好前都不成立**。
这正是 B-P3 要改的「对照组退化」，本文件只登记事实。

### 9.4 D2：累计收益只有一个字段，无法区分「全程」与「评估窗口内」

`scripts/gate1_run.py:600-601` 现状：

```python
"strategy_total_return": r.strategy.cumulative_return,      # 取自 strategy 全程
"benchmark_total_return": r.benchmark.cumulative_return,
```

`gate1_out/*/GATE1_RESULT_REPORT.md:52` 的敏感性表把这两列标为「策略累计 / 基准累计」，
**未说明是全程还是 OOS 窗口内**。`summary.json` 的 `main` 段只有
`n / mean_d / std_d / t_stat / p_value / win_ratio`，**不含任何累计收益字段**。

**含义**：目前**只有一个**累计值，且它覆盖全程（含 IS 预热段），
却被放在 OOS 语境下呈现。D2 要求拆成 `*_total_return_full` 与 `*_total_return_eval` 两个字段名，
本文件只登记现状，不判断该值应取哪个口径。


---

## 10. 本快照如何复算

```bash
# 1) 全量测试
cd backend && PYTHONPATH=src "D:/DeepQuant power by txm/venv/Scripts/python.exe" \
  -m pytest tests/ -q --ignore=tests/research -rxX -p no:cacheprovider

# 2) 指纹（gate1_out + docs + 预登记校验）
"C:/Users/77436/.workbuddy/binaries/python/envs/dq_demo/Scripts/python.exe" probe_out/ap0_sha256.py

# 3) 远端状态（只读 GitHub API）
"C:/Users/77436/.workbuddy/binaries/python/envs/dq_demo/Scripts/python.exe" probe_out/ap0_remote.py
```

| 产物 | 内容 |
|---|---|
| `probe_out/ap0_full_run.txt` | pytest 完整输出（含 8 条 xfail 的 reason 原文） |
| `probe_out/ap0_sha256.json` | gate1_out / docs 的逐文件 sha256 + 目录聚合指纹 |
| `probe_out/ap0_remote.json` | 47 个分支、11 个开放 PR（含 mergeable）、main HEAD |
| `probe_out/ap0_remote.py` / `ap0_sha256.py` | 上述两个采集脚本（只读） |

---

## 11. A-P0 完成度与下游依赖

| A-P0 要求 | 状态 | 落在本文件 |
|---|---|---|
| git / 分支 / 远程状态 | ✅ | §2 |
| 全量 pytest 结果 | ✅ | §3 |
| `gate1_out/` sha256 清单 | ✅ | §6 |
| `docs/` sha256 清单 | ✅ | §7 |
| 当前 8 条 xfail 逐条登记 | ✅ | §4 |

**下游依赖**：

- **A-P1**（缺陷测试先行）：测试规格已明确 4 个缺陷，§9 的复现脚本可作为「修复前」的对照证据。
  ⚠️ 需先裁决 §4.1 的「其余 4 条保持」口径。
- **A-P4**（回归验证）：第 7 项「Gate 1 原始输出未被覆盖」直接比对 §6 的 `tree_sha256` 与两个 run 的独立指纹。
- **A-P5**（修复前后差异）：以 §9 的两张表为「修复前」基线。
- **B 线**：P0-1 对应 §9.1、P0-2 对应 §9.2、P1-1 对应 §4 第 7 条、P1-2 对应 §4 第 8 条，指针与 NIGHT_PROGRESS 一致。
  **D1 对应 §9.3**（B-P3）、**D2 对应 §9.4**（B-P4）——两者本轮均取得现场证据，A-P1 的 D1/D2 测试以这两节为「修复前」基准。

**本轮未做（按边界）**：未 commit / 未 push / 未开 PR；未改 `src` 一行；未改预登记；
未覆盖或删除 `gate1_out/` 任何文件；未 reset / clean / restore / checkout。

---

## 12. 快照之后的变更（2026-10-19 01:24 ~ 02:00，**本节写于快照之后**）

> §1–§11 是 **01:32** 的现场快照，**保持原样不改**（快照的价值就在于「当时是这样」）。
> 本节记录其后发生的事，避免读者拿一份过期的状态去做判断。

### 12.1 B 线在并行会话中修完了全部 4 个缺陷

| 缺陷 | 修复位置 | 现行写法 |
|---|---|---|
| **P0-1** `end_date` 被忽略 | `walkforward/__init__.py:222` | `last_true_pos = len(index) - 1 - int(mask[::-1].argmax())`（`~mask` → `mask`） |
| **P0-2** `price_jump` 计数失真 | `data/validation/checks_extended.py:73` | `df_sorted["close"]`（单层）；`_check_price_jumps` 入口加 `TypeError` 防御 |
| **P1-1** `UnboundLocalError` | `walkforward/parameter_selection.py` except 分支 | 改用 `exc` 绑定，返回 `oos_success=False` |
| **P1-2** `_num()` 布尔判据 | `api/routes/backtests.py:354` | `not isinstance(v, bool)` |

**修复后实测**（A 线现场跑，与 §9 同参数）：

| 项 | 修复前（§9） | 修复后 |
|---|---|---|
| `end_date=None` | 26 窗，末窗 2024-12-06 | 26 窗，末窗 2024-12-06（不变，正确） |
| `end_date=2022-06-30` | **26 窗，末窗 2024-12-06** | **15 窗，末窗 2022-05-27** ✅ |
| 平盘 100 行 | **count=99** | **无 issue** ✅ |
| 1 处跳变 | count=99 | **count=1** ✅ |
| 3 处跳变 | count=99 | **count=3** ✅ |

### 12.2 A 线据此摘掉 8 个 xfail 标记（「转正」）

8 条全部由 XPASS→strict 报红，证明缺陷确实已修。摘除后**断言一条未改**，
仅更新了说明文字（`xfail` → 回归守卫）。涉及文件：

`tests/walkforward/test_walkforward_contracts.py`、
`tests/api/test_backtests_routes_contracts.py`、
`tests/walkforward/test_parameter_selection_coverage.py`、
`tests/data/test_checks_extended.py`（2 条）、
`tests/data/test_checks_extended_coverage.py`（3 条）。

### 12.3 A-P1 新增回归测试 26 条（`backend/tests/research_fixes/`）

| 文件 | 条数 | 覆盖 |
|---|---:|---|
| `test_ap1_generate_windows_end_date.py` | 9 | 上界不越界／确实收紧窗口集／闭区间／start>end 空／区间不足空／train_end<oos_start／确定性／单调性 |
| `test_ap1_price_jump_counting.py` | 9 | 0/1/多跳变精确计数／低于阈值静默／NaN 与全 NaN 不崩／与独立重算逐项一致／DataFrame 入参 `TypeError` |
| `test_ap1_control_input_separation.py` | 4 | 全池 vs 排除池指纹不同／内容敏感／退化碰撞可观测／指纹落盘可追溯 |
| `test_ap1_return_field_separation.py` | 4 | `*_full` 与 `*_eval` 双字段／eval 只吃窗口内收益／窗口变则 eval 变／基准侧同口径 |
| `_ap1_support.py` | — | 惰性加载 `scripts/gate1_run.py` + `RunResult` 工厂（**不是 conftest**，见下） |

**关于「整目录 `xfail(strict=True)`」的偏离（需协调者知悉）**：
任务书要求整目录 xfail，但落地时 4 个缺陷已被 B 线修好。若整目录 strict，
那 26 条里本来就该通过的会立即 **XPASS→strict 报红**，反而把套件染红。
故改为**普通断言**（职责：一旦退化就报红），并在文件头写明理由。
「其余 4 条保持 xfail」这一条也因此不再适用（8 条已全部转正，`xfailed` 归零）。

### 12.4 ⚠️ 一个必须记下的踩坑：`conftest.py` 遮蔽

最初把辅助代码放在 `tests/research_fixes/conftest.py`，结果 pytest 把该目录插进 `sys.path`，
`tests/api/test_backtest_history_api.py` 里的 `from conftest import ingest_dataset`
**解析到了新文件**，全量套件凭空多出 2 条 `ImportError`。
改为普通模块 `_ap1_support.py` 后消失。**后续在本仓库新增测试子目录时不要放 `conftest.py`。**

### 12.5 最新守卫结果（02:03）

| 指标 | 01:32 快照 | **02:03** | 差额 |
|---|---:|---:|---:|
| passed | 1746 | **1780** | +34 |
| skipped | 1 | 1 | 0 |
| xfailed | 8 | **0** | −8 |
| failed | 0 | **0** | 0 |
| 耗时 | 107.96 s | 106.61 s | — |

**对账**：1746 + 8（转正）+ 26（新增）= **1780** ✅ 逐项对得上。

### 12.6 顺带发现并已闭环的缺陷 #9（记录备查）

`make_run_id` / `pool_fingerprint` 原实现只哈希「标的集合 + 行数 + 首末日期」，
**不覆盖价格数值** → 形状相同、价格不同的两份数据会撞同一个 `run_id`（= 输出目录名），
既可能让两次不同数据的产物归到同一目录，也可能让 `ensure_no_overwrite` 误判「已存在」。
A 线用一条断言把它测出来（当时 XPASS 证明 B 线已在 01:33 改为 `_frame_digest` 逐值哈希），
该用例现为回归守卫。

