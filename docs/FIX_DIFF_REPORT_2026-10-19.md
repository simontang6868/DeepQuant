# P0/P1 修复前后输出差异对比（与 B 线对账）

> **任务 A-P5 / 交付物 #5 的一半**
> 执行时间：2026-10-19 02:40 ~ 03:05 GMT+8（系统时钟）
> 方法：**在修复后的代码上重算「修复后」，并用等价方式复刻「修复前」**，两边都实测；
> 再把结果与 B 线 `docs/RESEARCH_FIXES_REPORT.md` 的数字逐项对账。
> 边界：**不重跑 Gate 1 实验**（会覆盖 `gate1_out/`），因此涉及实验产物的项只能标注「不可比」。

---

## 0. 对账结论（先看表）

| 缺陷 | B 线报告的数字 | A 线独立实测 | 判定 |
|---|---|---|---|
| **P0-2** `price_jump` 计数 | 50 只池：**88,346 → 280**，**30/50** 只为纯误报 | 50 只池：**88,346 → 280**，**30/50** | ✅ **逐位一致** |
| **P0-1** `end_date` 被忽略 | 1000 交易日 / `end_date=2021-02-24`：**44 → 9** 窗，末窗 `2023-10-31 → 2021-02-23`，超界窗 **35 → 0** | 同索引同边界：**36 → 1** 窗，末窗 `2023-10-31 → 2021-02-23`，超界窗 **35 → 0** | ⚠️ **末窗与超界数一致，总窗数不同**（见 §2.2） |
| **P1-1** `UnboundLocalError` | 修前抛 `UnboundLocalError`，修后 `oos_success=False` | 由 `test_oos_strategy_construction_failure_returns_clean_result` 转正验证（XPASS→pass） | ✅ 行为一致 |
| **P1-2** `_num()` 布尔 | `v is not bool` 恒真 → 布尔被当 1.0 | 转正测试 `test_bool_metrics_are_rejected_not_coerced_to_one` 通过 | ✅ 行为一致 |
| **D1** 对照组输入退化 | 改为独立输入 + 各自指纹 | 代码已含 `inputs` 参数（`gate1_run.py:586,639`） | ⏸ **产物未重跑，无前/后可比** |
| **D2** 累计收益口径 | 拆 `*_full` / `*_eval` | 代码已含（`gate1_run.py:365-366,609-613`） | ⏸ **产物未重跑，无前/后可比** |

---

## 1. 「修复前」是怎么取得的（方法学，必须先说清）

代码已修好，不能回滚（红线：不改 `src`、不 git checkout）。因此「修复前」用**等价复刻**取得，
两边都可用同一份数据复核：

| 缺陷 | 等价关系 | 是否严格等价 |
|---|---|---|
| P0-1 | 修复前 = `end_date` 完全被忽略 ⇒ **等价于 `generate_windows(index, cfg with end_date=None)`** | ✅ 严格等价（旧码把 `end_idx` 恒置为 `len(index)`） |
| P0-2 | 旧码把 `df[["close"]]`（单列 DataFrame）传给 `pct_change` ⇒ 复刻 `pc = df[["close"]].pct_change().dropna(); len(pc[pc > thr])` | ✅ 数值可对照历史 JSON 验证（见 §3.1） |

**这不是"猜"**：§3.1 会证明复刻值恰好等于历史产物里记录的 `n_rows − 1`，
§2.1 会证明 P0-1 的两个端点日期与 A-P0 现场取证完全吻合。

---

## 2. P0-1 `generate_windows` 忽略 `end_date`

### 2.1 场景 B：项目实际口径（2018-01-01 ~ 2024-12-31，250/60/60）

| 项 | 修复前（等价） | 修复后 | 变化 |
|---|---:|---:|---|
| 窗口数 | **26** | **15** | −11 |
| 末窗 `oos_end` | **2024-12-06** | **2022-05-27** | 收紧 2.5 年 |
| `oos_end > end_date` 的窗口数 | **11** | **0** | 全部消除 |

修复后逐窗核对（15 个窗口全部 `valid=True`，且**每个** `oos_end ≤ 2022-06-30`）：

```
idx=0  train 2018-01-01~2018-12-14  oos 2018-12-17~2019-03-08  ok
...
idx=14 train 2021-03-22~2022-03-04  oos 2022-03-07~2022-05-27  ok   ← 末窗
```

> `end_date=2022-06-30` 是周五，末窗落在 **2022-05-27** 而不是 06-30 —— 因为窗口按 `step=60`
> 推进，不会为了贴边而截断一个不完整的 OOS。这是**正确**行为（闭区间语义 + 步长对齐）。

### 2.2 场景 A：与 B 线同索引同边界（1000 交易日，`end_date=2021-02-24`）

| 项 | B 线报告 | A 线实测（`250/50/20`） | 一致 |
|---|---:|---:|:--:|
| 修复前窗口数 | 44 | **36** | ❌ |
| 修复后窗口数 | 9 | **1** | ❌ |
| 修复前末窗 | 2023-10-31 | **2023-10-31** | ✅ |
| 修复后末窗 | 2021-02-23 | **2021-02-23** | ✅ |
| 修复前超界窗口 | **35** | **35** | ✅ |
| 修复后超界窗口 | 0 | **0** | ✅ |

**为什么总窗数不同**：B 线报告引用的 `fix_evidence/before.txt` / `after.txt`
**不在仓库里**（`find . -path "*fix_evidence*"` → 0 命中），窗口参数（`step` 尤其敏感）不可考。
A 线用自己的 `250/50/20` 复现，得到的是**同一组端点、同一超界数**。

**结论**：两边在**语义层**完全一致（末窗相同、超界数 35→0 相同）；
**计数层**的差异来自未公开的窗口参数，不是修复行为不同。

> 这也顺带暴露一个可改进点：B 线的证据脚本没入库，导致它的数字**无法被第三方复现**。
> 建议把 `fix_evidence/` 一并提交（这属 B 线文件，A 线只登记）。

---

## 3. P0-2 `price_jump` 计数

### 3.1 合成样本（最小复现）

| 样本 | 真实跳变 | 修复前 count | 修复后 count | 修复后 issue |
|---|---:|---:|---:|---|
| 100 行平盘 | 0 | **99** | **0** | 无（不再误报） |
| 100 行、1 处 +20% | 1 | **99** | **1** | 1 条，`count=1` |
| 100 行、3 处 ±20% | 3 | **99** | **3** | 1 条，`count=3` |

修复前 `99 = 100 − 1`，与历史产物里「`n_rows − 1`」的规律一致 —— 证明 §1 的复刻是准的。

### 3.2 六只标的（`data/step75`，7 年窗口，与历史 JSON 同窗口）

| 代码 | 行数 | 修复前 | 修复后 | 倍数 | `max` 修复前 | `max` 修复后 |
|---|---:|---:|---:|---:|---:|---:|
| 600519.SH | 1,699 | 1,698 | **1** | 1,698× | 18.14% | **18.14%** |
| 000001.SZ | 1,699 | 1,698 | **0** | ∞ | 12.81% | **12.81%** |
| 601318.SH | 1,699 | 1,698 | **0** | ∞ | 12.83% | **12.83%** |
| 300750.SZ | 1,594 | 1,593 | **25** | 63.7× | 3683.33% | **3683.33%** |
| 000858.SZ | 1,699 | 1,698 | **4** | 424.5× | 21.34% | **21.34%** |
| 600900.SH | 1,688 | 1,687 | **0** | ∞ | 8.79% | **8.79%** |
| **合计** | — | **10,072** | **30** | **335.7×** | — | **6/6 一致** |

**两个要点**：
1. **3 只标的的告警应整条消失**（真实 0 跳变）；
2. **`max` 一个都没变** —— 缺陷只污染 `count`，不污染幅度。

### 3.3 五十只池（与 B 线同一批数据，独立复算）

```bash
# 逐文件：旧实现复刻 vs 新实现
D:\DeepQuant power by txm\b50_v3\*.qfq.csv   （50 个文件，共 88,396 行）
```

| 指标 | B 线报告 | **A 线独立实测** | 一致 |
|---|---:|---:|:--:|
| 修复前合计 count | 88,346 | **88,346** | ✅ |
| 修复后合计 count | 280 | **280** | ✅ |
| 纯误报标的数 | 30 / 50 | **30 / 50** | ✅ |
| 行数合计 | — | 88,396（`88,396 − 50 = 88,346`） | ✅ 自洽 |

**逐位一致**。这是本次对账最硬的一条：两个执行者、两套脚本、同一结论。

---

## 4. P1 两条（测试层验证）

| 缺陷 | 修复前行为 | 修复后行为 | 验证用例 |
|---|---|---|---|
| P1-1 `run_walkforward_window` except 分支 | 访问未绑定局部变量 `strategy` → 抛 `UnboundLocalError` | 返回 `oos_success=False`，不抛 | `tests/walkforward/test_parameter_selection_coverage.py::TestWindowFailurePaths::test_oos_strategy_construction_failure_returns_clean_result`（原 xfail → 已转正） |
| P1-2 `_num()` 布尔判据 | `v is not bool` 恒真 → `True` 被转成 `1.0` | `not isinstance(v, bool)` → `None` | `tests/api/test_backtests_routes_contracts.py::test_bool_metrics_are_rejected_not_coerced_to_one`（原 xfail → 已转正） |

两条都是**先 xfail 固化 → 修复后 XPASS 报红 → 摘标记转正**，转正时**断言一条未改**。
因此「修复前会红、修复后绿」这件事有可执行的证据，不只是文字描述。

---

## 5. D1 / D2：代码已改，但**没有修复后的产物**

### 5.1 现状

| 缺陷 | 代码状态 | 产物状态 |
|---|---|---|
| D1 对照组输入退化 | ✅ `write_outputs(..., inputs=...)`（`scripts/gate1_run.py:586,639`） | ❌ 现有 `gate1_out/a027865f26ea237a/` 仍是**修复前**产物（两份 CSV `sha256` 相同） |
| D2 累计收益口径 | ✅ 拆为 `*_total_return_full` / `*_total_return_eval`（`365-366`、`609-613`） | ❌ 现有 `summary.json` 里**没有**这两个字段（`main` 只有 `mean_d/n/p_value/std_d/t_stat/win_ratio`） |

### 5.2 为什么不给「修复后」的数字

要得到修复后的 D1/D2 输出，必须**重跑 Gate 1 实验** → 会覆盖 `gate1_out/` 的两个原始 run
→ 直接违反「不覆盖未经确认的修改」与 A-P0 的冻结约束。

**所以这一项只能写"不可比"**。提供一个替代证据：修复后的字段契约已由测试固定
（`tests/research_fixes/test_ap1_control_input_separation.py` 4 条、
`test_ap1_return_field_separation.py` 4 条），一旦有人重跑，字段缺失或口径混用会直接报红。

---

## 6. 本次对账**没有**证明的事

| 项 | 说明 |
|---|---|
| 修复前后 **Gate 1 结论**是否变化 | **未验证**。产量未重跑，`judgement` 仍是修复前那次的结果 |
| P0-1 修复对历史研究窗口数的影响 | **未重算**。历史上各轮研究用的 window 数可能都偏多（`end_date` 失效），需逐轮重算才知道，属研究重做范畴 |
| B 线 `fix_evidence/` 的原始输出 | **不可得**，未入库 |
| 44 vs 36 的窗口参数差异 | **无法归因**，只能确认端点与超界数一致 |

---

## 7. 复现命令

```bash
PY="D:/DeepQuant power by txm/venv/Scripts/python.exe"
cd "D:/DeepQuant power by txm/DeepQuant-main/backend"

# P0-1：修复前 = end_date=None 的等价调用
PYTHONPATH=src "$PY" - <<'EOF'
import pandas as pd
from deepquant.walkforward import WalkForwardConfig, generate_windows
def stat(ws, end, tag):
    print(tag, len(ws), ws[-1].oos_end.date(), sum(w.oos_end > end for w in ws))
idx = pd.bdate_range("2018-01-01", "2024-12-31"); E = pd.Timestamp("2022-06-30")
c = dict(train_window=250, oos_window=60, step=60)
stat(generate_windows(idx, WalkForwardConfig(**c, end_date=None)), E, "before")
stat(generate_windows(idx, WalkForwardConfig(**c, end_date=E)),    E, "after ")
EOF

# P0-2：旧实现复刻 vs 新实现（50 只池）
PYTHONPATH=src "$PY" - <<'EOF'
import glob, pandas as pd
from deepquant.data.validation.checks_extended import validate_daily_extended
old = new = 0
for f in glob.glob(r"D:\DeepQuant power by txm\b50_v3\*.qfq.csv"):
    df = pd.read_csv(f); df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    pc = df[["close"]].pct_change().dropna()
    old += int(len(pc[pc > 0.15]))
    j = [i for i in validate_daily_extended(df, adjust_status="qfq").issues if i.code == "price_jump"]
    new += j[0].count if j else 0
print("old", old, "new", new)
EOF
```

---

## 8. 边界声明

1. **「修复前」是等价复刻，不是回滚运行**：§1 已论证等价性，且 §3.1 用历史产物的
   `n_rows − 1` 规律反证复刻正确。
2. **没有为对齐 B 线的 44/9 去调参数凑数**：A 线用自己的配置得到 36/1，
   并明确写出差异原因（B 线脚本未入库），**不伪造成一致**。
3. **D1/D2 不给数字**：没有产物就是没有，不拿代码推断冒充实测。
4. 本轮未改 `src`、未覆盖 `gate1_out/`、未 commit / push / PR。
