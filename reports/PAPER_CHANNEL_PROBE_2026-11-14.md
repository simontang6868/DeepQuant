# 仿真盘接入状态与「跑一次」实测（2026-11-14）

生成时间：2026-11-14 22:2x｜作者：DQ Agent｜性质：**只读连通性实测 + 就绪度核查**
用途：回答「接口不是给了吗，接上模拟盘跑跑试试」—— 说明已经接了什么、今天真的跑了什么、为什么账户里没有单。

## 1. 结论先行

**你说得对，接口 11-08 就给了，而且早就接上了**：云端 `DQ_GM_TOKEN` / `DQ_GM_ACCOUNT_ID` 已配置（值不回显），`.venv-gm` + `gmtrade` 可用，`paper_account_adapter.py` 每个交易日 17:10 真实连接仿真账户产快照，影子对账每天算出计划单。

**今天我又实测跑了一次，接口是活的**（`--probe` rc=0、`--no-write` rc=0）。但账户 `cash=100000 / positions=[]` 一直没变，原因不是接口没通，而是：

- 适配器被**刻意设计成只读**（源码第 8 行「绝不发单」，无任何 `order_*`）；
- 报单授权守卫实判 **`DENY｜NO_AUTH｜六项 0/6`**（`paper_authorization.json` 在云端命中 **0**）；
- **11-30 之前没有可执行目标**：当前 5 只来自 `LEGACY_NO_D4` 老台账，不是含 `target_holds_d4` 的 FORWARD 台账；
- **U-16（12-01 演练放行）未给**；演练驱动 / 成交回报对账器按方案 §6 **刻意未实现**（避免「管道写了却没人用」的假接入）。

⇒ 现在能跑、也确实在跑的是**只读快照 + 影子对账**；要让仿真账户里出现委托，需要你本人开两道闸门并等到 11-30/12-01。这不是技术故障，是项目自己设的边界（`LIVE_ENABLED=false`、11-30 前不向任何账户报单）。

## 2. 今天的实测记录（云端，全部只读、零副作用）

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `paper_account_adapter.py --date 20261113 --probe`（带 env 凭证） | **rc=0**；Cash 字段齐全（`available/balance/frozen/nav/cum_commission…`）；Position 字段 `[]`；只打印字段名、未打印值、未写快照 |
| 2 | `paper_account_adapter.py --date 20261113 --no-write` | **rc=0**；`no-write（零副作用）；未发任何委托` |
| 3 | `paper_authorization_guard.py --check --file <正式路径> ...` | **`DENY｜原因 NO_AUTH｜六项 0/6 通过｜未发单`** |
| 4 | `find /opt/quant -name paper_authorization.json` | **0 个** |
| 5 | 读 11-13 快照 / 影子对账产物 | 见下节 |

⚠️ 首次不带 env 直接跑时适配器报「缺少环境变量 DQ_GM_TOKEN, DQ_GM_ACCOUNT_ID」—— 那是我调用方式没加载 `EnvironmentFile`，**不是凭证丢失**；按 systemd 同款方式注入后立即 rc=0。如实记录，不把这次失败算成接口故障。

## 3. 系统每天真的在「算单」，但一笔都没发

`forward/shadow_reconcile/20261113.json`（真实产物，非演示）：

```text
mode            = REAL_PAPER
n_orders        = 5
targets         = ['600990', '603977', '002012', '301329', '600191']
order_codes     = 同上 5 只
orders_sent     = False
n_unexplained   = 0
legacy_no_d4    = True
counts_toward_g2= False
note            = LEGACY_NO_D4：台账缺 d4 字段（11-30 前的老记录），本日不计入 G2
```

`forward/account_snapshot_20261113_rev1.json`：`source=gm-paper`、`available=100000.00`、`frozen=0`、`positions=[]`、`settlement_verified=unknown`。

⭐ 这 5 只是**老台账主口径 top5**，不是 11-30 起要用的 d4 严格口径（Phase 1.5 实测两者 top5 重合 0/5）。所以即便现在放行，「按哪个目标下单」本身也没有合法依据。

## 4. 为什么现在不能发（四道，缺一不可）

| 闸门 | 现状 | 谁解除 |
| --- | --- | --- |
| `paper_authorization.json` 存在且 `--check` = ALLOW | **缺**（0 个文件） | **你本人**创建（模板见 `docs/PAPER_AUTH_FILE_TEMPLATE_2026-11-09.md`） |
| `check_channel_isolation.py --remote` = FAIL 0 项 | 上次 PASS，需 12-01 前复核 | 我（只读核验） |
| 11-30 产出 `mode=FORWARD` 且含 `target_holds_d4` 台账 | **未到时点** | 时间 + 日更链 |
| U-16 书面放行「开始 12-01 演练」 | **未给** | **你本人**书面一句话 |

另外：U-15 书面声明（11-09，sha `3207bff41a58…`）第 ⑧ 条明确「**不构成 12-01 演练的执行许可**」；`docs/SIM_REBALANCE_DRILL_2026-12-01.md` §6 明确演练驱动/对账器尚未实现，授权后第一步才实现且须先冻结预登记。**我不代签、不代创建授权文件、不自行放行。**

## 5. 最短路径（按这个顺序，12-01 就能真跑）

1. **现在，你本人**在云主机创建 `/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json`（`deepquant:deepquant` / `600` / UTF-8 无 BOM / **LF**）。字段模板在 `docs/PAPER_AUTH_FILE_TEMPLATE_2026-11-09.md` §2；四个 `scopes` 是预登记冻结的，不能增删。
2. **算哈希并自检**（凭证与账户 ID 都不进对话）：
   ```bash
   F=/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json
   /opt/quant/.venv/bin/python /opt/quant/scripts/paper_authorization_guard.py --compute-hash --file "$F"
   # 把 64 位 hex 填回 sha256 字段，其余字节一个不动
   /opt/quant/.venv/bin/python /opt/quant/scripts/paper_authorization_guard.py --check --file "$F" \
     --action sim_rebalance_order --notional 4000 --cumulative 0 \
     --available 100000 --now "2026-12-01T09:35:00+08:00"
   # 期望 ALLOW｜六项 6/6 通过｜未发单
   ```
3. **11-30 之后**给 U-16 一句话（原文可直接复制）：
   > 前置条件已全部满足（U-15 授权文件存在且 `--remote` 通道隔离 PASS、11-30 已产出 `mode=FORWARD` 且含 `target_holds_d4` 的台账、11-27 月末 DRYRUN 完成、日历缺口已记录），我书面确认：**开始执行 12-01 仿真月度调仓演练**，范围以 `docs/SIM_REBALANCE_DRILL_2026-12-01.md` 为准；成交回报对账任一差异即停机；五项演练各至少一次。
4. **我收到 U-16 后**：先冻结预登记 → 实现演练驱动与成交回报对账器（含变异测试）→ 12-01 按台账在仿真账户调仓 → 外部真值对账 → 归档。**仿真成交与净值不得用于经济性判断**，演练不改变 G1/G2/S4。

### 如果你只是想「现在就看到账户动一下」

**你本人在掘金终端手动下一笔仿真单**（例如 4,000 元买入），然后告诉我一声；我这边可以只读拉快照、跑影子对账、核对成交回报与费用（顺便实测「不足 5 元按 5 元」）。这不涉及我代发单，也不触碰任何授权闸门，今天就能做。

## 6. 边界声明

本轮**全部只读**：云端查询 + 两次零副作用试跑 + 一次授权守卫实判；**未发任何委托、未创建/修改授权文件、未改运行代码与配置、未写正式台账、未回显任何凭证值**。`LIVE_ENABLED=false` 未变；11-30 前不向任何账户报单的边界未变。
