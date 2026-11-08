"""R6-3 部署 manifest 检查器的变异测试（变异必须真改行为）。"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "deploy_manifest.py"
ORIGINAL = SRC.read_text(encoding="utf-8")
sys.path.insert(0, str(SRC.parent))
import deploy_manifest as base  # noqa: E402


def load_variant(code: str) -> dict:
    ns = {"__name__": "dm_variant", "__file__": str(SRC)}
    exec(compile(code, str(SRC), "exec"), ns)  # noqa: S102
    return ns


def _rec(tmp: Path, tamper: bool = False) -> dict:
    a, b = tmp / "a.py", tmp / "b.py"
    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")
    files = {"a.py": base.sha256_file(a), "b.py": base.sha256_file(b)}
    rec = {"schema_version": base.SCHEMA, "commit": "x", "n_files": 2,
           "files": files, "sha256_of_files": base.manifest_digest(files)}
    if tamper:
        rec["files"]["a.py"] = "0" * 64
    return rec


def probe_m1(mod):
    """空清单必须被拒绝。"""
    saved = mod["collect_paths"]
    mod["collect_paths"] = lambda: []
    try:
        try:
            mod["build"]()
            return False
        except RuntimeError:
            return True
    finally:
        mod["collect_paths"] = saved


def probe_m2(mod):
    """manifest 自身 sha 不符必须被拒绝。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "deploy_manifest.json"
        p.write_text(json.dumps(_rec(Path(d), tamper=True)), encoding="utf-8")
        # _rec 写了 a.py/b.py，不影响 load_manifest
        try:
            mod["load_manifest"](p)
            return False
        except RuntimeError:
            return True


def probe_m3(mod):
    """本地文件被改必须报 FAIL。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        rec = _rec(tmp)
        (tmp / "a.py").write_text("TAMPERED", encoding="utf-8")
        saved = mod["REPO"]
        mod["REPO"] = tmp
        try:
            ok, _, _ = mod["verify_local"](rec)
            return not ok
        finally:
            mod["REPO"] = saved


def _remote_with_drop(mod):
    """云端缺 b.py（sha256sum rc=1 但仍输出 a.py）必须报 FAIL。"""
    rec = {"files": {"a.py": "1" * 64, "b.py": "2" * 64}}
    out = types.SimpleNamespace(returncode=1, stdout=f"{'1' * 64}  a.py\n", stderr="")
    saved = mod["subprocess"]
    mod["subprocess"] = types.SimpleNamespace(
        run=lambda *a, **k: out, TimeoutError=TimeoutError)
    try:
        ok, _, _ = mod["verify_remote"](rec)
        return not ok
    finally:
        mod["subprocess"] = saved


MUTATIONS = [
    ("M1", "空清单不再拒绝生成",
     'if not files:\n        raise RuntimeError("清单为空 ⇒ 拒绝生成（空输入不得判 PASS）")',
     'if False:\n        raise RuntimeError("清单为空 ⇒ 拒绝生成（空输入不得判 PASS）")',
     probe_m1),

    ("M2", "manifest 自身 sha 校验被摘掉",
     'if manifest_digest(rec["files"]) != rec.get("sha256_of_files"):',
     'if False:',
     probe_m2),

    ("M3", "本地文件改动不再判红",
     'elif sha256_file(p) != want:',
     'elif False:',
     probe_m3),

    ("M4", "云端缺文件不再被发现（只遍历拿到的哈希）",
     "for rel in paths:\n        if rel not in got:",
     "for rel in got:\n        if False:",
     _remote_with_drop),

    ("M5", "所有差异一律判 PASS",
     "return (not diffs, note, diffs)",
     "return (True, note, diffs)",
     _remote_with_drop),
]


def main() -> int:
    detected = 0
    for mid, desc, old, new, probe in MUTATIONS:
        if old not in ORIGINAL:
            print(f"[{mid}] ✗ 变异定义失效：替换文本未命中源码")
            return 1
        variant = load_variant(ORIGINAL.replace(old, new, 1))
        base_red = probe(vars(base))
        var_red = probe(variant)
        if not base_red:
            print(f"[{mid}] ✗ 测试用例无效：原版没判红（{desc}）")
            return 1
        if var_red:
            print(f"[{mid}] ✗ 变异未被检出：{desc}")
            return 1
        detected += 1
        print(f"[{mid}] ✓ 检出：{desc}")
    print(f"[mutation] {detected}/{len(MUTATIONS)} 检出；全部变异均真改行为")
    return 0


if __name__ == "__main__":
    sys.exit(main())
