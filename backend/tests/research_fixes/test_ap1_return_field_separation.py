"""A-P1：累计收益字段分离（D2 回归守卫）。

**背景**：`scripts/gate1_run.py` 原本只写一个 `strategy_total_return`，
取自 `r.strategy.cumulative_return` —— 那是 **BACKTEST_START..BACKTEST_END 的全程累计**，
却出现在「OOS / 样本外」语境下（报告列名「策略累计」）。全程值包含 IS 预热段，
直接当成样本外累计是错的。

**修复后的契约（本文件守的）**：
- `*_total_return_full`：全程累计，来自 `strategy.cumulative_return`；
- `*_total_return_eval`：**评估窗口内**累计，按 `paired` 的月度收益复利算出，
  只使用 OOS_START..spec.eval_end 这段数据。

两者必须是**两个不同字段名**且取**不同来源**。若某个实现把同一列复制两遍
（或让 eval 也取全程值），本文件会红。

规格来源：NIGHT_PROGRESS「🎯 下一阶段任务分配（2026-10-19 01:10）」A-P1 规格第四条。
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from _ap1_support import judgement_stub, load_gate1_run, make_run_result


def _read_sensitivity(out_dir) -> pd.DataFrame:
    return pd.read_csv(out_dir / "sensitivity.csv")


def test_both_fields_exist_and_are_distinct_columns(tmp_path):
    gate1_run = load_gate1_run()
    """两个字段必须同时存在于落盘结果中，且是不同的列。"""
    run = make_run_result(gate1_run)
    gate1_run.write_outputs(tmp_path, [run], judgement_stub(), elapsed=0.0)

    df = _read_sensitivity(tmp_path)
    for col in (
        "strategy_total_return_full",
        "benchmark_total_return_full",
        "strategy_total_return_eval",
        "benchmark_total_return_eval",
    ):
        assert col in df.columns, f"缺少字段 {col}；实际列：{list(df.columns)}"

    # 旧的、语义含混的字段不得继续存在（否则调用方仍会误用）
    assert "strategy_total_return" not in df.columns, (
        "旧的 `strategy_total_return` 语义不明（全程 or 窗口内？），不应保留"
    )


def test_full_uses_whole_period_and_eval_uses_window_only(tmp_path):
    gate1_run = load_gate1_run()
    """`full` 取全程累计；`eval` 只按 OOS 窗口内的月度收益复利 —— 两者不得串味。

    构造：全程累计 = +500%，窗口内两个月 +10% / -10% ⇒ 窗口内累计 = -1%。
    若实现把全程值填进 eval（或反之），这里的断言会立刻失败。
    """
    run = make_run_result(gate1_run, cumulative_return=5.0, monthly_returns=(0.10, -0.10))
    gate1_run.write_outputs(tmp_path, [run], judgement_stub(), elapsed=0.0)

    row = _read_sensitivity(tmp_path).iloc[0]

    assert row["strategy_total_return_full"] == pytest.approx(5.0), (
        "full 必须等于 strategy.cumulative_return（全程）"
    )
    expected_eval = (1.0 + 0.10) * (1.0 - 0.10) - 1.0
    assert row["strategy_total_return_eval"] == pytest.approx(expected_eval), (
        f"eval 必须只由窗口内月度收益复利得出（期望 {expected_eval:.4f}），"
        f"实际 {row['strategy_total_return_eval']}"
    )
    assert row["strategy_total_return_eval"] != pytest.approx(5.0), "eval 不得等于全程值"


def test_eval_ignores_returns_outside_the_window(tmp_path):
    gate1_run = load_gate1_run()
    """`eval` 只看 `paired` 里的月份：把窗口收益换成另一组，eval 必须跟着变。

    这条防的是「eval 其实是别处算好的全程值、恰好数值相近」这类巧合 ——
    只要窗口内容变了 eval 不变，就说明它没有真正使用窗口数据。
    """
    a = make_run_result(gate1_run, monthly_returns=(0.10, -0.10))
    gate1_run.write_outputs(tmp_path / "a", [a], judgement_stub(), elapsed=0.0)

    b = make_run_result(gate1_run, monthly_returns=(0.02, 0.03))
    gate1_run.write_outputs(tmp_path / "b", [b], judgement_stub(), elapsed=0.0)

    eval_a = _read_sensitivity(tmp_path / "a").iloc[0]["strategy_total_return_eval"]
    eval_b = _read_sensitivity(tmp_path / "b").iloc[0]["strategy_total_return_eval"]

    assert eval_a == pytest.approx(-0.01)
    assert eval_b == pytest.approx(1.02 * 1.03 - 1.0)
    assert not math.isclose(eval_a, eval_b), "窗口收益不同，eval 不应相同"


def test_benchmark_fields_follow_the_same_split(tmp_path):
    gate1_run = load_gate1_run()
    """基准侧同样要有 full / eval 两列，口径与策略侧一致（不能只修一半）。"""
    run = make_run_result(
        gate1_run,
        benchmark_cumulative_return=0.2,
        monthly_returns=(0.10, -0.10),
        benchmark_monthly=(0.01, 0.01),
    )
    gate1_run.write_outputs(tmp_path, [run], judgement_stub(), elapsed=0.0)

    row = _read_sensitivity(tmp_path).iloc[0]
    assert row["benchmark_total_return_full"] == pytest.approx(0.2)
    assert row["benchmark_total_return_eval"] == pytest.approx(1.01 * 1.01 - 1.0)
