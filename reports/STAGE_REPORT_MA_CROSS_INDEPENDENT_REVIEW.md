# MA-CROSS 独立复核：阶段结题报告

| 项 | 值 |
|---|---|
| 任务 | DeepQuant 双均线策略后续任务：原始证据恢复与 v3 独立复核 |
| 报告日期 | 2026-10-27（GMT+8） |
| 代码基线 | git `5cfed94a`（审计期未产生新提交）；**未修改任何被测代码** |
| 复核对象 | 《双均线策略（ma_cross）结论汇总》2026-10-27（`docs/MA_CROSS_STRATEGY_SUMMARY_2026-10-27.md`） |
| 总判定 | **`CONFIRMED_NEGATIVE_EDGE_IN_TESTED_DESIGN`** |
| 附加限定 | `NEGATIVE_EDGE_WITH_REMAINING_VALIDITY_LIMITATIONS`（连续账户未实现 + 池偏差未量化） |
| KPI 达成 | 原始结果可追溯 ✅｜v2 可复现 ✅｜v3 可复核 ✅｜逐窗配对完成 ✅｜连续账户**明确区分且未伪造** ✅｜未解决问题如实披露 ✅ |

---

## 0. 一页纸

1. **原始产物找到了，而且能自证**：`../b50_dist/step75_research_summary.json` 的 sha256
   与 B20 文档记录的 `source.summary_sha256` **逐字符相同**；48 只行情与备份**逐字节一致 48/48**。
   → 原文"48 只数字不可复核"**解除**。
2. **v2 完全可复现**：重跑 288 个单元 × 9 个字段 = **2,592 项全部逐位相同**（100%）。
3. **v3（涨跌停修复）不改变结论**：**0 个单元符号翻转**；聚合中位从 −2.15% 到 −1.84%，但**逐单元 Δ 中位 = 0.0**、
   总成交只少 **14 笔**（5,256→5,242）→ 这是少数股票位移造成的横截面中位数移动，**不是修复有效**。
4. **配对比较成立且更严格**：2,982 个同窗配对，中位 **−2.93pp**、均值 −4.33pp，
   按股票聚类 95% CI **[−5.45, −3.37]**（不含 0）；**8/8 年为负**；剔 1% 极端后中位不变。
5. **新的机制证据**：配对差随窗口成交笔数**单调恶化**（0 笔 −0.79pp → 5+ 笔 **−12.09pp**）。
   在这套设计里，**"交易"本身是价值损耗源**，而不只是"选时不够好"。
6. **连续账户仍不可用**，且本次**没有伪造**：引擎 `BacktestConfig` 只有 `initial_cash`、无初始持仓字段，
   walk-forward 明文"窗口之间不传递现金/持仓"→ 只出差距分析与最小修复方案。

---

## 1. 八个问题的回答

### Q1 原始 48 只结果是否成功恢复？

**是（12 项清单，6 项验证全过）。** 但有三项**确实从未存在**，不是"丢了"：

| 恢复项 | 状态 |
|---|---|
| v2 主结果（288 条，`oos75-v2`） | **RECOVERED**，sha256 = B20 记录值 |
| 48 只行情 CSV | **RECOVERED**，与 backup 逐字节一致 48/48 |
| B1 基准聚合（B20） | **RECOVERED**（无逐窗明细，B20 自述 `paired=False`） |
| v1 结果（288 条，OOS=50） | RECOVERED（与 v2 不可比） |
| 6 只手挑池数据 | RECOVERED（含被闸门拦的 300750） |
| 逐窗收益明细 | **从未落盘** → 本次重跑生成 |
| 逐窗选参记录 | **从未落盘** → 本次重跑生成 |
| **涨跌停修复版（v3）48 只结果** | **从未产出**（`b50_v3` 是单费率 96 条的早期批次） |

详见 `reports/MA_CROSS_ARTIFACT_RECOVERY.md` + `MA_CROSS_ARTIFACT_MANIFEST.csv`。

### Q2 v2 主结果是否可复现？

**是，100%。** 288 单元 × 9 字段 = 2,592 项，`==` 级别相同（不是"接近"）。
触发条件：`PRICE_LIMIT=off`、池固定 48 只、数据用恢复的原件、`capture_trades=True`（后者被证明不改变任何指标）。

### Q3 v3 修复版是否改变原结论？

**没有。** 三档费率收益中位：−2.15→−1.84%（sim）、−2.13→−2.10%（broker）、−3.11→−2.87%（high_cost）；
**144 个单元中 0 个由负翻正**；亏损窗占比完全不变（0.565/0.565/0.609）。
机制：大市值池 + T+1 开盘成交 → 一字板很少挡住成交，**总成交只少 14 笔**。

### Q4 逐窗配对结果是否支持原先的相对表现判断？

**支持，而且更强。** 配对中位 −2.93pp（B20 分布级 Δ 为 −3.35pp，同向同量级）；
聚类 CI 整段为负；38/48 只股票的中位差为负；8 年中位全负；剔极端不变。
同时补上了原文缺失的**置信区间**。

### Q5 连续账户回测是否已可靠实现？

**没有，且本次未生成。** 十项能力核查：PASS 4 / FAIL 4 / PARTIAL 2。
硬缺口：① 引擎无"初始持仓"字段；② walk-forward 每窗重置账户并重选参；③ 停牌未显式建模。
详见 `reports/MA_CROSS_CONTINUOUS_ACCOUNT_GAP_ANALYSIS.md`（含 4 步最小修复方案）。

### Q6 哪些数据偏差仍未解决？

| 偏差 | 状态 | 影响 |
|---|---|---|
| 幸存者偏差（当日成分回填 2017-2024） | **已确认、量级不可验证** | 使结果偏乐观 → 负结论更稳健，但幅度未知 |
| 11 只晚上市（最晚 2022-08） | 已量化 | 窗口数 5~23 不等；等权聚合会被少窗口股影响 |
| 前复权 ≠ 全收益；期末不平仓 | 已声明 | 两侧同偏差，配对受影响小 |
| 停牌未建模 | 已确认 | 信号可跨停牌期成交，延迟未标记 |
| 窗口/股票相关性 | 已用聚类 bootstrap 缓解 | 有效独立观测远少于 2,982 |
| 可负担性断崖 | **不影响 48 只主池**（1 手 > 10 万：0 只） | 仅影响 6 只手挑池（茅台） |

详见 `reports/MA_CROSS_UNIVERSE_BIAS_AUDIT.md`。

### Q7 当前证据是否足以终止该设计空间内的进一步研究？

**足以终止"继续调参"，但建议先做两件便宜的事再正式归档**（见 Q8）。
理由：① 配对差 CI 不含 0；② 8/8 年为负；③ 剔极端不变；④ v3 修复不改变；⑤ 而且配对差与成交笔数单调恶化
——**更多交易 = 更多损耗**，这条路不存在"换个参数就好"的空间。

### Q8 若不终止，重新启动需要满足什么前置条件？

| # | 前置条件 | 现状 |
|---|---|---|
| P1 | 连续账户回测（引擎支持初始持仓 + 结转权益） | ❌ 未实现（差距分析已给方案） |
| P2 | 时点成分快照（消除幸存者偏差） | ❌ 无历史成分数据 |
| P3 | 事件密度与窗口长度匹配（OOS ≥ 慢线 2~3 倍） | ⚠️ 现为 75 日 vs 慢线 60 日，勉强 |
| P4 | 真实成本 + 可负担性（已具备，主池无断崖） | ✅ |
| P5 | 内建基准（B1 已在 W1 记录内，但引擎层仍无） | ⚠️ 部分 |
| P6（本轮新增） | 涨跌停**按日期**切换（创业板 2020-08-24 前后） | ❌ `MarketRules` 静态 |
| P7（本轮新增） | 停牌显式建模 + 订单顺延标记 | ❌ |

---

## 2. 最终判定与枚举映射

```
CONFIRMED_NEGATIVE_EDGE_IN_TESTED_DESIGN
  ├─ 附加限定：NEGATIVE_EDGE_WITH_REMAINING_VALIDITY_LIMITATIONS
  └─ 不适用：INCONCLUSIVE_DUE_TO_MISSING_ARTIFACTS（产物已恢复）
             INCONCLUSIVE_DUE_TO_EXECUTION_MODEL（执行模型已用 v3 复核，结论未变）
             RESEARCH_INFRASTRUCTURE_BLOCKED（v2/v3 均可复现，基础设施可用）
```

**边界（必须随引用）**：48 只沪深300 抽样股 ｜ 2017-01-01~2024-12-31 ｜ 日线 ｜ train150/OOS75/step75 ｜
三档费率 ｜ 主结果来自 v2（未模拟涨跌停）｜ v3 已复核且结论不变 ｜ 窗口级账户（非连续净值）。

**明确不能说的**：
- ❌「双均线在所有 A 股场景无效」（只测了单标的日线 + 48 只大市值）
- ❌「样本外已统计性证伪」（Q4 是窗口级配对，非独立样本检验；原 `R4_INCONCLUSIVE` 仍然有效）
- ❌「窗口收益中位数 = 可实现年化」
- ❌「具备实盘价值」

---

## 3. 测试

```bash
cd D:/DeepQuant power by txm/DeepQuant-main
PYTHONPATH="$PWD/backend/src" \
  C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe \
  -m pytest scripts/ma_cross_audit/tests -q
```

**结果：11 passed**（无 skip、无 xfail）。覆盖任务书 §九 全部 11 项：

| # | 行为 | 断言要点 |
|---|---|---|
| 1 | 相同输入可重复 | 两次回测 `final_equity` 与逐笔价格相同 |
| 2 | 窗口边界正确 | `train=150 / oos=75`、`train_end < oos_start`、步长 60~110 天 |
| 3 | 无未来函数 | 截断未来数据后历史信号**逐位不变** |
| 4 | 涨跌停不可成交 | 一字涨停日**无成交**，且订单记 `BLOCK_LIMIT_UP` |
| 5 | 成交价与日期一致 | `fill_date == T+1`；价格 = `round_money(open×(1+滑点))` 且 ≥ 开盘价 |
| 6 | 费用不重复 | 分项之和 = 总额；买入无印花；佣金/过户按成交额只计一次 |
| 7 | 基准同窗口 | `buy_and_hold` 的 start/end 与策略窗口一致；期末不平仓 |
| 8 | 配对按同窗逐条 | `excess_return == 策略 − 基准`，最大偏差 < 1e-9 |
| 9 | 连续账户恒等式 | 断言引擎**无** `initial_positions` 字段（记录"不支持"这一事实）+ 单次回测 `equity = cash + 市值` |
| 10 | 缺失行情不填充 | 账本日期集合 == 行情日期集合；有持仓且缺收盘 → `market_value is None`（不记 0） |
| 11 | 原产物不可覆盖 | 原 v2 哈希 == B20 记录值；mtime 早于审计开始；脚本无向仓库外写入 |

---

## 4. 全部交付物

| # | 文件 | 内容 |
|---|---|---|
| 1 | `reports/MA_CROSS_BASELINE_SNAPSHOT_2026-10-27.md` + 2 CSV | 执行前基线（git/哈希/数据清单） |
| 2 | `reports/MA_CROSS_ARTIFACT_RECOVERY.md` + `MA_CROSS_ARTIFACT_MANIFEST.csv` | P0-1 恢复（12 项，6 项验证） |
| 3 | `reports/MA_CROSS_V2_REPRODUCTION.md` + `MA_CROSS_V2_RECONCILIATION.csv` | P0-2 复现（2592/2592） |
| 4 | `reports/MA_CROSS_V2_WINDOW_RESULTS.csv` | v2 逐窗 2,982 行 |
| 5 | `reports/MA_CROSS_V3_MAIN_RESULTS.md` + `MA_CROSS_V3_WINDOW_RESULTS.csv` | P0-3 v3 主结果 |
| 6 | `reports/MA_CROSS_V2_V3_COMPARISON.csv` | 288 行逐单元对比 |
| 7 | `reports/MA_CROSS_PAIRED_COMPARISON.md` + `MA_CROSS_PAIRED_WINDOW_RESULTS.csv` | P0-4 配对（5,964 行） |
| 8 | `reports/MA_CROSS_CONTINUOUS_ACCOUNT_GAP_ANALYSIS.md` | P0-5 连续账户差距 |
| 9 | `reports/MA_CROSS_UNIVERSE_BIAS_AUDIT.md` | P1 池偏差 |
| 10 | `scripts/ma_cross_audit/p00~p03`、`run_designs.sh`、`tests/` | 全部可重跑 |
| 11 | `ma_cross_audit/` | 48 只输入、v2/v3 全量输出、`analysis_summary.json` |

**复现命令**：见 §3 与本报告 §5。

---

## 5. 复现步骤（端到端，含耗时）

```bash
cd D:/DeepQuant power by txm/DeepQuant-main
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

$PY -u scripts/ma_cross_audit/p00_baseline_snapshot.py      # 基线（秒级）
$PY -u scripts/ma_cross_audit/p02_artifact_recovery.py      # 恢复清单（秒级）
$PY -u scripts/ma_cross_audit/p01_prepare_workspace.py      # 固定池 + 复制 48 只（秒级）
bash   scripts/ma_cross_audit/run_designs.sh                # v2+v3，各约 25 分钟（实测 10:02 / 10:21 完成）
$PY -u scripts/ma_cross_audit/p03_analyze.py                # 对账+对比+配对（秒级）
```

> 全程**离线**（不调 akshare），数据由恢复件哈希固定 → 比"再抓一次"更可复现。

---

## 6. 未解决问题（如实披露）

| # | 项 | 状态 | 建议 |
|---|---|---|---|
| 1 | 连续账户（真实净值/回撤） | **未实现** | 按 P0-5 的 S1~S4 四步做，属核心改动需授权 |
| 2 | 历史成分股快照 | **无数据** | 采购或改用全 A 池（`data/exec_all` 5,472 只，含退市）作新实验 |
| 3 | `design_version` 硬编码 `oos75-v3` | **缺陷** | 按 `price_limit_mode` 分组，或修字段（需授权） |
| 4 | 停牌未显式建模 | **缺陷** | S3：标注 HALT + 顺延记录 |
| 5 | 涨跌停不按日期切换（创业板 2020-08-24） | **缺陷** | P6：`MarketRules` 支持按期切换 |
| 6 | 窗口/股票相关性 | 部分缓解 | 若要正式推断，需时间-股票双向聚类或块 bootstrap |
| 7 | `b50_v3` 命名误导 | **已登记** | 建议重命名或加 README（未动，属既有产物） |

---

## 7. 结论与建议

**研究结论**：在「单标的 · 日线 · 价格类技术信号 · 48 只沪深300 抽样 · 2017-2024 · train150/OOS75」这一设计空间内，
双均线**存在可确认的负相对边际**（配对中位 −2.93pp，CI [−5.45,−3.37]，8/8 年为负，剔极端不变，v3 修复不变），
且该损失**随交易频率单调放大**。

**处置建议**：**归档这条研究线**，但顺序是——

1. 先做两件**便宜且能提高结论可信度**的事（各约 1 人日）：
   a. 用 `data/exec_all`（含退市）重跑一次同规则，检验"池偏差是否改变结论方向"；
   b. 按 P0-5 的 S1+S2 实现连续账户，出**唯一一条**真实净值曲线（而非窗口拼接）。
2. 若 (a) 仍为负、(b) 的连续净值仍跑输买持 → **正式归档**，并在结题文件写明"重启需满足 P1~P7"。
3. **不做**：继续在 15 组参数网格里搜索、换窗口长度、换费率档"找好看的数字"。

**不再主张**：`EVIDENCE_INSUFFICIENT_FOR_CAPITAL` 的总体判定不因本报告改变——本报告只把"双均线在该设计空间内的负边际"从**弱证据**升级为**可复核的确认**，不构成任何实盘建议。
