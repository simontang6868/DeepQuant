#!/usr/bin/env python
"""P0-1 验证：generate_windows 是否真的尊重 cfg.end_date。

用法（在仓库根目录执行，需 PYTHONPATH 指向后端源码）：
    PYTHONPATH=backend/src python sandbox_verify/p0_1_windows.py
    PYTHONPATH=backend/src python sandbox_verify/p0_1_windows.py --data data/processed/600519.SH.daily.parquet

结论口径（唯一与参数无关的部分）：
    1) 每个窗口的 oos_end <= end_date；越界窗口数必须为 0；
    2) end_date 早于全部数据 -> 返回空列表；
    3) 相同输入两次调用 -> 窗口列表逐位相同。

窗口"个数"不是常量，它是 (index 长度, train_window, oos_window, step) 的函数。
因此下面用四组已固化的口径分别给出期望值（A/B/C 为可复现的合成索引，D 用真实行情）。
修复前的共同症状是：end_date 完全无效，越界窗口数 > 0。

退出码：全部通过 = 0；任何一条不符 = 1。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from deepquant.walkforward import WalkForwardConfig, generate_windows


@dataclass(frozen=True)
class Case:
    name: str
    index: pd.DatetimeIndex
    train: int
    oos: int
    step: int
    end_date: str
    expect_n: int
    expect_last_oos_end: str
    expect_n_unbounded: int
    expect_last_oos_end_unbounded: str
    note: str


def _run(index: pd.DatetimeIndex, train: int, oos: int, step: int, end_date: str | None):
    cfg = WalkForwardConfig(
        train_window=train,
        oos_window=oos,
        step=step,
        end_date=pd.Timestamp(end_date) if end_date else None,
    )
    windows = generate_windows(index, cfg)
    if not windows:
        return 0, None, 0
    out_of_bound = sum(1 for w in windows if cfg.end_date is not None and w.oos_end > cfg.end_date)
    return len(windows), windows[-1].oos_end.date().isoformat(), out_of_bound


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=None, help="真实行情 parquet 路径（可选，用于 Case D）")
    args = parser.parse_args()

    cases = [
        Case(
            name="A. 文档 §5.4 口径（2017-01-03~2024-12-31，250/60/60）",
            index=pd.bdate_range("2017-01-03", "2024-12-31"),
            train=250, oos=60, step=60,
            end_date="2022-06-30",
            expect_n=19, expect_last_oos_end="2022-05-02",
            expect_n_unbounded=30, expect_last_oos_end_unbounded="2024-11-11",
            note="2086 个工作日；end_date 正好落在周五",
        ),
        Case(
            name="B. B 线 fix_evidence 口径（2020-01-01 起 1000 个工作日，train+oos=140，step=20）",
            index=pd.bdate_range("2020-01-01", periods=1000),
            train=80, oos=60, step=20,
            end_date="2021-02-24",
            expect_n=9, expect_last_oos_end="2021-02-23",
            expect_n_unbounded=44, expect_last_oos_end_unbounded="2023-10-31",
            note="这就是 §7.2 里 9 的来源；参数不同，不是另一个修复结果",
        ),
        Case(
            name="C. FIX_DIFF §2.1 口径（2018-01-01~2024-12-31，250/60/60）",
            index=pd.bdate_range("2018-01-01", "2024-12-31"),
            train=250, oos=60, step=60,
            end_date="2022-06-30",
            expect_n=15, expect_last_oos_end="2022-05-27",
            expect_n_unbounded=26, expect_last_oos_end_unbounded="2024-12-06",
            note="1827 个工作日；A 线复现报告里的 15 / 26",
        ),
    ]

    if args.data:
        path = Path(args.data)
        if not path.is_file():
            print(f"[SKIP] Case D：找不到 {path}")
        else:
            df = pd.read_parquet(path)
            idx = pd.DatetimeIndex(pd.to_datetime(df["date"]))
            cases.append(
                Case(
                    name=f"D. 真实行情（{path.name}，{len(idx)} 个交易日，250/60/60）",
                    index=idx,
                    train=250, oos=60, step=60,
                    end_date="2022-06-30",
                    expect_n=18, expect_last_oos_end="2022-06-24",
                    expect_n_unbounded=28, expect_last_oos_end_unbounded="2024-12-12",
                    note="真实交易日含节假日缺口，与 bdate_range 的计数不同",
                )
            )

    failures: list[str] = []
    print("=" * 96)
    print("P0-1  generate_windows 尊重 end_date —— 逐组核对")
    print("=" * 96)
    header = f"{'组':<4}{'窗口数(有界/无界)':<22}{'末窗 oos_end(有界/无界)':<40}{'越界窗口':<10}{'结果'}"
    print(header)
    print("-" * 96)

    for i, c in enumerate(cases, 1):
        n1, last1, oob = _run(c.index, c.train, c.oos, c.step, c.end_date)
        n2, last2, _ = _run(c.index, c.train, c.oos, c.step, None)
        ok = (
            n1 == c.expect_n
            and last1 == c.expect_last_oos_end
            and oob == 0
            and n2 == c.expect_n_unbounded
            and last2 == c.expect_last_oos_end_unbounded
        )
        tag = "PASS" if ok else "FAIL"
        if not ok:
            failures.append(
                f"Case {i} ({c.name}): 实际 {n1}/{last1}/{oob} 与 {n2}/{last2}；"
                f"期望 {c.expect_n}/{c.expect_last_oos_end}/0 与 "
                f"{c.expect_n_unbounded}/{c.expect_last_oos_end_unbounded}"
            )
        print(
            f"{i:<4}{f'{n1} / {n2}':<22}{f'{last1} / {last2}':<40}{oob:<10}{tag}"
        )
        print(f"     {c.name}")
        print(f"     {c.note}")

    print("-" * 96)

    # 不变量 1：end_date 早于全部数据 -> 空列表
    idx = pd.bdate_range("2020-01-01", periods=300)
    cfg = WalkForwardConfig(
        train_window=60, oos_window=20, step=20,
        end_date=idx[0] - pd.Timedelta(days=1),
    )
    empty = generate_windows(idx, cfg) == []
    print(f"{'不变量1':<6}end_date 早于全部数据 -> 返回空列表：{'PASS' if empty else 'FAIL'}")
    if not empty:
        failures.append("不变量1 失败：end_date 早于全部数据时未返回空列表")

    # 不变量 2：确定性（相同输入两次 -> 逐位相同）
    idx2 = pd.bdate_range("2017-01-03", "2024-12-31")
    a = generate_windows(idx2, WalkForwardConfig(train_window=250, oos_window=60, step=60))
    b = generate_windows(idx2, WalkForwardConfig(train_window=250, oos_window=60, step=60))
    same = [(w.train_start, w.oos_end, w.window_index) for w in a] == [
        (w.train_start, w.oos_end, w.window_index) for w in b
    ]
    print(f"{'不变量2':<6}相同 index + 相同 cfg -> 窗口逐位相同：{'PASS' if same else 'FAIL'}")
    if not same:
        failures.append("不变量2 失败：两次生成的窗口列表不一致")

    # 不变量 3：参数扫描下"越界窗口数恒为 0"（个数会变，语义不变）
    print("-" * 96)
    print("参数扫描（同一 index=2086 个工作日、同一 end_date=2022-06-30）：窗口数随参数变，越界数必须恒为 0")
    print(f"{'train/oos/step':<20}{'窗口数':<10}{'末窗 oos_end':<18}{'越界窗口':<10}")
    sweep_ok = True
    for train, oos, step in [(250, 60, 60), (250, 60, 20), (120, 80, 60), (60, 20, 20), (500, 120, 120)]:
        n, last, oob = _run(idx2, train, oos, step, "2022-06-30")
        print(f"{f'{train}/{oos}/{step}':<20}{n:<10}{str(last):<18}{oob:<10}")
        if oob != 0:
            sweep_ok = False
    print(f"扫描结论：{'PASS（越界数全为 0）' if sweep_ok else 'FAIL（存在越界窗口）'}")
    if not sweep_ok:
        failures.append("参数扫描失败：出现 oos_end > end_date 的窗口")

    print("=" * 96)
    if failures:
        print(f"总计：FAIL（{len(failures)} 项）")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("总计：PASS —— end_date 生效，越界窗口数 0；窗口个数随参数变化属正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
