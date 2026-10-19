"""窗口数分层诊断的边界测试（`scripts/window_skew.py`）。

为什么要为「一个分析脚本」写测试：

    分层边界是左开右闭，极易写错，而且写错之后**输出看起来完全正常**
    —— 只是某一层悄悄吞掉了相邻层的样本。初版把 `(21, 23]` 配给标签 "21-22"，
    结果 n=23 的 34 只（占样本 71%）全被塞进 "21-22" 层，各层占比与结论都失真，
    而报表照样打得漂漂亮亮。这类错只能靠断言挡。

    同理，本项目此前在 `select_evenly` 上犯过一次几乎一样的错（步长末端够不到最大代码）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "window_skew.py"

# v2 口径实测出现的全部窗口数（b50_oos75，48 只标的）
OBSERVED_WINDOWS = (5, 6, 8, 11, 12, 13, 15, 17, 19, 20, 21, 22, 23)


@pytest.fixture(scope="module")
def skew():
    spec = importlib.util.spec_from_file_location("_window_skew", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_observed_window_lands_in_matching_label(skew) -> None:
    """标签必须与层内实际窗口数自洽（左开右闭口径）。"""
    for n in OBSERVED_WINDOWS:
        label = skew.bin_label(n)
        lo_s, _, hi_s = label.partition("-")
        lo = int(lo_s)
        hi = int(hi_s) if hi_s else lo
        assert lo <= n <= hi, f"窗口数 {n} 被分到标签 '{label}'，但该标签不含它"


def test_bins_cover_all_observed_windows_exactly_once(skew) -> None:
    """每个窗口数必须落到**唯一**一层 —— 既不漏也不重。"""
    buckets: dict[str, list[int]] = {}
    for n in OBSERVED_WINDOWS:
        buckets.setdefault(skew.bin_label(n), []).append(n)
    assert sum(len(v) for v in buckets.values()) == len(OBSERVED_WINDOWS)
    # 各层互斥：同一个 n 不可能出现在两层（dict 已保证，这里显式检查无重叠值）
    flat = [n for v in buckets.values() for n in v]
    assert sorted(flat) == sorted(set(flat)), "存在窗口数被重复计入两层"


def test_no_layer_swallows_the_dominant_bin(skew) -> None:
    """占样本 71% 的 n=23 必须单独成层，不能被邻层吞掉。

    这是初版 bug 的直接回归测试：`(21,23]` 配 "21-22" 时 n=23 全进了 "21-22"。
    """
    assert skew.bin_label(23) == "23"
    assert skew.bin_label(22) == "21-22"
    assert skew.bin_label(21) == "21-22"


def test_minimal_payload_stratification_keeps_all_rows(skew) -> None:
    """分层不得丢样本：各层标的数之和 == 总数。"""
    payload = {
        "design": {"design_version": "oos75-v2", "min_oos_periods": 75},
        "results": [
            {"strategy": "ma_cross", "fee_source": "simulation_placeholder",
             "n_windows": n, "median_ret": -0.01 * n, "positive_ratio": 0.4}
            for n in OBSERVED_WINDOWS
        ],
    }
    by_bin = skew.rows_by_bin(payload, "ma_cross", "simulation_placeholder")
    assert sum(len(v) for v in by_bin.values()) == len(OBSERVED_WINDOWS)
    report = skew.analyze(payload, ("ma_cross",), ("simulation_placeholder",))
    blk = report["strategy"]["ma_cross"]["simulation_placeholder"]
    assert blk["pooled"]["n"] == len(OBSERVED_WINDOWS)
    assert isinstance(blk["pooled_is_representative"], bool)


def test_stratification_is_not_exclusion(skew) -> None:
    """分层不是剔除 —— 脚本必须显式声明，否则会被拿来删样本。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "分层不是剔除" in src, "缺少「分层不是剔除」的声明"
