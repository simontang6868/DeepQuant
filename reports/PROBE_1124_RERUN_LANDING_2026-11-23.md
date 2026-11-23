# 补跑探针落地回执（PA-GM-PROBE-3B，2026-11-23 21:35 +08）

> 授权：账户持有人 2026-11-23 21:27「补跑探针，继续工作吧」。
> 前置：自审 `reports/SELF_AUDIT_2026-11-23.md` 发现 11-23 探针 GO 后无触发器、窗口已过。
> **这是新实验，不是 11-23 的续跑**：执行日/前收价/跌停价/产物路径全变；11-23 冻结件（`ccbc420d…`、`0fcb4c39…`）与脚本 `probe_min_order.py` 一字未改。

---

## 一、已落地（含文件路径）

| 项 | 文件 | 说明 |
|---|---|---|
| 11-24 新预登记 | `docs/PROBE_MIN_ORDER_PREREG_2026-11-24.md` + `.sha256` | 冻结 `b6aa6145…`；执行日 20261124、前收 9.150、跌停 8.23、名义 823 元 |
| 11-24 验收增补 | `docs/PROBE_MIN_ORDER_ACCEPTANCE_2026-11-24.md` + `.sha256` | 冻结 `c826fcaa…`；P/E/V/X 全项沿用 T+1 三段口径，日期/价格改 11-24 |
| 新脚本副本 | `scripts/probe_min_order_1124.py` | 独立文件，11-23 脚本零改动；双哈希启动校验、DATE_GATE=20261124 |
| 新测试 | `scripts/tests/test_probe_min_order_1124.py` | 16 条，含补跑专属 DATE_GATE 拒 11-23/11-25、8.23 取整、哈希锚 |
| 新变异 | `scripts/probe_min_order_1124_mutation_test.py` | M1 日期门 / M2 取整 / M3 哈希，3/3 检出 |
| D1 参数化 | `scripts/probe_automation.py` | `liquidate_probe_carry` 加 `day` / `min_order_out`，**默认仍 11-24 / 11-23 产物**（旧 timer 行为不变）；CLI 加 `--day` / `--min-order-out` |
| 补跑 D1 测试 | `scripts/tests/test_probe_automation.py` | +2 条：默认路径不变、11-25 参数可卖且 11-24 拒卖 |
| **真实触发器** | `deploy/systemd/deepquant-probe-min-order.service/.timer` | **OnCalendar 2026-11-24 09:35、Persistent=false** —— 11-23 缺的就是它 |
| 补跑 D1 触发器 | `deploy/systemd/deepquant-min-order-carry-1124.service/.timer` | OnCalendar 2026-11-25 09:36；比主买腿 09:35 晚 1 min，先 HALT 买腿再卖 carry |
| 依赖文档 | `deploy/TIMER_DEPENDENCY_POLICY.md` | 补两个 timer 的依赖行与"本次补的核心缺口"说明 |

## 二、已验证（含测试数 / 变异 / 实跑产物）

| 项 | 结果 |
|---|---|
| 11-24 补跑测试 | **16 passed** |
| 相关三套合计 | **54 passed**（含 11-23 原测试，确认零回归） |
| `scripts/tests` 全量 | **847 passed / 1 skipped** |
| 变异 | 补跑 3/3 + 施工单 3/3 = **6/6 全检出** |
| 执行机双哈希 | sha256sum 实测 = 侧车冻结值，逐字节一致 |
| 执行机 self-check | `PASS`，`limit_price_9.150=8.23`，四项检查全列 |
| 执行机 dryrun | `decision=DRYRUN`、`price=8.23`、`notional=823.0`、P-1 PASS、P-3 ROUND_FLOOR(0.01) ✅ |
| dryrun 产物 | `forward/probe_min_order_1124_dryrun.json`（LF，prereg=b6aa6145dd64） |
| systemd verify | 两个新 unit 无本次引入的告警（仅旧 sim-prep 的 Documentation URL 历史警告） |
| **timer 现场状态** | 两个均 `enabled=enabled active=active`；next = **11-24 09:35** / **11-25 09:36** |
| ExecStart 现场 | 三行前置块齐全（venv-gm / source env / cd /opt/quant），`--prev-close 9.150`，日志重定向 `/opt/quant/logs/` |
| 账户预检 | `CONNECT_OK`、nav/available=100000、`positions=[]`、`n_real_orders=0`、账户掩码匹配、SIM 通道 |
| **未发真单** | 全程只 self-check + dryrun + 只读连通性；**零报单**（与 11-23 一样，真单只在 timer 到点发） |

## 三、未做与原因（如实写）

| 项 | 状态 | 原因 |
|---|---|---|
| 真实买单 | 未发（将在 11-24 09:35 由 timer 发） | 现在是 11-23 21:35，非执行日；DATE_GATE 也会拒。这是设计内的等待，不是遗漏 |
| X-FILLED 真实卖出 | 未做（条件分支） | 仅当 11-24 探针意外成交才在 11-25 09:36 触发；跌停价买单预期不成交 |
| H2/H3/H4 | 未动 | [HUMAN] 本人栏位，执行方不代填 |
| GitHub 推送 | 未推 | 经典 ghp_ token 被 GitHub 拒用于 Git 操作；新 PAT 不在本会话环境（`github-pat` 键消失）。**本地提交已落，远端待凭据恢复补推** |

## 四、11-23 → 11-24 的因果闭环

11-23 探针"GO 后无触发器"→ 自审查出 → 本人授权补跑 → 11-24 新预登记（不污染 11-23 冻结件）→ **新建真 timer 且现场 active** → 执行机 dryrun 全绿 → 等 11-24 09:35 真发单。
本轮回执不再出现"到点自动跑"这类没有触发器支撑的措辞——**有没有 timer，现场说了算**。
