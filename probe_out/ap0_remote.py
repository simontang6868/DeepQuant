"""A-P0 只读采集：GitHub 远端状态（分支 / 开放 PR / main HEAD）。不做任何写操作。"""
import io, json, os
import httpx

cfg = json.loads(io.open(os.path.expanduser("~/.workbuddy/mcp.json"), encoding="utf-8").read())
tok = cfg["mcpServers"]["github-pat"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]
H = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
API = "https://api.github.com"
OWNER, REPO = "simontang6868", "DeepQuant"
out = {}

r = httpx.get(f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/main", headers=H, timeout=30)
out["main_sha"] = r.json()["object"]["sha"] if r.status_code == 200 else f"ERR {r.status_code}"

r = httpx.get(f"{API}/repos/{OWNER}/{REPO}/branches?per_page=100", headers=H, timeout=30)
out["branches"] = [
    {"name": b["name"], "sha": b["commit"]["sha"]} for b in r.json()
] if r.status_code == 200 else [{"error": r.status_code}]

r = httpx.get(
    f"{API}/repos/{OWNER}/{REPO}/pulls?state=open&per_page=100", headers=H, timeout=30
)
prs = []
if r.status_code == 200:
    for p in r.json():
        prs.append(
            {
                "number": p["number"],
                "title": p["title"],
                "head": p["head"]["ref"],
                "base": p["base"]["ref"],
                "draft": p["draft"],
                "mergeable": p.get("mergeable"),
                "mergeable_state": p.get("mergeable_state"),
                "created_at": p["created_at"],
                "updated_at": p["updated_at"],
                "commits": p.get("commits"),
                "changed_files": p.get("changed_files"),
                "additions": p.get("additions"),
                "deletions": p.get("deletions"),
            }
        )
out["open_prs"] = prs

r = httpx.get(f"{API}/repos/{OWNER}/{REPO}", headers=H, timeout=30)
if r.status_code == 200:
    j = r.json()
    out["repo"] = {
        "default_branch": j["default_branch"],
        "pushed_at": j["pushed_at"],
        "size_kb": j["size"],
        "private": j["private"],
    }

r = httpx.get(f"{API}/repos/{OWNER}/{REPO}/commits?per_page=5", headers=H, timeout=30)
out["recent_commits"] = [
    {"sha": c["sha"][:10], "msg": c["commit"]["message"].splitlines()[0],
     "date": c["commit"]["committer"]["date"]}
    for c in r.json()
] if r.status_code == 200 else []

io.open("probe_out/ap0_remote.json", "w", encoding="utf-8").write(
    json.dumps(out, ensure_ascii=False, indent=1)
)
print("main", out.get("main_sha"))
print("branches", len(out.get("branches", [])))
for b in out.get("branches", []):
    print("  ", b)
print("open PRs", len(out.get("open_prs", [])))
for p in out.get("open_prs", []):
    print(f"  #{p['number']} {p['head']} -> {p['base']} mergeable={p['mergeable']} "
          f"state={p['mergeable_state']} files={p['changed_files']} +{p['additions']}/-{p['deletions']} | {p['title'][:50]}")
print("repo", out.get("repo"))
print("recent:")
for c in out.get("recent_commits", []):
    print("  ", c)
