# 工程变更单 ECR-R8-2026-11-15（冻结于任何代码改动之前）

> 上游：`forward/authorization_statements/R8_statement_2026-11-15.md`（R8 原文逐字存证）
> 冻结 sha256：见本文件 `.sha256` 侧车；UTF-8 无 BOM、LF。
> 沿用任务书 v2.1、附录 A、R2–R7。本单**只界定范围与验收项**，不改写任何历史报告或冻结预登记原文。

---

## 0. 状态

| 项 | 值 |
| --- | --- |
| 编号 | ECR-R8-2026-11-15 |
| 冻结时间 | 2026-11-15（实施前） |
| 类型 | 工程变更 + 证据链修复 + 预登记设计（**不是**建模实验） |
| `StatisticalVerdict` | `ENGINEERING_SELF_CHECK_ONLY` |
| `MaturityLevel` | `DESIGN_VALIDATED_OFFLINE` |
| 预期产物目录 | `docs/` `scripts/` `reports/` `research/stock_pick_2026-10-25/forward/` |

## 1. 实施前实测基线（2026-11-15，只读）

| 项 | 实测 |
| --- | --- |
| D3 数据源 | 新机 `journalctl -u deepquant-daily` **无** `daily_chain 结束`／`[heartbeat]` 行 ⇒ 现脚本报 `0/10`；按日日志 `daily_20261112.log` / `daily_20261113.log` 有 `=== daily_chain 结束 mode=NOT_DUE ok=1 ===` |
| 新机产物 | `forward/heartbeat.json`（`ok/ts/stages/stage_note`）、`forward/stage_results.json`（`ok/ts/stages/stage_note`）；二者均为**单文件最新值**，无按日归档 |
| 交易日历 | 从 `20261112` 起 10 个交易日 = `…1120 / 1123 / 1124 / 1125` ⇒ **11-25 为第 10 个** |
| R1 更正台账 | 本地 **51 行**（sha `8fe34911…`）、云端 **17 行**（sha `f64b8311…`，停在 11-05）；`backup_offsite.sh` / `weekly_offsite.sh` **已含** `corrections.jsonl`，但云端源文件是旧版 |
| 每日状态邮件 | 正文由 `deploy/bin/heartbeat_alert.py` 的 `finish()` 拼接 |
| R2 logrotate | `/etc/logrotate.d/deepquant.bak.20261112` 重复定义 `/opt/quant/var/log/*.log` ⇒ 11-14、11-15 00:00 `logrotate.service` 连续 `status=1/FAILURE` |
| 日志归档 | `weekly_offsite.log` 已被 `/opt/quant/var/log/*.log` 通配符覆盖；`.log.1`／`.log-YYYYMMDD` 归档存在但**完全不在备份清单** |
| R5 git | 本地 HEAD `9f76f66`、origin `0dfdacc`，`ahead 3 / behind 0`（无分叉）；工作树 28 项（含 11-14 EARLY-IMPL-1 全部产物未入 git） |

## 2. 范围与硬边界（逐条对齐 R8 原文）

**范围内（授权执行）**：
1. `scripts/g1_progress.py` 修复 + 其测试/变异；`STATUS.md` 的 G1 判定日与时间戳。
2. 新增 `scripts/corrections_hashchain.py`；改 `deploy/bin/heartbeat_alert.py`（邮件正文追加链头）、`deploy/bin/backup_offsite.sh`（纳入链文件／`stage_results.json`／日志归档）、`deploy/bin/weekly_offsite.sh`（纳入链文件）。
3. 云端 `/etc/logrotate.d/deepquant.bak.*` **移动**（`mv`，保留原件）至 `/opt/quant/var/config-archive/`；验证 `logrotate.service`；日志轮转条目补充。
4. `git add` 11-14 及本轮全部产物 → `git diff --cached` 凭证扫描 → `git commit` → `git fetch origin` → `git push origin` → manifest 与 commit 对账。
5. 新增 `scripts/gm_paper_order_adapter.py`（**独立类，只实现 + 离线测试**）、其测试与真变异；探针授权模板。
6. 仿真执行门预登记**草案**（`DRAFT`，交用户确认）。
7. 交付记录与 2 页内报告；U-14 提醒。

**硬边界（一律不碰）**：
- `LIVE_ENABLED=false` 不变；11-30 前不改策略参数／`p13_forward.py`／`sp4_config_v*.json`。
- ⭐ **不创建、不修改、不读取内容**任何授权文件：`paper_authorization.json`、`live_authorization.json`。R8 与本项目既有纪律双重锁定。
- ⭐ **不向任何账户报单**；`--execute` 恒返回 `NOT_AUTHORIZED`；报单适配器不得含可执行的真实下单路径调用（SDK 下单函数仅作为**契约名**登记在类外部常量、离线测试用假 broker 注入）。
- 探针 `PROBE` 排除 G2／S4／一切经济性判断；11-30 前账户须回空仓（本单不执行任何交易，故该条为下游约束登记）。
- 旧台账 5 笔计划单含 `301329`（创业板）⇒ **不得**用作探针或演练标的。
- **不改 G2 定义**；不新增因子、不启动实盘、不合并两机、不事后调整 D。
- 11-27 DRYRUN 与 11-30 17:10 后 `preflight_decision_day.py --cloud` 保持不变。
- 不接 `daily_chain.sh` 的报单链路；不写正式 `forward/ledger/` 台账。
- 不回显凭证值；不 `curl` 心跳 ping 端点；`git push` 若 origin 断连／代理 502 ⇒ 如实报告、不强行绕、`fetch` 失败后**禁止** `reset --hard`。

## 3. 逐条验收项（判定必须来自产物或独立算术，闸门禁止调用被测函数）

| # | 验收项 | 判据 | 证据 |
| --- | --- | --- | --- |
| A1 | D3 起点 | `START=20261112` 硬编码；`--start < START` 启动即 FAIL（不得事后调整） | 单测 + 变异 |
| A2 | D3 数据源 | 读**新机**按日日志/归档 + `heartbeat.json` + `stage_results.json`；不合并旧机 | 单测 + 云端 `--remote` 实跑 |
| A3 | 红日重置 | 任一红日 ⇒ 连续计数归零重起，不跨红累加 | 单测 + 真变异（M：删重置 ⇒ 检出） |
| A4 | 判定日条件式 | 输出「若此后全绿，则 2026-11-25 收盘后判」；有红日则**不给新日期** | 单测 |
| A5 | STATUS 判定日 | `STATUS.md` 中 G1 判定日**只有 2026-11-25 一个**（旧 11-16 撤回记录移入本单／日志，历史原文不改） | 文本核对 |
| B1 | R1 同步 | 云端 `corrections.jsonl` 与本地**逐位一致**（51 行，sha `8fe34911…`）；同步前不用备份包证明完整性 | sha256 比对 |
| B2 | 哈希链 | 旁路文件逐行累计；`--verify` 独立复算 PASS；`--head` 输出链头；**不改写既有行** | 单测 + 变异 |
| B3 | 邮件链头 | `heartbeat_alert.py` 的正常/告警正文均含链头；链文件缺失 ⇒ 明确标注 `CHAIN_UNAVAILABLE`（失败关闭，不静默） | 单测 |
| B4 | 纳入备份 | `backup_offsite.sh` / `weekly_offsite.sh` 含链文件；`stage_results.json` 与日志归档 `.log.1` / `.log-YYYYMMDD` 进每日包 | 云端实跑备份 + `tar tzf` |
| C1 | logrotate | `.bak.*` 移出 `logrotate.d` 且原件保留；`logrotate.service` 重跑 `status=0` | `systemctl` + journal |
| C2 | 轮转覆盖 | `weekly_offsite.log` 在 `logrotate -d` 输出中被明确列出；配置不重复定义同一路径 | `logrotate -d` |
| D1 | git 凭证 | `git diff --cached` 扫描无 token/PAT/授权码/私钥字面量 | 扫描输出 |
| D2 | git 顺序 | 先 `fetch` 再 `push`；**无** `reset --hard`；提交后 manifest 与 commit 对账 | 命令序列 + 对账表 |
| E1 | 探针预登记 | **实现前**冻结 sha256；含目的/标的规则/2只各1手买次日卖/通过标准/PROBE 排除/11-30 空仓/301329 禁用 | `.sha256` 侧车 |
| E2 | 适配器 | 独立类；每次调用前经 `paper_authorization_guard.check` 判 `ALLOW`；幂等 `client_order_id`；失败关闭；禁市价大单 | 单测 |
| E3 | `--execute` | 恒 `NOT_AUTHORIZED`（rc≠0），无一条路径能发出真实委托 | 单测 + 变异 |
| E4 | 变异 | 每条 `DENY→ALLOW` 必须被检出（六项校验各一 + 幂等/市价/执行闸门） | 变异脚本 100% |
| E5 | 探针授权模板 | 交用户本人决定是否发出；明确「探针授权 ≠ U-16」 | 模板文件 |
| F1 | 执行门草案 | 含成交回报对账 + 五项演练 + 通过标准；`DRAFT` 状态；**不改 G2 定义** | 草案文件 |
| G1 | 报告长度 | 交付报告 ≤2 页；不再产长篇周报 | 报告文件 |
| G2 | STATUS 时间戳 | 头部「最后更新」与正文最新事件同步 | 文本核对 |
| G3 | 回归 | 串行四连 + 真变异 + manifest `--verify --remote` + 隔离 `--remote` | 命令输出 |

## 4. 实施顺序（固定，不得跳步）

1. 冻结 R8 原文存证（已完成）→ 冻结本变更单 → 冻结探针预登记（**在任何报单相关代码之前**）。
2. 本地实现 D3 → R1 → R2 备份/邮件脚本 → gm-paper 适配器 → 执行门草案。
3. 本地串行回归四连 + 真变异 + 自检；产物统一 LF / 无 BOM / sha256 侧车。
4. 云端：更正台账同步（**先于**任何「用备份包证明完整性」的表述）→ 建链 → scp 脚本 → logrotate 修复与验证 → 每日备份实跑 → 新增脚本云端自检。
5. git：暂存 → 凭证扫描 → commit → `fetch origin` → `push origin` → manifest 与 commit 对账。
6. STATUS.md 同步；交付 2 页内报告 + 工作日志 + U-14 提醒。

## 5. 失败与更正纪律

- 「没检出」≠「没问题」：任何 FAIL 先怀疑检查器，但**必须用独立实现去证**；不得靠放宽判据变绿。
- 变异必须**真改行为**；改检查器前先跑变异；变异脚本原地改源码期间禁止并发 pytest／提交。
- 期望值来自产物或独立算术（`Fraction`／手写），**禁止调用被测函数**推导期望值。
- 容差按产物存储精度定。
- 失败/缺陷：保留原始结果 + 出更正，**原文不改写**；预登记有缺陷也不改。
- 引用必带：预登记/变更单哈希 + 产物目录 + `StatisticalVerdict` + `MaturityLevel`。

## 6. 已知限制（如实登记，不掩盖）

- `heartbeat.json`／`stage_results.json` 是**单文件最新值**，新机无按日归档 ⇒ D3 对历史交易日的阶段级校验只能依赖按日日志；本单把 `stage_results.json` 纳入每日备份，仅为**今后**逐日阶段级判定打基础，不回填、不伪造历史文件。
- R8 预先声明 `D=2026-11-12` 为「首个全绿交易日」，本单**不复核该前提**、不事后调整；11-12/11-13 的全绿证据来自当日的 `heartbeat.json`（R5 证据卡已抄录）与按日日志。
- `--truth-probe` 只读真值源已接通，但**真实回报的 `exec_type` 与本地 `status` 编码对齐、5 元佣金实测**仍属探针目的，本单不预断结果。
- 云端 `/opt/quant` **不是 git 工作副本** ⇒ 改 `deploy/bin/` 运行脚本必须单独 scp 并云端实跑；push 只做异地备份。

## 7. 边界声明

本单在实施前冻结；§0–§7 原文**不改写**，实施记录以「§8 实施记录」追加。
`LIVE_ENABLED=false`、不报单、不创建/修改授权文件、不改策略参数、不新增因子、不启动实盘——全流程不变。
