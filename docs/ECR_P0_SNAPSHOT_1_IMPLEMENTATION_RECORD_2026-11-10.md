# ECR-P0-SNAPSHOT-1 实施记录（补充段，不改变更单原文）

> 变更单 `docs/ENGINEERING_CHANGE_P0_SNAPSHOT_2026-11-10.md` 已冻结
> sha256 `5c56ceb0821541699ea27b094c6bac44538643a42e22b3418a75e9d59fd9e699`，
> **本文件另立、不修改原文**（L3ac D1 / R5「原文不改写」）。

| 项 | 值 |
| --- | --- |
| 授权 | 「我给你授权去做所有的」，接收 `2026-11-10T21:10:54+08:00`，sha `b1b9522b810a…`（`corrections.jsonl` 第 45 行 + `STATUS.md`） |
| 云端实跑 | **2026-11-10 21:55:40 CST**，日志 `/opt/quant/var/log/r7_p0_verify_20261110.log` |
| 部署备份 | `*.bak.20261110r7`（4 个被覆盖文件，云端留回滚件） |
| 边界 | 未改策略参数／`p13_forward.py`／sp4 配置 v1/v2／任何授权文件；未报单；`LIVE_ENABLED=false` 未变；11-09/11-10 原产物保留 |

---

## 1. 变更单第 4 节验收条款 → 实际实现与证据

| 条款 | 实现 | 测试 ID（实际） | 变异 ID | 「删除实现即变红」证据 |
| --- | --- | --- | --- | --- |
| AC-1 跨日推进写成功 | `write_daily_snapshot` 按 day 分文件 | `test_p0_snapshot_r7.py::test_ac1_cross_day_advance_writes` | M1 | 原 PASS → 变异体 FAIL（改回单文件） |
| AC-2 同日不同内容拒绝 + `--supersede` | `account_body` 只比 asof/source/cash/positions | `::test_ac2_same_day_different_account_rejected`、`::test_ac2_supersede_creates_rev2_with_reason` | M2/M3 | 原 PASS → 变异体 FAIL |
| AC-3 陈旧快照拒绝 | `load_daily_snapshot` 校验 `asof==day` | `::test_ac3_stale_snapshot_rejected` | M4 | 原 PASS → 变异体 FAIL |
| AC-4 `REV_CONFLICT` | 多 rev 账户内容比对 | `::test_ac4_rev_conflict_blocks_g2` | M5 | 原 PASS → 变异体 FAIL |
| AC-5 关键阶段 rc≠0 ⇒ `ok=false` + 告警 | `scripts/stage_rc_gate.py` 判定真源 + `daily_chain.sh` 收紧 | `test_daily_chain_rc.py::test_ac5_every_key_stage_is_enforced` | M6 | 原 PASS → 变异体 FAIL |
| AC-6 迁移幂等 | `migrate_legacy_snapshot` 已存在分支 | `::test_ac6_migration_idempotent` | M7 | 原 PASS → 变异体 FAIL |

**新增测试 22 条**（`test_p0_snapshot_r7.py` 13 + `test_daily_chain_rc.py` 9）；`scripts/p0_snapshot_mutation_test.py` **7/7 检出**，逐条打印「原实现 PASS → 变异体 FAIL」。变异脚本用临时副本 + importlib，**不原地改产品源码**。

⭐ **变异脚本同步（实施中发现的问题）**：改了 `shadow_daily_reconcile.py` 后，旧 `shadow_reconcile_mutation_test.py` 的 M2 锚点 `if str(d.get("asof", "")) != day:` 变成 **2 次** ⇒ 脚本判「变异定义失效」，一度 **10/11**。已把 M2 锚点带上下文唯一化、`TESTS` 增加 `test_p0_snapshot_r7.py`、新增 **M12**（新按 day 解析路径不校验 asof），重跑 **12/12 检出、恢复后 PASS（38 passed）**。这是「改产品代码必须同步变异脚本」的实例。

## 2. 云端实跑结果（一手日志）

| 项 | 结果 |
| --- | --- |
| `--migrate` | `status=migrated`，生成 `account_snapshot_20261109_rev1.json`；`legacy_sha256` 前后逐位相同 `fcaeec0d…`，`legacy_unchanged=true` |
| 迁移幂等 | 第二次 `status=already_migrated`，原件与副本 sha 均不变 |
| `r7_dryrun_check.py` | 四项全 PASS（cross_day_advance / rev_conflict / stage_rc_gate / migration_idempotent） |
| rc 闸门 | `paper_snapshot=4:关键` ⇒ `ok=false, alert=true`, rc=1（预期）；`=0` ⇒ rc=0（预期） |
| SHADOW `--date 20261110 --no-write` | `SELF_CHECK_ONLY`，原因 `no_daily_snapshot_file`，`计入G2=否`，5 笔计划单**未发送**；**未写任何记录** |
| rev 文件清单 | 仅 `account_snapshot_20261109_rev1.json`；**无 11-10 文件**（R7 2c 缺失日不补写） |
| 授权文件命中数 | **0** |
| 部署哈希 | 6 个文件云端 sha 与本地逐位一致 |

## 3. 实跑中发现并修复的权限隐患

`--migrate` 由 root 执行，`mkstemp` 默认权限 **600 / root:root** ⇒ 日更链与 SHADOW 以 `deepquant` 运行会**次日读不到** rev 文件（又一个「没检出≠没问题」）。

修法：`paper_account_adapter._atomic_write()` 在 `os.replace` 前显式 `os.chmod(tmp, 0o644)`；云端已把迁移产物 `chown deepquant:deepquant` + `chmod 644`。**以 `deepquant` 身份实读验证 rc=0**（`asof=20261109, rev=1, settlement_verified=unknown`）。修复后 adapter sha 由 `2f00f866…` 变为 `452ad200…`，已重新 scp 并比对。

## 4. DRYRUN 覆盖（R7-5）

新增 `scripts/r7_dryrun_check.py`（四项自检，临时目录、零副作用），接入 `daily_chain.sh --dry-run`（11-27 月末 DRYRUN 必经）。同时修掉一个引入的回归：DRYRUN 里 `run()` 不产文件，收紧后的 `universe_snap` 判失败会让演练永远 `exit 3` ⇒ 改为 **DRY=1 时关键阶段只记 note、不计 `ok`**，R7 自检由独立脚本负责。`bash -n` 与本地 `r7_dryrun_check.py` 均 PASS。

## 5. ⚠️ 偏差登记（须用户/审阅者知悉）

R7 授权范围为「`paper_account_adapter.py`、`shadow_daily_reconcile.py`、`daily_chain.sh` 及其测试」，**不含 `deploy/bin/heartbeat_alert.py`** ⇒ **未改告警脚本**。R7-3「17:40 告警改为读取阶段结果，不只读 ok」改由 `daily_chain.sh` 落地：心跳新增 `stages` 明细 + `stage_results.json` + `ok=false` + `stage_note`；现有 `heartbeat_alert.py` 读 `ok` 与 `stage_note`，据其报失败阶段明细 ⇒ **漏报已消除**，但「告警脚本直接读 `stage_results.json`」未实现。如需该项，请另行书面授权。

## 6. 回归与部署可证性

| 项 | 结果 |
| --- | --- |
| `scripts/tests` | **637 passed / 1 skipped**（615 + 新增 22） |
| backend | **3,651 passed / 1 skipped** |
| `property_checks` | **9/9 PASS** |
| `verify_exec_all` | **PASS** |
| 变异 | p0 **7/7**、shadow **12/12**、asof 12/12、channel 10/10、manifest 5/5、calendar 5/5、paper_adapter 全检出、guard 10/10、session 全检出 |
| 部署清单 | 45 → **47 文件**（新增 `stage_rc_gate.py`、`r7_dryrun_check.py`），本地/云端逐位 PASS |
| 通道隔离 | 云端 **FAIL 0 项** |

⚠️ 批量串行跑变异时，shadow 脚本末行曾打印「恢复后复跑：FAIL」（pytest 临时目录 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 清理计数 >50 干扰退出码）；**单独重跑为 PASS（38 passed）、12/12 检出**，源码 diff 为空。

## 7. 回滚

从 `/opt/quant/**/*.bak.20261110r7` 恢复 4 个被覆盖文件；移除新增 2 个脚本；`deploy_manifest.prev.json` 保留 45 文件基线。新增的 `account_snapshot_20261109_rev1.json` 保留不删，原 `account_snapshot.json` 全程冻结不动 ⇒ 回滚后 SHADOW 回到按 day 找不到当日文件 ⇒ `SELF_CHECK_ONLY`、**不会假 ALLOW**。11-09/11-10 原产物、修复前后日志一律保留。
