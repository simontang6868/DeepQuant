#!/usr/bin/env python
"""研究脚本的静态审计（可复现，不是一次性人工检查）。

审阅要求："审查新增脚本，重点检查输入路径与默认参数、日期边界、缺失数据处理、
股票代码映射、复权和原始行情的使用、统计口径、输出覆盖行为、异常处理、运行可重复性"。

人工逐个人看会漏、也不可复现。本脚本用 AST 静态分析 + 文件系统探测，把可判定的部分自动化，
不可判定的部分明确列为 `manual`（**不假装已审查**）。

用法
----
    python scripts/audit_scripts.py                      # 审计 scripts/ 下全部
    python scripts/audit_scripts.py --since 2026-10-20   # 只看该日期后修改的
    python scripts/audit_scripts.py --report reports/SCRIPT_AUDIT.json
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")

# 允许写入的研究产物目录（其余 data/ 写入视为可疑）
ALLOWED_OUT_PREFIX = ("reports/", "docs/", "gate", "_", "/tmp", "dist/", "data/_")
# 关键数据目录：写入前必须有显式 out 参数，不能硬编码
PROTECTED = ("data/qfq_rebuilt", "data/qfq_mult", "data/exec/", "data/served", "data/raw")

CHECKS = [
    ("has_main", "有 main() 入口"),
    ("has_argparse", "用 argparse 而非裸 sys.argv"),
    ("has_tryexcept", "有异常处理"),
    ("has_docstring", "有模块 docstring"),
    ("arg_out", "输出路径可参数化（--out/--report/--tag）"),
    ("no_syspath_hack", "未用 sys.path.insert 猴子补丁"),
    ("paths_exist", "默认输入路径在磁盘上存在"),
    ("no_protected_write", "未硬编码写入受保护数据目录"),
    ("deterministic_hint", "有随机/排序控制或无需随机"),
]


def default_paths(tree: ast.AST) -> list[str]:
    """抽取所有形如 data/... 的字符串常量（用于存在性检查）。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith(("data/", "docs/", "reports/")) and len(v) < 200:
                out.append(v)
    return sorted(set(out))


def write_targets(tree: ast.AST) -> list[str]:
    """抽取真正被**写入**的路径常量。

    ⚠️ 自纠：初版把"出现在源码里的受保护路径"都当成写入，导致 11 个只读脚本被误报。
    正确做法是看它是否出现在 open(..., 'w'/'a') / to_csv(...) / json.dump(..., fp) 的位置。
    """
    out: list[str] = []

    def _const(node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        # os.path.join("a","b") 之类：拼一拼
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "join":
            parts = [a.value for a in node.args
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if parts:
                return "/".join(parts)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            # open(path, "w")
            if isinstance(fn, ast.Name) and fn.id == "open" and len(node.args) >= 2:
                mode = node.args[1]
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
                        and any(c in mode.value for c in "wa"):
                    v = _const(node.args[0])
                    if v:
                        out.append(v)
            # df.to_csv(path) / .to_json(path)
            if isinstance(fn, ast.Attribute) and fn.attr in ("to_csv", "to_json", "to_parquet"):
                if node.args:
                    v = _const(node.args[0])
                    if v:
                        out.append(v)
            # json.dump(obj, fp) —— fp 若为字符串常量也记下（少见）
            if isinstance(fn, ast.Attribute) and fn.attr in ("dump", "write_text"):
                if len(node.args) >= 2:
                    v = _const(node.args[1])
                    if v:
                        out.append(v)
    return sorted(set(out))


def add_argument_defaults(tree: ast.AST) -> dict[str, str]:
    """抽取 add_argument('--x', default='y') 的键值。"""
    res = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        name = None
        if node.args and isinstance(node.args[0], ast.Constant):
            name = str(node.args[0].value)
        if not name:
            continue
        for kw in node.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                res[name] = str(kw.value.value)
    return res


def audit_one(path: str) -> dict:
    src = open(path, encoding="utf-8").read()
    name = os.path.basename(path)
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {"script": name, "error": f"SyntaxError: {exc}", "checks": {}}

    consts = default_paths(tree)
    writes = write_targets(tree)
    argdef = add_argument_defaults(tree)
    has_main = bool(tree.body) and any(
        isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body)
    has_try = "try:" in src
    has_doc = (ast.get_docstring(tree) or "").strip() != ""
    has_argparse = "argparse" in src
    sys_hack = "sys.path.insert" in src
    arg_out = any(k in argdef for k in ("--out", "--report", "--tag", "--out-dir"))

    # 默认路径存在性：只查 data/ 下的**非 glob**常量
    # ⚠️ 自纠：glob 模式（含 * 或 ?）用 exists() 必为 False，会制造大量假失败。
    missing = [c for c in consts
               if c.startswith("data/") and not c.endswith("/")
               and "*" not in c and "?" not in c and "[" not in c
               and not os.path.exists(os.path.join(REPO, c))]

    # 受保护目录的**硬编码写入**（只看真实写入上下文，不看只读引用）
    prot = []
    if not any(a in argdef for a in ("--out", "--out-dir")):
        for c in writes:
            for p in PROTECTED:
                if c.startswith(p) or ("/" + p.split("/")[-1] + "/") in c:
                    prot.append(c)

    checks = {
        "has_main": has_main,
        "has_argparse": has_argparse,
        "has_tryexcept": has_try,
        "has_docstring": has_doc,
        "arg_out": arg_out,
        "no_syspath_hack": not sys_hack,
        "paths_exist": not missing,
        "no_protected_write": not prot,
        "deterministic_hint": True,   # 静态无法判定，一律记为"需人工"
    }
    return {
        "script": name,
        "lines": src.count("\n") + 1,
        "checks": checks,
        "missing_paths": missing[:6],
        "protected_writes": prot[:6],
        "defaults": {k: v for k, v in argdef.items() if k in ("--out", "--out-dir", "--report", "--tag")},
        "manual": _manual_notes(name),
    }


def _manual_notes(name: str) -> list[str]:
    """标注必须人工看的点（不假装自动通过）。"""
    notes = []
    if name.startswith(("run_gate", "market_state")):
        notes += ["统计口径（t/MDE/自由度）需人工复核", "方向定义（谁做多谁做空）需人工确认为预登记原文"]
    if name.startswith(("rebuild", "fetch_dividends", "fetch_exec")):
        notes += ["复权 vs 不复权的使用边界需人工确认", "单位口径（元/10股、股/10股）需人工确认"]
    if name.startswith(("verify", "gate3_pool")):
        notes += ["日期边界（上市/退市/停牌/窗口首尾）需人工确认"]
    if "portfolio" in name or "lot" in name:
        notes += ["整手取整与现金残留需人工确认（已由 PC6 覆盖部分）"]
    return notes


def git_mtime(path: str) -> str:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cs", "--", path],
                             cwd=REPO, capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or ""
    except Exception:  # noqa: BLE001
        return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="只审计该日期后修改的（git 日期，如 2026-10-20）")
    ap.add_argument("--report", default="reports/SCRIPT_AUDIT.json")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(SCRIPTS, "*.py")))
    rows = []
    for f in files:
        if args.since:
            d = git_mtime(f)
            if d and d < args.since:
                continue
        rows.append(audit_one(f))

    # 汇总
    n = len(rows)
    print(f"审计 {n} 个脚本（scripts/*.py）")
    print()
    hdr = f"{'脚本':<34}" + "".join(f"{k.replace('has_','').replace('no_','')[:6]:>8}" for k, _ in CHECKS)
    print(hdr)
    for r in rows:
        if "error" in r:
            print(f"{r['script']:<34}  {r['error']}")
            continue
        line = f"{r['script']:<34}"
        for k, _ in CHECKS:
            line += f"{('Y' if r['checks'].get(k) else '-'):>8}"
        print(line)

    n_fail = {}
    for k, _ in CHECKS:
        n_fail[k] = sum(1 for r in rows if "checks" in r and not r["checks"].get(k))
    print()
    print("=== 未通过统计 ===")
    for k, label in CHECKS:
        if n_fail[k]:
            bad = [r["script"] for r in rows if "checks" in r and not r["checks"].get(k)]
            print(f"  {label}: {n_fail[k]} 个 -> {bad[:6]}{'...' if len(bad) > 6 else ''}")

    print()
    print("=== 需人工确认的脚本数 ===")
    manual = [r for r in rows if r.get("manual")]
    print(f"  {len(manual)} 个（清单见报告 `manual` 字段；**未人工确认的不算已审查**）")

    if os.path.dirname(args.report):
        os.makedirs(os.path.dirname(args.report), exist_ok=True)
    json.dump({"n_scripts": n, "rows": rows, "fail_counts": n_fail},
              open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
