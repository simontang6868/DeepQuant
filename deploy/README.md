# 云主机部署清单（证据引擎）

> 这台机器**只做证据**：更新数据、跑月末决策、落台账、发心跳。
> **不下单、不接券商、没有任何交易凭证。** 若有人要求在这台机器上放券商账号，说明任务被误解了。

## 0. 前置（需要用户提供）

| 项 | 说明 |
| --- | --- |
| IP + 用户名 | 只开 SSH 密钥登录，不提供密码 |
| 系统 | Ubuntu 22.04/24.04（Debian 12 亦可） |
| 磁盘 | ≥ 40 GB（行情数据约 250 MB，但 ADJ 重建要写一份 stage 副本，日志与备份按年增长） |
| 出网 | 需能直连腾讯 / 新浪行情接口与 GitHub（见第 3 步逐源验证） |

## 1. 本机：生成 sha256 清单并上传

```bash
# 1.1 清单（排除每日状态快照：它可由行情重建，且每天 1.7 MB）
python deploy/bin/migrate_data.py manifest \
    --out var/migrate_manifest.json \
    --exclude universe_state

# 1.2 上传代码（不含数据与本地环境）
rsync -az --info=progress2 \
    --exclude '.git/' --exclude 'data/' --exclude 'var/' --exclude '.venv/' \
    --exclude 'research/stock_pick_2026-10-25/forward/universe_state/' \
    ./ user@HOST:/opt/deepquant/

# 1.3 上传清单与数据（数据单独传，便于断点续传与单独校验）
rsync -az var/migrate_manifest.json user@HOST:/opt/deepquant/var/
rsync -az data/exec_all user@HOST:/opt/deepquant/data/
rsync -az data/qfq_all  user@HOST:/opt/deepquant/data/
```

## 2. 主机：初始化

```bash
sudo /opt/deepquant/deploy/bootstrap.sh --repo /opt/deepquant
# 确认 SSH 端口放行无误后再决定是否启用防火墙：
#   sudo /opt/deepquant/deploy/bootstrap.sh --enable-firewall
```

`bootstrap.sh` 会做：装依赖 → 时区 Asia/Shanghai → chrony 校时 → 建 `deepquant` 系统用户
→ 建 venv 装固定版本依赖 → 生成 `/opt/deepquant/env` → 装 3 个 timer + logrotate
→ 配置防火墙规则 → **跑一次行情源自检**。

> ⚠️ 防火墙默认只配置不启用。在 SSH 端口非 22 的机器上，`ufw enable` 会当场切断会话。

## 3. 逐源验证（R2 要求：不通就上报，不换源）

```bash
cd /opt/deepquant
.venv/bin/python deploy/bin/preflight_sources.py
```

输出 `PASS` 才继续。四种状态：

| 状态 | 含义 | 处置 |
| --- | --- | --- |
| `OK` | 连通且结构符合预期 | — |
| `DEGRADED` | 连通但结构变了 | **停下上报**。这比失败更危险：下游会把它当成"没数据" |
| `FAIL` | 网络/HTTP 失败 | 停下上报。**不要**自行换源或改口径 |

结果落在 `deploy/preflight_<host>_<ts>.json`，随部署记录归档。

## 4. 迁移校验（逐文件）

```bash
cd /opt/deepquant
.venv/bin/python deploy/bin/migrate_data.py verify --manifest var/migrate_manifest.json
```

必须 `PASS`（一致数 = 清单条目数，缺失/大小/哈希/多余 全为 0）。
`verify` 会**双向**比：清单有磁盘无用"缺失"，磁盘有清单无用"多余"。
少了任何一个 `qfq_all/*.qfq.csv`，后果是该股票在截面里消失、选股变了，**但不会报错**。

## 5. 试跑

```bash
sudo -u deepquant /opt/deepquant/deploy/bin/daily_chain.sh --dry-run
sudo -u deepquant /opt/deepquant/deploy/bin/daily_chain.sh            # 真跑一次
sudo -u deepquant /opt/deepquant/deploy/bin/backup_offsite.sh
cat /opt/deepquant/research/stock_pick_2026-10-25/forward/heartbeat.json
```

`--dry-run` 的心跳 `mode` 是 `DRY_RUN`，它与真跑的 `DECIDED` / `NOT_DUE` 明确区分——
免得事后把演练日志当成真决策。

## 6. 外部心跳（必配）

在 healthchecks.io（或同类）建一个每日检查：周期 1 天、宽限 3 小时。
把 ping URL 填进 `/opt/deepquant/env` 的 `DEEPQUANT_HEARTBEAT_PING_URL`。

> 机内 `heartbeat.json` 只能证明"这台机器认为它跑过了"。整机宕机时**没有任何进程**
> 会去报告"我没跑"。这一条是唯一能覆盖整机故障的机制。

## 7. 验收（G1）

| 项 | 判据 | 怎么查 |
| --- | --- | --- |
| 连续 10 个交易日零漏跑 | 每天都有心跳且 `ok=true` | `ls forward/heartbeat.json` + 日志 |
| 台账与迁移前一致 | `migrate_data.py verify` 通过 | 同上 |
| 跨月测试全绿 | `pytest scripts/sp5/tests/test_month_end_calendar.py` | 见输出 |

## 8. 时间表

| 日期 | 事项 |
| --- | --- |
| 11-16 前 | 连续运行（若云主机在此之前就位） |
| **11-30** | 第一个合格月末。**当天 17:10 必须跑成**——这是唯一能产出真正 FORWARD 记录的时刻 |
| 12-31 前 | 若 11-30 未成，作为顺延目标；同时确认交易日历已覆盖 2027 年 |
| 其后 | 每月月末当天跑；漏跑**不得**用 LATE_RUN 补记（超过 3 天即判漏跑） |

## 9. 故障处置（按退出码 / mode）

| 信号 | 含义 | 处置 |
| --- | --- | --- |
| `rc=3` / `mode=ABORT_STALE` | 数据没覆盖到月末（更新没跑完或源缺当天） | 查 `var/update_*.json`；重跑更新；仍不行则上报 |
| `rc=6` / `mode=ABORT_CALENDAR` | 交易日历缺失/被改/过旧 | `python trade_calendar.py --refresh` |
| `rc=8` / `mode=MISSED` | 该月已漏跑，超过补记窗口 | **如实记录**，写进 STATUS 未决项；不得补写台账 |
| `mode=ABORT_QFQ_VERIFY` | ADJ 重建与现行版本不一致 | 不替换、不决策；查 `var/qfq_verify_*.json` |
| `mode=NOT_DUE` + `ok=true` | 非决策窗口，正常 | 无需动作 |

## 10. 禁止事项

1. **不得**在该机器上安装任何券商客户端或保存券商账号
2. **不得**创建 `live_authorization.json`
3. **不得**因行情源不通而自行换源或改口径
4. **不得**用补跑记录填充漏跑月份
5. **不得**手工改 `forward/ledger/` 下的任何文件（它是证据）
