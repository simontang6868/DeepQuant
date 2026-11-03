"""R3-5：d4 档内可买数熔断检查的单元测试。

钉死四条语义（每条对应一种会误判的写法）：
1. 连续 6 条**有值**记录全部 <100 → 触发；差一条 → 不触发；
2. LEGACY_NO_D4 记录（无 ok_buy_n_d4 字段）**不得**当作 0 参与判定——
   把缺失当 0 会让"旧台账+当月低值"直接触发，那是假熔断；
3. 当月记录（current_*）与台账记录合并、按日期排序后取尾部；
4. 值不足 6 条 → 不触发（样本不足 ≠ 无风险）。

运行：
    PYTHONPATH="research/stock_pick_2026-10-25/scripts/sp5" python -m pytest \\
        research/stock_pick_2026-10-25/scripts/sp5/tests/test_d4_stop_condition.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
SP5 = _HERE.parent.parent          # sp5/（tests/ 的上一级）
_spec = importlib.util.spec_from_file_location("p13_forward", SP5 / "p13_forward.py")
p13 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p13)


def _write_ledger(tmp: Path, date: str, ok_buy_n_d4):
    """ok_buy_n_d4=None 模拟 LEGACY_NO_D4（旧台账没有该字段）。"""
    rec = {"date": date, "mode": "FORWARD", "ok": True}
    if ok_buy_n_d4 is not None:
        rec["ok_buy_n_d4"] = ok_buy_n_d4
    (tmp / f"{date}.json").write_text(
        json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def test_triggers_on_six_consecutive_low(tmp_path):
    for d, v in [("20270129", 80), ("20270226", 90), ("20270331", 95),
                 ("20270430", 99), ("20270531", 60)]:
        _write_ledger(tmp_path, d, v)
    r = p13.check_d4_stop_condition(tmp_path, current_me="20270630",
                                    current_ok_buy_n_d4=88)
    assert r["triggered"] is True
    assert r["n_series"] == 6 and len(r["series"]) == 6
    assert r["series"][-1]["date"] == "20270630"


def test_no_trigger_when_one_month_high(tmp_path):
    for d, v in [("20270129", 80), ("20270226", 90), ("20270331", 95),
                 ("20270430", 99), ("20270531", 120)]:
        _write_ledger(tmp_path, d, v)
    r = p13.check_d4_stop_condition(tmp_path, current_me="20270630",
                                    current_ok_buy_n_d4=88)
    assert r["triggered"] is False


def test_legacy_records_without_field_are_ignored(tmp_path):
    """旧台账无字段 → 不得当 0 参与判定：4 条有值台账 + 当月 = 5 条 < 6，
    若把 legacy 当 0 就会凑满 6 条而误触发。正确行为：n_series=5，不触发。"""
    _write_ledger(tmp_path, "20260930", None)      # LEGACY_NO_D4（无字段）
    for d, v in [("20270129", 10), ("20270226", 10), ("20270331", 10),
                 ("20270430", 10)]:
        _write_ledger(tmp_path, d, v)
    r = p13.check_d4_stop_condition(tmp_path, current_me="20270531",
                                    current_ok_buy_n_d4=10)
    assert r["triggered"] is False and r["n_series"] == 5


def test_insufficient_series_does_not_trigger(tmp_path):
    _write_ledger(tmp_path, "20270129", 10)
    r = p13.check_d4_stop_condition(tmp_path, current_me="20270227",
                                    current_ok_buy_n_d4=10)
    assert r["triggered"] is False and r["n_series"] == 2


def test_current_record_deduped_against_ledger(tmp_path):
    """当月已写台账后重跑：current 与台账同日不得重复计数。"""
    _write_ledger(tmp_path, "20270630", 50)
    r = p13.check_d4_stop_condition(tmp_path, current_me="20270630",
                                    current_ok_buy_n_d4=50)
    assert r["n_series"] == 1


def test_invalid_args_raise():
    import pytest
    with pytest.raises(ValueError):
        p13.check_d4_stop_condition(".", months=0)
    with pytest.raises(ValueError):
        p13.check_d4_stop_condition(".", threshold=-1)


# ---------------------------------------------------------------------------
# R4-4：黄色预警（连续 3 个月 <200）；停止条件不变
# ---------------------------------------------------------------------------
def test_warn_triggers_on_three_consecutive_below_200(tmp_path):
    for d, v in [("20270129", 190), ("20270226", 150), ("20270331", 180)]:
        _write_ledger(tmp_path, d, v)
    r = p13.check_d4_stop_condition(tmp_path, current_me="20270430",
                                    current_ok_buy_n_d4=170)
    assert r["warn"] is True and r["triggered"] is False
    assert r["warn_threshold"] == 200 and r["warn_months"] == 3


def test_warn_not_triggered_when_insufficient_sample(tmp_path):
    """有值记录不足 warn_months 条 → 不触发（样本不足 ≠ 无风险）。"""
    _write_ledger(tmp_path, "20270331", 150)
    r = p13.check_d4_stop_condition(tmp_path, current_me="20270430",
                                    current_ok_buy_n_d4=170)
    assert r["warn"] is False and r["triggered"] is False


def test_warn_suppressed_when_stop_triggered(tmp_path):
    for d, v in [("20270129", 50), ("20270226", 60), ("20270331", 70),
                 ("20270430", 80), ("20270531", 90)]:
        _write_ledger(tmp_path, d, v)
    r = p13.check_d4_stop_condition(tmp_path, current_me="20270630",
                                    current_ok_buy_n_d4=95)
    assert r["triggered"] is True and r["warn"] is False


def test_warn_not_triggered_above_200(tmp_path):
    for d, v in [("20270129", 250), ("20270226", 210), ("20270331", 300)]:
        _write_ledger(tmp_path, d, v)
    r = p13.check_d4_stop_condition(tmp_path, current_me="20270430",
                                    current_ok_buy_n_d4=220)
    assert r["warn"] is False and r["triggered"] is False
