#!/usr/bin/env python
"""P0-2 验证：price_jump 计数是否等于真实跳变个数。

用法（在仓库根目录执行）：
    PYTHONPATH=backend/src python sandbox_verify/p0_2_price_jump.py
    PYTHONPATH=backend/src python sandbox_verify/p0_2_price_jump.py --data data/processed/600519.SH.daily.parquet

修复前的症状（两条同时存在）：
    1) 计数恒为「行数 − 1」：100 行平盘也报 count=99；
    2) 零跳变也报警（issue 恒定出现）。
修复后：
    平盘 100 行 -> 完全没有 price_jump issue；1 处真跳变 -> count=1；3 处 -> count=3；
    把 DataFrame（而不是 Series）喂进内层函数 -> 直接 TypeError，不再静默计数。

退出码：全部通过 = 0；任何一条不符 = 1。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from deepquant.data.validation.checks_extended import (
    _check_price_jumps,
    validate_daily_extended,
)
from deepquant.data.validation.checks import ValidationReport


def _frame(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    dates = pd.bdate_range("2021-01-04", periods=n)
    close = pd.Series(closes, dtype="float64")
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        }
    )


def _price_jump_issues(df: pd.DataFrame) -> list:
    report = validate_daily_extended(df, adjust_status="qfq")
    return [i for i in report.issues if i.code == "price_jump"]


def _series_count(closes: list[float]) -> int:
    report = ValidationReport()
    _check_price_jumps(report, pd.Series(closes, dtype="float64"), 0.15)
    hits = [i for i in report.issues if i.code == "price_jump"]
    return 0 if not hits else int(hits[0].count)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=None, help="真实行情 parquet 路径（可选）")
    args = parser.parse_args()

    failures: list[str] = []
    print("=" * 84)
    print("P0-2  price_jump 计数 —— 合成样本 + 内层函数直测")
    print("=" * 84)
    print(f"{'样本':<34}{'期望':<22}{'实际':<22}{'结果'}")
    print("-" * 84)

    def check(label: str, expected: str, actual: str, ok: bool) -> None:
        print(f"{label:<34}{expected:<22}{actual:<22}{'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(f"{label}：期望 {expected}，实际 {actual}")

    # 1) 平盘 100 行：不应有任何 price_jump issue（修复前 count=99）
    flat = _frame([10.0] * 100)
    issues = _price_jump_issues(flat)
    check(
        "平盘 100 行（公开路径）",
        "无 price_jump",
        "无" if not issues else f"{len(issues)} 条 / count={issues[0].count}",
        len(issues) == 0,
    )

    # 2) 1 处真跳变（+20%）
    one = [10.0] * 50 + [12.0] + [12.0] * 49
    issues = _price_jump_issues(_frame(one))
    actual_count = 0 if not issues else int(issues[0].count)
    check(
        "1 处 +20% 跳变（公开路径）",
        "1 条 issue / count=1",
        f"{len(issues)} 条 / count={actual_count}",
        len(issues) == 1 and actual_count == 1,
    )

    # 3) 3 处真跳变（±20%）
    three = list(one)
    three[70] = 9.6   # 12.0 -> 9.6 = -20%
    three[85] = 12.0  # 9.6  -> 12.0 = +25%
    issues = _price_jump_issues(_frame(three))
    actual_count = 0 if not issues else int(issues[0].count)
    check(
        "3 处跳变（公开路径）",
        "1 条 issue / count=3",
        f"{len(issues)} 条 / count={actual_count}",
        len(issues) == 1 and actual_count == 3,
    )

    # 4) 内层函数直测：Series 语义（替换掉 DataFrame 的模糊地带）
    check("Series 平盘 100 点（内层直测）", "count=0", f"count={_series_count([10.0] * 100)}", _series_count([10.0] * 100) == 0)
    check("Series 1 处跳变（内层直测）", "count=1", f"count={_series_count(one)}", _series_count(one) == 1)

    # 5) 传 DataFrame 必须 TypeError（而不是静默算成"行数-1"）
    try:
        _check_price_jumps(ValidationReport(), _frame([10.0] * 100)[["close"]], 0.15)
        typeerror_ok = False
        detail = "未抛异常（静默通过）"
    except TypeError as exc:
        typeerror_ok = True
        detail = f"TypeError: {str(exc)[:40]}..."
    except Exception as exc:  # noqa: BLE001
        typeerror_ok = False
        detail = f"{type(exc).__name__}（期望 TypeError）"
    check("传 DataFrame 给内层函数", "TypeError", detail, typeerror_ok)

    # 6) 可选：真实行情抽查
    #    FIX_DIFF §3.2 的「600519 = 1 次跳变」是 2018-01-01~2024-12-31（1699 行）这个窗口；
    #    全区间 2017-01-03~2024-12-31（1943 行）是 5 次 —— 跳变数随窗口变，这是正常现象，
    #    不变的是「不再是 行数−1」。两个窗口都打出来，方便对照，不把窗口差异当成缺陷。
    if args.data:
        path = Path(args.data)
        if not path.is_file():
            print(f"[SKIP] 真实行情抽查：找不到 {path}")
        else:
            df = pd.read_parquet(path)
            sub = df[(df["date"] >= "2018-01-01") & (df["date"] <= "2024-12-31")]
            issues = _price_jump_issues(sub)
            actual_count = 0 if not issues else int(issues[0].count)
            label = f"真实行情 {path.name} 2018-2024（{len(sub)} 行）"
            check(label, "count=1（FIX_DIFF §3.2）", f"count={actual_count}", actual_count == 1)

            full_issues = _price_jump_issues(df)
            full_count = 0 if not full_issues else int(full_issues[0].count)
            print(
                f"{'真实行情 全区间（' + str(len(df)) + ' 行）':<34}"
                f"{'参考值 5':<22}{f'count={full_count}':<22}{'INFO'}"
            )
            print(f"     说明：全区间比 2018 起多 2017 年一年，跳变 1 -> 5；"
                  f"与本条的判定无关（关键是不等于 行数−1 = {len(df) - 1}）")

    print("-" * 84)
    print("=" * 84)
    if failures:
        print(f"总计：FAIL（{len(failures)} 项）")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("总计：PASS —— 计数等于真实跳变个数，平盘不再误报，DataFrame 入口被显式拒绝")
    return 0


if __name__ == "__main__":
    sys.exit(main())
