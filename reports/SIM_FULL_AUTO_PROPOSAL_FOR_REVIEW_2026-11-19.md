# 方案报告：服务器端全自动仿真调仓（供外部专家审查）

> 日期：2026-11-19｜状态：**提案，未实施，未改任何代码/配置/授权**
> 目标：把原定 12-01 09:35 的 U-16 仿真调仓演练，改为**全部在云主机自动触发、自动跑完、自动对账归档**，无需人工在场敲命令。
> 审查请求：请重点审 §4 的两个执行机方案取舍、§5 的时序设计、§6 的风险披露。
> ⚠️ 本提案**不构成执行许可**，也不改变 U-16 版本 A 授权的任何条款；任何实施仍须账户持有人授权。

---

## 0. 结论先行

**技术上可行，但"全部自动"存在两个必须先解决的硬阻塞，以及三项无法靠自动化消除的判据缺口。** 自动化本身不是难点；难点在于把四道生效条件里的 E4 从"人工在场检查"变成"机器自证"，而自证在安全上天然比他证弱——这是本报告最需要专家裁决的地方。

| 项 | 结论 |
|---|---|
| 服务器端自动触发 | ✅ 可行（systemd timer，云端已有 4 个 timer 在跑） |
| 自动读账户 / 自动报单 | ✅ 可行（云端 `.venv-gm` 有 gmtrade 3.10.22，`/opt/quant/env` 有凭证） |
| 自动授权校验 | ✅ 已实测 `ALLOW 6/6` |
| 自动 E1 台账 | ✅ 日更链 `daily_chain.sh` 已调 `p13_forward.py --if-due` |
| 自动 E2 DRYRUN/preflight | ⚠️ 脚本存在但**未挂任何定时器**，需新增两个 timer 或并入日更链 |
| 自动 E4 隔离/清单 | ❌ **硬阻塞**：云端无 ssh 自连密钥，`--remote` 恒 `ssh_rc=255` ⇒ 失败关闭 |
| P1/P2 判据 | ❌ SDK 无 cid 入参、无行情接口 ⇒ **无法检验**，只能停机出更正 |

---

## 1. 实测证据（2026-11-19，云端真机只读）

执行环境：`root@1.92.141.140`（现役机，hostname `hcss-ecs-a4f6`）。所有命令均为只读，未发单、未改文件。

### 1.1 云端用真实 gm 环境跑四道门
```
cd /opt/quant && set -a; . /opt/quant/env; set +a
.venv-gm/bin/python scripts/sim_rebalance_executor.py --gates
```
结果：
- `account`: `error=null, nav=100000.0, available=100000.0, n_positions=0` ← 真账户可读
- **E1 ❌** `LEDGER_MISSING`：`/opt/quant/.../ledger/20261130.json` 不存在（预期，11-30 才生成）
- **E2 ❌** `MISSING:DRYRUN_20261127,PREFLIGHT_20261130`（两份产物都没有）
- **E3 ✅** `ALLOW 6/6`：`schema/channel/expiry/scope/notional/hash` 全 true
- **E4 ❌** `FAILED:ISOLATION,MANIFEST`，`isolation_rc=1, manifest_rc=1`

独立复核授权：
```
.venv-gm/bin/python scripts/paper_authorization_guard.py --check \
  --file .../paper_authorization.json --action sim_rebalance_order \
  --now "2026-12-01T09:35:00+08:00" --notional 5000 --cumulative 0 --available 100000
⇒ [guard] ALLOW｜六项 6/6 通过｜未发单   rc=0
```

### 1.2 为什么 E4 在云端恒失败
```
python3 scripts/check_channel_isolation.py --remote
⇒ [FAIL] 远端/本地采集  采集失败或输出为空（ssh_rc=255）⇒ 失败关闭，不得判 PASS

python3 scripts/deploy_manifest.py --verify --remote
⇒ 云端哈希集合为空（ssh rc=255）⇒ 失败关闭｜FAIL  - EMPTY_HASHES
```
根因：新机 `/root/.ssh/` 只有 `authorized_keys` 和 `known_hosts`，**没有任何私钥**；`--remote` 要从本机 ssh 到 `root@1.92.141.140` 采集，因此 `ssh_rc=255`。这是**失败关闭的正确行为**，不是检查器 bug。

对照：**本机**（Windows）跑同一套 E4 是 `PASS`：`[isolate] 结论：FAIL 0 项｜PASS`、`[manifest] 云端 59/59 逐位比对｜PASS`。
本机有 `~/.ssh/fk_server_ed25519`（能 ssh 云端），但没有 gmtrade、没有 `paper_authorization.json`、没有 `DQ_GM_*` 凭证。

⇒ **能过门控的机器不能下单，能下单的机器过不了门控。** 这是全自动化的核心矛盾。

### 1.3 定时器现状
```
systemctl list-timers --all | grep deepquant
deepquant-daily.timer     每日 17:10（日更链）
deepquant-alert.timer     每日 17:40
deepquant-calendar.timer  2026-12-01 09:00（只做 trade_calendar.py --refresh）
deepquant-audit.timer     2027-01-01
```
**没有任何 09:35 报单定时器。** `calendar.timer` 在 12-01 当天会响，但它只刷新交易日历，不下单。

### 1.4 日更链覆盖情况
- `daily_chain.sh` 调 `p13_forward.py --if-due`（E1 台账）—— ✅ 已接
- `dryrun_decision_day.py`、`preflight_decision_day.py`—— ❌ `grep` 在 `deploy/bin/*.sh` 中**零命中**，未接入
- `paper_account_adapter.py`—— ✅ 已接，但用 `.venv-gm`（py3.10.22）并注入 `/opt/quant/env` 凭证
- 快照序列 `account_snapshot_{20261109,11,12,13,16,17,18}_rev1.json`：`cash=100000`、`positions=[]`、`source=gm-paper`、`settlement_verified=unknown`
- SHADOW 对账 8 天产物，`counts_toward_g2=true` 的天数 = **0**（正确：日更 NOT_DUE，不算数）

### 1.5 解释器分工（重要约束）
| 解释器 | 版本 | gmtrade | pandas | 用途 |
|---|---|---|---|---|
| `/opt/quant/.venv/bin/python` | 3.12 | ❌ | ✅ 3.0.6 | 主链、p13、dryrun、preflight |
| `/opt/quant/.venv-gm/bin/python` | 3.10.22 | ✅ | — | 仅账户快照/报单（gmtrade 只有 cp36~cp310 wheel；cp311 改名加载会段错误 rc=139） |
| 系统 `python3` | 3.11 | ❌ | ❌ | 不适合跑 dryrun |

---

## 2. 现状梳理：四道门与六道补充作业

| # | 作业 | 当前 | 自动化后需要 |
|---|---|---|---|
| E1 | 11-30 `mode=FORWARD` + `target_holds_d4` 台账 | 日更链已接 | 保持 |
| E2a | 11-27 DRYRUN 产物 | 脚本有，**未挂 timer** | 新增作业 + 11-27 触发 |
| E2b | 11-30 17:10 后 `preflight --cloud` | 脚本有，**未挂 timer** | 新增作业 + 11-30 17:15 触发 |
| E3 | `paper_authorization.json` ALLOW 6/6 | 已实测 ✅ | 执行器内自动重校，保持 |
| E4a | `check_channel_isolation.py --remote` FAIL 0 | 云端恒 FAIL ❌ | **必须改造**（见 §4） |
| E4b | `deploy_manifest --verify --remote` 一致 | 云端恒 FAIL ❌ | **必须改造**（见 §4） |
| T1 | 12-01 09:35 触发执行器 | 无定时器 | 新增 timer/service |
| T2 | 凭据与解释器注入 | 日更链已有机制 | 复用 `.venv-gm` + `/opt/quant/env` |
| T3 | 18:00 结算后补快照再对账 | 无 | 新增 18:10 作业（见 §6.3） |
| T4 | P1/P2 无法检验的出更正 | 无 | 报告生成步骤（见 §6.4） |

---

## 3. 自动化的目标态（时序）

```
11-27 (四) 17:20  ── DRYRUN（主 venv）────────────→ forward/dryrun/*20261127*
11-30 (一) 17:10  ── 日更链 → p13_forward --if-due ─→ ledger/20261130.json (FORWARD, D4)
11-30 (一) 17:15  ── preflight_decision_day --cloud ─→ E2 第二份产物
                         ↓ 17:20 复核四道门（可只读 dry-run）
12-01 (二) 09:35  ── timer 触发 ─→ 执行器 --execute ──→ sim_orders/20261201.jsonl
12-01 (二) 10:30/14:30 ─ 重定价 ─ ⚠️ 预登记冻结但 SDK 无行情接口 ⇒ 实现为 CARRY 顺延
12-01 (二) 17:10  ── 首轮对账（结算前，仅参考）
12-01 (二) 18:10  ── 补快照 + 正式对账 ────────────→ sim_reconcile/20261201.json
                         ↓ 自动判定 + 生成更正段（P1/P2 UNVERIFIABLE）
12-02 (三)        ── 邮件归档 + 证据留痕
```
⚠️ 11-30 是周一（`MonthEndUnverified` 规则要求数据末日必须是该月最后一个交易日，11-30 满足）。11-23 不是月末，**不能**作为决策日。

---

## 4. 执行机方案（需专家裁决）

### 方案 A：云端自证（本机退场）
在云主机 `/root/.ssh/` 生成一把专用 ed25519 密钥并把公钥加入自身 `authorized_keys`，使 `--remote` 能从云端 ssh 回自己。

- **优点**：改动最小；真正单机全自动；timer/service 全在云端。
- **缺点（严重）**：`check_channel_isolation.py --remote` 的设计意图是"**本地工作树 vs 远端部署目录**"的交叉比对。云端自连后，`REPO=/opt/quant` 且远端也是 `/opt/quant`，等于**自己比自己**——比对退化为恒等，隔离检查失去意义。这与"两法一致 ≠ 实现正确"的已知陷阱同类。
- **缓解**：必须保留本机旁证——自动化前由本机跑一次 E4 并把结果（含 `FAIL 0`、59/59、commit）落盘为带哈希的 preflight 产物，云端执行器把它作为只读证据；云端自连结果**不得单独**判 PASS。
- **裁决点**：是否接受"自证 + 本机旁证"的双证据结构？若不接受，E4 无法全自动。

### 方案 B：本机出证 + 云端执行（两段式）
本机 09:20 跑 E4 → 把 PASS 结果写成带签名的 preflight 产物上传云端 → 云端 09:35 读该产物作为 E4 证据后报单。

- **优点**：保留"他证"语义，不弱化隔离检查；不新增云端私钥。
- **缺点**：**依赖本机在场**，与用户"全部自动、在服务器自动"的要求冲突；本机需保持开机与网络；多一次跨机产物传递，时效与哈希校验要重新设计。
- **裁决点**：若"全部自动"是硬要求，此方案不合格；若可接受"一次性预置证据"，它最安全。

### 方案 C：把凭据搬到本机（不推荐）
把 `DQ_GM_TOKEN` / `DQ_GM_ACCOUNT_ID` 与 gmtrade wheel 装到 Windows 本机，全部在本机跑。

- **缺点**：token 离开云端（安全边界扩大）；本机非 7×24；与"服务器自动"背道而驰。**仅列出供排除。**

**执行方倾向**：方案 A + 强制本机旁证（即把 A 的缓解措施写成硬门控），但这是**判据层面的变更**，按纪律必须经账户持有人书面授权 + 新预登记，不能由执行方自行决定。请专家裁决。

---

## 5. 具体实施设计（若方案 A 获批）

### 5.1 新增 systemd 单元（全部 root 可写，属主 deepquant）
```
deepquant-drill-prep.timer       OnCalendar=Mon..Fri 17:20   （或一次性 11-27/11-30）
  → dryrun_decision_day.py（主 venv）
  → preflight_decision_day.py --cloud（主 venv）
  → 产出带 sha256 的 E2 证据包

deepquant-sim-exec.timer         OnCalendar=2026-12-01 09:35（一次性，Persistent=false）
  → deepquant-sim-exec.service
      User=deepquant, WorkingDirectory=/opt/quant
      EnvironmentFile=/opt/quant/env
      ExecStart=/opt/quant/.venv-gm/bin/python scripts/sim_rebalance_executor.py --execute
      ⚠️ 启动前必须先跑 --gates 只读复核，任一 FAIL ⇒ 不进 --execute

deepquant-sim-reconcile.timer    OnCalendar=2026-12-01 18:10（一次性）
  → paper_account_adapter.py（.venv-gm，补结算后快照）
  → sim_reconcile.py（主 venv）→ sim_reconcile/20261201.json
```
所有单元设 `Persistent=false`：错过时点**不补跑**，避免用过期数据生成"格式合法、日期错误"的记录。

### 5.2 执行器内部改造（最小改动）
1. 在 `run_gates()` 之前加"执行日/时点"自检：`now` 必须由显式参数注入（现有实现已是硬编码 `2026-12-01T09:35`，符合），不读系统时钟。
2. E4 改造：从"子进程 `--remote`"改为读 §4-A 的本机旁证产物 + 云端本地哈希比对；**不得**把"文件不存在 / 输出为空"判为 PASS。
3. `--execute` 前统一 `notional` 口径：用真实计划金额复核 `max_notional ≤ available`，不用占位数（现有实现已是真实金额，保持）。
4. 每次报单前重校授权六项（现有实现已有，保持）。

### 5.3 改造的验证（铁律）
- 改检查器前先跑变异测试；变异必须**真改行为**（DENY→ALLOW 必须被检出），不接受"换个拒绝理由"。
- 至少 8 条变异：幂等键随机化 / 台账缺失放行 / 授权缺失放行 / 上取整超预算 / 非目标不清仓 / 静默超预算 / 门控失败返 0 / E4 恒通过。
- 回归四连：pytest tests（3651）→ property_checks（9/9）→ pytest scripts/tests → verify_exec_all；后端须带 `PYTHONPATH=<repo>/backend/src`。
- 云端实跑：`--self-check` PASS + `--gates` rc=4（当前未满足时）+ 两处部署目录逐位一致。
- manifest `--write` 重生成后本地/云端 59/59 PASS（当前 HEAD `6a6f4d6`，清单记录生成时 commit `822fb976` 属设计行为，非过期）。

---

## 6. 风险披露（请专家重点审）

### 6.1 E4 自证的结构性弱点
方案 A 若不加本机旁证，隔离检查退化为恒等通过，等于**把"没检出"当成"没问题"**——这正是本项目已复发 12 次的错误模式。必须把旁证做成硬门控（旁证缺失/哈希不符 ⇒ FAIL），不能只写警告。

### 6.2 P1 / P2 无法检验（已知预登记缺口，不改预登记）
- **P1**：`client_order_id` 无法透传（SDK `order_volume` 无该入参）⇒ 幂等只在本地 `sim_orders` 去重，预登记假设 H1「服务端按 cl_ord_id 去重」**未经证实**。
- **P2**：SDK 无行情接口（`get_*` 只有 cash/positions/orders/execution_reports/unfinished_orders/strerror/version）⇒ 10:30/14:30 重定价不可实现，`REPRICE_IMPLEMENTABLE=False`，实际为单次限价 + CARRY 顺延。**P2 无法被检验**。
- 按纪律：H1–H7 不成立 ⇒ **停机 + 出更正**，不改判据、不重跑凑通过。报告须自动标注 `StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`、`MaturityLevel=EXECUTION_READY_GATED`、`MDE_NOT_ESTIMABLE`。

### 6.3 结算时点冲突（自动化后新暴露）
`daily_chain.sh` 原注释明确："本链路 17:10 跑，而掘金沪深账户 18:00 才结算……**将来若允许报单，必须重评时点**"。当前零委托所以自洽；12-01 一旦真下过单，17:10 的快照是"收盘后、结算前"，与当日委托状态不自洽。必须在 18:00 后补一次快照再做正式对账（§5.1 的 `T3`）。

### 6.4 定时器的"错过即失效"
`Persistent=false` 意味着 12-01 09:35 若机器宕机/网络断，任务**不会补跑**。这是有意设计：事后补跑会用错日期的数据，产出格式合法但语义错误的结果。请专家确认这一取舍。

### 6.5 自动化不改变结果归属
演练结果标 `SIM_DRILL`，只验执行链路，**不计 G1/G2/S4、不进可引集合、不作收益判断**。`PROBE` 同理，分开计数、分开授权。`LIVE_ENABLED` 保持 `false`。

### 6.6 授权边界
U-16 版本 A 明确禁止执行方"创建或修改任何授权文件"；本提案不涉及任何授权文件写操作。方案 A/B 的选择涉及**判据变更**，按预登记 §2「任一维度改动 ⇒ 新预登记 + 新授权」须另走书面授权，本提案不代替。

---

## 7. 验收标准（自动化完成的判定）

1. 12-01 09:35 timer 自动触发，执行器 `rc=0`；若任一门 FAIL ⇒ `rc=4` 且零真实委托。
2. E1/E2/E3/E4 四门全部 PASS，其中 E4 须含本机旁证（若采方案 A）。
3. `forward/sim_orders/20261201.jsonl` 有条目，cid 集合双向差集逐条可解释，`n_unexplained=0`。
4. 18:10 结算后对账产物 `forward/sim_reconcile/20261201.json` 存在，`NO_INPUTS` 未触发（触发即停机，不判 PASS）。
5. P1/P2 自动标注 `UNVERIFIABLE` 并写更正段；原文不改写。
6. 回归四连全绿；变异 8/8 检出；manifest 本地/云端 59/59 PASS；数据层 sha 跑前跑后一致。
7. 邮件归档含：执行日、四门状态、委托计数、对账摘要、限制与更正段、预登记哈希。

---

## 8. 引用规范（若本报告被引用）

- 预登记：`U16-EXEC-1`，sha256 `53ace8c5064f…`（`docs/U16_EXEC_PREREG_2026-11-16.md`）
- 实施结果：`reports/U16_EXEC_IMPL_RESULT_2026-11-16.md`（§4 三条限制）
- 提交包：`reports/U16_EXEC_COMMIT_PACKAGE_2026-11-16.md`
- 代码提交：`822fb97`（执行器）/ `6a6f4d6`（清单刷新）
- `StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`｜`MaturityLevel=EXECUTION_READY_GATED`｜`MDE_NOT_ESTIMABLE`
- 证据目录：`research/stock_pick_2026-10-25/forward/{ledger,dryrun,sim_orders,sim_reconcile}/`

---

## 9. 给专家的三个问题

1. **E4 方案**：接受"云端自证 + 本机旁证"的双证据结构，还是坚持"本机出证 + 云端执行"（放弃全自动化）？
2. **错过时点**：确认 `Persistent=false` 不补跑的取舍吗？
3. **P1/P2**：在 SDK 限制无法消除的前提下，是否接受"照常执行 + 自动出更正"作为一个可接受的工程结论，还是要求必须先解决 SDK 缺口再跑？

---
**报告性质**：工程变更提案，未实施、未授权、未发单。引用前请核对上述哈希与产物目录。
