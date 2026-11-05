"""`scripts/check_universe_mirror.py` 的判据测试。

重点是**失败关闭**：云端哈希拿不到（空集合）时不得判 PASS。
这条来自一次真实的假绿——空 dict 上 `all()` 恒真，把我自己写的清单判成了"全部一致"。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_universe_mirror as m  # noqa: E402

H1 = "a" * 64
H2 = "b" * 64


def test_parse_strips_binary_marker():
    text = f"{H1} *20261103.csv\n{H2}  20261103.json\n"
    got = m.parse_sha256sum(text)
    assert got == {"20261103.csv": H1, "20261103.json": H2}


def test_parse_ignores_short_or_malformed_lines():
    text = "not-a-hash file.txt\nabcd  x.txt\n\n" + f"{H1} ok.csv\n"
    assert m.parse_sha256sum(text) == {"ok.csv": H1}


def test_compare_empty_remote_is_fail_not_vacuous_pass():
    res = m.compare({"20261103.csv": H1}, {})
    assert res["ok"] is False
    assert res["reason"] == "CLOUD_HASHES_EMPTY"


def test_compare_empty_local_is_fail():
    res = m.compare({}, {"20261103.csv": H1})
    assert res["ok"] is False
    assert res["reason"] == "LOCAL_SNAPSHOTS_EMPTY"


def test_compare_detects_hash_mismatch():
    res = m.compare({"a.csv": H1}, {"a.csv": H2})
    assert res["ok"] is False
    assert res["mismatch"] == ["a.csv"]


def test_compare_detects_missing_local_and_extra_local():
    res = m.compare({"a.csv": H1, "b.csv": H2}, {"a.csv": H1})
    assert res["ok"] is False
    assert res["local_only"] == ["b.csv"]
    res2 = m.compare({"a.csv": H1}, {"a.csv": H1, "c.csv": H2})
    assert res2["ok"] is False
    assert res2["missing_local"] == ["c.csv"]


def test_compare_identical_ok():
    res = m.compare({"a.csv": H1}, {"a.csv": H1})
    assert res["ok"] is True and res["reason"] == "OK"


def test_offline_without_manifest_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "MIRROR", tmp_path / "LOCAL_MIRROR.json")
    assert m.main(["--offline", "--dir", str(tmp_path)]) == 2


def test_offline_with_matching_manifest_passes(tmp_path, monkeypatch):
    (tmp_path / "20261103.csv").write_text("x", encoding="utf-8")
    h = m.sha256_file(tmp_path / "20261103.csv")
    man = tmp_path / "LOCAL_MIRROR.json"
    man.write_text(json.dumps({"files": {"20261103.csv": h}}), encoding="utf-8")
    monkeypatch.setattr(m, "MIRROR", man)
    assert m.main(["--offline", "--dir", str(tmp_path)]) == 0


def test_offline_with_stale_hash_fails(tmp_path, monkeypatch):
    """清单里的哈希与文件不匹配必须红——这是"本地被改过"的唯一证据。"""
    (tmp_path / "20261103.csv").write_text("x", encoding="utf-8")
    man = tmp_path / "LOCAL_MIRROR.json"
    man.write_text(json.dumps({"files": {"20261103.csv": H2}}), encoding="utf-8")
    monkeypatch.setattr(m, "MIRROR", man)
    assert m.main(["--offline", "--dir", str(tmp_path)]) == 1


def test_empty_dir_is_not_silent_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "MIRROR", tmp_path / "LOCAL_MIRROR.json")
    (tmp_path / "LOCAL_MIRROR.json").write_text(json.dumps({"files": {}}), encoding="utf-8")
    assert m.main(["--offline", "--dir", str(tmp_path)]) != 0
