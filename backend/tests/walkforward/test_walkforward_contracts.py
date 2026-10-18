"""walkforward/__init__.py 契约与不变量测试（A16#11，本地不提 PR）。

补既有 walkforward 测试（A11 parameter_selection 等）未覆盖的守卫 / 错误分支：

- WalkForwardConfig.__post_init__ 的输入校验（warmup 非法、oos_window < min_oos）；
- generate_windows 的入口契约：index 类型 / 时区 / 单调性 / 空索引 / start_date·end_date 裁剪；
- run_window 必须把引擎异常吞掉并包装成 WindowResult(success=False)（绝不能向上抛）；
- run_fixed_parameter_walkforward 对 full_df.index 的三道契约检查（类型 / 时区 / 单调）。

全部为语义断言：校验被违反的**具体不变量**与抛出的**稳定原因文案**，不靠「只断言不报错」。

⚠️ 死代码（不可达，已逐条核实，不强行覆盖，视作真实覆盖率上限）：
  generate_windows 的 `valid=False` 两个分支（约 259-261 / 263-264）永远走不到——
  因为 WalkForwardConfig.__post_init__ 已强制 train_window≥min_train 且 oos_window≥min_oos，
  而窗口内 n_train/n_oos 恒等于 train_window/oos_window。同理 step≤0 守卫（285）、
  _slice_with_warmup 的 get_loc 非 int 兜底（314-320 / 325-326）、
  run_fixed 的无效窗口占位分支（530-548，因 specs 永远 valid）均不可达。
  本文件用一个不变量测试「合法 config ⇒ 所有 spec.valid 恒为 True」把这条事实钉死：
  若有人移除 __post_init__ 预校验导致 specs 可变 invalid，该测试会失败报警。
"""
from __future__ import annotations

import pandas as pd
import pytest

from deepquant.backtest.config import (
    BacktestConfig,
    ExecutionPrice,
    FEE_SOURCE_SIMULATION,
)
from deepquant.walkforward import (
    WalkForwardConfig,
    WindowSpec,
    generate_windows,
    run_fixed_parameter_walkforward,
    run_window,
)

# 60 个交易日的基准索引；用较小的 min_train/min_oos 让窗口合法。
IDX = pd.date_range("2020-01-01", periods=60, freq="D")


def _cfg(**over):
    base = dict(train_window=20, oos_window=10, step=10, min_train=5, min_oos=5)
    base.update(over)
    return WalkForwardConfig(**base)


def _base_config():
    return BacktestConfig(
        symbol="600519.SH",
        initial_cash=100000.0,
        exec_price_rule=ExecutionPrice.NEXT_OPEN,
        fee_source=FEE_SOURCE_SIMULATION,
        assumptions=("a",),
    )


# =========================================================================== #
# 1. WalkForwardConfig.__post_init__ 校验守卫
# =========================================================================== #
def test_wfcfg_rejects_invalid_warmup():
    """warmup 为负或 None 以外非整数 → ValueError，且点名 warmup。"""
    with pytest.raises(ValueError) as exc:
        _cfg(warmup=-1)
    assert "warmup" in str(exc.value)
    with pytest.raises(ValueError):
        _cfg(warmup="x")  # 非整数


def test_wfcfg_rejects_oos_below_min_oos():
    """oos_window < min_oos → ValueError（点名 oos_window / min_oos）。"""
    with pytest.raises(ValueError) as exc:
        _cfg(oos_window=3, min_oos=5)
    assert "oos_window" in str(exc.value)
    assert "min_oos" in str(exc.value)


def test_wfcfg_accepts_valid_config():
    """合法构造成功且字段被正确保存。"""
    cfg = _cfg()
    assert cfg.train_window == 20 and cfg.oos_window == 10 and cfg.step == 10


# =========================================================================== #
# 2. generate_windows 入口契约
# =========================================================================== #
def test_generate_windows_rejects_non_datetime_index():
    """index 非 DatetimeIndex → TypeError。"""
    with pytest.raises(TypeError) as exc:
        generate_windows(pd.Index([1, 2, 3]), _cfg())
    assert "DatetimeIndex" in str(exc.value)


def test_generate_windows_rejects_tz_aware_index():
    """index 带时区 → ValueError。"""
    with pytest.raises(ValueError) as exc:
        generate_windows(
            pd.date_range("2020-01-01", periods=5, tz="UTC"), _cfg()
        )
    assert "tz" in str(exc.value).lower()


def test_generate_windows_rejects_non_monotonic_index():
    """index 非升序 → ValueError。"""
    with pytest.raises(ValueError) as exc:
        generate_windows(
            pd.to_datetime(["2020-01-03", "2020-01-01", "2020-01-02"]), _cfg()
        )
    assert "升序" in str(exc.value)


def test_generate_windows_empty_index_returns_empty():
    """空 DatetimeIndex → 返回空列表（不抛错）。"""
    assert generate_windows(pd.DatetimeIndex([]), _cfg()) == []


def test_generate_windows_start_date_clip_within_range():
    """start_date 落在数据区间内 → 从该位置起裁切，首窗 train_start ≥ start_date。"""
    cfg = _cfg(start_date=IDX[10])
    windows = generate_windows(IDX, cfg)
    assert windows, "预期产生窗口"
    assert windows[0].train_start >= IDX[10]


def test_generate_windows_start_date_beyond_all_data_returns_empty():
    """start_date 晚于所有数据 → 返回空列表。"""
    cfg = _cfg(start_date=IDX[-1] + pd.Timedelta(days=10))  # 超出范围
    assert generate_windows(IDX, cfg) == []


def test_generate_windows_end_date_branch_reached():
    """end_date 落在区间内 ⇒ 裁剪分支（217-225）被执行且不抛错（覆盖守卫行）。

    正确的边界裁剪语义另由 test_generate_windows_end_date_respects_boundary
    以 xfail(strict=True) 钉死——详见该测试注释中记录的 BUG。
    """
    cfg = _cfg(end_date=IDX[40])
    windows = generate_windows(IDX, cfg)
    assert windows  # 分支被触达，返回了窗口


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: generate_windows 的 end_date 裁剪在 __init__.py:222 用 (~mask) 而非 mask 求 "
        "last_true_pos，导致 end_idx 恒等于 len(index)，end_date 被完全忽略。"
        "正确行为应为『末窗 oos_end ≤ end_date』。修复后本用例将由 XPASS 触发 strict 报错，"
        "提示移除本标记。"
    ),
)
def test_generate_windows_end_date_respects_boundary():
    """end_date 落在区间内 ⇒ 末窗 oos_end 必须 ≤ end_date（正确语义，当前因 BUG 失败）。"""
    cfg = _cfg(end_date=IDX[40])
    windows = generate_windows(IDX, cfg)
    assert windows
    assert windows[-1].oos_end <= IDX[40]


def test_generate_windows_end_date_before_all_data_returns_empty():
    """end_date 早于所有数据 → 返回空列表。"""
    cfg = _cfg(end_date=IDX[0] - pd.Timedelta(days=1))
    assert generate_windows(IDX, cfg) == []


def test_generate_windows_clip_yields_empty_usable_index():
    """start_date 晚于 end_date → 可用区间为空 → 返回空列表。"""
    cfg = _cfg(start_date=IDX[50], end_date=IDX[49])
    assert generate_windows(IDX, cfg) == []


def test_generate_windows_deterministic():
    """相同 index + 相同 cfg ⇒ 完全相同的窗口列表（确定性契约）。"""
    a = generate_windows(IDX, _cfg())
    b = generate_windows(IDX, _cfg())
    assert [w.window_index for w in a] == [w.window_index for w in b]
    assert all(
        w1.train_start == w2.train_start and w1.oos_end == w2.oos_end
        for w1, w2 in zip(a, b)
    )


def test_generate_windows_all_specs_valid_for_valid_config():
    """不变量：合法 WalkForwardConfig ⇒ 生成的每个 WindowSpec 都 valid=True。

    这条把「valid=False 两个分支（259-261 / 263-264）不可达」钉成可验证事实：
    __post_init__ 已强制 train_window≥min_train 且 oos_window≥min_oos，
    窗口内 n_train/n_oos 恒等于二者，故不会触发 skip。若有人移除预校验使 specs 可变
    invalid，本测试会失败，提示契约被破坏。
    """
    windows = generate_windows(IDX, _cfg())
    assert windows
    assert all(w.valid for w in windows), "合法 config 不应产生 invalid 窗口"


# =========================================================================== #
# 3. run_window 必须把引擎异常包装成 WindowResult（绝不向上抛）
# =========================================================================== #
def test_run_window_wraps_engine_exception(monkeypatch):
    """run_backtest 抛任何异常 ⇒ 返回 WindowResult(success=False)，error 含异常类型。"""
    idx = pd.date_range("2020-01-01", periods=20, freq="D")
    full_df = pd.DataFrame({"close": range(20)}, index=idx)
    spec = WindowSpec(
        window_index=0,
        train_start=idx[0],
        train_end=idx[9],
        oos_start=idx[10],
        oos_end=idx[19],
        n_train=10,
        n_oos=10,
        valid=True,
    )

    def _boom(*a, **k):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("deepquant.backtest.engine.run_backtest", _boom)
    res = run_window(full_df, None, _base_config(), spec, warmup_days=0)
    assert res.success is False
    assert "RuntimeError" in res.error
    assert res.final_equity is None
    assert res.is_complete is False
    assert res.incomplete_reasons == ("回测引擎执行失败",)


# =========================================================================== #
# 4. run_fixed_parameter_walkforward 对 full_df.index 的三道契约检查
# =========================================================================== #
def test_run_fixed_rejects_non_datetime_index():
    """full_df.index 非 DatetimeIndex → TypeError（先于任何引擎调用）。"""
    bad = pd.DataFrame({"close": [1, 2, 3]}, index=[0, 1, 2])
    with pytest.raises(TypeError) as exc:
        run_fixed_parameter_walkforward(bad, None, None, _cfg())
    assert "DatetimeIndex" in str(exc.value)


def test_run_fixed_rejects_tz_aware_index():
    """full_df.index 带时区 → ValueError（先于任何引擎调用）。"""
    bad = pd.DataFrame(
        {"close": list(range(5))},
        index=pd.date_range("2020-01-01", periods=5, tz="UTC"),
    )
    with pytest.raises(ValueError) as exc:
        run_fixed_parameter_walkforward(bad, None, None, _cfg())
    assert "tz" in str(exc.value).lower()


def test_run_fixed_rejects_non_monotonic_index():
    """full_df.index 非升序 → ValueError（先于任何引擎调用）。"""
    bad = pd.DataFrame(
        {"close": list(range(3))},
        index=pd.to_datetime(["2020-01-03", "2020-01-01", "2020-01-02"]),
    )
    with pytest.raises(ValueError) as exc:
        run_fixed_parameter_walkforward(bad, None, None, _cfg())
    assert "升序" in str(exc.value)
