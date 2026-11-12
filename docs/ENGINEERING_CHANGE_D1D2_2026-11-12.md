# 工程变更单 ECR-D1D2-MIGRATION-1（2026-11-12）

> 授权来源：用户转发的协同处理单 `docs/SERVER_MIGRATION_HANDOFF_TO_AGENT_2026-11-12.md` 第三、四节；迁移 Agent 2026-11-12 回执「R3 ③ 决定保留现钥、本轮不新增受限密钥 ⇒ D1/D2 密钥前置条件已解除，可以开始，禁区 6 文件我继续不动」。
> 冻结时间：2026-11-12 10:40 CST（**先于任何代码改动**）。
> 性质：基础设施迁移适配，**不是研究实验**，不改策略参数、不改统计估计量本身。

---

## 1. 缺陷与根因

| 编号 | 缺陷 | 根因 |
|---|---|---|
| D1 | 4 个核查脚本 + `git remote cloud` 仍指向旧机 `110.40.190.225` 与旧密钥 `var/inbox/dq_deploy_ed25519` | 换机后未回灌；旧机 11-19 释放后 `--remote` 校验与 cloud 推送彻底失效 |
| D2 | 本地仓库 `daily_chain.sh` / `notify_mail.py` 落后于新机（47 文件比对 45/47） | `/opt/quant` 是部署目录**非 git 工作副本**，迁移时在新机直接改的运行脚本未回灌仓库 |

## 2. 改动清单（6 个文件，均在 47 文件 manifest 内）

| 文件 | 改前 | 改后 |
|---|---|---|
| `scripts/g1_progress.py` | `HOST=root@110.40.190.225`、`KEY=REPO/var/inbox/dq_deploy_ed25519` | `HOST=root@1.92.141.140`、`KEY=Path.home()/".ssh"/"fk_server_ed25519"` |
| `scripts/deploy_manifest.py` | 同上（`REMOTE_HOST` / `SSH_KEY`） | 同上 |
| `scripts/check_channel_isolation.py` | 同上 | 同上 |
| `scripts/check_universe_mirror.py` | 同上 | 同上 |
| `deploy/bin/daily_chain.sh` | 缺 `case "$due_line" in NOT_DUE*) due_rc=0 ;; esac` | 补上（与云端 `4979f331…` 逐字一致） |
| `deploy/bin/notify_mail.py` | `SEC_PER_MB=8.0` / `MIN_TIMEOUT=180.0` | `30.0` / `300.0`（与云端 `5ed822f5…` 逐字一致） |

> 只改常量与一行 shell 判定，**不改任何判定逻辑、统计公式、策略参数**。`notify_mail.py` 的历史注释仍写「取 8 s/MB」，为保持与云端逐位一致本轮不同步修文案，另记待办。

## 3. ⚠️ 必须登记的口径副作用（不得悄悄改统计）

`g1_progress.py` 的 `HOST` 切到新机后，`journalctl -u deepquant-daily` **只能看到新机 11-12 起的记录**；历史 11-03~11-11 的 journal 在旧机。因此：

- 换 HOST 之后任何 G1 数字都是**单机视角**，与「原口径 6/10」**不可直接比较**；
- 本变更**不合并两机 journal、不改 `START`/`NEED`/连续段算法**；
- 「原口径 6/10」与「收紧口径、从修复上线后首个全绿交易日 D 起第 10 个全绿交易日」按 R7/Q3 原文保留；
- 两机合并规则或改数据源属 **D3**，须另立口径变更记录（本轮不做）。

## 4. 回滚

- 云端：scp 前对 6 个文件逐一 `cp -p` 留 `.bak.20261112d1`；回滚即拷回并复核 sha256。
- 本地：`git revert` 本变更提交；`deploy_manifest.prev.json` 保留上一版 47 文件基线。
- 密钥：R3 ③ 保留现钥，不触碰 `/root/.ssh/authorized_keys`，因此**回滚不涉及密钥**。

## 5. 验收条款 → 测试 ID

| AC | 条款 | 测试 | 「删除实现即变红」证据 |
|---|---|---|---|
| AC-1 | 4 个脚本默认目标为新机、密钥指向 `~/.ssh/fk_server_ed25519`、源码不含旧 IP/旧密钥字面量 | `test_d1d2_migration.py::test_*_targets` | 变异 M1/M2（改回旧 HOST / 旧 KEY） |
| AC-2 | `daily_chain.sh` 含 NOT_DUE 归一化，`is_due` 返回 1 不再被判关键失败 | `test_d1d2_migration.py::test_not_due_normalization` | 变异 M3（删该 case） |
| AC-3 | 26.45MB 附件超时 ≥ 853s（独立算术 `60+30×26.45=853.5`） | `test_d1d2_migration.py::test_large_attachment_timeout` | 变异 M4（`SEC_PER_MB` 改回 8） |
| AC-4 | `MIN_TIMEOUT=300`，且 `timeout_for(0)` ≥ 300 | 同上 | 变异 M5（`MIN_TIMEOUT` 改回 180） |
| AC-5 | 回归四连与既有变异脚本不受破坏 | `pytest scripts/tests` / backend / property_checks / verify_exec_all | — |
| AC-6 | 部署清单本地与云端 47/47 逐位一致 | `deploy_manifest.py --verify` / `--verify --remote` | 漂移即 FAIL |
| AC-7 | 通道隔离云端 FAIL 0 项 | `check_channel_isolation.py --remote` | — |

## 6. 部署顺序（不可颠倒）

1. 改本地 6 文件 → 补测试/变异 → **串行**跑回归与变异；
2. `git commit`（代码）；
3. 云端先备份 → scp 6 文件 → 云端实跑冒烟；
4. `deploy_manifest.py --write`（**必须在最后一次改代码之后**，记录步骤 2 的 commit）→ 本地/云端 `--verify`；
5. `git commit`（manifest 基线）→ `git remote set-url cloud` → 推 cloud（带新密钥）与 origin；
6. 复验 `g1_progress.py`、`check_universe_mirror.py --offline`、R7 DRYRUN 自检。

## 7. 边界

未改策略参数、`p13_forward.py`、sp4 配置 v1/v2、任何授权文件；11-09/10/11 原产物保留；未报单；`LIVE_ENABLED` 未设；不改 systemd/cron/调度（今天 17:10 首跑按现有配置执行）；不改 hc-ping 外部后台（属账号持有人）；不碰 `/root/.ssh/authorized_keys`。
