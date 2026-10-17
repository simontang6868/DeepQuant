"""return_shape.py（B12）的判据边界测试。

存在的理由：本仓库已连续三次栽在「边界看起来对、跑起来正常、结论悄悄错」
（select_evenly 步长末端、window_skew 分层区间、以及本次的负值未定义）。
判据是结论的尺子，尺子本身的刻度必须先被测住。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "return_shape.py"


@pytest.fixture(scope="module")
def rs():
    spec = importlib.util.spec_from_file_location("_return_shape", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 判据 1：亏损窗口占比（方向性）────────────────────────────────────────


def test_loss_ratio_boundaries_are_strict(rs) -> None:
    """<40% 方向性存在 / 40~50% 不明确 / >50% 方向错，边界值必须落在高档。"""
    assert rs._verdict_loss_ratio(0.0) == "方向性存在"
    assert rs._verdict_loss_ratio(0.3999) == "方向性存在"
    assert rs._verdict_loss_ratio(0.40) == "方向不明确", "40% 是边界，应归入不明确档"
    assert rs._verdict_loss_ratio(0.4999) == "方向不明确"
    assert rs._verdict_loss_ratio(0.50) == "方向错（假设不成立）", "50% 是边界，应归入方向错档"
    assert rs._verdict_loss_ratio(1.0) == "方向错（假设不成立）"


def test_loss_ratio_exactly_half_is_direction_wrong(rs) -> None:
    """恰好一半窗口亏损 = 多数不成立但也不算方向对，按预设归入「方向错」。

    这一条是回归测试：早期版本把 ==0.5 判成「不明确」，
    会让「一半窗口在亏」被读成中性结论。
    """
    assert rs._verdict_loss_ratio(0.5) == "方向错（假设不成立）"


# ── 判据 2：|均值| − |中位数|（形状，含负值）─────────────────────────────


def test_tail_verdict_covers_negative_values(rs) -> None:
    """负值必须被定义。初版只处理了非负情况，实测中出现 -0.0131 直接落进错误档。"""
    assert rs._verdict_tail(0.05) == "尾部主导（少数窗口撑起均值）"
    assert rs._verdict_tail(0.01) == "尾部主导（少数窗口撑起均值）"
    assert rs._verdict_tail(0.0099).startswith("均值≈中位数")
    assert rs._verdict_tail(0.0).startswith("均值≈中位数")
    assert rs._verdict_tail(-0.0099).startswith("均值≈中位数")
    assert rs._verdict_tail(-0.01) == "典型窗口主导（非尾部问题）"
    assert rs._verdict_tail(-0.05) == "典型窗口主导（非尾部问题）"


def test_tail_verdict_is_symmetric_around_zero(rs) -> None:
    """正负两侧必须用同一个阈值，不能一侧严一侧松。"""
    T = rs.TAIL_DOMINANCE_PP
    assert rs._verdict_tail(T) != rs._verdict_tail(-T)
    assert rs._verdict_tail(T * 0.99) == rs._verdict_tail(-T * 0.99)


# ── 阈值常量锁定：改阈值等于改结论 ────────────────────────────────────────


def test_thresholds_are_locked(rs) -> None:
    """阈值在看到数据之前设定，任何改动都必须走提案，测试先挡一道。"""
    assert rs.LOSS_RATIO_DIRECTIONAL_MAX == 0.40
    assert rs.LOSS_RATIO_AMBIGUOUS_MAX == 0.50
    assert rs.TAIL_DOMINANCE_PP == 0.01


def test_script_docstring_declares_preset_criteria(rs) -> None:
    """判据「预先固定」必须是文件里写明的，不能只存在于口头约定。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "在看到数据之前预设" in src
    assert "禁止事后调整" in src


# ── 汇总逻辑 ────────────────────────────────────────────────────────────


def _payload(rows: list[dict]) -> dict:
    return {"results": rows, "design": {"design_version": "test"}}


def _row(strategy: str, fee: str, loss_ratio: float, tail: float, med: float = -0.01) -> dict:
    return {
        "symbol": f"{strategy}-{fee}-{loss_ratio}",
        "strategy": strategy,
        "fee_source": fee,
        "median_ret": med,
        "window_stats": {
            "n_windows_valid": 10,
            "loss_window_ratio": loss_ratio,
            "abs_mean_minus_abs_median": tail,
            "median_loss": -0.02,
            "median_gain": 0.03,
        },
    }


def test_shape_counts_sum_to_n_symbols(rs) -> None:
    """尾/典/均三档计数必须互斥且穷尽，否则报表会漏标的。"""
    rows = [
        _row("s", "simulation_placeholder", 0.3, 0.05),
        _row("s", "simulation_placeholder", 0.3, -0.05),
        _row("s", "simulation_placeholder", 0.3, 0.0),
    ]
    out = rs.summarize(_payload(rows), "simulation_placeholder")["s"]
    assert out["n_symbols"] == 3
    assert out["symbols_tail_dominated"] == 1
    assert out["symbols_typical_dominated"] == 1
    assert out["symbols_symmetric"] == 1
    assert (
        out["symbols_tail_dominated"] + out["symbols_typical_dominated"] + out["symbols_symmetric"]
        == out["n_symbols"]
    )


def test_rows_without_window_stats_are_skipped(rs) -> None:
    """旧口径结果没有 window_stats，必须跳过而不是崩掉或算成 0。"""
    rows = [
        _row("s", "simulation_placeholder", 0.3, 0.0),
        {"symbol": "old", "strategy": "s", "fee_source": "simulation_placeholder", "median_ret": 0.0},
    ]
    out = rs.summarize(_payload(rows), "simulation_placeholder")["s"]
    assert out["n_symbols"] == 1


def test_percentile_helper_matches_linear_interpolation(rs) -> None:
    v = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert rs._pct(v, 0) == 0.0
    assert rs._pct(v, 50) == 2.0
    assert rs._pct(v, 100) == 4.0
    assert rs._pct([7.0], 50) == 7.0
    assert rs._pct([], 50) == 0.0


def test_report_excludes_all_fees_when_no_data(rs) -> None:
    """空输入必须报错退出，不能打印一张看起来正常的空表（B11 的教训）。"""
    payload = {"results": [{"strategy": "s", "fee_source": "x", "median_ret": 0.0}]}
    payloads = {f: rs.summarize(payload, f) for f in rs.FEE_ORDER}
    assert all(not v for v in payloads.values())
