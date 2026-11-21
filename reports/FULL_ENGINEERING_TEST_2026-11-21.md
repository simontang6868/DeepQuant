# 全工程测试报告（2026-11-21 18:55 CST）

> 触发：用户 @测试驱动开发 "测试整个工程"
> 执行方式：MCP 环境 + `dq_clean` python（pandas 3.0.6）；pytest 固定绝对 basetemp 串行
> 性质：测试执行报告，不新增/不改任何测试；未下单、未 enable timer、未碰实盘
> 基线：HEAD `e761df9`（本地=origin=cloud 一致）

---

## 0. 结论

**主链路全绿，但发现 6 条历史遗留失败 + 1 条已知检验惰性 + 一批环境钩子噪声。** 工程"回归四连"只覆盖 6 个测试目录中的 2 个；本次把其余 4 个和 30 个变异脚本全跑了一遍，才暴露出长期未被覆盖的 sp5 测试集。

**没有任何一条失败是本轮改动（D4 价格修复 + 评审整改）引入的回归**——用 `git worktree` 在改动前基线 `f6aa17f` 独立复跑 sp5：基线 12 failed，当前 6 failed（还少了 6 条）。

---

## 1. 执行结果总表

| 套件 | 结果 | 说明 |
|---|---|---|
| backend/tests | **3651 passed / 1 skipped** | 在 `backend/` 目录下跑（从仓库根跑会 `No module named tests.api`，跑法问题非工程） |
| scripts/tests | **761 passed / 1 skipped** | 含本轮新增的 `test_price_map_from_ledger...` |
| property_checks | **9/9 PASS** | |
| verify_exec_all | **PASS** | |
| research/sp5/tests | **6 failed / 210 passed** | 历史遗留，见 §2 |
| research/audit/tests | **21 passed** | |
| research/exq5_audit/tests | **29 passed** | |
| scripts/ma_cross_audit/tests | **11 passed** | |
| 30 个变异脚本 | **23 PASS / 7 FAIL（其中 6 条经复跑或分析为环境噪声，1 条为已知惰性）** | 见 §3 |

合计 pytest **3651+761+210+21+29+11 = 4683 passed**，property 9，verify PASS。

---

## 2. sp5 六条历史遗留失败（非本轮引入）

| 测试 | 失败原因 | seam 归属 |
|---|---|---|
| `test_T6_no_silent_fill_in_backtest` | 静态审计发现 p04 仍用 `pd.Series(arr).ffill()`（审计项10） | 源码静态审计 |
| `test_ledger_hash_is_content_dependent` | `ledger_sha256` 在 L1079 先算，`d4_stop_check`/`notes` 在 L1087-1090 后写进 rec ⇒ 声明 hash ≠ 最终 body 重算 hash | 台账自校验一致性 |
| `test_provenance_records_code_hash_and_git_truth` | 断言"research/ 未入 git ⇒ code_in_git=False"，但历史早已入 git | code_provenance 的 git truth |
| `test_record_carries_provenance_and_data_last_date` | 同上，断言 `git_commit == NOT_IN_GIT` 实际有值 | code_provenance |
| `test_verify_is_readonly` | verify 相关，陈旧假设 | 只读性 |
| `test_hardcoded_sites_match_baseline` | 基线未含 p03d_d4_strict.py / p03e_afford.py（历史脚本后加） | 策略参数单一来源 |

**独立证据**：`git worktree` checkout `f6aa17f`（本轮改动前）跑同一套 sp5 = **12 failed / 203 passed**；当前 HEAD = 6 failed / 210 passed。失败数减半，证明本轮改动未引入回归。

其中 `ledger_sha256` 那条值得单独记一笔：它是**真实的实现缺陷**（hash 计算早于最后字段写入），不是纯陈旧假设；但它是 2026-11-03 的既有代码（`git blame` 证实），与本次无关。

---

## 3. 变异测试 7 条 FAIL 的性质判定

| 脚本 | 首跑 | 复跑/分析 | 判定 |
|---|---|---|---|
| `l3ad_mutation_test` | rc=1，5/6 检出 | M1「判据去掉完全覆盖」在本数据上输出不变，脚本自标"本数据上惰性" | **已知检验惰性**（非回归），5 条有效变异均检出 |
| `r8_hashchain_mutation_test` | rc=1 | 复跑 rc=0，6/6 DETECTED + PASS | 并发/环境噪声 |
| `paper_adapter_mutation_test` | rc=1 | 7/7 变异 `✓ 检出`；"恢复后复跑 FAIL"实为 safe-delete 钩子拦截 pytest 临时目录清理 | 环境钩子噪声 |
| `shadow_reconcile_mutation_test` | rc=1 | M3-M12 全 `✓ 检出`；同样 safe-delete 钩子噪声 | 环境钩子噪声 |
| `sim_corroboration_mutation_test` | rc=1 | 复跑 rc=0，2/2 DETECTED，sha 一致 | safe-delete 钩子噪声 |
| `sim_rebalance_executor_mutation_test` | rc=1 | 复跑 rc=0，12/12 DETECTED + 金丝雀 + sha_stable | safe-delete 钩子噪声 |
| `session_mutation_test` | rc=0 但工作树残留 | 残留是 `heartbeat.json`（decision=29991231 的测试副作用，CRLF），已恢复 | 运行时副作用，非源码污染 |

**sandbox 的 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（清理 ≥50 个临时文件时触发）会让依赖 pytest tmp 目录清理的脚本退出码非零**——这是本环境特有问题，不是工程缺陷。所有变异脚本的检测本身均为 DETECTED。

---

## 4. 源码完整性确认

跑完 30 个原地变异脚本后，逐文件核对 sha256：
- `sim_rebalance_executor.py` = `70c039cd…`（manifest 记录一致）
- `sim_corroboration.py` = `d4bea41b…`（manifest 一致）
- `paper_account_adapter.py` = `3901edca…`（H9 修复记录一致）
- `p13_forward.py` = `cd27055e…`（manifest 一致）

工作区已跟踪文件 clean，无变异残留。`PROPERTY_CHECKS.json` 被 property_checks 重跑写成 CRLF（68），已恢复 LF。

---

## 5. 剩余 seam 与建议（需确认后再动）

按 TDD 技能纪律，**补测试/修测试前必须先约定 seam**。本次发现的未覆盖 seam 与建议：

| seam | 现状 | 建议 | 风险 |
|---|---|---|---|
| 台账 `ledger_sha256` 自校验 | 实现有"hash 早于末字段写入"缺陷，测试红 | 若修：把 hash 计算移到所有 rec 字段写入之后；须新预登记（判据邻接变更） | 改动会影响历史台账 hash，属数据格式变更 |
| `code_provenance` git truth | 测试假设"research 未入 git"已过期 | 若修：更新测试断言为当前真实 git 状态 | 纯测试更新，低风险；但属"改测试追代码"，需确认不是掩盖问题 |
| p04 ffill 审计项10 | 静态审计红 | 若修：改 p04 消除 `pd.Series(arr).ffill()` | 涉及研究代码行为，须新预登记 |
| 策略参数硬编码站点 | 基线未含 p03d/p03e | 若修：更新 EXPECTED_SITES 基线 | 纯基线更新 |
| safe-delete 钩子噪声 | 6 个变异脚本受影响 | 若修：脚本改用固定 basetemp 或 `--basetemp` 指向自建目录 | 测试基础设施改动 |

**我不擅自修任何一条**——要么需要新预登记（判据/数据格式），要么需要先跟你确认 seam 属于"测试追代码"还是"代码追测试"。

---

## 6. 引用口径

本件为测试执行报告，不构成任何统计结论或样本外证据。所有判定以实测输出为准，失败项均给出独立复现/基线对比证据，未以"测试全绿"掩盖验收缺口。

署名：simontang
