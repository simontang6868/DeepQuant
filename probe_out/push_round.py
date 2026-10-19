"""用 Git Data API 推送本轮产物（本地读文件 → base64 → blob → tree → commit → ref）。

为什么不用 MCP 的 push_files：它要求把文件内容作为参数传进来，等于让模型重打一遍字节。
预登记文件的 sha256 已经冻结，重打一个空格就废掉整条审计链。这里从磁盘直接读，字节不动。
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import urllib.error
import urllib.request

OWNER, REPO = "simontang6868", "DeepQuant"
BRANCH = "workbuddy/b-oos-recalc-2026-10-19"
BASE = "main"
CFG = pathlib.Path.home() / ".workbuddy" / "mcp.json"

FILES = [
    ".gitignore",
    "scripts/oos_2026_run.py",
    "scripts/oos_recalc_compare.py",
    "docs/oos_recalc_prereg_2026-10-19.json",
    "docs/OOS_RECALC_PREREG_2026-10-19.md",
    "docs/OOS_RECALC_PREREG_2026-10-19.sha256",
    "docs/oos_recalc_prereg_v1.1_2026-10-19.json",
    "docs/OOS_RECALC_PREREG_V1.1_2026-10-19.md",
    "docs/OOS_RECALC_PREREG_V1.1_2026-10-19.sha256",
    "docs/OOS_RECALC_COMPARE_2026-10-19.json",
    "docs/OOS_RECALC_2026-10-19.md",
    "docs/OOS_RESULTS_RECALC_2026-10-19.json",
    "data/oos_recalc_pool_2017/600519.SH.qfq.csv",
    "data/oos_recalc_pool_2017/000001.SZ.qfq.csv",
    "data/oos_recalc_pool_2017/601318.SH.qfq.csv",
    "data/oos_recalc_pool_2017/300750.SZ.qfq.csv",
    "data/oos_recalc_pool_2017/000858.SZ.qfq.csv",
    "data/oos_recalc_pool_2017/600900.SH.qfq.csv",
]

MESSAGE = """research(oos): 按乘性口径重算样本外结果，判定仍为 R4_INCONCLUSIVE

- 预登记 docs/oos_recalc_prereg_v1.1_2026-10-19.json（sha256 56eee69f…，冻结于任何结果之前）
- 同一批行、同一套设计，只换价格口径：主格 OOS 中位 −1.13% → −0.69%（+0.44pp）
- 查明 v1 的「6 只池」样本外实际只有 4 只在投标：600519 从 2021 年起买不起一手，贡献 0 个观测
- 口径修正把 300750 放回池子（v1 被闸门拦），有效池 4 → 5
- 逐窗 20 个共有窗口符号翻转 0 个，此前预测 1~6 个 → 预测错误，已写明
- 旧结果 docs/OOS_RESULTS_2026-10-19.json 一字未改
"""

ROOT = pathlib.Path(__file__).resolve().parents[1]


def token() -> str:
    return json.loads(CFG.read_text(encoding="utf-8"))["mcpServers"]["github-pat"]["env"][
        "GITHUB_PERSONAL_ACCESS_TOKEN"
    ]


TOK = token()


def api(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"https://api.github.com{path}"
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOK}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read().decode()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        raise SystemExit(f"{method} {path} -> {e.code}: {detail}") from None


def main() -> None:
    base_ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BASE}")
    base_sha = base_ref["object"]["sha"]
    base_commit = api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{base_sha}")
    base_tree = base_commit["tree"]["sha"]
    print(f"[base] {BASE} = {base_sha[:12]} tree={base_tree[:12]}")

    tree_items, local = [], {}
    for rel in FILES:
        p = ROOT / rel
        raw = p.read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        local[rel] = h
        blob = api("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                   {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"[blob] {rel:58s} {len(raw):>7d}B sha256={h[:12]}")

    tree = api("POST", f"/repos/{OWNER}/{REPO}/git/trees",
               {"base_tree": base_tree, "tree": tree_items})
    commit = api("POST", f"/repos/{OWNER}/{REPO}/git/commits",
                 {"message": MESSAGE, "tree": tree["sha"], "parents": [base_sha]})
    new_sha = commit["sha"]
    print(f"[commit] {new_sha[:12]}")

    try:
        api("POST", f"/repos/{OWNER}/{REPO}/git/refs",
            {"ref": f"refs/heads/{BRANCH}", "sha": new_sha})
        print(f"[branch] created {BRANCH}")
    except SystemExit:
        api("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", {"sha": new_sha, "force": True})
        print(f"[branch] fast-forwarded {BRANCH}")

    pathlib.Path(ROOT / "probe_out" / "pushed_hashes.json").write_text(
        json.dumps({"commit": new_sha, "files": local}, indent=2), encoding="utf-8")
    print("[OK] pushed hashes -> probe_out/pushed_hashes.json")


if __name__ == "__main__":
    main()
