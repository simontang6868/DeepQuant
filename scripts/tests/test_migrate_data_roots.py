"""migrate_data 清单 roots 路径的回归测试。

背景（2026-11-03 首次云主机部署时真实发生）：
清单生成时把 ``roots_rel`` 写成了**源机的绝对路径**（``D:/...``），目标机
（Linux）按 ``base / "D:/..."`` 拼接 ⇒ 10,948 个文件全部报"缺失"，
校验在 0.2 s 内 FAIL，且报错看不出根因。

本文件钉死三件事：
1. ``manifest`` 生成的 ``roots_rel`` 必须是**相对仓库根**的路径；
2. ``verify`` 遇到绝对路径 roots 必须拒绝并说清原因（不许装作正常跑完）；
3. manifest → verify 全链在临时目录里能 PASS，且缺一个文件必须 FAIL
   （防止"校验器永远绿"的假校验）。

运行：
    cd "D:/DeepQuant power by txm/DeepQuant-main"
    PYTHONPATH=deploy/bin python -m pytest scripts/tests/test_migrate_data_roots.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
_SPEC = _REPO / "deploy" / "bin" / "migrate_data.py"
_spec = importlib.util.spec_from_file_location("migrate_data", _SPEC)
migrate_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_data)


def _make_repo(tmp_path: Path) -> Path:
    """造一个微型"仓库"：data/exec_all 两个 csv + forward 一个 json。"""
    base = tmp_path / "repo"
    (base / "data" / "exec_all").mkdir(parents=True)
    (base / "forward").mkdir(parents=True)
    (base / "data" / "exec_all" / "000001.SZ.exec.csv").write_text(
        "date,close\n2026-01-05,10.0\n", encoding="utf-8", newline="\n")
    (base / "data" / "exec_all" / "000002.SZ.exec.csv").write_text(
        "date,close\n2026-01-05,20.0\n", encoding="utf-8", newline="\n")
    (base / "forward" / "heartbeat.json").write_text(
        '{"ok": true}\n', encoding="utf-8", newline="\n")
    return base


def test_manifest_records_relative_roots(tmp_path):
    base = _make_repo(tmp_path)
    out = tmp_path / "m.json"
    rc = migrate_data.main([
        "manifest", "--base", str(base), "--out", str(out),
        "--root", "exec_all=data/exec_all",
        "--root", "forward=forward",
    ])
    assert rc == 0
    m = json.loads(out.read_text(encoding="utf-8"))
    for rel in m["roots_rel"].values():
        assert ":" not in rel[:3] or rel[1] != ":", f"roots_rel 出现盘符：{rel}"
        assert not rel.startswith("/"), f"roots_rel 出现绝对路径：{rel}"
    # 且这些相对路径拼回去必须真实存在
    for rel in m["roots_rel"].values():
        assert (base / rel).is_dir(), rel
    assert m["n_files"] == 3


def test_verify_refuses_absolute_roots(tmp_path):
    """坏清单（历史 bug 形态）必须被明确拒绝，而不是报 1 万个缺失。"""
    base = _make_repo(tmp_path)
    good = tmp_path / "good.json"
    migrate_data.main(["manifest", "--base", str(base), "--out", str(good),
                       "--root", "exec_all=data/exec_all"])
    m = json.loads(good.read_text(encoding="utf-8"))
    # 复刻历史 bug：把 rel 换成绝对路径（内容/哈希不动，manifest_sha256 也就不动）
    m["roots_rel"] = {k: str(base / v) for k, v in m["roots_rel"].items()}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    rc = migrate_data.main(["verify", "--base", str(base), "--manifest", str(bad)])
    assert rc == 1


def test_manifest_verify_roundtrip_pass(tmp_path):
    base = _make_repo(tmp_path)
    out = tmp_path / "m.json"
    migrate_data.main(["manifest", "--base", str(base), "--out", str(out),
                       "--root", "exec_all=data/exec_all",
                       "--root", "forward=forward"])
    rc = migrate_data.main(["verify", "--base", str(base), "--manifest", str(out)])
    assert rc == 0


def test_verify_catches_missing_file(tmp_path):
    """反假校验：删掉一个文件必须 FAIL。校验器不能永远绿。"""
    base = _make_repo(tmp_path)
    out = tmp_path / "m.json"
    migrate_data.main(["manifest", "--base", str(base), "--out", str(out),
                       "--root", "exec_all=data/exec_all"])
    (base / "data" / "exec_all" / "000002.SZ.exec.csv").unlink()
    rc = migrate_data.main(["verify", "--base", str(base), "--manifest", str(out)])
    assert rc == 1


def test_verify_catches_corrupted_content(tmp_path):
    """同大小篡改也要抓到（只比大小抓不住内容被改）。"""
    base = _make_repo(tmp_path)
    out = tmp_path / "m.json"
    migrate_data.main(["manifest", "--base", str(base), "--out", str(out),
                       "--root", "exec_all=data/exec_all"])
    f = base / "data" / "exec_all" / "000001.SZ.exec.csv"
    same_size = f.read_bytes().replace(b"10.0", b"10.1")  # 同字节数的内容篡改
    assert len(same_size) == f.stat().st_size
    f.write_bytes(same_size)
    rc = migrate_data.main(["verify", "--base", str(base), "--manifest", str(out)])
    assert rc == 1
