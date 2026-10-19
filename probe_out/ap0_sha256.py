"""A-P0 指纹采集：gate1_out/ 与 docs/ 的逐文件 sha256 + 目录级聚合指纹。只读。"""
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(r"D:\DeepQuant power by txm\DeepQuant-main")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_sha(files):
    """目录级聚合：把 (相对路径, 文件sha) 按路径排序后串联再哈希，可复现且对重命名敏感。"""
    h = hashlib.sha256()
    for rel, s in sorted(files):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(s.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def collect(base: Path, pattern="*"):
    out = []
    for p in sorted(base.rglob(pattern)):
        if p.is_file():
            out.append({"path": p.relative_to(ROOT).as_posix(),
                        "size": p.stat().st_size,
                        "sha256": sha256_file(p)})
    return out


res = {}

# --- gate1_out 整个目录 ---
g = collect(ROOT / "gate1_out")
res["gate1_out"] = {"files": g, "count": len(g), "tree_sha256": tree_sha([(x["path"], x["sha256"]) for x in g])}
res["gate1_out"]["runs"] = {}
for run in sorted((ROOT / "gate1_out").iterdir()):
    if run.is_dir():
        sub = collect(run)
        res["gate1_out"]["runs"][run.name] = {
            "count": len(sub), "tree_sha256": tree_sha([(x["path"], x["sha256"]) for x in sub])
        }

# --- docs 整个目录 ---
d = collect(ROOT / "docs")
res["docs"] = {"files": d, "count": len(d), "tree_sha256": tree_sha([(x["path"], x["sha256"]) for x in d])}

# --- 预登记冻结校验 ---
prereg = ROOT / "docs" / "GATE1_PREREGISTRATION.md"
sha_file = ROOT / "docs" / "GATE1_PREREGISTRATION.sha256"
res["preregistration"] = {
    "doc_sha256": sha256_file(prereg) if prereg.exists() else None,
    "expected_line": sha_file.read_text(encoding="utf-8").strip() if sha_file.exists() else None,
}

# --- 代码/测试规模 ---
res["counts"] = {
    "tests_py": len(list((ROOT / "backend" / "tests").rglob("test_*.py"))),
    "tests_all_py": len(list((ROOT / "backend" / "tests").rglob("*.py"))),
    "src_py": len(list((ROOT / "backend" / "src" / "deepquant").rglob("*.py"))),
    "docs_md": len(list((ROOT / "docs").glob("*.md"))),
}

io.open(ROOT / "probe_out" / "ap0_sha256.json", "w", encoding="utf-8").write(
    json.dumps(res, ensure_ascii=False, indent=1)
)

print("gate1_out files:", res["gate1_out"]["count"], "tree", res["gate1_out"]["tree_sha256"][:16])
for k, v in res["gate1_out"]["runs"].items():
    print("  run", k, v["count"], "files", v["tree_sha256"][:16])
print("docs files:", res["docs"]["count"], "tree", res["docs"]["tree_sha256"][:16])
print("prereg doc  :", (res["preregistration"]["doc_sha256"] or "")[:16])
print("prereg expect:", res["preregistration"]["expected_line"])
print("counts:", res["counts"])
