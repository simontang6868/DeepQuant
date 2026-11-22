# PROBE / DRYRUN 执行清单（2026-11-23 → 11-25）｜一页纸照做版

> 生成 2026-11-22 19:55 CST｜性质：执行清单，不是预登记、不产生授权效力。
> 依据：外部专家第 10 轮四条；`LIVE_ENABLED=false`；全程不得实盘。
> 标记：✅ 已完成（有产物）｜⬜ 待做｜❓ 须账户持有人本人决定。

---

## 一、已提前完成（11-22，比原计划 11-23 早一天）

| # | 项 | 结果 | 产物 |
|---|---|---|---|
| ✅ 1 | **gm-paper 只读连通性探针** | **`CONNECT_OK`**：nav=100000、available=100000、positions=0、`n_real_orders=0`（未发单） | `scripts/probe_connectivity_check.py` → `forward/probe_connectivity_2026-11-22.json` |
| ✅ 2 | **PROBE 三腿 smoke test** | **SMOKE_PASS**：self-check PASS、buy/sell/decontam dryrun 全通、n_real_orders=0；真实授权未触碰 | `scripts/probe_smoke_test.py` → `forward/probe_smoke_20261122.json` |

**连通性探针踩到的两个坑（写在这里避免 11-25 现查）**：

- `gmtrade` **只装在 `/opt/quant/.venv-gm`**，`/opt/quant/.venv` 与 `/usr/bin/python3` 都 `ModuleNotFoundError`；
- 凭据来自 `/opt/quant/env`（600、systemd `EnvironmentFile` 注入），须 `set -a; source /opt/quant/env; set +a`。**`probe_executor.py` 真实路径读 `os.environ["DQ_GM_TOKEN"]`，若手工 SSH 不 source env 会直接崩**。

**连通性复跑命令**（11-24 可选再跑一次，30 秒）：

```bash
ssh -i ~/.ssh/fk_server_ed25519 root@1.92.141.140 \
'set -a; source /opt/quant/env; set +a; /opt/quant/.venv-gm/bin/python /opt/quant/scripts/probe_connectivity_check.py'
```

---

## 二、11-23（周一）必做

### ⬜ 3. 缺价行为已查清 —— 只需定"11-25 价格来源"

**代码事实（已实测）**：

| 场景 | 行为 | 证据 |
|---|---|---|
| dryrun 缺 `601398` 价格 | **不报错**，notional 只算另一只（980 元），codes 仍列两只 | `probe_smoke_test` 补测 |
| 真实下单缺 `601398` 价格 | 第 130 行 `prices[c]` ⇒ **`KeyError`**，崩在下单循环前 | `scripts/probe_executor.py:118,130` |

⇒ 不是"静默买 1 只"，是**直接崩**。但异常路径无测试覆盖，仍按 P0 处理。

**❓ 须本人定 11-25 价格来源（三选一，执行方不代选）**：

| 选项 | 做法 | 风险 |
|---|---|---|
| A（推荐） | 11-25 09:30 集合竞价后，手工读 `601398/600000` 现价传入 `--price` | 需在场；价格真实 |
| B | 用 11-24 收盘价 | 隔夜跳空可能大，但可提前准备 |
| C | 用 09:35 实时快照 | 需脚本支持，当前 CLI 无此选项 |

**硬规定（建议写进预登记）**：`--price` 必须同时给全 `601398` 与 `600000` 两个 code；缺任一 ⇒ **不发单、报错退出**，不得用 0 或缓存价。

### ⬜ 4. 11-25 09:35 谁执行 / 迟到窗口 / 去污次数（❓ 治理最后一公里，须本人拍板）

| 问 | 建议 | 须本人确认 |
|---|---|---|
| **谁在 09:35 跑** | 执行方远程 SSH 到执行机跑（本地无授权文件、无 venv-gm）；账户持有人在场监督 | ❓ 执行方 11-25 上午是否有其他安排 |
| **在哪跑** | **必须在执行机**：`/opt/quant/.venv-gm/bin/python` + `source /opt/quant/env` + `probe_executor.py --leg buy` | ❓ 若是本地跑，授权文件与 venv 都不在本地（已查） |
| **迟到窗口** | 限价单全天有效，建议写死 **最晚 11:30（上午收盘）**；超过 ⇒ 顺延下一交易日、不硬凑当天 | ❓ 09:35 未跑成是否算失败 |
| **去污最多几次** | 建议 **3 次**（10:00 / 14:00 / 14:55），3 次后 `positions` 非空 ⇒ 判 11-27 **no-go**，不再第 4 次 | ❓ 确认次数与时点 |

> 这四项不是文档，是人的安排。执行方不能替账户持有人决定"谁在场"。

---

## 三、11-24（周二）17:10 后

### ⬜ 5. 重跑 smoke test（覆盖真实输入）

日更链 17:10 跑完会重写台账；11-22 的 SMOKE_PASS 读的是周末静态快照，**不覆盖 11-25 的输入**。

```bash
cd "D:/DeepQuant power by txm/DeepQuant-main"
PYTHONPATH=scripts C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe \
  -u scripts/probe_smoke_test.py
# 期望 verdict=SMOKE_PASS、n_real_orders=0；产物覆盖 forward/probe_smoke_20261122.json（文件名保留）
```

同时复跑连通性探针（§二命令），确认 11-24 日更没动 env/venv。

### ⬜ 6. 外部锚字段级核对（前轮遗留，仍未做）

`forward/authorization_statements/EXTERNAL_ANCHOR_pending_2026-11-22.md` 修订 01 的**四字段核对栏仍空**。须本人勾 `not_after`/`max_notional`/`scopes`/`channel`；执行方不代勾。

---

## 四、11-25（周三）当天

| 时点 | 动作 | 门 |
|---|---|---|
| 09:30 | 读集合竞价价，确定 `--price 601398=… --price 600000=…` | 价格来源须符合 §二第 3 项 |
| 09:35 | 执行机跑 `probe_executor.py --leg buy`（source env + venv-gm） | 先确认连通性 PASS（§一） |
| 当日 | 记录成交回报双时间戳、`cash_before/after/费用合计` | `decision=EXECUTED`、n_real_orders=2 |
| 11-26 | `--leg sell` + `--leg decontam`（≤3 次，见 §二第 4 项） | `positions=[]` 唯一去污标准 |

**失败关闭**：任一门不过 ⇒ rc=4、零报单、保留原始产物、出更正；不重跑凑通过。

---

## 五、三天后的"只剩市场一个变量"核对表

- [ ] 连通性 CONNECT_OK（✅ 11-22 已达标；11-24 复跑）
- [ ] smoke test SMOKE_PASS（✅ 11-22 已达标；11-24 覆盖真实输入）
- [ ] 价格来源定死 + 缺价报错（⬜ 11-23）
- [ ] 09:35 执行人 / 迟到窗口 / 去污次数定死（⬜ 11-23，须本人）
- [ ] 外部锚四字段核对（⬜ 11-24，须本人）

> 五条全绿 ⇒ 11-25 只剩"市场怎么走"这一个不可控变量。工程侧 sp5 215P/1F(T6) 不因本清单放宽。
