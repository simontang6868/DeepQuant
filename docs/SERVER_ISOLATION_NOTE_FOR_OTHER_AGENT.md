# 服务器现状与「第三个任务」隔离规范（给另一个 AI Agent）

> 自包含，可直接转发。**不含任何凭证、邮箱、心跳 URL 或服务器 IP**（用占位符），
> 隔离设计不需要这些；确需登录时由管理员单独提供。
> 采集时间：2026-11-07 22:52 CST，全部为只读实测，非推测。

---

## 0. 先说结论

**每日健身提醒目前是云端自动化（两条，10:00 / 15:00），根本不在这台服务器上跑。**
最优解是**不要把它部署到这台机器**——继续用云端的定时自动化即可，零干扰、零资源占用、零维护。
只有当它必须依赖服务器本地文件/本地网络时，才按本文 §4 做隔离部署。

这台机器只有 **2 核 / 1.9 GiB 内存 + 2 GiB swap**，已经承载两套长期任务，
且其中一套在每天 17:10 会做 1.67 GB 数据的重建。再加常驻服务前请务必设资源上限。

---

## 1. 服务器事实清单（实测）

| 项 | 值 |
| --- | --- |
| 主机 | 腾讯云轻量/CVM，`<SERVER_IP>`（仅密钥登录，端口 22） |
| OS | TencentOS Server 4，内核 `6.6.117-45.14.1.tl4.x86_64` |
| CPU / 内存 | **2 核 / 1.9 GiB**，swap `/swapfile` **2 GiB**（已用约 93 MiB） |
| 磁盘 | `/dev/vda1` 50 G，已用 11 G（22%），inode 用 1% |
| 时区 | **Asia/Shanghai (CST, +0800)**，已正确，不要改 |
| 空闲内存 | 采集时约 1.4 GiB available（buff/cache 1.4 GiB，实际可调度的不多） |
| 监听端口 | **22**（sshd）、**8000**（media-downloader，0.0.0.0）、127.0.0.1:36023（containerd） |
| 容器运行时 | docker + containerd 在跑 |

---

## 2. 已经跑着的两套任务（不要动）

### 任务 A：DeepQuant 量化证据链（`/opt/quant`）

| 排程 | 触发时刻（CST） | 做什么 | 资源特征 |
| --- | --- | --- | --- |
| `deepquant-daily.timer` | **周一~周五 17:10** | 行情增量更新 → 校验 → 月末决策 → 心跳；月末还会重建 1.67 GB 前复权数据并原子替换 | **峰值内存 549–579 MB**，`MemoryMax=1200M`，耗时约 6–10 分钟，CPU/IO 密集 |
| `deepquant-alert.timer` | **周一~周五 17:40** | 检查心跳 + 发每日状态邮件（死信开关） | 轻，但要占 SMTP 通道 |
| `deepquant-calendar.timer` | **每月 1 日 09:00** | 刷新交易日历 | 轻，失败关闭 |
| `/etc/cron.d/deepquant-weekly-offsite` | **每周一 09:30** | git bundle 全历史 + 证据 zip → 发一封 **约 25 MB** 的邮件 | 磁盘 IO + 出网带宽密集，SMTP 会话可到 260 s |
| `/etc/cron.d/deepquant-docker-stats` | **每小时整点** | 记一行容器内存/CPU | 极轻 |

- 运行用户：systemd service 用 **`deepquant` (uid 993)**；cron 那两条用 `root`。
- 凭证文件：**`/opt/quant/env`，`-rw------- deepquant:deepquant`，不在 git 里**。
  service 通过 `EnvironmentFile=/opt/quant/env` 注入；cron 脚本自己 `set -a; . /opt/quant/env; set +a`。
- 关键路径：`/opt/quant`（仓库）、`/opt/quant-backup/repo.git`（裸仓库备份）、
  `/opt/quant/logs`、`/opt/quant/var`（750，deepquant）。
- Python：`/opt/quant/.venv/bin/python`（3.12，依赖锁定）。
- systemd unit 前缀：**`deepquant-`**（6 个文件在 `/etc/systemd/system/`）。
- 邮件脚本：`/opt/quant/deploy/bin/notify_mail.py`（SMTP_SSL 465，失败非零退出）。

### 任务 B：media-downloader（Docker 容器）

- 容器名 `media-downloader`，**Up 12 天**，重启策略 `unless-stopped`。
- 宿主目录 **`/opt/vdl`** ↔ 容器内 `/srv`；容器内主进程 `python app.py`（root）。
- 对外端口 **8000**（0.0.0.0:8000 -> 8000/tcp）。
- 实时占用约 **56.84 MiB / 1.917 GiB，CPU 0.11%**；**没有设置内存/CPU 上限**（`Memory=0, NanoCpus=0`）。
- `/opt/vdl` 下有 `AGENTS.md`、`app.py` 及多个 `.bak`、`cookies.json` 等，属于该应用自有文件。

> 系统自带、也不要碰的：`/etc/cron.d/0hourly`、`sgagenttask`、`yunjing`（腾讯云 agent）、
> `sysstat-collect`(23:00)、`systemd-tmpfiles-clean`(23:20)、`logrotate`(00:00)、
> `raid-check`(周日 01:00)。

---

## 3. 绝对禁止触碰清单

1. 不要修改 `/etc/systemd/system/deepquant-*` 任何文件，不要 `systemctl restart/disable/mask` 这些 unit。
2. 不要修改或删除 `/etc/cron.d/deepquant-*`，**不要把新任务追加进 `deepquant-weekly-offsite`**。
3. 不要读写/覆盖 `/opt/quant/env`（尤其不要往里加你自己的变量），不要把它改成 644。
4. 不要写 `/opt/quant/logs/`、`/opt/quant/var/`、`/opt/quant/forward/`，不要改 `/opt/quant/.venv`。
5. 不要 `git push` 到 `/opt/quant-backup/repo.git`，也不要在该裸仓库里建新分支。
6. 不要 `docker restart/stop/rm media-downloader`，不要改它的重启策略，不要 `docker system prune`。
7. 不要占用端口 **22 / 8000**；不要新增 `0.0.0.0` 对外监听。
8. 不要改系统时区、swap、全局 Python（`pip install` 到系统解释器）、`/etc/cron.d/0hourly`。
9. 不要复用 `notify_mail.py` 的 env 文件发你自己的信——一份配置被两个任务共用，任一方改动会让两方同时挂掉。

---

## 4. 如果必须部署到这台机器：隔离规范

| 维度 | 要求 |
| --- | --- |
| 目录 | 新建 **`/opt/fitness/`**，与 `/opt/quant`、`/opt/vdl` 平级且完全独立；不要放 `/opt/quant/` 里 |
| 系统用户 | 新建专用用户 **`fitness`**（`useradd -r -s /sbin/nologin fitness`），不要用 `root` 或 `deepquant` |
| 虚拟环境 | 自带 `/opt/fitness/.venv`，不碰 `/opt/quant/.venv`、不全局 pip |
| 凭证 | **`/opt/fitness/env`，`chmod 600`，属主 `fitness:fitness`，不进任何 git 仓库**；变量名加前缀 `FITNESS_` 避免与 `DEEPQUANT_*` 撞 |
| unit 命名 | 一律用 **`fitness-`** 前缀：`fitness-reminder.service` / `.timer`；`SyslogIdentifier=fitness-reminder` |
| 日志 | 写 `/opt/fitness/logs/`，或只走 journal（用独立 SyslogIdentifier）；**不写 `/opt/quant/logs`** |
| 端口 | 如必须监听，只绑 `127.0.0.1` 的高位端口（例 `127.0.0.1:18081`），不要 `0.0.0.0` |
| **资源上限（强制）** | service 里必须写：`MemoryMax=128M`、`CPUQuota=20%`、`Nice=10`、`IOSchedulingClass=best-effort`、`IOSchedulingPriority=7`、`TimeoutStartSec=60`。理由：全机 1.9 G，任务 A 峰值已到 ~580 MB 且硬上限 1200M，任务 B 无上限常驻 |
| 定时器 | `Persistent=true`（休眠补跑）、`RandomizedDelaySec=60` 错峰；不要和 17:10 / 17:40 / 周一 09:30 / 月初 09:00 重叠 |
| 失败语义 | 脚本失败必须非零退出；不要静默吞异常；不要在日志里打印任何凭证值 |
| 邮件 | 用自己的发件配置与独立主题前缀，或干脆走云端自动化推送，**不要共用 `/opt/quant/env`** |

### 时间占用表（挑空闲时段）

| 时段（CST） | 状态 |
| --- | --- |
| **17:10–17:50 工作日** | 🔴 任务 A 日更 + 告警，内存/IO 高峰，**禁止** |
| **周一 09:00–10:00** | 🔴 周包（月初还叠加日历刷新），25 MB 附件发信，**禁止** |
| 每月 1 日 09:00 | 🔴 日历刷新，**禁止** |
| 每小时整点 ±1 分钟 | 🟡 docker-stats，极轻，可忽略 |
| 23:00–01:10 | 🟡 系统维护窗口（sysstat / logrotate / raid-check），建议避开 |
| **07:00–08:30 / 12:00–13:00 / 20:00–22:00** | 🟢 推荐 |

> 健身提醒若用现有的云端自动化（10:00 / 15:00），**完全不占上表任何一行**，这是最省事的选择。

---

## 5. 上线前后验收（务必执行）

```bash
# 上线前：记录基线
systemctl list-timers --all --no-pager | grep -E 'deepquant|fitness'
docker ps --format '{{.Names}} {{.Status}}'
free -h

# 上线后：确认原有两套没被影响
systemctl is-active deepquant-daily.timer deepquant-alert.timer deepquant-calendar.timer
systemctl list-timers --no-pager | grep deepquant     # NEXT 仍是原来的时刻
docker ps --format '{{.Names}} {{.Status}}'           # media-downloader 仍 Up
journalctl -u deepquant-daily --since today --no-pager | tail -20

# 演练：手动触发新任务，观察是否挤占
systemctl start fitness-reminder.service
systemctl status fitness-reminder.service --no-pager
free -h                                               # 与基线比对
```

**验收标准**：新任务跑完之后，三个 `deepquant-*.timer` 的 NEXT 时刻不变、
`media-downloader` 仍 Up、内存 available 没有持续下降、日更链日志仍 `ok=1`。
任何一条不满足，先 `systemctl stop --now fitness-reminder.timer` 回滚，再排查。

---

## 6. 一句话给执行者

这台机器是**证据链 + 下载器**的生产环境，资源紧、任务 A 有硬内存上限且每天 17:10 跑重活。
**优先把健身提醒留在云端自动化，不要上服务器**；若必须上，
就按 §4 用独立用户 / 独立目录 / 独立 env / `fitness-` 前缀 / `MemoryMax=128M` / 错峰时段，
并且**一次都不要改 `deepquant-*` 和 `/opt/quant/` 下的任何文件**。
