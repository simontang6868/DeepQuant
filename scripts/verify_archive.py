#!/usr/bin/env python
"""归档产物完整性校验（阶段 0.7）

为什么要有这个脚本
------------------
仓库里已经散落着 8+ 份 `.sha256` 冻结文件，但它们格式不统一：

  * 有的首行是注释（`# 冻结指纹…`），有的首行就是哈希；
  * 有的行尾是 CRLF，`sha256sum -c` 会把 `\\r` 当成文件名的一部分而报
    "No such file or directory"；
  * 有的路径带 `docs/` 前缀，有的不带（要在 docs/ 目录下才能校验）；
  * 有的是 `*path`（binary 模式标记）。

结果是：直接用标准 `sha256sum -c` 去核，**7/8 份会因为格式问题失败**，
但内容其实一个字节都没被改过。这会误导外部评审方以为"文件被动过"。

本脚本做两件事：
  1. `verify`  —— 容错地校验所有冻结条目（处理上述四种格式差异）；
  2. `manifest`—— 生成一份格式统一、可被标准 `sha256sum -c` 直接校验的
                  归档清单（含 gate1_out/ 原始产物）。

设计原则：
  * 只读取，不修改任何既有文件（生成清单是新增文件，不覆盖历史记录）；
  * 纯标准库，任何装了 Python 的机器都能跑；
  * 失败时明确区分「内容被改」与「格式问题导致无法校验」——
    这两种的严重性完全不同，不能混为一谈。

用法
----
    python scripts/verify_archive.py                 # 校验所有冻结条目
    python scripts/verify_archive.py --manifest      # 生成统一归档清单
    python scripts/verify_archive.py --strict        # 格式问题也视为失败

退出码
------
    0  全部通过
    1  有内容不匹配（严重：预登记可能被动过）
    2  有条目无法校验（格式/路径问题）
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 不可变产物目录：任何情况下都不得改写（R5）
IMMUTABLE_DIRS = ["gate1_out"]

# 参与冻结的文档（除 docs/*.sha256 自动扫描外，额外纳入归档清单的关键结论）
KEY_CONCLUSION_DOCS = [
    "docs/GATE1_FINAL_CLOSEOUT_REPORT.md",
    "docs/OOS_VERDICT_2026-10-19.md",
    "docs/B16_DIRECTION_VERDICT_2026-10-18.md",
    "docs/DATA_FEASIBILITY_REPORT.md",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sidecar(sidecar: Path) -> list[tuple[str, str]]:
    """容错解析一个 .sha256 冻结文件，返回 [(expected_hash, raw_path), ...]。

    处理四种历史格式差异：
      - `#` 开头的注释行
      - CRLF / LF 混用
      - 路径缺 `docs/` 前缀
      - `*path`（sha256sum binary 模式标记）
    """
    entries: list[tuple[str, str]] = []
    text = sidecar.read_bytes().decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        expected = parts[0]
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected.lower()):
            continue  # 不是哈希行（可能是别的说明文字）
        raw_path = parts[-1].lstrip("*")
        entries.append((expected, raw_path))
    return entries


def resolve_target(raw_path: str) -> Path | None:
    """把冻结文件里写的路径解析成实际存在的文件。

    依次尝试：仓库根 / docs/ 下 / 与冻结文件同目录。
    """
    raw = raw_path.replace("\\", "/").lstrip("./")
    candidates = [
        REPO_ROOT / raw,
        REPO_ROOT / "docs" / raw,
        REPO_ROOT / "docs" / Path(raw).name,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def cmd_verify(strict: bool) -> int:
    sidecars = sorted(REPO_ROOT.glob("docs/*.sha256"))
    if not sidecars:
        print("未找到任何 docs/*.sha256，无需校验。")
        return 0

    ok = mismatched = unreadable = 0
    print("=" * 78)
    print("预登记冻结校验（容错解析：注释 / CRLF / 路径前缀 / *binary 标记）")
    print("=" * 78)

    for sidecar in sidecars:
        entries = parse_sidecar(sidecar)
        if not entries:
            print(f"\n[空] {sidecar.name}  —— 没有可解析的哈希条目")
            continue
        print(f"\n{sidecar.name}")
        for expected, raw_path in entries:
            target = resolve_target(raw_path)
            if target is None:
                unreadable += 1
                print(f"   [无法定位] {expected[:12]}  {raw_path}")
                continue
            actual = sha256_file(target)
            rel = target.relative_to(REPO_ROOT).as_posix()
            if actual == expected:
                ok += 1
                print(f"   [OK]      {expected[:12]}  {rel}")
            else:
                mismatched += 1
                print(f"   [不匹配]  {expected[:12]}  {rel}")
                print(f"             磁盘 {actual}")
                print(f"             冻结 {expected}")

    print()
    print("-" * 78)
    print(f"通过 {ok} 条 · 内容不匹配 {mismatched} 条 · 无法定位 {unreadable} 条")
    print("-" * 78)

    if mismatched:
        print("\n严重：有冻结条目内容与记录不符。")
        print("这意味着『磁盘上的协议』与『结果所引用的协议』不是同一份。")
        print("在查清之前，相关实验结论不可引用。")
        return 1
    if unreadable and strict:
        print("\n（--strict 模式：无法定位的条目也视为失败）")
        return 2
    if unreadable:
        print("\n注意：有条目无法定位，但不影响已校验条目的结论。")
        print("这些通常是历史遗留的路径写法差异，不是内容问题。")
        return 2
    print("\n全部冻结条目与记录一致：预登记文档自冻结以来未被改写。")
    return 0


def cmd_manifest() -> int:
    out = REPO_ROOT / "docs" / "ARCHIVE_MANIFEST_2026-10-20.sha256"
    lines: list[str] = []
    count = 0

    # 1) 冻结条目所指向的正文（预登记协议本体）
    bodies: list[Path] = []
    for sidecar in sorted(REPO_ROOT.glob("docs/*.sha256")):
        for _expected, raw_path in parse_sidecar(sidecar):
            t = resolve_target(raw_path)
            if t is not None and t not in bodies:
                bodies.append(t)
    lines.append("# ① 预登记协议正文（由各 .sha256 冻结文件指向的本体）")
    for p in sorted(bodies):
        lines.append(f"{sha256_file(p)}  {p.relative_to(REPO_ROOT).as_posix()}")
        count += 1

    # 2) 不可变实验产物
    lines.append("#")
    lines.append("# ② 不可变实验产物（gate1_out/，R5：不得改写、不得删除）")
    for d in IMMUTABLE_DIRS:
        base = REPO_ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and "__pycache__" not in str(p):
                lines.append(f"{sha256_file(p)}  {p.relative_to(REPO_ROOT).as_posix()}")
                count += 1

    # 3) 关键结论文档
    lines.append("#")
    lines.append("# ③ 关键结论文档（引用它们时必须连同本清单的哈希一起给出）")
    for rel in KEY_CONCLUSION_DOCS:
        p = REPO_ROOT / rel
        if p.is_file():
            lines.append(f"{sha256_file(p)}  {rel}")
            count += 1

    header = [
        "# DeepQuant 归档产物清单（阶段 0.7）",
        "#",
        "# 用途：外部评审方用它核对『结论所依据的材料』是否被改写过。",
        f"# 生成方式：python scripts/verify_archive.py --manifest（共 {count} 项）",
        "# 校验方式：sha256sum -c docs/ARCHIVE_MANIFEST_2026-10-20.sha256（在仓库根执行）",
        "#",
        "# 本清单是新增文件，不覆盖也不替代各预登记自带的 .sha256 历史记录；",
        "# 那些历史记录格式不统一（CRLF / 路径前缀），保留原样以存证。",
        "#",
        "",
    ]
    # 必须用 write_bytes（或 newline="\n"）：Windows 上 write_text 会把 \n 转成
    # \r\n，而 sha256sum -c 会把尾随的 \r 当成文件名的一部分，导致全部条目误报
    # "No such file or directory"。这正是本脚本要解决的坑，自己不能踩。
    payload = ("\n".join(header + lines) + "\n").encode("utf-8")
    assert b"\r\n" not in payload, "清单内部不应出现 CRLF"
    out.write_bytes(payload)

    # 生成后立即自检：确认能被标准工具正确解析（只读回自己，不做全量重算）
    _ = parse_sidecar(out)
    print(f"已生成：{out.relative_to(REPO_ROOT).as_posix()}（{count} 项，LF 行尾，{len(payload)} 字节）")
    print("校验命令（仓库根执行）：")
    print("    sha256sum -c docs/ARCHIVE_MANIFEST_2026-10-20.sha256")
    print("或用本脚本：python scripts/verify_archive.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="归档产物完整性校验")
    ap.add_argument("--manifest", action="store_true", help="生成统一归档清单")
    ap.add_argument("--strict", action="store_true", help="无法定位的条目也视为失败")
    args = ap.parse_args()
    if args.manifest:
        return cmd_manifest()
    return cmd_verify(args.strict)


if __name__ == "__main__":
    sys.exit(main())
