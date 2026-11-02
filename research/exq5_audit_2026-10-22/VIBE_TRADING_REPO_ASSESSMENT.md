# Vibe-Trading（港大 HKUDS）参考价值评估

- 评估对象：资料库报告《Vibe-Trading 开源交易 Agent 调研报告》（docx，2026-10-23）+ 上游仓库 github.com/HKUDS/Vibe-Trading
- 仓库版本：`main @ ad04a0b8fe81d16c395f70a48275c656d23c3a53`（2026-10-23T03:50:17Z）
- 落盘参考件：`research/exq5_audit_2026-10-22/incoming/vibe_trading_ref/`（10 个文件，sha 见 §八）
- 证据分级：A=直接读源码验证；B=与自有独立实现交叉一致；C=二手文档；D=无来源
- 性质：外部参考评估，**不改变任何既有结论与数字**；不新增因子、不涉实盘

## 一、结论

有用，但价值不在策略侧，在**执行层口径**。报告本身零统计证据、零回测、零因子；真正可拿的是仓库里 pt-v1.1 模拟撮合引擎的**拒单词表、涨跌停/ST 溯源、费率默认值与 Decimal 金额纪律**。其中 cn_a 费率默认值与我们的 BASE 口径逐项一致，构成 §12/§13 的独立外部佐证（B 级）。

三项落地：
1. 前瞻验证协议直接采用其 11 项拒单码 + 固定决策顺序（P0）
2. 费率/最低佣金改用 Decimal 金额路径（P1）
3. ST ±5% 涨跌停用表驱动补齐，关掉 E3 成本因素清单里的「ST 5% 漏判」（P1）

三项不拿：Alpha Zoo 478 因子（违反「不新增因子」）、券商实盘接入（违反「不启动实盘」）、29 数据源回退链与 Swarm（研究层用本地存档，不是瓶颈）。

## 二、报告本身的可信度

- 对项目定位的判断准确（研究基础设施而非执行平台），风险提示（无第三方实盘验证、0.1.x 版本、官方从未发币）与官方口径一致。抽查 README、pt-v1.1 契约、`rules.py`/`limits_cn_a.py`/`china_a.py` 源码，工程性陈述属实（A/B 级）。
- 数字来源混杂：35,900 Star 引自小红书笔记（D 级）；3,324 commits、v0.1.18、113 工具/82 MCP 可核实（A 级）。报告整体为「工程结论 B、传播数据 D」。
- 报告不提供任何统计证据或回测，**不能用于任何策略判断**；其唯一价值是工程范式。

## 三、逐项对照（报告建议 vs 我们的现状）

| 报告建议 | 我们的现状 | 判定 | 动作 |
|---|---|---|---|
| Run Card（证据链+指标+caveats） | 预登记冻结 sha256 + 产物目录 + `StatisticalVerdict` + `MaturityLevel`（跑前冻结、跑后校验，比 run card 更严格） | 已有且更强 | 报告模板补「不可计算项」小节 |
| Fail-closed（算不出就说算不出） | 「没检出≠没问题」铁律 + 变异测试 + MDE/功效必报 | 同源已有 | 采纳「不可计算→显式 null，不得填近似值」为报告规则 |
| 外部依赖降级链 | 推送通道已有 git→API 双路；数据层为本地存档（exec_all/qfq_all） | 部分已有 | 不新增 |
| 能力契约化（113 工具 schema） | 研究脚本 + 单测 + 变异测试 | 不适用 | — |
| 影子复盘（解析成交日志反推规则） | 无真实成交日志；已有反事实分解（容量分解、剔极端月） | 不适用 | 等交割单到位（§16 待办 2） |
| 安全边界显式化 | 离线研究，无对外服务 | 不适用 | — |
| 闸门式验证（回测→MC→WF→限额模拟盘） | 已有 bootstrap/滚动窗/NW 剖面；模拟盘是前瞻协议缺的那一环 | 可拿 | 写进 `EXQ5_FORWARD_VALIDATION_PROTOCOL.md` |
| 数据质量前置 | 存档指纹 + ADJ 自算 + 送转以 `IMPL_PLAN_PROFILE` 为准 | 已有且更强 | — |
| Swarm 投委会 | A/B 分工已停，单人独立完成 | 不适用 | — |

## 四、值得拿的东西（代码级）

### 4.1 拒单词表 + 固定决策顺序（P0，写进前瞻协议）

`agent/src/tools/paper_trade/rules.py::REJECTION_ORDER`（11 项，先命中先返回）：

```
unknown_symbol → non_trading_day → suspended → corporate_action_unresolved
→ lot_size → limit_up → limit_down → limit_not_marketable
→ t_plus_1 → insufficient_position → insufficient_cash
```

对我们的意义：
- 阶段 I 只记录「卖出不可成交 1.93%（10,296/533,733）」，是一个**混合桶**；按这套词表可拆成 `t_plus_1` / `limit_down` / `suspended` / `lot_size` 四类，归因才可做。
- **顺序即口径**：卖出跌停股先命中 `limit_down` 而非 `t_plus_1`，归因时不能混。
- 涨跌停 band 只对**可能命中的一侧**判定（买对 up_limit、卖对 down_limit）；band 为 `None` 时**绝不伪造拒单**（`band_rejection` 返回 None）——这正是我们「没检出≠没问题」的镜像。

### 4.2 cn_a 费率默认值与我们的 BASE 逐项一致（B 级佐证）

`agent/src/tools/paper_trade/state_hash.py::_DEFAULT_FEE_MODELS["cn_a"]`：

| 项目 | 他们的值 | 我们的 BASE（§12） |
|---|---:|---:|
| commission_rate（双边） | 0.00025（万2.5） | 0.025% 双边 |
| commission_min（每笔） | 5.0 元 | 5 元（阶段 O CONSERVATIVE） |
| stamp_tax_sell | 0.0005（万5，仅卖出） | 0.05% 仅卖出 |
| transfer_fee（双边） | 0.00001（万0.1） | 0.001% 双边 |

等价对称单边 0.051%——与 §12 完全一致。这是第二个独立来源（第一个是东财官方 PDF，§13），提高 §12 的证据等级，**不改变任何数字**。

### 4.3 Decimal 金额纪律 + JCS 状态哈希（P1）

- `rules.py`/`limits_cn_a.py` 的钱一律 `Decimal(str(x))` + `ROUND_HALF_UP` + quantize 2dp，明令禁止 float 与 Python 内置 `round`（banker's rounding：2.675→2.67）。注释原文：*"never the builtin rounding function, never float arithmetic on money"*。
- 我们的 `cost_of()` 是 float 路径。对 0.051% 量级无影响，但**最低佣金 5 元档是硬边界**：1000万/1,937 只 → 单笔 5,162 元 → 佣金 1.29 元 < 5 元，全部触底。此时舍入方式产生 0.01 元/笔的系统性偏差。
- 状态哈希：`sha256(JCS(输入))`，且警告文本**禁止含时间戳**（同输入必须同字节）。我们已有产物哈希，前瞻台账可加「输入状态哈希」以便复跑比对。

### 4.4 ST ±5% 的关闭方式（P1）

`limits_cn_a.py` 三段式：表优先（`stk_limit` 当日涨跌停价）→ 启发式（主板 10% / 创业板科创板 20% / 北交所 30%，ST 覆盖为 5%）→ `limits:unavailable`（fail-closed，不伪造 band）。

我们 E3 成本因素清单里「ST 5% 漏判」正是这个缺口：`_price_limit` 只看代码段认不出 ST，必须用 `stk_st_daily` 表补 5%。落地：前瞻协议规定 ST 状态取自日度表（或直接排除 ST），**不允许用代码段猜**。

## 五、不拿的东西及理由

1. **Alpha Zoo 478 因子**：违反铁律「不新增因子」；其因子定义与 MAX5/REV21 未必同口径，作为独立 oracle 的价值低于我们已做的 numpy 独立重实现（A 级）。若未来做定义级交叉，只作只读对照。
2. **券商接入/实盘**：违反「不启动实盘」；其官方定位亦为研究工具、默认只读。
3. **29 数据源回退链、Swarm 多 Agent**：研究层用本地存档，瓶颈在数据质量不在可用性；Swarm 不解决单人研究的方法论问题。

## 六、一个需要说清的边界：V1 不需要改

仓库里最容易误读的是 `china_a.py::_blocked_by_limit` 的注释：旧实现用**当日收盘价**判涨跌停是 lookahead，改为「pre_close 推 band + 对比实际成交价（开盘+滑点）」。

但这成立的前提是**按开盘价成交**。我们的 V1 按**收盘价建仓**，`flags_fast` 用 `ret = close/prev_close − 1` 判涨跌停、`o==h==lo==c` 判一字板，与成交价对齐，**内部自洽，不需要改**。

真正对应这条原则的是我们已量化的 **T+1 缺口**：冻结口径按 t 日收盘成交，实际最早 t+1 开盘（月均 −0.0108pp）。这条写进前瞻协议，而不是回改 V1。

## 七、落地清单

| 优先级 | 事项 | 落点 | 触发 |
|---|---|---|---|
| P0 | 拒单码表 + 决策顺序 + provenance 字段 | `EXQ5_FORWARD_VALIDATION_PROTOCOL.md` | 本审计交付 |
| P1 | Decimal 金额路径 / 最低佣金边界 | 前瞻台账实现 | 前瞻启动时 |
| P1 | ST 5% 表驱动 | 前瞻协议（或排除 ST） | 同上 |
| P2 | 输入状态哈希（JCS） | 前瞻台账 | 同上 |
| — | Run Card 的 caveats 字段 | 结题报告模板 | 下一次报告起 |

## 八、参考件与校验

仓库 `HKUDS/Vibe-Trading`，`main @ ad04a0b8`（2026-10-23）。落盘 `incoming/vibe_trading_ref/`：

| 文件 | 字节 | blob sha 前 12 |
|---|---:|---|
| schema/tool_contracts/paper_trade_step-v1.1.json | 11,502 | 78b83b9c3a89 |
| agent/src/tools/paper_trade/README.md | 5,221 | f79741c15f21 |
| agent/src/tools/paper_trade/rules.py | 11,021 | 45e4fde0d947 |
| agent/src/tools/paper_trade/limits_cn_a.py | 9,893 | a2111b1af46c |
| agent/src/tools/paper_trade/engine.py | 26,201 | 7620c6089290 |
| agent/src/tools/paper_trade/state_hash.py | 8,029 | 22c68f7ef066 |
| agent/src/tools/paper_trade/calendar.py | 12,074 | 70e6956571aa |
| agent/src/tools/paper_trade/corporate_actions.py | 15,205 | e93e9a146fea |
| agent/backtest/engines/china_a.py | 7,938 | d1963a2472f4 |
| agent/backtest/run_card.py | 23,750 | 1aa03fc796c5 |

分级说明：§4.2 费率一致为 **B**（自有独立推导 + 外部实现交叉）；§4.1/4.3/4.4 为 **A**（直接读源码）；报告传播数字为 **D**（小红书）。本评估为补充说明，不占用本审计 8 个交付件名额，供最终报告附录引用。
