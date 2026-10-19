# 开放 PR 处置记录（2026-10-20）

> 授权：用户 2026-10-20「第一个合并，剩下的都按你的建议走」
> 执行：单线（Agent 协调层）
> 原则：**先核验，再处置**。不照抄旧快照——旧文档写"11 个 PR"，实际开放 13 个，且集合不同。

---

## 0. 结论先行

**没有一个 PR 是"顺利合进去"的，因为全部 13 个都已被覆盖。**
正确处置是：关闭它们，改由 **PR #59（统一基线）** 一次性把最新状态合入 main。

- PR #59 已合并 → main HEAD = `dd163cf`
- 本文件是这 13 个 PR 的处置审计记录

---

## 1. 核验方法（可复现）

```bash
git fetch origin '+refs/pull/*/head:refs/remotes/pr/*'
# 对每个 PR：取它与 main 的 merge-base，列出变更文件，
# 逐个比较 blob：main 缺失 / 与 main 相同 / 与 main 不同
```

判据：

| 比对结果 | 含义 | 处置 |
|---|---|---|
| 全部文件与 main **逐字节相同** | 内容已进 main（被后来的 PR 带入） | 关闭 |
| 文件在 main 缺失或不同 | 有未合并内容 | 再看它是否已落在 consolidated 分支 |
| 已落在 consolidated 分支 | 内容将通过 PR #59 进 main | 关闭，注明出处 |

---

## 2. 逐条处置（13 个）

| PR | 标题 | 变更文件 | 与 main 相同 | main 缺失 | main 不同 | 其中已在 consolidated | 处置 |
|---|---|---:|---:|---:|---:|---:|---|
| **#35** | B18 Gate 0：涨跌停 + 基准体系 + 组合层 A1/A2 | 17 | 9 | 0 | 8 | — | 关闭（核心代码 `backtest/portfolio.py`、`performance/portfolio_metrics.py`、`performance/benchmarks.py`、`market/rules.py` **均已在 main**） |
| **#36** | B19/B20：A3 组合记账 + 窗口级基准回补 | 28 | 17 | 0 | 11 | — | 关闭（同 #35；其依赖的 #35 内容已在 main） |
| **#37** | A13：刷新覆盖率基线 | 1 | 0 | 0 | 1 | — | 关闭（`docs/COVERAGE_GAPS.md` 在 main 已含 10-18 刷新章节，共 12 处提及；合并只会冲突） |
| **#38** | A15：R12 防线（窗口级统计 ≠ 连续净值） | 1 | **1** | 0 | 0 | — | 关闭（**逐字节已在 main**） |
| **#39** | A16#1：store.py 契约测试 | 1 | **1** | 0 | 0 | — | 关闭（**逐字节已在 main**） |
| **#40** | A16#2：backtest/models.py 契约测试 | 1 | **1** | 0 | 0 | — | 关闭（**逐字节已在 main**） |
| **#41** | A16#3：api/registry.py 契约测试 | 1 | **1** | 0 | 0 | — | 关闭（**逐字节已在 main**） |
| **#42** | A16#4：strategies/signals.py 契约测试 | 1 | **1** | 0 | 0 | — | 关闭（**逐字节已在 main**） |
| **#46** | 【草稿·勿合并】整合验证快照 | — | — | — | — | — | 关闭（**原作者自己标注"勿合并"**：两线改动混合，diff 无法逐条评审，只作 CI 取证与换机通道） |
| **#53** | 口径修复对已冻结样本外结果的影响（文档） | 1 | 0 | 0 | 1 | 0 | 关闭（`docs/STAGE0_OOS_CALIBER_IMPACT_2026-10-19.md` **在 consolidated 分支**） |
| **#54** | 按乘性口径重算样本外结果 | 18 | 0 | 0 | 18 | 6 | 关闭（`OOS_RECALC_*`、`OOS_RESULTS_RECALC_*`、`oos_recalc_prereg_v1.1_*` **全部在 consolidated 分支**） |
| **#56** | 有效池硬闸门 + 资金档定档 + 长区间重算 | 14 | 0 | 0 | 14 | 10 | 关闭（`research/effective_pool.py`、`CAPITAL_TIER_*`、`capital_tier_prereg_*`、`LONG_HORIZON_CALIBER_RECALC_*` **在 consolidated 分支**） |
| **#58** | 阶段 0 结论稳健性检验（资金混淆 + 复权拼接） | 7 | 0 | 0 | 7 | 4 | 关闭（`STAGE0_ROBUSTNESS_*`、`scripts/audit_qfq_mult_drift.py`、`scripts/audit_delist_price_spikes.py`、`scripts/stage0_capital_confound.py` **在 consolidated 分支**） |

---

## 3. 为什么"逐个合并"是错的

三条理由，按重要性排：

1. **#38–#42 已经在 main 里了** —— 后来合并的 #43（A 线收官）完整包含了这些契约测试。
   再合一次不仅无收益，还会因为它们与 main 的当前版本不同而产生冲突。
2. **#35/#36 会把旧版本覆盖回去** —— 它们的 `portfolio.py` 等文件是 10-18 的状态，
   main 上现在的版本经过后续演进。强行合并 = 代码回退。
3. **#53/#54/#56/#58 的内容已在 consolidated 分支** —— 那才是最终落地状态，
   且已通过 `1997 passed / 1 skipped / 0 failed` 验证。再合会与它冲突。

**唯一干净路径**：让 main 追上 consolidated 分支（PR #59，本地试合**零冲突**）。

---

## 4. 保留与未动（红线）

- `gate1_out/032a11f591553e25`、`gate1_out/a027865f26ea237a` 两个原始 run **未改**
- `docs/GATE1_PREREGISTRATION.md`（sha256 前缀 `8b011551`）**未改**
- 任何历史结论未被删除或改写

---

## 5. 教训（记下来，避免重犯）

1. **"N 个待合并 PR"是会过期的**。每次合并前必须重新查实际状态，不能照抄文档里的旧清单。
   本次差异：文档写 11 个（#35–#45），实际开放 13 个，且 #43/#44/#45 已合并。
2. **并行多线会制造"僵尸 PR"**：线 A 提的 PR 还没合，线 B 的收官 PR 已经把同样内容带进 main，
   前者的 PR 就永远停在"开放但已被覆盖"的状态。
3. 因此**单线负责 + 统一基线分支**（本次做法）比"多线各提 PR"更适合单人项目。
