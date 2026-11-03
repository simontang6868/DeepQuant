# -*- coding: utf-8 -*-
"""`scripts/verify_qfq_rebuild.py` 的行为测试。

重点不是「正常路径能过」，而是**每一类坏数据都被单独拦下**。
本项目铁律「没检出 ≠ 没问题」在这里的具体形态是：
  若比对只覆盖 `close`，那么 volume（科创板「股 vs 手」的唯一暴露点）的错误会 100% 通过。
  所以下面专门有一条 `test_volume_only_difference_is_caught`。
"""

from __future__ import annotations

import csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import verify_qfq_rebuild as vq  # noqa: E402


HEADER = ["date", "open", "close", "high", "low", "volume"]


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow(r)


def _rows(n=5, base=10.0, vol=1000.0, start_day=1):
    return [[f"2026-10-{start_day + i:02d}", base + i, base + i + 0.5,
             base + i + 1, base + i - 0.5, vol] for i in range(n)]


@pytest.fixture()
def dirs(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    return old, new


def test_identical_is_bitwise(dirs):
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows())
    _write(new / "000001.SZ.qfq.csv", _rows())
    res = vq.build(str(old), str(new))
    assert res["verdict"] == "PASS_BITWISE"
    assert res["worst_overall"] == 0.0
    assert res["violations"] == []


def test_new_days_appended_is_still_bitwise(dirs):
    """新增交易日不影响重叠区间的逐位一致。"""
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows(5))
    _write(new / "000001.SZ.qfq.csv", _rows(8))
    res = vq.build(str(old), str(new))
    assert res["verdict"] == "PASS_BITWISE"
    assert res["rows"][0]["n_only_new"] == 3
    assert res["distinct_new_days"] == {3: 1}


def test_one_ulp_of_rounding_is_storage_not_fail(dirs):
    """差 1 个末位（1e-6）算写盘舍入 —— 两个各自舍入的数最大就是差这么多。"""
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows())
    shifted = _rows()
    shifted[0][2] = float(shifted[0][2]) + vq.STORE_ULP
    _write(new / "000001.SZ.qfq.csv", shifted)
    res = vq.build(str(old), str(new))
    assert res["verdict"] == "PASS_WITH_ROUNDING"
    assert res["rows"][0]["status"] == vq.STORAGE


def test_half_ulp_is_inside_tolerance(dirs):
    """半末位当然在内。这条是为了钉住边界：0.5e-6 在 float64 下略大于 5e-07，
    若把界写成 STORE_HALF 本身就会误判 FAIL（第一版正是如此）。"""
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows())
    shifted = _rows()
    shifted[0][2] = float(shifted[0][2]) + 0.5e-6
    _write(new / "000001.SZ.qfq.csv", shifted)
    res = vq.build(str(old), str(new))
    assert res["rows"][0]["status"] == vq.STORAGE


def test_beyond_storage_precision_is_fail(dirs):
    """差 1e-5（> 半末位 20 倍）⇒ 口径变了，必须阻断。"""
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows())
    shifted = _rows()
    shifted[0][2] = float(shifted[0][2]) + 1e-5
    _write(new / "000001.SZ.qfq.csv", shifted)
    res = vq.build(str(old), str(new))
    assert res["verdict"] == "BLOCKED_REBUILD_MISMATCH"
    assert res["rows"][0]["status"] == vq.FAIL
    assert any(v["status"] == vq.FAIL for v in res["violations"])


def test_lost_days_outranks_value_match(dirs):
    """旧有日期在新序列里消失 —— 即使重叠部分逐位相同也必须拦。

    这是最危险的静默失败：只比交集的话，砍掉一半历史仍然 PASS。
    """
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows(10))
    _write(new / "000001.SZ.qfq.csv", _rows(10)[3:])   # 删掉前 3 天，其余不变
    res = vq.build(str(old), str(new))
    assert res["rows"][0]["status"] == "LOST_DAYS"
    assert res["rows"][0]["n_only_old"] == 3
    assert res["verdict"] == "BLOCKED_REBUILD_MISMATCH"
    assert res["worst_overall"] == 0.0   # 关键：值全对，但判定仍不通过


def test_missing_new_file_is_caught(dirs):
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows())
    _write(old / "600000.SH.qfq.csv", _rows())
    _write(new / "000001.SZ.qfq.csv", _rows())
    res = vq.build(str(old), str(new))
    st = {r["code"]: r["status"] for r in res["rows"]}
    assert st["600000.SH"] == "NEW_MISSING"
    assert res["verdict"] == "BLOCKED_REBUILD_MISMATCH"


def test_extra_symbols_blocks_and_lists(dirs):
    """新目录多出标的 ⇒ 属池子变更，不能混进 PASS。"""
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows())
    _write(new / "000001.SZ.qfq.csv", _rows())
    _write(new / "688001.SH.qfq.csv", _rows())
    res = vq.build(str(old), str(new))
    assert res["n_new_symbols"] == 2 and res["n_old_symbols"] == 1
    viol = [v for v in res["violations"] if v["status"] == "EXTRA_SYMBOLS"]
    assert len(viol) == 1
    assert "688001.SH" in viol[0]["detail"]["sample"]
    assert res["verdict"] == "BLOCKED_REBUILD_MISMATCH"


def test_volume_only_difference_is_caught(dirs):
    """**只差 volume**：价格全对。

    这条针对的正是「只比 close/high/low」的实现 —— 它会 100% 通过，
    而 volume 单位混用（科创板股 vs 手）恰恰只体现在这一列。
    """
    old, new = dirs
    _write(old / "688001.SH.qfq.csv", _rows(vol=6896485.0))
    _write(new / "688001.SH.qfq.csv", _rows(vol=68964.85))   # 差 100 倍
    res = vq.build(str(old), str(new))
    assert res["verdict"] == "BLOCKED_REBUILD_MISMATCH"
    r = res["rows"][0]
    assert r["status"] == vq.FAIL
    assert r["max_abs_diff"]["volume"] > 0
    for c in ("open", "close", "high", "low"):
        assert r["max_abs_diff"][c] == 0.0


def test_bad_field_value_is_caught(dirs):
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows())
    rows = _rows()
    rows[1][2] = "NA"
    _write(new / "000001.SZ.qfq.csv", rows)
    res = vq.build(str(old), str(new))
    assert res["rows"][0]["status"] == "BAD_FIELD"
    assert res["verdict"] == "BLOCKED_REBUILD_MISMATCH"


def _report(tmp_path, code, dates):
    """造一份最小重建报告，只含 events（REANCHORED 的佐证来源）。"""
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"results": [
        {"code": code, "events": [{"ex_date": d} for d in dates]}]}, ensure_ascii=False),
        encoding="utf-8")
    return str(p)


def test_uniform_rescale_with_new_event_is_reanchored(dirs, tmp_path):
    """整体等比缩放 + 新增区间确有除权事件 ⇒ REANCHORED（锚点移动，不是口径错）。

    这正是 2026-10-23 那批三季报分红的真实情形：旧序列止于 10-16，
    新序列把 10-23 的除权纳入后，全部历史同乘一个因子。
    """
    old, new = dirs
    _write(old / "000099.SZ.qfq.csv", _rows(6))
    factor = 0.995
    scaled = [[r[0], *(float(x) * factor for x in r[1:])] for r in _rows(6)]
    _write(new / "000099.SZ.qfq.csv", scaled)
    rep = _report(tmp_path, "000099.SZ", ["2026-10-06"])   # 落在旧序列末日之后
    res = vq.build(str(old), str(new), report_path=rep)
    r = res["rows"][0]
    assert r["status"] == vq.REANCHORED
    assert r["ratio_spread"] <= vq.RATIO_SPREAD_MAX
    assert r["new_segment_has_event"] is True
    assert res["verdict"] == "PASS_WITH_REANCHOR"


def test_uniform_rescale_without_event_stays_fail(dirs, tmp_path):
    """同样的缩放，但**没有**除权事件佐证 ⇒ 仍是 FAIL。

    这条防的是「只要比例一致就放过」：无理由的整体缩放可能掩盖单位换算错误。
    """
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows(6))
    scaled = [[r[0], *(float(x) * 0.995 for x in r[1:])] for r in _rows(6)]
    _write(new / "000001.SZ.qfq.csv", scaled)
    no_rep = vq.build(str(old), str(new))
    assert no_rep["rows"][0]["status"] == vq.FAIL
    assert no_rep["verdict"] == "BLOCKED_REBUILD_MISMATCH"

    rep = _report(tmp_path, "000001.SZ", ["2026-10-06"])
    with_rep = vq.build(str(old), str(new), report_path=rep)
    assert with_rep["rows"][0]["status"] == vq.REANCHORED


def test_event_before_old_last_does_not_justify_reanchor(dirs, tmp_path):
    """事件若落在旧序列区间**之内**，不能用来解释末端锚点移动。"""
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows(6))          # 末日 2026-10-06
    scaled = [[r[0], *(float(x) * 0.99 for x in r[1:])] for r in _rows(6)]
    _write(new / "000001.SZ.qfq.csv", scaled)
    rep = _report(tmp_path, "000001.SZ", ["2026-10-01"])  # 早于末日 ⇒ 不算新增
    res = vq.build(str(old), str(new), report_path=rep)
    assert res["rows"][0]["status"] == vq.FAIL


def test_nonuniform_rescale_stays_fail(dirs, tmp_path):
    """比值不恒定（结构性差异）⇒ 即便有事件佐证也 FAIL。"""
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows(6))
    scaled = _rows(6)
    for i, r in enumerate(scaled):
        scaled[i] = [r[0], *(float(x) * (1.0 - 0.01 * (i + 1)) for x in r[1:])]
    _write(new / "000001.SZ.qfq.csv", scaled)
    rep = _report(tmp_path, "000001.SZ", ["2026-10-06"])
    res = vq.build(str(old), str(new), report_path=rep)
    assert res["rows"][0]["status"] == vq.FAIL
    assert res["rows"][0]["ratio_spread"] > vq.RATIO_SPREAD_MAX


def test_allow_new_days_guard(dirs):
    """单只新增天数超限 ⇒ 报违规（防「增量更新只覆盖了一部分股票」）。"""
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows(5))
    _write(new / "000001.SZ.qfq.csv", _rows(20))
    ok = vq.build(str(old), str(new), allow_new_days=20)
    assert ok["verdict"] == "PASS_BITWISE"
    bad = vq.build(str(old), str(new), allow_new_days=5)
    assert bad["verdict"] == "BLOCKED_REBUILD_MISMATCH"
    assert any(v["status"] == "TOO_MANY_NEW_DAYS" for v in bad["violations"])


def test_missing_dirs_raise(tmp_path):
    with pytest.raises(FileNotFoundError):
        vq.build(str(tmp_path / "nope"), str(tmp_path / "nope2"))


def test_main_writes_json_and_exit_code(dirs, tmp_path, capsys):
    old, new = dirs
    _write(old / "000001.SZ.qfq.csv", _rows())
    _write(new / "000001.SZ.qfq.csv", _rows())
    out = tmp_path / "v.json"
    assert vq.main(["--old", str(old), "--new", str(new), "--json", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["verdict"] == "PASS_BITWISE"

    _write(new / "000001.SZ.qfq.csv", _rows(3))     # 制造丢失
    assert vq.main(["--old", str(old), "--new", str(new), "--json", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["verdict"] == "BLOCKED_REBUILD_MISMATCH"
