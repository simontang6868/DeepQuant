"""对比本地 A 线产出与 GitHub 分支 workbuddy/a-2026-10-18-final 的差异。

只读对比，不做任何写操作。token 从 ~/.workbuddy/mcp.json 读取（与 MCP 服务同源同意图-authorization）。
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path

import httpx  # 本地 dq_demo venv 里有 httpx

OWNER = "simontang6868"
REPO = "DeepQuant"
BRANCH = "workbuddy/a-2026-10-18-final"
API = "https://api.github.com"

REPO_ROOT = Path(r"D:\DeepQuant power by txm\DeepQuant-main")
BLOCKED_PREFIX = ("data/", "node_modules/", "dist/", ".venv", "venv/", "frontend/node_modules/")
BLOCKED_EXACT = {"backend/data/backtests.db", "NIGHT_PROGRESS.md"}


def load_token() -> str:
    cfg = json.loads(io.open(os.path.expanduser("~/.workbuddy/mcp.json"), encoding="utf-8").read())
    return cfg["mcpServers"]["github-pat"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]


def main() -> None:
    tok = load_token()
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    r = httpx.get(f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", headers=h, timeout=30)
    print("ref status", r.status_code)
    if r.status_code != 200:
        print(r.text[:500])
        return
    head = r.json()["object"]["sha"]
    print("branch HEAD", head)

    r = httpx.get(
        f"{API}/repos/{OWNER}/{REPO}/git/trees/{head}",
        headers=h,
        params={"recursive": "1"},
        timeout=60,
    )
    print("tree status", r.status_code)
    tree = r.json()
    remote = {}
    for item in tree.get("tree", []):
        if item["type"] == "blob":
            remote[item["path"]] = item["sha"]
    print("remote blobs", len(remote))

    # 本地 A 线候选文件
    candidates = []
    for sub, pat in ((Path("backend/tests"), "*.py"), (Path("docs"), "*.md")):
        for p in sorted((REPO_ROOT / sub).rglob(pat)):
            rel = p.relative_to(REPO_ROOT).as_posix()
            if any(rel.startswith(b) for b in BLOCKED_PREFIX) or rel in BLOCKED_EXACT:
                continue
            candidates.append((rel, p))
    print("local candidates", len(candidates))

    missing, same, diff = [], [], []
    for rel, p in candidates:
        raw = p.read_bytes()
        # git blob sha1 = sha1("blob <len>\0" + content)
        import hashlib

        blob = hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()
        if rel not in remote:
            missing.append((rel, len(raw)))
        elif remote[rel] == blob:
            same.append(rel)
        else:
            diff.append((rel, len(raw)))

    print("\n== MISSING (远端没有) ==", len(missing), "文件", sum(s for _, s in missing), "字节")
    print("== DIFF (内容不同) ==", len(diff), "文件", sum(s for _, s in diff), "字节")
    print("== SAME ==", len(same))

    out = {"head": head, "missing": missing, "diff": diff}
    io.open("probe_out/a_line_push_diff.json", "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1)
    )
    print("\n写入 probe_out/a_line_push_diff.json")

    print("\n--- MISSING 明细 ---")
    for rel, s in missing:
        print(f"{s:>8}  {rel}")
    print("\n--- DIFF 明细 ---")
    for rel, s in diff:
        print(f"{s:>8}  {rel}")


if __name__ == "__main__":
    main()
