#!/usr/bin/env python3
"""corroboration：本机出证的旁证产物（U16-EXEC-2，冻结格式不冻结值）。

时序悖论解法（专家复审裁决 2）：预登记冻结**格式 + 生成程序 + 发布动作**，
值后产生；旁证经现有 17:40 告警邮件外发 sha256，12-02 归档邮件对验。

旁证三条件（运行时 E4 校验，任一不符 FAIL）：
1. 内嵌哈希与内容一致（``sha256_of_files`` 由 files 字典独立算术重算）
2. commit 与当前部署树匹配（``commit`` 字段 == 生成时 git HEAD）
3. 与当前部署树**双向集合全等**（检出新增/删除/内容变化，不只逐文件哈希）

威胁边界：单主机内不存在机器可验的外部锚。邮件双发给出的是
tamper-evidence（事后可检测），不是事前预防。仅适用 SIM_DRILL。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORROB_REL = "research/stock_pick_2026-10-25/forward/corroboration/20261130.json"
SCHEMA = "sim_corroboration/v1"
# 旁证覆盖的部署文件集合（与 DEPLOY_FILES 一致，从 manifest 读取）
MANIFEST_REL = "research/stock_pick_2026-10-25/forward/deploy_manifest.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_digest(files: dict[str, str]) -> str:
    """独立算术：排序后拼 ``rel:sha\\n`` 再 sha256，不调用被测函数。"""
    body = "".join(f"{k}:{v}\n" for k, v in sorted(files.items()))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _git_head() -> str:
    cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                        capture_output=True, text=True, timeout=30)
    if cp.returncode != 0:
        return ""
    return (cp.stdout or "").strip()


def build_corroboration(*, manifest_path: Path | None = None,
                        root: Path | None = None) -> dict[str, Any]:
    """本机生成旁证：从冻结 manifest 取文件哈希集合 + 当前 git HEAD。"""
    root = Path(root) if root else ROOT
    mp = Path(manifest_path) if manifest_path else root / MANIFEST_REL
    rec = json.loads(mp.read_text(encoding="utf-8"))
    files = dict(rec.get("files") or {})
    if not files:
        raise ValueError("MANIFEST_EMPTY")
    return {"schema": SCHEMA, "generated_at": dt.datetime.now(
                dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
            "commit": _git_head(), "files": files,
            "sha256_of_files": manifest_digest(files),
            "n_files": len(files)}


def corrob_sha(rec: dict[str, Any]) -> str:
    """旁证自身 sha256（排除 none；整字典 canonical json）。"""
    body = json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def write_corroboration(rec: dict[str, Any], path: Path | None = None) -> Path:
    import os
    import tempfile
    p = Path(path) if path else ROOT / CORROB_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
    return p


def verify_corroboration(rec: dict[str, Any], *, root: Path | None = None,
                         expected_sha: str = "", expected_commit: str = "") -> dict[str, Any]:
    """三条件校验：内嵌哈希 / commit / 双向集合全等。返回独立结论。"""
    root = Path(root) if root else ROOT
    checks: dict[str, bool] = {}
    diffs: list[str] = []
    files = rec.get("files") if isinstance(rec.get("files"), dict) else None
    checks["schema"] = rec.get("schema") == SCHEMA and files is not None
    checks["hash"] = bool(files) and manifest_digest(files) == rec.get("sha256_of_files")
    cur_commit = _git_head()
    # commit 校验以外部固化值为准（云端 /opt/quant 无 .git，不可依赖运行时 HEAD）；
    # 有 expected_commit 时必须相等，无则只要求旁证自身有值。
    checks["commit"] = bool(rec.get("commit")) and (
        rec.get("commit") == expected_commit if expected_commit else True)
    if expected_sha:
        checks["external_sha"] = corrob_sha(rec) == expected_sha
    if expected_commit:
        checks["external_commit"] = rec.get("commit") == expected_commit
    # 双向集合全等：旁证文件集 vs 当前 manifest 文件集（检出新增/删除），
    # 再逐文件比对当前哈希（检出内容变化）。新增 manifest 文件也算差异。
    current_set = set((json.loads((root / MANIFEST_REL).read_text(encoding="utf-8"))
                       .get("files") or {}).keys()) if (root / MANIFEST_REL).is_file() else set()
    for rel, sha in (files or {}).items():
        p = root / rel
        if not p.is_file():
            diffs.append(f"MISSING:{rel}")
        elif _sha(p) != sha:
            diffs.append(f"CHANGED:{rel}")
    if current_set:
        want = set(files or {})
        for rel in sorted(current_set ^ want):
            diffs.append(f"SET_DIFF:{rel}")
    checks["set_equal"] = not diffs
    ok = all(checks.values())
    return {"ok": ok, "checks": checks, "diffs": diffs,
            "n_files": len(files or {}), "commit": cur_commit}


def self_check(tmp: Path | None = None) -> dict[str, Any]:
    """离线自检：篡改哈希 / 过期 commit / 增删文件三者必须被检出。"""
    import tempfile
    checks = []
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        mp = root / "manifest.json"
        exact = {"a.py": hashlib.sha256(b"a").hexdigest(),
                 "b.py": hashlib.sha256(b"b").hexdigest()}
        sample = {"files": exact, "commit": "deadbeef"}
        mp.write_text(json.dumps(sample), encoding="utf-8", newline="\n")
        (root / "a.py").write_text("a", encoding="utf-8")
        (root / "b.py").write_text("b", encoding="utf-8")
        # 真实 manifest 路径也要放一份，verify 的双向集合比对读它
        mreal = root / MANIFEST_REL
        mreal.parent.mkdir(parents=True, exist_ok=True)
        mreal.write_text(json.dumps(sample), encoding="utf-8", newline="\n")
        rec = {"schema": SCHEMA, "generated_at": "2026-11-30T17:20:00+08:00",
               "commit": "deadbeef", "files": exact,
               "sha256_of_files": manifest_digest(exact), "n_files": 2}
        v = verify_corroboration(rec, root=root, expected_commit="deadbeef")
        checks.append(("base_ok", v["ok"]))

        bad = dict(rec); bad["sha256_of_files"] = "0" * 64
        checks.append(("tamper_hash_detected",
                       not verify_corroboration(bad, root=root)["ok"]))

        bad = dict(rec); bad["commit"] = "0" * 8
        checks.append(("stale_commit_detected",
                       not verify_corroboration(bad, root=root,
                                                expected_commit="deadbeef")["ok"]))

        # 新增文件：当前 manifest 多一项而旁证没有 ⇒ 对称差检出
        new_sample = dict(sample)
        new_sample["files"] = dict(exact)
        new_sample["files"]["c.py"] = hashlib.sha256(b"new").hexdigest()
        mreal.write_text(json.dumps(new_sample), encoding="utf-8", newline="\n")
        checks.append(("added_file_detected",
                       not verify_corroboration(rec, root=root)["ok"]))
        mreal.write_text(json.dumps(sample), encoding="utf-8", newline="\n")

        # 删除文件：旁证少一项（且哈希重算自洽）⇒ 对称差检出
        bad_files = {k: v for k, v in exact.items() if k != "b.py"}
        bad = dict(rec); bad["files"] = bad_files
        bad["sha256_of_files"] = manifest_digest(bad_files)
        checks.append(("removed_file_detected",
                       not verify_corroboration(bad, root=root)["ok"]))

        # 内容变化：文件被改但旁证哈希未更新 ⇒ CHANGED 检出
        (root / "a.py").write_text("MODIFIED", encoding="utf-8")
        checks.append(("content_change_detected",
                       not verify_corroboration(rec, root=root)["ok"]))
    return {"decision": "PASS" if all(v for _, v in checks) else "FAIL",
            "checks": [k for k, v in checks],
            "statistical_verdict": "ENGINEERING_SELF_CHECK_ONLY"}
