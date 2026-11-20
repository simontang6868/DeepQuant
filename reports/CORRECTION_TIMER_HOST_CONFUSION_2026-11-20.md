# 更正：复核报告"sim timer 未部署"结论有误——主机混淆（2026-11-20 23:10 CST）

> 原文不改写：本件更正 `reports/RECHECK_1201_READINESS_2026-11-20.md`（sha `e388c7de…`）§3/§4/§0 的结论。
> 性质：工程自证 `ENGINEERING_SELF_CHECK_ONLY`；未下单、未 enable 任何 timer、未碰实盘。
> 触发：账户持有人重发"再检查一遍，12-01 能不能跑通"；复核时对两台云主机状态做了交叉验证。

---

## 0. 一句话结论

**上一份复核报告的"关键缺口——sim timer 从未部署到生产机"是误判，现予撤回。** 根因是我把**异地备份机 110.40.190.225** 当成了执行机去查 systemd。真正的执行机是 **1.92.141.140**（2026-11-12 迁移后的新机），其 4 个 sim timer **已全部部署且正确保持 disabled**，核心脚本、授权、venv、日更链全部就位。**按现状，代码与触发器两层都已 ready；12-01 能否真报单仍取决于按日期发生的 PROBE/出证/全门 PASS 与届时 enable——不是工程缺口。**

---

## 1. 错在哪（如实记录，不改写原文）

| 项 | 上一份报告的说法 | 实测更正 |
|---|---|---|
| 查 timer 的机器 | 隐含当作执行机 | 实际查的是 **110.40.190.225**（cloud 裸仓库备份机） |
| sim timer 状态 | "0 unit files / not-found / 从未部署" | 那是备份机的状态；执行机 **1.92.141.140** 上 4 个 timer 全在、disabled |
| 核心脚本 | "sim_rebalance_executor 等 4 个 MISSING" | 同样是备份机；执行机上 4 个全 OK |
| 根因判断 | "bootstrap.sh 不含 sim 单元" | bootstrap.sh 确实只装 6 个日常单元属实，但 **sim 单元是通过迁移/手工另行装入执行机的**，备份机没有是正常的（它只做裸仓库异地备份） |
| §4 三选项 A/B/C | 请持有人拍板补部署 | **作废**，无需补部署 |

---

## 2. 为什么两台机器会混

- `deploy_manifest.py` / `check_channel_isolation.py` / `check_universe_mirror.py` / `g1_progress.py` 的 `REMOTE_HOST` 均硬编码 `root@1.92.141.140`，注释："2026-11-12 迁移：新机 1.92.141.140"。
- git remote `cloud` 仍指向旧机 `root@110.40.190.225:/opt/quant-backup/repo.git`——**裸仓库异地备份**，与执行机不是一回事。
- 上一轮我用 `ssh root@110.40.190.225` 查 systemd，恰好那台也残留了一个 `/opt/quant`（迁移前的旧部署，已停用），于是读到"有 env/venv 但没有 sim timer/脚本"的半残留状态，误判成执行机缺口。
- **教训（并入长期笔记）**：查生产状态前先以代码里的 `REMOTE_HOST` 常量为准确认执行机 IP，不要用 git remote 的主机名反推；git 备份机 ≠ 执行机。

---

## 3. 执行机 1.92.141.140 实测体检（2026-11-20 23:09）

| 检查项 | 结果 |
|---|---|
| 4 个 sim timer（prep/exec/sentinel/reconcile） | **已部署**，`systemctl list-unit-files` 列出 4 个 |
| enable 状态 | 4 个全部 `disabled`（VENDOR PRESET=enabled，但 STATE=disabled）——**正确，未提前武装** |
| OnCalendar 时点 | prep 11-27 17:20 / exec 12-01 09:35 / sentinel 12-01 09:45 / reconcile 12-01 18:10 |
| 核心脚本 | sim_rebalance_executor / sim_exec_result / sim_settlement_gate / sim_reconcile 全 OK |
| 授权文件 | `paper_authorization.json` OK |
| `.venv-gm` / `env` | 全 OK |
| 日更链 timer | `deepquant-daily.timer` active |
| 时区 | Asia/Shanghai |

结论：门控侧、出证/发布侧、**执行触发侧（timer 已装+未 enable）** 三段全部 ready。`Persistent=false`，错过不补跑，需到期 enable。

---

## 4. 更新后的 12-01 结论（回答"能不能跑通"）

**代码与触发器已就绪，无工程缺口。** 但"接入模拟盘"仍不是现在——按纪律必须按日期发生：

| 日期 | 事件 | 现状 |
|---|---|---|
| 11-20（现在） | 工程就绪 | 零报单；4 timer disabled；正确 |
| 11-25 | PROBE 买（2 只×1 手限价） | 通道就绪，触发时才下单 |
| 11-26 | PROBE 卖 + 去污 `positions=[]` | 已备好 |
| 11-27 17:00 | go/no-go：PROBE 干净 + 无 H 类 ⇒ enable prep timer | 检查点 |
| 11-30 17:10/17:20/17:40 | 台账 + 出证 + 邮件外发 + 冻结 | 断链 A 已通 |
| 12-01 09:35 | **预测全门 PASS 才 enable exec timer 报单** | 主演练；任一红线未达按条件 C 顺延 |

**"能跑通"的准确表述**：车道已全部铺好并验过（这次连触发器都逐项查实了），但车按时刻表发——11-25 才第一笔委托，12-01 才是主演练。我不会提前下单、不会提前 enable。

---

## 5. 引用口径

`StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`；`MaturityLevel=SIM_DRILL`；本件为工程就绪更正，不构成统计结论或样本外证据。上一份报告 §3/§4/§0 以本件为准；原文件保留不改写，作为误判留痕。

署名：simontang
