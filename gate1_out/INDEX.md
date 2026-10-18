# Gate 1 结果索引（交付物 #5：逐窗口结果及其索引）

> 本索引由「逐窗明细所在目录」的落盘产物生成，所有 sha256 为文件实际摘要。
> 统计量一律取自 `summary.json`，本索引不重新计算任何绩效指标。

## run `032a11f591553e25`

- 用途：主实验：预登记 v1.3，全池 50 只，主实验 + 12 组敏感性
- 预登记：`gate1-prereg-v1.3`（sha256 前缀 `8b011551`）
- OOS：2021-01-01 ~ 2024-12-31，n=48 个月
- 排除标的：无
- 主实验：mean(d)=-0.003961，t=-0.569，p=0.5723，胜率=41.7%
- 判定：`failure`（12 组敏感性中 `mean(d) > 0` 的组数 = 2）
- 涨跌停比例（降级值）：10%；耗时 92.1 s

| 文件 | 字节 | 数据行 | sha256（前 16） | 说明 |
|---|---|---|---|---|
| `GATE1_RESULT_REPORT.md` | 5784 | — | `f0f1f962e6cf566a` | 由 scripts/gate1_report.py 机械生成的报告（渲染层不重算指标） |
| `manifest.json` | 538 | — | `722b08f784edbb77` | 本 run 落盘文件的 sha256 与字节数（完整性校验用） |
| `monthly_paired.csv` | 3623 | 48 | `54a432f6fd4e1acb` | 逐月配对明细：month, r_strategy, r_benchmark, d（主实验口径） |
| `sensitivity.csv` | 2290 | 13 | `c02be62fc80d0db6` | 主实验 + 12 组敏感性逐组统计（含 BH 校正 q 值） |
| `summary.json` | 700 | — | `b34d2cd1f8e567e2` | 主实验统计量、判定档位、rule_hits 与运行元信息 |

## run `a027865f26ea237a`

- 用途：对照跑：排除 600809.SH（49 只），主实验 + 12 组敏感性 + 脚本内对照
- 预登记：`gate1-prereg-v1.3`（sha256 前缀 `8b011551`）
- OOS：2021-01-01 ~ 2024-12-31，n=48 个月
- 排除标的：600809.SH
- 主实验：mean(d)=-0.003284，t=-0.464，p=0.6445，胜率=47.9%
- 判定：`failure`（12 组敏感性中 `mean(d) > 0` 的组数 = 2）
- 涨跌停比例（降级值）：10%；耗时 99.7 s

| 文件 | 字节 | 数据行 | sha256（前 16） | 说明 |
|---|---|---|---|---|
| `GATE1_RESULT_REPORT.md` | 5914 | — | `e5089e11f6b838cf` | 由 scripts/gate1_report.py 机械生成的报告（渲染层不重算指标） |
| `manifest.json` | 712 | — | `194af80219582ed0` | 本 run 落盘文件的 sha256 与字节数（完整性校验用） |
| `monthly_paired.csv` | 3622 | 48 | `74be84da8d315a8e` | 逐月配对明细：month, r_strategy, r_benchmark, d（主实验口径） |
| `monthly_paired_excluded.csv` | 3622 | 48 | `74be84da8d315a8e` | 脚本内对照组的逐月配对明细；经核实与本 run 的 monthly_paired.csv 同 sha256，即它是对同一份已剔除数据重跑同一配置的结果，不是「含 vs 排除」的对照 |
| `sensitivity.csv` | 2366 | 13 | `ecebcbb74e3e666c` | 主实验 + 12 组敏感性逐组统计（含 BH 校正 q 值） |
| `summary.json` | 1165 | — | `1f9b7f404db1c406` | 主实验统计量、判定档位、rule_hits 与运行元信息 |

## 复现命令

```bash
python scripts/gate1_run.py --execute --authorized-by "<用户授权原话>"
python scripts/gate1_run.py --execute --authorized-by "<用户授权原话>" --exclude-symbols "600809.SH"
```

> `run_id` 由「协议版本 + 标的集合」的内容摘要生成（见 `scripts/gate1_run.py:make_run_id`），
> 因此同一份协议 + 同一份数据会得到同一个目录名；目录名变了说明输入变了。

## 读取方式

- 逐窗（逐月）明细：`monthly_paired.csv`，每行一个自然月，`d = r_strategy − r_benchmark`。
- 判定与统计量：`summary.json`；渲染层 `scripts/gate1_report.py` 只读这些落盘文件。
- 完整性：`manifest.json` 记录落盘瞬间的 sha256，可用于验证事后未被改动。
