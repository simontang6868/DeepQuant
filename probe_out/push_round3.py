"""用 Git Data API 推送本轮产物（本地读盘 → base64 → blob → tree → commit → ref）。

不用 MCP push_files 的原因同上一轮：它要求内容作为参数传进来，等于重打一遍字节，
预登记的 sha256 会对不上。这里字节零改动。
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import urllib.error
import urllib.request

OWNER, REPO = "simontang6868", "DeepQuant"
BRANCH = "workbuddy/b-effective-pool-2026-10-19"
BASE = "main"
CFG = pathlib.Path.home() / ".workbuddy" / "mcp.json"

FILES = [
    "backend/src/deepquant/research/effective_pool.py",
    "scripts/oos_2026_run.py",
    "scripts/effective_pool_check.py",
    "scripts/selftest_effective_pool.py",
    "scripts/capital_tier_resolve.py",
    "scripts/long_horizon_caliber_recalc.py",
    "docs/capital_tier_prereg_2026-10-19.json",
    "docs/CAPITAL_TIER_PREREG_2026-10-19.sha256",
    "docs/CAPITAL_TIER_TABLE_2026-10-19.json",
    "docs/CAPITAL_TIER_2026-10-19.md",
    "docs/EFFECTIVE_POOL_GATE_2026-10-19.json",
    "docs/EFFECTIVE_POOL_GATE_2026-10-19.md",
    "docs/LONG_HORIZON_CALIBER_RECALC_2026-10-19.json",
    "docs/LONG_HORIZON_CALIBER_RECALC_2026-10-19.md",
]

MESSAGE = """research: 有效池硬闸门 + 资金档定档 + 长区间口径重算

三件都是上一轮列的待办，本轮全部落地。

1) 有效池闸门（G2/G3 从文档变成代码）
   - 新模块 backend/src/deepquant/research/effective_pool.py（纯计算，无 IO）
   - 判定分三层：缺席（样本外 0 窗可建仓，阻断）/ 部分阻断（记录不阻断）/ 无数据（阻断）
   - 对账：样本外 5 个窗口预测全中，0 误报 0 漏报（严格/宽松判据结论一致）
   - 接进 oos_2026_run.py，--gate-pool {default,off,warn,block}；规格未声明时默认 warn
   - 实测：warn 重跑冻结 v1 规格，145 个窗口键、0 处数值差异、判定不变；block 退出码 2 且不写文件

2) 资金档定档（预登记 ce2feed8…，先冻结后计算，规则只用价格不看收益）
   - 独立账户口径 30 万；等权组合口径 150 万；差 5 倍
   - 四条预测全中；20 万"无一缺席但非全部窗口"，说明缺席/部分阻断必须分开记

3) 长区间口径重算（两池 × 两窗口）
   - 6 只池 2017-2024 加性中位 +1870.4% vs 乘性 +234.9%（差 1635pp）
   - 50 只池同期 +132.3% vs +78.1%；符号翻转 0 只 → 方向不变、幅度全废
   - 极端个案 600809 加性 +71395.8% vs 乘性 +642.9%

自测 9/9（scripts/selftest_effective_pool.py）；全量回归 1992 passed / 6 skipped / 0 failed。
未改动任何冻结结果文件（OOS_RESULTS / OOS_PREREG / STAGE0_* 一字未动）。
"""

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOK = json.loads(CFG.read_text(encoding="utf-8"))["mcpServers"]["github-pat"]["env"][
    "GITHUB_PERSONAL_ACCESS_TOKEN"
]


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
    head = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BASE}")
    base_sha = head["object"]["sha"]
    base_tree = api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{base_sha}")["tree"]["sha"]
    print(f"[base] {BASE} = {base_sha[:12]} tree={base_tree[:12]}")

    items, local = [], {}
    for rel in FILES:
        raw = (ROOT / rel).read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        local[rel] = {"sha256": h, "bytes": len(raw)}
        blob = api("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                   {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"[blob] {rel:60s} {len(raw):>8d}B {h[:12]}")

    tree = api("POST", f"/repos/{OWNER}/{REPO}/git/trees", {"base_tree": base_tree, "tree": items})
    commit = api("POST", f"/repos/{OWNER}/{REPO}/git/commits",
                 {"message": MESSAGE, "tree": tree["sha"], "parents": [base_sha]})
    new_sha = commit["sha"]
    print(f"[commit] {new_sha[:12]}")

    try:
        api("POST", f"/repos/{OWNER}/{REPO}/git/refs",
            {"ref": f"refs/heads/{BRANCH}", "sha": new_sha})
        print(f"[branch] created {BRANCH}")
    except SystemExit:
        api("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
            {"sha": new_sha, "force": True})
        print(f"[branch] fast-forwarded {BRANCH}")

    (ROOT / "probe_out" / "pushed_hashes_round3.json").write_text(
        json.dumps({"commit": new_sha, "branch": BRANCH, "files": local}, indent=2),
        encoding="utf-8")
    print("[OK] hashes -> probe_out/pushed_hashes_round3.json")


if __name__ == "__main__":
    main()
