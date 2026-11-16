# U-16 仿真执行器 · 交付包（待提交清单）

生成时间：2026-11-16 19:3x +08:00
状态：**文件已就位，git 未做任何写操作**（无 add / commit / push；HEAD 仍为 `b5f5608`，工作树未变）
用途：本次"提交执行器"的归档凭证。等授权后再按 §5 的命令真正入库。

---

## 1. 本次要提交的 5 个文件（全部为新增/未跟踪）

| # | 路径 | 字节 | 行 | sha256 |
|---|------|------|----|--------|
| 1 | `scripts/sim_rebalance_executor.py` | 29,471 | 646 | `be3d31fcd34ca4805ff359e4d7170c154d62370adb302559c27bbe57d664a5de` |
| 2 | `scripts/tests/test_sim_rebalance_executor.py` | 9,420 | 229 | `a93403368a09f3365e906865310c39d5a0ff01e4c5cfed6a0ec9c4a2648a60ea` |
| 3 | `scripts/sim_rebalance_executor_mutation_test.py` | 8,058 | 183 | `e1fe0446a40c0d7e85cb2addf5de12b171b8d9a04d3557052308f28454c91740` |
| 4 | `reports/U16_EXEC_IMPL_RESULT_2026-11-16.md` | 4,727 | 79 | `e05e81e3c5edda4ec4f8a0bba009ac7f7bbe8eeb5bec4a7fca81110ac42944bd` |
| 5 | `reports/PROPERTY_CHECKS_U16EXEC_20261116.json` | 1,842 | 68 | `2605c3e0c64075264d1110050052d0946e70bc0b6389efd6bbc4af30d85cd2ab` |

依赖（**已在库中**，本次不改）：`docs/U16_EXEC_PREREG_2026-11-16.md`
`53ace8c5064f615806a07ae012bd3c2d344103e4b93b7a74e9e047eb8b0b703b`（U16-EXEC-1，执行器启动时校验其 sha256，改动预登记会直接 `HALT`）。

## 2. 明确不在本次提交范围（不许顺手带上）

- `research/.../deploy_manifest.json`、`deploy_manifest.prev.json`、`scripts/deploy_manifest.py`（M，属 R8/外部自动化产物，需单独判断）
- `*.bak.20261115` / `*.bak.20261114`（本地备份，入 git 无意义）
- `scripts/gm_paper_order_adapter.py`（已在库，本次无改动）

## 3. 本次重跑验证（2026-11-16 19:31 本机，dq_clean 解释器）

| 项 | 结果 |
|----|------|
| `--self-check` | `PASS`，10 项检查全绿，`n_real_orders=0`，`maturity_level=EXECUTION_READY_GATED` |
| `--gates` | **rc=4**（E1 `LEDGER_MISSING` 等未满足，符合"四项生效条件未满足前不放行"） |
| `--execute` | **rc=4**（同上，`NOT_AUTHORIZED`，零真实报单） |
| 单元测试 | `20 passed in 0.48s` |
| 真变异 | **8/8 检出**（M1 幂等盐 / M2 台账缺失放行 / M3 授权缺失放行 / M4 上取整 / M5 不清非目标 / M6 静默超预算 / M7 门控失败返 0 / M8 E4 恒通过）；恢复后 sha256 回到 `be3d31fcd34c`，与 §1 一致 |

## 4. 三条实现限制（不可忽略，引用执行器结论时必须同时声明）

1. gmtrade 无 tick/quote 接口 ⇒ 预登记 §2 的 10:30 / 14:30 重定价**不可实现**，实际为单次限价 + CARRY 顺延（`REPRICE_IMPLEMENTABLE=False`，`PRICE_SOURCE=LEDGER_REF_CLOSE`）。
2. SDK 无 `client_order_id` 字段 ⇒ 幂等只在本地下单台账内成立，**不构成券商侧幂等**。
3. E4 隔离检查器白名单只覆盖 `backend/src/deepquant/broker/`，**不覆盖 `scripts/`**；执行器不在其保护面内。

## 5. 获授权后的入库命令（现在不执行）

```bash
git add scripts/sim_rebalance_executor.py \
        scripts/tests/test_sim_rebalance_executor.py \
        scripts/sim_rebalance_executor_mutation_test.py \
        reports/U16_EXEC_IMPL_RESULT_2026-11-16.md \
        reports/PROPERTY_CHECKS_U16EXEC_20261116.json
git commit -m "feat(u16): 仿真调仓执行器（四道门控 + 幂等 cid + 限价 + 8/8 真变异）"
# push：等单独授权；按惯例先试 git 直连，失败再走 scripts/api_push_report_v2.py
```

## 6. 与"生效条件"的关系（防止误读）

执行器入库 ≠ 可报单。`--execute` 仍受四道门约束（E1 FORWARD 台账存在且 D4 目标齐全 / E2 DRYRUN+preflight / E3 书面授权 ALLOW 6/6 / E4 通道隔离+清单 59/59）。
本次交付只把"代码与测试"落到工作树与归档，**不改变任何门的判定输入**，也未触碰 token、未触碰 U-16 之外的任何授权。
