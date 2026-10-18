"""`scripts/gate1_run.py` 的契约测试。

这个脚本不是普通工具：它一按下回车就会产出研究结论。所以测试的重点不是覆盖率，
而是**闸门是否真的关着**、**统计口径是否真的正确**：

1. 授权闸门：无 `--authorized-by` / 占位符 / 太短 → 一律拒绝执行。
2. 默认行为：不带 `--execute` 时不计算任何收益，也不落盘。
3. 权重构造：负动量分数不得变成负权重（A 股无融券）。
4. 月度收益必须来自**连续净值**（R12），且能被 S12 截短。
5. 预登记哈希前缀必须与冻结文件一致——文档改了代码没改，这里必须红。
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "gate1_run.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("gate1_run_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


g1 = _load_script()


# --------------------------------------------------------------------------- #
# 授权闸门
# --------------------------------------------------------------------------- #


def test_dry_run_needs_no_authorization():
    """不带 --execute 时，闸门直接放行（默认安全的那条路不能有摩擦）。"""
    g1._check_authorization(False, None)


def test_execute_without_authorization_is_refused():
    with pytest.raises(SystemExit) as ei:
        g1._check_authorization(True, None)
    assert "用户明确授权" in str(ei.value)


def test_execute_with_blank_authorization_is_refused():
    with pytest.raises(SystemExit):
        g1._check_authorization(True, "   ")


@pytest.mark.parametrize("text", ["TODO", "tbd", "xxx", "placeholder", "待定", "test"])
def test_execute_with_placeholder_authorization_is_refused(text):
    """角色名和占位符不算授权——审计时无法区分「谁批准的」。"""
    with pytest.raises(SystemExit) as ei:
        g1._check_authorization(True, text)
    assert "占位符" in str(ei.value)


def test_execute_with_too_short_authorization_is_refused():
    with pytest.raises(SystemExit):
        g1._check_authorization(True, "跑吧")


def test_execute_with_real_authorization_passes_gate():
    """只验证闸门放行，不触发任何回测。"""
    g1._check_authorization(True, "我授权执行 Gate 1 主实验与敏感性分析")


def test_cli_defaults_to_dry_run():
    args = g1._parse_args([])
    assert args.execute is False
    assert args.authorized_by is None


# --------------------------------------------------------------------------- #
# dry-run 不产生收益
# --------------------------------------------------------------------------- #


def _toy_prices(n_days: int = 200) -> dict[str, pd.DataFrame]:
    """造 4 只跨 2017-01~2017-10 的合成行情（够 60 日窗口与多个月末）。"""
    dates = pd.bdate_range("2017-01-03", periods=n_days)
    out = {}
    for i, code in enumerate(["600000.SH", "600001.SH", "300001.SZ", "600002.SH"]):
        base = 10.0 + i
        close = [base * (1.001 ** k) for k in range(n_days)]
        out[code] = pd.DataFrame({
            "date": dates,
            "open": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "amount": [1e7] * n_days,
        })
    return out


def test_dry_run_computes_no_returns_and_writes_nothing(tmp_path):
    prices = _toy_prices()
    report = g1.dry_run(prices, data_root=tmp_path)

    assert report["will_compute_returns"] is False
    assert report["mode"] == "dry-run"
    # 输出里不得出现任何绩效字段名
    text = str(report).lower()
    for forbidden in ("mean_d", "t_stat", "sharpe", "cumulative_return", "excess"):
        assert forbidden not in text, f"dry-run 输出泄露了绩效字段：{forbidden}"
    # 目录里不该多出任何文件
    assert list(tmp_path.iterdir()) == []


def test_dry_run_reports_data_defects_not_hides_them():
    prices = _toy_prices()
    prices["300001.SZ"].loc[5, "close"] = -1.0
    report = g1.dry_run(prices, data_root=Path("."))
    assert report["data_quality"]["n_nonpositive_symbols"] == 1
    assert "300001.SZ" in report["data_quality"]["nonpositive_rows"]


def test_dry_run_probes_every_experiment():
    """13 组都必须演练过调度——跑完才发现某组几乎全程空仓就晚了。"""
    report = g1.dry_run(_toy_prices(), data_root=Path("."))
    names = [r["name"] for r in report["per_experiment_schedule"]]
    assert names[0] == "main"
    assert [n for n in names[1:]] == [f"S{i}" for i in range(1, 13)]


# --------------------------------------------------------------------------- #
# 权重构造
# --------------------------------------------------------------------------- #


def test_momentum_weight_never_assigns_negative_weights():
    """负动量分数不得变成负权重——A 股无融券，负权重是执行不了的。"""
    w = g1.momentum_weighted_targets(
        {"A": 0.30, "B": 0.10, "C": -0.20}, top_fraction=1.0, min_assets=1
    )
    assert all(v >= 0 for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["C"] == 0.0
    # 权重按分数比例：0.30 / (0.30+0.10) = 0.75
    assert w["A"] == pytest.approx(0.75)


def test_momentum_weight_falls_back_to_equal_when_all_scores_negative():
    """整段下跌行情里全部分数为负 → 退化为等权，而不是把钱按负数比例分掉。"""
    w = g1.momentum_weighted_targets(
        {"A": -0.1, "B": -0.2, "C": -0.3}, top_fraction=1.0, min_assets=1
    )
    assert len(w) == 3
    for v in w.values():
        assert v == pytest.approx(1.0 / 3)


def test_momentum_weight_is_deterministic_on_ties():
    """并列时按代码升序，否则同一份数据两次运行会选出不同组合。"""
    scores = {"600002.SH": 0.1, "600001.SH": 0.1, "600003.SH": 0.05}
    w1 = g1.momentum_weighted_targets(scores, top_fraction=0.68, min_assets=1)
    w2 = g1.momentum_weighted_targets(dict(reversed(list(scores.items()))), top_fraction=0.68, min_assets=1)
    assert w1 == w2


def test_momentum_weight_respects_min_assets():
    assert g1.momentum_weighted_targets({"A": 0.1}, top_fraction=1.0, min_assets=5) == {}


def test_equal_weight_path_matches_existing_builder():
    """weighting='equal' 必须复用它已有的构造函数，不能在这里长出第二份逻辑。

    ``min_assets`` 用实现里的值（预登记 §5 写死为 5）：只有 2 只候选时，
    正确行为是空仓而不是凑数持有。
    """
    from deepquant.backtest.portfolio import build_rebalance_schedule

    scores = {pd.Timestamp("2017-01-31"): {"A": 0.1, "B": 0.2}}
    got = g1.build_targets(scores, top_fraction=0.5, weighting="equal")
    want = build_rebalance_schedule(scores, top_fraction=0.5, min_assets=g1.MIN_ASSETS)
    assert got == want
    assert got[0].weights == {}, "候选 2 只 < min_assets=5，应空仓"


def test_equal_weight_path_with_enough_candidates_picks_top_slice():
    """候选够 5 只时按 top_fraction 取，验证上面那条不是「永远空仓」的假通过。"""
    scores = {pd.Timestamp("2017-01-31"): {f"A{i}": i / 100 for i in range(10)}}
    got = g1.build_targets(scores, top_fraction=0.2, weighting="equal")
    assert len(got[0].weights) == 2
    assert set(got[0].weights) == {"A9", "A8"}


# --------------------------------------------------------------------------- #
# 池子过滤
# --------------------------------------------------------------------------- #


def test_filter_universe_excludes_gem_for_main_board():
    prices = _toy_prices()
    main = g1.filter_universe(prices, "main_board")
    assert "300001.SZ" not in main
    assert set(main) == {"600000.SH", "600001.SH", "600002.SH"}


def test_filter_universe_all_keeps_everything():
    prices = _toy_prices()
    assert set(g1.filter_universe(prices, "all")) == set(prices)


def test_filter_universe_rejects_unknown():
    with pytest.raises(ValueError, match="universe"):
        g1.filter_universe(_toy_prices(), "gem_only")


# --------------------------------------------------------------------------- #
# 月度收益（R12）
# --------------------------------------------------------------------------- #


def _states(pairs):
    return [SimpleNamespace(date=pd.Timestamp(d), total_equity=v) for d, v in pairs]


def test_monthly_returns_uses_month_end_equity():
    """100 → 110 → 121：两个月，月收益各 10%。"""
    states = _states([
        ("2021-01-05", 100.0), ("2021-01-29", 100.0),
        ("2021-02-10", 110.0), ("2021-02-26", 110.0),
        ("2021-03-15", 121.0), ("2021-03-31", 121.0),
    ])
    r = g1.monthly_returns(states, start="2021-01-01", end="2021-12-31")
    assert list(r.index.strftime("%Y-%m")) == ["2021-02", "2021-03"]
    assert r.tolist() == pytest.approx([0.10, 0.10])


def test_monthly_returns_skips_days_without_equity():
    """估值为 None 的日子（极端停牌）不能参与，但也不能因此断掉整条序列。"""
    states = _states([
        ("2021-01-29", 100.0),
        ("2021-02-26", None),
        ("2021-03-31", 120.0),
    ])
    r = g1.monthly_returns(states, start="2021-01-01", end="2021-12-31")
    # 2 月无估值 → 被剔除；3 月相对 1 月算收益
    assert list(r.index.strftime("%Y-%m")) == ["2021-03"]
    assert r.iloc[0] == pytest.approx(0.20)


def test_monthly_returns_respects_eval_window():
    """S12 靠这个截短判定窗口，不能顺手把全样本一起算进去。"""
    states = _states([
        ("2021-01-29", 100.0), ("2021-02-26", 110.0),
        ("2021-03-31", 120.0), ("2021-04-30", 130.0),
    ])
    full = g1.monthly_returns(states, start="2021-01-01", end="2024-12-31")
    half = g1.monthly_returns(states, start="2021-01-01", end="2021-03-31")
    assert len(full) == 3
    assert len(half) == 2


def test_monthly_returns_rejects_empty():
    with pytest.raises(ValueError, match="为空"):
        g1.monthly_returns([], start="2021-01-01", end="2024-12-31")


# --------------------------------------------------------------------------- #
# 实验定义与协议一致性
# --------------------------------------------------------------------------- #


def test_spec_names_are_unique_and_complete():
    specs = g1.all_specs()
    assert len(specs) == g1.N_SENSITIVITY + 1
    names = [s.name for s in specs]
    assert names == ["main"] + [f"S{i}" for i in range(1, 13)]


def test_main_spec_matches_preregistration():
    """主实验只有一个参数组合，自由度 0——这些值是预登记 §9.2 写死的。"""
    m = g1.MAIN_SPEC
    assert (m.lookback, m.skip, m.top_fraction) == (60, 5, 0.2)
    assert m.fee_source == "broker_reference_common_2024"
    assert m.weighting == "equal"
    assert m.universe == "all"


def test_prereg_hash_prefix_matches_frozen_file():
    """脚本里记的哈希前缀必须与冻结文件一致。

    这条会红的场景：改了预登记但没更新脚本里的常量（或反之）——那意味着
    「结果报告引用的协议版本」与「实际执行的协议」可能不是同一份。
    """
    sha_file = REPO_ROOT / "docs" / "GATE1_PREREGISTRATION.sha256"
    if not sha_file.exists():
        pytest.skip("冻结文件不存在")
    frozen = sha_file.read_text(encoding="utf-8").split()[0]
    assert frozen.startswith(g1.PREREG_SHA256), (
        f"脚本记录 {g1.PREREG_SHA256}，冻结文件为 {frozen[:16]}…"
    )


def test_run_id_is_content_addressed():
    """同数据同协议 → 同 run_id；改一只股票的行数 → 必变。"""
    prices = _toy_prices()
    a = g1.make_run_id(prices)
    assert a == g1.make_run_id(dict(prices))

    changed = dict(prices)
    changed["600000.SH"] = changed["600000.SH"].iloc[:-1]
    assert g1.make_run_id(changed) != a


def test_portfolio_price_limit_is_conservative():
    """预登记 §6 要 board 模式，组合层只能给单一比例。

    选 10% 而不是 20% 是有方向的：10% 对 41/50 主板正确、对 2020-08-24 前的
    创业板也正确，只在之后对 9 只双创偏严 → 悲观偏差。20% 会让主板永久偏松。
    """
    assert g1.PORTFOLIO_PRICE_LIMIT_RATE == 0.10
    assert g1.PORTFOLIO_PRICE_LIMIT_RATE <= 0.10
    assert math.isfinite(g1.PORTFOLIO_PRICE_LIMIT_RATE)


def test_execute_refuses_to_overwrite_existing_results(tmp_path):
    """同配置重复运行必须拒绝覆盖——覆盖会毁掉「可复现」这条证据。"""
    out = tmp_path / "run"
    out.mkdir()
    g1.ensure_no_overwrite(out)          # 空目录放行

    (out / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        g1.ensure_no_overwrite(out)
    assert "拒绝覆盖" in str(ei.value)


# --------------------------------------------------------------------------- #
# --exclude-symbols：预登记 §10.2 第三条（数据缺陷影响是否可隔离）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,want",
    [
        ("600809.SH", ("600809.SH",)),
        ("600809.SH,000568.SZ", ("600809.SH", "000568.SZ")),
        ("600809.SH, 000568.SZ", ("600809.SH", "000568.SZ")),
        ("  600809.SH   000568.SZ  ", ("600809.SH", "000568.SZ")),
        ("600809.SH,，000568.SZ", ("600809.SH", "000568.SZ")),   # 全角逗号也认
        ("600809.SH,600809.SH", ("600809.SH",)),                  # 去重
    ],
)
def test_parse_exclude_symbols_accepts_comma_and_whitespace(raw, want):
    assert g1.parse_exclude_symbols(raw) == want


def test_parse_exclude_symbols_default_is_empty():
    """不传参数 = 不排除；这条是「默认行为零变化」的前提。"""
    assert g1.parse_exclude_symbols(None) == ()


@pytest.mark.parametrize("raw", ["", "   ", ",", ",,", " "])
def test_parse_exclude_symbols_rejects_blank(raw):
    """空串必须报错，不能静默当成「没排除」。

    若 ``--exclude-symbols ""`` 被当成不过滤，用户会以为跑的是排除版对照，
    实际跑的是全池版——两版混淆会直接污染 §10.2 第三条的符号判定。
    """
    with pytest.raises(ValueError, match="exclude-symbols"):
        g1.parse_exclude_symbols(raw)


def test_apply_exclusion_drops_symbol_and_keeps_order():
    prices = _toy_prices()
    kept = g1.apply_exclusion(prices, ("600001.SH",))
    assert "600001.SH" not in kept
    assert list(kept) == [c for c in prices if c != "600001.SH"]


def test_apply_exclusion_is_noop_when_empty():
    prices = _toy_prices()
    assert g1.apply_exclusion(prices, ()) == prices


def test_apply_exclusion_rejects_unknown_symbol():
    """不存在的代码必须报错：静默忽略会让「排除后重跑」实际没排除。"""
    with pytest.raises(ValueError, match="不存在"):
        g1.apply_exclusion(_toy_prices(), ("999999.SH",))


def test_apply_exclusion_refuses_to_empty_the_pool():
    prices = _toy_prices()
    with pytest.raises(ValueError, match="清空池子"):
        g1.apply_exclusion(prices, tuple(prices))


def test_excluded_symbol_is_really_absent_from_panel():
    """端到端确认：剔除后 CrossSectionPanel 的 symbols 里确实没有它。"""
    prices = _toy_prices()
    kept = g1.apply_exclusion(prices, ("600001.SH",))
    cal = g1.trading_calendar(kept)
    panel = g1.CrossSectionPanel(
        kept, config=g1.CrossSectionConfig(lookback=60, skip=5), calendar=cal
    )
    assert "600001.SH" not in panel.symbols
    assert "600000.SH" in panel.symbols
    assert len(panel.symbols) == len(prices) - 1


def test_symbol_flip_flag_fires_on_sign_change():
    flag = g1.symbol_flip_flag(0.004, -0.001)
    assert flag is not None
    assert "符号翻转" in flag and "无法隔离" in flag


def test_symbol_flip_flag_is_silent_when_sign_agrees():
    assert g1.symbol_flip_flag(0.004, 0.002) is None
    assert g1.symbol_flip_flag(-0.004, -0.002) is None


def test_symbol_flip_flag_treats_exact_zero_as_non_positive():
    """控制组恰为 0 视为非正（与脚本里 S11/S9 两处既有判定同一口径）。"""
    assert g1.symbol_flip_flag(0.004, 0.0) is not None
    assert g1.symbol_flip_flag(-0.004, 0.0) is None


def test_dry_run_without_exclusion_has_no_extra_keys():
    """回归：不带 --exclude-symbols 时输出结构与加入该功能之前完全一致。"""
    report = g1.dry_run(_toy_prices(), data_root=Path("."))
    for key in ("excluded_symbols", "extra_control_run", "n_symbols_after_exclusion"):
        assert key not in report, f"默认输出不应新增 {key}"
    assert report["estimated_backtests"] == 2 * len(g1.all_specs())


def test_dry_run_with_exclusion_reports_control_cost():
    prices = _toy_prices()
    kept = g1.apply_exclusion(prices, ("600001.SH",))
    report = g1.dry_run(kept, data_root=Path("."), excluded_symbols=("600001.SH",))

    assert report["excluded_symbols"] == ["600001.SH"]
    assert report["n_symbols_after_exclusion"] == len(kept)
    # 26 → 28：对照跑是 1 组实验 × (策略 + 基准)
    assert report["estimated_backtests"] == 2 * (len(g1.all_specs()) + 1)
    assert "600809" in report["extra_control_run"] or "数据缺陷" in report["extra_control_run"]


def test_cli_parses_exclude_symbols_argument():
    args = g1._parse_args(["--exclude-symbols", "600809.SH"])
    assert args.exclude_symbols == "600809.SH"
    assert args.execute is False
    assert g1._parse_args([]).exclude_symbols is None


def _write_toy_csvs(tmp_path: Path) -> None:
    for code, df in _toy_prices().items():
        df.to_csv(tmp_path / f"{code}.qfq.csv", index=False)


def test_main_dry_run_with_exclusion_runs_no_backtest(tmp_path, monkeypatch):
    """带 --exclude-symbols 走 dry-run 时，一条回测都不许跑。"""
    _write_toy_csvs(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("dry-run 不得调用 run_one（那会算出收益）")

    monkeypatch.setattr(g1, "run_one", _boom)
    # 断言「运行前后输出目录一致」，而不是「输出目录不存在」。
    # 后者把结论建立在工作区恰好干净之上：一旦有人真跑过一次，目录留下，
    # 这条就会假失败，而真正要守的东西——dry-run 没改动任何东西——反而没测到。
    before = _gate1_out_snapshot()
    code = g1.main(["--data-root", str(tmp_path), "--exclude-symbols", "600001.SH"])
    assert code == 0
    assert _gate1_out_snapshot() == before


def _gate1_out_snapshot() -> dict[str, tuple[int, int]]:
    """默认输出目录快照：相对路径 → (字节数, mtime_ns)。"""
    d = REPO_ROOT / "gate1_out"
    if not d.exists():
        return {}
    return {
        str(p.relative_to(d)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in d.rglob("*")
        if p.is_file()
    }


def test_write_outputs_persists_control_evidence(tmp_path):
    """对照跑必须落盘可复核，不能只在内存里比较完就丢。"""
    spec = g1.MAIN_SPEC

    def _result(mean_d, name):
        stats = SimpleNamespace(
            n=48, mean_d=mean_d, std_d=0.05, t_stat=1.5, p_value=0.14,
            win_ratio=0.5, as_dict=lambda: {"n": 48, "mean_d": mean_d},
        )
        paired = pd.DataFrame({"month": ["2021-01"], "r_strategy": [0.0],
                               "r_benchmark": [0.0], "d": [mean_d]})
        return SimpleNamespace(
            spec=SimpleNamespace(name=name, variable="—", value="基准配置"),
            strategy=SimpleNamespace(cumulative_return=0.1),
            benchmark=SimpleNamespace(cumulative_return=0.05),
            paired=paired, stats=stats, elapsed=1.0, q_value=None,
        )

    results = [_result(0.004, "main")] + [
        _result(0.001, s.name) for s in g1.SENSITIVITY_SPECS
    ]
    judgement = SimpleNamespace(as_dict=lambda: {"verdict": "insufficient"})
    control = _result(-0.002, "main_excluded")

    summary = g1.write_outputs(
        tmp_path, results, judgement, elapsed=1.0,
        excluded_symbols=("600809.SH",), control=control,
    )

    assert (tmp_path / "monthly_paired_excluded.csv").exists()
    assert summary["excluded_symbols"] == ["600809.SH"]
    assert summary["control_excluded"]["stats"]["mean_d"] == -0.002

    import json
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["excluded_symbols"] == ["600809.SH"]
    assert "monthly_paired_excluded.csv" in manifest["files"]
