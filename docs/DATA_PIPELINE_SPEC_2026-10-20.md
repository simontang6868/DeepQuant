# 数据管线规格 · 口径切换与延长决策（阶段 2 · P0-1 + P0-2 + P1-2）

> 生效日期：2026-10-20 ｜ 阶段：数据体系修复（路线图 §6）
> 性质：**数据工程决策与可重跑管线规格**。本文件不重算任何研究结论。
> 前置基线：`v0.9.0-gate1-closed`（Gate 1 数据冻结于 step75 加性 qfq / 2024-12-31，不受本文件影响）

---

## 1. 一句话决策

> 把主池（b50_v3，50 只）的 served 数据源从 **`data/step75/`（加性 qfq，末日 2024-12-31，2 只 invalid_price）**
> 切换到 **`data/qfq_mult/listed/`（乘性 qfq，末日 2026-10-16，0 负价）**。
> 一次切换同时完成 **P1-2（复权方式修复）+ P0-1（行情延长）**，并修好两只原 invalid 标的。

---

## 2. 现状管线地图（先划清谁是谁）

```
                        ┌─ data/step75/*.qfq.csv ─────────┐  （加性 qfq，末日 2024-12-31，2 只负价）
                        │  现 served 数据源（v0.9.0 基线）  │
                        └────────────┬───────────────────┘
                                     │ scripts/import_pool.py（sha256 严格校验）
                                     ▼
   腾讯 web.ifzq.gtimg.cn   ┌─ data/raw/*.raw.csv ───────────┐
   hfq: fqkline/get ,hfq    │  data/processed/*.daily.parquet │  ← /api/datasets 读这里
   raw: kline/kline（锚点）  │  data/metadata/*.meta.json      │    （51 条，2 invalid）
                           └────────────────────────────────┘

                        ┌─ data/qfq_mult/listed/*.qfq.csv ─┐  （乘性 qfq，末日 2026-10-16，0 负价）
   scripts/repair_qfq_mult.py  │  300 只，stage0 幸存者偏差研究已建 │  ← 本文件建议的新源
   qfq_mult = hfq × (raw_anchor/hfq_anchor) └────────────────┘
```

**关键事实**：`data/qfq_mult/listed/` 的 300 只乘性文件**已经存在**（stage0 研究副产物），主池 50 只里 **49 只 + 600809.SH 已在**，末日全部 2026-10-16，仅 **688981.SH（中芯国际）腾讯无 hfq 数据**。

## 3. 三种复权口径（落地 A1）

| 口径 | 公式 | 问题 | 本项目处置 |
|---|---|---|---|
| **加性 qfq**（旧，step75） | `qfq_t = raw_t − 未来累计分红_t` | 分红累计超价位 → **负价**（600809 有 131 行负价，000568 最低 −12.98）；价位过零后日收益分母失效 | ❌ 弃用为 served 源 |
| **乘性 qfq_mult**（新，qfq_mult/listed） | `qfq_mult_t = hfq_t × (raw_anchor / hfq_anchor)` | 无负价；日收益与 hfq 完全一致；末值=真实收盘价 | ✅ 采用为 served 源 |
| **hfq（后复权）** | `hfq_t = raw_t × F_t`（F 仅除权日跳变） | 末值≠真实价（读不出"一手买不买得起"），但收益正确 | 研究层用，不直接 served |

## 4. 切换的实测证据（2026-10-20 本轮验证）

对 `data/qfq_mult/listed/` 里主池相关的 50 只做逐文件校验：

| 项 | 结果 |
|---|---|
| 主池 50 只中已在 qfq_mult/listed | **49 只**（缺 688981.SH） |
| 600809.SH（原 invalid_price） | ✅ 在，2606 行，2016-01-04~2026-10-16，**0 负价** → 乘性修好了 |
| 000568.SZ（原 invalid_price） | ✅ 在，2600 行，2016-01-04~2026-10-16，**0 负价** → 乘性修好了 |
| 末日分布 | 全部 **2026-10-16**（49 只一致） |
| 负价行 | **0**（49 只全部干净） |
| 首日 | 2016-01-04（与 step75 同源同起点，可比） |
| 行数 | ~2600（2016-01-04~2026-10-16 全段） |
| 688981.SH | ❌ 腾讯 hfq 接口返回 0 条（真实缺口，非瞬时；已重试确认） |

**结论**：切换后 served 数据集 = **50 只**（49 主池 + 600809），全部乘性、末日 2026-10-16、0 invalid、0 负价。
**代价**：数据集数 51 → 50（丢 688981.SH，因腾讯数据源限制；如需补，须换数据源，但东财 push2his 本机已死、akshare 走东财亦死）。

## 5. 为什么不"先延长 step75 再切"（顺序纠正）

路线图原把 P0-1（延长）排在 P1-2（切复权）前。**实测后纠正**：延长加性 qfq 会把负价问题**原样带进 2025-2026**——等于把已知坏数据扩段。正确顺序是**先切乘性、再延长**，而 qfq_mult/listed 恰好**两步合一**（已是乘性 + 已到 2026-10-16）。

## 6. 重新摄入程序（P0-2 可重跑管线）

`scripts/import_pool.py` 用 sha256 严格校验池清单，qfq_mult/listed 没有对应清单。重新摄入有两条路：

### 路 A（推荐）：为 qfq_mult/listed 建兼容清单后复用 import_pool
1. 对 50 只（49 + 600809）的 `data/qfq_mult/listed/<code>.qfq.csv` 逐个算 sha256；
2. 按 `PoolResult.as_manifest` 的 schema（`pool_manifest.json`）生成清单：`pool.label="qfq_mult_ext_2026-10-16"`、`n_listed=50`、每只 `code/name/sha256`、`research_skipped={}`（乘性下两只原 invalid 已有效，不再 skip）；
3. `python scripts/import_pool.py --pool-dir data/qfq_mult/listed`（清单放该目录下作 `pool_manifest.json`）；
4. 校验：`/api/datasets` 应 50 条 / invalid 0；`python sandbox_verify/check_env.py --expect-datasets 50`。

### 路 B：写专用摄入脚本（更直白，但要碰管线原语）
直接调 `deepquant.data.pipeline.ingest_daily_csv` + `validate_daily` + `storage_io`，绕开 import_pool 的清单仪式。**风险**：原语签名写错会产出坏 parquet/meta，破坏 /api/datasets。**不推荐**除非 import_pool 适配受阻。

### 幂等与可重跑
- import_pool 对同 sha256 已导入 → 跳过（不改 mtime）；换 sha 才覆盖并报冲突。
- 因此「同一输入两次产出的 sha256 一致」= P0-2 验收判据。校验法：记录摄入后 `data/processed/*.parquet` 的 sha256，重跑一次，比对全等。

## 7. 备份与回滚（已执行）

切换前已备份 v0.9.0 基线的 served 数据到：
```
data/_backup_step75_baseline_20261020/{raw,processed,metadata}/   （51 个 parquet + 51 个 meta）
```
回滚 = 把备份覆盖回 `data/{raw,processed,metadata}/`。冻结基线的云端副本另在 `DeepQuant-data-core-2026-10-20.zip`（资料库）。

## 8. C2 红线（必须记）

> **延长/切换后的数据，不得用于回算 Gate 1 旧结论。**

- Gate 1 的 `failure` 判定建立在 step75 加性 qfq / 2024-12-31 / 同池等权基准上，**冻结于 v0.9.0-gate1-closed**，不动。
- 在新数据（乘性 + 2026-10-16）上重跑同一假设 = **对同一假设看第二眼** = **新实验**，须走阶段 3 新预登记（路线图 §7）。
- 新数据的用途是**为阶段 3 的新研究准备干净、延长、无负价的输入**，不是改写 Gate 1。

## 9. 执行结果（2026-10-20 已完成 ✅）

重新摄入已按 §6 路 A 执行并全部验收通过：

| 项 | 结果 |
|---|---|
| 输入清单 | `data/qfq_mult/listed/qfq_mult_ext_summary.json`（52 只：49 主池含 000568/600809 + 3 手加 000858/600519/601318，逐只记 sha256） |
| dry-run 预览 | 52 只全过校验，0 拒绝（50 conflict + 2 would_import） |
| 正式导入 | `import_pool.py --pool-dir data/qfq_mult/listed --summary qfq_mult_ext_summary.json --force` → **imported 52，exit 0** |
| /api/datasets | **52 valid / 0 invalid**（discover_datasets 实测），末日全 **2026-10-16** |
| 负价 | **0**（600809 从加性 131 负价 → 乘性 0；000568 同理） |
| 修好原 invalid | ✅ 000568 泸州老窖、600809 山西汾酒（乘性下有效） |
| 688981.SH | 已从 served 移除（腾讯无 hfq；删 raw/processed/metadata 三件套） |
| **P0-2 可重跑性** | ✅ **PASS**：清空 3 目录后重摄入，52 个 parquet sha256 与首次**逐字节全等** |
| 全量测试 | **1997 passed / 1 skipped / 0 failed**，与 v0.9.0 基线**完全一致**（切数据零破坏） |
| pool_manifest | `data/pool_manifest.json` 更新为 52 只乘性清单（gate={imported:52}，research_skipped={}，disagreements=[]） |

**最终 served 状态**：52 数据集，全部乘性前复权，末日 2026-10-16，0 invalid，0 负价。

## 10. 数据集数 51 → 52 的说明

P0-1 原验收写「仍 51 条」。实际结果为 **52 条**（更多且更干净）：
- **−1**：688981.SH（中芯国际）腾讯无 hfq，无乘性源，移除；
- **+2**：000568、600809 原 invalid_price（加性负价），乘性下有效，纳入；
- 净效果：51（含 2 invalid）→ **52（全 valid）**。

若日后必须保某只特定标的或凑回 51，属数据范围决策，单独立项；当前 52 全 valid 是更优状态。
