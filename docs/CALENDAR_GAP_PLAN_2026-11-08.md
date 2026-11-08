# 交易日历缺口预案（R6-4，2026-11-08）

**背景**：`forward/trade_calendar.json` 现止于 **20261231**（8,773 条，源 `sina:tool_trade_date_hist_sina`）。12-01 的月度刷新若拿不到 2027 年数据，**不得报错阻塞**；12 月的月末判定只依赖 2026 年。历来的铁律不变：**日历不可用 / 当月不完整 ⇒ 失败关闭**，本预案只是把「缺未来年份」与「当月不完整」分开。

## 一、已实现（`research/.../scripts/sp5/trade_calendar.py`）

| 函数 / 行为 | 作用 |
| --- | --- |
| `assert_window_covered(days, through_ym)` | 复用生产 `ensure_covered`，只要求覆盖到 `through_ym`（12-01 时 = `202612`）；**不看 2027** |
| `missing_months(days, from_ym, n=12)` | 列出未来 12 个月未覆盖的月份，只记录 |
| `write_gaps(...)` | 写 `forward/calendar_gaps.json`：`status` / `fetch_ts` / `calendar_last` / `required_ym` / `missing_months` / `source` / `note` |
| `--refresh` 写前校验 | 新日历缺**当月** ⇒ 不 `save`、沿用缓存；绝不把坏日历落盘 |
| 拉取失败 | 只要现有缓存覆盖当月 ⇒ 返回 **0**，`status=FETCH_FAILED_USING_CACHE`；缓存也不覆盖 ⇒ 返回 **1**（失败关闭） |

三种结果：`OK`（刷新成功、缺口记录）、`FETCH_FAILED_USING_CACHE`（沿用缓存）、`rc=1`（当月无法判定，写不进坏文件）。**缺 2027 永远只出现在 `missing_months` 里，不影响返回码。**

验证：7 条 pytest + 5/5 变异检出（`scripts/calendar_gap_mutation_test.py`）；变异专守「坏日历落盘」「缓存坏仍判成功」「拉取失败写成 OK」「缺口不记录」。

## 二、刷新排程

| 时点 | 来源 | 说明 |
| --- | --- | --- |
| 每月 1 日 09:00 | 既有 `deepquant-calendar.timer` | 12-01 会刷一次 |
| **12-15 09:05** | **新增** `/etc/cron.d/deepquant-calendar-retry` | 补刷 + 更新缺口台账 |
| **12-31 09:05** | 同上 | 月末决策日当天补刷；**刷完须重跑决策日预检 C1/C2**（日历 sha 会变） |
| 2027-01-01 09:00 | 既有 timer | 交易所通常 12 月公布次年休市安排，这是最可能补齐的一次 |

12 月的两个决策日（**11-30** 与 **12-31**）都只依赖 2026 年；2027 缺口不影响它们。

## 三、仍缺 2027 时：MANUAL 流程（**不得凭空生成**）

1. **两个独立来源**：① 国务院办公厅《关于 2027 年部分节假日安排的通知》；② 上交所 / 深交所 2027 年休市安排公告。**缺任一不得手工生成**。
2. **交叉核对**：两个来源分别录入交易日集合，逐日比对；冲突日单列，**不一致即失败关闭**，不靠猜。核对人、来源 URL/文件名、日期、差异清单写进证据记录。
3. **落盘标记**：用 `save(days, source="MANUAL:国务院放假安排+沪深交易所公告")`，并在 `calendar_gaps.json` 的 `source` / `note` 同步标注 `MANUAL`；`corrections.jsonl` 记一条。
4. **额外确认**：标 `MANUAL` 的日历在决策日预检里须人工复核，**不得自动当权威日历**；2027 第一次月末决策前由用户确认。
5. **agent 边界**：不代为编造交易日、不把 MANUAL 日历写成 `sina` 源、不静默覆盖原日历（保留副本 + sha）。

## 四、与其他闸门的关系

- `p13_forward.resolve_decision_day` 仍由**独立交易日历**推导月末，日历不可用一律失败关闭，不按星期几猜。
- 决策日预检 C1/C2 不变；**C11（R6-3）新增部署一致性检查**，`trade_calendar.py` 本身在 manifest 清单里 ⇒ 每次改动后必须重新 `--write` + scp + `--remote` 验证。
- 11-27 演练与 11-30 17:10 后须各跑一次 `trade_calendar.py --refresh` 的缺口检查项，并把 `calendar_gaps.json` 纳入当日证据包。
