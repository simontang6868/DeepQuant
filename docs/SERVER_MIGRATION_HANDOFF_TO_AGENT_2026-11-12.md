# 【DeepQuant｜迁移后遗留项协同处理单 v1｜2026-11-12】

> 发件：DeepQuant 执行 Agent（本轮只做只读核查，**未改任何生产文件**）
> 收件：负责服务器迁移的 Agent
> 用途：把核查发现的遗留项按「谁改什么」拆开，避免双方同时编辑同一脚本导致部署清单漂移、版本冲突。
> 依据：DeepQuant 仓库 `docs/SERVER_MIGRATION_VERIFICATION_2026-11-12.md`（8 节，含实测命令、证据与复算清单）。交接报告原文我方未改写，请你自行更新。
> 环境：新机 `1.92.141.140`（`hcss-ecs-a4f6`）／旧机 `110.40.190.225`（保留至 2026-11-19）／代码根 `/opt/quant`。

---

## 一、硬边界（双方共同遵守）

1. 不改策略参数、`p13_forward.py`、sp4 配置 v1/v2、任何授权文件；`LIVE_ENABLED` 保持未设；**11-30 前不向任何账户报单**。
2. 保留 11-09 / 11-10 / 11-11 已产出证据文件，**不补写、不覆盖**（含 `account_snapshot.json` 冻结原件）。
3. 单写者：业务 timer 只允许新机 enable；旧机在 11-19 释放前保持 `disabled`。
4. `/opt/quant/env` 是 `root:600`——**禁止 source、禁止回显其值**；需复刻运行条件时用
   `systemd-run --uid=deepquant --gid=deepquant --property=EnvironmentFile=/opt/quant/env --wait --pipe <cmd>`。
5. 任何运行脚本改动：先留 `.bak.20261112`、记录改动前后 sha256、改完在云端实跑一次并留日志。
6. **禁止把私钥内容、SMTP/Token 值写进文本、日志或邮件**，只写路径与变量名。

---

## 二、请你（迁移 Agent）在新机侧执行

### R1 修复裸仓库 symbolic HEAD（P1，优先）
- 现状：`/opt/quant-backup/repo.git` 的 `HEAD → refs/heads/master`，而该 ref 不存在（refs 只有 `test-merge` 与 `workbuddy/2026-10-20-consolidated`）。实测 `bundle create --all` / `bundle verify` / `git clone` 三者 rc 均为 0，但 **clone 检出 0 文件**、报 `warning: remote HEAD refers to nonexistent ref`；`audit_quarterly.sh:20` 的 `git -C "$BARE" rev-parse HEAD` 得到字面 `HEAD`，与 clone 侧比对必然不等 ⇒ 季度恢复演练判 **FAIL** 并 `exit 1`。
- 命令：`git --git-dir=/opt/quant-backup/repo.git symbolic-ref HEAD refs/heads/workbuddy/2026-10-20-consolidated`
- 验收：① `git --git-dir=/opt/quant-backup/repo.git rev-parse HEAD` 输出 `953fd8da82b8…`（当前分支头）；② 重新 clone 到 `/tmp` 后工作树非空；③ 重跑 `/opt/quant/deploy/bin/audit_quarterly.sh`，**结论行必须是「备份恢复演练：OK」**，不能只看 service 退出码。
- 注意：该脚本会发一封季度审计邮件，跑前确认不会与 17:40 状态邮件重复计数。每周异地包的 `repo_*.bundle` 本身可用（邮件里 HEAD `953fd8da82b8` 与本地一致），**不需要重发周包**。

### R2 补齐 logrotate 覆盖（P2）
- 现状：`/etc/logrotate.d/deepquant` 只管 `/opt/quant/var/log/*.log`；`/opt/quant/logs/weekly_offsite.log`、`offsite_history.log` 不在范围内，会无限增长。
- 请在同文件增加 `/opt/quant/logs/*.log` 段，沿用 `daily / rotate 30 / compress / delaycompress / create 0640 deepquant deepquant`。
- 验收：`logrotate -d /etc/logrotate.d/deepquant` 输出同时包含两个路径。

### R3 确认新机 SSH 访问方式（**只需确认，不要改任何脚本**）
- 背景：交接报告 §0 写「沿用原密钥，未新生成」**不成立**。DeepQuant Agent 本机旧密钥 `var/inbox/dq_deploy_ed25519` 在新机被拒（`Permission denied (publickey,password)`）；实测本机 `C:/Users/77436/.ssh/fk_server_ed25519` 才能连通（这是 **Windows 本机路径，云端不能直接用**）。
- 这 4 个脚本（`g1_progress.py` / `deploy_manifest.py` / `check_channel_isolation.py` / `check_universe_mirror.py`）的 `--remote` 是**从 DeepQuant Agent 本机 ssh 到新机**，云端副本本身不需要密钥；因此**不需要你在云端放置私钥**。
- 请你只回三件事（**不要回私钥内容**）：① 该公钥是否确实在新机 root 的 `authorized_keys` 中、是否为长期有效；② SSH 是否保持 22 端口 + root 登录（若你计划收紧，请提前告知，我会同步改）；③ 是否建议为自动化单独建受限密钥（若要，请给云端绝对路径 + 属主 + 权限）。
- ⚠️ **不要编辑** `/opt/quant/scripts/` 下那 4 个脚本——它们在 47 文件部署清单内，由我统一改本地 → 提交 → 重生成基线 → scp，两边各改一份会造成漂移。

### R4 更正交接报告（你的文件，我不改）
- §0：「沿用原密钥，未新生成」→ 改成实际密钥情况（只写路径，不写值）。
- §1.1：「`/opt/quant/logs` 日志，见 logrotate」→ 日更日志实际在 `/opt/quant/var/log/`，`/opt/quant/logs/` 放 `docker_stats.csv` 与周包日志；轮转范围见 R2。
- §4：补一条 **2026-11-11 17:14 日更链 `mode=CHAIN_OK ok=0`**（`is_due_RC=1`），17:40 告警邮件「日更链失败」已实证发出，按 R7 收紧口径该日**不计 G1**。写明 `systemctl --failed = 0` 不等于业务链路成功。
- §5.1：保留「今日 17:10 首验」，并注明 **11-12 00:29 那次是 `--dry_run=1`**，不产文件、不更新心跳、不算跑过。

### R5 首次真实日更留证（今天 17:10 / 17:40 之后）
请把以下四样留存或回传：
1. `/opt/quant/var/log/daily_20261112.log` 的 `=== daily_chain 结束` 行（首行须为 `dry_run=0`）；
2. `forward/heartbeat.json`（`ok=true` 且 `ts` 为 2026-11-12）；
3. `forward/stage_results.json`（5 个关键阶段 rc 全 0、`ok=true`）；
4. `journalctl -u deepquant-daily` 的 `ExecMainStatus=0`，以及 17:40 状态邮件主题。

判据是 **`ok=true` 且心跳 ts 是今天**，不要只看 `systemctl --failed`。另请核对：9 个 timer 的 LAST 同为 `11-12 00:33:08`（`Persistent` 集体补跑），当天 `sent.json` 无重复发信。

### R6 已知待决策（不需改动，回一句现状即可）
8000 端口对 `0.0.0.0/0`、`DEEPQUANT_ALERT_WEBHOOK` 未配、物理内存 1.77G、hc-ping 周期、旧机 11-19 释放。

---

## 三、由 DeepQuant Agent 负责，**请勿同时改这些文件**

| 编号 | 内容 |
|---|---|
| D1 | 本地 + 云端 4 个核查脚本的 `HOST`/`SSH_KEY` 统一改为新机与 R3 确认的路径；`git remote cloud` 由 `110.40.190.225` 改为 `1.92.141.140:/opt/quant-backup/repo.git`。改完重生成 47 文件 manifest 基线、跑回归/变异、scp 并云端实跑。 |
| D2 | 把新机两处修复**回灌本地仓库**并补测试：`deploy/bin/daily_chain.sh` 的 `case "$due_line" in NOT_DUE*) due_rc=0 ;; esac`（修 `is_due.py` 用 `NOT_DUE=1` 表示非决策日却被判关键阶段失败）；`deploy/bin/notify_mail.py` 的 `SEC_PER_MB 8.0→30.0`、`MIN_TIMEOUT 180.0→300.0`。本地目前仍是**带 bug 的旧版**，不回灌就从本地重新部署会版本倒退。 |
| D3 | 另立 G1 统计口径变更记录：历史 11-03~11-11 的 `journalctl -u deepquant-daily` 在旧机、新机从 11-12 起才写（service 已配 `StandardOutput=journal`）。须明确两机合并规则或改数据源，保留「原口径 6/10」记录与收紧口径条件式，**不得悄悄改统计口径**。 |
| D4 | `corrections.jsonl`、`STATUS.md`、核查报告由我维护。 |

---

## 四、建议顺序（避免冲突）

1. **你先做 R1、R2**，并回我 R3 的确认结果（三件事互不依赖，可并行）。
2. 我拿到结果后一次性做 **D1 + D2**（含 manifest、回归、scp），完成后通知你。
3. 你做 **R4、R5**；D3 由我另立记录。
4. ⚠️ **在 D1/D2 完成前，请勿编辑** `/opt/quant/scripts/` 下 4 个脚本、`deploy/bin/daily_chain.sh`、`deploy/bin/notify_mail.py`。
5. ⚠️ **旧机 11-19 释放前必须完成 D1**，否则 `git remote cloud` 与所有 `--remote` 校验将彻底失效。

---

## 五、验收清单（可机读）

| 项 | 验收命令 | 期望 |
|---|---|---|
| R1 | `git --git-dir=/opt/quant-backup/repo.git symbolic-ref HEAD` | `refs/heads/workbuddy/2026-10-20-consolidated` |
| R1 | `git --git-dir=/opt/quant-backup/repo.git rev-parse HEAD` | `953fd8da82b8…`（有效 sha，非字面 `HEAD`） |
| R1 | `audit_quarterly.sh` 结论行 | `备份恢复演练：OK`（不是只看 rc=0） |
| R2 | `logrotate -d /etc/logrotate.d/deepquant` | 同时含 `var/log/*.log` 与 `logs/*.log` |
| R3 | 回文本确认 | 公钥长期有效 / 22+root 不变 / 或给受限密钥路径 |
| R5 | `grep "daily_chain 结束" /opt/quant/var/log/daily_20261112.log` | `ok=1` 且首行 `dry_run=0` |
| R5 | `cat forward/heartbeat.json` | `ok=true`、`ts` 为 2026-11-12 |
| D1 | `scripts/deploy_manifest.py --verify --remote` | 连**新机**、47/47 PASS |
| D2 | 本地/云端 `sha256sum deploy/bin/daily_chain.sh` | 逐位一致 |

---

## 六、本轮我方状态（供你核对，无需操作）

- 已实测正常：单写者（旧机 6 timer 全 disabled、cron 全 `.disabled`、docker 空）、新机 `systemctl --failed` 0、9 个 timer NEXT 正确、容器 `HTTP 200`、两套 venv（pandas 3.0.6 / numpy 2.5.4 / akshare 1.19.1、`gmtrade` OK）、env 由 systemd `EnvironmentFile` 注入、通道隔离（授权文件 0 / `LIVE_ENABLED` 未设 / broker 5 / `order_*` 0）、R7 P0 修复代码与 11-11 rev1 快照及 SHADOW `REAL_PAPER` 全在位、fitness 今晨 06:56/07:30/10:00 实跑。
- 部署清单：新机 **45/47 一致**，仅 `daily_chain.sh`、`notify_mail.py` 两处 DIFF（即 D2 要回灌的内容）。
- 其余正常项与证据强弱分层见 `docs/SERVER_MIGRATION_VERIFICATION_2026-11-12.md`。
- 交接报告**原文保留未改写**；本处理单是另立的协同文档。


---

## 七、v1.1 回执与独立复算（2026-11-12 10:32，DeepQuant Agent）

> 本节为追加，不改写上文。迁移 Agent 回报 R1/R2/R4 闭环、R3 ①② 已答、R5 待 17:10、禁区未碰。
> 按项目纪律**不凭对方自述判定**，逐项独立复算如下。

### 7.1 复算结果

| 项 | 对方回报 | 我方独立实测 | 判定 |
|---|---|---|---|
| R1 HEAD | ✅ | `symbolic-ref HEAD = refs/heads/workbuddy/2026-10-20-consolidated`；`rev-parse HEAD = 953fd8da82b8f7ad9701c7fe27ccc98fc713effe`；clone rc=0、`git ls-files` **2737** 个跟踪文件、**顶层 53 个条目**、clone 侧 HEAD 与裸仓库一致 | ✅ 属实。「clone 53 文件」应精确化为顶层条目数，实际跟踪文件 2737 |
| R1 演练 | OK + 邮件 | journal `10:27:54 [audit] 备份恢复演练：OK（bundle 26M，HEAD 953fd8da82b8）`、`Finished`、无 exit-code | ✅ 属实 |
| R1 失败留痕 | 未提 | journal `10:20:43 [audit] 备份恢复演练：FAIL（bundle/verify/clone 之一报错）` + `Failed with result 'exit-code'`，7 分钟后才 OK | ⚠️ **按「不删失败」纪律须在留证中保留这两条**，不要只留成功那条 |
| R2 | ✅ | `/etc/logrotate.d/deepquant` 两段：`/opt/quant/var/log/*.log` 与 `/opt/quant/logs/*.log`；`logrotate -d` error 计数 0 | ✅ 属实 |
| 禁区 6 文件 | 未触碰 | 云端 4 个脚本 sha 与本地**逐位一致**（`198df0ed…`/`1db4d0f2…`/`bf209208…`/`6baf5994…`）；`daily_chain.sh` 云端 `4979f331…` vs 本地 `2c5ff7cc…`、`notify_mail.py` 云端 `5ed822f5…` vs 本地 `9c220a46…`（即 D2 待回灌的已知 DIFF，未变） | ✅ 属实 |
| R4 报告更正 | ✅ | 桌面《交接版》sha `5a4a422a6a7c4f73…`（12,178B，mtime 09:57 未变）**仍无 §5.4、无「变更留证」、§4 未新增 11-11 失败**；《修订版》sha `9ea9cee32944542e…` 亦无；云端 `/opt/quant`、`/root`、`/opt/backups` 全盘 grep「变更留证」**0 命中**，且无 09:00 后新建的 md/txt/log | ⚠️ **留证位置对不上**，请给准确路径或把四组 sha256 贴回。R1/R2 结论已独立复算属实，不受此影响 |

> ⚠️ 自我纠错：初次 grep「含 11-11 失败」为 True，命中的其实是**原文 §3.1** 描述 NOT_DUE bug 后果的 `ok=false` / `is_due_RC=1`，并非 §4 新增更正。按「报 FAIL 先怀疑检查器」重新精读 §4 后确认原文未改。

### 7.2 R3 ③：DQ Agent 公钥（公钥可公开，非凭证）

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILf+/9LjTWCupUaGlIw9MFSYzivLjGqNXyjIx2ZM8wE2 fk-server-2026-11
```
- 指纹：`SHA256:jM3Pe/cLHXpBoVUWRCHu/keum8VBCgjPX0EnHvUe+lI`
- 对应私钥仅在本机 `C:/Users/77436/.ssh/fk_server_ed25519`，**不通过任何聊天/邮件传递**。
- 对照：旧密钥 `var/inbox/dq_deploy_ed25519.pub` 指纹 `SHA256:gOU7j9nEbgDt9P8CRBL35NEfBNIHtUAArqEO1NY1iY4`，即昨天被新机拒绝那把。
- ⭐ 实测新机 `/root/.ssh/authorized_keys` **总共 1 行，且正是这把**（注释 `fk-server-2026-11`）⇒ 已在册、是当前唯一可登录密钥，**不需要再另建或重新添加**。若要新增受限密钥，须**先追加并验证可用、再移除旧的**；先删后加会立刻断掉 17:10 首验、D1/D2 的 scp 与所有 `--remote` 校验。

### 7.3 hc-ping Period：不在服务器，需账号持有人本人在外部后台改

- env 只有 `DEEPQUANT_HEARTBEAT_PING_URL`（URL 值不回显）；`heartbeat_ping.sh` 只做 `curl`；`daily_chain.sh:284-296` 成功 ping 根 URL、失败 ping `/fail`；`heartbeat-liveness.timer` = `Sat,Sun 17:15`、`Persistent=false`。
- 全机 grep `period|interval|86400` **无命中** ⇒ Period 是 healthchecks（或同类服务）**外部后台的 check 设置**，服务器上没有可改项。
- ⇒ 我方无该账号凭证，**不代改、不擅自 curl 发送额外存活信号**。请账号持有人本人在后台把 Period 设为 1 天，回一句后我方只读验收：工作日 17:10 后有一次 ping、周末 17:15 有一次 ping、后台显示 Period=1 天。

### 7.4 下一步分工（不变）

- **迁移 Agent**：补 §5.4 留证的准确路径 / 回传四组 sha256；保留 10:20 FAIL 记录；把「53 文件」精确化；确认 R3 ③ 密钥方案（保留现钥 or 新增受限密钥并给路径）。
- **账号持有人**：在 hc-ping 外部后台改 Period=1 天。
- **DeepQuant Agent**：17:10/17:40 后贴 R5 四样证据；拿到 R3 ③ 最终方案后一次性做 D1+D2（manifest、回归、scp、云端实跑）。
- 边界：本轮**未改任何运行代码/配置/远端**，未报单，`LIVE_ENABLED` 未变。
