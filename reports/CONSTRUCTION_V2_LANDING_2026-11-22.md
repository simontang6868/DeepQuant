# 施工单 v2 三栏回执（2026-11-22 23:59 CST）

**11-23 探针具备执行条件**（P0-2/P0-3 已落地）。**12-01 尚不具备执行条件**——`deepquant-sim-exec.timer` 仍 disabled，按设计须等 11-30 18:15 六门 ARMED；本回执不把“预测可跑”写成“12-01 已放行”。

---

## 一、已落地（文件路径 + commit）

| 项 | 落地内容 | 路径 |
|---|---|---|
| P0-1 时序重排 | `deepquant-predict.timer` OnCalendar **17:50 → 18:15**，PREFLIGHT 18:00 不变；15 min 缓冲写死在注释 | `deploy/systemd/deepquant-predict.timer` |
| P0-1 依赖规则 | 规则“下游时点 > 上游时点 + 缓冲（≥15 min，有观测上限取观测上限+15 min）”；14 个 timer 逐一核对表；重排前后对照 | `deploy/TIMER_DEPENDENCY_POLICY.md`、`deploy/README.md` |
| P0-1 MOCK 能力 | `probe_automation.py predict-mock`：假 DRYRUN/PREFLIGHT 验 E2，**不调用 systemctl**，产物标 `MOCK` | `scripts/probe_automation.py` |
| P0-2 T+1 分支 | X-FILLED-D0：11-23 只记录 `symbol/qty/fill_price/order_id/filled_at`，`probe_carry=true`，V-1=SKIP，**零卖单**；X-FILLED-D1：11-24 09:35 后卖出，失败保持 carry=true 并升 P0 | `scripts/probe_min_order.py`、`scripts/probe_automation.py` |
| P0-2 新 timer | `deepquant-probe-carry-liquidation.timer` 11-24 09:35，Persistent=false；service 无 carry 时 NO_CARRY rc=0 | `deploy/systemd/deepquant-probe-carry-liquidation.{service,timer}` |
| P0-3 隔离 | 持仓快照按 `PROBE_CARRY` / `NORMAL` 分列，含 `source`；11-25 09:30 preflight 硬门 `GATE_HALT_PROBE_CARRY`；`available` 未恢复 100000（±0.01）另报 `AVAILABLE_NOT_RESTORED` | `scripts/probe_automation.py` |
| P1-1 日历核对 | 脚本抽五类文件全部日期，与 `trade_calendar.json`（8797 个交易日，覆盖至 20261231）逐条比对；输出 JSON+MD；两个模板加固定检查行 | `scripts/trading_day_check.py`、`reports/TRADING_DAY_CHECK_2026-11-22.md`、`forward/trading_day_check_20261122.json`、`docs/PREREG_TEMPLATE_2026-11-10.md`、`docs/U16_WRITTEN_AUTHORIZATION_TEMPLATE_2026-11-15.md` |
| P1-2 H3 三模式 | A/B/C 互斥；默认未选定 ⇒ `NO_PRICE_MODE` 零报单 rc≠0；B 系数 `buy_factor=1.01`/`sell_factor=0.99` 为可配置常量；切换写 `set-price-mode` 审计（谁/何时/依据）；成本基线隔离句进预登记增补 | `scripts/probe_automation.py`、`docs/H3_PRICE_MODE_PREREG_2026-11-22.md` |
| 变更登记 | 验收增补重新冻结 `df3d42fb…` → `0fcb4c3922c0…`（T+1 修正），原预登记 `ccbc420d…` 未改写；执行机原 unit 备份 `.bak.20261122-v2` | `docs/PROBE_MIN_ORDER_ACCEPTANCE_2026-11-22.md(.sha256)`、执行机 `/etc/systemd/system/` |

### P0-1 全部 timer 依赖核对表（节选，完整版见 `deploy/TIMER_DEPENDENCY_POLICY.md`）

| timer | OnCalendar | 依赖上游 | 上游时点 | 满足 下游 > 上游 + 缓冲 |
|---|---|---|---|---|
| daily | Mon..Fri 17:10 | 外部行情 | — | OK |
| alert | Mon..Fri 17:40 | 日更心跳 | 17:10（6–10 min） | OK（30 min） |
| calendar | 每月 1 日 09:00 | 外部日历 | — | OK |
| smoke | 11-24 17:40 | 11-24 台账 | 17:10 | OK（观测 6–10 min；最坏 1h race 已登记） |
| probe-connect | 11-25 09:30 | env/授权/台账 | 前日 | OK |
| probe-buy | 11-25 09:35 | connect + 本人 09:34:59 价 | 09:30 | OK（价格仅 1 s 是有意最晚读价；缺价失败关闭） |
| carry-liquidation | 11-24 09:35 | D0 持仓（T+1） | 11-23 | OK（隔夜） |
| probe-sell | 11-26 10:00/14:00/14:55 | 11-25 买腿 | 11-25 09:35 | OK |
| sim-prep | 11-27 17:20 | 日更 mock 台账 | 17:10 | OK（10 min；最坏 race 已登记） |
| sim-preflight | 11-30 18:00 | 台账 17:10 + 旁证 17:40 | 17:40 | OK（20 min） |
| **predict** | **原 17:50 → 现 18:15** | PREFLIGHT 18:00 | 18:00 | **原不满足**（早 10 min ⇒ E2 必 FAIL）；现 OK（15 min） |
| sim-exec | 12-01 09:35 | predict ARMED | 11-30 18:15 | OK（≈15 h） |
| sim-sentinel | 12-01 09:45 | 执行结果文件 | 09:35 | OK（10 min；exec 最坏 30 min race 已登记） |
| sim-reconcile | 12-01 18:10 | 执行 + 结算门 | 09:35 | OK（8 h+） |

---

## 二、已验证（测试 / 变异 / 实跑产物）

- `scripts/tests` 全量 **825 passed / 1 skipped**；其中施工单 v2 新增测试 36 条（D0 不卖、D1 成功/失败、过早拒卖、标签分列、carry 硬门、MOCK E2、日历核对、H3 模式/审计）。
- 变异 **9/9 检出**：`probe_guardrail` 3/3（缺价/available/掩码）、`probe_automation` 3/3（缺价/市场证据/六门）、`probe_min_order_acceptance` **3/3：M4 取整、M5 D0 注入卖单、M6 D1 失败仍清 carry**。旧 M5 验“立即卖”已作废，现验“11-23 不卖、11-24 才卖”。
- 执行机实跑产物：
  - `forward/m1201_predict_MOCK_20261130.json`：`decision=MOCK_PASS`、`E2.ok=true`、`systemctl_calls_MUST_BE_EMPTY=[]`、`armed=[]`。
  - `forward/trading_day_check_20261122.json`：35 行、2 冲突（均是 P-1 字面 11-21 周六，同一语义两次引用）；`11-23/24/25/26/27/30/12-01` 逐个确认都是交易日。
  - `forward/probe_min_order_dryrun.json`：P-1 PASS、跌停价 8.46、零报单；`--self-check` PASS（新验收 sha `0fcb4c39…`）。
  - `systemctl list-timers`：predict next = `11-30 18:15`，carry-liquidation next = `11-24 09:35`，preflight next = `11-30 18:00`。
  - `systemd-analyze verify`：新单元无 fatal error（`deepquant-alert-p0.service` 原残留 `__REPO__` 占位导致 fatal，已现场修复并复验）。
- 额度影响：100 股 × 8.46 ≈ **846 元 = max_notional 的 0.85%**；若 11-24 卖出成功，11-25 09:30 前置自检要求 `available` 回到 100000（±0.01），否则写 `AVAILABLE_NOT_RESTORED` 并报告实际值，不放行。

---

## 三、未做与原因（如实写）

| 项 | 状态 | 原因 |
|---|---|---|
| H2 外部锚四字段勾选 | 未做 | [HUMAN] 本人栏位；执行方不代勾 |
| H3 A/B/C 选择 + B 系数确认 | 未做 | [HUMAN] 本人未定；默认 `NO_PRICE_MODE` HALT 已生效 |
| H3-B 静态价接入 11-26 卖腿 CLI | 未做 | 卖价 helper 已实现，但 `probe_executor --leg sell` 仍按外部 `--price` 取价；H3 未选定前接 B 价等于代选/改冻结 CLI 路径 |
| H4 联系电话两处 `[HUMAN-FILL]` | 未做 | [HUMAN] 本人填写 |
| 12-01 实盘演练放行 | 未做 | `sim-exec.timer` 保持 disabled；须 11-30 18:15 六门全 PASS 才由 A5 武装 |
| S0 月度调仓 T-1 preflight timer | 未做 | 12-02 后按实际调仓日历补；已在 `docs/PROBE_HUMAN_CARDS_2026-11-22.md` 留痕 |
| 11-23 真下单探针执行 | 未做 | 到点自动跑；H1 已 GO，但本回执生成时未到 11-23 |

---

## 四、提交与推送

- 本地提交：`TBD`
- GitHub API 提交：`TBD`（git 直连仍 502，走 `api_push_report_v2.py` 同树校验）
