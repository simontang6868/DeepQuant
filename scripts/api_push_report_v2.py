#!/usr/bin/env python
"""Push local file changes to GitHub via the Git Data API (git 直连失败时的备用通道).

背景：本机代理（127.0.0.1:56541）对 github.com 反复 502 / connection reset，
git push/fetch 时通时断。本脚本走 api.github.com，用 PAT 直接建
blob -> tree -> commit -> ref，把指定文件的新内容推成远端头上的一个 commit。

安全约定：
- token 只从 CODEBUDDY_MCP_CONFIG 的 github-pat 读取，**绝不打印**；
- 远端头与 --remote-head（或脚本内 EXPECTED_REMOTE_HEAD）不一致时**中止**；
- **树一致才算成功**：上传字节直接从本地 git 对象库读
  （`git cat-file blob HEAD:<path>`）——git 存什么就传什么，从根上绕开
  autocrlf / .gitattributes / 行尾的全部陷阱；再用 API 建树，与本地 commit
  的 tree 逐 sha 比对，**不一致就不建 commit**。

用法：
    python scripts/api_push_report_v2.py <file> [<file> ...] \
        --remote-head <远端当前40位sha> --message-file <提交信息文件> \
        [--expect-tree <本地commit的40位tree sha>] [--no-tree-check]

注意：文件必须**先本地 commit** 再推送（本脚本推的就是本地 commit 的内容）。
API 建出的 commit 与本地 commit 树相同但 sha 不同（GitHub 原始对象里有
API 不暴露的字节，本地无法复现）。属预期，不是错误；代理恢复后跑一次
`git fetch` 即可让本地跟踪引用对齐。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
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


def read_canonical(path: str, ref: str = "HEAD") -> bytes:
    """从本地 git 对象库读该路径在 ref 下的规范字节——git 存什么就上传什么。

    为什么不能直接读工作区文件（2026-10-22 实测教训）：
    Windows 文本模式写出的文件带 CRLF；git 提交时按 autocrlf/.gitattributes
    归一（*.sha256 按 .gitattributes 是 -text 不归一，普通 md/py 归一成 LF），
    API 若上传工作区原文就会与本地 commit 的树对不上。读对象库零启发式。
    代价：文件必须先 commit。
    """
    p = path.replace(os.sep, "/")
    try:
        out = subprocess.run(["git", "cat-file", "blob", f"{ref}:{p}"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SystemExit(f"git cat-file 失败：{path}: {e}")
    if out.returncode != 0:
        raise SystemExit(
            f"路径不在 {ref} 中（先 commit 再推送）：{path}\n"
            f"{out.stderr.decode(errors='replace')[:300]}")
    return out.stdout


def local_head_tree() -> str | None:
    """本地 HEAD 的 tree sha（用于树一致性校验）；不在 git 仓库则 None。"""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD^{tree}"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSrror, subprocess.TimeoutExpired):
        pass
    return None


def parse_args(argv: list[str]) -> tuple[list[str], str, str, str, bool]:
    files: list[str] = []
    remote_head = read_expected_head()
    message_file = ""
    expect_tree = ""
    tree_check = True
    i = 0
    while i < len(argv):
        if argv[i] == "--remote-head":
            remote_head = argv[i + 1]
            i += 2
        elif argv[i] == "--message-file":
            message_file = argv[i + 1]
            i += 2
        elif argv[i] == "--expect-tree":
            expect_tree = argv[i + 1]
            i += 2
        elif argv[i] == "--no-tree-check":
            tree_check = False
            i += 1
        else:
            files.append(argv[i])
            i += 1
    if not files or not message_file:
        raise SystemExit(__doc__)
    return files, remote_head, message_file, expect_tree, tree_check


def main() -> int:
    files, expected, message_file, expect_tree, tree_check = parse_args(sys.argv[1:])
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

    if tree_check and not expect_tree:
        expect_tree = local_head_tree() or ""
        if expect_tree:
            print(f"local HEAD tree: {expect_tree}（树一致性校验基准）")

    tree_items = []
    for path in files:
        norm = read_canonical(path)
        blob = api("POST", "/git/blobs",
                   {"content": base64.b64encode(norm).decode(), "encoding": "base64"}, tok)
        print(f"blob {path}: {blob['sha'][:12]} ({len(norm)} bytes, 取自本地对象库)")
        tree_items.append({"path": path.replace(os.sep, "/"), "mode": "100644",
                           "type": "blob", "sha": blob["sha"]})

    tree = api("POST", "/git/trees", {"base_tree": base_tree, "tree": tree_items}, tok)
    print("new tree:", tree["sha"])
    if tree_check:
        if not expect_tree:
            print("!! 取不到本地 HEAD tree，无法校验；如确认无误请加 --no-tree-check 重试")
            return 4
        if tree["sha"] != expect_tree:
            print(f"!! 树不一致：API {tree['sha']} vs 本地 {expect_tree}")
            print("   未创建 commit、未动 ref。请检查文件内容/行尾/文件清单。")
            return 3
        print("树一致性校验通过（与本地 commit 逐 sha 相同）")

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
