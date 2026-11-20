# D4 目标价格缺口（P0）修复交付报告（2026-11-21 00:25 CST）

> 授权：账户持有人 2026-11-20 "授权修复并测试"
> 预登记：`docs/D4_PRICE_COVERAGE_FIX_PREREG_2026-11-21.md`（sha `ffb6935b…`），**先于任何代码改动冻结**
> 基线：`f6aa17f` → 修复 commit `dee3906`；manifest `sha_of_files=53d11e12210e`
> 性质：判据邻接变更追认；未下单、未 enable timer、未碰实盘

---

## 0. 一句话结论

**P0 阻塞已修复并端到端验证通过。** 原缺陷：执行器按 d4 目标选股却用主口径 orders 价格，dry-run 实证两组股票零重合 ⇒ 12-01 必 `NO_PRICE` 零报单。修复后：台账新增与 d4 目标同源同键的 `ref_close_d4`，执行器价格源改为此字段，E1 门加"每个 d4 目标必须有正有限价"校验。**P1-P6 六条预登记判据全部成立，无"预测被反驳"；回归四连全绿；真变异 10/10；执行机云端 65/65 一致且 self_check PASS。**

---

## 1. 改了什么（3 个源文件 + 测试 + 预登记）

| 文件 | 改动 |
|---|---|
| `p13_forward.py` | `build_d4_targets` 为 d4 / d4_gem 两组各输出 `ref_close_<key>`：从同一次 pick 的 `top`（含 close 列）构造 `{code: close}`，与 `target_holds_<key>` 同源同键；异常分支输出 None；台账写入段落盘新字段 |
| `sim_rebalance_executor.py` | 价格源从 `orders[].ref_close` 改为 `rec["ref_close_d4"]`；`PRICE_SOURCE` 常量 `LEDGER_REF_CLOSE` → `LEDGER_D4_REF_CLOSE`；E1 门 `gate_ledger` 加"d4 目标价格全覆盖"校验（缺价/非正/非有限 ⇒ `NO_PRICE_COVERAGE_D4`）；argparse 描述"五道"→"六门"；`IMPLEMENTATION_LIMITATION` 文本同步 |
| `sim_rebalance_executor_mutation_test.py` | 新增 M9（覆盖校验恒放行）、M10（目标缺价不计入 missing）两条真变异 + 独立检测器 d9/d10 |
| `test_sim_rebalance_executor.py` | 同步 `_ledger()` 夹具加 `ref_close_d4`；`test_limitation` 断言新常量+新文本且**加强**（不是弱化）；新增 `test_gate_ledger_requires_d4_price_coverage`（缺价/空映射/非正价三分支） |
| 预登记 + 侧车 | 详见上 |

**没改**：选股口径（仍 d4 严格）、授权原文、任何 timer、实盘、PROBE/断链A/重定价等既有登记。

---

## 2. 预登记判据逐条验收（P1-P6）

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| P1 | dry-run asof 20261030：`ref_close_d4` 键集合与 `target_holds_d4` 全等 | ✅ TRUE | 两组均 5 只 `000721/000659/601996/001209/001223`，双向差集空，全正有限价（5.86/2.36/2.10…） |
| P2 | 真实同源数据 `build_plan` 不再 NO_PRICE，产出 5 BUY | ✅ TRUE | plan n=5、BUY=5、codes=d4 五只、qty=[800,2100,2300,300,100]；反事实用旧 orders 价仍 `HALT:NO_PRICE:000721` |
| P3 | `gate_ledger` 对缺价台账判 `NO_PRICE_COVERAGE_D4` | ✅ TRUE | 删一个价/空映射/0.0 价三分支全部 False；正常夹具 True |
| P4 | 真变异必须 DETECTED | ✅ DETECTED | 10/10 全检出（含新增 M9/M10），恢复后 sha `0c372521` 一致 |
| P5 | 回归四连全绿 + manifest 本地云端一致 | ✅ TRUE | 见 §3 |
| P6 | 既有 E1 三类失败分支未被破坏 | ✅ TRUE | self_check 的 gate_e1 含新分支、老测试 `test_gate_ledger_requires_forward_and_d4` 全过 |

**六条预测全部成立。** 过程中一条自造的"带 .SZ 后缀"测试夹具因误解 `normalize_code` 约定而误报失败——那是测试夹具假设错（p13 实际输出纯 6 位码），已删除该无效夹具，非系统缺陷。

---

## 3. 回归与部署验证

| 项 | 结果 |
|---|---|
| 回归四连 | **3651/1｜9/9｜760/1（+1 新测试）｜verify PASS**，全 rc=0 |
| 一次 2 failed | 老测试 `_ledger()` 夹具缺新字段 + limitation 断言旧常量——**测试夹具同步**，已修并加强断言，重跑 21 passed（单文件）/760 passed（全量） |
| manifest | 65 文件，`sha_of_files=53d11e12210e`，commit `dee39065`（本地 HEAD） |
| 执行机同步 | 3 代码文件 + manifest 已 scp 至 `1.92.141.140:/opt/quant`，属主修正为 deepquant，哈希逐位一致 |
| manifest --verify --remote | 云端 65/65 PASS |
| 执行机 self_check | PASS（`EXECUTION_READY_GATED`） |

---

## 4. 剩余风险与边界

1. **PROBE 仍是 go/no-go 前置**：11-25 买、11-26 卖去污、11-27 17:00 检查点，均按日期发生，本次未提前触发。
2. **价格仍是 11-30 收盘价，非 12-01 开盘可成交价**：gmtrade 无行情接口，10:30/14:30 重定价不可实现的既有登记不变；新字段只保证"与选股同源同键"，不保证成交价。
3. **d4 严格口径**（不含创业板）与预登记一致；d4_gem 价格字段一并写入但当前不用。
4. **E1 新校验依赖新台账**：11-30 必须用**改后 p13** 生成台账（含 `ref_close_d4`），否则 E1 会 `NO_PRICE_COVERAGE_D4` 失败关闭——这是期望的失败关闭行为，执行机已同步新 p13。
5. **定时器仍全部 disabled**：enable 只在 12-01 09:35 预测全门 PASS 当天。

---

## 5. 结论与下一步

**P0 阻塞清除，系统从 NO-GO 恢复为"条件 GO"**：条件 = 按日期完成 PROBE/出证/11-30 新台账生成，且 12-01 全门 PASS 才 enable timer。

按时间线：11-25 PROBE 买 → 11-26 卖+去污 → 11-27 go/no-go → 11-30 新台账（含 ref_close_d4）+ 出证 + 冻结 → 12-01 主演练。

引用口径：`StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`；`MaturityLevel=EXECUTION_READY_GATED`。本件为工程修复交付，不构成统计结论或样本外证据。

署名：simontang

---

## 6. 推送状态追加（2026-11-21 00:30）

- **cloud 裸仓库备份**：✅ 已推送至 `e5e5138`（`GIT_SSH_COMMAND` 带 fk_server_ed25519）。
- **origin（GitHub）**：⚠️ **未同步**，远端仍停在 `f6aa17f`。原因：代理对 github.com 反复 `CONNECT tunnel failed, response 502` / `schannel server closed abruptly`；重试 3 次 + 禁用代理直连均失败（无外网直连能力）。备用 API 通道 `scripts/api_push_report_v2.py` 的 token 源（`CODEBUDDY_MCP_CONFIG` 的 github-pat）当前不可用。
- **代码不丢**：本地 commit `dee3906`/`e5e5138` 完整，cloud 备份机已同步；待代理恢复后一次 `git push origin` 即可补齐，无需重做。
- 这不是工程缺陷，是网络通道问题；按事实登记，不伪装成已推送。
