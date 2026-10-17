"""Step 8.5' ensemble 评价结果导出（JSON / CSV）的测试。

覆盖：JSON / CSV 导出、结构完整性、roundtrip、notes 诚实性标注保留、
格式分发、未知格式报错。全部用确定性合成数据（非真实行情，R3）。
"""

from __future__ import annotations

import csv
import json
import random

import pandas as pd

from deepquant.analysis import ensemble_eval
from deepquant.analysis.ensemble_eval import evaluate_ensemble
from deepquant.analysis.ensemble_report import (
    export_ensemble_report,
    export_ensemble_report_csv,
    export_ensemble_report_json,
)


def _synth(days: int = 30, seed: int = 1):
    """生成确定性的合成信号与收盘价（仅用于测试，不代表真实行情，R3）。"""
    rng = random.Random(seed)
    dates = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=days))

    def _sig() -> pd.Series:
        vals: list[int] = []
        for _ in range(days):
            r = rng.random()
            vals.append(1 if r < 0.2 else (-1 if r < 0.4 else 0))
        return pd.Series(vals, index=dates, dtype="int8")

    signals = {"ma": _sig(), "mr": _sig()}

    r2 = random.Random(seed + 7)
    price = 100.0
    closes = []
    for _ in range(days):
        price *= 1.0 + r2.uniform(-0.02, 0.02)
        closes.append(price)
    close = pd.Series(closes, index=dates, dtype="float64")
    return signals, close


def _report():
    signals, close = _synth()
    return evaluate_ensemble(signals, close, horizon=5)


# ---------------------------------------------------------------- JSON


def test_export_json_writes_and_parses(tmp_path):
    rep = _report()
    out = tmp_path / "report.json"
    returned = export_ensemble_report_json(rep, out)

    assert returned == out
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) >= {"method", "horizon", "ensemble", "per_source", "notes"}
    assert data["method"] == rep.method
    assert set(data["per_source"]) == set(rep.per_source)


def test_export_json_roundtrip_equals_to_dict(tmp_path):
    rep = _report()
    out = tmp_path / "report.json"
    export_ensemble_report_json(rep, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == rep.to_dict()


def test_export_json_preserves_honesty_notes(tmp_path):
    rep = _report()
    out = tmp_path / "report.json"
    export_ensemble_report_json(rep, out)
    notes = json.loads(out.read_text(encoding="utf-8"))["notes"]
    joined = " ".join(notes)
    # 诚实性声明不得被导出环节抹掉（R2 / R11）
    assert "R2" in joined and "R11" in joined
    assert "参数选择" in joined


# ---------------------------------------------------------------- CSV


def test_export_csv_row_count_and_columns(tmp_path):
    rep = _report()
    out = tmp_path / "report.csv"
    export_ensemble_report_csv(rep, out)

    with out.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    # 1 行 ensemble + 各源行
    assert len(rows) == 1 + rep.n_sources
    expected_cols = set(rep.ensemble.to_dict().keys())
    assert expected_cols.issubset(set(reader.fieldnames or []))


def test_export_csv_first_row_is_ensemble(tmp_path):
    rep = _report()
    out = tmp_path / "report.csv"
    export_ensemble_report_csv(rep, out)

    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert rows[0]["source"] == rep.ensemble.source


def test_export_csv_notes_is_json_list(tmp_path):
    rep = _report()
    out = tmp_path / "report.csv"
    export_ensemble_report_csv(rep, out)

    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    notes = json.loads(rows[0]["notes"])
    assert isinstance(notes, list)
    assert any("R2" in n or "R11" in n for n in notes)


def test_csv_numeric_columns_roundtrip(tmp_path):
    rep = _report()
    out = tmp_path / "report.csv"
    export_ensemble_report_csv(rep, out)

    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    # ensemble 行的覆盖度应等于报告的 ensemble_coverage（标量往返一致）
    assert float(rows[0]["density"]) == rep.ensemble.density


# ---------------------------------------------------------------- dispatch


def test_dispatch_json_and_csv(tmp_path):
    rep = _report()
    j = export_ensemble_report(rep, tmp_path / "r.json", fmt="json")
    c = export_ensemble_report(rep, tmp_path / "r.csv", fmt="csv")
    assert j.exists() and c.exists()


def test_unknown_format_raises(tmp_path):
    rep = _report()
    try:
        export_ensemble_report(rep, tmp_path / "r.xls", fmt="xls")
    except ValueError as exc:
        assert "xls" in str(exc)
    else:
        raise AssertionError("未知格式应当报错")


def test_module_is_pure_addition_no_selection_entrypoints():
    """守卫：导出模块不得暴露任何择优 / 搜索 / 优化入口（R2 / R11）。"""
    import deepquant.analysis.ensemble_report as er

    forbidden = ("select", "best", "optimi", "optima", "tune", "search", "grid", "fit")
    public = [n for n in dir(er) if not n.startswith("_")]
    for name in public:
        lowered = name.lower()
        assert not any(w in lowered for w in forbidden), (
            f"导出模块暴露了疑似择优选参入口：{name!r}（违反 R2 / R11）"
        )
