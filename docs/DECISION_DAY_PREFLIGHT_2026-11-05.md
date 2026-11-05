# 11-30 首个 FORWARD 决策日：只读前置预检（2026-11-05）

**结论**：静态前置 **10 项 PASS / 0 FAIL / 1 PENDING**。唯一未决项是「当日 universe_state 快照」，**必须在 11-30 17:10 之后重跑本预检**才能判定，不得提前当 PASS。
**性质**：只读。未写台账、未发单、未改配置、未跑重活（ADJ 重建留给当天链路）。
**复算**：`python scripts/preflight_decision_day.py`（8 条判据测试在 `scripts/tests/test_preflight_decision_day.py`）。

---

## 1. 为什么现在做这件事

11-30 是**唯一能产出真正前瞻记录的时刻**（当天收盘后跑 = `mode=FORWARD`）。错过要再等一个月。
而它的失败大多是**静默的**：链路报 `DECIDED`、台账也写了，但这期记录按 R3 不能计入证据。等到 12 月才发现就补不回来。

## 2. 预检结果（today=20261130，data_last 模拟为 20261130）

| # | 检查 | 结果 | 依据 |
| --- | --- | --- | --- |
| C1 | 日历自校验 | PASS | 8,797 条 / 1990-12-19~2026-12-31 / sha `39c1d23e9fbc`；直接调生产 `tcal.load` |
| C2 | 日历覆盖 202611 | PASS | `ensure_covered` 通过 |
| C3 | 决策日推导 | PASS | 调生产 `resolve_decision_day` ⇒ **20261130**，无异常，且是它自己那个月的最后一个交易日 |
| C4 | 数据末日覆盖 | PASS（模拟） | 11-30 当天须用**真实全池末日**重跑 |
| C5 | 目标台账未落盘 | PASS | `forward/ledger/20261130.json` 不存在 ⇒ 会 `DUE` |
| C6 | 定时器会触发 | PASS | 11-30 是**周一**且日历列为交易日；`OnCalendar=Mon..Fri 17:10` |
| C7 | 补跑窗口余量 | PASS | `LATE_RUN ≤3 天` / `决策窗口 ≤7 天`；当天 gap=0 |
| C8a | d4 字段写入路径 | PASS | `p13_forward.py` 含 `target_holds_d4` 赋值 |
| **C8b** | **快照时点** | **PENDING** | `universe_state/20261130.json` 当天由 p17 生成 |
| C9 | ADJ 重建 + 校验链 | PASS | 两脚本齐 + `--div-source merge`；任一失败 ⇒ `ABORT_QFQ_*` |
| C10 | 本地快照镜像 | PASS | 8 文件 `all_identical=True` |

**判据有效性已自证**（不是"全绿所以没问题"）：
`--data-last 20261127` ⇒ `MonthEndUnverified`、rc=1；把日历最后一个交易日改掉但不动 sha ⇒ `CalendarUnavailable`、rc=1；`--today 20261201` ⇒ 仍指向 11-30（补跑）、rc=0。

## 3. ⭐ 最危险的两个失败模式

1. **快照失败不中断链路**。`daily_chain.sh` 里 `p17 --fetch` 失败只告警，决策「沿用最近可用快照 ≤ 决策日」。
   ⇒ 台账仍会写出 `target_holds_d4`，但 `universe_state.asof` 是 11-27（甚至更早），**时点证据不新鲜**。
   **11-30 必查**：台账里 `universe_state.asof == 20261130`，否则这期只能标 `STALE_UNIVERSE`，不得计入 G1/L1 证据统计。
2. **数据末日没覆盖月末**。`is_due` 会 `ABORT`、当期不跑；若强跑到 12-01 补记，虽仍在 7 天窗口内，但已属**回溯**而非前瞻。
   ⇒ 11-30 17:10 后先看 `data_last_date()` 是否等于 20261130，**不要**用「看日历今天开市」代替。

其余三个：`ABORT_QFQ_VERIFY`（ADJ 重建校验不过，绝不替换、也不决策）、`NOT_DUE`（台账已存在，说明别处先写了）、周末/节假日 timer 不触发。

## 4. 11-30 当天运行单（17:10 之后）

```bash
# ① 本地/云端都跑一遍预检（当天快照已生成 ⇒ C8b 应为 PASS）
python scripts/preflight_decision_day.py --today 20261130 --data-last 20261130

# ② 看链路结果
ssh root@110.40.190.225 'journalctl -u deepquant-daily --since "2026-11-30 17:00" --no-pager | tail -40'

# ③ 逐项核对新台账（最关键）
#    mode == FORWARD、target_holds_d4 非空、universe_state.asof == 20261130、
#    code_sha 与 git 一致、counts_toward_observation == true
# ④ 快照镜像回拉 + 逐文件 sha256
python scripts/check_universe_mirror.py
```

任一项不符：**如实记 `MISSED` 或 `STALE_UNIVERSE`**，不要用补跑记录充数。

## 5. 本轮没有做的事

- 没有触发 ADJ 重建（1.67 GB 重活，留给 11-27 DRYRUN 与 11-30 正式链）。
- 没有提前写任何 11-30 台账或快照。
- 没有改任何策略参数、没有开 `LIVE_ENABLED`、没有联系券商。
