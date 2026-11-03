> **本文件是历史报告，不反映当前状态。** 当前状态见仓库根目录 `STATUS.md`（2026-11-03 04:14）。

# Phase 1.1 单一配置源：实现与验收

> 生成时间：2026-11-02 20:15（GMT+8）
> 任务书原文：「策略参数只在一处定义，回测、前瞻、实盘都从它读取。新增一致性测试：三处解析出的参数逐字段相等，不等则测试失败。」
> 验收标准先冻结在 `docs/PHASE1_ACCEPTANCE_CRITERIA_2026-11-02.md`，本文件只报结果。

## 1. 动手前的实际状况（与 Phase 0 的描述有出入，以实测为准）

Phase 0 记的是「`sp5_out/` 与 `forward/` 各有一份 `sp4_config_v1.json`」。**实测 `sp5_out/` 那份并不存在**，真源只有 `forward/sp4_config_v1.json` 一处。

但问题并没有因此消失，只是换了个形态：

| 问题 | 实测 |
| --- | --- |
| 配置里的阈值是**自然语言** | `"f3pct <= 0.70"`、`"amt20 全市场分位 < 20%"`、`"amt20 > 500 万"` —— **没有任何一处代码真的读它们** |
| 代码各自硬编码 | `p13_forward.py` 一套；`scripts/audit/audit_sp4_*.py` **6 个文件**各一套；`sp2_sell_signal.py`、`sp3_entry_rule.py`、`p02c/p03/p04/p04b/p11/p12/p13b/p15` 再加 **11 处**。共 **20 个文件、47 处硬编码常量** |
| 静默切换通道 | `KF = BASE/"sp5_out"/… if 存在 else FWD/…`。哪天 `sp5_out/` 出现副本，前瞻会悄悄改用那份，而 `--verify` 校验的仍是 `forward/sp4_config_v1.sha256` ⇒ **校验通过、用的却是另一份配置** |

⇒ 所以 1.1 的核心不是「合并两个文件」，而是**造一个唯一的解析层，并把「不许另写一份」变成可执行的测试**。

## 2. 做了什么

### 2.1 新增唯一解析层 `scripts/sp5/strategy_params.py`

- 从 `forward/sp4_config_v1.json` 读取，把自然语言阈值解析成数值：
  - `"f3pct <= 0.70"` → `thresh = 0.70`
  - `"amt20 全市场分位 < 20%"` → `liq_max = 0.20`
  - `"amt20 > 500 万"` → `amt_min = 5_000_000`
- **解析失败即抛 `ConfigError`，绝不回退默认值**。这是本模块最重要的一条：
  配置从 `f3pct <= 0.70` 改成别的写法而正则失配时，若静默用 0.70，参数漂移将**完全不可见**。
- 校验 `version == "sp4-v1"`；版本变了就报错（字段语义可能已不同）。
- 暴露 `config_sha256`，并核对 `forward/sp4_config_v1.sha256` 声明的哈希（实测一致：`d4dacb71…`）。

### 2.2 前瞻改为只从解析层取参数

`p13_forward.py` 的 `K / THRESH / LIQ_MAX / AMT_MIN / CLOSE_HI_CFG / 五项费率` 全部改成从 `StrategyParams` 取，**不再有字面量**。

`CLOSE_HI_MAIN = 200.0` 不再写死，而是取自 `KNOWN_DIVERGENCES["price_hi"]["code_value"]`——让「200 是一个已登记的偏差」这件事在代码里**看得见**，而不是一个孤立常量。

### 2.3 拆掉静默切换

```python
KF = FWD / "sp4_config_v1.json"          # 只认一处
if (BASE / "sp5_out" / "sp4_config_v1.json").exists():
    raise RuntimeError("发现重复的配置真源…")
```

## 3. 一致性测试（13 条，`scripts/sp5/tests/test_strategy_params.py`）

任务书要求「三处解析出的参数逐字段相等，不等则测试失败」。三处指**回测 / 前瞻 / 实盘**。实盘尚不存在，所以本阶段的「第三处」用**全部现存脚本的硬编码值**替代——这比只比三处更严。

| 组 | 测试 | 钉的是什么 |
| --- | --- | --- |
| A 解析层 | `test_parses_all_strategy_fields` / `_cost_fields` | 每个字段解出的值正确 |
| | `test_config_hash_matches_declared_sidecar` | 哈希与 `.sha256` 一致 |
| | `test_missing_field_raises` | 缺字段报错 |
| | **`test_unparseable_threshold_raises`** | 阈值改成「百分之七十」必须报错，不能静默用旧值 |
| | `test_version_mismatch_raises` / `test_missing_config_raises` | 版本/文件异常 |
| | `test_known_divergence_is_real` | 偏差表与实际不符必须失败（防它变成永久豁免） |
| B 漂移 | **`test_hardcoded_sites_match_baseline`** | 硬编码站点清单必须与基线**一字不差**，新增/删除都失败 |
| | **`test_no_hardcoded_drift`** | 每处硬编码的值必须等于配置值，否则须登记为「有意覆盖」 |
| | **`test_intentional_overrides_are_still_needed`** | 登记过的覆盖若已与配置一致，登记必须删除 |
| C 单一真源 | `test_no_duplicate_config_file` | `sp5_out/` 下不得出现副本 |
| | `test_p13_forward_constants_come_from_params` / `_reads_the_same_file` | 前瞻常量 == 解析值，且是同一物理文件 |
| | `test_p13b_constants_come_from_params` | 回填脚本同样 |

### 3.1 为什么用 AST 而不是 grep

grep 分不清三件事：`K = 5`（硬编码）、`K = _P.n_hold`（正确取法）、注释里提到 5。
AST 只看语法结构：右值是字面量才算硬编码，右值是表达式一律跳过——**这正是我们想要的方向**（写表达式就不会被判定为硬编码，写死就会被抓）。

### 3.2 「有意覆盖」的处理

`p04b_ledger_independent.py` 的 `SPREAD = 0.0` 与配置的 `spread_scenario = 0.001` 不同，但这是**设计意图**（独立台账只校验流程与分档，不叠加 0.10% 价差压力）。已登记在 `INTENTIONAL_OVERRIDES` 并写明理由。

配套的 `test_intentional_overrides_are_still_needed` 防止登记变成永久豁免：若哪天那处改成 0.001，登记必须删掉。

## 4. 行为不变的证明

参数重构最怕「值改了但没人注意」。所以改完后重跑同一决策日：

| 项 | 重构前 | 重构后 |
| --- | --- | --- |
| 主口径 pool（1-200） | 758 | **758** |
| 主口径 top5 | `603193, 301336, 301202, 000597, 002692` | **完全相同** |
| rand5 | `300389, 300447, 301584, 600125, 603022` | **完全相同** |
| 配置口径 pool（1-50） | 687 | **687** |
| 配置口径 top5 | `603135, 300946, 000715, 600713, 600055` | **完全相同** |

⇒ 只是把「值的来源」从硬编码换成了配置解析，**没有改变任何数值**。

## 5. 没改的部分与理由

`scripts/audit/audit_sp4_*.py`（6 个）与其余 11 个脚本**仍然硬编码**。

理由：它们是历史审计/研究脚本，其产物（Gate 1–6、L3z–L3af 等）已被引用。改它们的取值方式虽然值不变，但会让「当时的代码」与「现在的代码」产生差异，追溯时多一层不确定性。

替代方案：**测试守住值**。任何站点若与配置漂移，`test_no_hardcoded_drift` 立即失败；任何新增站点，`test_hardcoded_sites_match_baseline` 立即失败并要求作者做出选择（改用 import，或显式登记）。

⇒ 「只在一处定义」在本阶段落实为：**唯一解析层 + 其余处被强制校验**。这是可执行的，不依赖人记得。

## 6. 与 1.2 的接口

配置里的 `price_filter` 是 `[1.0, 50.0]`，而回测实现是 `between(1.0, 200.0)`（`scripts/build_panel.py:190`），任务书 D5 又要求 40。

这三个数**不是**「配置写错了」，而是三个不同来源（详见 `SP4_AMENDMENT_A1_价格上限_预登记.md` §1）：
研究预登记 200、前瞻配置 50（= 单只预算/100，按 25% 仓位）、实盘约束 40（同公式，20% 仓位）。

本阶段**没有**统一它们。`KNOWN_DIVERGENCES` 只登记、不消解；等 1.2 的 amendment 结果。

## 7. 测试结果

| 套件 | 结果 |
| --- | --- |
| `test_strategy_params.py` | **15 passed** |
| `scripts/sp5/tests` 全套 | **113 passed / 1 failed**（+15；唯一红仍是 T6 = `p04` 的 ffill，属回测侧未修） |

## 8. 遗留

1. 实盘侧（`BrokerAdapter`）尚不存在，「第三处」暂以现存脚本的硬编码代替。适配器落地时须同样从 `strategy_params` 取值，并加一条测试。
2. `sp4_config_v1.json` 的阈值仍是自然语言。长期应改成数值字段，但那要 amendment（冻结件），本阶段不做。
3. 20 处硬编码未改为 import（理由见 §5）。
