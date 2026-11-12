# 服务器迁移后功能核查（DeepQuant · 2026-11-12）

> 性质：**执行 Agent 独立只读核查**，不凭《服务器迁移报告-2026-11-12-DeepQuant交接版》的自述下结论。
> 核查时间：2026-11-12 09:58–10:05 CST。全部操作为只读（含一次 `/tmp` 内的 bundle→clone 演练，跑完即删），**未改任何代码、配置、git remote，未启停任何服务，未报单**。
> 数据源：本地仓库 `git`/manifest、新机与旧机 SSH 实测、systemd journal、云端日志与产物、QQ 邮箱外部真值。
> 交接报告原文保留，不改写；本文件另立更正段落。

---

## 1. 结论先行

**系统层基本正常，但「各项功能都正常」这个结论不能下。** 三套业务的调度、容器、Python 环境、通道隔离、单写者切换均实测通过；但存在 **2 个 P0（会让 Agent 侧全部自动核查失效／代码版本倒退）、3 个 P1（业务链路状态、备份演练、G1 统计口径）**，以及若干 P2。

1. ⭐⭐ **Agent 侧工具链全部仍指向旧机 `110.40.190.225` 与旧密钥**：4 个核查脚本 + `git remote cloud`。交接报告 §0 写「沿用原密钥，未新生成」**不成立**——旧密钥在新机被拒（`Permission denied (publickey)`），实际可用的是 `~/.ssh/fk_server_ed25519`。
2. ⭐⭐ **本地仓库代码落后于新机 2 个运行脚本**：`daily_chain.sh`（NOT_DUE 回归修复）与 `notify_mail.py`（发信超时系数）。新机 47 文件逐位比对 **45/47 一致**。若从本地重新部署，会把已修好的 bug 带回去。
3. ⭐ **新机至今没有一次真实日更**：11-11 的日更实际跑在**旧机**且 `ok=false`（`is_due_RC=1`），11-12 00:29 那次是 `--dry-run`。当前 `heartbeat.json` 仍是 `ts=2026-11-11T17:14:09, ok=false`。**今天 17:10 才是新机首验**。
4. ⭐ **季度恢复演练的 HEAD 比对必然 FAIL**：裸仓库 `HEAD` 指向不存在的 `master`，实测 bundle/verify/clone 均 rc=0 但 clone 检出 **0 文件**，`a=HEAD` vs `b=fatal: ambiguous argument 'HEAD'`，`audit_quarterly.sh` 会判 FAIL 并 `exit 1`。报告 §4「均已手动触发成功」未覆盖结论行。
5. ⭐ **G1 统计跨机断裂**：历史 11-03~11-11 的 journal 在旧机，新机从 11-12 起才写；`g1_progress.py` 当前报 **G1=0/10、断点 20261111**（11-11 本来就不该计入，但即便改主机也需要合并两机数据）。
6. 其余正常：单写者、9 个 timer、3 个 cron.d、容器 8000、两套 venv、env 注入、通道隔离、R7 P0 修复代码与证据、周包邮件、fitness 三套今晨实跑。

---

## 2. 核查范围与方法

| 层 | 手段 | 说明 |
|---|---|---|
| 单写者 | 旧机 `systemctl is-enabled/is-active` + cron.d + docker | 防止两台同时发信/日更 |
| 调度 | 新机 `systemctl --failed`、`list-timers --all`、`systemctl cat` | 看 NEXT/LAST/User/EnvironmentFile/输出去向 |
| 代码 | 47 文件 sha256 逐位比对（本地 manifest → 新机 `/opt/quant`） | 不用 `--verify --remote`，因为它连的是旧机 |
| 环境 | 变量名（**不取值**）、venv 包版本、`gmtrade` import | 禁止 env 全量输出 |
| 业务证据 | 日更日志、`heartbeat.json`、`stage_results.json`、`shadow_reconcile/*.json`、快照 rev 文件 | 一手产物 |
| 外部真值 | QQ 邮箱主题检索 + 正文回读 | 不拿自己的输出自证 |
| 备份恢复 | `/tmp` 内 bundle→verify→clone→HEAD 比对 | 只读于生产，跑完删 |

⚠️ 一处自我纠错：初判「新机 service 不写 journal」是**过度推断**——`systemctl cat` 显示 `StandardOutput=journal`/`StandardError=journal` 已配；新机 `deepquant-daily` 无记录只是**因为还没跑过真实日更**。真实问题不是「不写日志」，而是「历史与未来分属两台机」。

---

## 3. 逐项核查结果

### 3.1 系统层 ✅

| 项 | 实测 | 判定 |
|---|---|---|
| 新机 | `hcss-ecs-a4f6` / `1.92.141.140` / uptime 11h35m（约 11-11 22:23 启动） | ✅ |
| 旧机停用 | 6 个业务 timer 全 `disabled`+`inactive`；3 个 cron.d 全 `.disabled`；`docker ps` 空 | ✅ **单写者成立** |
| 失败单元 | `systemctl --failed` = **0 loaded units** | ✅ |
| timer | 9 个 NEXT 正确：10:00 / 17:11:55 / 17:40 / 次日 06:25、07:30、09:05 / 11-14 17:15 / 12-01 09:00 / 2027-01-01 10:30 | ✅（但 LAST 全部同为 `00:33:08`，为迁移时 `Persistent` 集体补跑，需留意重复发信） |
| cron.d | 3 个均在且未 disabled | ✅ |
| 容器 | `media-downloader` Up 9h，`curl -I 127.0.0.1:8000` → `HTTP/1.1 200 OK` | ✅ |
| 资源 | 40G 用 19G(49%)；内存 1771M / available ≈1306M；zram 1.5G(45M 用)+swapfile 4G；OOM 未见 | ✅ 与报告一致 |
| Python | `.venv` pandas 3.0.6 / numpy 2.5.4 / akshare 1.19.1；`.venv-gm` `import gmtrade` OK | ✅ |
| env 注入 | `deepquant-daily.service`：`User=deepquant` + `EnvironmentFile=/opt/quant/env` + `MemoryHigh=900M`/`MemoryMax=1200M` | ✅ env 是 `root:root 600` 但由 systemd 注入，日更链可读，今天 17:10 前置满足 |
| 端口 | 22、`0.0.0.0:8000` 对外；53 等仅 127.0.0.1 | ⚠️ 8000 公网开放（报告 §5.2 已列为待决策） |

### 3.2 DeepQuant 数据与证据层 ✅ / ⚠️

| 项 | 实测 | 判定 |
|---|---|---|
| 部署清单 | 47 文件，**45 一致**；2 DIFF：`deploy/bin/daily_chain.sh`（`2c5ff7cc…`→`4979f331…`）、`deploy/bin/notify_mail.py`（`9c220a46…`→`5ed822f5…`） | ⚠️ 见 §4.2 |
| R7 P0 修复代码 | `paper_account_adapter.py` `452ad200…`、`shadow_daily_reconcile.py` `a81caf4d…`、`stage_rc_gate.py` `91d572b3…`、`r7_dryrun_check.py` `572e6aaf…` 与本地 manifest **逐位一致** | ✅ 迁移带过去了 |
| 快照分文件 | `account_snapshot_20261109_rev1.json`(11-10 21:55)、`account_snapshot_20261111_rev1.json`(11-11 17:14)、`latest.json`(day=20261111/rev=1)；原 `account_snapshot.json` 142B 冻结未动 | ✅ R7 迁移规则生效 |
| SHADOW 11-11 | `mode=REAL_PAPER`、`account_snapshot_reason=ok`、`n_account_revs=1`、diff 仅 1 条 `match`、`n_unexplained=0`、5 笔 `orders_sent=false`、`counts_toward_g2=false`（`LEGACY_NO_D4`） | ✅ 不计 G2 正确 |
| 通道隔离 | 授权文件命中 **0**、`LIVE_ENABLED` 未设、env 无真实通道变量、broker 白名单 5 个、只读适配器 `order_*(` **0** | ✅ |
| 裸仓库 | 245 提交；`workbuddy/2026-10-20-consolidated` = `953fd8da82b8` = **本地 HEAD**；但 `symbolic-ref HEAD = refs/heads/master`（不存在） | ⚠️ 见 §4.4 |
| 周包邮件 | 11-12 01:05 先发「发送失败」告警，01:08 修复后 `✅ 已发送`（bundle 26.45M / 245 提交 / HEAD `953fd8da82b8` + zip 956K） | ✅ 超时系数修复生效（日志里「SMTP 会话超时 853s」是推算配置值，非失败） |
| 备份目录 | `/opt/quant/var/backup/` **0 文件**（迁移时清理）；11-11 的 29 文件备份包已通过邮件外发 | ⚠️ 本机无留存，接受但需注明 |

### 3.3 业务链路状态 ⚠️（报告未写）

| 日期 | 事实 | 依据 |
|---|---|---|
| **11-11 17:14** | 日更在**旧机**跑，`mode=CHAIN_OK ok=0`，`stage_note=is_due_RC=1`，闸门判 `ok=false`、`alert=true`、`failed_key_stages=["is_due"]` | `daily_20261111.log`、`stage_results.json`；备份邮件主机名 `VM-0-16-tencentos` |
| 11-11 17:40 | **确实发了「【DeepQuant 告警】日更链失败」**（正文 `mode=CHAIN_OK note=is_due_RC=1`） | QQ 邮箱外部真值 ⇒ R7 收紧口径生效，该日不计 G1 |
| 11-12 00:29 | `dry_run=1`；R7 自检四项 PASS；`mode=DRY_RUN ok=1`、`universe_snap_DRY_RC=1`；**不产文件、不更新心跳** | `daily_20261112.log` 首行；报告 §6.5 也明确 DRYRUN 不算跑过 |
| 11-12 01:17 | 一封「【DeepQuant 正常】20261112 日更链正常」，正文心跳 `2026-11-12T01:00:00` | ⚠️ **与当前 `heartbeat.json`（11-11 17:14 / `ok=false`）不符** ⇒ 迁移期手工复刻的测试邮件，**不是业务事实**，外部审阅者勿据此判定日更已成功 |
| 当前 | `heartbeat.json` `ts=2026-11-11T17:14:09`、`ok=false`、`stage_note=is_due_RC=1` | 文件 mtime 亦为 11-11 17:14 |
| **11-12 17:10** | **新机首次真实日更尚未发生**（核查时 10:05） | 唯一未验收的关键路径 |

> ⚠️ 报告 §4 只写「systemctl failed 0」，未提 11-11 业务链路 `ok=0`。**单元没失败 ≠ 业务链路成功**——这是本项目反复记录过的判据错误。

### 3.4 fitness 三套 ✅

- `sent.json` 共 **9 条**，末条 `2026-11-12|10:00 = 2026-11-12T10:00:48+08:00`（报告 §4 写的「8 条／末条 11-11|15:00」是迁移当时的快照，现已更新）。
- 今晨 06:56 取信 `[imap] … 候选=0` + `[done] rc=0`；07:30 `[watchdog] 2026-11-12 已被覆盖，无缺口（末日 2026-11-24）`。
- 10:00 提醒已发（sent.json 实证），15:00 待自然触发。

---

## 4. 需要处理的缺陷（按优先级）

### 4.1 ⭐⭐ P0-A：Agent 工具链全部指向旧机／旧密钥

| 位置 | 硬编码 |
|---|---|
| `scripts/g1_progress.py:29-30` | `HOST=root@110.40.190.225`、`KEY=var/inbox/dq_deploy_ed25519` |
| `scripts/deploy_manifest.py:33-34` | 同上 |
| `scripts/check_channel_isolation.py:28-30` | 同上 |
| `scripts/check_universe_mirror.py:35-36` | 同上 |
| `git remote cloud` | `root@110.40.190.225:/opt/quant-backup/repo.git` |

**实测后果**：`deploy_manifest.py --verify --remote` 报「云端 47/47 … FAIL」——那个「云端」是**旧机**，与新机无关；`g1_progress.py` 报 `G1=0/10｜断点 20261111`；`check_channel_isolation.py --remote` 同样查的是旧机。
**密钥**：`ssh -i var/inbox/dq_deploy_ed25519 root@1.92.141.140` → `Permission denied (publickey,password)`；`ssh -i ~/.ssh/fk_server_ed25519 …` → rc=0。交接报告 §0「沿用原密钥，未新生成」对 Agent 手上的旧密钥**不成立**。
**额外一层**：新机上的这 4 个脚本也是从旧机整体拷来的，同样写着旧 IP/旧密钥；即使把本地改好、云端脚本不改，云端自校验仍会连错主机。
**G1 口径**：历史 11-03~11-11 的 `journalctl -u deepquant-daily` 在旧机，新机从 11-12 起才有（service 已配 `StandardOutput=journal`）。换机后 `g1_progress.py` 若只改 HOST，会丢掉历史；须改为「两机 journal 合并」或改读 `stage_results.json`/日更日志序列，并**另立预登记**说明统计口径变化（不得悄悄改）。

### 4.2 ⭐⭐ P0-B：本地仓库代码落后于新机，存在版本倒退风险

新机 `daily_chain.sh:144` 多了 `case "$due_line" in NOT_DUE*) due_rc=0 ;; esac`（修的是我 R7 收紧 rc 时引入的回归：`is_due.py` 用 `NOT_DUE=1` 表示"非决策日"，被当成关键阶段失败）；`notify_mail.py` 的 `SEC_PER_MB 8.0→30.0`、`MIN_TIMEOUT 180.0→300.0`。
**本地两处都还是旧值**（本地 `daily_chain.sh:135,144` 无该 case；`notify_mail.py:96-97` 仍 8.0/180.0）。⇒ 本地 git 里是**带 bug 的版本**，manifest 基线也记的是旧哈希。若按常规流程从本地 scp 部署，会把 NOT_DUE 回归和 26MB 邮件超时一起带回去。
⚠️ 项目 MEMORY §12 已记过同类坑：`/opt/quant` 是**部署目录非 git 工作副本**，改 `deploy/bin/` 运行脚本必须另 scp 回灌仓库。本次迁移正是又一次发生。

### 4.3 ⭐ P1-A：11-11 业务失败未进报告；当前心跳为 `ok=false`

见 §3.3。按 R7/Q3 收紧口径，11-11 `ok=false` **不计 G1** 是正确行为，不是新缺陷；但交接报告应写明，且今天 17:40 告警读到的会是 `heartbeat.json`——若 17:10 首跑失败，告警会正确报失败（脚本 `:172` 判 `ok is not True`）。
另注：`heartbeat_alert.py` **不读 `stage_results.json`**（R7 偏差登记已如实记录），只能报 `mode`/`stage_note`，没有阶段明细。

### 4.4 ⭐ P1-B：季度恢复演练 HEAD 比对失效

实测（`/tmp` 内，跑完删）：`bundle rc=0`、`verify rc=0`、`clone rc=0`，但
`a=$(git -C "$BARE" rev-parse HEAD)` = **字面 `HEAD`**，`b=$(git -C "$TMP/c" rev-parse HEAD)` = `fatal: ambiguous argument 'HEAD'`，clone 检出 **0 文件**，直接 clone 裸仓库报 `warning: remote HEAD refers to nonexistent ref, unable to checkout`。
⇒ `audit_quarterly.sh` 会 `add "备份恢复演练：FAIL"` 并 `exit 1`。根因：裸仓库 `HEAD → refs/heads/master`，而 refs 只有 `test-merge` 与 `workbuddy/2026-10-20-consolidated`。
**注意**：每周异地包用 `git bundle create --all`，邮件里明确写了 `HEAD 953fd8da82b8`，**bundle 本身可用**；坏的是裸仓库的 symbolic HEAD 与依赖它的季度演练。

### 4.5 P2：其余小项

| 项 | 事实 |
|---|---|
| 日志轮转 | `/etc/logrotate.d/deepquant` 只覆盖 `/opt/quant/var/log/*.log`，**不覆盖** `/opt/quant/logs/*.log`（`weekly_offsite.log`、`offsite_history.log` 会无限增长） |
| 报告 §1.1 | 写「`/opt/quant/logs` 日志，见 logrotate」，与实际轮转路径不符 |
| LAST 集体补跑 | 9 个 timer 的 LAST 同为 11-12 00:33:08（迁移时 `Persistent` 补跑/enable 触发），建议核对当天无重复发信 |
| 备份目录 | `/opt/quant/var/backup/` 0 文件，历史 tar 已清；外发邮件是唯一留存 |
| 安全/告警 | 8000 `0.0.0.0/0`、`DEEPQUANT_ALERT_WEBHOOK` 未配（无 webhook 时 `_notify()` 不算送出）、内存 1.77G —— **报告 §5.2 已列，非新发现** |
| 裸仓库同步 | 分支 sha = 本地 `953fd8d` ✅；但 `git remote cloud` 仍指旧机，**旧机 11-19 释放后该远端彻底失效** |

---

## 5. 尚未验收（等自然触发，与报告 §5.1 一致）

| 时间 | 验收点 | 建议核查方式 |
|---|---|---|
| **今日 17:10** | 新机首次真实日更 | `journalctl -u deepquant-daily`、`daily_20261112.log`（须是 `dry_run=0`）、`heartbeat.json` 被覆盖为 `ok:true`、`stage_results.json` 5 阶段全 0 |
| 今日 17:40 | 状态邮件 | QQ 邮箱收 `【DeepQuant 正常】`，且正文心跳 ts 是今天 |
| 今日 15:00 | 第二封健身提醒 | `sent.json` 新增 `2026-11-12|15:00` |
| 周一 09:30 | 周包 | `weekly_offsite.log` 新增 `✅ 已发送` |
| 12-01 09:00 | 日历刷新 | 缺 2027 只记录不阻塞，失败关闭仅限当月 |

⚠️ 17:10 首跑必须**人工看结论行**：R7 收紧后关键阶段任一非零即 `ok=false`；`systemctl --failed` 为 0 不代表链路成功。

---

## 6. 修复建议（**待你书面授权，本轮一律未做**）

1. **P0-A（最小必要）**：把 4 个脚本的 `HOST`/`SSH_KEY` 改为新机 + 新密钥，并同步回灌云端副本；`git remote cloud set-url` 指向 `1.92.141.140`。改脚本会触发 manifest 漂移，须重生成基线并跑变异/回归。
2. **P0-B**：把新机两处修复回灌本地 `deploy/bin/daily_chain.sh` 与 `notify_mail.py`（补测试：`NOT_DUE` 不得判关键失败、26MB 附件超时余量），提交后重生成 47 文件基线，使本地/云端再次逐位一致。
3. **P1-B**：`git --git-dir=/opt/quant-backup/repo.git symbolic-ref HEAD refs/heads/workbuddy/2026-10-20-consolidated`，再重跑一次季度演练并核对结论行（不只是 service rc）。
4. **G1 口径**：另立变更记录，明确换机后统计数据源合并规则与「原口径 6/10 保留、收紧口径条件式」的关系，**不得悄悄改统计口径**。
5. **P2**：logrotate 增 `/opt/quant/logs/*.log`；修报告 §0/§1.1 两处描述；确认 00:33 补跑无重复发信。

**硬边界**：未改策略参数、`p13_forward.py`、sp4 配置 v1/v2、任何授权文件；未报单；`LIVE_ENABLED=false` 未变；11-09/11-10/11-11 原产物保留。

---

## 7. 审阅者可自行复算的命令

```bash
# 1 单写者（旧机）
ssh -i var/inbox/dq_deploy_ed25519 root@110.40.190.225 \
  'for t in deepquant-daily deepquant-alert deepquant-calendar fitness-plan-fetch fitness-plan-watchdog fitness-reminder; do echo $t $(systemctl is-enabled $t.timer); done; ls /etc/cron.d/'

# 2 新机必须用新密钥
ssh -i ~/.ssh/fk_server_ed25519 root@1.92.141.140 'hostname; systemctl --failed --no-pager; systemctl list-timers --all --no-pager | head -14'

# 3 47 文件逐位比对（注意 Windows 输出 CRLF，read 时去 \r）
#   把 deploy_manifest.json 的 path\tsha 导出为 tsv → scp 到 /tmp → 远端 while read 比对
#   预期：45/47 一致，仅 daily_chain.sh、notify_mail.py DIFF

# 4 业务状态
ssh -i ~/.ssh/fk_server_ed25519 root@1.92.141.140 '
cat /opt/quant/research/stock_pick_2026-10-25/forward/heartbeat.json;
head -2 /opt/quant/var/log/daily_20261112.log;
grep "日更链 结束" /opt/quant/var/log/daily_20261111.log'

# 5 裸仓库 HEAD 与季度演练
ssh -i ~/.ssh/fk_server_ed25519 root@1.92.141.140 '
git --git-dir=/opt/quant-backup/repo.git symbolic-ref HEAD;
git --git-dir=/opt/quant-backup/repo.git show-ref;
grep -n "rev-parse HEAD" /opt/quant/deploy/bin/audit_quarterly.sh'

# 6 本地工具链失效
grep -rn "110.40.190.225" scripts/g1_progress.py scripts/deploy_manifest.py \
  scripts/check_channel_isolation.py scripts/check_universe_mirror.py; git remote -v
```

---

## 8. 证据强弱与限制

- **强**（一手实测）：本节所有 ✅ 与 DIFF 数字、journal、日志、产物 JSON、邮件正文回读。
- **中**：11-11 日更"在旧机跑"由备份邮件主机名 `VM-0-16-tencentos` + 新机 uptime（约 11-11 22:23 启动）交叉推断，未做旧机 journal 逐条比对。
- **未验证**：今天 17:10 首跑、15:00 提醒、周一 09:30 周包、12-01 日历刷新——均须等自然触发。
- **未做**：未登录 QQ 邮箱以外的外部系统；未查安全组规则（只能看到 `0.0.0.0:8000` 监听）；未读取任何 env 值。
- 本报告是**执行 Agent 自审**，不构成独立审查；如转交第三方，请按 `docs/REVIEWER_GUIDE_2026-11-05.md` 自行复算。
