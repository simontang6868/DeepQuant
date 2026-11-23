# PROBE 最小下单探针·补跑验收标准增补（PA-GM-PROBE-3B，2026-11-24）

> 配套 `docs/PROBE_MIN_ORDER_PREREG_2026-11-24.md`（同一实验，同一执行日）。
> 本文件独立冻结 `.sha256`；脚本 `probe_min_order_1124.py` 启动时校验预登记与本文双哈希。
> 11-22 验收增补 `df3d42fb…` → T+1 修正版 `0fcb4c39…` **保持原样不改写**；本文为该冻结件的 11-24 镜像，仅改日期与价格。

---

## 验收标准（全满足=PASS，任一不符=FAIL）

前置：
- P-1 前收价取自 **11-23 收盘**（上一交易日，字面=实际=`20261123`，无周末回退）
- P-2 `601398` 沪市主板 ⇒ 跌停 = 前收 × 0.90
- P-3 跌停价按 0.01 **向下取整**：`9.150×0.9=8.235 → 8.23`

执行：
- E-1 `place_limit_order` 返回 order_id（非空、非异常）
- E-2 记录委托状态（pending / filled / rejected）
- E-3 30 秒内 cancel_order，记录撤单返回

验收：
- V-1 positions == 0（未成交）
- V-2 available 恢复至 100000（±0.01）
- V-3 订单列表无残留 pending
- V-4 产物落盘 `forward/probe_min_order_2026-11-24.json`

异常分支（FAIL 之外，单独记录）：
- X-1 D0（11-24 当日）：T+1 禁卖；只记持仓字段、`probe_carry=true`、V-1 SKIP、零卖单
- X-1 D1（11-25 09:36 后）：由 timer 卖出 probe_carry；失败保持 carry=true 并升 P0
- X-2 cancel 失败 ⇒ `X-CANCEL-FAIL`
- X-3 available 未恢复 ⇒ `X-FREEZE`

边界：探针只验权限/白名单/额度/下单/撤单链路，不验 11-25 最新价参数、成交概率、回报字段完整性。PASS ⇒ 11-25 失败只可能来自价格或市场，**不是「11-25 一定成」**。

---

## 价格来源独立声明

前收 `9.150` 取自执行机独立数据 `data/exec_all/601398.SH.exec.csv` 的 `2026-11-23` 行 `close` 字段（该数据 4,855 行，末行即 11-23）；脚本启动时与 `--prev-close` 交叉校验，不一致即 HALT，**不手填、不猜价**。

## 交易日历核对声明

`20261124` / `20261123` / `20261125` 均经 `trade_calendar.json` 核对为交易日；核对表 `forward/trading_day_check_20261122.json`。
