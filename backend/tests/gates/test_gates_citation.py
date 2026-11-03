"""L3u 引用规范登记的单元测试。

重点钉住三件事：
1. 判定序列的重建（尤其「格间配对差」不能退化成单格水平）；
2. 判定字符串的分类必须按**原文**，`ABSORBED` 与 `CONFIRMED` 不可混；
3. 核对不通过的条目不得拿到正式分级（但要有参考分级）。
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import pandas as pd
import pytest

from deepquant.gates import citation as cit


SEMANTICS = {
    "negative_markers": [
        "NOT_DETECTED",
        "FAILURE",
        "failure",
        "ABSORBED",
        "INSUFFICIENT",
        "NEGLIGIBLE",
        "BLOCKED",
        "INCONCLUSIVE",
    ],
    "positive_markers": ["CONFIRMED", "SUCCESS", "DETECTED", "SIGNIFICANT", "EXECUTABLE"],
}

RULES = {
    "detected_true": {},
    "detected_false": {},
    "ratio_definition": "|effect| / mde",
    "power_definition": "two-sided",
}


# --------------------------------------------------------------------------
# 判定字符串
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("POOL_EFFECT_CONFIRMED", True),
        ("SUCCESS", True),
        ("MODERN_ERA_ALPHA_DETECTED", True),
        ("SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT", True),
        # 被吸收 = 不成立，这是本项目踩过的语义反转陷阱
        ("INCREMENTAL_ABSORBED", False),
        ("SURGE_AVOID_ABSORBED", False),
        ("NET_AFTER_EXECUTION_ABSORBED", False),
        ("FAILURE", False),
        ("failure", False),  # gate1 的原文就是小写
        ("LOW_TURNOVER_INSUFFICIENT", False),
        ("LAG_COST_NEGLIGIBLE", False),
        ("L3_BLOCKED_PREREQUISITE", False),
        ("IC_INCONCLUSIVE_POWER", False),
        # 两者都不含：不得默认成任一边
        ("T_FRAGILE", None),
        ("COST_CONVENTION_SENSITIVE", None),
        (None, None),
        ("", None),
    ],
)
def test_classify_verdict_uses_original_text(text, expected):
    assert cit.classify_verdict(text, SEMANTICS) is expected


def test_classify_verdict_negative_wins_over_positive():
    """同时含两类词时必须判「未检出」——原文是 NOT_DETECTED_AFTER_COST_CONFIRMED 这种写法。"""
    assert cit.classify_verdict("NOT_DETECTED_BUT_CONFIRMED_BY_OTHER", SEMANTICS) is False


# --------------------------------------------------------------------------
# 序列重建
# --------------------------------------------------------------------------


def _dates(n: int) -> list[str]:
    return [f"20{20 + i // 12:02d}-{i % 12 + 1:02d}-01" for i in range(n)]


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def test_rebuild_column_applies_scale(tmp_path):
    frame = pd.DataFrame({"signal_date": _dates(12), "d": [0.01] * 12})
    _write_csv(tmp_path / "m.csv", frame)
    entry = {
        "key": "k",
        "unit": "pp",
        "series": {"kind": "column", "path": "m.csv", "column": "d", "scale": 100},
    }
    out = cit.rebuild_series(entry, tmp_path)
    assert out.values == pytest.approx((1.0,) * 12)


def test_rebuild_rejects_too_few_rows(tmp_path):
    """少于 10 条必须抛错：样本太少的「分辨率」没有意义，不能静默算下去。"""
    frame = pd.DataFrame({"signal_date": _dates(3), "d": [0.01] * 3})
    _write_csv(tmp_path / "m.csv", frame)
    entry = {
        "key": "k",
        "unit": "pp",
        "series": {"kind": "column", "path": "m.csv", "column": "d", "scale": 100},
    }
    with pytest.raises(cit.CitationError, match="有效样本仅"):
        cit.rebuild_series(entry, tmp_path)


def test_rebuild_pivot_reproduces_pair_difference(tmp_path):
    """格间配对差必须由两格相减得到，不能退化成任一格的水平。"""
    rows = []
    for date in _dates(12):
        rows.append({"signal_date": date, "cell": "A", "d": 0.010})
        rows.append({"signal_date": date, "cell": "B", "d": 0.030})
    _write_csv(tmp_path / "five_cells.csv", pd.DataFrame(rows))
    entry = {
        "key": "k",
        "unit": "pp",
        "series": {
            "kind": "pivot",
            "path": "five_cells.csv",
            "index": "signal_date",
            "pivot_column": "cell",
            "value_column": "d",
            "terms": [{"key": "B", "weight": 1.0}, {"key": "A", "weight": -1.0}],
            "multiplier": 2.0,
            "scale": 100,
        },
    }
    out = cit.rebuild_series(entry, tmp_path)
    # 2*(0.030-0.010)=0.04 -> 4.0pp
    assert out.values == pytest.approx((4.0,) * 12)


def test_rebuild_terms_subtracts_two_columns(tmp_path):
    frame = pd.DataFrame(
        {
            "signal_date": _dates(12),
            "spread_v1": [0.05] * 12,
            "spread": [0.04] * 12,
        }
    )
    _write_csv(tmp_path / "cells.csv", frame)
    entry = {
        "key": "k",
        "unit": "pp",
        "series": {
            "kind": "terms",
            "path": "cells.csv",
            "index": "signal_date",
            "terms": [
                {"column": "spread_v1", "weight": 1.0},
                {"column": "spread", "weight": -1.0},
            ],
            "scale": 100,
        },
    }
    out = cit.rebuild_series(entry, tmp_path)
    assert out.values == pytest.approx((1.0,) * 12)


def test_rebuild_terms_respects_filter(tmp_path):
    frame = pd.DataFrame(
        {
            "cell": ["D"] * 12 + ["B"] * 12,
            "signal_date": _dates(12) + _dates(12),
            "spread_v1": [0.05] * 12 + [9.0] * 12,
            "spread": [0.04] * 12 + [1.0] * 12,
        }
    )
    _write_csv(tmp_path / "cells.csv", frame)
    entry = {
        "key": "k",
        "unit": "pp",
        "series": {
            "kind": "terms",
            "path": "cells.csv",
            "index": "signal_date",
            "terms": [
                {"column": "spread_v1", "weight": 1.0},
                {"column": "spread", "weight": -1.0},
            ],
            "filter": {"cell": "D"},
            "scale": 100,
        },
    }
    out = cit.rebuild_series(entry, tmp_path)
    assert out.values == pytest.approx((1.0,) * 12)


def test_rebuild_json_list_reads_nested_path(tmp_path):
    folds = [{"test_return": 0.1} for _ in range(11)] + [{"x": 1}]
    payload = {"result": {"folds": folds}}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    entry = {
        "key": "k",
        "unit": "pct_fold",
        "series": {
            "kind": "json_list",
            "path": "r.json",
            "list_path": "result.folds",
            "value": "test_return",
            "scale": 100,
        },
    }
    out = cit.rebuild_series(entry, tmp_path)
    # 缺字段的那条被跳过，不得当 0
    assert out.values == pytest.approx((10.0,) * 11)


def test_rebuild_missing_file_raises(tmp_path):
    entry = {
        "key": "k",
        "unit": "pp",
        "series": {"kind": "column", "path": "nope.csv", "column": "d", "scale": 1},
    }
    with pytest.raises(cit.CitationError):
        cit.rebuild_series(entry, tmp_path)


# --------------------------------------------------------------------------
# 统计量与核对
# --------------------------------------------------------------------------


def test_summarise_series_matches_manual_formulas():
    values = [0.5, -0.2, 0.3, 0.1, -0.4] * 12
    out = cit.summarise_series(values)
    assert out["n"] == 60
    assert out["mean"] == pytest.approx(statistics.fmean(values))
    assert out["mde"] == pytest.approx(cit.MDE_FACTOR * out["se_nw"])
    assert 0.0 <= out["power"] <= 1.0


def test_compare_row_match():
    row = {"key": "k", "mean": 1.0, "t_nw3": 2.0, "n": 100}
    stat = {"applicable": True, "mean": 1.0, "t": 2.0, "n": 100}
    out = cit.compare_row(row, stat, {"mean_abs": 1e-6, "t_abs": 1e-4})
    assert out["class"] == cit.MATCH


def test_compare_row_t_only_is_flagged_not_called_wrong_column():
    """mean 与 n 都对、只有 t 不同 ⇒ 不是「取错列」，必须能被分辨出来。"""
    row = {"key": "k", "mean": 0.708, "t_nw3": 4.6443, "n": 237}
    stat = {"applicable": True, "mean": 0.708, "t": 4.39, "n": 237}
    out = cit.compare_row(row, stat, {"mean_abs": 1e-6, "t_abs": 1e-4})
    assert out["class"] == cit.MISMATCH_MEAN
    assert out["t_only"] is True


def test_compare_row_mean_mismatch_is_not_t_only():
    row = {"key": "k", "mean": 0.014, "t_nw3": 0.09, "n": 237}
    stat = {"applicable": True, "mean": 1.939, "t": 6.37, "n": 237}
    out = cit.compare_row(row, stat, {"mean_abs": 1e-6, "t_abs": 1e-4})
    assert out["class"] == cit.MISMATCH_MEAN
    assert out["t_only"] is False


def test_compare_row_n_mismatch_marks_mean_identical():
    """分母口径不同（回归全样本 vs 分段月数）不该被当成窗口取错。"""
    row = {"key": "k", "mean": 0.1294, "t_nw3": 2.581, "n": 129}
    stat = {"applicable": True, "mean": 0.1294, "t": 2.581, "n": 237}
    out = cit.compare_row(row, stat, {"mean_abs": 1e-6, "t_abs": 1e-4})
    assert out["class"] == cit.MISMATCH_N
    assert out["mean_identical"] is True


def test_compare_row_n_mismatch_with_different_mean_is_real_error():
    row = {"key": "k", "mean": -0.398, "t_nw3": -1.69, "n": 125}
    stat = {"applicable": True, "mean": 0.484, "t": 2.12, "n": 108}
    out = cit.compare_row(row, stat, {"mean_abs": 1e-6, "t_abs": 1e-4})
    assert out["class"] == cit.MISMATCH_N
    assert out["mean_identical"] is False


def test_read_stat_requires_note_when_mean_is_none():
    entry = {"key": "k", "stat": {"mean": None, "scale": 1}}
    with pytest.raises(cit.CitationError):
        cit.read_stat({}, entry)


def test_read_stat_raises_when_path_missing():
    """读不到必须抛错，不能返回 None 让「没找到」变成「没问题」。"""
    entry = {"key": "k", "stat": {"mean": "main.mean_pp", "scale": 1}}
    with pytest.raises(cit.CitationError):
        cit.read_stat({"main": {"nope": 1}}, entry)


def test_read_stat_falls_back_to_alt_keys():
    entry = {
        "key": "k",
        "stat": {"mean": "a.b", "mean_alt_keys": ["net_pp"], "scale": 1},
    }
    out = cit.read_stat({"net_pp": 0.136}, entry)
    assert out["mean"] == pytest.approx(0.136)


# --------------------------------------------------------------------------
# 分级
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "effect, mde, expected",
    [
        (2.0, 1.0, "QUOTABLE"),
        (1.0, 1.0, "QUOTABLE_MARGINAL"),
        (0.5, 1.0, "DIRECTION_ONLY"),
        (0.49, 1.0, "NOT_QUOTABLE"),
        (-2.0, 1.0, "QUOTABLE"),  # 用绝对值，方向单独记
    ],
)
def test_grade_detected_uses_ratio(effect, mde, expected):
    out = cit.grade_quotability(
        effect=effect, mde=mde, detected=True, power=0.99, rules=RULES
    )
    assert out["tier"] == expected


def test_grade_detected_uses_absolute_effect():
    out = cit.grade_quotability(effect=-1.0, mde=1.0, detected=True, power=0.9, rules=RULES)
    assert out["ratio"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "power, expected",
    [(0.95, "NULL_CONFIRMED"), (0.6, "NULL_WEAK"), (0.2, "UNDERPOWERED")],
)
def test_grade_null_uses_power_not_ratio(power, expected):
    out = cit.grade_quotability(
        effect=0.01, mde=1.0, detected=False, power=power, rules=RULES
    )
    assert out["tier"] == expected


def test_grade_unclassified_when_verdict_unknown():
    out = cit.grade_quotability(effect=1.0, mde=1.0, detected=None, power=0.9, rules=RULES)
    assert out["tier"] == "UNCLASSIFIED"


def test_grade_not_graded_when_mde_unusable():
    out = cit.grade_quotability(
        effect=1.0, mde=float("nan"), detected=True, power=0.9, rules=RULES
    )
    assert out["tier"] == "NOT_GRADED"


# --------------------------------------------------------------------------
# 预登记校验
# --------------------------------------------------------------------------


def test_check_prereg_sha_rejects_tampering(tmp_path):
    path = tmp_path / "p.json"
    path.write_bytes(b'{"a": 1}\n')
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert cit.check_prereg_sha(path, digest) == digest

    path.write_bytes(b'{"a": 2}\n')
    with pytest.raises(cit.CitationError):
        cit.check_prereg_sha(path, digest)


def test_load_prereg_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(
        json.dumps({"entries": [{"key": "a"}, {"key": "a"}]}), encoding="utf-8"
    )
    with pytest.raises(cit.CitationError):
        cit.load_prereg(path)
