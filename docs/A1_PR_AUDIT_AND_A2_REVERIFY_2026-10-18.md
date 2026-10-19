# A-1 仓库与 PR 核实 + A-2 复核（2026-10-18 16:00）

> 执行：协调者 ｜ **本文只补两件事**：A-1 的 PR 审计（A 线原报告未做实体核查）、
> 以及 A-2 中 ③ 与 ⑦ 的**独立复核**。
> A-2 主体报告见 `docs/ENGINEERING_BASELINE_REPORT.md`（A 线 15:35 版），本文不重复其环境清单。
> 本轮硬约束：**不 commit / 不 push / 不 merge / 不提 PR**。

---

## 0. 必须先知道：代码有两份，只有一份是权威的

| 位置 | 实际情况 | 能否用于对比/合并 |
|---|---|---|
| `D:\DeepQuant` | 真 git 仓库（`main`，HEAD `6e5e32d`，remote `simontang6868/DeepQuant`，工作区干净） | ❌ 不含任何 Gate 0 产物（`backtest/` 无 `portfolio.py`；`performance/` 无 `benchmarks.py`/`curve.py`/`portfolio_metrics.py`） |
| `D:\DeepQuant power by txm\DeepQuant-main` | 无 `.git`，B18/B19/B20 全在这里，历史经 GitHub API 直推远端 | ✅ 唯一权威工作副本 |

拿 `D:\DeepQuant` 做 diff 会得出「Gate 0 什么都没做」的错误结论。任务书禁止 `git init`，
本轮不改这一结构，只记录清楚。

---

## 1. A-1：PR 实体核查（GitHub REST API）

`git ls-remote` 本机不可达 → 全部走 API（token 取自 `~/.workbuddy/mcp.json` 的 `github-pat`，不硬编码）。
脚本 `a1_pr_audit.py`，原始结果 `a1_pr_audit.json`（均在 `D:\DeepQuant power by txm\`）。

### 1.1 目标 PR

| PR | 标题 | 状态 | base ← head | 变更 |
|---|---|---|---|---|
| **#29** | B15 八年 30 组汇总与诊断 | ✅ **已合并** | `main` ← `b15-8y-summary` | 2 文件 +437/−0 |
| **#30** | B16 策略方向性裁决 | ✅ **已合并** | `main` ← `b16-direction-verdict` | 1 文件 +179/−0 |
| **#31** | B17 结题报告终稿 | ✅ **已合并** | `main` ← `b17-closure-report` | 1 文件 +427/−0 |
| **#35** | B18 Gate 0（一）：涨跌停 + 基准体系 + Sharpe/换手率 + 组合层 A1/A2 | 🟡 开放，`mergeable=true/clean` | `main` ← `b18-gate0-device` | 17 文件 +2338/−16 |
| **#36** | B19/B20：A3 组合记账循环 + 窗口级 B0/B1 基准回补 | 🟡 开放，`mergeable=true/clean` | `b18-gate0-device` ← `b19-a3-portfolio` | 13 文件 +3190/−21 |

### 1.2 叠序结论：#35 → #36 仍成立

```
#35 head = workbuddy/b18-gate0-device
#36 base = workbuddy/b18-gate0-device     ← 相等
```

- **合并顺序必须 #35 → #36，不可反序**。#36 的 diff 是相对 #35 head 计算的。
- 两 PR 有 2 个文件重叠（`backtest/portfolio.py`、`performance/__init__.py`）——这是叠序的
  正常表现（#36 在 #35 新建的文件上继续改），**不是冲突信号**。
- #35 尚未合并，所以「#36 改基到 main」现在不需要做；若 #35 先合，再做一次合并后 diff 复核。

### 1.3 并行自动化冲突排查：无

8 个开放 PR，base 明确、head 互不相同，**无重复 PR、无交叉 base**：

| PR | base ← head | 主题 |
|---|---|---|
| #35 | `main` ← `b18-gate0-device` | Gate 0（一） |
| #36 | `b18-gate0-device` ← `b19-a3-portfolio` | B19/B20 |
| #37 | `main` ← `a13-coverage-refresh` | 覆盖率基线刷新 |
| #38 | `main` ← `a15-r12-window-stats-defense` | R12 防线 |
| #39 | `main` ← `a16-store-contracts` | store.py 契约 |
| #40 | `main` ← `a16-models-contracts` | models.py 契约 |
| #41 | `main` ← `a16-registry-contracts` | registry.py 契约 |
| #42 | `main` ← `a16-signals-contracts` | signals.py 契约 |

---

## 2. A-2 复核：只补 A 线未做的两项

A 线 15:35 版已覆盖 ①~⑥ + 环境清单，本文只做**独立复核**，不复述。

### ② 前端构建（`npm run build`）—— 通过

```
tsc --noEmit  → 通过（无输出即通过）
vite build    → 672 modules transformed，built in 4.69s
dist/index.html  0.41 kB ｜ CSS 10.72 kB (gzip 2.80) ｜ JS 629.63 kB (gzip 179.97)
```

⚠️ 一条**警告**（非失败）：单 chunk > 500 kB。属打包优化项，不阻塞，记录备查。

### ⑦ 结果可重复生成 —— 通过（用哈希证明，不是「看起来一样」）

用 B-1 新落盘的逐窗明细做**逐字节**确定性检验：同标的、同参数网格、同费率档，
**两个独立进程各跑一遍**：

```
af433a28161b8b80/windows.csv  b6eec789222accdfab8b4c76af98f2a741d81b252212b746f9b8cc531445ecfa   32754 B
b10b676fd91e941c/windows.csv  f48f127ae847281390f7fa3de4a9118921503ec5d802fe11e909094d292defef   36285 B
两次运行 → 两组哈希各自相同，cmp 逐字节一致
```

- 覆盖 23 窗 × 2 策略，含 35 / 24 笔成交、费用、基准、策略−基准差值。
- 索引 `w1_index.json` 两次不同，差异**仅** `run_at` 与输出路径 → 因此 `run_at` 被刻意排除在
  `windows.csv` 之外，否则「结果一致」无法用哈希证明。
- 边界：这只证明**确定性**，不证明**正确性**。正确性由口径测试与引擎对账另行保证。

### 测试基线口径（消除一处数字分歧）

| 来源 | 数值 | 说明 |
|---|---|---|
| A 线 15:35 报告 | 1285 passed | 该轮实测 |
| 本轮（含 B-1 新增 23 条） | **1308 passed** / 1 skipped / 6 xfailed / 0 failed | 本轮实测，62s |
| 差 | 23 | 恰好等于本轮新增条数，**对得上** |

早前文档里出现的 **1212** 是更早的快照（A 线 #38~#42 契约测试尚未加入），不是同一时点，不可直接比。

---

## 3. 结论

- **A-1 完成**：#29/#30/#31 已合并；#35/#36 开放且 clean，**叠序 #35→#36 成立**；8 个开放 PR 无冲突。
- **A-2 完成**：①~⑥ 由 A 线实测，③ 与 ⑦ 由本轮独立复核通过；基线 **1308 passed / 0 failed**。
- **A-3 已具备开工条件**（B-1 落盘实现已完成）。
- **B-3 仍被硬闸门挡住**：A-1✅ A-2✅ B-1✅ + B-2⏳ + 用户另行授权。

> 措辞边界：「通过」只表示工程可运行且可重复，**不表示回测结果可信，更不表示策略有效**。
