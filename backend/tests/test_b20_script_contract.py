"""B20 诊断脚本（`scripts/b20_window_benchmark.py`）的契约测试。

为什么需要它：这个脚本的价值全部建立在**边界**上——不跑新回测、不改参数、
不删结果、缺数据就停下。边界一旦被后人放宽，产出的就是一张「看起来是历史
结果、其实是重跑结果」的表，而表面上看不出区别。因此用 AST 把边界钉死。

同时用合成行情验证两个纯逻辑：窗口复现的数量、以及「买不起 1 手 → None」。
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "b20_window_benchmark.py"


def _load_module():
    """按文件路径加载脚本模块（scripts/ 不是包，无法 import）。"""
    spec = importlib.util.spec_from_file_location("b20_window_benchmark", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["b20_window_benchmark"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def source() -> str:
    assert SCRIPT_PATH.exists(), f"脚本不存在：{SCRIPT_PATH}"
    return SCRIPT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tree(source: str) -> ast.Module:
    return ast.parse(source)


def _called_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


# --------------------------------------------------------------------------- #
# 1. 硬边界：不跑新回测
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "forbidden",
    [
        "run_backtest",
        "run_walkforward_v2",
        "run_fixed_parameter_walkforward",
        "run_window",
        "generate_ma_cross_candidates",
    ],
)
def test_script_never_reruns_backtest(tree: ast.Module, forbidden: str) -> None:
    """禁止调用任何「跑策略」的入口 —— 一旦调用，B20 就不再是对历史结果的诊断。"""
    assert forbidden not in _called_names(tree), (
        f"脚本调用了 {forbidden}：这会产生新的回测结果，"
        "违反「不跑新回测」，且与已落盘的 288 组不再是同一批证据"
    )


def test_script_never_writes_data(tree: ast.Module) -> None:
    """不得写回行情目录（`to_csv` / `download` 之类）。"""
    called = _called_names(tree)
    assert "to_csv" not in called, "脚本不得写回行情文件"


def test_script_reuses_a7_benchmarks(tree: ast.Module) -> None:
    """必须复用 A7 的基准模块，而不是自写一份买入持有。"""
    called = _called_names(tree)
    assert "buy_and_hold" in called, "未使用 A7 的 buy_and_hold"
    assert "cash_benchmark" in called, "未使用 A7 的 cash_benchmark"


def test_script_uses_shared_window_distribution(tree: ast.Module) -> None:
    """分布口径必须走包内实现，不得在脚本里另写一套分位数。"""
    called = _called_names(tree)
    assert "WindowDistribution" in called or "from_mapping" in called
    assert "compare_windows" in called
    assert "aggregate_comparisons" in called
    # 脚本自己不得直接算分位数（那是包内实现的职责）
    assert "percentile" not in called, "脚本内出现 percentile：口径会与包内实现分叉"


def test_script_stops_when_price_cache_missing(source: str) -> None:
    """缺行情缓存必须停下（R3：不编造数据），不得跳过或造假。"""
    assert "缺少行情缓存" in source
    assert "不编造数据，直接停下" in source


def test_script_declares_pairing_limitation(source: str) -> None:
    """B1 的分布级性质必须写在脚本里，避免以后被当成配对结果引用。"""
    assert "paired=False" in source or "paired" in source
    assert "分布级" in source


# --------------------------------------------------------------------------- #
# 2. 纯逻辑：窗口复现与「不可执行 → None」
# --------------------------------------------------------------------------- #


def _synthetic_frame(n: int, *, start_price: float = 10.0, step: float = 0.1) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=n)
    closes = [start_price + step * i for i in range(n)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
        },
        index=idx,
    )


def test_build_windows_matches_recorded_count() -> None:
    """窗口数 = floor((n − train) / step)。这是历史 n_windows 的对齐依据。"""
    module = _load_module()
    design = {"min_train_periods": 150, "min_oos_periods": 75}
    df = _synthetic_frame(500)
    specs = module.build_windows(df, design)
    # 500 行：oos_end 依次 225/300/375/450 → 4 个窗口，第 5 个需 525 行
    assert len(specs) == 4
    assert specs[0].oos_start == df.index[150]
    assert specs[0].oos_end == df.index[224]
    assert specs[1].oos_start == df.index[225]


def test_build_windows_is_deterministic() -> None:
    module = _load_module()
    design = {"min_train_periods": 150, "min_oos_periods": 75}
    df = _synthetic_frame(400)
    first = module.build_windows(df, design)
    second = module.build_windows(df, design)
    assert [(w.oos_start, w.oos_end) for w in first] == [
        (w.oos_start, w.oos_end) for w in second
    ]


def test_window_returns_none_when_lot_unaffordable() -> None:
    """10 万本金买不起 1 手（100 股 × 2000 元）→ None，绝不是 0%。"""
    module = _load_module()
    from deepquant.backtest.config import BacktestConfig
    from deepquant.market.rules import a_share_rules

    df = _synthetic_frame(240, start_price=2000.0, step=0.0)
    design = {"min_train_periods": 150, "min_oos_periods": 75}
    specs = module.build_windows(df, design)
    cfg = BacktestConfig(
        symbol="600XXX.SH",
        initial_cash=100_000.0,
        market_rules=a_share_rules(price_limit_rate=None),
    )
    returns, n_bad, _ = module.window_returns(df, specs, cfg)
    assert all(r is None for r in returns)
    assert n_bad == len(specs)


def test_window_returns_positive_on_rising_prices() -> None:
    module = _load_module()
    from deepquant.backtest.config import BacktestConfig
    from deepquant.market.rules import a_share_rules

    df = _synthetic_frame(240, start_price=10.0, step=0.5)
    design = {"min_train_periods": 150, "min_oos_periods": 75}
    specs = module.build_windows(df, design)
    cfg = BacktestConfig(
        symbol="600XXX.SH",
        initial_cash=100_000.0,
        market_rules=a_share_rules(price_limit_rate=None),
    )
    returns, n_bad, _ = module.window_returns(df, specs, cfg)
    assert n_bad == 0
    assert all(r is not None and r > 0 for r in returns)
