"""把本地 A 线产出批量推到 GitHub 分支 workbuddy/a-2026-10-18-final。

走 Git Data API（blob -> tree -> commit -> update-ref），一次提交可含任意多文件，
文件内容不经过会话上下文（115 个测试文件约 1MB，用 MCP push_files 内联会撑爆上下文）。

token 来源与 MCP 服务同源：~/.workbuddy/mcp.json -> mcpServers.github-pat.env.GITHUB_PERSONAL_ACCESS_TOKEN。

用法：
    python probe_out/push_a_final.py <manifest.json>
manifest.json = {"message": "...", "files": ["backend/tests/xxx.py", ...]}
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import httpx

OWNER = "simontang6868"
REPO = "DeepQuant"
BRANCH = "workbuddy/a-2026-10-18-final"  # 默认分支，manifest 里可用 "branch" 覆盖
API = "https://api.github.com"
ROOT = Path(r"D:\DeepQuant power by txm\DeepQuant-main")

BLOCKED_PREFIX = (
    "data/",
    "node_modules/",
    "dist/",
    ".venv",
    "venv/",
    "frontend/node_modules/",
    "backend/data/",
    "gate1_out/",
)
BLOCKED_EXACT = {"backend/data/backtests.db", "NIGHT_PROGRESS.md"}


def load_token() -> str:
    cfg = json.loads(io.open(os.path.expanduser("~/.workbuddy/mcp.json"), encoding="utf-8").read())
    return cfg["mcpServers"]["github-pat"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]


def main() -> None:
    manifest = json.loads(io.open(sys.argv[1], encoding="utf-8").read())
    message = manifest["message"]
    rels = manifest["files"]
    branch = manifest.get("branch", BRANCH)
    print("target branch:", branch)

    safe = []
    for rel in rels:
        if rel in BLOCKED_EXACT or any(rel.startswith(p) for p in BLOCKED_PREFIX):
            print("SKIP(禁止推):", rel)
            continue
        p = ROOT / rel
        if not p.exists():
            print("SKIP(不存在):", rel)
            continue
        safe.append(rel)
    print(f"待推 {len(safe)} 个文件，共 {sum((ROOT / r).stat().st_size for r in safe)} 字节")

    tok = load_token()
    h = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }

    r = httpx.get(f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/{branch}", headers=h, timeout=30)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    head = r.json()["object"]["sha"]
    print("base HEAD", head)

    r = httpx.get(f"{API}/repos/{OWNER}/{REPO}/git/commits/{head}", headers=h, timeout=30)
    assert r.status_code == 200, r.text[:300]
    base_tree = r.json()["tree"]["sha"]

    tree = []
    for rel in safe:
        raw = (ROOT / rel).read_bytes()
        rr = httpx.post(
            f"{API}/repos/{OWNER}/{REPO}/git/blobs",
            headers=h,
            content=json.dumps(
                {"content": raw.decode("utf-8"), "encoding": "utf-8"}
            ).encode("utf-8"),
            timeout=60,
        )
        if rr.status_code != 201:
            print("BLOB FAIL", rel, rr.status_code, rr.text[:200])
            sys.exit(1)
        tree.append(
            {"path": rel, "mode": "100644", "type": "blob", "sha": rr.json()["sha"]}
        )
    print("blobs 创建完成", len(tree))

    rr = httpx.post(
        f"{API}/repos/{OWNER}/{REPO}/git/trees",
        headers=h,
        content=json.dumps({"base_tree": base_tree, "tree": tree}).encode("utf-8"),
        timeout=60,
    )
    assert rr.status_code == 201, (rr.status_code, rr.text[:300])
    new_tree = rr.json()["sha"]
    print("new tree", new_tree)

    rr = httpx.post(
        f"{API}/repos/{OWNER}/{REPO}/git/commits",
        headers=h,
        content=json.dumps({"message": message, "tree": new_tree, "parents": [head]}).encode("utf-8"),
        timeout=60,
    )
    assert rr.status_code == 201, (rr.status_code, rr.text[:300])
    commit = rr.json()["sha"]
    print("commit", commit)

    rr = httpx.patch(
        f"{API}/repos/{OWNER}/{REPO}/git/refs/heads/{branch}",
        headers=h,
        content=json.dumps({"sha": commit}).encode("utf-8"),
        timeout=60,
    )
    assert rr.status_code == 200, (rr.status_code, rr.text[:300])
    print("branch updated ->", commit)


if __name__ == "__main__":
    main()
