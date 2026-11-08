# 仿真报单授权 · 一页方案（R6-1）

**状态**：只设计，**未授权、未创建任何文件**。`LIVE_ENABLED=false`，11-30 前不向任何账户报单。agent 不创建/修改/代签授权文件、不索取凭证。详细版：`PAPER_ORDER_AUTHORIZATION_PLAN_2026-11-08.docx`。

**一、`paper_authorization.json`（仅仿真）**
`schema_version="paper_auth_v1"`；**`channel` 只允许 `gm-paper`**；`account_ref` 为账户 ID 引用（非凭证）；`max_notional` ≤ 实时 `available`（每笔＋累计双校）；`not_after` 有效期；`scopes` 最小动作集；`issued_by`/`issued_at`/`statement_ref` 用户书面留痕；`sha256` 全文自校验。与 `live_authorization.json` **不同 schema / 文件 / 目录**。每次报单前重校六项，失败＝**停机**；文件缺失＝「无授权」，不是默认允许。

**二、三层隔离**
适配器：`PaperAccountAdapter`（只读、禁 `order_*`）vs `LiveBrokerAdapter`（**不同类/文件/包，Phase 3 闸门通过前不得部署到 `/opt/quant`**）。凭证：`DQ_GM_*` 于 `/opt/quant/env`(600) vs `DQ_LIVE_*` + 独立 `live.env`，禁止复用。配置：`paper_authorization.json` / `sp4_config_v2` vs `live_authorization.json` + 独立执行配置。真实通道凭证或适配器出现在生产目录 ⇒ 检查判 FAIL。

**三、自动化检查（已实现）**
`scripts/check_channel_isolation.py`（本地 / `--remote`）：查真实授权文件、禁用 env 前缀、`LIVE_ENABLED`、broker 白名单、只读适配器 `order_*(`；**采集失败/空输出判 FAIL 关闭**；只输出文件名与变量名、不输出凭证值。27 条测试 + 9/9 变异检出，本地与云端均 PASS。

**四、流程**
用户书面给出要素 → **本人**签发 → `--remote` 只读核验 → 用户再明确「开始 12-01 演练」才执行（每次报单前重校）→ 异常即停机＋告警＋留证。**本授权不解锁 `LIVE_ENABLED`、不改变四道门**；仿真与实盘是两份文件、两条校验路径、两个时间点。
