# 预登记：仿真报单授权校验器（PA-GUARD-1）

**2026-11-09**｜触发：U-15 书面授权声明已收到并存证（`forward/authorization_statements/U15_statement_2026-11-09.md`，sha256 `3207bff41a58…`）
**性质**：**工程闸门预登记，不是策略实验**。不改窗口/基准/池/成本口径，不改任何策略参数，不新增因子。
**状态**：`DESIGN_AND_GUARD_IMPL`｜`NO_ORDER_SENT`｜`NOT_WIRED_INTO_DAILY_CHAIN`｜`LIVE_ENABLED=false`

---

## 一、要解决什么、预期方向与机制（落在可算的量上）

**问题**：书面授权已给，但「每次报单前重校六项」目前只是文档里的一句话，没有可执行的判定器；若不先冻结判据就写，容易出现「闸门调用被测函数」「容差拍脑袋」「空输入判 PASS」这三类已在本项目复发过的假闸门。

**预期方向**：校验器对**任一**不合格输入必须返回 `DENY` 与具体原因码；对唯一合格样本返回 `ALLOW`。判据落在三个可算量上：① 六项校验各自的拒绝覆盖数（应为 6/6）；② 变异检出数（应等于变异总数）；③ 空/缺失/不可解析输入下的 `DENY` 数（应为 100%，不得有 INFO 冒充 PASS）。

**机制**：把「文件存在且可解析」与「六项内容合规」拆成两段；哈希用独立算术重算而非调用被测函数自身；金额校验同时吃「单笔」与「累计」两个入参；时间用注入的 `now`，不读系统Clock 之外的数据。**结构假设**：授权文件是单个 UTF-8 JSON 对象、字段值无嵌套时间对象；若实际文件不满足，判 `DENY` 而不是猜。

⚠️ 本预登记**不替授权添加它没有的性质**：不把「max_notional ≤ available」写成「不会超目标」；不把「有效期」写成「可自动续期」；不把「建立能力」写成「已获执行许可」。

## 二、按字面执行的授权要素（原文见存证）

| 字段 | 冻结值 | 来源 |
| --- | --- | --- |
| `schema_version` | `paper_auth_v1` | R6-1 方案 |
| `channel` | **仅 `gm-paper`** | 声明 ① |
| `max_notional` | **100000**（元） | 声明 ③ 字面值；⚠️ 模板曾建议 20,000–30,000，**按字面登记不擅自压低**；仍受 `≤ 实时 available` 与单笔＋累计双校约束 |
| `not_after` | `2026-12-02T23:59:00+08:00` | 声明 ④；过期不自动续期 |
| `scopes` | `query_cash` / `query_positions` / `query_execution_reports` / `sim_rebalance_order` | 声明 ⑤ 的四类动作，冻结为枚举；未列一律拒绝 |
| `account_ref` | **由用户本人在文件中填写**，agent 不索取、不猜测、不从 env 抄入 | 声明 ②⑦ |
| `statement_ref` | `3207bff41a58`（书面声明 sha256 前 12 位） | 存证文件 |
| `sha256` | 见第三节口径 | 声明 ⑥ |

## 三、`sha256` 规范化口径（冻结，供用户本人填写文件时使用）

文件要求：**UTF-8 无 BOM、LF 换行、顶层为单个 JSON 对象**，`sha256` 字段为 64 位小写十六进制。

```
body = {除 "sha256" 外的全部字段}
规范化字节 = json.dumps(body, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")).encode("utf-8")
sha256     = hashlib.sha256(规范化字节).hexdigest()
```

**校验时用 `hmac.compare_digest` 比对**，不用 `==`；重算逻辑写在校验器里、独立实现，**不调用文件提供者给的任何哈希函数**。文件内容读入后再取 `sha256` 字段，禁止「先算全文哈希再和自己比」的自指实现。

## 四、六项校验与失败关闭（判据冻结）

| # | 校验 | 不满足 ⇒ |
| --- | --- | --- |
| 1 | `schema_version == "paper_auth_v1"` | `DENY:SCHEMA` |
| 2 | `channel == "gm-paper"`（出现 live/qmt/broker 一律拒绝） | `DENY:CHANNEL` |
| 3 | `now <= not_after` 且文件在有效期内；**不自动续期** | `DENY:EXPIRED` |
| 4 | 请求的 action ∈ `scopes` 且 ∈ 冻结的四类枚举 | `DENY:SCOPE` |
| 5 | `max_notional > 0` 且 `≤ available`，**并且** `单笔 notional ≤ 上限` 与 `累计+本笔 ≤ 上限` 同时成立 | `DENY:NOTIONAL` |
| 6 | 重算哈希与文件 `sha256` 一致 | `DENY:HASH` |

**失败关闭**：文件缺失、不可读、非 JSON、`sha256` 缺失/格式错、`available ≤ 0`、时间不可解析 ⇒ 一律 `DENY:NO_AUTH`，**不是「默认允许」，也不是 INFO**。任一失败即停机，不重试、不降级、不回退到「上次通过的授权」。校验器**不输出任何凭证值、不输出文件内容**，只回原因码。

## 五、本轮实现范围与明确不做

**做**：`scripts/paper_authorization_guard.py`（纯判定，无网络、无 `order_*`、无凭证读取）+ `scripts/tests/test_paper_authorization_guard.py` + `scripts/paper_auth_guard_mutation_test.py`（变异必须真改行为，覆盖六项校验与失败关闭）。

**不做（如实登记，避免假接入）**：
- **不创建、不修改 `paper_authorization.json`**——该文件只能由账户持有人本人创建（R6-1 硬边界、声明 ⑦）。
- **不接入日更链、不写正式台账、不向任何账户报单**：声明 ⑧ 明确「不是 12-01 演练的执行许可」，U-16 未给。
- **成交回报对账器与演练驱动本轮不实现**：它们需要外部真值（仿真成交回报）与 U-16 授权才能真验证；现在没有任何成交，硬写并宣称「验证通过」正是本项目反复禁止的「管道写了却没人用」。触发条件见第六节。
- 不解锁 `LIVE_ENABLED`、不改变四道实盘门；仿真成交与净值不得用于经济性判断。

## 六、后续触发条件（缺一不推进）

1. 用户本人创建并落盘 `paper_authorization.json`（路径 `PAPER_AUTH_REL`，`deepquant:600`）；
2. 本校验器 `--check` 返回 `ALLOW` 且 `check_channel_isolation.py --remote` 为 `FAIL 0 项`；
3. **11-30 产出含 `target_holds_d4` 的 `mode=FORWARD` 台账**；
4. 用户给出 **U-16** 明确书面放行。

四者齐备后，另立新预登记并实现成交回报对账器与演练驱动（外部真值优先、五项演练各 ≥1 次、费率小额实测 5 元下限），实现前冻结输入契约与 sha256。

## 七、验收标准

六项校验 6/6 覆盖、失败关闭用例全部 `DENY`、变异 100% 检出且**每个变异真改行为**、`scripts/tests` 全量不回归；仓库内 `paper_authorization.json` 命中数仍为 **0**；云端无授权文件被 agent 创建。任一不达标 ⇒ 不宣称完成，保留失败产物。
