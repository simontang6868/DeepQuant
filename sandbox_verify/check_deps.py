"""依赖完备性冒烟：按 requirements.txt 逐条核对「声明 / 已装 / 版本」。

用途
----
交付包被换环境安装后，最先出问题的不是代码，是依赖：
- 少装一个包 → 某些测试红；
- 装了但版本漂 → 数字对不上；
- 靠传递依赖「碰巧装上」→ 换个装法就消失（本项目真实踩过：tabulate）。

本脚本只读，不改环境、不装包。它回答一个问题：
**「这台机器上的依赖，和 requirements.txt 声明的是不是同一套？」**

用法
----
    python sandbox_verify/check_deps.py
    python sandbox_verify/check_deps.py --requirements backend/requirements.txt --strict

退出码
------
    0  全部满足（含允许的可选缺失）
    1  有必需依赖缺失
    2  全部存在但版本与 pin 不一致

约定
----
- `akshare` 视为**可选**：README §1.1 明确允许不装它跑前 7 条清单。
  默认缺了只 WARN；加 `--strict` 才当失败。
- `[standard]` 这类 extras 会展开成实际分发包逐个核对（uvicorn[standard] 在 Windows 上不含 uvloop）。
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import re
import sys
from pathlib import Path

# 可选依赖：缺失不算失败（默认），但必须显式报告，不能静默。
OPTIONAL = {"akshare"}

# extras -> 实际分发包。只登记本项目用到的 extras，不做通用解析。
EXTRAS: dict[str, list[str]] = {
    "uvicorn": ["httptools", "watchfiles", "websockets", "python-dotenv", "PyYAML"],
}
# uvloop 在 Windows 上按环境标记排除，单独处理。
PLATFORM_EXTRAS: dict[str, dict[str, str]] = {
    "uvicorn": {"uvloop": "sys_platform != 'win32'"},
}

REQ_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+)\s*(?P<extras>\[[^\]]+\])?\s*(?P<spec>.*)$")


def parse_requirements(path: Path) -> list[tuple[str, str, list[str]]]:
    """返回 [(分发名, 版本约束, extras 列表)]；跳过注释、空行、-r/-c 等指令行。"""
    out: list[tuple[str, str, list[str]]] = []
    if not path.is_file():
        raise SystemExit(f"找不到依赖清单：{path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = REQ_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        spec = (m.group("spec") or "").strip()
        extras_raw = (m.group("extras") or "").strip("[]")
        extras = [e.strip() for e in extras_raw.split(",") if e.strip()]
        out.append((name, spec, extras))
    return out


def installed_version(name: str) -> str | None:
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


def check_pin(actual: str | None, spec: str) -> str:
    """仅对 `==x.y.z` 做严格比对；其它约束只判断是否存在。"""
    if actual is None:
        return "missing"
    s = spec.strip()
    if s.startswith("=="):
        want = s[2:].strip()
        return "ok" if actual == want else f"mismatch(want {want})"
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description="依赖完备性冒烟")
    ap.add_argument("--requirements", default="backend/requirements.txt")
    ap.add_argument("--strict", action="store_true", help="把可选依赖缺失也算失败")
    args = ap.parse_args()

    reqs = parse_requirements(Path(args.requirements))
    rows: list[tuple[str, str, str, str]] = []
    missing: list[str] = []
    mismatch: list[str] = []

    for name, spec, extras in reqs:
        actual = installed_version(name)
        status = check_pin(actual, spec)
        optional = name.lower() in OPTIONAL
        if status == "missing":
            status = "OPTIONAL-MISSING" if optional and not args.strict else "MISSING"
            if status == "MISSING":
                missing.append(name)
        elif status.startswith("mismatch"):
            mismatch.append(name)
        rows.append((name, spec or "(任意)", actual or "-", status))

        for extra in extras:
            for dep in EXTRAS.get(name, []):
                av = installed_version(dep)
                st = "ok" if av else ("OPTIONAL-MISSING" if optional and not args.strict else "MISSING")
                if st == "MISSING":
                    missing.append(dep)
                rows.append((f"{name}[{extra}] → {dep}", "(extras)", av or "-", st))
            for dep, marker in PLATFORM_EXTRAS.get(name, {}).items():
                applies = sys.platform != "win32"
                if not applies:
                    rows.append((f"{name}[{extra}] → {dep}", marker, "N/A(this platform)", "skipped"))
                else:
                    av = installed_version(dep)
                    st = "ok" if av else "MISSING"
                    if st == "MISSING":
                        missing.append(dep)
                    rows.append((f"{name}[{extra}] → {dep}", "(extras)", av or "-", st))

    width = max(len(r[0]) for r in rows) + 2
    print(f"Python {sys.version.split()[0]}  ({sys.platform})")
    print(f"清单：{Path(args.requirements).resolve()}")
    print(f"{'包'.ljust(width)} {'声明':<16} {'已装':<16} 状态")
    print("-" * (width + 50))
    for name, spec, actual, status in rows:
        print(f"{name.ljust(width)} {spec:<16} {actual:<16} {status}")

    print()
    if missing:
        print(f"[FAIL] 缺失必需依赖 {len(missing)} 个：{', '.join(sorted(set(missing)))}")
        print("       按 README §1.1 装齐后重跑；不要为了跑通去改 requirements.txt 的 pin。")
    if mismatch:
        print(f"[WARN] 版本与 pin 不一致 {len(mismatch)} 个：{', '.join(sorted(set(mismatch)))}")
        print("       版本不一致时，测试通过数与原环境不可直接对表，需在报告中写明。")
    if not missing and not mismatch:
        print("[PASS] 依赖与 requirements.txt 完全一致。")

    if missing:
        return 1
    if mismatch:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
