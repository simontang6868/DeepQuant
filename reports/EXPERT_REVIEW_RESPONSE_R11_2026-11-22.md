# 对外部专家第 11 轮意见的答复（2026-11-22 20:15 CST）

> 性质：执行前答复 + 已落地修复回执。`LIVE_ENABLED=false`；本轮**没有发任何真实订单**。
> 结论先行：您点的两个 P0 都已落到代码与预登记；在把脚本搬到执行机实跑时，又挖出**四个新的隐形坑**，其中两个足以让 11-25 直接 HALT。

---

## 一、四问，逐条回答

| # | 您问 | 我的答复（采用值） | 依据 |
|---|---|---|---|
| 1 | 价格时点 | **采纳 A 的改写：09:34:59 最新成交价**，不是 09:30 集合竞价。开盘即涨跌停 ⇒ 接受"挂单未成交"作为 PROBE 合法结果，已写进清单；11-25 当天不现议 | 限价单更容易在 09:35 形成真实回报；未成交也是可记录结果，不伪造成交 |
| 2 | 09:35 谁执行 | **执行方 SSH 执行 + 第二人在线**（只需在场、能接电话） | `gmtrade` 无 `client_order_id`（`sim_rebalance_executor.py:476-480`），异常需立即人工撤单/核持仓 |
| 3 | 迟到窗口 | **09:40 硬截止**，不用 11:30 | 09:35→09:40 覆盖 SSH/导入慢；超过即"出事"，不硬凑 |
| 4 | 去污 3 次 | **3 次（10:00/14:00/14:55），但强制分类**：停牌/涨跌停 ⇒ 市场原因、按 B 分支顺延；脚本/网络报错 ⇒ 技术失败、不得顺延 | 技术失败靠顺延不会自愈，留痕不同 |

## 二、P0 §一：最小下单探针 —— 工程已备妥，**真下单等您 GO**

- 独立预登记：`docs/PROBE_MIN_ORDER_PREREG_2026-11-23.md`，冻结 sha `ccbc420d0761cb919…`（侧车同值）；**不修改** PA-GM-PROBE-2 任何参数。
- 脚本：`scripts/probe_min_order.py`。跌停价不手填：`Decimal(prev_close)×0.9`，ROUND_HALF_UP 两位；买 `601398` **100 股** → 取 order_id → `gm.order_cancel` → `get_orders()==[]`、positions 不变 ⇒ `MIN_ORDER_OK`。
- 硬门：账户锚不一致 / 已有在途单 / 授权非 ALLOW 6/6 ⇒ **零报单**；`ORDER_REJECTED` 不发第二单；`CANCEL_FAILED` / `UNEXPECTED_FILL` 升级您本人，**不自动卖出**。
- 证据：9 条测试全绿；执行机实跑 `--self-check` PASS、`--dryrun` PASS（`n_real_orders=0`）。
- **未执行真下单**：今天是 11-22 周日，且这是一笔超出 11-25 冻结日的新动作。11-23 开盘后您给一句 GO，我立即按 §0 命令块执行并回传 `order_id`/撤单/持仓三项结果。

```bash
cd /opt/quant && set -a; . /opt/quant/env; set +a
/opt/quant/.venv-gm/bin/python -u scripts/probe_min_order.py \
  --prev-close <11-20 收盘价，人工从行情软件抄录>
```

## 三、P0 §二：缺价"部分发送" —— 已修，且变异测试证明旧代码真会部分发送

- `probe_executor.py` 在**建连与循环之前**做整批预检：`missing = [c for c in codes if c not in prices …]`，非空 ⇒ `MISSING_PRICE`、`n_real_orders=0`、不实例化 broker、不进授权门。buy/sell 两腿都覆盖，dryrun 同样拒绝。
- TDD：先写 8 条测试跑出 **7 红**（含"第 1 只正常、第 2 只缺价"场景），修复后 **8/8 绿**。
- 真变异 `scripts/probe_guardrail_mutation_test.py`：M1 同时恢复旧预检与旧 `.get(c,0)` 名义金额口径后，**实测复现第 1 只已发出、第 2 只 KeyError**，被检出；M2（available 写死）、M3（账户不掩码）同样检出 ⇒ **3/3**。变异锚点缺失即报错，杜绝假变异。
- smoke test 新增 `missing_price_dryrun` 门，11-22 复跑 `SMOKE_PASS`、`missing_price_guard=PASS`。

## 四、P1 §三：§0 执行前置命令块 —— 已原样贴进清单

`docs/PROBE_EXEC_CHECKLIST_2026-11-23_TO_25.md` 顶部新增 §0，三行逐条写明写错后的失败模式：

```bash
ssh -i ~/.ssh/fk_server_ed25519 root@1.92.141.140
cd /opt/quant
set -a; . /opt/quant/env; set +a
/opt/quant/.venv-gm/bin/python -u <脚本路径> <参数>
```

## 五、P1 §四：`max_notional` 是单笔还是累计？ —— **两者同时是**

判据在 `scripts/paper_authorization_guard.py:104`（预登记 `PAPER_AUTH_GUARD_PREREG_2026-11-09.md` §5 第 5 行）：

```python
c5 = (cap <= avail and n <= cap and cum + n <= cap)
```

即 **100000 元上限同时约束单笔、本批累计，并要求 `cap ≤ 实时 available`**。它与账户初始 nav=100000 数值相等，但不是"任何成交吃掉全部额度"：

| 动作 | 名义金额 | 结论 |
|---|---|---|
| 11-23 最小探针 | ~550 元 | 额度内 |
| 11-25 主 PROBE | 2 只×1 手 ≈ 1,590 元 | 额度内 |
| 12-01 主演练（20% 仓） | ≈ 2.5 万 | 额度内；须按**实时** `available` 重校 |

跨日已平仓持仓不计入 `cumulative`；同一批多笔按本批累计校验。⚠️ 顺带承认原 `probe_executor._auth_ok` 把 `available` 写死为 100000，**绕开了这条硬门**，已改为账户快照实时值（100000 恰等于 nav 只是巧合，不是授权设计）。

## 六、P1 §五：连的是哪个账户 —— 已加字段级核对，不是只看"成功"

新增产物 `research/stock_pick_2026-10-25/forward/probe_connectivity_identity_2026-11-22.json`（**保留原 CONNECT_OK 产物不改写**）：

| 字段 | 实测值 |
|---|---|
| `verdict` | `CONNECT_OK` |
| `account_id_mask` / `expected_account_id_mask` | `0a47****************************2aa6`（前4+后4，中间 28 星） |
| `account_env_match` | `true`（`get_cash().account_id == DQ_GM_ACCOUNT_ID`） |
| `auth_account_ref_label` / `channel_hint` | `虚拟盘测试202611` / `SIM` |
| `positions_with_volume` / `n_real_orders` | `[]` / 0 |

完整 UUID **不落盘**：`account_name` 在真实 gm 里就是 UUID，原样写文件等于凭证级泄漏，已改掩码。账户掩码不一致时 verdict 变 `CONNECT_IDENTITY_MISMATCH` 并 rc=4，零报单。

## 七、把脚本搬到执行机后，又挖出四个 dryrun 看不见的坑

| # | 坑 | 后果 | 状态 |
|---|---|---|---|
| 1 | `gmtrade.api` **没有 `cash()`**，只有 `get_cash()`，且返回 **Cash 对象不是 list** | 旧 `_cash_fields` 用 `gm.cash()[0]` ⇒ 真跑 `AttributeError`/`TypeError` | 已改为两种 API/两种返回形态都兼容；执行机复跑 `CONNECT_OK` |
| 2 | 执行机**没有** `docs/PROBE_EXEC_PREREG_2026-11-20.md` | `probe_executor --self-check` 直接 `PREREG_SHA_MISMATCH`，**11-25 主 PROBE 会 HALT** | 已按本地冻结 sha `2998a7a0…` 补齐；复跑 `PASS`，现场 sha 一致 |
| 3 | `_auth_ok` 的 `available` 写死 100000 | 绕过 `max_notional ≤ 实时 available` 硬门 | 已用账户快照实时值；3/3 变异检出 |
| 4 | `account_name` 原样落盘 | 完整账户 UUID 泄漏 | 已掩码，测试断言全串不在产物中 |

另：11-22 的 `probe_connectivity_2026-11-22.json` 字段结构与本地脚本不同（是执行机内联命令产生的），**归因为"产物非脚本生成"而非脚本 bug**；本轮已用统一脚本重跑并另存身份产物，原始文件保留。

## 八、账本与立场

- 本轮新增测试：护栏 8、最小下单 9、账户身份 3 = **20 passed**；`scripts/tests` 全量 **781 passed / 1 skipped**；3/3 真变异；smoke `SMOKE_PASS`。
- 纪律瑕疵与更正：单测一度把假 broker 的 `FAKE_SENT` 落进 `forward/sim_orders/PROBE_20261125_buy.json`、本地调试一度覆盖原 `probe_connectivity_2026-11-22.json`。两者均已识别：**原始 CONNECT_OK 从 HEAD 恢复、假委托文件删除、单测改为只 mock 落盘**，并在报告中留痕，不改写历史结论。
- 授权续期已在执行机落地：`paper_authorization.json` 的 `not_after=2027-01-31T23:59:00+08:00`，`max_notional=100000`、四 scopes、`channel=gm-paper` 未变；签署 sha 前缀与回执见既有 `SIGNED_AUTH_2026-11-22.md`。
- 立场七条**全部不变**（A ffill 后修、sp5 纳第五连、20%≥50 万/25%≥40 万且 S3≤2500 万、签字人已签 11-27 不再签、仿真 A/实盘 B、8%/12%、S0 仅验执行）。
- **未决项**：① 11-23 最小下单真执行的 GO；② 外部锚四字段本人勾选；③ 09:34:59 价格人工读数与第二人在线；④ 11-24 17:10 后覆盖真实输入的 smoke 复跑。

> 您说"清单已经写得很好了，不再动它"——我只加了 §0 三行与缺价前置门，并把您四问的结论并入正文；结构未改。工程侧不再雕清单：下一步是 11-23 的最小下单探针。
