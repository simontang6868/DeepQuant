"""L3t 分辨率登记的单元测试。

重点钉住四件事：
1. **单位换算**：小数 ×100、pp 原样、IC 不换算；
2. **量级自检能抓住单位错**：把 pp 当小数（或反之）必须被 flag 出来，不能静默通过；
3. **族中位只采用合格条目**：n<50 与单位可疑的条目不得进中位；
4. **「低于同族最好分辨率」的判定**用的是同族最好 MDE，不是自己的 MDE。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from deepquant.gates import resolution as rs


def _csv(tmp_path, rows, name="monthly.csv"):
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


# ---------------------------------------------------------------- 单位换算


def test_to_pp_converts_decimal_and_keeps_pp():
    assert rs._to_pp([0.01, 0.02], "decimal") == pytest.approx([1.0, 2.0])
    assert rs._to_pp([1.0, 2.0], "pp") == pytest.approx([1.0, 2.0])
    assert rs._to_pp([0.03, 0.04], "ic") == pytest.approx([0.03, 0.04])


def test_to_pp_drops_nan():
    assert rs._to_pp([0.01, float("nan"), 0.03], "decimal") == pytest.approx([1.0, 3.0])


def test_spec_rejects_unknown_family_and_unit():
    with pytest.raises(rs.ResolutionError):
        rs.SeriesSpec(key="x", family="Z", label="x", path="p", column="c", unit="pp")
    with pytest.raises(rs.ResolutionError):
        rs.SeriesSpec(key="x", family="A_cross_spread", label="x", path="p", column="c", unit="元")


# ---------------------------------------------------------------- 读序列


def test_load_series_applies_filter_and_keeps_source_unit(tmp_path):
    path = _csv(
        tmp_path,
        [
            {"cell": "A", "d": 0.01, "k": 100},
            {"cell": "B", "d": 0.02, "k": 200},
            {"cell": "A", "d": 0.03, "k": 100},
        ]
        + [{"cell": "A", "d": 0.01, "k": 100} for _ in range(10)],
    )
    spec = rs.SeriesSpec(
        key="t", family="A_cross_spread", label="t", path=str(path), column="d",
        unit="decimal", filters={"cell": "A"}, k_column="k",
    )
    values, meta = rs.load_series(spec, tmp_path)
    assert meta["n"] == 12  # 只取 cell=A
    assert meta["source_unit"] == "decimal"
    assert meta["k_median"] == pytest.approx(100)
    assert max(values) == pytest.approx(3.0)  # 已换算成 pp


def test_load_series_rejects_empty_filter(tmp_path):
    path = _csv(tmp_path, [{"cell": "A", "d": 0.01} for _ in range(12)])
    spec = rs.SeriesSpec(
        key="t", family="A_cross_spread", label="t", path=str(path), column="d",
        unit="decimal", filters={"cell": "Z"},
    )
    with pytest.raises(rs.ResolutionError):
        rs.load_series(spec, tmp_path)


def test_load_series_reads_json_items(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps({"result": {"folds": [{"test_return": 0.01 * i} for i in range(1, 15)]}}),
        encoding="utf-8",
    )
    spec = rs.SeriesSpec(
        key="t", family="E_fold", label="t", path=str(path), column="test_return",
        unit="decimal", source="json", item_path="result.folds",
    )
    values, meta = rs.load_series(spec, tmp_path)
    assert meta["n"] == 14
    assert values[0] == pytest.approx(1.0)


# ---------------------------------------------------------------- 汇总与自检


def test_summarise_uses_project_estimator():
    values = [0.5, -0.3, 0.2, 0.1, -0.05] * 12
    out = rs.summarise(values, unit="pp")
    assert out["n"] == 60
    assert out["mean"] == pytest.approx(sum(values) / len(values))
    assert out["sd"] == pytest.approx(pd.Series(values).std(ddof=1))
    assert out["mde"] == pytest.approx(2.8016 * out["se_nw"], rel=1e-9)


def test_flag_catches_wrong_unit_magnitude():
    """把 pp 当小数写（×100 后 sd 变 100 倍）必须被抓住，这是整张表的单位总闸。"""
    wrong = {"unit": "pp", "family": "A_cross_spread", "n": 200, "sd": 250.0, "mean": 0.5, "t_nw3": 0.5}
    assert any("疑似单位错" in f for f in rs.flag_row(wrong))

    right = {"unit": "pp", "family": "A_cross_spread", "n": 200, "sd": 2.5, "mean": 0.5, "t_nw3": 0.5}
    assert rs.flag_row(right) == []


def test_flag_uses_separate_bounds_for_fold_family():
    """折级收益是 %/折，量级本来就大，不能被当成单位错。"""
    row = {"unit": "pp", "family": "E_fold", "n": 28, "sd": 12.0, "mean": 5.0, "t_nw3": 1.5}
    flags = rs.flag_row(row)
    assert not any("疑似单位错" in f for f in flags)
    assert any("n=28 < 50" in f for f in flags)


def test_family_rollup_excludes_small_or_suspect():
    rows = [
        {"family": "A_cross_spread", "unit": "pp", "n": 200, "mde": 0.5, "unit_suspect": False},
        {"family": "A_cross_spread", "unit": "pp", "n": 200, "mde": 0.7, "unit_suspect": False},
        {"family": "A_cross_spread", "unit": "pp", "n": 20, "mde": 9.9, "unit_suspect": False},
        {"family": "A_cross_spread", "unit": "pp", "n": 200, "mde": 99.0, "unit_suspect": True},
    ]
    rollup = rs.family_rollup(rows)
    item = rollup["A_cross_spread"]
    assert item["n_series"] == 4
    assert item["n_usable"] == 2
    assert item["mde_median"] == pytest.approx(0.6)
    assert item["mde_max"] == pytest.approx(0.7)


def test_below_best_uses_family_best_not_own_mde():
    rows = [
        {"key": "big", "family": "A_cross_spread", "unit": "pp", "n": 200,
         "mde": 0.1, "mean": 0.5, "unit_suspect": False},
        {"key": "small", "family": "A_cross_spread", "unit": "pp", "n": 200,
         "mde": 0.8, "mean": 0.05, "unit_suspect": False},
    ]
    rollup = rs.family_rollup(rows)
    below = rs.references_below_best_resolution(rows, rollup)
    assert [item["key"] for item in below] == ["small"]
    assert below[0]["family_best_mde_pp"] == pytest.approx(0.1)


def test_check_prereg_sha_rejects_tampering(tmp_path):
    path = tmp_path / "prereg.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(rs.ResolutionError):
        rs.check_prereg_sha(path, "deadbeef")
