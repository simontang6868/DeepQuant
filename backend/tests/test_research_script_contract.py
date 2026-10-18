"""Step 7.5 研究脚本（`data/step75/research.py`）的契约测试。

背景（B 线 ⑩）
---------------
`research.py` 是**顶层可执行脚本**（模块级代码直接跑），无法 import，因此也不可能有
常规单测。这导致三件事长期无人发现：

1. `min_trades=None` —— 样本充分性门槛功能早已实现，但研究里**从未启用**；
2. `[i for i in issues if i.level == "warning"]` —— ERROR 级 issue 被静默丢弃，
   于是 qfq 产生的非正价格（实测宁德 2018-06-11 close=-2.07）直接进了 walk-forward；
3. `sys.path.insert(0, "/workspace/DeepQuant/backend/src")` —— 硬编码容器路径，
   换机器后 import 立即失败。

本文件用两种方式把这三条钉死：

- **AST 契约**：解析源码，断言配置项与闸门结构存在且取值正确。
  用 AST 而不是字符串匹配，是为了让「把 min_trades 改回 None」这类回归
  在结构层面被发现，而不是被注释里的同名文本蒙混过关。
- **闸门有效性**：构造真实的负价格形态，断言 `validate_daily_extended`
  确实报 ERROR 且 `is_valid` 为 False —— 证明闸门判据本身有效，不是摆设。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from deepquant.data.validation.checks_extended import validate_daily_extended

# backend/tests/<this file> → parents[2] == 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH = REPO_ROOT / "data" / "step75" / "research.py"

pytestmark = pytest.mark.skipif(
    not RESEARCH.exists(),
    reason="data/step75/research.py 不存在（裁剪检出），跳过脚本契约测试",
)


@pytest.fixture(scope="module")
def source() -> str:
    return RESEARCH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tree(source: str) -> ast.Module:
    return ast.parse(source)


def _calls(tree: ast.Module, func_name: str) -> list[ast.Call]:
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
        ):
            out.append(node)
    return out


# =========================================================================== #
# 1. 配置契约：min_trades 必须启用且为 2
# =========================================================================== #


def test_every_grid_enables_min_trades(tree: ast.Module) -> None:
    """每个 GridSearchConfig 都必须显式设 min_trades=2。

    min_trades 的作用位置是 IS 段（`parameter_selection.py:190`，
    在 `select_best` 之前、用 train_df 的交易次数），不接触 OOS → 不违 R2/R11。
    取值 2 的结构性理由：IS 150 交易日 / 网格最慢均线 60 日 → 至少一个完整往返。
    """
    grids = _calls(tree, "GridSearchConfig")
    assert len(grids) >= 2, f"预期至少 MA / MR 两个网格，实际 {len(grids)} 个"

    for idx, call in enumerate(grids):
        kwargs = {k.arg: k.value for k in call.keywords if k.arg}
        assert "min_trades" in kwargs, f"第 {idx+1} 个 GridSearchConfig 未显式设置 min_trades"
        value = kwargs["min_trades"]
        assert isinstance(value, ast.Constant), (
            f"第 {idx+1} 个 min_trades 必须是字面量（便于人工审计），实际是 {type(value).__name__}"
        )
        assert value.value == 2, (
            f"第 {idx+1} 个 min_trades 应为 2，实际 {value.value!r}。"
            "若确需变更，请先更新 docs/DECISION_RULING_2026-10-17.md 并保留本条断言"
        )


def test_no_min_trades_none_left(tree: ast.Module) -> None:
    """NONE 常量残留检查：不得再出现 min_trades=None。"""
    for call in _calls(tree, "GridSearchConfig"):
        for kw in call.keywords:
            if kw.arg == "min_trades":
                assert not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is None
                ), "min_trades 不应为 None（该门槛在研究里必须启用）"


# =========================================================================== #
# 2. 数据质量闸门
# =========================================================================== #


def test_gate_uses_validation_report_is_valid(source: str) -> None:
    """闸门判据必须来自 ValidationReport.is_valid（既有实现，勿自造新判据）。"""
    assert "report_ext.is_valid" in source, "未使用 ValidationReport.is_valid 作为数据闸门判据"


def test_gate_records_data_ok_and_skips(source: str) -> None:
    """data_ok 必须随标的落盘，被拦截的标的必须进入 skipped（R5 不删失败记录）。"""
    assert '"data_ok"' in source, "download_results 未记录 data_ok"
    assert "skipped" in source, "被闸门拦截的标的未记录到 skipped"
    assert '"data_quality_gate"' in source, "skipped 未标注 reason=data_quality_gate"


def test_errors_are_retained_not_dropped(source: str) -> None:
    """ERROR 与 WARNING 都必须原样保留（此前只收 warning）。"""
    assert 'if i.level == "error"' in source, "未收集 ERROR 级 issue"
    assert 'if i.level == "warning"' in source, "未收集 WARNING 级 issue"


# =========================================================================== #
# 3. 可移植性与溯源
# =========================================================================== #


def test_no_hardcoded_container_path(tree: ast.Module) -> None:
    """代码中的字符串常量不得再出现 /workspace/ 容器路径。

    用 AST 而非字符串匹配：注释里说明历史原因（"此前是 /workspace/..."）
    是合法且必要的，不该被判为违规；只有**代码里**的路径才会导致 import 失败。
    """
    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "/workspace/" in node.value
    ]
    assert not offenders, f"代码中仍存在硬编码容器路径: {offenders}"


def test_research_range_is_env_overridable(source: str) -> None:
    """研究区间必须可通过环境变量覆盖（便于做 7 年 vs 8 年 vs 15 年对照）。"""
    assert "DEEPQUANT_START" in source
    assert "DEEPQUANT_END" in source


def test_default_start_is_2017(tree: ast.Module) -> None:
    """默认起点必须是 20170101（8 年），**不能**是 20100101（15 年）。

    15 年经实测否决：qfq 前复权会把高分红标的的早期价格减成负数。
    2010-2024 实测 6 只里 5 只含负价（茅台 1575 行 / 五粮液 1231 / 长电 1170 /
    中国平安 849），负价区间全部在 2016-09-29 之前结束。
    配数据质量闸门后 2010 起只有 1/6 标的可用；2017 起有 5/6 可用，且正好 8 年。

    这条断言的作用：防止后人「为了更多数据」把默认值改回 2010，
    那会让 60 组研究静默缩水成 6 组。
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "START" for t in node.targets):
            continue
        call = node.value
        assert isinstance(call, ast.Call), "START 应通过 os.environ.get 赋值"
        assert len(call.args) == 2, "START 应有 2 个参数（环境变量名、默认值）"
        default = call.args[1]
        assert isinstance(default, ast.Constant), "START 的默认值必须是字面量"
        assert default.value == "20170101", (
            f"START 默认值应为 20170101（8 年），实际 {default.value!r}。"
            "改回 20100101 会因 qfq 负价使可用标的从 5/6 掉到 1/6"
        )
        return
    raise AssertionError("未找到 START 的赋值语句")


def test_output_records_range_and_design(source: str) -> None:
    """输出 JSON 必须记录请求区间与设计参数，否则不同区间结果无法区分。"""
    assert '"request_range"' in source, "输出未记录 request_range"
    assert '"design"' in source, "输出未记录 design（min_trades / 仓位 / 历史年数）"


# =========================================================================== #
# 4. 闸门有效性：真实负价格必须被拦下
# =========================================================================== #


def _ohlcv(closes: list[float]) -> pd.DataFrame:
    """构造最小合法日线表：date + OHLC + volume。"""
    n = len(closes)
    c = np.asarray(closes, dtype="float64")
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2018-01-02", periods=n),
            "open": c * 0.995,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": np.full(n, 1_000_000.0),
        }
    )


def test_gate_rejects_negative_qfq_price() -> None:
    """复刻宁德时代 2018-06-11（qfq close=-2.07）：必须判为数据不合格。

    这是本闸门存在的**唯一理由**——若这条断言不成立，闸门就是摆设。
    """
    df = _ohlcv([10.0, 9.5, -2.07, 8.0, 8.2])
    report = validate_daily_extended(df, adjust_status="qfq")

    assert not report.is_valid, "负价格数据竟被判为合格"
    assert "invalid_price" in report.error_codes(), (
        f"未报 invalid_price，实际 ERROR 列表: {report.error_codes()}"
    )
    issue = report.get("invalid_price")
    assert issue is not None and issue.count >= 1, "invalid_price 的计数应为非零"


def test_gate_accepts_clean_series() -> None:
    """干净数据必须通过——闸门不能误杀正常标的。"""
    df = _ohlcv([10.0, 10.2, 9.9, 10.1, 10.3])
    report = validate_daily_extended(df, adjust_status="qfq")
    assert report.is_valid, f"干净数据被误判为不合格: {report.error_codes()}"
    assert "invalid_price" not in report.error_codes()


def test_gate_rejects_non_finite_price() -> None:
    """非有限价格（inf/nan）同样属于 invalid_price。"""
    df = _ohlcv([10.0, 10.1, 10.2, 10.3, 10.4])
    df.loc[2, "close"] = np.inf
    report = validate_daily_extended(df, adjust_status="qfq")
    assert not report.is_valid
    assert "invalid_price" in report.error_codes()


# =========================================================================== #
# 5. 扩样股票池（build_universe.py + DEEPQUANT_UNIVERSE）
# =========================================================================== #
# 扩样的动机是 docs/EXTERNAL_VALIDITY_2026-10-17.md 的结论：6 只手挑标的
# 支撑不了任何总体结论。这里锁住三件事，防止扩样变成「换个池子继续讲故事」：
#   1) 池子可复现（等距抽样，无随机种子争议）；
#   2) 偏差必须随结果落盘（幸存者偏差不写清楚 = 结果不可信）；
#   3) 默认池不变（历史结果仍可比）。

UNIVERSE_BUILDER = REPO_ROOT / "data" / "step75" / "build_universe.py"


@pytest.fixture(scope="module")
def universe_source() -> str:
    if not UNIVERSE_BUILDER.exists():
        pytest.skip("data/step75/build_universe.py 不存在（裁剪检出）")
    return UNIVERSE_BUILDER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def universe_module(universe_source: str):
    """按文件路径加载 build_universe（不是包，只能 spec 加载）。

    该模块顶层 `import akshare`（取成分股用）。未装 akshare 的环境直接跳过，
    不让「缺可选依赖」变成红灯 —— 与 PR #8 的 extras 口径一致。
    """
    import importlib.util

    pytest.importorskip("akshare", reason="未安装 akshare，跳过扩样池单测")
    spec = importlib.util.spec_from_file_location("_step75_build_universe", UNIVERSE_BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_research_supports_universe_env(source: str) -> None:
    """research.py 必须支持 DEEPQUANT_UNIVERSE，否则扩样只能靠改代码。"""
    assert "DEEPQUANT_UNIVERSE" in source, "未支持 DEEPQUANT_UNIVERSE 环境变量"


def test_research_output_records_universe_meta(source: str) -> None:
    """扩样池的 meta（含偏差声明）必须写进输出 JSON。

    只报「50 只的中位数」而不报「池子是抓取当日的成分股」= 用偏差数据讲总体结论。
    """
    assert '"universe"' in source, "输出未记录 universe 元数据"


def test_evenly_sampling_is_deterministic(universe_module) -> None:
    """同一输入两次抽样必须完全一致（无随机种子，可逐字复现）。"""
    rows = [{"code": f"{i:06d}", "name": f"S{i}"} for i in range(300)]
    first = universe_module.select_evenly(rows, 50)
    second = universe_module.select_evenly(list(reversed(rows)), 50)
    assert [r["code"] for r in first] == [r["code"] for r in second]
    assert len(first) == 50


def test_evenly_sampling_covers_code_range(universe_module) -> None:
    """等距抽样必须覆盖代码区间的两端，不能只挤在 600xxx 段。"""
    rows = [{"code": f"{i:06d}", "name": f"S{i}"} for i in range(300)]
    picked = universe_module.select_evenly(rows, 50)
    codes = [r["code"] for r in picked]
    assert codes[0] == "000000", f"未覆盖最小代码，实际首位 {codes[0]}"
    assert codes[-1] == "000299", f"未覆盖最大代码，实际末位 {codes[-1]}"


def test_evenly_sampling_returns_all_when_pool_smaller(universe_module) -> None:
    """池子小于目标数量时应全取，不能重复填充。"""
    rows = [{"code": f"{i:06d}", "name": f"S{i}"} for i in range(7)]
    picked = universe_module.select_evenly(rows, 50)
    assert len(picked) == 7
    assert len({r["code"] for r in picked}) == 7


def test_tencent_symbol_prefix(universe_module) -> None:
    """腾讯行情接口要求 sh/sz 前缀：6 开头 → sh，其余 → sz。"""
    assert universe_module._tencent_symbol("600519") == "sh600519"
    assert universe_module._tencent_symbol("000001") == "sz000001"
    assert universe_module._tencent_symbol("300750") == "sz300750"


def test_universe_builder_declares_survivorship_bias(universe_source: str) -> None:
    """幸存者偏差与抽样偏差必须以 meta 键名落盘（不是只写在注释里）。

    抓取当日的成分股 ≠ 2017 年的成分股：缺退市/被剔除的失败样本，
    收益类指标系统偏乐观。这个声明一旦丢了，扩样结果就会被当成总体结论。
    """
    assert "bias_survivorship" in universe_source, "未声明幸存者偏差"
    assert "bias_sampling" in universe_source, "未声明抽样偏差"


# =========================================================================== #
# 6. 设计探针开关：只许改研究设计，不许借道调参
# =========================================================================== #
# 探针要回答「零中位数是网格太慢还是 OOS 太短」，必须能改 OOS 长度与慢线上限。
# 但这个开关一旦放开到 min_trades / IS 长度，就从「设计探针」变成「调参」，
# 直接踩 R2（不用 OOS 选参）与 R11（不按 OOS 收益择优）。所以白名单必须被测试锁住。


def _design_whitelist(tree: ast.Module) -> set[str]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "_ALLOWED_DESIGN_KEYS"
                for t in node.targets
            )
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError("未找到 _ALLOWED_DESIGN_KEYS 定义（探针白名单被删了？）")


def test_design_whitelist_excludes_tuning_knobs(tree: ast.Module) -> None:
    """白名单只允许设计维度；min_trades / IS 长度 / 预热一律不得进。"""
    allowed = _design_whitelist(tree)
    assert allowed == {"min_oos_periods", "ma_slow_max", "fees"}, (
        f"探针白名单被改动：{sorted(allowed)}。"
        "放开 min_trades / min_train_periods 会同时违反 R2 与 R11"
    )
    forbidden = {"min_trades", "min_train_periods", "warmup_days"}
    assert not (allowed & forbidden), f"白名单混入调参键：{sorted(allowed & forbidden)}"


def test_design_defaults_match_ruling(source: str) -> None:
    """默认值必须是**当前已裁决口径**：OOS 75 天、慢线上限 60、三档费率。

    历史：v1 是 OOS 50（PR #23 及此前全部结果）。设计探针实测后拍板改 75 ——
    零中位数率 ma 33.3%→10.4%、mr 29.2%→16.7%。
    **这条断言是防回退的锚点**：谁把默认值改回 50，就是悄悄让新旧结果混算。
    """
    assert "DEFAULT_OOS_WINDOW = 75" in source, (
        "OOS 默认值被改动（裁决为 75）。若要再改，必须同时 bump DESIGN_VERSION "
        "并在文档里标注旧结果口径"
    )
    assert "DEFAULT_MA_SLOW_MAX = 60" in source, "慢线上限默认值被改动（裁决为 60）"
    # 默认值必须从常量取，不能散落字面量（否则两处不一致时测试查不出来）
    assert '_DESIGN.get("min_oos_periods", DEFAULT_OOS_WINDOW)' in source, (
        "OOS 默认值未走 DEFAULT_OOS_WINDOW 常量"
    )
    assert '_DESIGN.get("ma_slow_max", DEFAULT_MA_SLOW_MAX)' in source, (
        "慢线上限默认值未走 DEFAULT_MA_SLOW_MAX 常量"
    )
    assert "_FEE_SET = _DESIGN.get(\"fees\")" in source, "费率档未走探针开关"


def test_design_version_recorded(source: str) -> None:
    """口径版本必须落盘，否则不同口径的收益数字会被当成同一尺度比较。

    v1(50) → v2(75)：OOS 越长，单窗口覆盖的行情段越长，收益中位数天然更极端
    （ma −0.58% → −2.15%）——这是口径差，不是策略变差。
    v2 → **v3**（B18 / D2）：启用涨跌停会拦截部分成交，成交笔数与收益双双改变，
    与 v2 同样不可直接比较。

    **这条断言是变更留痕机制**：每次 bump 都必须到这里显式改一次，
    不允许「悄悄改版本号让结果看起来可比」。
    """
    assert 'DESIGN_VERSION = "oos75-v3"' in source, (
        "口径版本号被删或未同步（当前裁决为 oos75-v3：OOS75 + 启用涨跌停）"
    )
    assert '"design_version": DESIGN_VERSION' in source, "design 未落盘 design_version"


def test_probe_override_is_flagged(source: str) -> None:
    """探针变体偏离默认口径时必须自证，不能被当成正式口径的结果引用。"""
    assert '"is_probe_override": bool(_DESIGN)' in source, (
        "design 未落盘 is_probe_override，探针结果会被误当正式口径"
    )


def test_output_records_resolved_design(source: str) -> None:
    """落盘必须是**实际生效值**，否则 50/75/100 天 OOS 的结果无法区分。"""
    assert '"min_oos_periods": OOS_WINDOW' in source, "design 未落盘生效的 OOS 长度"
    assert '"ma_slow_candidates"' in source, "design 未落盘生效的慢线候选"
    assert '"fees"' in source, "design 未落盘生效的费率档"


def test_design_override_rejects_tuning_keys(tmp_path) -> None:
    """传入 min_trades 覆盖时必须直接报错退出，绝不静默忽略。"""
    pytest.importorskip("akshare", reason="未安装 akshare，跳过脚本启动测试")
    import subprocess
    import sys

    env = {
        **os.environ,
        "DEEPQUANT_DESIGN_OVERRIDES": '{"min_trades": 2}',
        "DEEPQUANT_UNIVERSE": str(REPO_ROOT / "data" / "step75" / "universe" / "csi300_50.json"),
        # 写到临时目录：脚本在解析覆盖前就会 mkdir(DATA_ROOT)，不能让它污染仓库
        "DEEPQUANT_DATA_ROOT": str(tmp_path),
    }
    proc = subprocess.run(
        [sys.executable, str(RESEARCH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode != 0, "非法覆盖键竟未报错退出"
    combined = proc.stdout + proc.stderr
    assert "不允许的键" in combined, f"报错信息不明确，实际输出：{combined[:300]}"
    # 白名单生效意味着在下载前就退出 → 临时目录里不应该有任何 CSV
    assert not list(tmp_path.glob("*.csv")), "非法覆盖后仍执行了下载"


# =========================================================================== #
# 7. 涨跌停配置（B18 / D2 修复）
# =========================================================================== #
# 历史缺陷：`BacktestConfig(...)` 未传 `market_rules` → 走默认 `a_share_rules()`
# → `price_limit_rate=None` → `limit_prices()` 返 None → 涨跌停判定被跳过。
# 于是**全部历史研究（含 48 只 288 组）从未模拟涨跌停**。
# 对横截面动量影响最大：赢家组合常含涨停股，实盘买不到，不建模 = 系统性高估。
#
# 这组断言的职责：任何人都不能把 market_rules 悄悄删回默认。


def test_backtest_config_passes_market_rules(tree: ast.Module) -> None:
    """每个 BacktestConfig 都必须显式传 market_rules（D2 回归闸门）。"""
    calls = _calls(tree, "BacktestConfig")
    assert calls, "未找到 BacktestConfig 调用"
    for idx, call in enumerate(calls):
        kwargs = {k.arg for k in call.keywords if k.arg}
        assert "market_rules" in kwargs, (
            f"第 {idx + 1} 个 BacktestConfig 未传 market_rules —— "
            "会退回 price_limit_rate=None，涨跌停判定被跳过（D2 回归）"
        )


def test_price_limit_defaults_to_board_mode(source: str) -> None:
    """默认模式必须是 board，不能是 off。

    off 会让主结果重新变回「未模拟涨跌停」，且这一次是**主动配置**的，
    比原来的「忘了传」更难被发现。
    """
    assert "DEEPQUANT_PRICE_LIMIT" in source, "未提供涨跌停模式开关"
    assert 'os.environ.get("DEEPQUANT_PRICE_LIMIT", "board")' in source, (
        "涨跌停默认模式必须是 board（按板块 10%/20%），实际默认值被改动"
    )


def test_price_limit_mode_uses_shared_resolver(source: str) -> None:
    """研究脚本不得自写一份板块判定，必须复用 market.rules 的纯函数。"""
    assert "a_share_price_limit_rate" in source, (
        "未复用 market.rules 的比例解析 —— 两份实现迟早分叉"
    )


def test_output_records_resolved_price_limits(source: str) -> None:
    """必须逐标的落盘实际比例，否则无法判断某份结果是启用前还是启用后。"""
    assert '"price_limit_rate_by_symbol"' in source, "未逐标的落盘实际涨跌停比例"
    assert '"price_limit_enabled"' in source, "未落盘涨跌停是否启用"


def test_off_mode_carries_warning(source: str) -> None:
    """off 模式必须自带「不得作主结果」的警告，防止消融对照被当正式结论。"""
    assert "off_mode_warning" in source
    assert "不得将本结果作为主结果引用" in source
