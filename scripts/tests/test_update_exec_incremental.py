# -*- coding: utf-8 -*-
"""增量更新的行为测试（Phase 1.4）。

重点不是「能追加」，而是「**拒绝**追加什么」：
  价格不符、成交量单位错、单源缺失、目标日已最新 —— 这四类都必须不写。

其中「成交量单位错」最容易被放过：把腾讯的「手」当「股」比对时，
**收盘价完全正常**，只有成交量差 100 倍。只看价格的实现会一路通过。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import update_exec_incremental as ux  # noqa: E402


HEADER = "date,open,high,low,close,volume\n"


def _mk(path: Path, last="2026-10-16", close=10.0, vol=1000.0):
    path.write_text(
        HEADER + f"2026-10-15,9.9,10.0,9.8,{close:.3f},900.000\n"
                 f"{last},9.9,10.0,9.8,{close:.3f},{vol:.3f}\n",
        encoding="utf-8", newline="")


def _ten(close=10.5, vol=1200.0):
    return {"2026-10-19": {"open": 10.0, "high": 10.6, "low": 9.9,
                           "close": close, "volume": vol}}


def _sin(close=10.5, vol=None):
    """新浪的 volume 是**股**：腾讯 1200 手 ⇒ 120000 股。"""
    return {"2026-10-19": {"open": 10.0, "high": 10.6, "low": 9.9,
                           "close": close, "volume": (vol if vol is not None else 1200.0 * 100)}}


def test_appends_when_both_sources_agree(tmp_path, monkeypatch):
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: _ten())
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: _sin())

    rec = ux.check_one("600000.SH", str(tmp_path), "2026-10-30", True)
    assert rec["status"] == "READY"
    assert rec["appended"] == 1
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[-1].startswith("2026-10-19")
    assert lines[-1].endswith("1200.000")      # 写入的仍是「手」，与既有文件同单位
    assert len(lines) == 4


def test_refuses_close_mismatch(tmp_path, monkeypatch):
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    before = p.read_bytes()
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: _ten(close=10.5))
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: _sin(close=11.9))

    rec = ux.check_one("600000.SH", str(tmp_path), "2026-10-30", True)
    assert rec["status"] == "NO_AGREEMENT"
    assert rec["appended"] == 0
    assert any("收盘价不符" in f for f in rec["flags"])
    assert p.read_bytes() == before


def test_volume_unit_is_inferred_hand(tmp_path, monkeypatch):
    """腾讯=手（×100 才等于新浪）⇒ 判定 hand 并写入。"""
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: _ten(vol=1200.0))
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: _sin(vol=120000.0))

    rec = ux.check_one("600000.SH", str(tmp_path), "2026-10-30", True)
    assert rec["status"] == "READY"
    assert rec["volume_unit"] == "hand"


def test_volume_unit_is_inferred_share_for_star_market(tmp_path, monkeypatch):
    """腾讯已经是「股」（科创板实测如此）⇒ 判定 share 并写入。

    ⚠️ 这条与上一条合起来才完整：单位**不是**全市场统一的。
    2026-11-02 全量实测：616 只科创板全部落在这一支，若硬编码 ×100 会被全部判为错。
    """
    p = tmp_path / "688001.SH.exec.csv"
    _mk(p)
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: _ten(vol=6896485.0))
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: _sin(vol=6896485.0))

    rec = ux.check_one("688001.SH", str(tmp_path), "2026-10-30", True)
    assert rec["status"] == "READY"
    assert rec["volume_unit"] == "share"


def test_refuses_unexplained_volume_mismatch(tmp_path, monkeypatch):
    """既非等值也非 100 倍 ⇒ 不是单位问题，是数据对不上，必须拒。

    这条是铁律「没检出 ≠ 没问题」的落点：只比价格的实现会把它放过去，
    然后把错 100 倍的成交额写进研究层。
    """
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    before = p.read_bytes()
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: _ten(vol=5000.0))
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: _sin(vol=120000.0))

    rec = ux.check_one("600000.SH", str(tmp_path), "2026-10-30", True)
    assert rec["status"] == "NO_AGREEMENT"
    assert rec["appended"] == 0
    assert any("既非等值也非 100 倍" in f for f in rec["flags"])
    assert p.read_bytes() == before


def test_refuses_mixed_unit_within_window(tmp_path, monkeypatch):
    """同一只股票窗口内单位不一致 ⇒ 全部不写（宁可不更新，不可写半真半假）。"""
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    before = p.read_bytes()
    ten = {"2026-10-19": {"open": 10.0, "high": 10.6, "low": 9.9, "close": 10.5, "volume": 1200.0},
           "2026-10-20": {"open": 10.0, "high": 10.6, "low": 9.9, "close": 10.5, "volume": 1200.0}}
    sin = {"2026-10-19": {"open": 10.0, "high": 10.6, "low": 9.9, "close": 10.5, "volume": 120000.0},
           "2026-10-20": {"open": 10.0, "high": 10.6, "low": 9.9, "close": 10.5, "volume": 1200.0}}
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: ten)
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: sin)

    rec = ux.check_one("600000.SH", str(tmp_path), "2026-10-30", True)
    assert rec["status"] == "NO_AGREEMENT"
    assert rec["appended"] == 0
    assert any("不一致" in f for f in rec["flags"])
    assert p.read_bytes() == before


def test_refuses_missing_second_source(tmp_path, monkeypatch):
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    before = p.read_bytes()
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: _ten())
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: {})

    rec = ux.check_one("600000.SH", str(tmp_path), "2026-10-30", True)
    assert rec["status"] == "NO_AGREEMENT"
    assert any("新浪缺该日" in f for f in rec["flags"])
    assert p.read_bytes() == before


def test_up_to_date_skips_without_fetch(tmp_path, monkeypatch):
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p, last="2026-10-30")

    def boom(*a, **k):
        raise AssertionError("已是最新就不该联网")

    monkeypatch.setattr(ux, "fetch_tencent", boom)
    rec = ux.check_one("600000.SH", str(tmp_path), "2026-10-30", True)
    assert rec["status"] == "UP_TO_DATE"


def test_no_local_file_is_not_silently_treated_as_ok(tmp_path):
    rec = ux.check_one("999999.SH", str(tmp_path), "2026-10-30", False)
    assert rec["status"] == "NO_LOCAL_FILE"


def test_no_new_rows_is_distinct_from_up_to_date(tmp_path, monkeypatch):
    """停牌/退市表现为「没有新行」，必须与「已更新到目标日」区分开。"""
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: {})
    rec = ux.check_one("600000.SH", str(tmp_path), "2026-10-30", False)
    assert rec["status"] == "NO_NEW_ROWS"


def test_dry_run_never_writes(tmp_path, monkeypatch):
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    before = p.read_bytes()
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: _ten())
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: _sin())

    rec = ux.check_one("600000.SH", str(tmp_path), "2026-10-30", False)
    assert rec["status"] == "READY" and rec["appended"] == 1
    assert not rec.get("written")
    assert p.read_bytes() == before


def test_append_preserves_original_bytes(tmp_path, monkeypatch):
    """只追加：原文件最后一个换行之前的字节必须一字不差。"""
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    before = p.read_bytes()
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: _ten())
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: _sin())

    ux.check_one("600000.SH", str(tmp_path), "2026-10-30", True)
    after = p.read_bytes()
    assert after.startswith(before), "追加不得改动历史字节"
    assert len(after) > len(before)


def test_symbol_mapping():
    assert ux.to_symbol("600000.SH") == "sh600000"
    assert ux.to_symbol("000001.SZ") == "sz000001"


def test_report_is_written_with_units_documented(tmp_path, monkeypatch):
    p = tmp_path / "600000.SH.exec.csv"
    _mk(p)
    monkeypatch.setattr(ux, "fetch_tencent", lambda *a, **k: _ten())
    monkeypatch.setattr(ux, "fetch_sina", lambda *a, **k: _sin())
    rep = tmp_path / "r.json"

    rc = ux.main(["--exec-dir", str(tmp_path), "--codes", "600000.SH",
                  "--end", "2026-10-30", "--report", str(rep)])
    assert rc == 0
    d = json.loads(rep.read_text(encoding="utf-8"))
    assert "share" in d["volume_unit_note"] and "hand" in d["volume_unit_note"]
    assert d["status"]["READY"] == 1
