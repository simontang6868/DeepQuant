# 预登记：D4 目标价格覆盖修复（P0 阻塞）——先于代码冻结（2026-11-21 00:10 CST）

> 授权：账户持有人 2026-11-20 "授权修复并测试"；本预登记**先于任何代码改动**冻结。
> 触发缺陷：`reports/FULL_RECHECK_1201_2026-11-20.md` §2（P0，NO-GO）。
> 基线 commit：`f6aa17f`；本预登记侧车独立，不写自身哈希。
> 性质：判据邻接变更（台账结构 + 执行器价格源 + E1 门），追认执行，不改预登记 U16-EXEC-V2 的选股口径。

---

## 1. 缺陷陈述（已实证，非推测）

- 预登记 `U16_EXEC_PREREG_V2` L29 写死目标来源 = `target_holds_d4`（D4 严格口径），L40 明确旧台账 5 笔（含 301329）整组禁用。
- 执行器 `build_plan` 按 `target_holds_d4` 选股；唯一价格源 = 台账 `orders[].ref_close`，而 orders 由 p13 从**主口径** `target_m` 生成。
- 两条选股路径结构性分离：dry-run 真实数据（asof 20261030）主口径 top5 `600990/603977/002012/301329/600191` 与 D4 top5 `000721/000659/601996/001209/001223` **交集为空**。
- 后果：`build_plan` 对第一个 D4 目标取价 `None` ⇒ `Halt("NO_PRICE:000721")` ⇒ 12-01 计划阶段失败、零报单。独立复现已坐实。

## 2. 修复方案（不改选股口径）

1. **台账增字段**：p13 `build_d4_targets` 在正常分支为两组口径各输出 `ref_close_<key>`：从 `top`（含 `close` 列）构造 `{code: float(close)}`，仅含该口径 top 的目标股；异常分支（快照不可靠）输出 `None`。台账写入处把 `ref_close_d4` / `ref_close_d4_gem` 落盘。
2. **执行器价格源**：`main()` 改为优先读 `rec["ref_close_d4"]`（严格 d4 口径，与选股同源）；`ref_close` orders 仅在主口径被禁用时不作为 d4 价格源。`PRICE_SOURCE` 常量由 `LEDGER_REF_CLOSE` 更新为 `LEDGER_D4_REF_CLOSE`。
3. **E1 门增强**：`gate_ledger` 增加"D4 目标价格全覆盖"校验——`target_holds_d4` 非空且每个 code（normalize 后）在 `ref_close_d4` 中有正有限价格。缺失 ⇒ `NO_PRICE_COVERAGE_D4` 失败关闭。
4. **表述修正**：`main()` argparse description "五道生效条件门控" → "六门"。

**结构假设（明确写下，避免把模板当定律）**：`ref_close_d4` 与 `target_holds_d4` 由同一个 `top` DataFrame 一次构造，故键集合按构造关系必然相等；本修复只保证"同源同键"，不保证价格数值等于 12-01 开盘可成交价（gmtrade 无行情接口，重定价不可实现的既有限制不变）。

## 3. 预期方向与可算判据

| # | 预登记判据 | 预期方向 | 性质 |
|---|---|---|---|
| P1 | dry-run asof 20261030 台账含 `ref_close_d4`，键集合与 `target_holds_d4` 全等（normalize 后双向差集空） | TRUE | 可算 |
| P2 | 用真实 dry-run 的 `target_holds_d4` + `ref_close_d4` 调 `build_plan`，不再 `NO_PRICE`，产出 5 个 BUY intent | TRUE | 可算 |
| P3 | `gate_ledger` 对"d4 目标有价格"台账判 True；对"删去一个 d4 价格"夹具判 False 且 reason=`NO_PRICE_COVERAGE_D4` | TRUE | 可算 |
| P4 | 真变异：把 E1 覆盖校验改成恒 True（或删去 ref_close_d4 读取），自检/测试须 DETECTED | DETECTED | 变异 |
| P5 | 回归四连全绿（3651/1｜9/9｜759/1｜verify PASS）；manifest 重生成后本地/云端逐位一致 | TRUE | 回归 |
| P6 | 既有 E1 三类失败分支（LEDGER_MISSING / MODE_NOT_FORWARD / NO_TARGET_HOLDS_D4）不被新校验破坏，self_check 的 gate_e1 仍 PASS | TRUE | 回归 |

**预测 P1-P6 全部为 TRUE/DETECTED**。若任一被反驳，如实报"预测被反驳"，保留原始产物，不改本预登记。

## 4. 变异方案（真变异，须真改行为）

- M-D4A：删除 `build_d4_targets` 里 `ref_close_d4` 输出 ⇒ dry-run 台账无该字段 ⇒ E1 应 `NO_PRICE_COVERAGE_D4`（P3 检出）。
- M-D4B：执行器价格源改回只读 orders（`ref_close`）⇒ 用 d4 目标应 `NO_PRICE` ⇒ P2 检出。
- 变异体必须真改行为，恢复后 sha256 与冻结版逐位一致；假变异（只改注释/字符串）不算。

## 5. 边界

- 不改 U16-EXEC-V2 的选股口径（仍 d4 严格）、不碰授权原文、不提前下单、不 enable 任何 timer、不碰实盘。
- 不修 PROBE / 断链 A / 重定价等既有已登记限制。
- 判据邻接变更按三零一不变判别式：新增字段与校验是**判据支撑增强**，冻结顺序 = 改程序 → 本预登记追认 → 部署 → manifest 重生成 → 回归 → 出证（11-30）。

署名：simontang
