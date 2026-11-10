"""ECR-P0-SNAPSHOT-1 / R7-4：快照按日期分文件、rev 冲突、迁移的验收闸门。

每条对应工程变更单第 4 节的验收条款；期望值来自独立构造，不调用被测函数推导。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import paper_account_adapter as paa  # noqa: E402
import shadow_daily_reconcile as sdr  # noqa: E402


def rec(day: str, available: float = 100000.0,
        collected_at: str = "2026-11-10T09:00:00+00:00") -> dict:
    """手工构造快照；字段与 build_snapshot 一致，但**不调用被测函数**。"""
    return {"schema_version": "paper_snapshot_v2", "asof": day,
            "source": "gm-paper",
            "cash": {"available": available, "frozen": 0.0, "total": available},
            "positions": [], "collected_at": collected_at,
            "settlement_verified": "unknown"}


def write(d: Path, r: dict, **kw):
    return paa.write_daily_snapshot(r, directory=d, **kw)


# ── AC-1 跨日推进必须写成功 ────────────────────────────────────────────
def test_ac1_cross_day_advance_writes(tmp_path):
    d = Path(tmp_path)
    rc1, p1, why1 = write(d, rec("20261109"))
    rc2, p2, why2 = write(d, rec("20261110", collected_at="2026-11-11T09:00:00+00:00"))
    assert (rc1, rc2) == (paa.EXIT_OK, paa.EXIT_OK)
    assert p1.name == "account_snapshot_20261109_rev1.json"
    assert p2.name == "account_snapshot_20261110_rev1.json"
    assert why1 == why2 == "written"
    latest = json.loads((d / "latest.json").read_text(encoding="utf-8"))
    assert latest["day"] == "20261110" and latest["rev"] == 1
    assert latest["path"] == p2.name and len(latest["sha256"]) == 64
    # 原冻结单文件绝不被写
    assert not (d / "account_snapshot.json").exists()


def test_ac1_same_day_rerun_with_same_account_is_idempotent(tmp_path):
    """账户未变、仅采集时刻不同 ⇒ 幂等，不产生 rev2、不重写文件。"""
    d = Path(tmp_path)
    write(d, rec("20261110", collected_at="T1"))
    before = (d / "account_snapshot_20261110_rev1.json").read_bytes()
    rc, p, why = write(d, rec("20261110", collected_at="T2"))
    assert rc == paa.EXIT_OK and why == "idempotent"
    assert (d / "account_snapshot_20261110_rev1.json").read_bytes() == before
    assert not (d / "account_snapshot_20261110_rev2.json").exists()


# ── AC-2 同日不同内容拒绝 + 告警 + 非零 rc；--supersede 生成 rev2 ─────────
def test_ac2_same_day_different_account_rejected(tmp_path, capsys):
    d = Path(tmp_path)
    write(d, rec("20261110", available=100000.0))
    rc, p, why = write(d, rec("20261110", available=90000.0))
    assert rc == paa.EXIT_CONFLICT and p is None and why == "conflict"
    assert "拒绝覆盖" in capsys.readouterr().err
    assert not (d / "account_snapshot_20261110_rev2.json").exists()


def test_ac2_supersede_creates_rev2_with_reason(tmp_path):
    d = Path(tmp_path)
    write(d, rec("20261110", available=100000.0))
    rc, p, why = write(d, rec("20261110", available=90000.0),
                       supersede_reason="结算后重取")
    assert rc == paa.EXIT_OK and why == "superseded"
    assert p.name == "account_snapshot_20261110_rev2.json"
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body["rev"] == 2 and body["supersedes_rev"] == 1
    assert body["supersede_reason"] == "结算后重取"
    assert json.loads((d / "latest.json").read_text(encoding="utf-8"))["rev"] == 2


# ── AC-3 陈旧快照必须拒绝 ──────────────────────────────────────────────
def test_ac3_stale_snapshot_rejected(tmp_path):
    d = Path(tmp_path)
    (d / "account_snapshot_20261110_rev1.json").write_text(
        json.dumps(rec("20261109"), ensure_ascii=False) + "\n", encoding="utf-8")
    why, payload, meta = sdr.load_daily_snapshot("20261110", d)
    assert why.startswith("account_snapshot_stale")
    assert payload is None and meta["n_revs"] == 1


def test_ac3_missing_day_file_is_not_a_pass(tmp_path):
    why, payload, meta = sdr.load_daily_snapshot("20261110", Path(tmp_path))
    assert why == "no_daily_snapshot_file" and payload is None
    assert meta["n_revs"] == 0


# ── AC-4 rev 冲突判 REV_CONFLICT 且不计 G2 ───────────────────────────────
def test_ac4_rev_conflict_blocks_g2(tmp_path):
    d = Path(tmp_path)
    (d / "account_snapshot_20261110_rev1.json").write_text(
        json.dumps(rec("20261110", 100000.0)) + "\n", encoding="utf-8")
    (d / "account_snapshot_20261110_rev2.json").write_text(
        json.dumps(rec("20261110", 90000.0)) + "\n", encoding="utf-8")
    why, payload, meta = sdr.load_daily_snapshot("20261110", d)
    assert why == sdr.REV_CONFLICT and payload is None
    assert meta["n_revs"] == 2 and meta["revs"] == [1, 2]
    # payload=None ⇒ make_snapshot 必为 SELF_CHECK_ONLY ⇒ constitutes 必为 False
    _, mode = sdr.make_snapshot(None, "20261110", {"sleeve": 20000.0})
    assert mode == sdr.SELF_CHECK_ONLY


def test_ac4_identical_revs_are_allowed_and_latest_rev_chosen(tmp_path):
    d = Path(tmp_path)
    body = json.dumps(rec("20261110"), ensure_ascii=False) + "\n"
    (d / "account_snapshot_20261110_rev1.json").write_text(body, encoding="utf-8")
    (d / "account_snapshot_20261110_rev2.json").write_text(body, encoding="utf-8")
    why, payload, meta = sdr.load_daily_snapshot("20261110", d)
    assert why == "ok" and payload is not None
    assert meta["rev"] == 2 and len(meta["sha256"]) == 64


# ── AC-6 迁移幂等 ──────────────────────────────────────────────────────
def test_ac6_migration_idempotent(tmp_path):
    d = Path(tmp_path)
    legacy = d / "account_snapshot.json"
    legacy_body = json.dumps({"asof": "20261109", "source": "gm-paper",
                              "cash": {"available": 100000.0, "frozen": 0.0,
                                       "total": 100000.0},
                              "positions": []}, indent=1, sort_keys=True) + "\n"
    legacy.write_bytes(legacy_body.encode("utf-8"))
    r1 = paa.migrate_legacy_snapshot(directory=d, legacy=legacy)
    assert r1["status"] == "migrated" and r1["legacy_unchanged"] is True
    target = d / "account_snapshot_20261109_rev1.json"
    assert target.is_file()
    target_before = target.read_bytes()
    legacy_before = legacy.read_bytes()
    r2 = paa.migrate_legacy_snapshot(directory=d, legacy=legacy)
    assert r2["status"] == "already_migrated"
    assert legacy.read_bytes() == legacy_before          # 冻结不动
    assert target.read_bytes() == target_before          # 不重写
    assert r1["legacy_sha256"] == r2["legacy_sha256"]
    why, payload, _ = sdr.load_daily_snapshot("20261109", d)
    assert why == "ok" and payload["asof"] == "20261109"


def test_ac6_legacy_asof_must_be_valid(tmp_path):
    d = Path(tmp_path)
    legacy = d / "account_snapshot.json"
    legacy.write_text(json.dumps({"asof": "not-a-day"}) + "\n", encoding="utf-8")
    r = paa.migrate_legacy_snapshot(directory=d, legacy=legacy)
    assert r["status"] == "legacy_bad_asof"
    assert not list(d.glob("account_snapshot_*_rev*.json"))


# ── R7 2a：SHADOW 不依赖 latest.json ───────────────────────────────────
def test_shadow_does_not_depend_on_latest_pointer(tmp_path):
    d = Path(tmp_path)
    (d / "account_snapshot_20261110_rev1.json").write_text(
        json.dumps(rec("20261110")) + "\n", encoding="utf-8")
    (d / "latest.json").write_text("BROKEN-NOT-JSON", encoding="utf-8")
    why, payload, meta = sdr.load_daily_snapshot("20261110", d)
    assert why == "ok" and meta["rev"] == 1
