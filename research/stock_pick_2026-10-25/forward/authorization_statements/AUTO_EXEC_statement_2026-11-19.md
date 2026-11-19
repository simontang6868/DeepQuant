# 书面授权存证｜全自动仿真调仓判据变更与实施（2026-11-19）

> 本文件逐字记录账户持有人 2026-11-19 15:21 CST 的授权指令原文，不含解释性扩写。
> UTF-8 无 BOM、LF；sha256 侧车冻结。原文不改写；更正另立段落。

## 1. 接收时间戳（两口径，shell 实测，非推算）

| 口径 | 值 | 来源 |
|---|---|---|
| 消息接收时间标记 | `2026-11-19T15:21:22+08:00` | 会话 `<current_time>`（Thursday, 19 Nov 2026 15:21:22 GMT+8） |
| 本机存证生成时间 | `2026-11-19T15:21:53+08:00` | `date "+%Y-%m-%dT%H:%M:%S%:z"` |
| 同一时刻 UTC | `2026-11-19T07:21:53Z` | `date -u` |
| epoch | `1795072913` | `date +%s`（单独取） |

## 2. 授权指令原文（逐字）

```
可以的，我给你书面授权，继续工作吧
```

## 3. 授权范围（按字面登记，不扩写、不代填）

- 授权对象：`reports/SIM_FULL_AUTO_PROPOSAL_V3_FINAL_2026-11-19.md`（sha `52c4e744…`）所列**判据变更六项**及冻结顺序实施：
  E0 时钟门（±10min + NTP 前置）｜旁证双证据结构（旁证格式/生成程序/邮件发布动作）｜M9 旁证篡改变异（8→9）｜`sim_exec_result/v1` 失败日志 schema｜哨兵四态｜验收第 9 条（`execution_reports.created_at` 落在 09:35±10min）。
- 上游：`reports/SIM_FULL_AUTO_PROPOSAL_FOR_REVIEW_2026-11-19.md` v1 + `...REV1...` v2 + 专家复审四项裁决（全部吸收）。
- 授权人：账户持有人（本会话用户）。**署名未提供**：按不代填、不代签原则如实登记，可随时补；补则另立补记，原文件不改。

## 4. 明确不含（边界，照 v3 §0/§7）

- 不含实盘：`LIVE_ENABLED` 保持 `false`，不得创建/修改 `live_authorization.json`。
- 不得创建或修改任何授权文件（含 `paper_authorization.json`）；不得改 U-16 版本 A 授权原文与 U16-EXEC-1 预登记正文。
- 不改策略参数、不新增因子、不改 G1/G2/S4 定义与计数、不写正式台账。
- `SIM_DRILL` 结果不计样本外证据、不作收益判断；`StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY`。
- 本授权不是探针授权，也不打开探针 `--execute`。

## 5. 实施前置（冻结前已实测）

- `ExecRpt.DESCRIPTOR.fields` 含 `created_at`（云端 `.venv-gm` 只读 DESCRIPTOR 实测）⇒ 验收第 9 条可落为代码判据。
- 云端 NTP：`NTPSynchronized=yes`，chrony 漂移 `0.000027202s`，`Leap status=Normal` ⇒ E0 前置满足。
- 授权时间窗：`not_after=2026-12-02T23:59:00+08:00`，约 38h ⇒ E0 动机成立。
