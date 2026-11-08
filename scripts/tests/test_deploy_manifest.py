"""R6-3 部署 manifest：自校验、本地/云端比对、失败关闭。"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "deploy_manifest.py"
spec = importlib.util.spec_from_file_location("dm", SRC)
assert spec and spec.loader
dm = importlib.util.module_from_spec(spec)
sys.modules["dm"] = dm
spec.loader.exec_module(dm)


def fake_rec(tmp_path, names=("a.py", "b.py")):
    files = {}
    for n in names:
        p = tmp_path / n
        p.write_text("content-" + n, encoding="utf-8")
        files[n] = dm.sha256_file(p)
    return {"schema_version": dm.SCHEMA, "commit": "deadbeef", "n_files": len(files),
            "files": files, "sha256_of_files": dm.manifest_digest(files)}


def test_build_nonempty_and_commit():
    rec = dm.build()
    assert rec["n_files"] > 20
    assert rec["commit"] not in ("", "UNKNOWN")
    assert dm.manifest_digest(rec["files"]) == rec["sha256_of_files"]


def test_build_refuses_empty(monkeypatch):
    monkeypatch.setattr(dm, "collect_paths", lambda: [])
    with pytest.raises(RuntimeError, match="清单为空"):
        dm.build()


def test_load_manifest_detects_tampering(tmp_path):
    rec = fake_rec(tmp_path)
    rec["files"]["a.py"] = "0" * 64          # 改了 files 但没改 sha
    p = tmp_path / "deploy_manifest.json"
    p.write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(RuntimeError, match="sha256 不符"):
        dm.load_manifest(p)


def test_load_manifest_missing_file(tmp_path):
    with pytest.raises(RuntimeError, match="缺 .*先跑"):
        dm.load_manifest(tmp_path / "nope.json")


def test_verify_local_ok(tmp_path, monkeypatch):
    rec = fake_rec(tmp_path)
    monkeypatch.setattr(dm, "REPO", tmp_path)
    ok, why, diffs = dm.verify_local(rec)
    assert ok and not diffs


def test_verify_local_detects_changed_and_missing(tmp_path, monkeypatch):
    rec = fake_rec(tmp_path)
    (tmp_path / "a.py").write_text("TAMPERED", encoding="utf-8")
    (tmp_path / "b.py").unlink()
    monkeypatch.setattr(dm, "REPO", tmp_path)
    ok, _, diffs = dm.verify_local(rec)
    assert not ok and len(diffs) == 2
    assert any("改 a.py" in d for d in diffs) and any("缺 b.py" in d for d in diffs)


def remote_out(rec, drop=(), rc=0):
    lines = []
    for k, v in rec["files"].items():
        if k not in drop:
            lines.append(f"{v}  {k}")
    return types.SimpleNamespace(returncode=rc, stdout="\n".join(lines) + "\n", stderr="")


def test_verify_remote_ok(monkeypatch):
    rec = {"files": {"a.py": "1" * 64, "b.py": "2" * 64}}
    monkeypatch.setattr(dm.subprocess, "run", lambda *a, **k: remote_out(rec))
    ok, why, diffs = dm.verify_remote(rec)
    assert ok and not diffs and "2/2" in why


def test_verify_remote_rc1_still_reports_missing(monkeypatch):
    """sha256sum 缺文件时 rc=1 但仍输出其余哈希：必须报「缺哪个」，不能被 SSH_FAIL 吞掉。"""
    rec = {"files": {"a.py": "1" * 64, "b.py": "2" * 64}}
    monkeypatch.setattr(dm.subprocess, "run",
                        lambda *a, **k: remote_out(rec, drop=("b.py",), rc=1))
    ok, why, diffs = dm.verify_remote(rec)
    assert not ok and any("云端缺 b.py" in d for d in diffs)
    assert "sha256sum rc=1" in why


def test_verify_remote_empty_hashes_is_fail_closed(monkeypatch):
    rec = {"files": {"a.py": "1" * 64}}
    monkeypatch.setattr(dm.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=255, stdout="", stderr=""))
    ok, why, diffs = dm.verify_remote(rec)
    assert not ok and diffs == ["EMPTY_HASHES"] and "失败关闭" in why


def test_verify_remote_ssh_exception_is_fail_closed(monkeypatch):
    def boom(*_a, **_k):
        raise TimeoutError("ssh")
    monkeypatch.setattr(dm.subprocess, "run", boom)
    ok, why, diffs = dm.verify_remote({"files": {"a.py": "1" * 64}})
    assert not ok and "失败关闭" in why and diffs == ["SSH_FAIL"]


def test_main_exit_codes(monkeypatch, tmp_path):
    rec = fake_rec(tmp_path)
    monkeypatch.setattr(dm, "REPO", tmp_path)
    monkeypatch.setattr(dm, "MANIFEST", tmp_path / "deploy_manifest.json")
    dm.MANIFEST.write_text(json.dumps(rec), encoding="utf-8")
    assert dm.main(["--verify"]) == 0
    (tmp_path / "a.py").write_text("CHANGED", encoding="utf-8")
    assert dm.main(["--verify"]) == 1
