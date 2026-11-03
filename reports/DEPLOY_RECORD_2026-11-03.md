# 云主机部署记录（2026-11-03）

> 机器：腾讯云轻量 `110.40.190.225`（TencentOS Server 4 / 2C / 1.9G 内存 / 50G 盘）
> 同机有既有服务 `media-downloader`（:8000）。本次部署**未触碰**该容器、docker 守护进程与 `/opt/vdl`。

## 1. 部署结果

| 项 | 值 |
| --- | --- |
| 仓库根 | `/opt/quant`（按交接文档约定，未用原计划的 /opt/deepquant） |
| 解释器 | `/opt/quant/.venv` = **Python 3.12.13**（EPOL 源安装；系统 3.11 不满足 numpy 2.5.4 的 ≥3.12 下限） |
| 关键库 | pandas 3.0.6 / numpy 2.5.4（与 requirements.txt 冻结版本一致） |
| 服务用户 | `deepquant`（系统用户）；`data/`、`var/`、`forward/`、研究 `data/out/sp5_out` 归其所有 |
| 定时器 | deepquant-daily（交易日 17:10，Persistent=true）/ deepquant-alert（17:40）/ deepquant-calendar（每月 1 日 09:00），均已 enable |
| 时区/校时 | Asia/Shanghai；chronyd active，NTP synced |
| 交换空间 | 新增 **2G swapfile**（/swapfile，已入 fstab）——内存仅 1.9G，防 OOM 挤死同机服务 |
| 登录 | 已安装 SSH 密钥 `deepquant-deploy-20261103`（ed25519）；**密码登录未禁用**（保留用户的兜底通道） |

## 2. 逐层校验结果（全部留档在主机）

1. **传输完整性**：code.tar.gz / data.tar.gz 上传前后 sha256 逐一比对一致。
2. **数据逐文件校验**：`migrate_data.py verify` → **一致 10,948｜缺失 0｜大小不符 0｜哈希不符 0｜多余 0 = PASS**（1.67 GB，覆盖 exec_all/qfq_all/forward，排除 universe_state 每日快照）。
3. **四源自检** `preflight_sources.py` → **PASS**（腾讯 K 线 116ms、新浪 K 线 96ms、腾讯快照 54ms、新浪日历 8797 个交易日）。
4. **到期判定** `is_due.py` → `NOT_DUE 20261030 台账已存在`（10 月台账是本地已产出的真实证据，随 forward/ 一起迁移，**不是补记**）。
5. **日更链** `daily_chain.sh --dry-run` → mode=DRY_RUN ok=1，五步全部可执行。

## 3. 部署过程中发现并修复的缺陷（均已加回归测试）

| 缺陷 | 后果（若不修） | 修复 | 测试 |
| --- | --- | --- | --- |
| `migrate_data.py` 把 `roots_rel` 写成源机**绝对路径** | 目标机校验 10,948 项全报"缺失" | 清单只写相对路径；verify 遇绝对路径**明确拒绝** | `scripts/tests/test_migrate_data_roots.py`（5 条） |
| `build_panel.py` import 期 `os.makedirs` 硬编码盘符路径 | is_due 一启动 PermissionError，整条链起不来 | 根路径 = `DEEPQUANT_HOME` 或由 `__file__` 推导 | `scripts/tests/test_no_hardcoded_roots.py`（10 条） |
| `fill_engine.py` 同样硬编码 | 同上 | 同上 | 同上 |
| bootstrap 只支持 apt/ufw | TencentOS 上不可用 | 增加 dnf/RHEL 分支、路径 `__REPO__` 参数化、firewalld/安全组分流 | 模板占位断言（同上文件） |
| requirements 未声明 Python 下限 | 装不上 numpy 2.5.4 时无从判断 | requirements 头部注明 ≥3.12；bootstrap 自动选/装解释器，**禁止**降级版本凑合 | — |

本地全量回归：backend **3571 passed / 1 skipped**（含新增板块权限 25 条 + 路径 10 条 + 清单 5 条），scripts/tests 与 property_checks 见当日报告。

## 4. 创业板权限（用户确认：无）

- 执行层新增 `deepquant.execution.board`：板块判定表 + `BoardPermissions`（**失败关闭**：未知板块名抛错、空配置=什么都不许买）。
- `PlanInput.permissions` 接入 planner：无权限标的记 skipped（带板块名与配置来源）、**不占预算、不补位**；卖出永不拦截；`permissions=None` 兼容旧行为但 `PlanResult.permission_unchecked=True`，Phase 3 发单入口必须过 `require_checked`。
- **对既有真实台账的影响（事实，非新实验）**：
  - 20260930 top5：603193 / **301336（创业板）** / **301202（创业板）** / 000597 / 002692 → 2/5 不可买；
  - 20261030 top5：600990 / 603977 / 002012 / **301329（创业板）** / 600191 → 1/5 不可买。
- ⚠️ **待用户决策（U-6）**：不开创业板权限 ⇒ 实盘组合将系统性缺 20~40% 仓位（被拦标的按规则留现金，不买替代票）；开权限 ⇒ 与历史回测口径一致。**在决定之前，这与 U-4（仓位）一样属于阻塞项。**

## 5. 机外备份 → 邮箱

- `deploy/bin/backup_offsite.sh` 新增**邮件通道**（优先级：EMAIL → RSYNC → RCLONE，取第一个已配置者；都未配置 → **失败退出**，实测 rc=69）。
- `deploy/bin/notify_mail.py`：stdlib SMTP_SSL；配置缺失 exit 2（错误信息不含配置值）；附件 >15 MB exit 3 **不截断**；授权码绝不进邮件头/正文/日志（6 条测试）。
- 证据归档实测 18 文件 / 32 KB——行情数据（1.6 GB）不入邮件：可由行情源重建，且邮件装不下。
- ⚠️ **待用户提供**：QQ 邮箱 SMTP **授权码**（设置→账户→开启 SMTP 服务后生成，不是 QQ 密码）。填入 `/opt/quant/env` 的 `DEEPQUANT_SMTP_*` 后 `--dry-run` 验证，再设 `DEEPQUANT_BACKUP_CMD` 挂进日更链。

## 6. 尚未完成 / 待办

| 项 | 状态 |
| --- | --- |
| 外部心跳（healthchecks.io 类） | 待用户提供 URL；未配置前 daily_chain 每天会打警告（有意为之） |
| QQ SMTP 授权码 | 待用户提供（见 §5） |
| 17:10 首次真实日更 | 2026-11-03 当天观察 `journalctl -u deepquant-daily` |
| 下一次决策窗口 | 2026-11-30（月末）；10-30 一期已由本地产出，机器不补记 |
| Phase 3 发单循环 | 未实现（2.2-c/d 调度、限速器接入、滑点校准）；`DRAFTED_NOT_SUBMITTABLE` 状态不变 |
| 创业板权限 / 仓位 25% vs 20% | U-6 / U-4，用户决策 |

---

## 附：第二轮（2026-11-03 上午，用户反馈后）

### U-2b 决策落地：实盘目标口径 = d4 严格（沪深主板）

- 用户决策原话：「这部分钱投入能投的股票」。落地方式：**采用预登记过的 d4 严格口径**
  （`p17_universe_filter.BOARD_D4_STRICT = {SH_MAIN, SZ_MAIN}`，同时排除创业板**和**科创板
  ——后者同样需要开户权限），而非临时发明「全池顺延补位」。
- 代码事实：`p13_forward.build_d4_targets` 已并行计算 `target_holds_d4` / `target_holds_d4_gem`，
  11-30 起台账自动记录，**无需改策略代码**。
- 执行层双保险（`execution/board.py`）：`BoardPermissions` 闸 + `substitute_targets`
  （名单若仍混入无权限板块 → 拦下记 skipped；可投不足 → **失败关闭**）。
  测试 31 条（含顺延 5 条），执行层合计 355 条全绿。
- **引用限定（必带）**：sp4-v1 回测池含创业板/科创板 ⇒ d4 口径组合的收益/回撤
  **没有回测背书**；要量化 d4 口径表现必须新预登记（换池=新实验）。
- **不追溯**：20261030 的 d4 名单不补算——当期板块/ST 快照缺失（universe_state 只有
  20261102），用两天后的快照近似是造假。11-30 决策时自然产出。

### 每日状态邮件（死信开关，替代 healthchecks.io）

- 用户表示不会配置外部心跳，选择邮件方案。实现：`heartbeat_alert.py` 扩展
  （`DEEPQUANT_MAIL_STATUS=1` + SMTP 配置齐全时，每天 17:40 发一封状态邮件，
  正常/告警都在标题）。死信逻辑：**当天没收到邮件 = 出了问题**。
- 「一切正常却发不出邮件」→ rc=5 暴露在 systemd（否则人会以为每天有信）。6 条测试。
- ⚠️ 它弱于外部监控（要靠人注意到没来信）；healthchecks.io 仍建议以后补上。

### SMTP 凭证：用户给的码无效

- 用户提供的「授权码」为 `ghp_` 前缀——那是 **GitHub Personal Access Token 的格式**，
  不是 QQ 邮箱 SMTP 授权码。本地与云主机各试一次，QQ 在认证前即断开连接
  （`SMTPServerDisconnected`）。已停止重试（防触发 QQ 安全限制）。
- 已提醒用户：若那是真实 GitHub 令牌，**立即去 GitHub 作废**（已出现在聊天记录里）。
- 正确获取路径：网页版 mail.qq.com → 设置 → 账号 →「POP3/IMAP/SMTP 服务」→
  开启 IMAP/SMTP → 手机短信验证 → 生成 **16 位小写字母授权码**。

---

## 附：第三轮（2026-11-03 09:47，SMTP 激活）

- 用户提供正确授权码（16 位小写字母）→ 云主机 `LOGIN OK 235`。
- `/opt/quant/env` 写入：`DEEPQUANT_SMTP_*`、`DEEPQUANT_MAIL_STATUS=1`、
  `DEEPQUANT_BACKUP_EMAIL`、`DEEPQUANT_BACKUP_CMD`（root:deepquant 640）。
- **实测三发全成功（rc=0）**：
  1. 通道验证邮件（无附件）；
  2. 证据备份归档（18 文件 / 32 KB 附件，backup_offsite.sh 以 deepquant 身份）；
  3. 状态邮件【DeepQuant 正常】（heartbeat_alert.py，DEEPQUANT_MAIL_STATUS=1）。
- 从此每天自动：日更链末尾发证据备份；17:40 发状态邮件（正常/告警）。
  **用户侧死信约定：交易日当天没收到状态邮件 = 出了问题。**
- 小修：backup_offsite.sh 开头显式 `cd "$REPO"`，消除从 /root 启动时 find 的
  无害警告（防误读为错误）。
- U-6 状态：邮件死信开关 ✅ 已激活；healthchecks.io 仍建议以后补（更強的整机宕机覆盖）。
