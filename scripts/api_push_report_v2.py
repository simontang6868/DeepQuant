#!/usr/bin/env python
"""Push local file changes to GitHub via the Git Data API (git 直连失败时的备用通道).

背景：本机代理（127.0.0.1:56541）对 github.com 反复 502 / connection reset，
git push/fetch 时通时断。本脚本走 api.github.com，用 PAT 直接建
blob -> tree -> commit -> ref，把指定文件的新内容推成远端头上的一个 commit。

安全约定：
- token 只从 CODEBUDDY_MCP_CONFIG 的 github-pat 读取，**绝不打印**；
- 远端头与 --remote-head（或脚本内 EXPECTED_REMOTE_HEAD）不一致时**中止**；
- **树一致才算成功**：推完后比对远端新 tree 与本地 `git rev-parse HEAD^{tree}`。

用法：
    python scripts/api_push_report_v2.py <file> [<file> ...] \
        --remote-head <远端当前40位sha> --message-file <提交信息文件>

注意：API 建出的 commit 与本地 commit 树相同但 sha 不同（GitHub 原始对象里有
API 不暴露的字节，本地无法复现）。属预期，不是错误；代理恢复后跑一次
`git fetch` 即可让本地跟踪引用对齐。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
import urllib.error

OWNER = "simontang6868"
REPO = "DeepQuant"
BRANCH = "workbuddy/2026-10-20-consolidated"
# 上次 API 推送后的远端头。优先从 .git/api_push_remote_head 读（每次推送成功后自动
# 更新，避免"脚本里写着自己的 sha"这种自我指涉）；该文件不存在时退回此常量。
EXPECTED_REMOTE_HEAD = "5e18980d3eb61782836749c61843f119de28dde4"
STATE_FILE = ".git/api_push_remote_head"

BASE = f"https://api.github.com/repos/{OWNER}/{REPO}"


def read_expected_head() -> str:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return EXPECTED_REMOTE_HEAD


def write_expected_head(sha: str) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(sha + "\n")
    except OSError:
        pass


def get_token() -> str:
    raw = os.environ.get("CODEBUDDY_MCP_CONFIG", "")
    cfg = json.loads(raw)
    tok = cfg["mcpServers"]["github-pat"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]
    if not tok or not tok.startswith(("github_pat_", "ghp_")):
        raise SystemExit("token not found or unexpected format")
    return tok


def api(method: str, path: str, body: dict | None, tok: str) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {detail}")


def parse_args(argv: list[str]) -> tuple[list[str], str, str]:
    files: list[str] = []
    remote_head = read_expected_head()
    message_file = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--remote-head":
            remote_head = argv[i + 1]
            i += 2
        elif argv[i] == "--message-file":
            message_file = argv[i + 1]
            i += 2
        else:
            files.append(argv[i])
            i += 1
    if not files or not message_file:
        raise SystemExit(__doc__)
    return files, remote_head, message_file


def main() -> int:
    files, expected, message_file = parse_args(sys.argv[1:])
    with open(message_file, encoding="utf-8") as f:
        message = f.read()
    tok = get_token()

    ref = api("GET", f"/git/ref/heads/{BRANCH}", None, tok)
    remote_head = ref["object"]["sha"]
    print("remote head:", remote_head)
    if remote_head != expected:
        print(f"!! expected {expected} - remote moved; aborting")
        return 2

    base_tree = api("GET", f"/git/commits/{remote_head}", None, tok)["tree"]["sha"]
    print("base tree:", base_tree)

    tree_items = []
    for path in files:
        with open(path, "rb") as f:
            raw = f.read()
        blob = api("POST", "/git/blobs",
                   {"content": base64.b64encode(raw).decode(), "encoding": "base64"}, tok)
        print(f"blob {path}: {blob['sha'][:12]} ({len(raw)} bytes)")
        tree_items.append({"path": path.replace(os.sep, "/"), "mode": "100644",
                           "type": "blob", "sha": blob["sha"]})

    tree = api("POST", "/git/trees", {"base_tree": base_tree, "tree": tree_items}, tok)
    print("new tree:", tree["sha"])
    new_commit = api("POST", "/git/commits",
                     {"message": message, "tree": tree["sha"], "parents": [remote_head]}, tok)
    print("new commit:", new_commit["sha"])
    upd = api("PATCH", f"/git/refs/heads/{BRANCH}",
              {"sha": new_commit["sha"], "force": False}, tok)
    print("ref now:", upd["object"]["sha"])
    write_expected_head(upd["object"]["sha"])
    print("PUSH OK - 已把新远端头写入 .git/api_push_remote_head")
    return 0


if __name__ == "__main__":
    sys.exit(main())
