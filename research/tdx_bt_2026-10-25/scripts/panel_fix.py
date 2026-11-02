"""数据层修正：用「并集日历 + 前向填充」替代严格交集。

【为什么必须改】
v3/v4/v5 用 `set.intersection` 取 20 只票的共同日期，结果：
  2014 只剩 4 个月、2015 只剩 3 个月（1/2/4 月，**5 月股灾起全部丢失**）、2016 只剩 1 个月。
根因：2015 年股灾期间全市场约 1400 只停牌，只要有 1 只票整月无成交，该月就被剔除。
后果：**2015 年最大回撤被算成 0.00%**，全部基于回撤的结论（含 v3 的 −28.93%）系统性失真。

【正确做法（业界标准）】
  1. 日历 = 所有标的月末日期的**并集**（限制在分析窗口内）
  2. 每只票的价格序列前向填充到日历：停牌期间价格不变 → 收益 0（而非"该月不存在"）
  3. 组合收益 = 当月**已上市且有数据**的票等权平均（自动处理不同上市时间）
  4. 上市前不计入（last is None）

⚠️ 该修正会改变所有历史结论的方向：回撤会变大（股灾被补回）。
   受影响脚本：bt_diversify_decomp(_v2/_v3)、bt_decomp_v4_stress、bt_diverse_final。
   全部需用本模块重跑，旧输出保留但标注「数据对齐缺陷，不可引用」。
"""
from __future__ import annotations
import statistics


def build_panel(series: dict[str, dict[str, tuple[float, float]]],
                start: str | None = None,
                end: str | None = None) -> tuple[list[str], dict[str, dict[str, float]]]:
    """构建对齐面板。返回 (日历, {code: {date: close}})，缺失已前向填充。

    ⚠️ 日历必须是「每自然月一个日期」，不能直接取所有票日期的并集：
       不同票的"月末"可能落在不同日（A 票 1/31 正常交易、B 票 1/31 停牌其最后交易日是 1/30），
       并集会令同一月出现 2 个日期 → 该月收益被重复计算、月份数虚增
       （v6 首版即因此算出 286 个月末 / 2008 年 20 个月，正确应为 228 / 12）。
       正确做法：按月分组取每月最大日期作为该月月末。
    某票上市前其值为 None（不可投资）。
    """
    from collections import defaultdict

    all_dates = sorted(set().union(*[set(s) for s in series.values()]))
    if start:
        all_dates = [d for d in all_dates if d >= start]
    if end:
        all_dates = [d for d in all_dates if d <= end]

    # 先 ffill 到全部原始日期
    filled: dict[str, dict[str, float | None]] = {}
    for c, s in series.items():
        px: dict[str, float | None] = {}
        last: float | None = None
        for d in all_dates:
            if d in s:
                last = s[d][1]
            px[d] = last
        filled[c] = px

    # 再按月取每月的最后一个原始日期作为日历
    by_month: dict[str, list[str]] = defaultdict(list)
    for d in all_dates:
        by_month[d[:6]].append(d)
    calendar = [max(v) for _, v in sorted(by_month.items())]

    panel = {c: {d: filled[c][d] for d in calendar} for c in series}
    return calendar, panel


def month_rets(panel: dict[str, dict[str, float | None]],
               dates: list[str]) -> dict[str, list[float]]:
    """逐月收益；某票上市前（None）不出现在组合里，由调用方用 active 掩码处理。

    返回的列表长度 = len(dates) − 1，缺失记 None。
    """
    out: dict[str, list[float | None]] = {}
    for c, px in panel.items():
        r: list[float | None] = []
        for i in range(1, len(dates)):
            p0, p1 = px[dates[i - 1]], px[dates[i]]
            if p0 is None or p1 is None or p0 <= 0 or p1 <= 0:
                r.append(None)
            else:
                r.append(p1 / p0 - 1)
        out[c] = r
    return out


def port_rets(rets: dict[str, list[float | None]], codes: list[str]) -> list[float]:
    """等权组合收益：每月对「当月有数据」的票等权。"""
    T = len(rets[codes[0]])
    out = []
    for i in range(T):
        vals = [rets[c][i] for c in codes if rets[c][i] is not None]
        out.append(statistics.fmean(vals) if vals else 0.0)
    return out


def coverage_report(dates: list[str]) -> None:
    """打印年度覆盖，确认股灾月份是否在日历中。"""
    from collections import Counter
    cnt = Counter(d[:4] for d in dates)
    print(f"  日历 {len(dates)} 月末  {dates[0]} → {dates[-1]}")
    bad = {y: n for y, n in cnt.items() if n < 11 and y not in ("2010", "2026")}
    print(f"  月份数 <11 的年份（应仅首末年）：{bad if bad else '无 ✓'}")
