# U16-EXEC-2 实施交付报告（2026-11-19）

> 提交 `f837dc8`｜本地 = origin = cloud(110) = 新机备份 全部一致
> 授权逐字存证 `AUTO_EXEC_statement_2026-11-19.md`（sha `321d2092…`）
> 预登记 `U16_EXEC_PREREG_V2_2026-11-19.md`（sha `ec322be5…`），**先于任何实现代码冻结**
> 设计链：v1 `121fb0ec` → v2 `d33580d1` → v3 `52c4e744` → 专家四项裁决（全部吸收）

---

## 1. 做了什么

把 12-01 仿真调仓从"人工在场敲门"改造为"服务器端全自动"，共 26 文件 +1,724 行。

### 判据变更（按书面授权）
| 门 | 内容 | 验证 |
|---|---|---|
| **E0** | 真实系统时钟 ±10min + `NTPSynchronized=yes` | 云端实测同步、漂移 27µs；错日/失步均 FAIL |
| **E4-CORROB** | 旁证三条件：内嵌哈希 / commit / 双向集合全等（含增删检出） | self_check 6 项 PASS；M9A/B 两向真变异检出 |
| 失败日志 | `sim_exec_result/v1` 原子写 tmp+rename，全退出路径 try/finally | 哨兵四态自检 PASS |
| 验收第 9 条 | `execution_reports.created_at` ∈ 09:35±10min | DESCRIPTOR 实测 `ExecRpt.created_at` 存在；6 项测试 PASS |
| T3 | 结算完成性判别 + 退避重试（≤30min），不静默降级 | 自检 4 项 PASS |

### 工程实现
- 8 个一次性 systemd 单元：prep(11-27 17:20) / exec(12-01 09:35) / sentinel(09:45) / reconcile(18:10+)，全部 `Persistent=false`
- 将 `__REPO__` 替换为 `/opt/quant` 后部署到新机 `/etc/systemd/system/`，`daemon-reload` 完成
- **未 enable 任何定时器**：11-27/12-01 之前不会自动触发

---

## 2. 验收证据

| 项 | 结果 |
|---|---|
| 回归四连 | **3651/1**｜**9/9**｜**753/1**（原 739 + 新 14）｜`verify PASS` |
| 执行器 self-check | **PASS 12 项**（原 10 + `gate_e0` + `exec_result_4states`） |
| 旁证 self_check | PASS（base / 篡改哈希 / 过期 commit / 新增 / 删除 / 内容变化） |
| 结算门 self_check | PASS 4 项 |
| 真变异 | 执行器 **8/8** + M9 **2/2** = **10/10 检出**；恢复后 sha 逐位一致（`3fb0ba30` / `9a7932c5`） |
| manifest | 59 → **63 文件**，`sha_of_files=3b903a66`；本地 63/63 PASS、云端 63/63 PASS |
| 云端实跑 | self-check PASS；六门 `[E0 CLOCK_SKEW, E1 LEDGER_MISSING, E2 缺DRYRUN/preflight, E3 ✅, E4-CORROB MISSING, E4 FAIL]` ⇒ `--execute` 仍 rc=4 零报单 |
| 凭证扫描 | 暂存 diff 敏感模式 0 命中 |
| 文件同步 | 6 个核心脚本本地/云端 sha256 逐位一致 |

---

## 3. 边界与已知限制（引用时必带）

1. **定时器已部署但未启用**。11-27/12-01 前不会自动触发；`Persistent=false` 错过不补跑。
2. **11-30 前仍有一次人工前置**：本机出证 + 17:40 告警邮件外发旁证 sha256。12-01 当天零人工。
3. **旁证锚定是事后可检测（tamper-evidence）**，不是事前预防。单主机内不存在机器可验的外部锚。
4. **P1/P2 无法检验**：SDK 无 `client_order_id` 入参 ⇒ 幂等仅本地；无行情接口 ⇒ 重定价不可实现（`REPRICE_IMPLEMENTABLE=False`）。按预登记停机出更正，不改判据。
5. **`ExecRpt.created_at` 的服务端语义**须上线后核对；若为客户端时点则验收第 9 条失效并出更正（H9）。
6. `SIM_DRILL`，不计 G1/G2/S4，不作收益判断，`LIVE_ENABLED=false`，不碰任何授权文件。

---

## 4. 下一步（尚未做）

- 11-27 17:20：启用 prep timer，产出 DRYRUN；本机出证 + 邮件外发旁证 sha，冻结
- 11-30 17:10：日更链产出 FORWARD+D4 台账；preflight 事件驱动
- 12-01：启用 exec / sentinel / reconcile timer
- 全程证据归档 + 账户持有人终审

**本轮未启用任何定时器、未报单、未改授权文件、未碰 token。**
