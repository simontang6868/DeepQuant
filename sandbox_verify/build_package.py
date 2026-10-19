"""打包 DeepQuant 交付 zip（给沙箱实测用）。

规则：
- 白名单目录/文件 + 黑名单模式；
- zip 根目录放 README_FOR_SANDBOX.md 与 MANIFEST.sha256；
- 代码树放在 DeepQuant-main/ 下（保持仓库层级）。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(r"D:/DeepQuant power by txm/DeepQuant-main")
OUT = Path(r"D:/DeepQuant power by txm/_upload/DeepQuant-main_sandbox_2026-10-19.zip")

INCLUDE_DIRS = [
    "backend/src",
    "backend/tests",
    "backend/tools",
    "scripts",
    "frontend/dist",
    "frontend/src",
    "frontend/public",
    "data/metadata",
    "data/processed",
    "data/step75",
    "data/step75_ext",
    "docs",
    "gate1_out",
    "probe_out",
    "sandbox_verify",
]
INCLUDE_FILES = [
    "backend/requirements.txt",
    "backend/requirements-dev.txt",
    "backend/pyproject.toml",
    "backend/pytest.ini",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/index.html",
    "start_dev.sh",
    "start_deepquant.bat",
]
EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".git",
    "htmlcov",
    ".ipynb_checkpoints",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd", ".so", ".log", ".coverage"}
EXCLUDE_FILE_NAMES = {".DS_Store", "Thumbs.db"}


def included_files() -> list[Path]:
    files: list[Path] = []
    for rel in INCLUDE_DIRS:
        base = ROOT / rel
        if not base.is_dir():
            raise SystemExit(f"缺少目录：{base}")
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
            for name in filenames:
                p = Path(dirpath) / name
                if name in EXCLUDE_FILE_NAMES:
                    continue
                if p.suffix.lower() in EXCLUDE_SUFFIXES:
                    continue
                files.append(p)
    for rel in INCLUDE_FILES:
        p = ROOT / rel
        if not p.is_file():
            raise SystemExit(f"缺少文件：{p}")
        files.append(p)
    files.extend(sorted(ROOT.glob("*.md")))
    return sorted(set(files))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    files = included_files()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    manifest_lines: list[str] = []
    total_raw = 0
    for p in files:
        total_raw += p.stat().st_size
        manifest_lines.append(f"{sha256(p)}  {p.relative_to(ROOT).as_posix()}")

    manifest = "\n".join(manifest_lines) + "\n"
    # 包身份：MANIFEST 原文的 sha256。它只随内容变，不随 zip 压缩参数变，
    # 因此重建一次内容相同 → 同一个 ID；任何文件被改 → ID 必变。复核方引用这个。
    package_id = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    built_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    info = {
        "package_id": package_id,
        "built_at": built_at,
        "file_count": len(files),
        "python": platform.python_version(),
        "builder": "sandbox_verify/build_package.py",
        "verify_with": "python sandbox_verify/verify_package.py",
        "note": (
            "package_id = sha256(MANIFEST.sha256 原文)。它与本 zip 文件的 sha256 不是一回事："
            "后者随压缩方式变，前者只随内容变。请在复核报告里引用 package_id。"
        ),
    }

    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in files:
            arc = "DeepQuant-main/" + p.relative_to(ROOT).as_posix()
            zf.write(p, arc)

        # zip 根目录的三个文件
        readme = ROOT / "sandbox_verify" / "README_FOR_SANDBOX.md"
        zf.write(readme, "README_FOR_SANDBOX.md")
        zf.writestr("MANIFEST.sha256", manifest)
        zf.writestr("PACKAGE_INFO.json", json.dumps(info, ensure_ascii=False, indent=2) + "\n")

    print(f"输出：{OUT}")
    print(f"文件数：{len(files)}（+3 根文件：README / MANIFEST / PACKAGE_INFO）")
    print(f"原始体积：{total_raw / 1024 / 1024:.1f} MiB")
    print(f"zip 体积：{OUT.stat().st_size / 1024 / 1024:.1f} MiB")
    print(f"包 ID（引用这个）：{package_id}")
    print(f"  built_at：{built_at}")
    print(f"zip sha256（仅本文件）：{sha256(OUT)}")

    # 结构统计
    from collections import Counter

    c = Counter()
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        c[rel.split("/")[0] + "/" + (rel.split("/")[1] if rel.count("/") >= 1 else "")] += 1
    print("主要目录文件数：")
    for k, v in sorted(c.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {k:<32}{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
