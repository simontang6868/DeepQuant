#!/usr/bin/env python3
"""M9 真变异（U16-EXEC-2）：旁证被篡改/换过期 commit 必须被检出。

纪律：让旁证校验判据真失效（篡改也放行），用独立算术构造被测输入，
不调用被测函数推导期望值；跑完恢复并复核 sha256。
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "sim_corroboration.py"

OLD = '    checks["hash"] = bool(files) and manifest_digest(files) == rec.get("sha256_of_files")'
NEW = '    checks["hash"] = True  # MUT: 篡改哈希也放行'
WHY = "旁证内嵌哈希被篡改仍判 PASS（hash 判据失效）"

# 独立算术：篡改 sha256_of_files，期望原实现 FAIL、变异实现 PASS
OLD_COMMIT = ('    checks["commit"] = bool(rec.get("commit")) and (\n'
              '        rec.get("commit") == expected_commit if expected_commit else True)')
NEW_COMMIT = ('    checks["commit"] = True  # MUT: 过期 commit 也放行')
OLD_COMMIT2 = ('    if expected_commit:\n'
               '        checks["external_commit"] = rec.get("commit") == expected_commit')
NEW_COMMIT2 = ('    if expected_commit:\n'
               '        checks["external_commit"] = True  # MUT2')
WHY_COMMIT = "旁证 commit 过期仍判 PASS（commit 两处判据同时失效）"

# 双锚点变异：用一个 marker 同时替换两段
COMBINED_OLD = OLD_COMMIT + "\n" + OLD_COMMIT2
COMBINED_NEW = NEW_COMMIT + "\n" + OLD_COMMIT2.replace(
    'rec.get("commit") == expected_commit', 'True  # MUT2')


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fixture_root(root: Path) -> dict:
    """独立构造合法旁证 + 目录，不调用被测 build。"""
    exact = {"a.py": hashlib.sha256(b"a").hexdigest(),
             "b.py": hashlib.sha256(b"b").hexdigest()}
    (root / "a.py").write_text("a", encoding="utf-8")
    (root / "b.py").write_text("b", encoding="utf-8")
    manifest = root / "sim_corroboration" / ".." / "manifest.json"
    # verify 读 MANIFEST_REL = research/stock_pick_2026-10-25/forward/deploy_manifest.json
    mreal = root / Path("research/stock_pick_2026-10-25/forward/deploy_manifest.json")
    mreal.parent.mkdir(parents=True, exist_ok=True)
    mreal.write_text(json.dumps({"files": exact}), encoding="utf-8", newline="\n")
    body = "".join(f"{k}:{v}\n" for k, v in sorted(exact.items()))
    sha = hashlib.sha256(body.encode()).hexdigest()
    return {"schema": "sim_corroboration/v1", "commit": "abc12345",
            "files": exact, "sha256_of_files": sha}


def detect_tamper_hash(mod) -> bool:
    """返回 True = 变异被检出（篡改哈希仍 PASS）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rec = _fixture_root(root)
        rec["sha256_of_files"] = "0" * 64
        with contextlib.redirect_stdout(io.StringIO()):
            v = mod.verify_corroboration(rec, root=root)
        return v["ok"] is True


def detect_stale_commit(mod) -> bool:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rec = _fixture_root(root)
        with contextlib.redirect_stdout(io.StringIO()):
            v = mod.verify_corroboration(rec, root=root, expected_commit="abc12345")
        assert v["ok"] is True, "基线就不过，检测器无意义"
        rec["commit"] = "00000000"   # 过期 commit
        with contextlib.redirect_stdout(io.StringIO()):
            v2 = mod.verify_corroboration(rec, root=root, expected_commit="abc12345")
        return v2["ok"] is True


def detect_stale_commit_no_expect(mod) -> bool:
    """不传 expected_commit：旁证 commit 为空/过期时 OLD_COMMIT 是唯一判据。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rec = _fixture_root(root)
        rec["commit"] = ""          # 过期/缺失 commit
        with contextlib.redirect_stdout(io.StringIO()):
            v = mod.verify_corroboration(rec, root=root)
        return v["ok"] is True      # 变异后放行 ⇒ 检出


MUTATIONS = [
    ("M9A_CORROB_HASH_ALWAYS_OK", OLD, NEW, detect_tamper_hash, WHY),
    ("M9B_CORROB_COMMIT_ALWAYS_OK", OLD_COMMIT, NEW_COMMIT,
     detect_stale_commit_no_expect, WHY_COMMIT),
]


def main() -> int:
    baseline = hashlib.sha256(SRC.read_bytes()).hexdigest()
    backup = SRC.read_bytes()
    results = []
    try:
        for name, old, new, check, why in MUTATIONS:
            text = backup.decode("utf-8")
            n = text.count(old)
            if n != 1:
                results.append((name, "ANCHOR_INVALID", f"命中 {n} 次", why))
                continue
            mut = text.replace(old, new, 1)
            tag = hashlib.sha256(name.encode()).hexdigest()[:8]
            target = HERE / f"_mut_corrob_{tag}.py"
            target.write_bytes(mut.encode("utf-8"))
            try:
                mod = load(target, f"_mut_corrob_{tag}")
                detected = check(mod)
                results.append((name, "DETECTED" if detected else "MISSED",
                                "", why))
            except Exception as exc:  # noqa: BLE001
                results.append((name, "ERROR", f"{type(exc).__name__}: {exc}", why))
            finally:
                if target.exists():
                    target.unlink()
    finally:
        SRC.write_bytes(backup)
        restored = hashlib.sha256(SRC.read_bytes()).hexdigest()
    ok = all(r[1] == "DETECTED" for r in results)
    for name, state, detail, why in results:
        print(f"[mut9] {state:14} {name:32} {why}" + (f" | {detail}" if detail else ""))
    print(f"[mut9] 恢复后 sha256 {restored[:12]} "
          f"{'一致' if restored == baseline else '不一致!!'}")
    return 0 if ok and restored == baseline else 1


if __name__ == "__main__":
    sys.exit(main())
