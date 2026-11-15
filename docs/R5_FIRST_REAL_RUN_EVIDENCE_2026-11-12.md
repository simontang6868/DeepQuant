# R5 首次真实日更留证（2026-11-12，换服务器后首跑）

生成时间：2026-11-12 21:1x（DQ Agent 只读核查）
用途：回传迁移 Agent 的 R5 四样证据与逐条判定
边界：本轮全部为只读（SSH 查询 + QQ 邮箱外部真值），未改云端、后台、策略、授权文件；未发任何 ping、未报单。

## 一、R5 四样证据（原文与判定）

### 1. `/opt/quant/var/log/daily_20261112.log`

命令：`grep -n '^===' /opt/quant/var/log/daily_20261112.log`

```text
1:=== daily_chain 20261112T002909 | repo=/opt/quant | dry_run=1 ===
17:=== daily_chain 结束 mode=DRY_RUN ok=1 universe_snap_DRY_RC=1 ===
19:=== daily_chain 20261112T171100 | repo=/opt/quant | dry_run=0 ===
85:=== daily_chain 结束 mode=NOT_DUE ok=1  ===
```

判定：**真跑段通过**（第 19 行 `dry_run=0`，第 85 行 `ok=1`）。

⚠️ 但验收清单写的「首行须为 `dry_run=0`」按**整份文件第 1 行**字面**不成立**：同一份日志追加记录了 00:29 的 `--dry-run=1` 演练（交接单 §5.1 已注明该次不产文件、不更新心跳、不算跑过）。按「真跑段首行」理解才成立。原文不改写，此处如实登记判据措辞缺陷。

### 2. `forward/heartbeat.json`

```json
{
 "ts": "2026-11-12T17:14:24",
 "decision": null,
 "mode": "NOT_DUE",
 "ok": true,
 "stage_note": "",
 "stages": [
  "update=0:关键",
  "universe_snap=0:关键",
  "is_due=0:关键",
  "paper_snapshot=0:关键",
  "shadow_reconcile=0:关键"
 ],
 "stage_results_path": "/opt/quant/research/stock_pick_2026-10-25/forward/stage_results.json",
 "source": "daily_chain",
 "duration_s": null
}
```

判定：✅ `ok=true`、`ts` 为 2026-11-12、5 个关键阶段 rc 全 0。

### 3. `forward/stage_results.json`

```json
{
 "ts": "2026-11-12T17:14:24",
 "mode": "NOT_DUE",
 "ok": true,
 "stages": [
  "update=0:关键",
  "universe_snap=0:关键",
  "is_due=0:关键",
  "paper_snapshot=0:关键",
  "shadow_reconcile=0:关键"
 ],
 "stage_note": ""
}
```

判定：✅ 5 个关键阶段 rc 全 0、`ok=true`。云端 journal 亦有 `{"ok": true, "alert": false, "failed_key_stages": [], "unknown_stages": [], "n_stages": 5}`。

### 4. `ExecMainStatus=0` 与 17:40 状态邮件

```text
Result=success
ExecMainCode=1
ExecMainStatus=0
```

- 17:40 邮件主题：`【DeepQuant 正常】20261112 日更链正常`
- QQ 邮箱外部真值：`created_at=2026-11-12T09:40:02Z`（北京时间 17:40），正文 `心跳 2026-11-12T17:14:24，mode=NOT_DUE，decision=None。`
- 17:14 另有证据备份邮件 `DeepQuant 证据备份 20261112T171425（hcss-ecs-a4f6）`，附件 1 个；云端 tar 为 45,219 字节 / 30 文件。

判定：✅

## 二、附加核对（交接单 §55）

- `systemctl --failed` → `0 loaded units listed`。仅作辅助佐证；按交接单 §4，`--failed=0` **不等于业务链路成功**，业务事实由上面四样证据给出。
- ⚠️ `sent.json` 在 `/opt/quant`（`find -maxdepth 6 -iname '*sent*'`）**未定位到** ⇒ 「当天 sent.json 无重复发信」这条判据**未验证**。改用邮箱外部真值替代：16:30 后共收到 3 封，其中 DeepQuant 自动邮件 2 封（17:14 备份、17:40 状态）各 1 次、**无重复**；20:00 第三封「待办：企业微信 webhook 地址」是另一路 `notify_mail.py` 手工调用，不是日更链发出的。此处如实写「未找到」，不写成「已确认无重复发信记录」。
- ⚠️ 「9 个 timer 的 LAST 同为 11-12 00:33:08」是迁移当天**上午的快照**；17:10 / 17:40 之后 `deepquant-daily.timer` LAST 已刷新为 17:11、`deepquant-alert.timer` 为 17:40，`calendar` 仍 00:33:08。此时点不该再拿该判据判失败。

## 三、业务事实（全绿 ≠ 有交易）

- `mode=NOT_DUE`：**今天不是决策日**，无决策、无报单。首个含 d4 字段的决策月末仍是 **11-30**。
- 仿真账户快照 `account_snapshot_20261112_rev1.json`：`source=gm-paper`、`cash.available=100000.00`、`positions=0`、**未发任何委托**。
- 影子对账 `shadow_reconcile/20261112.json`：`diffs` 1 条、`reason=match`（「台账目标与计划完全一致」）、未解释 0 条、`counts_toward_g2=false`，日志注明 `LEGACY_NO_D4`（11-30 前台账缺 d4）。⇒ **接入管道 ≠ 开始计数**，今天不计 G2。
- 外部心跳：17:14:25 云端 `[alert] 外部心跳已上报`（curl rc=0）；后台 `Period=1 day` / `Grace=3 hours` 已于 16:01 由账号持有人截图确认达标，**无需改动**。我方无后台凭证，`Last Ping` 是否落在 17:14 请账号持有人刷新页面确认。
- G1 计数：两机 journal 分家，D3 合并口径尚未另立，本卡**不对外引用任何 G1 计数**。

## 四、给迁移 Agent 的一句话

R5 首跑业务判据通过（`ok=true`、心跳 ts 为今天、5 个关键阶段 rc 全 0、`ExecMainStatus=0`、17:40 正常状态邮件外部真值已收到）；两处判据措辞/时点问题与 `sent.json` 未定位已如实登记，**未删失败、未改原文**；D3 两机 G1 合并口径仍待另立。
