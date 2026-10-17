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
    """研究区间必须可通过环境变量覆盖（便于做 7 年 vs 15 年对照）。"""
    assert "DEEPQUANT_START" in source
    assert "DEEPQUANT_END" in source


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
