# SHADOW 每日对账接入日更链（2.9 / G2）

**日期**：2026-11-08｜**状态**：`WIRED_NOT_COUNTING`｜**作者**：执行 agent（自审）
**范围**：只接观测管道，**未改任何策略参数、未接任何券商/仿真通道、未发单**。

---

## 一、结论先行

`execution/shadow.py` 的 2.9-a（生成不发送、差异可解释）早已 PASS，但
**此前只被 `backend/tests/execution/test_shadow.py` 引用，日更链一次都没调用过**。
所以 STATUS 里 G2 的「当前 0 天」是真实的：不是"跑了没算"，是根本没接进来。

2026-11-08 已接入：

| 项 | 文件 | 作用 |
| --- | --- | --- |
| 对账脚本 | `scripts/shadow_daily_reconcile.py` | 每交易日算「本应下的单」→ 比对台账 → 落一条时点记录 |
| 日更链 | `deploy/bin/daily_chain.sh` 新增 **3b** 段 | 心跳前调用；**不阻断链路**，异常写进 `stage_note`（17:40 状态邮件可见） |
| 机外备份 | `deploy/bin/backup_offsite.sh` | 新增 `shadow_reconcile/`、镜像清单、`sp4_config_v2` 进每日证据包 |
| 测试 | `scripts/tests/test_shadow_daily_reconcile.py` | 27 条 |
| 变异 | `scripts/shadow_reconcile_mutation_test.py` | **11/11 检出** |

⚠️ **接入管道 ≠ G2 开始计数**。截至今日 G2 仍是 **0/20**，原因见下一节。

---

## 二、最重要的一条：不许自证

G2 判据是「连续 20 个交易日无未解释差异」。当前 `LIVE_ENABLED=false`、
没有券商账户、也没有仿真账户 ⇒ **没有真实的资金与持仓**。
若自己造一个账户快照、跑出「无差异」再计入 G2，就是拿自己的输出证明自己正确——
本项目反复踩过的「没检出 ≠ 没问题」。

因此每天的结果分三档：

| mode | 触发 | `counts_toward_g2` | 退出码 |
| --- | --- | --- | --- |
| `REAL_PAPER` | `forward/account_snapshot.json` 存在、`asof == 当日`、`source` 非空、结构合法，且无未解释差异 | **true** | 0（有未解释差异则 3） |
| `SELF_CHECK_ONLY` | 无上述快照（**当前所有日子**） | **false** | 0 |
| `NO_INPUTS` | 台账或当日 universe 快照缺失 | false | **2（失败关闭）** |

补充限定：台账缺 `target_holds_d4`（11-30 前的 `LEGACY_NO_D4` 老记录）时，
即便有真实账户也**不计入** G2——与 STATUS §3「证据窗口只用 11-30 起含 d4 字段的记录」一致。

⇒ 20 天倒计时从「有真实仿真账户快照」的第一个交易日起。
`python scripts/shadow_daily_reconcile.py --summary` 可随时查连续段。

### 输入契约（`forward/account_snapshot.json`）

```json
{"asof":"YYYYMMDD","source":"gm-paper",
 "cash":{"available":0.0,"frozen":0.0,"total":0.0},
 "positions":[{"code":"600990","quantity":700,"available":700,"avg_cost":5.41}]}
```

`asof` 不等于当日一律拒绝（防用三天前的持仓冒充今日对账）。该文件**目前不存在**，
由用户选定模拟盘方案后另写只读适配器产出；选型见
`docs/PAPER_TRADING_API_OPTIONS_2026-11-08.md`。

---

## 三、⭐ 一个必须写下来的运维事实

`/opt/quant` **不是 git 工作副本**（没有 `.git`），只是部署目录；
裸仓库在 `/opt/quant-backup/repo.git`。
⇒ `git push cloud --all` **只做异地备份，不会让日更链生效**。
本次接入是单独 `scp` 了 3 个文件到 `/opt/quant/`，并在云端用
`.venv/bin/python scripts/shadow_daily_reconcile.py --date 20261106 --no-write`
实跑确认 `rc=0`、`bash -n` 两个 shell 脚本通过。
改动前原文件已备份为 `deploy/bin/*.bak.20261108`。

**今后凡改 `deploy/bin/` 下的运行脚本，必须同时做这两件事**，否则就是"假接入"。

---

## 四、变异测试抓到的真实漏洞（自查处）

首轮 10 个变异里 **M1（去掉「必须有真实仿真账户」）竟然全绿**。
原因：当时所有测试都用 `LEGACY_NO_D4` 台账，被后面「老台账不计入 G2」
这条兜住了，M1 的效果被完全吸收。而 **11-30 之后恰好是「d4 台账 + 无账户」**
——漏洞正好落在最需要守的那一天。

补 `test_d4_ledger_without_account_still_never_counts` 后重跑：**11/11 检出**。
这是本轮最有价值的一步：不是"测试写好了"，是"测试被证明有牙齿"。

**其余守住的闸门**：陈旧快照、输入缺失静默成功、停牌股填价、
未知原因硬凑解释、`orders_sent=True`、同日静默覆盖、未解释差异不报红、
老台账计入 G2、门槛 20 日改成 1 日、**事后补跑计入 G2（M11）**。

⚠️ 另一个必须写下的时点闸门：记录里有 `generated_at` / `run_date` /
`is_backfill`，**生成日不等于交易日一律不计入 G2**，否则事后补跑一堆
历史日期就能把 20 天凑出来、把回顾包装成连续前瞻。M11 变异专门守这条。
（M1、M11 的变异定义都曾因源码改动而失效一次，是变异脚本自己报出来的，
没有假装检出。）

---

## 五、实测记录

| 项 | 结果 |
| --- | --- |
| 本地 `--date 20261106 --no-write` | `SELF_CHECK_ONLY`｜diff 1 条（MATCH）｜未解释 0｜5 笔单（未发送）｜rc=0｜不计 G2 |
| 云端同命令 | 同上，`rc=0`；`bash -n` 两脚本 OK |
| `scripts` 回归 | 见提交说明 |
| 变异测试 | 11/11 检出，恢复后复跑 27 passed |
| 快照镜像 | 回拉 20261106 后两侧 10 文件逐文件一致，`universe_state_mirror.json` 已更新 |
| 事后补记 | **未生成 20261106/20261108 的证据文件**（`--no-write`）；第一条真实记录应由 11-09 17:10 链路自然产出 |

---

## 六、11-30 前后要盯的

1. **11-09（周一）17:10** 起，链路会自动写 `forward/shadow_reconcile/YYYYMMDD.json`。
   当天日志应出现 `[shadow] ... 管道自检 ... 计入G2=否 ... rc=0`。
2. 若某天出现 `SHADOW_RECONCILE_RC=2`，说明当日 `universe_state` 快照没生成——
   与 11-30 决策日「`asof` 必须等于 20261130」是同一类时点风险，须人工核对。
3. **11-27 演练**已加一步：在云主机跑 `--summary` 与只读对账，结果写进简报。
4. G2 的 20 天**不会**因为 11-30 自动开始；仍取决于是否接入仿真账户。

---

## 七、引用限定

- 本文是自审材料，作者即执行 agent；独立判断请按
  `docs/REVIEWER_GUIDE_2026-11-05.md` 自行复算。
- `docs/ACCEPTANCE_TRACEABILITY_2026-11-03.md` 里 2.9-b 仍写
  `NOT_IMPLEMENTED`，**历史文档不改写**；状态更正记在
  `forward/corrections.jsonl` 与 STATUS §3。
- 接入只改变「有没有每日记录」，**不改变任何已有证据、不产生样本外结论**。
