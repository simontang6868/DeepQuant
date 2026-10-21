#!/usr/bin/env python
"""PC2 判据的变异测试（mutation test）。

## 为什么必须做

本项目的铁律之一是：**任何一致性检查都必须先做变异测试**——
故意注入已知缺陷，确认检查器真的能检出。

这条规矩不是洁癖。本项目已经发生过三次"检查器自己写错、通过率是假的"：
- V2 交叉验证只比交集数值，砍掉 200 行仍报 PASS；
- PC2 初版把阈值放大成 2.02 倍，报 PASS 但什么都没检；
- PC2 第三版用 `|ret|` 当标尺，把 000738.SZ 的重组复牌（+30.27%）判成 FAIL。

**这次改 PC2（第四版），所以必须证明两件事：**
1. 真缺陷仍然抓得到（下跌超量级、ADJ 与 EXEC 不一致）；
2. 真实行情不再误报（上涨且 EXEC 同幅）。

## 五个变异体

| # | 注入的缺陷 | 期望 |
|---|---|---|
| M1 | 非除权日下跌 −44%（ADJ 与 EXEC 同跌） | **必须 FAIL**（未登记除权的形态） |
| M2 | 非除权日上涨 +30%（ADJ 与 EXEC 同涨） | **不得 FAIL**（真实复牌，归 up_no_limit） |
| M3 | 非除权日上涨 +30%，但只有 ADJ 涨 | **必须 FAIL**（ADJ 自己造出来的涨幅） |
| M4 | 除权日下跌 −33%，且该日在事件表内 | **不得 FAIL**（除权日本就该跳） |
| M5 | 平稳 ±3% 波动 | **不得 FAIL** |

外加一个真实数据回归：000738.SZ 2010-10-15（重组复牌 +30.27%）不得判 FAIL。

用法：
    python scripts/pc2_mutation_test.py
退出码：全部符合期望 = 0，否则 1。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from property_checks import pc2_scan, read_adj, read_exec_simple, read_events_sina  # noqa: E402


def _bdays(start: str, n: int) -> list:
    return list(pd.bdate_range(start, periods=n))


def _mk(closes: list[float], start: str = "2015-01-05") -> pd.DataFrame:
    return pd.DataFrame({"date": _bdays(start, len(closes)), "close": closes})


def _flat(n: int, px: float = 10.0) -> list[float]:
    return [px] * n


def case_m1_ex_div_drop() -> tuple[str, bool, str]:
    """非除权日下跌 −44%：ADJ 与 EXEC 都跌（未登记除权的形态）。"""
    n = 60
    closes = _flat(30) + [5.6] + _flat(n - 31, 5.6)
    r = pc2_scan(_mk(closes), _mk(closes), set(), 0.10)
    ok = len(r["fatal"]) == 1 and r["fatal"][0]["kind"] == "ex_div_magnitude_drop"
    return "M1 非除权日下跌 −44% → 必须 FAIL", ok, str(r["fatal"][:1]) or "未检出"


def case_m2_real_resume() -> tuple[str, bool, str]:
    """非除权日上涨 +30%：ADJ 与 EXEC 同幅（真实复牌，无涨跌幅限制）。"""
    n = 60
    closes = _flat(30) + [13.0] + _flat(n - 31, 13.0)
    r = pc2_scan(_mk(closes), _mk(closes), set(), 0.10)
    ok = not r["fatal"] and len(r["up_no_limit"]) == 1
    return "M2 非除权日上涨 +30%（EXEC 同幅）→ 不得 FAIL", ok, str(r["up_no_limit"][:1]) or "未识别"


def case_m3_adj_only_jump() -> tuple[str, bool, str]:
    """非除权日上涨 +30%，但 EXEC 不动 → ADJ 自己造的涨幅。"""
    n = 60
    adj = _flat(30) + [13.0] + _flat(n - 31, 13.0)
    exc = _flat(n, 10.0)
    r = pc2_scan(_mk(adj), _mk(exc), set(), 0.10)
    ok = len(r["fatal"]) == 1 and r["fatal"][0]["kind"] == "adj_vs_exec_mismatch"
    return "M3 仅 ADJ 上涨 +30%（EXEC 不动）→ 必须 FAIL", ok, str(r["fatal"][:1]) or "未检出"


def case_m4_ex_date_exempt() -> tuple[str, bool, str]:
    """除权日 −33%，该日在事件表内 → 豁免（除权日本就该跳）。"""
    n = 60
    closes = _flat(30) + [6.7] + _flat(n - 31, 6.7)
    df = _mk(closes)
    ex_day = df["date"].iloc[30].strftime("%Y-%m-%d")
    r = pc2_scan(df, df, {ex_day}, 0.10)
    ok = not r["fatal"]
    return "M4 除权日 −33%（在事件表内）→ 不得 FAIL", ok, f"fatal={len(r['fatal'])}"


def case_m5_normal() -> tuple[str, bool, str]:
    """平稳 ±3% 波动 → 不得 FAIL。"""
    closes = [10.0 * (1.03 if i % 2 else 0.97) for i in range(60)]
    r = pc2_scan(_mk(closes), _mk(closes), set(), 0.10)
    ok = not r["fatal"]
    return "M5 平稳 ±3% 波动 → 不得 FAIL", ok, f"fatal={len(r['fatal'])}"


def case_real_000738() -> tuple[str, bool, str]:
    """真实数据回归：000738.SZ 2010-10-15 重组复牌 +30.27%。

    第三版 PC2 把它判成 FAIL（因为用 |ret| 当标尺）。第四版必须不判失败，
    且要归到 `up_no_limit`。
    """
    code = "000738.SZ"
    df = read_adj(code)
    ex = read_exec_simple(code)
    if df is None or ex is None:
        return "R1 真实 000738.SZ 复牌日 → 不得 FAIL", True, "跳过（无本地数据）"
    r = pc2_scan(df, ex, read_events_sina(code), 0.10)
    hit = [u for u in r["up_no_limit"] if u["date"] == "2010-10-15"]
    fatal_hit = [f for f in r["fatal"] if f["date"] == "2010-10-15"]
    ok = bool(hit) and not fatal_hit
    return ("R1 真实 000738.SZ 2010-10-15 复牌 +30.27% → 归 up_no_limit 而非 FAIL",
            ok, f"up_no_limit={hit[:1]} fatal={fatal_hit[:1]}")


def main() -> int:
    cases = [case_m1_ex_div_drop, case_m2_real_resume, case_m3_adj_only_jump,
             case_m4_ex_date_exempt, case_m5_normal, case_real_000738]
    bad = 0
    for fn in cases:
        name, ok, detail = fn()
        tag = "PASS" if ok else "FAIL"
        if not ok:
            bad += 1
        print(f"[{tag}] {name}\n       {detail}")
    print()
    print(f"=== 变异测试 {len(cases)} 项 | 符合期望 {len(cases)-bad} | 不符 {bad} ===")
    if bad:
        print("⚠ 判据未通过变异测试：它要么抓不到真缺陷，要么仍在误报。**不许带着这个状态跑正式检验。**")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
