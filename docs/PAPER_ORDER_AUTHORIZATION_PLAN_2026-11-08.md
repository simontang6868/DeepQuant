# 仿真报单授权方案（R6-1，只设计，**未授权、未创建任何文件**）

**2026-11-08**｜`DESIGN_ONLY`｜`LIVE_ENABLED=false`｜11-30 前不向任何账户报单
本文件是方案不是授权。**agent 不创建/修改/代签任何授权文件、不索取凭证、不自行报单**；用户书面授权前 R6-2 不执行。

## 一、`paper_authorization.json`（仅仿真通道）

字段：`schema_version="paper_auth_v1"`；**`channel` 枚举只允许 `gm-paper`**（出现 live/qmt/broker 即拒绝）；`account_ref` 是账户 ID 的**引用**而非凭证（凭证仍在独立 env）；`max_notional` >0 且 ≤ 实时 `get_cash().available`，**每笔与累计双重校验**；`not_after` 有效期，过期拒绝、不自动续期；`scopes` 为动作最小权限子集，未列动作一律拒绝；`issued_by`/`issued_at`/`statement_ref` 为用户书面授权留痕，`sha256` 全文自校验。**与 `live_authorization.json` 不同 schema、不同文件、不同目录**。

**每次报单前**重读并校验六项（schema/通道/有效期/scopes/金额上限/哈希）；失败＝**停机**，不重试、不降级。文件缺失或不可解析＝「无授权」，不是「默认允许」。

## 二、与真实通道的三层隔离

| 层 | 仿真 | 真实通道（Phase 3 闸门通过后） |
| --- | --- | --- |
| 适配器 | `PaperAccountAdapter`（只读，源码禁止 `order_*`） | `LiveBrokerAdapter`：**不同类/文件/包**；闸门通过前**不得部署到 `/opt/quant`** |
| 凭证 | `DQ_GM_*` 于 `/opt/quant/env`（600） | `DQ_LIVE_*` + 独立 `live.env`，**禁止复用** env |
| 配置 | `paper_authorization.json` / `sp4_config_v2` | `live_authorization.json` + 独立执行配置 |

另：两套适配器不共享下单实现；报单入口不得由「有没有凭证」推断通道；真实通道凭证或适配器出现在生产目录 ⇒ 检查判 FAIL；`LIVE_ENABLED` 与用户书面确认仍彼此独立。

## 三、自动化检查与授权流程

`scripts/check_channel_isolation.py`（本地 / `--remote`）：查真实授权文件、禁用 env 前缀、`LIVE_ENABLED`、broker 白名单、只读适配器 `order_*(`；**采集失败/空输出判 FAIL 关闭**，只输出文件名与变量名、不输出凭证值。**29 条测试 + 10/10 变异检出**（2026-11-09 修订后），本地与云端均 PASS。

用户书面给出要素 → **本人**签发 → `--remote` 只读核验 → 用户再明确「开始 12-01 演练」才执行（每次报单前重校）→ 异常即停机 + 告警 + 留证。**本授权不解锁 `LIVE_ENABLED`、不改变四道门**；仿真与实盘是两份文件、两条校验路径、两个时间点。

## 四、文件位置（2026-11-09 补；**更正**，原文不改写）

**唯一有效路径**：`/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json`（与所有 forward 证据同目录；属主 `deepquant`、权限 `600`）。真实通道授权另放 `/opt/quant/config/`，满足「不同目录」。

⚠️ **发现的缺陷**：R6-1 首版检查器查的是 `/opt/quant/forward/paper_authorization.json`，而部署根下**没有** `forward/` 目录（`find` 实测 MISSING）⇒ 用户把文件放对位置也会被判「不存在」。27 条测试全绿却没覆盖这条路径分叉，正是「测试全绿≠验收项覆盖」的又一例。2026-11-09 已改为本地/远端共用单一常量 `PAPER_AUTH_REL`，补 2 条真调 `collect_local` 的测试与 1 条真改行为的变异 M10（10/10 检出），并重新部署与复验。**本段为更正记录，前面的方案正文按原文保留。**
