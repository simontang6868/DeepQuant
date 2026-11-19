# 如期跑通模拟盘——工程就绪交付报告（2026-11-20 00:40）

> 授权：`FULLAUTO_GO_statement_2026-11-20.md`（sha `463bd735…`，逐字存证）
> 预登记先于代码冻结：PROBE `2998a7a0…`、断链A/方案甲 `0edbc480…`
> 基线：本地＝origin＝两处备份 `44ac7b4`；本轮改动待入库
> 性质：工程自证 `ENGINEERING_SELF_CHECK_ONLY`，`SIM_DRILL`，`LIVE_ENABLED=false`

---

## 0. 一句话结论

**两条断链已接通、PROBE 真实下单通道已就绪、方案甲落盘门槛已实现并通过双向变异。** 通往 12-01 如期跑通的工程障碍全部清除。但**真实委托必须按日期发生**：今天 11-20 不下单；11-25 PROBE 买、11-26 卖+去污、11-27 go/no-go、11-30 出证、12-01 主演练。我不会提前下单、不会提前 enable 定时器——这是纪律，不是拖延。

---

## 1. 本轮做了什么

### 1.1 授权与预登记（先于代码）
- 逐字存证授权原文 + shell 实测时间戳（接收 00:25:17 / 生成 00:26:09）。
- 冻结两份预登记（都不写自身哈希，侧车独立）：
  - `docs/PROBE_EXEC_PREREG_2026-11-20.md`（`2998a7a0…`）：PROBE 执行，含 7 条硬判据 P1-P7、现金三记录字段、`positions=[]` 唯一去污标准、created_at 双记录。
  - `docs/CORROB_CHAIN_PREREG_2026-11-20.md`（`0edbc480…`）：断链 A 追认 + 方案甲，含三零一不变判别式、窗口单一来源、双向变异 M10A/B。

### 1.2 断链 A 接通（出证 + 发布）
- `sim_corroboration.py` 新增 CLI：`--build`（本机出证，原子写 + sha256 侧车）、`--verify`（内部一致性 + 部署树双向集合全等）、`--remote`（云端复核）。自检 6 项 PASS。
- `heartbeat_alert.py` 新增 `_with_corroboration()`：旁证存在时把 sha256 + commit 并入 11-30 17:40 告警邮件正文（tamper-evidence 锚）；旁证不存在 no-op、侧车不符标 `ANCHOR_UNAVAILABLE`，**不改心跳检查 rc 语义**。三态离线验证 PASS。
- 冻结顺序经过实测验证：出证早于 manifest 重生成时，`--verify` 正确报 `CHANGED`（证明"出证晚于部署冻结"纪律生效）。

### 1.3 方案甲落盘门槛（搭车）
- **窗口单一来源**：执行器 `E0_TOLERANCE_MIN` 收敛为引用 `sim_exec_result.FRESHNESS_WINDOW_MIN`；`intended_now()` 与 `sim_exec_result.EXEC_INTENT` 启动校验一致，不一致 `Halt`。
- `write_result()` 在 `path=None` 时按 `started_at` 是否在意图窗口内分流：窗口外/不可解析 ⇒ 落 `.TEST_<stamp>.json`，窗口内 ⇒ 生产名 `20261201_result.json`。两处调用点（`_log_exec_result`、`main_logged`）自动受保护。
- 双向变异 M10A（窗口外→生产名）、M10B（窗口内→TEST 名）均 DETECTED，sha 恢复。

### 1.4 PROBE 真实下单通道
- 新增 `scripts/probe_executor.py`：复用 `GmTradeBroker`（`gm.order_volume` 限价，真单）+ 已签 `paper_authorization.json`（scope 已含 `sim_rebalance_order`，**不新建/不修改任何授权文件**）。
- 三段日期门：`--leg buy` 仅 11-25、`--leg sell` 仅 11-26、`--leg decontam` 仅 11-26 日终。
- 2 只×1 手（`601398`/`600000`，单价<40 元满足单笔≤4000 预算，预登记未写死代码故为实现选择）、cid PROBE 前缀、双记录 created_at、现金三字段、`positions=[]` 去污硬标准。
- dryrun 全腿 PASS；日期门错日 HALT；超预算被 guard 拦（证授权门有效）；零真实报单。

---

## 2. 验收证据

| 项 | 结果 |
|---|---|
| 回归四连 | **3651/1｜9/9｜759/1｜verify PASS** |
| 变异总计 | **14 条全检出**：执行器 8/8、M9 旁证 2/2、H9 1/1、哨兵 1/1、方案甲 2/2；各脚本恢复后 sha 一致 |
| 出证 CLI | `--build`/`--verify`/`--self-check` 全 PASS；篡改 commit ⇒ verify FAIL |
| 邮件外发 | 旁证不存在 no-op / 侧车一致附 sha / 侧车不符标 ANCHOR_UNAVAILABLE，三态 PASS |
| 方案甲 | 窗口单一来源实测相等、窗口外落 `.TEST_`、窗口内落生产名、不可解析落 `.TEST_` |
| PROBE | self-check PASS、buy/sell/decontam dryrun PASS、错日 HALT、超预算 DENY、零报单 |
| manifest | 63→**65 文件**，`sha_of_files=353b4589`；本地 65/65、云端 65/65 PASS；云端 json 属主 deepquant |
| .gitattributes | 新增文件均 `text: unset`（blob==工作树，无 CRLF 警告） |

---

## 3. 当前真实状态与必须按日期发生的事

| 日期 | 动作 | 当前状态 |
|---|---|---|
| **11-20（今天）** | 工程就绪、代码入库 | 零报单；定时器全 disabled |
| **11-25** | PROBE 买（2 只×1 手限价） | 通道已备好；触发时才下单 |
| **11-26** | PROBE 卖 + 日终去污 `positions=[]` | 已备好 |
| **11-27 17:00** | go/no-go：PROBE 干净 + 无 H 类 ⇒ enable DRYRUN prep timer | 检查点 |
| **11-30 17:10/17:20** | 日更出台账 + 出证 + 邮件外发 + 冻结 | 断链 A 已通 |
| **12-01 09:35** | 全门 PASS 才 enable exec timer 报单 | 主演练 |
| 12-01 09:45 / 18:10+ | 哨兵 / 结算后对账（验收第9条） | 已备好 |

**主武装判据不变**：enable exec timer 的唯一条件是"预测 12-01 09:35 时点全门 PASS"。任一红线未达 ⇒ 按授权条件项 C 顺延，不按期白跑。

---

## 4. 边界与未决（引用必带）

1. **不提前下单、不提前 enable 定时器**：今天到 11-24 之间零真实委托是设计。
2. **PROBE 授权复用已签文件**：`paper_authorization.json`（issued_by=simon）scope 已含 `sim_rebalance_order`、有效期至 12-02；本次未新建/修改任何授权文件。
3. **执行机=云端**（`.venv-gm` 有 gmtrade + token）；PROBE/主演练都在云端跑。本机做旁证出证 + E4 门控。
4. **P1/P2 仍无法检验**：SDK 无 `client_order_id` 入参（幂等仅本地）、无行情接口（限价单次不重定价）；按预登记停机出更正，不改判据。
5. **created_at 服务端语义**：强指示性证据，PROBE 首笔回报双记录原始 `seconds/nanos` + ISO 事后闭合。
6. `SIM_DRILL`，不计 G1/G2/S4，不作收益判断，不碰实盘/token/授权原文。

---

## 5. 下一步（按日期，不预制）

- 11-25：触发 PROBE buy（真实 2×1 手限价单，价格取当日合规限价）
- 11-26：触发 PROBE sell + decontam，验证 `positions=[]`
- 11-27 17:00：go/no-go 检查；通过则 enable DRYRUN prep timer
- 11-30：出证 + 邮件外发 + 冻结；enable exec 相关 timer
- 12-01：全门 PASS 则主演练；否则按 C 顺延

**本轮未提前下单、未 enable 定时器、未改授权原文、未碰实盘。**
