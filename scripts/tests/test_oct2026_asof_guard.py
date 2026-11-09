"""PA-OCT2026-1 §6 as-of 守卫的 pytest 闸门。

独立构造小型行情目录；期望值来自显式构造，不调用被测函数推导期望。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
GUARD_PY = REPO / "scripts" / "oct2026_asof_guard.py"
_spec = importlib.util.spec_from_file_location("asof_guard_t", GUARD_PY)
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)

CODES = g.CODES
DAYS = ("20261008", "20261009", "20261012", "20261013", "20261014",
        "20261015", "20261016", "20261019", "20261020", "20261021",
        "20261022", "20261023", "20261026", "20261027", "20261028",
        "20261029", "20261030")
HEADER = "date,open,close,high,low,volume\n"


def _write(p: pathlib.Path, text: str) -> None:
    p.write_bytes((text if text.endswith("\n") else text + "\n").encode("utf-8"))


@pytest.fixture
def clean_root(tmp_path):
    d = tmp_path / "qfq"
    d.mkdir()
    for i, c in enumerate(CODES):
        lines = [HEADER.rstrip("\n")]
        for j, day in enumerate(DAYS):
            h = day[:4] + "-" + day[4:6] + "-" + day[6:]
            close = 10.0 + i + 0.01 * j
            lines.append("%s,%.2f,%.2f,%.2f,%.2f,1000" % (h, close, close, 999.0, 0.01, ))
        _write(d / (c + ".SH.qfq.csv"), "\n".join(lines))
    return d


def test_clean_input_is_ok(clean_root):
    r = g.evaluate(clean_root, CODES, DAYS, g.ASOF)
    assert r["ok"] is True
    assert r["n_files"] == 5
    assert r["max_date_seen"] == "20261030"
    assert r["future_rows"] == 0


def test_future_row_is_rejected(clean_root):
    p = next(clean_root.glob("603193.*"))
    _write(p, p.read_text(encoding="utf-8").rstrip("\n")
           + "\n2026-11-09,1,1,1,1,1\n")
    r = g.evaluate(clean_root, CODES, DAYS, g.ASOF)
    assert r["ok"] is False
    assert any(x.startswith("FAIL:FUTURE_ROW") for x in r["reasons"])
    assert r["future_rows"] == 1


def test_missing_file_is_not_silently_skipped(clean_root):
    next(clean_root.glob("301336.*")).unlink()
    r = g.evaluate(clean_root, CODES, DAYS, g.ASOF)
    assert r["ok"] is False
    assert "FAIL:MISSING_FILE:301336" in r["reasons"]


def test_missing_middle_trade_day_is_rejected(clean_root):
    p = next(clean_root.glob("000597.*"))
    lines = [x for x in p.read_text(encoding="utf-8").splitlines()
             if not x.startswith("2026-10-20,")]
    _write(p, "\n".join(lines))
    r = g.evaluate(clean_root, CODES, DAYS, g.ASOF)
    assert r["ok"] is False
    assert any(x.startswith("FAIL:MISSING_TRADE_DAY") for x in r["reasons"])


def test_nonpositive_close_is_rejected(clean_root):
    p = next(clean_root.glob("002692.*"))
    out = []
    for x in p.read_text(encoding="utf-8").splitlines():
        if x.startswith("2026-10-15,"):
            z = x.split(",")
            z[2] = "0"
            x = ",".join(z)
        out.append(x)
    _write(p, "\n".join(out))
    r = g.evaluate(clean_root, CODES, DAYS, g.ASOF)
    assert r["ok"] is False
    assert any(x.startswith("FAIL:NONPOSITIVE_CLOSE") for x in r["reasons"])


@pytest.mark.parametrize("codes,days", [((), DAYS), (CODES, ()), ((), ())])
def test_empty_input_fails_closed(clean_root, codes, days):
    """all({}.values()) 恒真坑：空集必须 FAIL 关闭。"""
    r = g.evaluate(clean_root, codes, days, g.ASOF)
    assert r["ok"] is False
    assert "FAIL:EMPTY_INPUT" in r["reasons"]


def test_normalize_date_mixed_format():
    assert g.normalize_date("2026-11-09") == "20261109"
    assert g.normalize_date("20261030") == "20261030"
    assert g.normalize_date("2026-11-09") > "20261030"   # 归一化后未来日期确实更大
    assert "2026-11-09" < "20261030"                      # 未归一化则是恒真的假安全
    with pytest.raises(ValueError):
        g.normalize_date("not-a-date")


def test_close_is_taken_by_column_name_not_position(clean_root):
    """qfq 列序 date,open,close,high,low,volume：按位置取会拿到 high=999。"""
    r = g.evaluate(clean_root, CODES, DAYS, g.ASOF)
    assert r["ok"] is True
    for c in CODES:
        v = r["closes"][c]["20261008"]
        assert 0 < v < 100, (c, v)


def test_trade_days_from_independent_calendar():
    days = g.trade_days(REPO)
    assert len(days) == 17
    assert days[0] == "20261008"
    assert days[-1] == "20261030"


def test_real_data_asof_ok():
    r = g.evaluate(REPO / "data" / "qfq_all", CODES, g.trade_days(REPO), g.ASOF)
    assert r["ok"] is True
    assert r["max_date_seen"] == "20261030"
    assert r["future_rows"] == 0


def test_product_json_fields_are_sufficient(tmp_path, clean_root):
    """产物至少含判定所需字段，避免「输出为空也算过」。"""
    r = g.evaluate(clean_root, CODES, DAYS, g.ASOF)
    payload = {k: v for k, v in r.items() if k != "closes"}
    payload["verdict"] = "ASOF_OK" if r["ok"] else "ASOF_FAIL"
    p = tmp_path / "x.json"
    p.write_bytes((json.dumps(payload) + "\n").encode("utf-8"))
    d = json.loads(p.read_text(encoding="utf-8"))
    for k in ("ok", "reasons", "max_date_seen", "future_rows", "n_files", "verdict"):
        assert k in d
