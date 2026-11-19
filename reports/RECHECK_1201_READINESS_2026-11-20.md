# 12-01 全链路复核报告——推送确认 + 一次"再检查一遍"（2026-11-20 01:05 CST）

> 触发：账户持有人"记得推送，然后再检查一遍，到 12 月 1 号是不是能够跑通整个系统"
> 性质：工程自证 `ENGINEERING_SELF_CHECK_ONLY`；`SIM_DRILL`；未下单、未 enable 任何 timer、未碰实盘
> 基线：本地＝origin＝cloud裸仓库 = `78d492d`；报告原文不改写，本件为追加复核

---

## 0. 一句话结论

**推送已完成并三处校验一致；代码层通往 12-01 的链路全部实测通过。但本次复核抓到一个此前报告用措辞掩盖的真实缺口——4 个 sim 定时单元从未装到生产机，按现状 12-01 到点不会有任何东西自动触发。** 这不是"日期没到"的问题，是"触发器根本不在生产机上"。缺口可补，但涉及生产机改动与 timer 语义，我不擅自补，交你在 §4 拍板。

---

## 1. 推送确认（已完成）

| 通道 | 结果 | 证据 |
|---|---|---|
| origin (GitHub) | PASS | `44ac7b4..78d492d workbuddy/2026-10-20-consolidated -> ...` push rc=0 |
| cloud 裸仓库 `/opt/quant-backup/repo.git` | PASS | `workbuddy/2026-10-20-consolidated 78d492d`；push rc=0（Everything up-to-date） |
| 本地 HEAD | `78d492d` | 三处 sha 一致 |

- 附带发现：cloud 的 SSH 默认密钥不对（`Permission denied publickey`），须显式 `ssh -i ~/.ssh/fk_server_ed25519`。推送本身成功，但**后续自动化脚本若要直连 cloud 备份，需固化该密钥路径**，否则会静默推不上去。
- 工作区未提交项均为历史遗留（6 个 `.bak.*` 备份 + `sim_exec/` 测试产物），本轮未新增改动。`PROPERTY_CHECKS.json` 被 property_checks 重跑写成 CRLF（Windows 换行坑），已复核内容一致后恢复为 LF（CRLF 68→0），未提交。

---

## 2. 代码链路复核（全部实测通过）

| 检查项 | 结果 | 说明 |
|---|---|---|
| 回归四连 | **3651/1｜9/9｜759/1｜verify PASS** | 全部本机实跑，rc=0。一次 17failed/469errors 系并发共享临时目录竞争，串行重跑归零（单次定点测试三种 basetemp 均 PASS 佐证） |
| 关键脚本 sha256 | 逐位一致 | 5 件（执行器 `0445a42c`／结果 `c80d0e8c`／旁证 `d4bea41b`／PROBE `705d3ace`／告警 `52202ea3`）与 manifest、交付报告三方吻合 |
| manifest 云端一致性 | 65/65 PASS | `deploy_manifest.py --verify --remote`：云端 `/opt/quant` 逐文件 sha256 一致；commit 字段显示 `44ac7b49` 仅因生成时机，不参与校验 |
| E4 隔离门 | PASS | `check_channel_isolation.py --remote`：FAIL 0 项；真实通道授权/环境变量命中 0；`LIVE_ENABLED` 未设置 |
| 六门判定 | 符合预期 | E0 `CLOCK_SKEW`／E1 `LEDGER_MISSING`／E2 `MISSING:DRYRUN,PREFLIGHT`／E3 `NO_AUTH`（授权在云端）／E4-CORROB `CORROB_MISSING`／E4 PASS —— **全部是时间依赖或位置依赖，无一条工程断链** |
| PROBE / 旁证自检 | PASS | PROBE `date_buy_ok,date_wrong_detected`；旁证 `base_ok` + 5 项篡改检出 |
| 生产路径卫生 | PASS | 无生产名 `20261201_result.json`，5 个 `.TEST_` 隔离产物 |

**结论**：交付报告声称"工程障碍全部清除"在**脚本/门控/出证代码层**属实；云端跑的确实是本轮修复版（脚本哈希逐位一致），不是旧代码。

---

## 3. ⚠️ 本次新发现：sim 定时单元从未部署到生产机

这是"再检查一遍"抓到的、此前 `--gates` 与回归测试都覆盖不到的缺口。

### 3.1 实测证据（云端 `/opt/quant`，2026-11-20 01:04）
- `systemctl list-unit-files 'deepquant-sim-*.timer'` → **0 unit files listed**
- `deepquant-sim-{prep,exec,sentinel,reconcile}.timer` → 全部 `not-found`
- `/etc/systemd/system/` 下只有 3 对日常单元：`deepquant-daily`／`deepquant-alert`／`deepquant-calendar`
- 云端部署目录 `/opt/quant/deploy/systemd/` 下 **连 sim 单元源文件都没有**（`NO_SIM_UNITS_IN_REPO`）

### 3.2 根因
- `deploy/bootstrap.sh` 第 187-192 行的安装循环**只列了 6 个日常单元**，不含任何 `deepquant-sim-*`。
- 本地仓库 `deploy/systemd/` 有完整的 8 个 sim 单元（4 对 service/timer），但从未被同步到生产机。
- 这正是记忆里 "/opt/quant 无 .git、部署目录非工作副本；改脚本/加文件必须另 scp 并云端实跑" 的具体实例。

### 3.3 为什么此前所有检查都漏了它
- `manifest` 的 65 个文件**全是 Python 脚本，0 个 systemd 单元**——"65/65 PASS"在结构上不可能覆盖触发器。
- E4 门只校验脚本哈希与通道隔离，不检查 timer 是否 `systemctl` 可见。
- 回归测试跑的是 Python 逻辑，与生产机 systemd 状态无关。
- 交付报告写"8 个 systemd 单元已部署（未 enable）"——**措辞失真**：实际情况是"8 个单元文件已写入本地仓库 `deploy/systemd/`"，从未安装到生产机。

### 3.4 后果
若不在 12-01 前补齐：即使 11-25/26 PROBE、11-27 go/no-go、11-30 出证全部顺利、全门 PASS，`systemctl enable deepquant-sim-exec.timer` 也会因单元不存在而失败，**12-01 09:35 无任何自动动作 = 白跑**。这比"门控失败零报单"更糟——是"到点根本没人起床"。

---

## 4. 待账户持有人拍板（我不擅自做）

生产机环境本身齐备（`.venv-gm` 软链、`env` 文件属主 deepquant、运行用户 uid=993 均在），补齐技术上可行。以下三项需你定：

| 选项 | 动作 | 风险/代价 | 我的建议 |
|---|---|---|---|
| **A（推荐）** | 11-30 出证那次一并 scp 8 个 sim 单元到 `/etc/systemd/system/`、`daemon-reload`，**仍不 enable**；enable 留到 12-01 全门 PASS 当天 | 仅放置文件、不触发任何执行；与既定 11-30 部署窗口合并，无额外窗口 | 采纳。消除"到点无人起床"，又不提前武装 |
| B | 现在立即 scp + daemon-reload（不 enable） | 同上，只是更早；不违反"不提前 enable"纪律 | 可接受，但无必要抢跑 |
| C | 不补，12-01 当天手工前台跑一次 `--gates`／`--execute` | 放弃自动化织物验证；手工触发违背"全自动"目标；且 E0 时钟门在手工时点未必 PASS | 不建议 |

**三条红线不变**（无论选哪项）：
1. **不提前下单**：11-25 PROBE 买、11-26 卖+去污才发生。
2. **不提前 enable timer**：只在 12-01 09:35 预测全门 PASS 时才武装 exec timer；任一红线未达按条件 C 顺延。
3. **PROBE go/no-go 仍是前置**：11-27 17:00 检查 PROBE 干净 + 无 H 类。

---

## 5. 时间线现状（今天 11-20，下步动作）

| 日期 | 事件 | 当前状态 |
|---|---|---|
| 11-20 | 推送 + 本复核 | 完成；零报单，timer 全 disabled |
| 11-25 | PROBE 买（2 只×1 手限价） | 通道就绪，触发时才下单 |
| 11-26 | PROBE 卖 + 去污 | 已备好 |
| 11-27 17:00 | go/no-go | 检查点 |
| 11-30 17:10/17:20/17:40 | 台账 + 出证 + 邮件外发 + 冻结 | 断链 A 已通；**§4 选项 A 的单元部署并入此处** |
| 12-01 09:35 | 全门 PASS 才 enable exec timer 报单 | **前提：sim 单元已部署** |

---

## 6. 引用口径

`StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`；`MaturityLevel=SIM_DRILL`；本件为工程就绪复核，不构成任何统计结论或样本外证据。所有"已部署"类表述今后统一按三段式：门控侧已实现／出证发布侧已实现／**执行触发侧（timer 部署+enable）未落地**。

署名：simontang
