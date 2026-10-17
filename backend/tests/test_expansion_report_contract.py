"""扩样对照脚本的口径守卫测试（`scripts/expansion_report.py`）。

为什么单独为「一个报表脚本」写测试：

    这个脚本唯一容易犯的错，是拿**两份不同研究口径**的结果直接相减，然后把
    **口径差**当成**扩样效应**报出去。这种错在数字上完全看不出来 ——
    OOS 50→75 会让收益中位数天然更极端（ma −0.58% → −2.15%），
    若基线是 OOS50、扩样是 OOS75，对照列会显示「扩样后策略变差 3.7 倍」，
    而真相只是窗口变长了。没有任何一位读者能从报表里看出这一点。

    所以守卫必须比报表本身更可靠：它若失效，前面所有结论都可疑。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "expansion_report.py"


@pytest.fixture(scope="module")
def report_module():
    """按文件路径加载脚本（scripts/ 不是包）。"""
    spec = importlib.util.spec_from_file_location("_expansion_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(oos: int, *, version: str | None = "oos75-v2", design: bool = True) -> dict:
    """构造最小 summary payload（只含口径守卫关心的字段）。"""
    body: dict = {"results": []}
    if design:
        body["design"] = {
            "design_version": version,
            "min_oos_periods": oos,
            "min_train_periods": 150,
            "min_trades": 2,
            "ma_slow_candidates": [15, 25, 40, 60],
        }
    return body


# ---------------------------------------------------------------- 守卫判定


def test_same_design_passes(report_module) -> None:
    """同口径 → 放行，且不产生差异项。"""
    diffs = report_module.assert_same_design(_payload(75), _payload(75), allow_mixed=False)
    assert diffs == []


def test_mixed_oos_is_rejected(report_module) -> None:
    """OOS 50 vs 75 必须硬拒绝 —— 这是 v1/v2 口径的真实分界。"""
    with pytest.raises(SystemExit) as exc:
        report_module.assert_same_design(_payload(50, version=None), _payload(75), allow_mixed=False)
    msg = str(exc.value)
    assert "口径不同" in msg
    # 报错必须点名是哪一项不一致，否则用户不知道该改哪个文件
    assert "min_oos_periods" in msg


def test_mixed_min_trades_is_rejected(report_module) -> None:
    """min_trades 不同同样不可比（它直接改变参与窗口集合）。"""
    base = _payload(75)
    wide = _payload(75)
    wide["design"]["min_trades"] = 3
    with pytest.raises(SystemExit):
        report_module.assert_same_design(base, wide, allow_mixed=False)


def test_mixed_slow_candidates_is_rejected(report_module) -> None:
    """慢线网格不同也不可比（探针 v2 变体就是只改这一项）。"""
    base = _payload(75)
    wide = _payload(75)
    wide["design"]["ma_slow_candidates"] = [15, 25, 40]
    with pytest.raises(SystemExit):
        report_module.assert_same_design(base, wide, allow_mixed=False)


def test_allow_mixed_returns_diffs(report_module) -> None:
    """显式放行时不能静默通过：必须把差异项交回去，供报表标注「不可比」。"""
    diffs = report_module.assert_same_design(
        _payload(50, version=None), _payload(75), allow_mixed=True
    )
    assert diffs, "放行时必须返回差异清单，否则报表无法标注不可比"
    joined = " ".join(diffs)
    assert "min_oos_periods" in joined
    assert "design_version" in joined


def test_legacy_payload_without_design_is_compatible(report_module) -> None:
    """远古 JSON 没有 design 段 → 双方各项为 None → 视为同口径（向后兼容）。

    若不这样处理，2026-10-17 之前的全部历史结果都无法互相比较，
    而它们其实是同一个口径（OOS50），只是当时还没落盘 design。
    """
    legacy = {"results": []}
    assert report_module.assert_same_design(legacy, {"results": []}, allow_mixed=False) == []
    # 但「无标注」与「有标注」不可比：前者可能是任何口径
    with pytest.raises(SystemExit):
        report_module.assert_same_design(legacy, _payload(75), allow_mixed=False)


def test_signature_only_tracks_comparability_keys(report_module) -> None:
    """指纹只收影响可比性的键 —— 把 run_at 之类加进来会导致永远「不可比」。"""
    sig = report_module.design_signature(_payload(75))
    assert set(sig) == set(report_module._COMPARABLE_DESIGN_KEYS)
    assert "run_at" not in sig and "request_range" not in sig


# ---------------------------------------------------------------- 报表标注


def _empty_report(**over) -> dict:
    base = {
        "gate": {"n_requested": 0, "n_fetched": 0, "n_usable": 0, "usable_rate": 0.0,
                 "skip_reasons": {}, "universe_bias": None},
        "strategy": {},
        "compare": {},
        "fee_sensitivity": {},
        "caveats": [],
        "design": {"base": {}, "wide": {}},
        "design_comparable": True,
        "design_diffs": [],
    }
    base.update(over)
    return base


def test_markdown_states_version_when_comparable(report_module) -> None:
    """同口径时，报表第一行就要写明口径版本与 OOS 长度。"""
    md = report_module.to_markdown(
        _empty_report(
            design={"base": report_module.design_signature(_payload(75)),
                    "wide": report_module.design_signature(_payload(75))}
        )
    )
    assert "oos75-v2" in md
    assert "OOS 75" in md
    assert "对照有效" in md


def test_markdown_warns_when_mixed(report_module) -> None:
    """跨口径放行时，警告必须出现在正文最前面，不能埋在文末 caveats 里。"""
    md = report_module.to_markdown(
        _empty_report(
            design_comparable=False,
            design_diffs=["min_oos_periods: base=50 vs wide=75"],
        )
    )
    head = md[:400]
    assert "不可比" in head, "跨口径警告没有出现在报表开头"
    assert "min_oos_periods" in head


# ---------------------------------------------------------------- 守卫不越权


def test_guard_does_not_read_results_only_design(report_module) -> None:
    """守卫只能看 design，不许偷看 results —— 否则等于用收益做判断（R11）。

    用「results 差异巨大但 design 相同」构造：必须放行。
    """
    rich = _payload(75)
    rich["results"] = [{"strategy": "ma_cross", "median_ret": -0.9}]
    poor = _payload(75)
    poor["results"] = [{"strategy": "ma_cross", "median_ret": 0.9}]
    assert report_module.assert_same_design(rich, poor, allow_mixed=False) == []


def test_script_is_importable_and_has_main(report_module) -> None:
    """脚本本身可加载且暴露 main（防止改名后测试静默失效）。"""
    assert callable(report_module.main)
    assert json.dumps(_payload(75))  # payload 可序列化，供其他测试复用
