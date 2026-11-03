# L3v 结论卡片注册表：设计与闸门说明

> 预登记 `docs/gates/l3v_prereg.json`｜卡片数据 `docs/gates/l3v_claims.json`｜实现 `backend/src/deepquant/gates/claims.py`｜运行 `scripts/l3v_claims.py`

## 一、为什么需要这一层

L3u 已经把「引用的数字对不对」查完：27 条里 8 条一致、6 条取错量、6 条只是 t 口径不同、7 条无单一判定均值；
可引用强度最好的是 `phasef`（1.22 倍）与 `phaseo`（1.44 倍），**没有一条达到 2 倍的「可放心单引点估计」门槛**。

但查完不等于不再漂移。同一批数字散落在 13 个阶段报告、`MEMORY.md`、`RESULTS_INDEX.md` 与各种对外材料里，
每写一次都可能换一种舍入口径、换一个 t、丢一个负号。L3v 的作用是给出**唯一来源**：
每一条结论只有一句话，这句话里的每个数字都钉在一个可复算的字段上。

## 二、数据流

```
原始序列 CSV/JSON
   └─(L3u rebuild_series + summarise_series)→ recomputed {mean, t_iid, t_nw3, mde, power, n}
        └─(L3u 分级)→ grade.tier {QUOTABLE … NOT_GRADED}
             └─(L3v 卡片)→ headline + numbers[]（每个数字回指 recomputed 字段）
                  └─(L3v 闸门)→ violations[] / citation_blocks[]
```

关键点：卡片**不重新计算**任何统计量。它只声明「我引用的是哪个字段」，由闸门去核对。
这样「数字写错」与「字段指错」都能被抓到，而且不会出现两套算法互相背书。

## 三、三个硬约束与七个闸门

| 代码 | 检查什么 | 为什么必须有 |
|---|---|---|
| `MISSING_CLAIM` / `ORPHAN_CLAIM` | 卡片与 L3u 条目的**双向**差集 | 只比交集时，砍掉一半条目也能 PASS（C 阶段犯过） |
| `VALUE_DRIFT` / `UNREGISTERED_NUMBER` / `UNUSED_NUMBER` | 数字的值、覆盖、残留 | 手写舍入与正文改数字不删登记，是同一类漂移 |
| `SIGN_MISMATCH` / `SIGN_IN_TEXT` | display 与正文的符号都必须与实值一致 | `abs()` 套差值把负 alpha 翻正，复发过一次 |
| `TIER_UPGRADE` | 卡片强度 ≤ L3u 正式分级 | NOT_GRADED 被写成「可引用」就是把核对失败洗成结论 |
| `MISSING_WINDOW` / `MISSING_D_DEFINITION` / `MISSING_COST_ID` / `MISSING_UNIT_NOTE` | 按族强制的必带限定 | 「仅供参考」不是限定；窗口与成本口径一换就是另一个实验 |
| `MISSING_RESOLUTION` / `MISSING_CORE_FIELD` | 未检出必须报 MDE 或功效 | 「没检出」≠「没问题」；不报分辨率就无法区分 |
| `BANNED_PHRASING` / `ORPHAN_RETRACTION` | 已撤回措辞不得回流 | 判定字符串 `ABSORBED` 被写成 `INCREMENTAL` 已经发生过 |

`ESTIMATOR_MISMATCH` 单独一条：判定用 iid 却只引 NW3 的 t（或反之）等于挑显著性。
本项目 Gate 1~6 的头条 t 是 iid，C 阶段起才是 NW(3)，两套数字并存，必须同列。

## 四、符号的处理（踩过的坑）

首版让 `display` 只写数值、负号只出现在正文，结果校验拿不到对照，12 处 `SIGN_MISMATCH` 全是假阴性方向。
现在改为**三处互校**：`display` 必须带符号，正文中该数字的 token 必须同符号，两者都必须与 `recomputed` 实值一致。

## 五、数字 token 的边界

`NUM_TOKEN` 的 lookbehind 排除紧跟字母/数字/点的情形。原因：首版把 `NW3`、`MAX5`、`m1` 里的数字当成统计量，
一次产生 13 处假阳性，把预测 P2 的阈值顶穿。**这是检查器缺陷，不是卡片漂移**——两者必须分开报告，
否则会得出「27 条卡片全是错的」这种错误印象。

## 六、已知缺口

1. **literals 白名单可以被滥用**：把统计量谎报成 literal 就能绕过数字校验。缓解：每条 ≤4 个且必须给 reason，产物逐条列出供人工审。
2. **只校验 headline**：`limits_of_use` 与 `window_note` 里的数字不受管（多来自其他阶段）。
3. **同一数值多处出现无法区分**：若某个量恰好等于实验编号（如 Gate 5 与功效 5%），token 无法判断是哪一个。
4. **卡片覆盖范围 = L3u 收录的 27 条**；未进入 L3t 登记表的结论不在表内，也不得引用。

## 七、复现

```bash
# 仓库根
PYTHONPATH="backend/src" python scripts/l3v_claims.py
# 单元测试
cd backend && PYTHONPATH=src pytest tests/gates/test_gates_claims.py -q
```

产物：`reports/gates/H-L3V-CLAIM-REGISTRY/l3v_claims.{json,md}`、`reports/L3V_CLAIMS_EVIDENCE.json`。
首跑产物（含仪器缺陷）保留为 `l3v_claims_firstrun_tokenbug.*`，不删失败。
