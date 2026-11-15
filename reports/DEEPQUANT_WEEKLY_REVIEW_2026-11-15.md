# DeepQuant 一周进展全面审阅报告（2026-11-08 ~ 2026-11-15）

**生成时间**：2026-11-15 13:2x CST
**覆盖区间**：2026-11-08（周日）～ 2026-11-15（周日）13:20 CST，共 8 个自然日 / 5 个工作日（11-09~11-13）
**作者**：DeepQuant 执行 Agent（**自审**）
**用途**：交账户持有人指定的第三方专家做**全面审核**
**性质声明**：⭐ 本报告是**执行方对自身一周工作的证据链汇总与自审，不构成独立审查**。请按 `docs/REVIEWER_GUIDE_2026-11-05.md` 与本报告 §10 自行复算；签名只对你自己算出来的数负责（R5）。
**研究类型**：证据综述 / 工程与运维审计。**本周没有新预测、新因子、新模型、新调参、新实盘**；唯一一次回测是 2026-10 单窗口描述性归因，已明确标为不可引。

---

## 0. 阅读须知（审核方法与免责）

**证据等级标记**（本报告每条关键结论后标注，便于你按优先级复核）：

| 标记 | 含义 | 复核方式 |
| --- | --- | --- |
| 【一手·云端】 | 2026-11-15 本报告生成前经 SSH 只读实查 | §10 给出命令，可重跑 |
| 【一手·产物】 | 仓库内落盘的 JSON / 日志 / 哈希文件 | 按路径打开逐字比对 |
| 【文档】 | 既有报告、工作日志、`STATUS.md` 的断言 | 按引用路径复核，**不等于已验证** |
| 【推断】 | 基于以上事实的解释或机制判断 | 可能被证伪，须单独判断 |

**三个不能混的「时间」**（外部审阅最易误读处）：

1. 系统连续运行 **11-03 起 9 个工作日**（本周 11-09~11-13 五个）；
2. 历史回测覆盖 **237 个月，全部样本内**；
3. 前瞻样本外从 **11-30 首个含 `target_holds_d4` 的 FORWARD 台账**才起算，目前 **0 个月**。

**引用规范**（沿用 `.workbuddy/memory/RESULTS_INDEX.md`）：任何数字须带「判定 + 统计口径 + 成熟度 + 预登记哈希 + 产物目录」。本周新产物一律标 `StatisticalVerdict` / `MaturityLevel`，见 §9。

**本报告不含**：凭证值、token、账户 ID 明文、心跳 ping URL 完整值、任何可触发报单的命令。所有云端命令均为只读。

---

## 1. 结论先行（12 条）

1. **本周主线**：把「仿真账户」从概念接成**可审计的只读链路**，并在过程中连续发现并修掉三个会污染证据链的缺陷（R6-1 路径分叉、P0 快照停滞、服务器迁移工具链指向旧机）。【文档 + 一手·云端】
2. **系统能用、工程基本好用；「能不能赚钱」仍既不能证明能、也不能证明不能**。10 万本金真实费率下预期年化 **7.50%–7.66%**，低于项目自设 **8%** 门槛；可引 7 条结论属 exQ5 线、**不是将要交易的 sp4/d4**，且 `S4 0/7` 表示没有任何样本外证据。【文档；见 `reports/STAGE_CONCLUSION_2026-11-14.md`】
3. ⭐ **本周最重要的工程事件 = P0 缺陷 `PAPER_SNAPSHOT_STALE_FROM_DAY2`**：11-10 17:12 快照跨日推进触发 `rc=4`，日更链仍报 `ok=1`、17:40 告警仍报「正常」、备份包照打包陈旧快照。按「变更单先行 → 授权存证 → 实现 → 回归/变异 → 云端实跑」修复（R7）。**该缺陷使「G2 最早 12-25 可判」被撤回**。【文档 + 一手·云端：归档日志 `daily_20261110.log.1` 有 `PAPER_SNAPSHOT_RC=4`】
4. **服务器迁移（11-12）**：独立只读核查发现 4 个核查脚本 + `git remote cloud` 仍指旧机、本地运行脚本落后新机 2 个修复、裸仓库 `HEAD→master` 导致季度演练必然 FAIL、两机 journal 分家。**D1/D2 已实施并云端实跑**；D3（G1 两机合并口径）**未立**，故本周起**不对外引用任何 G1 计数**。【文档 + 一手·云端】
5. **授权链**：U-15 书面声明（11-09，sha `3207bff41a58…`）、PA-GUARD-1 预登记与六项校验器、`paper_authorization.json`（11-14 由账户持有人本人创建并定稿，sha `3a3c35a3…`，`ALLOW 6/6`）。⭐ **`ALLOW ≠ 可报单`**：11-30 前无可执行目标、U-16 未给、真实报单适配器未实现。【一手·云端 + 一手·产物】
6. **11-14 EARLY-IMPL-1**：按账户持有人「按『做这个』授权提前实现」，**在写代码之前**冻结预登记（sha `a6f5a9be…`），实现只读外部真值源 / 成交回报对账器 / 五项演练离线驱动；**不接日更链、不执行、不报单**。`--execute` 恒返回 `NOT_AUTHORIZED`（rc=4）。【一手·产物 + 一手·云端】
7. **回归与部署可证性持续全绿（截至 11-15 实测）**：`backend 3,651/1`｜`property_checks 9/9`｜`scripts/tests 680/1`｜`verify_exec_all PASS`｜真变异 `11/11`｜部署清单 **50/50 逐位一致**｜通道隔离 **FAIL 0 项**。测试数本周从 475 增至 680，每个台阶都有对应事件（§4）。【一手·云端】
8. **硬边界本周始终未破**：`LIVE_ENABLED=false`（云端未显式配置，隔离判 PASS）、无 `live_authorization.json`、**零委托零成交**、`daily_chain.sh` 在 EARLY-IMPL-1 中零改动、正式台账仍只有两份 `LEGACY_NO_D4`、未改策略参数 / `p13_forward.py` / sp4 配置 v1·v2。【一手·云端】
9. ⭐ **三个尚未闭环的证据/运维风险（本周新发现，请勿当作已解决）**：① 云端 `corrections.jsonl` 停在 11-05（17 行），本地已 51 行，**每日机外备份打的是旧版**；② `logrotate.service` 11-14、11-15 连续失败；③ 11-14 全部产物**未提交 git**、`origin` 落后本地 3 个提交。详见 §7。【一手·云端】
10. **G1 / G2 / S4**：G1 原口径 6/10 仅作历史记录且因换机失效；**G2 0/20**（`LEGACY_NO_D4` 不计）；**S4 0/7**。三个"判定日/可判日"本周均被撤回或改为条件式，**没有「验证完成日」**。【文档】
11. **11-30 / 12-01 前置仍缺 4 项**：11-30 `mode=FORWARD` + `target_holds_d4` 台账、11-27 月末 DRYRUN、U-16 书面放行 + 另立执行预登记、**gm-paper 真实报单适配器**（本周只有只读真值源 + 离线 broker）。【一手·产物】
12. **请审核者优先看 §6（失败与撤回）、§7（未闭环风险）、§11（待判断问题）**——这三节是本周最可能被"全绿"掩盖的部分。

---

## 2. 一周时间线

| 日期 | 触发 | 主要工作 | 关键产物 / 提交 |
| --- | --- | --- | --- |
| 11-08 上午 | 阶段/模拟盘问答；「按你说的，接入」 | G1 同步 4/10；**SHADOW 对账真正接入日更链**；免费模拟盘选型评估 | `scripts/shadow_daily_reconcile.py`、`docs/PAPER_TRADING_API_OPTIONS_2026-11-08.md`；`f934ec9`、`7bd184c` |
| 11-08 凌晨~中午 | 「帮我注册并接入掘金」 | 不代注册；只读适配器 + 3a 段 + 备份；**gmtrade cp311 wheel 坑** → `.venv-gm` CPython 3.10；用户配好凭证后实测接通 | `scripts/paper_account_adapter.py`；`3e60e16`、`2198a36` |
| 11-08 12:50 | 「阶段性总结报告给另一位专家」 | `STAGE_SUMMARY`（7 页，md+docx）；三层验证区分；官网原文纠错 | `reports/STAGE_SUMMARY_2026-11-08.{md,docx}`；`c7fb586` |
| 11-08 13:50 | R6 七条收口 | 授权方案 / 演练方案（仅设计）/ 部署 manifest / 日历补刷 / 整手决策材料 / token 轮换验收 / 报告体例 | `d0e8394`、`0cb3907`；回归 560/1、清单 44/44 |
| 11-09 11:03 | 「U-15/U-16 怎么弄」 | ⭐ 抓到 `CONFIG_AUTH_PATH_MISMATCH`（授权文件路径分叉）；U-15/U-16 填写指引 | `2aab998`、`ec69635`；清单 44→45 |
| 11-09 11:16 | 用户给 U-15 书面声明（8 条） | 原文存证 → PA-GUARD-1 预登记 → 六项校验器（纯判定、失败关闭）→ 模板；**刻意不实现报单** | `cc82b7f`、`12c262d`；清单 45/45 |
| 11-09 17:10~21:00 | 首次完整日更链 | 11 环节全 rc=0；首条真实快照；首次 `REAL_PAPER`；G1 5/10；首次运行报告 | `reports/FIRST_FULL_RUN_REVIEW_2026-11-09.{md,docx}`；`702ec47` |
| 11-09 21~23 点 | 容量 / 清理 / 10 月回测 | 只读容量诊断（增速 ≈3.2 MiB/交易日）；用户授权删 4 个部署 tar（1.49 GiB）；10 月下跌月任务书（阶段 0/1） | `docs/DEPLOY_TARBALL_CLEANUP_2026-11-09.md`、`docs/OCT2026_DOWNTURN_RESEARCH_DESIGN_2026-11-09.md`；`51a62a8`、`519ea37` |
| 11-10 凌晨 | 「继续」 | 冻结 `PA-OCT2026-1` 并跑双口径描述性归因；**P3 按预登记字面被反驳**；Q5 归因后由 R7 独立复算修正 | `oct2026_out/cd5d016b8c9f/`；`45851e6` |
| 11-10 20:32 | 「结合模拟盘数据全面报告给专家」 | ⭐⭐ **抓到 P0 快照停滞缺陷**（`corrections.jsonl` 第 44 行）；14 节综合报告 + 15 条复算命令 | `reports/DEEPQUANT_COMPREHENSIVE_REVIEW_2026-11-10.{md,docx}`；`41938c9`、`90db703` |
| 11-10 21:06~22:00 | 评审 Q1–Q7 + R7 修订；「我给你授权去做所有的」 | 授权原文存证（sha `b1b9522b…`，标泛授权歧义）→ ECR-P0-SNAPSHOT-1 先行冻结 → P0 修复 → 报告 rev2 → 云端实跑 → 清单 47/47 | `f7d9a5f`、`6559ab4`、`953fd8d` |
| 11-12 上午 | 换服务器交接报告 | 独立只读核查（新旧两机、47 文件逐位、邮件外部真值、bundle→clone 演练）；**D1/D2 协同单** | `docs/SERVER_MIGRATION_VERIFICATION_2026-11-12.*`、`docs/SERVER_MIGRATION_HANDOFF_TO_AGENT_2026-11-12.md`；`8f1b15d` 等 |
| 11-12 10:40~11:02 | 迁移 Agent 回执「可以开始」 | D1/D2：4 脚本换 HOST/KEY、`git remote cloud` 切新机、回灌两处运行脚本修复；云端实跑；清单 47/47 | `40cfb2d`、`5a9bd6c`、`9f76f66`（本地 HEAD） |
| 11-12 11:10~16:01 | 「连上网站」→ 纠错 → hc-ping 截图 | 误开本地工作台（已关）；打开 healthchecks.io 后台；**截图证实 Period=1 day / Grace=3 hours 已达标 ⇒ 待办撤销** | `docs/R5_FIRST_REAL_RUN_EVIDENCE_2026-11-12.md` |
| 11-12 17:10~21:12 | 换机后首次真跑 | R5 四样证据卡；`NOT_DUE` 无交易；两处判据按字面不成立已如实登记 | `docs/R5_FIRST_REAL_RUN_EVIDENCE_2026-11-12.md` |
| 11-14 21:20 | 「跑了这么久，能不能赚钱」 | 阶段结论：能用 / 基本好用 / 赚钱无答案；发现 logrotate 失败与日志轮转 | `reports/STAGE_CONCLUSION_2026-11-14.md` |
| 11-14 22:20~22:54 | 「接上模拟盘跑跑」→ 授权文件 | 只读连通实测；四道闸门；手把手操作指引；**用户本人创建并定稿授权文件**（占位、`read` 报错两次均保留） | `reports/PAPER_CHANNEL_PROBE_2026-11-14.md`、`docs/PAPER_AUTH_FILE_STEP_BY_STEP_2026-11-14.md`、`docs/PAPER_AUTH_READBACK_2026-11-14.md` |
| 11-14 22:57~23:00 | 「为什么不能 11-16 演练」→「做这个」 | 决策时点答疑；两项只读预检（PASS 12/FAIL 0/PENDING 1、47/47）；变更单 DRAFT | `docs/ENGINEERING_CHANGE_EARLY_DRILL_PREP_2026-11-14.md` |
| 11-14 23:03 | 「按『做这个』授权提前实现」 | **EARLY-IMPL-1**：存证 → 预登记冻结 → 实现 → 回归四连 + 11/11 真变异 → scp 云端自检 → 清单 50/50 | `reports/EARLY_DRILL_PREP_RESULT_2026-11-14.md`；变更单 §8 |
| 11-15（本报告） | 外部专家审核 | 云端只读实况复核 + 最终回归补齐（`680/1`、50/50、隔离 PASS） | `reports/DEEPQUANT_WEEKLY_REVIEW_2026-11-15.{md,docx}` |

**本周产物规模【一手·产物】**：`docs/` + `reports/` 下 11-08 之后新增/修改 **48 个文件**（30 md / 11 docx / 3 sha256 / 2 txt / 2 json）；`scripts/` 下有 **43 个 `.py`** 文件时间戳更新（含本周新建的 3 个 EARLY-IMPL 模块、D1/D2 适配、10 月研究脚本，以及各类变异测试与单元测试；⚠️ 按 mtime 计，**不等于 43 个功能新增**）；11-08~11-12 共 **28 次 git 提交**，最后一个 `9f76f66`。

---

## 3. 专题进展

### 3.1 仿真通道：从选型到只读闭环（11-08）

- **选型**【文档】：硬需求 = 能 API 读 cash/positions + 沪深 A 股 + Linux 云主机 + 免费。首选掘金量化仿真（独立线上服务、柜台级撮合）；次选聚宽（平台内模拟，本机拿不到账户）；本地兜底 vnpy；最终实盘通道仍是 QMT（U-12）。
- **注册不代做**：token 官方定义为「相当于用户 ID+密码」，注册/建账户/生成 token 须本人完成。由此产生 **U-14（token 曾明文出现在对话，建议官网轮换）**，工具 `scripts/verify_token_rotation.py` 已就绪，**至今未轮换**。
- ⭐ **gmtrade ABI 坑**【文档】：官方写「Python 3.6+」，但 PyPI 只有 cp36~cp310 wheel、无 sdist；把 `c_sdk.cpython-310` 改名按 3.11 加载 ⇒ **段错误 rc=139**。最终用 CPython 3.10.22 + `.venv-gm` 解决。教训：**文档版本范围 ≠ 实际 wheel tag，不要改 ABI 文件名绕过**。
- **只读适配器**：仅 `set_token / set_endpoint / account / login / get_cash / get_positions`，源码零 `order_*`；`Cash` 无 `total` ⇒ `total = available + frozen` 显式算术；`Position.symbol` 形如 `SHSE.600990` ⇒ 转 6 位代码；缺凭证 / 非交易日 / 字段不认识一律失败关闭。
- **实测（11-08 12:40）**【文档】：`api.myquant.cn:9000` OPEN、登录成功、账户初始 **100000 元 / frozen 0 / 持仓 0**；`--probe` 与 `--no-write` 在 root 与 deepquant 两种身份均 rc=0。凭证值未进仓库/日志/文档（提交前 `git diff --cached | grep` 计数 0）。

### 3.2 SHADOW 接入与「接入管道 ≠ 开始计数」

- `scripts/shadow_daily_reconcile.py` 三档：`REAL_PAPER` / `SELF_CHECK_ONLY` / `NO_INPUTS`；⭐ **不许自证**：无真实账户快照时照跑但 `counts_toward_g2=false`。
- 计入 G2 的四个条件（缺一不可）：真实账户快照（asof=当日、source 非空）、无未解释差异、**生成日 == 交易日**、非 `LEGACY_NO_D4`。
- ⭐⭐ **首轮真变异 M1（去掉「必须有真实账户」）漏检**：所有测试都用 `LEGACY_NO_D4` 台账被"老台账不计 G2"兜住，效果被完全吸收；而 11-30 之后恰是「d4 台账 + 无账户」——洞正好落在最关键那天。补 `test_d4_ledger_without_account_still_never_counts` 后全检出。
- ⭐ **G2「约 12-01 可判」撤回**：缺的是「实际资金/持仓」这个外部输入，不是时间；改为「接入仿真账户后的第 20 个交易日」，随后又补上 `LEGACY_NO_D4` 条件 ⇒ **倒计时须等 11-30 首个含 `target_holds_d4` 台账**。
- ⭐ **G2 判据本身不校验实际成交**：比对的是「台账目标 vs 规划出的买单」，`AccountSnapshot` 只用于算股数。要验证执行须另行授权向仿真账户报单（这正是 12-01 演练的目的）。

### 3.3 U-15 授权与六项闸门（11-09）

- 原文逐字存证 `forward/authorization_statements/U15_statement_2026-11-09.md`（sha `3207bff41a58…`）。⭐ `max_notional` 按字面 **100000** 登记，不擅自压低到模板建议的 20,000–30,000；仍受「≤ 实时 available」与单笔+累计双校约束。声明 ⑧ 明确「不是 12-01 演练的执行许可」。
- 预登记 `PA-GUARD-1`（sha `1e48091a…`）冻结六项判据与 `sha256` 规范化口径（去掉 `sha256` 字段后 `sort_keys + separators=(",",":") + ensure_ascii=False` 的 UTF-8 字节）。
- `scripts/paper_authorization_guard.py`：纯判定、无网络、无 `order_*`、不读凭证；文件缺失/不可解析 ⇒ `DENY:NO_AUTH`；时间必须 `--now` 显式注入且带时区；哈希用 `hmac.compare_digest` 与独立重算；金额同时吃单笔/累计/available。**29 测试 + 10/10 真变异**（每条 DENY→ALLOW）。
- ⭐⭐ **`CONFIG_AUTH_PATH_MISMATCH`**：检查器原查 `/opt/quant/forward/paper_authorization.json`，云端实测该目录 MISSING；真实路径是 `/opt/quant/research/stock_pick_2026-10-25/forward/`。**27 条测试全绿却没覆盖路径分叉**——「测试全绿 ≠ 验收项覆盖」的采集层案例。修为单一常量 `PAPER_AUTH_REL`，补真调 `collect_local` 的反例测试与真变异 M10。

### 3.4 首次完整日更与 R5 证据（11-09 / 11-12）

| 项 | 11-09 首次完整运行 | 11-12 换机后首次真跑 |
| --- | --- | --- |
| 日更链 | `mode=NOT_DUE ok=1`，11 环节全 rc=0 | 真跑段 `dry_run=0`，17:14:27 `mode=NOT_DUE ok=1` |
| 行情 / 宇宙 | 5,472 只 / READY 5,215 / flag 0；宇宙 5,444、有报价 5,207(95.6%) | 备份 30 文件 45,219 B |
| 快照 / SHADOW | 首条真实快照 `asof=20261109`；首次 `REAL_PAPER`，diffs 1 条 `reason=match`、未解释 0、`counts_toward_g2=false` | 仿真账户 cash 100000 / positions 0；shadow `LEGACY_NO_D4` 不计 |
| 外部真值 | QQ 邮箱 3 封（备份带附件 / 正常 / 周包）、心跳 ok | QQ 邮箱 17:14 备份 + 17:40 正常；17:14:25 外部心跳已上报 |
| systemd | 首次完整链路 | `ExecMainStatus=0`、`Result=success`、`--failed` 0 |

⭐ **两处按字面读会误判，已专门对冲**：① 11-12 日志**第 1 行**是 00:29 `dry_run=1`（演练与真跑追加写同一文件），真跑段首行是第 19 行 `dry_run=0` ⇒ R5「首行须为 dry_run=0」按整份文件字面不成立，按真跑段成立；② `sent.json` 在 `/opt/quant` maxdepth 6 **未定位到** ⇒ 「无重复发信」该条未验证，改用邮箱外部真值。**没找到 ≠ 没问题**。

### 3.5 2026-10 下跌月描述性归因（**不可引**）

- 预登记 `PA-OCT2026-1`（sha `cd5d016b8c9f…`）**冻结前**把草案「二选一」改成两口径并列，消除择优空间：A `V1_HISTORICAL_REPLAY`(w=0.25)、B `V2_EX_POST_REPLAY`(w=0.20，**11-03 后验配置重演 9-30、非样本外**)。
- 主结果【一手·产物 `oct2026_out/cd5d016b8c9f/metrics.json`】：全池 10-08→10-30 **−11.5648%**（`exec_all` 独立 −11.6670%，差 0.10pp）；持股端 **−12.3420%**、α **−0.7772pp**；A 月净 −2.9635%/MDD −3.6884%；B 月净 −2.3314%/MDD −2.9210%；`|R_B|/|R_pool| = 0.1998 ≈ w=0.20`。
- ⭐⭐ **P3 按预登记字面被反驳**：原文「`MDD(B) < MDD(A)` 且 `MDD(B) > −5%`」，第二子句证明 MDD 用**负值**约定 ⇒ 第一子句 `−2.9210 < −3.6884` 为假。按 **L3ac D1「预登记有缺陷也不改」**：按字面执行、报 `P3_REFUTED_BY_LITERAL_SIGN`、不改写原文、不放宽判据；另立缺陷记录段补记「按幅度口径 2.9210% < 3.6884% 机制成立」。
- ⭐ **MDE 不编造**：n=1 ⇒ `MDE_NOT_ESTIMABLE_FROM_THIS_EXPERIMENT`；只报借用量级（不同信号，禁止与 α 并列判显著）。
- ⭐ **Q5 独立复算推翻审阅者反推归因**（R7）：`0.936243` 是**现金利息抵消**（利息 157.377049 元 = +0.157377pp），不是整手投入不足；`0.838840` 只属 A 的整手副口径。采纳「0.1998≈w 不能证明线性缩放」但**修正机制解释**。
- 结论：**描述性、单窗口、`EXPLORATORY_ONLY`、不可引、不改 S4 0/7**；「抗跌来自仓位（80% 现金）非选股」的既有结论不变。

### 3.6 ⭐⭐ P0 缺陷与 R7 修复（本周最重要工程事件）

| 阶段 | 事实 |
| --- | --- |
| 现象（11-10 20:35） | 3a `paper_snapshot` **rc=4**「当日快照已存在且内容不同，拒绝覆盖」；快照停在 `asof=20261109`；3b 退回 `SELF_CHECK_ONLY`；**日更链仍 `ok=1`、17:40 告警仍报「正常」、备份包照打包陈旧快照** |
| 根因 | 旧 `OUT` 单文件 + `asof` 写进内容 + `write_snapshot()` 对「文件存在且内容不同」一律 `EXIT_CONFLICT=4`（本为**同日重跑幂等**）⇒ 跨日 asof 推进必然触发；测试只覆盖同日冲突 |
| 影响 | SHADOW 硬要求当日快照 ⇒ 11-30 后即便有 d4 台账，**G2 也永远 0/20** ⇒ 「最早 12-25 判 G2」撤回 |
| 授权 | 用户 21:10:54「我给你授权去做所有的」逐字存证（sha `b1b9522b…`）；⭐ **如实标注泛授权歧义**：未点名 P0 / 未列范围 / 未列有效期 ⇒ 按 R7 自身已列范围执行，**不解释为解禁实盘/报单/策略参数/授权文件** |
| 变更单先行 | `docs/ENGINEERING_CHANGE_P0_SNAPSHOT_2026-11-10.md`（ECR-P0-SNAPSHOT-1，sha `5c56ceb0…`）**冻结于任何代码改动之前** |
| 实现 | 按日期分文件 `account_snapshot_YYYYMMDD_rev<N>.json` + 原子 `latest.json`（`os.replace`，非符号链接）+ 冲突只比 `account_body` + `--supersede` / `--migrate` + `collected_at` / `settlement_verified=unknown`；`shadow_daily_reconcile.py` 加 `load_daily_snapshot(day, dir)` 与 `REV_CONFLICT`；`daily_chain.sh` 关键阶段 rc 收紧 `ok=false` + `stage_results.json` + 心跳 `stages` |
| 只读核查 Q1(c) | 现有 **5 个阶段**非零 rc 被忽略（1.5 / 3a / 3b / 4b / 5），其中 **3 个连 stage_note 都不写** ⇒ 比审阅者预期更广 |
| 云端实跑 | `--migrate` 生成 `20261109_rev1`、原单文件 sha `fcaeec0d…` 前后不变、第二次 `already_migrated`；DRYRUN 四项 PASS；rc 闸门 4⇒rc1 / 0⇒rc0；SHADOW 判 `no_daily_snapshot_file` 不补写 |
| ⭐ 实跑抓到权限隐患 | `--migrate` 由 root 跑，`mkstemp` 默认 `600 root:root` ⇒ 日更链以 deepquant 运行会次日读不到。`_atomic_write` 加 `os.chmod(tmp, 0o644)`，**以 deepquant 身份实读 rc=0** |
| 偏差登记 | R7 授权范围不含 `deploy/bin/heartbeat_alert.py` ⇒ **未改告警脚本**；改由 `daily_chain.sh` 写心跳 `stages` + `ok=false` + `stage_note`，漏报已消除；「告警脚本直接读 stage_results.json」未实现，需另行授权 |
| 验收 | `scripts/tests 637/1`（615+22）、backend 3,651/1、property 9/9、verify PASS；`p0_snapshot_mutation_test.py` 7/7；清单 45→47、本地/云端 47/47 PASS |

### 3.7 服务器迁移与 D1/D2（11-12）

- ⭐⭐ **P0-A 工具链全指向旧机**：`g1_progress.py` / `deploy_manifest.py` / `check_channel_isolation.py` / `check_universe_mirror.py` 与 `git remote cloud` 均为旧 IP + 旧密钥；新机用 `~/.ssh/fk_server_ed25519` 才通，交接报告「沿用原密钥」不成立。`--verify --remote` 报的 47/47 是**旧机**。
- ⭐⭐ **P0-B 本地代码落后**：新机 47 文件 **45/47 一致**，仅 `daily_chain.sh` 与 `notify_mail.py` DIFF（云端已有两处修复，本地仍是带 bug 旧版）——又是「`/opt/quant` 非 git 工作副本，`git push cloud` 只做异地备份、改 `deploy/bin/` 必须另 scp」的老坑。
- ⭐ **11-11 业务失败**：在旧机跑，`ok=0`、`is_due_RC=1`、17:40 告警邮件「日更链失败」实证 ⇒ R7 收紧生效、该日不计 G1。
- ⭐ **裸仓库 symbolic HEAD**：`HEAD→refs/heads/master`（不存在），`bundle/verify/clone` 全 rc=0 却 clone 检出 **0 文件** ⇒ 季度演练必判 FAIL。由迁移 Agent 修为 `workbuddy/2026-10-20-consolidated`（我方独立复算：`rev-parse 953fd8da…`、`git ls-files 2737`）。
- **D1/D2 实施**：4 脚本 HOST→`root@1.92.141.140`、KEY→`Path.home()/".ssh"/"fk_server_ed25519"`；回灌两处运行脚本修复（与云端逐字一致）；`git remote set-url cloud` 切新机；新增 4 测试 + 11/11 真变异；云端实跑；清单 47/47。
- ⭐⭐ **口径副作用如实登记**：换 HOST 后 `g1_progress.py` 只看到新机 journal ⇒ 实测「链路 ok 0 天 / G1 0/10」。**这不是从 6/10 倒退**，是两机 journal 分家；**D3 两机合并规则未立**，故不对外引用 G1 计数。
- **hc-ping**：云端 env 只有 `DEEPQUANT_HEARTBEAT_PING_URL`；官方 Simple 调度 = Period + Grace Time；项目要求「周期 1 天、宽限 3 小时」。用户截图证明后台**已是 1 day / 3 hours / Up** ⇒ **上一轮「请改 Period」的推断被证伪，待办关闭**（原文不改，出更正）。⚠️ 链路用 `curl -fsS -m 10 -o /dev/null "$PING"` = **GET**，后台若设 "Only POST" 会挡掉真实心跳。

### 3.8 阶段结论：能不能用 / 好不好用 / 能不能赚钱（11-14）

【文档 `reports/STAGE_CONCLUSION_2026-11-14.md`】

| 问题 | 判定 | 依据 |
| --- | --- | --- |
| 能不能用（研究系统） | **能用，已在用** | 11-03 起工作日 17:10 自动跑；换机后 11-12/11-13 `ok=1`；回归与清单全绿 |
| 能不能用（实盘） | **不能用且禁止** | `LIVE_ENABLED=false`、无 `live_authorization.json`、U-16 未授权、从未报单 |
| 好不好用 | **可用但非免维护** | 好处：自动化 / 失败关闭 / 邮件与心跳外部真值 / 逐文件校验；代价：11-11 业务失败 + 告警漏报、G1 口径断裂、G2 0/20、10 万本金整手闲置期望 12.0%、27 条研究结论可对外仅 7 条且 S4 0/7 |
| 能不能赚钱 | **既不能证明能、也不能证明不能** | 样本内 `+0.1360 pp/月 (t=4.04)` 全为样本内且属 exQ5 线；真实费率 0.051%（0.3% 是 5.9 倍压力参数）；10 万本金显性成本 0.302% 往返 ≈ 回测 3 倍；**实盘预期 7.50%–7.66% < 8% 门槛**；现代段 `+0.1112 pp/月 (t=2.218)` boot 跨 0；**样本外证据 = 0** |

⭐ 三层收紧：① 系统能跑 ≠ 策略被验证；② `S4 0/7` 计的是 exQ5 线、不是所交易 sp4/d4，R7 已写明六项上线条件无一回答「所交易策略有没有效」；③ 真正的时间成本是 **11-30 起逐月累积的前瞻样本外**，不是演练本身。`UNDERPOWERED` 只能引「无法判断」，**不得写成「没用」**。

### 3.9 授权文件定稿与「四道前置」（11-14）

- 用户本人创建 `paper_authorization.json`：`deepquant:deepquant / 600 / 477 B`、无 BOM、纯 LF；`--compute-hash` 与独立验算逐位一致 `3a3c35a35c5c8a92…`；干净复跑 `ALLOW｜六项 6/6｜未发单` rc=0；通道隔离 `--remote` 第三次 FAIL 0 PASS。
- ⭐⭐ **过程如实保留**：首版 `account_ref` / `issued_by` 是中文占位文字，六项校验不查这两个字段内容 ⇒ 照样 `ALLOW`——**「测试全绿 ≠ 验收项覆盖」**；第二次修订占位又原样粘贴；交互式 `read` 两行合并导致 `-bash: read: '-p': not a valid identifier`，python `assert` 在写文件前中止 ⇒ **文件未损坏**；第三次显式赋值才换成 `虚拟盘测试202611` / `simon`。全部失败按 R5 保留在 `docs/PAPER_AUTH_READBACK_2026-11-14.md` §2/§6/§7/§8，原文未改写。
- **四道前置现状**：① U-15 声明 ✅；② 授权文件 + 六项校验 + 隔离 ✅；③ 11-30 `mode=FORWARD` + `target_holds_d4` 台账（另需 11-27 DRYRUN、日历补刷）⏳；④ U-16 书面放行 ⏳。

### 3.10 EARLY-IMPL-1 提前实现（11-14 23:03）

- **授权性质**：「按『做这个』授权提前实现」= **工程准备授权，不是 U-16**。原文存证 sha `5a99c555…`；预登记 `docs/SIM_DRILL_PREP_PREREG_2026-11-14.md` sha `a6f5a9be…` **在任何运行代码改动之前冻结**。
- **实现**：`paper_account_adapter.py` 加只读外部真值（`--truth-probe` / `--truth-out`、`DESCRIPTOR.fields` 读 protobuf 业务字段、身份字段脱敏）；`scripts/sim_reconcile.py`（外部真值优先、`NO_INPUTS` 与「无差异」两条独立路径、`Fraction` 独立算术、任一差异 `HALT:*`）；`scripts/sim_drill_driver.py`（五项演练离线 fixture、`--self-check` 唯一模式、`--execute` 恒 `NOT_AUTHORIZED`、`selftest_` 路径守卫）。
- ⭐ **两处真发现**：① 云端 `--truth-probe` 首轮 **rc=2**——gmtrade `ExecRpt`/`Order` 是 protobuf，`dir()` 的 `Extensions` 读取抛 `AttributeError`；改按 `DESCRIPTOR.fields` + 回归测试后 rc=0。**这正好命中预登记结构假设 4：离线自检 PASS 不证明 SDK 契约**。② SDK 对象带 `account_id` / `account_name` / `strategy_id` ⇒ 加脱敏。③ 云端既有用例首轮 2 FAIL 是**环境差异**（云端保留 R7 冻结的 `account_snapshot.json`，本地没有；`OUT` 定义改动前后逐字一致 ⇒ 非回归），改注入 `tmp_path` 的 OUT，判据等价不弱化。
- ⭐ **字段实证**：`FIELD_MAP` 与真实 `ExecRpt` 字段对得上（`cl_ord_id/symbol/side/volume/price/commission/exec_id/exec_type`）；但 `status` 实际取 **`exec_type`（执行类型，非订单终态）**，语义对齐须 12-01 用真实回报确认。
- **验收**：P1 10/10、P2 4/4、P3 变异 11/11、P4 100% 拒绝、P5 五项 5/5 离线；回归四连（最终 `680/1`）；云端自检 rc=0、`--truth-probe` rc=0、单测 39/39 + 19/19；清单 47→50、**50/50 PASS**。
- ⭐ **仍未实现**：**gm-paper 真实报单适配器**——本单只有只读真值源和离线 fixture broker，这是刻意留的边界。

---

## 4. 验收与回归证据（本周演进，数字可逐条复核）

| 时点 | backend | property | scripts/tests | verify_exec_all | 真变异 | 部署清单 |
| --- | --- | --- | --- | --- | --- | --- |
| 11-08 02:30 SHADOW 接入 | — | — | 475/1 | — | shadow 11/11 | — |
| 11-08 02:55 只读适配器 | — | — | 494/1 | — | paper 7/7 | — |
| **11-08 13:50 R6 收口** | **3,651/1** | **9/9** | **560/1** | **PASS** | channel 9/9、manifest 5/5、calendar 5/5、shadow 11/11、paper 7/7、session 11/11 | **44/44** |
| 11-09 11:03 路径分叉修复 | — | — | 562/1 | — | channel 10/10 | 44/44 |
| 11-09 11:16 U-15 闸门 | — | — | 591/1 | — | guard 10/10、manifest 5/5 | **45/45** |
| 11-10 00:38 10 月归因 | 3,651/1 | 9/9 | 615/1 | PASS | asof 12/12 等 | 45/45 |
| 11-10 21:40 R7 修复 | 3,651/1 | 9/9 | 637/1 | PASS | p0 7/7、shadow 12/12 | **47/47** |
| 11-12 11:02 D1/D2 | 3,651/1 | 9/9 | 641/1 | PASS | d1d2 11/11、shadow 12/12、paper 7/7 | 47/47 |
| 11-14 23:03 EARLY-IMPL | 3,651/1 | 9/9 | 678/1 | PASS | sim_drill 11/11 | **50/50** |
| **11-15 13:2x 最终复核** | 3,651/1（11-14 跑） | 9/9 | **680/1** | **PASS** | **11/11** | **50/50 PASS** |

⭐ **数字口径说明**：`678/1` 是 protobuf 回归测试加入**之前**的全量值；补 2 条 protobuf 形态测试后，11-15 重跑全量为 **680/1**。`backend 3,651/1` 为本周多次跑同一基线的结果，最后一次在 11-14。数据层 `data/exec_all/_acceptance.json` 与 `_asof_manifest.json` 的 sha256 在本周回归前后**逐位一致**（`b7d2d7d0…` / `04e39a96…`），证明回归未改动行情数据。

**部署可证性（11-15 实测）**【一手·云端】：`deploy_manifest.py --verify --remote` ⇒ commit `9f76f66b`、云端 **50/50 文件逐位一致 PASS**；`check_channel_isolation.py --remote` ⇒ **FAIL 0 项 PASS**（真实通道文件 0、敏感 env 前缀 0、`LIVE_ENABLED` 未设置、broker 白名单 5/5、只读适配器 `order_*(` 远端计数 0）；`paper_authorization.json` 存在且 `channel=gm-paper`（INFO）。

---

## 5. 冻结证据链（sha256 侧车，本周新增/更新 9 项）

| 文件 | sha256 前 16 位 | 用途 |
| --- | --- | --- |
| `docs/PAPER_AUTH_GUARD_PREREG_2026-11-09.md` | `1e48091a4bfd9047` | PA-GUARD-1 六项判据 + sha256 规范化口径 |
| `research/.../forward/authorization_statements/U15_statement_2026-11-09.md` | `3207bff41a588f96` | U-15 书面声明原文 |
| `research/.../SP5_OCT2026_DOWNTURN_PREREG.md` | `cd5d016b8c9f4078` | 2026-10 双口径描述性归因 |
| `research/.../sp5_lotapp_decision_prereg.sha256` | `465ea143bf89a234` | 整手追加规则决策材料（R6⑤） |
| `research/.../d4_strict_out/p03e_script.sha256` | `c525c67d2f131fea` | manifest 漂移后脚本基线 |
| `research/.../forward/authorization_statements/R7_P0_FIX_authorization_2026-11-10.md` | `b1b9522b810a1f47` | 用户泛授权原文（含歧义标注） |
| `docs/ENGINEERING_CHANGE_P0_SNAPSHOT_2026-11-10.md` | `5c56ceb082154169` | ECR-P0-SNAPSHOT-1，**冻结于代码改动前** |
| `research/.../forward/authorization_statements/EARLY_IMPL_statement_2026-11-14.md` | `5a99c555fbb37f69` | 工程准备授权原文（非 U-16） |
| `docs/SIM_DRILL_PREP_PREREG_2026-11-14.md` | `a6f5a9beb29efa52` | EARLY-IMPL-1 预登记，**冻结于代码改动前** |

所有冻结文件 UTF-8 无 BOM / LF（CRLF=0），`sha256sum -c` 于 11-15 复核 **OK**。⭐ **预登记一经冻结不改**：发现缺陷另立更正记录、按字面执行、如实报「预测被反驳」（P3 案例），不替规则圆场。

---

## 6. 失败、缺陷、更正与撤回清单（不删失败，逐条可查）

| # | 日期 | 事件 | 性质 | 处置 | 闭环 |
| --- | --- | --- | --- | --- | --- |
| 1 | 11-08 | 变异 M1「无账户也计入 G2」首轮漏检 | 测试全被 `LEGACY_NO_D4` 兜住 | 补 d4+无账户用例 | ✅ |
| 2 | 11-08 | **并发跑 pytest 与原地变异 ⇒ 假 FAIL** | 变异脚本原地改源码 | 改串行；写入长期铁律 | ✅ |
| 3 | 11-08 | gmtrade cp311 wheel 不兼容，改 ABI 名 ⇒ 段错误 rc=139 | 文档与发布物不一致 | `.venv-gm` CPython 3.10 | ✅ |
| 4 | 11-08 | 测试 helper 把 `read_credentials` 换成 lambda 造成无限递归、被 except 吞成 rc=2 | **所有「期望失败」用例因错误原因通过** | 存原函数 + 每条补明确原因断言 | ✅ |
| 5 | 11-08 | manifest 基线生成后 2 分钟代码又改 ⇒ 漂移 | 顺序错误 | 先提交代码 → 再 `--write` → 再提交 manifest | ✅ |
| 6 | 11-09 | `CONFIG_AUTH_PATH_MISMATCH` | 27 测试全绿未覆盖路径分叉 | 单一常量 + 反例测试 + 真变异 M10 | ✅ |
| 7 | 11-09 | 「3 个月未激活影响只读查询」是推测 | 官网原文只说不能交易 | 精确化，原句保留不删 | ✅ |
| 8 | 11-10 | **P0 `PAPER_SNAPSHOT_STALE_FROM_DAY2`** | 快照停滞 + 告警漏报 | R7 全套修复 | ✅ 代码；⏳ 告警脚本独立读取未授权 |
| 9 | 11-10 | **P3 按预登记字面被反驳** | 符号约定缺陷 | 保留原文、报 `REFUTED`、另立缺陷段 | ✅ 纪律 |
| 10 | 11-10 | 闸门容差 1e-6 小于产物 6 位小数存储下限 | 判据永远不可满足 | 改 2e-6（**产品代码未动**） | ✅ |
| 11 | 11-10 | qfq 日期 `YYYY-MM-DD` 与 8 位日历字符串比较恒真 | as-of 守卫可能放过未来行 | `replace('-','')` 归一化 + 12/12 变异 | ✅ |
| 12 | 11-10 | **Q5 `0.936243` 归因被独立复算推翻** | 审阅者反推成整手不足，实为现金利息抵消 | 采纳结论、修正机制、改判 P1 恒等式 | ✅ |
| 13 | 11-10 | origin 推送 `schannel` 断连 / API 通道缺 `github-pat` | 外部通道 | 不翻找凭证、不强行绕；恢复后一次补齐 | ✅ 当时；⚠️ 本周再落后 3 提交（§7） |
| 14 | 11-12 | 47 文件比对被 Windows CRLF 误判全 DIFF | 检查器输入未去 `\r` | 去回车后 45/47 | ✅ |
| 15 | 11-12 | 「新机 service 不写 journal」是过度推断 | 实为两机 journal 分家 | 更正为 D3 口径问题 | ⏳ D3 未立 |
| 16 | 11-12 | 迁移 Agent 留证位置对不上（桌面两份报告都无 §5.4） | 外部协同留证缺 | 请对方给路径/贴 sha；10:20 一次演练 FAIL 与 10:27 OK 并列保留 | ⏳ |
| 17 | 11-12 | 「连上网站」被误做成本地回测工作台 | 指代缺失、澄清被跳过 | 关闭服务、改交付 hc-ping 后台 | ✅ |
| 18 | 11-12 | hc-ping「请改 Period=1 天」被截图证伪 | 推断未核实 | 待办关闭、出更正、原文不改 | ✅ |
| 19 | 11-12 | R5「日志首行须 dry_run=0」按整份文件字面不成立 | 判据措辞缺陷 | 如实登记，按真跑段判，原文不改 | ✅ 登记 |
| 20 | 11-12 | `sent.json` 未定位到 ⇒ 「无重复发信」未验证 | **没找到 ≠ 没问题** | 改用邮箱外部真值 | ⏳ 判据未验证 |
| 21 | 11-14 | 授权文件占位值两轮未替换、`read` 语法报错 | 「测试全绿 ≠ 验收项覆盖」 | 交互式改显式赋值；失败保留 | ✅ |
| 22 | 11-14 | 云端 `--truth-probe` 首轮 rc=2（protobuf `Extensions`） | 离线自检不覆盖 SDK 契约 | 按 `DESCRIPTOR.fields` 读 + 回归测试 | ✅ |
| 23 | 11-14 | SDK 对象带 `account_id` 等身份字段会写进证据 | 脱敏缺失 | `REDACTED_FIELDS` | ✅ |
| 24 | 11-14 | 云端既有用例 2 FAIL（legacy 冻结文件存在） | 测试环境依赖 | 注入 `tmp_path` OUT，判据等价 | ✅ |
| 25 | 11-10~14 | **三次撤回**：G2「约 12-01」/「最早 12-25」、G1「11-16 判定日」、G2「接入即计数」 | 口径错误 | 原文不改、出更正、改条件式 | ✅ 登记 |

⭐ **本周「没检出 ≠ 没问题」复发 13 次**，全部是断言/闸门/采集层写错，不是业务代码漏实现。项目长期铁律：报 FAIL 先怀疑检查器，但**必须用独立实现去证**（Q5 就是手写 `nav_daily_B.csv` 复算推翻的）。

---

## 7. ⭐ 尚未闭环的风险（外部审核重点）

| # | 风险 | 一手证据（11-15 实测） | 影响 | 建议 |
| --- | --- | --- | --- | --- |
| R1 | **更正台账云端/本地不同步** | 本地 `corrections.jsonl` **51 行** / sha `8fe34911…`；云端 `/opt/quant/.../corrections.jsonl` **17 行** / sha `f64b8311…`（mtime 11-05 14:11）；最新备份包 `deepquant_evidence_20261113T171312.tar.gz` 内该文件 sha 亦为 `f64b8311…`、17 行 | ⭐ **本周 34 条更正记录没有机外备份**，直接冲淡「邮件锁时点防事后补记」的证据链 | 12-01 前把本地 51 行同步到云端并纳入每日备份；同步前不要用备份包证明更正完整性 |
| R2 | **`logrotate.service` 连续失败** | `systemctl --failed` ⇒ `logrotate.service failed`；timer LAST `Sun 11-15 00:00:18`、NEXT `Mon 11-16 00:00`；根因（11-14 诊断）`/etc/logrotate.d/deepquant.bak.20261112` 被当配置读取、与主配置 duplicate log entry | 日志轮转每天失败；且 `logs/*.log`（weekly_offsite）本就不在轮转覆盖内，会无限增长 | 需**用户单独授权**移走/改名备份文件；本 Agent 不擅自动系统配置 |
| R3 | **日志文件被轮转成 0 字节** | `daily_20261109~20261113.log` 均为 **0 B**，但 `.log-YYYYMMDD` / `.log.1` 归档全在（如 `daily_20261110.log.1` 3,054 B、`daily_20261112.log.1` 4,717 B），本报告 §2 的逐日结束行均从归档读出 | **证据没丢，但直接打开当天日志会是空的** | 审核时一律读归档；建议把归档纳入备份包清单 |
| R4 | **G1 统计口径跨机断裂 / D3 未立** | 换 HOST 后 `g1_progress.py` 只看新机 journal（11-12 起），实测 0/10；旧机 journal 保留 11-03~11-11 | 不对外引用任何 G1 计数；r7 收紧口径需从首个全绿交易日 D 重算 | 另立 D3「两机合并口径」变更记录 |
| R5 | **git 同步与产物可追溯性** | 本地 HEAD `9f76f66`（11-12）＝ cloud 裸仓库 HEAD；**origin 落后 3 个提交**（`40cfb2d` / `5a9bd6c` / `9f76f66`）；工作树 **26 项**（6 modified + 20 untracked）未提交/未跟踪，其中 11-14 的 EARLY-IMPL-1 全部代码/文档/预登记/产物**完全未入 git** | 外部专家按 commit 复核时会看不到本周后半段实现；仅靠工作树 + 云端 scp 可证 | 代理恢复后 `fetch` 对齐再 push（**禁止 `reset --hard`**）；11-14 产物需授权后提交 |
| R6 | **`STATUS.md` 时间戳滞后** | 文件头「最后更新 2026-11-08 13:10」，但正文已追加 11-09 U-15 / 11-10 P0+R7 / 11-12 迁移核查段落 | 状态权威文件的「最后更新」与内容不一致，易被误读为状态停在 11-08 | 审核以正文段落为准；建议同步时间戳 |
| R7 | **11-30 / 12-01 前置 4 项未齐** | 正式台账 `forward/ledger/` 仍只有 `20260930.json`、`20261030.json` 两份 `LEGACY_NO_D4`；`paper_authorization.json` 命中 1 但 U-16 未给；`sim_drill_driver.py --execute` rc=4 | 不能报单、不能演练、不能把演练计入 G2/S4 | 见 §12 |
| R8 | **真实报单适配器未实现 / SDK 语义未对齐** | 本单只有只读真值源 + 离线 fixture broker；`FIELD_MAP["status"]` 对成交回报实际取 `exec_type`（执行类型非订单终态）；`--truth-probe` 显示回报/未完结委托当前均为 0 | 12-01 空回报必然 `NO_INPUTS` 停机（设计内）；状态编码、5 元佣金下限均须用真实成交实测 | 执行预登记扩项 + 一笔约 4,000 元实测（方案 §四.1） |
| R9 | **U-14 token 未轮换** | token 曾明文出现在对话，官方定义等同「用户 ID+密码」；`verify_token_rotation.py` 就绪但用户未在官网轮换 | 凭证暴露风险 | 用户本人在官网重新生成并更新 `/opt/quant/env`，Agent 不接手新值 |

---

## 8. 硬边界（本周始终未破，11-15 复核）

| 边界 | 11-15 实测 / 证据 |
| --- | --- |
| 实盘开关 | `LIVE_ENABLED` 云端未显式配置，隔离检查判 PASS；仓库内无 `live_authorization.json` |
| 报单 | 仿真账户 `nav=100000 / available=100000 / frozen=0 / cum_trade=0 / cum_commission=0 / cum_pnl=0 / n_positions=0 / n_execution_reports=0 / n_unfinished_orders=0`【一手·云端：11-15 只读实查】 |
| 日更链 | `daily_chain.sh` 在 EARLY-IMPL-1 中相对 HEAD **零改动**；EARLY-IMPL-1 三脚本未接入 `daily_chain.sh` |
| 正式台账 | `forward/ledger/` 只有两份 `LEGACY_NO_D4`；自检产物只在 `sim_orders/` / `sim_reconcile/` 且带 `selftest_` 前缀 |
| 下单调用 | 三个脚本 `order_*(` 命中 **0**；云端 grep 计数 0 |
| 策略与配置 | 未改 `p13_forward.py` / `sp4_config_v1.json` / `sp4_config_v2.json` / 任何策略参数；未上线 `SP5-LOTAPP-1` 整手追加规则 |
| 证据分级 | 演练与自检未计入 G1 / G2 / S4；仿真成交与净值未用于经济性判断 |
| 凭证 | 未回显 token / 账户 ID / 心跳 URL 完整值；`env` 只按 `^VAR=` 单行读取 |

**当前日更链实况**【一手·云端】：`heartbeat.json` 末次 `ts=2026-11-13T17:13:11 / ok=true / mode=NOT_DUE`，5 个关键阶段 rc 全 0；11-14（周六）、11-15（周日）无 `daily_*.log` 属**设计内**（`OnCalendar=Mon..Fri 17:10`），周末 17:15 存活信号照常；timer NEXT `Mon 11-16 17:11:58`（daily）、`17:40`（alert）。

---

## 9. 引用规范与不可引清单

**可引（带完整口径）**：`STATUS.md` §2 结论表 7 条可引结论、`+0.1360 pp/月 (t=4.04)`（阶段 O / BASE 费率 / 3000 万档 / **237 月全样本内**）、真实费率等价单边 **0.051%**、实盘预期年化 **7.50%–7.66%**（10 万本金、现行整手规则、低于 8% 门槛）。引用须带 `StatisticalVerdict`、`MaturityLevel`、预登记哈希、产物目录（`.workbuddy/memory/RESULTS_INDEX.md`）。

**本周新产物的成熟度**：

| 产物 | StatisticalVerdict | MaturityLevel | 可否对外 |
| --- | --- | --- | --- |
| `oct2026_out/cd5d016b8c9f/` | `DESCRIPTIVE_SINGLE_MONTH` / `EXPLORATORY_ONLY` | 不可引 | ❌ 不得改 S4、不得称样本外 |
| EARLY-IMPL-1 自检产物 | `ENGINEERING_SELF_CHECK_ONLY` | `DESIGN_VALIDATED_OFFLINE` | ⚠️ 仅作工程自检；不构成样本外证据 |
| R7 / D1D2 / 迁移核查 | 工程与运维事实 | 一手可复核 | ✅ 但须带路径与时间 |

**不可引 / 已撤回（本周确认）**：

- 10 月单月归因的任何「策略跑输/抗跌」结论（v1/v2 配置错配、台账事后补跑、数据穿越风险、单月无统计分辨力）；
- P3 预测（按预登记字面 `REFUTED`）；
- G1「11-16 收盘后判」、原口径 6/10 的外推；
- G2「约 12-01 可判」「最早 12-25」「接入即开始计数」；
- 「11-16 可以提前演练」（无 FORWARD 台账、无 U-16、演练不计样本外证据）；
- `UNDERPOWERED` 反引成「无效效 / 没用」；
- `+0.1360 pp/月` 给实盘背书（属 exQ5 线，非所交易 sp4/d4；S4 0/7）；
- 仿真成交与净值用于经济性判断（官网明确撮合不模拟冲击成本、成交价可能优于实际）。

---

## 10. 审阅者复算清单（**建议逐条执行，不依赖本报告断言**）

> `PY = C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe`；仓库根 `D:/DeepQuant power by txm/DeepQuant-main`；云端 `ssh -i ~/.ssh/fk_server_ed25519 root@1.92.141.140`。**所有命令只读或只跑自检，不触发报单。**

| # | 复算项 | 命令 / 位置 | 预期 |
| --- | --- | --- | --- |
| 1 | 回归四连 | `PY -m pytest backend/tests -q`（`backend/` 下，带 `PYTHONPATH=<repo>/backend/src`）；`PY scripts/property_checks.py`；`PY -m pytest scripts/tests -q`；`PY scripts/verify_exec_all.py` | `3,651/1`｜`9/9`｜**`680/1`**｜`PASS` |
| 2 | EARLY-IMPL 真变异 | `PY scripts/sim_drill_mutation_test.py` | **11/11**，全部「停机/拒绝 → PASS/放行」 |
| 3 | 三模块自检 | `PY scripts/sim_reconcile.py --self-check`；`PY scripts/sim_drill_driver.py --self-check`；`PY scripts/sim_drill_driver.py --execute` | `PASS` rc=0｜`SELF_CHECK_PASS` 五项 5/5｜`NOT_AUTHORIZED` **rc=4** |
| 4 | 部署清单 | `PY scripts/deploy_manifest.py --verify --remote` | **50/50 PASS**，commit `9f76f66b` |
| 5 | 通道隔离 | `PY scripts/check_channel_isolation.py --remote` | **FAIL 0 项 PASS**，`order_*(` 计数 0 |
| 6 | 预登记冻结 | `sha256sum -c docs/SIM_DRILL_PREP_PREREG_2026-11-14.md.sha256` 及其余 8 个 `.sha256` | 全部 `OK`，CRLF=0 |
| 7 | 授权文件 | 云端 `PY=.../paper_authorization_guard.py --check --file <正式路径> --action sim_rebalance_order --notional 4000 --cumulative 0 --available 100000 --now "<当前时间>"` | `ALLOW｜六项 6/6｜未发单` rc=0 |
| 8 | 仿真账户零成交 | 云端 source env 后 `.venv-gm/bin/python` 读 `get_cash()` / `get_positions()` / `get_execution_reports()` / `get_unfinished_orders()` | `nav=100000`、`cum_trade=0`、`cum_commission=0`、四类计数 0 |
| 9 | 只读外部真值探测 | 云端 `.venv-gm/bin/python /opt/quant/scripts/paper_account_adapter.py --date 20261113 --truth-probe` | rc=0；只打印字段名与条数；`redacted_fields` 含身份字段 |
| 10 | 逐日日更结论（**须读归档**） | `grep -h 'daily_chain 结束' /opt/quant/var/log/daily_2026111{0,1,2,3}.log.1` | 11-10 `PAPER_SNAPSHOT_RC=4`；11-11 `ok=0 is_due_RC=1`；11-12/13 `ok=1` |
| 11 | 心跳与阶段 | `cat /opt/quant/.../forward/heartbeat.json`、`stage_results.json` | `ts=2026-11-13T17:13:11 / ok=true`、5 关键阶段 rc 全 0 |
| 12 | 正式台账 | `ls /opt/quant/.../forward/ledger/*.json` | 仅 `20260930.json`、`20261030.json`（`LEGACY_NO_D4`） |
| 13 | ⭐ R1 更正台账缺口 | 本地 `wc -l .../corrections.jsonl` + `sha256sum`；云端同两条；`tar xOf <最新备份包> <路径> \| sha256sum` | 本地 **51** / `8fe34911…`；云端与备份包 **17** / `f64b8311…` |
| 14 | ⭐ R2 logrotate | `systemctl status logrotate.service --no-pager`、`ls /etc/logrotate.d/` | `failed`；存在 `deepquant.bak.20261112` 与主配置重复定义 |
| 15 | ⭐ R5 git 同步 | `git rev-parse --short HEAD`、`git rev-list --left-right --count origin/...HEAD`、`GIT_SSH_COMMAND=... git ls-remote cloud HEAD`、`git status --short \| wc -l` | `9f76f66`｜`0 3`｜`9f76f66…`｜**26** |
| 16 | P0 老代码根因 | `git show 90db703:scripts/paper_account_adapter.py` 第 43 / 160-170 行 | 单文件 OUT + 内容不同即 `EXIT_CONFLICT` |
| 17 | R7 修复验收 | `PY -m pytest scripts/tests/test_p0_snapshot_r7.py scripts/tests/test_daily_chain_rc.py -q`；`PY scripts/p0_snapshot_mutation_test.py` | 22 passed｜7/7 |
| 18 | 10 月归因复算 | `PY -m pytest scripts/tests/test_oct2026_replay.py scripts/tests/test_oct2026_asof_guard.py -q`；`PY scripts/oct2026_asof_mutation_test.py` | 24 passed（P1 恒等式、**P3 保留 REFUTED**）｜12/12 |
| 19 | Q5 现金利息复算 | 读 `oct2026_out/cd5d016b8c9f/nav_daily_B.csv` 首/末行 | `stock_value/100000 = 0.2000000000`；利息 `157.377049` 元 ⇒ `0.936243 = 1 − 0.063757` |
| 20 | 授权存证 | `forward/authorization_statements/` 下 3 份 `.md` + `.sha256` | sha `3207bff4…` / `b1b9522b…` / `5a99c555…` |
| 21 | SHADOW 四项条件 | `scripts/shadow_daily_reconcile.py --summary`；`forward/shadow_reconcile/20261113.json` | G2 `0/20`；`REAL_PAPER` / `n_orders=5` / `orders_sent=False` / `counts_toward_g2=False`（`LEGACY_NO_D4`） |
| 22 | 决策日预检 | `PY scripts/preflight_decision_day.py --cloud` | `PASS 12 / FAIL 0 / PENDING 1`（**C8b PENDING 不得提前当 PASS**；11-30 17:10 后须重跑） |

⚠️ **审阅者不得做的事**（`REVIEWER_GUIDE_2026-11-05.md` §5）：不得改任何策略参数 / 门槛 / 仓位；不得把「文档一致」当成「行为正确」（本项目出现过 22 条验收全绿但功能未实现）；不得以本自审报告代替自己的复算结论。

---

## 11. 建议专家重点判断的 10 个问题

1. **R1（更正台账分叉）有多严重**？本周 34 条更正记录没有机外备份，是否足以让「邮件锁时点防事后补记」这条证据链在 11-30 前失效？修复优先级是否应高于等 11-30？
2. **P0 修复方案 A（按日期分文件 + `latest.json` 原子指针）**是否破坏幂等 / 备份 / 回滚语义？`--supersede` 生成 rev2 会不会给「同日多次改写」留下可争议空间？
3. **R7 收紧 rc 之后**：5 个原本被忽略的关键阶段现在 `ok=false`，但告警脚本本身未改（仅靠心跳 `stages` 传话）——这个「偏差登记」是否可接受，还是必须补独立读取？
4. **G1 两机合并口径（D3）**应该怎么定义？旧机 11-03~11-11 与新机 11-12 起的 journal 合并后，收紧口径从哪个交易日 D 起算才不产生事后选择偏差？
5. **G2 判据只比「台账目标 vs 规划买单」、不校验实际成交**——12-01 演练若加入成交回报对账，是否应把 G2 定义一并扩项？扩项会不会变成「改判据凑通过」？
6. **10 月归因的 P3 处理**：按预登记字面报 `REFUTED` 而不改原文，是否比「修正符号约定后重判」更正确？审阅者是否认同「缺陷另立记录」的处置？
7. **Q5 归因修正**：`0.936243` 是现金利息抵消而非整手不足，这一独立复算是否充分？「抗跌来自仓位非选股」的结论在修正机制后是否仍成立？
8. **EARLY-IMPL-1 的边界**：只读真值源 + 离线 fixture broker 是否算「假接入」？在 U-16 未给、真实报单适配器未实现的前提下，提前实现是否反而增加了 12-01 的返工风险？
9. **`exec_type` ↔ `status` 语义对齐**与「5 元最低佣金」未实测：这两项若 12-01 才发现不一致，是否会导致成交回报对账必然 `NO_INPUTS` 停机？应否在本周就做一次只读字段核对（已做）之外的一笔实测？
10. **经济性结论**：7.50%–7.66% < 8% 门槛、`S4 0/7`、样本外 0 个月——审阅者是否同意「既不能证明能、也不能证明不能」是目前唯一可辩护的表述？有没有被本报告遗漏的、可提前收紧或放宽的口径？

---

## 12. 下一步时间线与待办

| 时点 | 事项 | 状态 |
| --- | --- | --- |
| 11-16（周一）17:10 | 换机后第 3 个工作日真跑；**不再有「G1 11-16 判定日」含义** | 待时间 |
| 11-19 前 | 旧机释放（D1/D2 已完成，不受阻） | ✅ 前置已了 |
| 11-27 | 月末 DRYRUN（`r7_dryrun_check.py` 四项；注意 `--cloud` 只能本地发起） | ⏳ |
| 11-30 17:10 | 首个 `mode=FORWARD` + `target_holds_d4` 台账；**之后重跑 `preflight_decision_day.py --cloud`**（C8b 当日快照 PENDING） | ⏳ |
| 11-30 之后 | 用户给 **U-16 书面放行**（变更单 §5 有可复制原文）+ 另立执行预登记 | ⏳ 待用户 |
| 12-01 09:35 | 仿真月度调仓演练（方案 §一）；成交回报对账、五项演练、证据归档 | ⛔ 未授权、不执行 |
| 12-01 前 | 实现 **gm-paper 真实报单适配器**；`exec_type`/`status` 对齐；5 元佣金下限实测 | ⏳ 需新预登记 |
| 12-15 / 12-31 09:05 | 日历缺口补刷（`trade_calendar.py --refresh`） | 已排程 |
| 待授权 | R1 更正台账同步、R2 logrotate 备份文件、R5 origin 推送与 11-14 产物提交、D3 G1 合并口径、U-14 token 轮换 | ⏳ 需用户 |

⭐ **一句话给审核者**：本周所有「通过」都是**工程判据的通过**，没有一条是「策略有效」的通过。系统现在是一个可审计的证据生产系统，不是可以投真钱的策略。

---

## 附录 A：本周主要产物（按主题）

| 主题 | 文件 |
| --- | --- |
| 阶段 / 综合报告 | `reports/STAGE_SUMMARY_2026-11-08.{md,docx}`、`reports/FIRST_FULL_RUN_REVIEW_2026-11-09.{md,docx}`、`reports/DEEPQUANT_COMPREHENSIVE_REVIEW_2026-11-10{,_rev2}.{md,docx}`、`reports/OCT2026_DOWNTURN_CONCLUSION_CARD_2026-11-10.docx`、`reports/STAGE_CONCLUSION_2026-11-14.md`、`reports/DEEPQUANT_WEEKLY_REVIEW_2026-11-15.{md,docx}` |
| 仿真 / 授权 | `docs/PAPER_TRADING_API_OPTIONS_2026-11-08.md`、`docs/PAPER_ONBOARDING_2026-11-08.md`、`docs/PAPER_ORDER_AUTHORIZATION_{PLAN_2026-11-08,CARD_2026-11-08}.md`、`docs/SIM_REBALANCE_DRILL_2026-12-01.md`、`docs/U15_U16_AUTHORIZATION_FILL_GUIDE_2026-11-09.md`、`docs/PAPER_AUTH_GUARD_PREREG_2026-11-09.md`、`docs/PAPER_AUTH_FILE_TEMPLATE_2026-11-09.md`、`docs/PAPER_AUTH_FILE_STEP_BY_STEP_2026-11-14.md`、`docs/PAPER_AUTH_READBACK_2026-11-14.md`、`reports/PAPER_CHANNEL_PROBE_2026-11-14.md` |
| 工程变更 / 迁移 | `docs/ENGINEERING_CHANGE_P0_SNAPSHOT_2026-11-10.md`、`docs/ECR_P0_SNAPSHOT_1_IMPLEMENTATION_RECORD_2026-11-10.md`、`docs/R7_P0_CLOUD_VERIFY_LOG_2026-11-10.txt`、`docs/SERVER_MIGRATION_VERIFICATION_2026-11-12.{md,docx}`、`docs/SERVER_MIGRATION_HANDOFF_TO_AGENT_2026-11-12.md`、`docs/ENGINEERING_CHANGE_D1D2_2026-11-12.md`、`docs/D1D2_CLOUD_VERIFY_LOG_2026-11-12.txt`、`docs/R5_FIRST_REAL_RUN_EVIDENCE_2026-11-12.md` |
| 提前实现 | `docs/SIM_DRILL_PREP_PREREG_2026-11-14.md(.sha256)`、`docs/ENGINEERING_CHANGE_EARLY_DRILL_PREP_2026-11-14.md`、`reports/EARLY_DRILL_PREP_RESULT_2026-11-14.md`、`scripts/sim_reconcile.py`、`scripts/sim_drill_driver.py`、`scripts/sim_drill_mutation_test.py`、`scripts/tests/test_sim_drill_prep.py` |
| 其他 | `docs/CALENDAR_GAP_PLAN_2026-11-08.md`、`docs/DEPLOY_TARBALL_CLEANUP_2026-11-09.md`、`docs/OCT2026_DOWNTURN_RESEARCH_DESIGN_2026-11-09.md`、`docs/PREREG_TEMPLATE_2026-11-10.md`、`reports/PROPERTY_CHECKS_EARLY_IMPL_20261114.json` |

## 附录 B：本周 git 提交链（关键节点）

`f934ec9`（G1 4/10）→ `7bd184c`（SHADOW 接入）→ `3e60e16` → `2198a36`（gmtrade 接入）→ `c7fb586`（STAGE_SUMMARY）→ `d0e8394`（R6 全量）→ `0cb3907`（44 文件基线）→ `2aab998`（路径分叉修复）→ `ec69635` → `cc82b7f`（U-15）→ `12c262d`（45 基线）→ `702ec47`（首次运行报告）→ `51a62a8`（容量清理）→ `519ea37`（10 月任务书）→ `45851e6`（10 月归因）→ `41938c9`（综合报告）→ `90db703` → `f7d9a5f`（R7）→ `6559ab4`（47 基线）→ `953fd8d`（rev2 更正）→ `8f1b15d`（迁移核查）→ `5d36e13` / `0dfdacc`（协同单）→ `40cfb2d`（D1/D2）→ `5a9bd6c`（D1/D2 日志）→ **`9f76f66`（本地 HEAD = cloud HEAD）**。

⚠️ **11-14 之后零提交**：EARLY-IMPL-1 的全部代码、预登记、报告、自检产物均只在工作树 + 云端 scp，见 §7 R5。

## 附录 C：术语与口径速查

| 术语 | 含义 |
| --- | --- |
| SHADOW | 影子账本，算出本应下的单、**绝不发送**；`MODE_PAPER` 只是枚举值无下单实现 |
| `REAL_PAPER` / `SELF_CHECK_ONLY` / `NO_INPUTS` | SHADOW 三档；只有前者且四项条件齐备才计入 G2 |
| `LEGACY_NO_D4` | 20260930 / 20261030 两份老台账的 sidecar 标记，**不计入任何证据窗口** |
| `target_holds_d4` | 11-30 起 FORWARD 台账才有的字段；G2 倒计时的起点 |
| G1 / G2 / S4 | 前瞻连续运行 / SHADOW 20 日 / 样本外证据分级；当前 G1 口径断裂、G2 0/20、S4 0/7 |
| `NO_INPUTS` | 外部真值缺失 / 空 / 不可解析 ⇒ **停机**，不是「无差异」 |
| 真变异 | 变异必须真改行为（停机→放行），仅换个拒绝理由不算检出 |
| 失败关闭 | 缺凭证 / 非交易日 / 字段不认识 / 异常 ⇒ 非零退出且不写文件 |
| 统计口径 | Gate 1~6 头条 t 是 **iid**，C 阶段起才是 NW(3)；`d = 0.5×long − 0.5×short` 与文献 spread 差 2 倍 |

---

**报告结束**。所有证据路径均为相对仓库根或云端绝对路径；如需任何一节的原始输出，可按 §10 命令复现。本自审报告不代表独立审查结论。
