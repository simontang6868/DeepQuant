# PROBE→12-01 施工单落地回执（2026-11-22 21:45 CST）

> 施工单 v1 逐项落地结果。**只做 [AUTO]；[HUMAN] 四项只生成卡片与提醒，未代执行、未代填、未标完成。**
> 全程零真实订单；执行机只跑了只读快照与 unit 校验。

## 一、已落地（AUTO）

| 项 | 落地内容 | 现场证据 |
|---|---|---|
| A1 四个 timer | `deepquant-probe-connect`(11-25 09:30)、`probe-buy`(11-25 09:35)、`probe-sell`(11-26 10:00/14:00/14:55)、`sim-preflight`(11-30 18:00)；全部 `Persistent=false` | `systemctl list-timers` 显示四个 next 时点正确；均 `enabled + active` |
| A1 补充 timer | `deepquant-smoke`(11-24 17:40)、`deepquant-predict`(11-30 17:50)、`sim-prep`(11-27 17:20) 已 enable+start | next 时点分别为 11-24 17:40 / 11-30 17:50 / 11-27 17:20 |
| A2 前置命令块 | 所有新 service 的 `ExecStart` 写死 `cd /opt/quant`、`set -a; . /opt/quant/env; set +a`、`/opt/quant/.venv-gm/bin/python` | 施工单原文写 `/opt/quant/DeepQuant-main`，执行机**不存在**该路径；已按现场真实根 `/opt/quant` 落地 |
| A3 缺价硬门 | `probe_automation.buy-from-file` 从本人价格文件取价，缺任一 code ⇒ `MISSING_PRICE`、零报单、不建连、rc=4；仍调 `probe_executor.run_buy` 原硬门 | 单测 + M1 变异检出（去掉预检后真会下单） |
| A4 去污三次分类 | 三次时点严格映射；`GATE_NO_GO_MARKET` 必须 `source ∈ {exec_all, gmtrade_get_positions}`、`producer` 非去污脚本、`observed_at ≤ 2026-11-26T15:00+08:00`；否则一律 `GATE_FAIL_TECHNICAL`；两类字段/动作不同 | 单测（含迟到/自产/来源非法）+ M2 变异检出；非三时点 `SLOT_NOT_RECOGNIZED` rc=4 |
| A5 M-1201-PREDICT | `probe_automation.predict` 调既有 `--gates`（注入时点已是 12-01 09:35），六门全 PASS 才武装；否则 `POSTPONED` 不武装 | 单测 + M3 变异检出；**enable 不等于 active** 已修并写进代码注释 |
| **A5 武装命令原文** | `systemctl enable deepquant-sim-exec.timer` → `systemctl start deepquant-sim-exec.timer`；同两行用于 sentinel/reconcile 的 `.timer`。**全程不 start 任何 `.service`**，12-01 09:35 前不会发出订单 | `scripts/probe_automation.py` 的 `POST_CHAIN_TIMERS` 全为 `.timer` |
| A6 告警卡片 | P0/P1/P2 三张卡片落 `forward/alert_cards/`，含现象、字段、持仓快照命令、撤单命令、电话占位；告警服务 `deepquant-alert-p0/p1/p2.service` | 执行机 `gen-cards` 已跑，三张卡片存在 |
| §4 告警链 | 邮件（`notify_mail`，SMTP 已配）+ 企业微信 webhook（`DEEPQUANT_ALERT_WEBHOOK` 已配）；sim-exec/sentinel 失败 → P0，sim-reconcile 失败 → P1（drop-in `OnFailure`）；P0 启动 900 秒确认窗口，无 ack ⇒ disable **+ stop** 全部 `deepquant-sim-*.timer` | `systemd-analyze verify` 仅报系统里既有 snapd/ai-team 无关告警；新单元无告警 |
| H1–H4 卡片 | `docs/PROBE_HUMAN_CARDS_2026-11-22.md`，四项均标 ⬜ 未授权/未核对/未选/未触发 | 未代勾、未代填 |
| 定时提醒 | H1(11-23 09:31)、H2(11-24 09:00)、H3(11-25 09:33) 三条一次性提醒 | H4 事件驱动不定时 |
| 守门验证 | 13 条自动化测试全绿 + 3/3 真变异；`probe_automation.py snapshot` 执行机实跑 `SNAPSHOT_READONLY`、orders=[]、positions=[] | 零报单 |

## 二、待本人（HUMAN，四项，未动）

| # | 事项 | 时点 | 当前状态 | 本人动作 |
|---|---|---|---|---|
| H1 | 最小下单探针 GO | 11-23 开盘后 | ⬜ 未授权 | 回一句“GO 最小下单探针”；未 GO 则探针不跑 |
| H2 | 外部锚四字段勾选 | 11-24 任意 | ⬜ 四栏空白 | 逐行勾 `not_after`/`max_notional`/`scopes`/`channel` 是/否 |
| H3 | A 读价 / B 静态价 / C 第二人在线 | 11-25 09:34:59~09:40 | ⬜ A/B/C 均未选、第二人未安排 | 一次定完；选 A 则写 `forward/probe_price_20261125.json` |
| H4 | 异常响应 + 联系电话 | 仅告警触发 | ⬜ 未触发、联系电话空白 | 15 分钟内按卡片撤单/核持仓；填 `[HUMAN-FILL]` 两处 |

## 三、施工单与现场的冲突/限制（照实登记，不偷偷改判据）

1. **仓库路径**：A2 的 `/opt/quant/DeepQuant-main` 不存在；执行机仓库根是 `/opt/quant`。已按真实路径写 unit，否则 11-25 timer 必然 `cd` 失败。
2. **sim-preflight 时点**：A1 写“12-02 后 09:20”，§1 写“11-30 18:00 PREFLIGHT”。按 §1 落地（演练前才有意义）；**未建** 12-02 版。若确需 12-02 项，请明示，我另建，不动 11-30 那条。
3. **电话告警**：执行机只有 SMTP + 企业微信 webhook，没有语音/短信网关。P0 已发“邮件 + webhook + 卡片电话占位”，**“电话”这两字无法自动化**，仍属 H4；不会假装发出去了。
4. **enable ≠ active**：现场实测只 `enable` 后 `list-timers` 仍是 n/a，11-25 不会触发。所有新 timer 已 `enable + start`；A5 的武装也改成 `enable + start` 双步。
5. **sim-exec 仍 disabled**：按 A5，只有 11-30 六门全 PASS 才武装；今天不 enable，避免“预测还没跑就先开枪”。
6. **P0 误报可能**：若 H3 未选/价格文件缺失，probe-buy 会 `MISSING_PRICE` rc=4 并触发 P0 告警与 15 分钟锁定窗口——这是判据变严的代价，不放宽。

## 四、机制未变声明

门控顺序、落盘位置、幂等逻辑、**六门判据**均未改动；A2–A5 全部是外围编排（读价格文件、调既有函数、解析既有 `--gates` 输出、控制 timer 开关）。新增的只有：分类留痕字段、告警投递、timer 编排。判据方向：`GATE_FAIL_TECHNICAL` 优先于市场原因、无独立市场证据按技术失败 ⇒ **只严不严**。

## 四B、H1 验收标准已进脚本（21:48 追加）

- 验收增补 `docs/PROBE_MIN_ORDER_ACCEPTANCE_2026-11-22.md`（sha `df3d42fb…`）独立冻结；原预登记 `ccbc420d…` 保留不改写。脚本 `verify_prereg()` 同时校两个哈希，docstring 与产物 JSON 均含完整 P-1/P-2/P-3、E-1/E-2/E-3、V-1..V-4、X-1/X-2/X-3 原文。
- **P-1 事实校正**：`2026-11-21` 是周六非交易日；独立数据 `data/exec_all/601398.SH.exec.csv` 的上一交易日为 `11-20`（收盘 `9.400`）。脚本按周末过滤取 11-20，并在产物同时记录字面请求 `20261121` 与实际 `20261120` 及说明。
- **P-3 修正**：跌停价由 ROUND_HALF_UP 改为 **ROUND_FLOOR(0.01)**；`9.40×0.9=8.46`。修改原预登记参数需新预登记，故以增补冻结并保留原文件。
- **X-1**：意外成交不再按“失败”处理，而是 `X-FILLED`：立即市价反向卖出（用户 21:48 书面授权覆盖原“不自动卖出”），纳入 11-26 去污，`positions=0` 门标 SKIP 而非 FAIL。X-2/X-3 单独留痕，不合并。
- **边界写进文档**：探针 PASS 只说明权限/白名单/额度/下单/撤单链路通；**不推出 11-25 一定成**，正确表述是“11-25 失败只可能来自价格或市场”。
- 验证：13 条 H1 验收测试 + 2/2 新变异（M4 取整、M5 反向卖出）；A4 独立证据新增迟到/自产/非法来源三类测试。

## 四C、S0 期间的预检留痕（只登记，本次不做）

S0 跑 1~3 个月、U-16 月度调仓 ⇒ **每次调仓日 T-1 都要 preflight**。现建的 11-30 18:00 是一次性 timer，12-02 后的月度预检 timer **暂未建**，待 12-01 后按实际调仓日历补；不得写成“已有每日预检”。

## 五、提交与推送

- 本地提交：`1bdc4b5`（26 files changed）
- GitHub API 提交：`ca439b7d08161d96bb576b66e4a5668f2f82db75`（同树不同 sha，预期；git 直连仍 502，走 `api_push_report_v2.py` 推送并逐 sha 校验树一致）
