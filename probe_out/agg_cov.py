"""从 coverage json 聚合出分模块覆盖率表（排除 research 子包）。"""
from __future__ import annotations

import io
import json
from collections import OrderedDict

data = json.loads(io.open(r"D:\DeepQuant power by txm\DeepQuant-main\probe_out\coverage_2026-10-18.json", encoding="utf-8").read())

groups = OrderedDict()
tot = {"stmts": 0, "miss": 0}
res = {"stmts": 0, "miss": 0}
rows = []
for path, f in sorted(data["files"].items()):
    s = f["summary"]
    p = path.replace("\\", "/")
    if not p.startswith("src/deepquant/"):
        continue
    rel = p[len("src/deepquant/"):]
    rows.append((rel, s["num_statements"], s["missing_lines"], s["percent_covered"], f.get("missing_lines")))
    tot["stmts"] += s["num_statements"]
    tot["miss"] += s["missing_lines"]
    if rel.startswith("research/"):
        res["stmts"] += s["num_statements"]
        res["miss"] += s["missing_lines"]
        continue
    grp = rel.split("/")[0] if "/" in rel else "(root)"
    g = groups.setdefault(grp, {"stmts": 0, "miss": 0})
    g["stmts"] += s["num_statements"]
    g["miss"] += s["missing_lines"]

print("TOTAL  stmts=%d miss=%d cover=%.2f%%" % (tot["stmts"], tot["miss"], 100 * (tot["stmts"] - tot["miss"]) / tot["stmts"]))
print("RESEARCH stmts=%d miss=%d" % (res["stmts"], res["miss"]))
nr_s = tot["stmts"] - res["stmts"]
nr_m = tot["miss"] - res["miss"]
print("NON-RESEARCH stmts=%d miss=%d cover=%.2f%%" % (nr_s, nr_m, 100 * (nr_s - nr_m) / nr_s))
print()
print("| 包 | Stmts | Miss | Cover |")
print("|---|---:|---:|---:|")
for g, v in sorted(groups.items()):
    cov = 100 * (v["stmts"] - v["miss"]) / v["stmts"] if v["stmts"] else 100.0
    print("| %s | %d | %d | %.1f%% |" % (g, v["stmts"], v["miss"], cov))

print()
print("== 非 100% 的模块（按 miss 降序，排除 research）==")
print("| 模块 | Stmts | Miss | Cover | Missing 行 |")
print("|---|---:|---:|---:|---|")
for rel, st, mi, pc, ml in sorted(rows, key=lambda r: -r[2]):
    if rel.startswith("research/") or mi == 0:
        continue
    miss_str = ",".join(str(x) for x in ml)[:180]
    print("| %s | %d | %d | %d%% | %s |" % (rel, st, mi, round(pc), miss_str))

print()
print("== 100% 模块清单（排除 research）==")
hundred = [r[0] for r in rows if r[2] == 0 and not r[0].startswith("research/")]
print(len(hundred), "个")
print("== 0% 模块清单 ==")
print([r[0] for r in rows if r[1] > 0 and r[3] == 0])
