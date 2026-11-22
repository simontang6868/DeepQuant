# PROBE / DRYRUN 执行清单（2026-11-23 → 11-25）｜一页纸照做版

> 更新 2026-11-22 20:12 CST（专家第 11 轮后）｜性质：执行清单，不是预登记、不产生授权效力。
> 依据：外部专家第 10/11 轮；`LIVE_ENABLED=false`；全程不得实盘。
> 标记：✅ 已完成（有产物）｜⬜ 待做｜❓ 须账户持有人本人决定。

---

## §0 执行前置命令块（三条原样贴，不靠记忆）

**任何真实执行（连通性 / 最小下单 / 11-25 买卖 / 去污）都必须整块复制：**

```bash
ssh -i ~/.ssh/fk_server_ed25519 root@1.92.141.140
cd /opt/quant
set -a; . /opt/quant/env; set +a
/opt/quant/.venv-gm/bin/python -u <脚本路径> <参数>
```

| 行 | 作用 | 写错的失败模式 |
|---|---|---|
| `cd /opt/quant` | 预登记/产物/授权文件全部相对仓库根 | 找不到 `docs/*.md` ⇒ `PREREG_SHA_MISMATCH`；产物落到错误目录 |
| `set -a; . /opt/quant/env; set +a` | systemd `EnvironmentFile` 注入 `DQ_GM_TOKEN` / `DQ_GM_ACCOUNT_ID` | 少一步 ⇒ `ENV_MISSING` / 空 token |
| `/opt/quant/.venv-gm/bin/python` | `gmtrade` **只装在此 venv** | `.venv` 或 `/usr/bin/python3` ⇒ `ModuleNotFoundError` |

---

## 一、已提前完成（11-22）

| # | 项 | 结果 | 产物 |
|---|---|---|---|
| ✅ 1 | **gm-paper 只读连通性探针** | **`CONNECT_OK`**：nav=100000、available=100000、positions=0、`n_real_orders=0` | `forward/probe_connectivity_2026-11-22.json` |
| ✅ 2 | **PROBE 三腿 smoke test** | **SMOKE_PASS**：三腿 dryrun 全通 + 缺价门 PASS，`n_real_orders=0` | `forward/probe_smoke_20261122.json` |
| ✅ 3 | **账户字段级核对（专家 §五）** | `account_id_mask=0a47****************************2aa6`、`account_env_match=true`、`auth_account_ref_label=虚拟盘测试202611`、`channel_hint=SIM`；完整 UUID 不落盘 | `forward/probe_connectivity_identity_2026-11-22.json` |
| ✅ 4 | **缺价 KeyError P0（专家 §二）** | 循环**外**整批预检：缺任一 code ⇒ `MISSING_PRICE`、零报单、不建连；8 条测试 + 3/3 真变异检出 | `scripts/probe_executor.py`、`scripts/tests/test_probe_executor_guardrails.py` |
| ✅ 5 | **最小下单探针工程准备（专家 §一）** | 独立预登记冻结 `ccbc420d…`、脚本 9 条测试全绿、执行机 `--self-check` / `--dryrun` PASS | `docs/PROBE_MIN_ORDER_PREREG_2026-11-23.md`、`scripts/probe_min_order.py` |

**本轮真实环境又挖出四个隐形坑（dryrun 全绿也看不见）**：

1. `gmtrade.api` **没有 `cash()`**，只有 `get_cash()`，且返回 **Cash 对象不是 list** —— 旧 `gm.cash()[0]` 真跑必崩（已修并双形态兼容）。
2. 执行机**缺 `docs/PROBE_EXEC_PREREG_2026-11-20.md`**，导致 `probe_executor --self-check` 直接 `PREREG_SHA_MISMATCH` ⇒ **11-25 主 PROBE 原本会 HALT**（已按本地冻结 sha `2998a7a0…` 补齐并复跑 PASS）。
3. `probe_executor._auth_ok` 把 `available` **写死 100000**，绕开「`max_notional ≤ 实时 available`」硬门（已改为账户快照实时值）。
4. `account_name` 在真实 gm 里就是完整 UUID，直接落盘等于凭证级泄漏（已改掩码）。

**连通性复跑命令**（11-24 再跑一次，30 秒）：

```bash
cd /opt/quant && set -a; . /opt/quant/env; set +a
/opt/quant/.venv-gm/bin/python -u scripts/probe_connectivity_check.py
```

---

## 二、11-23（周一）必做

### ⬜ 6. 最小下单探针（专家 §一 P0，真下单但即时撤）

已冻结预登记与脚本；**11-23 开盘后由账户持有人明确 GO 再跑**（本动作超出 11-25 主 PROBE 的冻结日，不代签）：

```bash
cd /opt/quant && set -a; . /opt/quant/env; set +a
/opt/quant/.venv-gm/bin/python -u scripts/probe_min_order.py \
  --prev-close <11-20 收盘价，人工从行情软件抄录>
```

脚本自算跌停价（前收×0.9，ROUND_HALF_UP 2 位）→ 买 `601398` 100 股 → 取 order_id → 撤单 → `get_orders()==[]`、`positions` 不变 ⇒ `MIN_ORDER_OK`。
若返回 `ORDER_REJECTED` / `CANCEL_FAILED` / `UNEXPECTED_FILL` ⇒ **保留产物、升级持有人，不发第二单、不自动卖出**。

### ✅ 7. 缺价行为（已修完，不再是决策项）

`--price` 必须同时给全 `601398` 与 `600000`；缺任一 ⇒ `MISSING_PRICE`、`n_real_orders=0`，dryrun 同样拒绝。不得用 0 或缓存价。

### ❓ 8. 价格来源（四问答复见 `reports/EXPERT_REVIEW_RESPONSE_R11_2026-11-22.md`）

| 项 | 本清单采用 | 备注 |
|---|---|---|
| 价格时点 | **09:34:59 最新成交价**（不是 09:30 集合竞价） | 人工读价后 `--price 601398=… --price 600000=…`；开盘即涨跌停 ⇒ 接受"挂单未成交"为合法 PROBE 结果，预登记已写明，不得当天现议 |
| 谁执行 | **执行方 SSH 执行 + 第二人在线** | 第二人只需在场 + 能接电话；`gmtrade` 无 `client_order_id`，异常须立即人工撤单/核持仓 |
| 迟到窗口 | **09:40 硬截止**（不是 11:30） | 09:35→09:40 覆盖 SSH/导入慢；超过即视为出事、不硬凑，顺延下一交易日 |
| 去污 3 次 | **10:00 / 14:00 / 14:55**，但区分原因 | 仍有持仓：停牌/涨跌停 ⇒ 市场原因、按 B 分支顺延；脚本/网络报错 ⇒ 技术失败，**不得顺延**（链路有问题） |

---

## 三、11-24（周二）17:10 后

### ⬜ 9. 重跑 smoke test（覆盖真实输入）

```bash
cd "D:/DeepQuant power by txm/DeepQuant-main"
PYTHONPATH=scripts C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe -u scripts/probe_smoke_test.py
```

期望 `SMOKE_PASS` + `missing_price_guard=PASS`、`n_real_orders=0`。同时按 §一命令复跑连通性与执行机 `--self-check`，确认日更没动 env/venv/预登记。

### ⬜ 10. 外部锚字段级核对（前轮遗留，仍未做）

`forward/authorization_statements/EXTERNAL_ANCHOR_pending_2026-11-22.md` 修订 01 的**四字段核对栏仍空**。须本人勾 `not_after`/`max_notional`/`scopes`/`channel`；执行方不代勾。

---

## 四、11-25（周三）当天

| 时点 | 动作 | 门 |
|---|---|---|
| 09:34:59 | 读最新成交价，定 `--price 601398=… --price 600000=…` | 价格来源须符合 §二第 8 项；两码缺一即 `MISSING_PRICE` |
| 09:35 | 执行机跑 `probe_executor.py --leg buy`（§0 三行） | 先确认连通性 PASS + 11-23 `MIN_ORDER_OK` |
| **09:40** | **硬截止**；未跑成即顺延，不硬凑 | 第二人在线方可尝试最后一次 |
| 当日 | 记录成交回报双时间戳、`cash_before/after/费用合计`、`available_at_check` | `decision=EXECUTED`、`n_real_orders=2` |
| 11-26 | `--leg sell` + `--leg decontam`（≤3 次，区分技术/市场原因） | `positions=[]` 唯一去污标准 |

**失败关闭**：任一门不过 ⇒ rc=4、零报单、保留原始产物、出更正；不重跑凑通过。

---

## 五、`max_notional` 一句话澄清（专家 §四）

**既不是纯单笔也不是纯累计：guard 的 c5 同时要求 `cap ≤ 实时 available` ∧ `单笔 notional ≤ cap` ∧ `累计 + 本笔 ≤ cap`。** 100000 恰等于仿真账户初始 nav，但**不是"任何成交吃掉全部额度"**：11-25 两笔共约 1,590 元、12-01 主演练约 2.5 万，都在额度内；额度按**同一 guard 调用里的累计**校验，跨日已平仓持仓不累计。⚠️ 若 12-01 改为 25% 仓且同时持有未平仓单，须按真实 `available` 重算，不得拿 100000 当可用余额。

---

## 六、三天后的"只剩市场一个变量"核对表

- [x] 连通性 CONNECT_OK（11-22 达标；含账户字段级核对；11-24 复跑）
- [x] smoke test SMOKE_PASS（11-22 达标；含缺价门；11-24 覆盖真实输入）
- [x] 价格来源定死 + 缺价整批报错（⬜ 11-25 价格人工读数待执行）
- [x] 09:35 执行人 / 迟到窗口 / 去污次数定死（09:40、3 次分类）
- [ ] **11-23 最小下单探针 `MIN_ORDER_OK`**（⬜ 须本人口头/书面 GO）
- [ ] 外部锚四字段核对（⬜ 11-24，须本人）

> 全绿 ⇒ 11-25 只剩"市场怎么走"。工程侧 sp5 215P/1F(T6) 不因本清单放宽。
