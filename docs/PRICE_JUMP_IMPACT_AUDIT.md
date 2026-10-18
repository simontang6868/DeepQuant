# `price_jump` 计数缺陷：影响面审计（四分类）

> **交付物 #2 / 任务 A-P2**
> 审计对象：缺陷 **P0-2** —— `validate_daily_extended` 中 `price_jump` 的 `count` 恒等于「行数 − 1」，
> 且零跳变也会报警。
> 审计时间：2026-10-19 01:45 ~ 02:20 GMT+8（系统时钟）
> 审计方式：**全仓库静态检索 + 用修复后实现逐标的重算 + 产物逐字节核对**，不依赖记忆、不引用未复现的数字。
> 审计边界：**只出分类与证据，不改任何 B 线代码 / 文档 / 数据**。

---

## 0. 一句话结论

**这条缺陷污染的是「文字」，不是「数字」。**

- 全仓库**唯一**的 `validate_daily_extended` 生产调用点是 `data/step75/research.py:306`；
- `gate1_out/` 12 个文件里 `jump` 命中数 **0**；`scripts/gate1_run.py` 引用数 **0**；
- `results[]` 的 36 条 walk-forward 结果**不含**任何跳变字段，门禁只看 `is_valid`（= 无 ERROR），
  而 `price_jump` 是 **WARNING**，**结构上不可能**影响任何标的的去留；
- 受影响的只有 **6 条 WARNING 文案里的计数**（合计 10,072 → 真实 **30**）与 **9 处引用该计数的文档表述**。

因此：**没有任何一条收益结论需要重跑**；需要改的是「说错了的话」。

---

## 1. 缺陷回顾（只写与本审计相关的部分）

| 项 | 内容 |
|---|---|
| 位置 | `backend/src/deepquant/data/validation/checks_extended.py:73`（调用方），`_check_price_jumps` 本身无错 |
| 根因 | `df_sorted[["close"]]` 取到**单列 DataFrame**；`pct_change[mask]` 在 DataFrame 上走「同形 + NaN 填充」语义，`len()` 退化为行数 |
| 症状 1 | `count` = 行数 − 1，与真实跳变数无关 |
| 症状 2 | **零跳变也报警**（掩码后 `len()` 仍 > 0） |
| 修复 | 改单层方括号取 Series；`_check_price_jumps` 入口对非 Series `raise TypeError` |
| 修复提交 | B 线，2026-10-19 01:24~01:33（见 `docs/RESEARCH_FIXES_REPORT.md` §2） |
| 回归守卫 | `backend/tests/research_fixes/test_ap1_price_jump_counting.py`（9 条）+ 原 5 条 xfail 已转正 |

**关键区分（本审计的核心判据）**：缺陷污染的是 `count`；`max`（最大跳变幅度）走的是**未过滤序列**的
最大值，**没有被污染**。下文字段级证据会证明这一点 —— 它决定了「哪些结论可以留、哪些必须改」。

---

## 2. 证据一：`count` 是算术恒等式，不是测量值

历史产物 `data/step75/step75_research_summary.json`（`run_at = 2026-10-16T09:32:33Z`，
`data_source = akshare.stock_zh_a_hist_tx`）里，6 只标的的计数与行数关系如下：

| 代码 | `n_rows` | 历史 `count` | `n_rows − 1` | 相等 |
|---|---:|---:|---:|:--:|
| 600519.SH | 1,699 | 1,698 | 1,698 | ✅ |
| 000001.SZ | 1,699 | 1,698 | 1,698 | ✅ |
| 601318.SH | 1,699 | 1,698 | 1,698 | ✅ |
| 300750.SZ | 1,594 | 1,593 | 1,593 | ✅ |
| 000858.SZ | 1,699 | 1,698 | 1,698 | ✅ |
| 600900.SH | 1,688 | 1,687 | 1,687 | ✅ |
| **合计** | — | **10,072** | — | **6/6** |

6 个样本、3 种不同的行数，全部**逐位相等**。这不可能是巧合：
它证明这 6 个数字全部是缺陷的**输出**，与价格内容无关。

---

## 3. 证据二：用修复后实现重算（窗口逐一对齐）

重算口径刻意与历史一致：读同一份 CSV → 数值化 → 按 `date` 排序 → **过滤 `date >= 2018-01-01`**。
过滤后 6 只标的的行数与历史 `n_rows` **完全一致**（1699 / 1699 / 1699 / 1594 / 1699 / 1688），
说明比对的是同一个时间窗口。

| 代码 | 历史 `count` | **真实 `count`** | 虚增倍数 | 历史 `max` | 重算 `max` | max 一致 |
|---|---:|---:|---:|---:|---:|:--:|
| 600519.SH 贵州茅台 | 1,698 | **1** | 1,698× | 18.14% | 18.14% | ✅ |
| 000001.SZ 平安银行 | 1,698 | **0** | ∞（误报） | 12.81% | 12.81% | ✅ |
| 601318.SH 中国平安 | 1,698 | **0** | ∞（误报） | 12.83% | 12.83% | ✅ |
| 300750.SZ 宁德时代 | 1,593 | **25** | 63.7× | 3683.33% | 3683.33% | ✅ |
| 000858.SZ 五粮液 | 1,698 | **4** | 424.5× | 21.34% | 21.34% | ✅ |
| 600900.SH 长江电力 | 1,687 | **0** | ∞（误报） | 8.79% | 8.79% | ✅ |
| **合计** | **10,072** | **30** | **335.7×** | — | — | **6/6** |

三条可直接引用的结论：

1. **6 只标的里有 3 只根本不该报警**（000001 / 601318 / 600900 真实跳变数为 0）——
   它们的「最大日涨幅」分别只有 12.81% / 12.83% / 8.79%，**全部低于 15% 阈值**。
   历史报告据此说它们「有 1,698 个跳变」，是纯粹的假警报。
2. **`max` 完全未失真**：6/6 逐位一致。这也反证了「`max` 取自未过滤序列」——
   否则 000001 的 `max` 只会在>15%的值里取，而它根本没有这样的值。
3. 宁德时代真实 25 次跳变（占 1,594 个交易日的 **1.6%**），不是「每天都在跳」。

---

## 4. 证据三：影响链的边界（为什么「收益结论不用重跑」）

### 4.1 静态检索结果

| 检索项 | 命令 | 结果 |
|---|---|---|
| 生产代码调用 `validate_daily_extended` | `grep -rn validate_daily_extended --include=*.py .` | **仅 1 个文件**：`data/step75/research.py`（第 20 行 import、第 306 行调用）；其余命中全在 `backend/tests/**` |
| `scripts/gate1_run.py` 是否引用 | `grep -n "validate_daily\|checks_extended\|jump" scripts/gate1_run.py` | **0 命中** |
| `gate1_out/` 是否含跳变记录 | `grep -rn "jump\|validate_daily\|warnings" gate1_out/` | **0 命中**（12 个文件全部检查） |
| 下游脚本是否消费 `warnings` | `grep -n warnings scripts/b15_summarize.py scripts/expansion_report.py scripts/b20_window_benchmark.py` | **0 命中**（只读 `stocks[].ok` 与 `results[]`） |
| 产品链（api / backtest / frontend） | 同上全仓库检索 | **无调用点** |

### 4.2 门禁逻辑：`price_jump` 结构上进不了筛选

```python
# backend/src/deepquant/data/validation/checks.py:44
def is_valid(self) -> bool:
    """不存在 ERROR 级问题即视为通过（WARNING 可被清洗）。"""
    return not any(i.level == ERROR for i in self.issues)
```

`price_jump` 的级别是 `WARNING`（`checks_extended.py:199-204`）。
`research.py:313` 用 `data_ok = report_ext.is_valid` 做闸门 → **WARNING 永远不参与拦截**。

因此：
- **6 只标的全部进入了 walk-forward**（`results[]` 共 36 条 = 6 标的 × 2 策略 × 3 费率档，
  即 `ma_cross` / `mean_reversion` × `simulation_placeholder` / `broker_reference_common_2024` / `research_scenario_high_cost`），
  没有一只因为「跳变多」被剔除；
- 宁德时代被拦是**后续版本**（`research.py` 于 2026-10-18 15:54 加入 `is_valid` 门禁）才发生的事，
  依据是 `invalid_price`（**ERROR**，2 行 qfq 负价），**不是** `price_jump`；
- 历史 JSON 里 `data_ok` 字段为 `null`（该字段当时尚未存在），也不构成任何筛选。

### 4.3 结论

**收益、窗口数、参数分布、风险指标全部与 `count` 无关** —— 它们既没读该字段，也没受该字段影响的筛选。
唯一被污染的是「数据质量警告文本」，而该文本的读者是**人**，不是程序。

---

## 5. 四分类清单

分类口径先写明，避免各说各话：

| 类别 | 判定标准 |
|---|---|
| **不受影响** | 该处不依赖 `count`（或根本不含该字段），且已实测确认 |
| **需重算** | 该处把 `count` 当作测量值或推导前提，**数字本身必须替换** |
| **需补说明** | 结论成立且不依赖 `count`，但正文引用了错误数字 / 错误归因，需加注或改写措辞 |
| **证据不足不能确认** | 当前仓库无法复现（数据缺失 / 版本不可追），既不能确认也不能否认 |

### 5.1 不受影响（8 项）

| # | 对象 | 位置 | 依据 |
|---|---|---|---|
| A1 | `gate1_out/**` 全部 12 个文件（含两个 run 的 `summary.json` / `monthly_paired*.csv` / `GATE1_RESULT_REPORT.md`） | `gate1_out/` | `grep -rn "jump\|validate_daily\|warnings" gate1_out/` → **0 命中**；Gate 1 从不调用校验器 |
| A2 | `scripts/gate1_run.py` 全部逻辑 | 整个文件 | 对 `validate_daily` / `checks_extended` / `jump` 的引用数为 **0** |
| A3 | step75 的 36 条 walk-forward 结果 | `step75_research_summary.json → results[]` | 字段清单为 `symbol/strategy/n_windows/positive_ratio/median_ret/median_mdd/total_trades/total_fees/param_dist/...`，**无任何跳变字段** |
| A4 | 标的去留判定 | `research.py:313,331,344` | 门禁 = `is_valid` = 无 ERROR；`price_jump` 是 WARNING，**结构上无法触发拦截** |
| A5 | 所有「最大跳变 X%」的表述 | STEP_7.5:260-265、STEP_7.4:209 等 | `max` 取自未过滤序列，重算 **6/6 逐位一致**（见 §3） |
| A6 | `docs/B15_8Y_30GROUPS_2026-10-18.md` 的实验结论 | 全文 | 该文 §「每只标的都带一条 price_jump warning」明确写「**本文不将其作为跳变频率使用**」；结论建立在收益/窗口指标上 |
| A7 | `docs/B17_CLOSURE_REPORT_2026-10-18.md` / `docs/B18_RESEARCH_VERDICT_2026-10-18.md` 的结论 | B17:92、B18:477 | 两份都明确「**不使用** count 做频率推断」，并把缺陷登记为 D5 |
| A8 | 产品链：API / 回测引擎 / 前端 / `ingest_daily_csv` | `backend/src/**`、`frontend/**` | 全仓库唯一生产调用点是 `data/step75/research.py`；产品链**零调用** |

### 5.2 需重算（4 项）

| # | 对象 | 位置 | 现状 | 应改为 |
|---|---|---|---|---|
| B1 | 6 条 `price_jump` WARNING 的计数 | `data/step75/step75_research_summary.json:16,32,48,64,80,96` | 1,698 / 1,698 / 1,698 / 1,593 / 1,698 / 1,687（合计 **10,072**） | **1 / 0 / 0 / 25 / 4 / 0**（合计 **30**）；其中 3 条**应整条消失**（无跳变） |
| B2 | 「1594 行里有 1593 次跳变，等于每天都在跳变」 | `docs/EXTERNAL_VALIDITY_2026-10-17.md:251,254` | 以 count 为前提推出「每天都在跳变，极其刺眼」 | 真实 **25 次 / 1,594 个交易日 = 1.6%**；该句的**论证方向作废**，但「宁德数据坏」的结论由 2 行非正价独立支撑，不受影响 |
| B3 | 「所有股票都有 1698+ 个 price_jump > 15%」 | `STEP_7.5_DELIVERY_REPORT.md:269` | 6/6 都是 1687~1698 | 真实值 0~25；**6 只里 3 只为 0**；「不是真的每天都跳」这半句结论正确，数字全错 |
| B4 | 「存在 725 天的相对变化 > 15%」 | `STEP_7.4_DELIVERY_REPORT.md:209` | 725（≈ 该样本行数 − 1） | 具体真值见 **D2**（对应数据集已不在仓库）；但可确定「725」= 行数 − 1 |

> **B1 的处置建议**：`summary.json` 属历史产物，**不建议原地改写**（会破坏「当时就是这样」的可追溯性）。
> 推荐两种做法之一：① 用修复后实现重跑一份新文件（新文件名 + 新 `run_at`），保留旧文件；
> ② 旧文件不动，在 `docs/` 侧登记更正表（本文件即该表）。**A 线不擅自改 `data/**`。**

### 5.3 需补说明（8 项）

| # | 对象 | 位置 | 要补什么 |
|---|---|---|---|
| C1 | 宁德被拦的「佐证」 | `docs/DATA_GATE_FINDINGS_2026-10-17.md:83` | 结论（宁德被拦正确）**成立**，但理由里的「1,593/1,594 行跳变」是缺陷产物。应改为：**真正阻断依据是 2 行 `invalid_price`（ERROR）；真实跳变 25 次，不是 1,593** |
| C2 | 30 组研究的「自记」引用 | `docs/B15_8Y_30GROUPS_2026-10-18.md:42,44` | 该文已声明不用 count，但 1,593 仍以「原研究自记」出现。建议并列真值（25），避免读者二次引用错数 |
| C3 | 泸州老窖的数字对 | `docs/EXPANSION_2026-10-17.md:123` | 「报 1942，真实 41」：**1942 已确认是缺陷产物**（= 1,943 行 − 1）；「41」见 **D1**，需标注「未复现」 |
| C4 | 归因错误 | `STEP_7.4_DELIVERY_REPORT.md:209`、`STEP_7.5_DELIVERY_REPORT.md:269` | 两处都写「**pandas 3.0 `.pct_change()` 的边界问题**」。**结论（数字不可信）对，归因错**：真因是 `df[["close"]]` 双层方括号取到 DataFrame。归因错了会让人以为升级 pandas 就能好 |
| C5 | 自相矛盾的复现描述 | `docs/B17_CLOSURE_REPORT_2026-10-18.md:90` | 「100 行 3 处跳变，真实 5 次，count 报 99」——「3 处」与「5 次」互相打架。可复现样本是「3 处 → 3」（见 `test_ap1_price_jump_counting.py`），需核对样本定义 |
| C6 | 代码注释沿用错数 | `data/step75/research.py:95` | 注释「它 2018-06 上市初期 2 行 qfq 负价，且原研究 JSON 自记『1593/1594 行跳变』」——后半句应删或标注为缺陷产物 |
| C7 | 待办范围 | `docs/STAGE_REPORT_FOR_REVIEW_2026-10-19.md:161,229` | 「任何引用过 price_jump 计数的历史结论都需重核」已由本文件执行完毕，建议回链本文件、把该项标记为 done |
| C8 | 缺陷描述性文档（A 线自有） | `docs/TEST_REPORT_2026-10-18.md:355-357`、`docs/KNOWN_ISSUES_SOFTWARE_2026-10-18.md:102-104`、`docs/BASELINE_SNAPSHOT_2026-10-19.md:345-372` | 这三处描述的是**缺陷本身**（不是引用其数字做结论），**无需修改**；已在文末互加指针到本审计 |

### 5.4 证据不足不能确认（4 项）

| # | 对象 | 为什么不能确认 |
|---|---|---|
| D1 | 「泸州老窖真实 41 次」 | 仓库内**没有** `600809.SH` 的任何数据文件（`find . -name "*600809*"` → 0 命中），无法重算。41 这个数字既不能确认也不能否认 |
| D2 | STEP_7.4 的「725 天」真值 | 该报告对应 2022-01-04~2024-12-31 的样本，而当前 `data/step75/*.qfq.csv` 最早只到 **2017-01-03**，无法重建同一窗口 |
| D3 | 历史 CSV 与当前 CSV 是否「同一份」 | 历史 JSON 里的 `stocks[].sha256` 与当前文件 sha256 **全部不一致**（例：600519 历史 `7e34d88c…` vs 当前 `25819b9a…`）。但过滤后行数 6/6 一致、`max` 6/6 一致 → 可判定**价格序列相同**，只是文件序列化格式变了；**逐字节同一性无法证明** |
| D4 | 30 组研究（B15）的逐标的 count | 该研究的 `b8_out/step75_research_summary.json` **不在仓库**（`find . -name step75_research_summary.json` 只有 `data/step75/` 一份）。只能依据「= 行数 − 1」推断，无法逐个重算 |

---

## 6. 需要谁动手

| 归属 | 事项 | 说明 |
|---|---|---|
| **B 线** | B2 / B3 / B4 / C1~C6 的正文修订 | 这些文档属 B 线所有权（`GATE1_*`、`B*_*`、`HISTORICAL_*`、`EXPANSION`、`STEP_7.*`、`data/**`）。A 线**不越界修改**，只登记 |
| **协调者** | 决定 B1 的处置方式（重跑新文件 vs 仅在 docs 侧更正） | 涉及是否覆盖历史产物，属判断类 |
| **A 线（本文件）** | 完成四分类与证据 | 已完成 |
| **无需任何人** | A1~A8 | 实测确认不受影响，不改 |

---

## 7. 复现命令

```bash
# 环境（任务书写的 dq_demo 没装 pytest / pandas，别用）
PY="D:/DeepQuant power by txm/venv/Scripts/python.exe"

# ① 影响面静态检索
cd "D:/DeepQuant power by txm/DeepQuant-main"
grep -rn "validate_daily_extended" --include="*.py" . | grep -v "backend/tests"
grep -rn "jump\|validate_daily\|warnings" gate1_out/          # 期望 0 命中

# ② 重算真实计数（窗口与历史对齐）
cd backend && PYTHONPATH=src "$PY" -c "
import pandas as pd, json, io
from pathlib import Path
from deepquant.data.validation.checks_extended import validate_daily_extended
ROOT = Path(r'D:/DeepQuant power by txm/DeepQuant-main/data/step75')
for code in ['600519.SH','000001.SZ','601318.SH','300750.SZ','000858.SZ','600900.SH']:
    df = pd.read_csv(ROOT/f'{code}.qfq.csv'); df['date']=pd.to_datetime(df['date'])
    for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df.sort_values('date').reset_index(drop=True)
    sub=df[df['date']>='2018-01-01'].reset_index(drop=True)
    rep=validate_daily_extended(sub, adjust_status='qfq')
    j=[i for i in rep.issues if i.code=='price_jump']
    print(code, len(sub), j[0].count if j else 0)
"

# ③ 回归守卫（缺陷不会悄悄回来）
cd backend && PYTHONPATH=src "$PY" -m pytest tests/research_fixes/test_ap1_price_jump_counting.py -q
```

---

## 8. 边界声明

1. **本审计不构成对任何收益结论的重新认定**：只回答「哪些文字引用了失真数字」，不回答「策略是否有效」。
2. **「不受影响」是实测结论，不是推测**：A1~A4、A8 均由静态检索或结构（WARNING 不进 `is_valid`）证明。
3. **不由 A 线修改 B 线文件**：§5.2 / §5.3 是**登记清单**，不是已执行的修改。
4. **D 类不猜**：数据不在仓库就是不在，不用「大概是」补齐。
5. 本文件不引用**任何**未经本次重算或静态检索确认的数字。
