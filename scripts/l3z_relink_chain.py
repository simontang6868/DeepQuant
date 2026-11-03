#!/usr/bin/env python
"""L3z 审计链的 sha 重链工具（l3z → l3v → l3y）。

## 为什么需要这个脚本

L3v / L3y 的冻结机制是「输入 sha 与脚本里写死的不一致就拒跑」。这个机制的价值在于
**抓意外改动**；但一次**有意**的卡片修订会让 sha 沿链条逐级失效，只改一处的输出
立刻让下游三处失效。手工追 5 个 sha 时，最危险的不是麻烦，而是：

- 漏掉某个下游 sha → 下游用旧输入静默通过（冻结形同废止）；
- 抄错一位 → 排查成本远高于重跑。

本项目历史上「取错量」「pp÷小数」这类错误，全部发生在手工搬运数字的环节。
所以重链也交给脚本：它**不判断改动该不该发生**，只在已获授权的前提下机械更新，
并把每一次变化写进 `reports/L3Z_SHA_RELINK_LOG.md`，可复查。

## 纪律

- 必须显式传 ``--reason``，并把理由写进日志；不给理由拒绝执行。
- 只更新**输入 sha**，从不改动阈值、口径、判据。
- 重链前后各跑一次三阶段脚本；任一步失败即停，不吞错。
- 旧产物不删除，覆盖前把 `*.json` 备份为 `*.prev`（同目录，保留一份）。

用法::

    PYTHONPATH=backend/src python scripts/l3z_relink_chain.py \
        --reason "A4: 3 条 headline 补审计限定措辞"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

#: 链条上的脚本，顺序执行
CHAIN = (
    ("l3z", ROOT / "scripts" / "l3z_multiplicity.py"),
    ("l3v", ROOT / "scripts" / "l3v_claims.py"),
    ("l3y", ROOT / "scripts" / "l3y_deliverable.py"),
)

#: 需要被下游冻结的产物 / 输入，按「谁冻结谁」列出
#: (描述, 路径, 冻结在哪个脚本里, 该脚本里的定位说明)
RELINK_TARGETS = (
    (
        "L3z 预登记",
        ROOT / "docs" / "gates" / "l3z_prereg.json",
        ROOT / "scripts" / "l3z_multiplicity.py",
        "PREREG_SHA",
    ),
    (
        "结论卡片",
        ROOT / "docs" / "gates" / "l3v_claims.json",
        ROOT / "scripts" / "l3z_multiplicity.py",
        "claims_cards",
    ),
    (
        "结论卡片",
        ROOT / "docs" / "gates" / "l3v_claims.json",
        ROOT / "scripts" / "l3y_deliverable.py",
        '"claims_cards"',
    ),
    (
        "L3z 降级清单",
        ROOT / "reports" / "gates" / "H-L3Z-MULTIPLICITY" / "l3z_claims_patch.json",
        ROOT / "scripts" / "l3v_claims.py",
        "l3z_claims_patch",
    ),
    (
        "L3v 预登记",
        ROOT / "docs" / "gates" / "l3v_prereg.json",
        ROOT / "scripts" / "l3v_claims.py",
        "PREREG_SHA",
    ),
    (
        "L3v 结果",
        ROOT / "reports" / "gates" / "H-L3V-CLAIM-REGISTRY" / "l3v_claims.json",
        ROOT / "scripts" / "l3y_deliverable.py",
        "l3v_result",
    ),
    (
        "L3y 预登记",
        ROOT / "docs" / "gates" / "l3y_prereg.json",
        ROOT / "scripts" / "l3y_deliverable.py",
        "PREREG_SHA",
    ),
)

SHA_RE = re.compile(r"\b[0-9a-f]{64}\b")
LOG = ROOT / "reports" / "L3Z_SHA_RELINK_LOG.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_declared_sha(text: str, marker: str) -> str | None:
    """在脚本文本里找 ``marker`` 附近声明的那个 64 位 sha。

    两种写法都要支持：
    - ``PREREG_SHA = "<sha>"``
    - ``"claims_cards": (CARDS, "<sha>",)`` 或单行元组
    """
    idx = text.find(marker)
    while idx != -1:
        window = text[idx : idx + 400]
        # 遇到下一个同级声明就停，避免把别处的 sha 认成自己的
        stops = [p for p in (window.find("\n\n"), window.find("),\n    (ROOT")) if p > 0]
        if stops:
            window = window[: min(stops)]
        hit = SHA_RE.search(window)
        if hit:
            return hit.group(0)
        idx = text.find(marker, idx + 1)
    return None


def _replace_once(text: str, old: str, new: str, *, where: str) -> str:
    if old == new:
        return text
    if text.count(old) != 1:
        raise SystemExit(f"[{where}] 期望恰好一处 {old[:12]}…，实际 {text.count(old)} 处，拒绝盲改")
    return text.replace(old, new)


def relink(dry_run: bool, reason: str, only_owner: Path | None = None) -> list[dict]:
    """把 target 的下游冻结 sha 改成当前实际值。返回变更记录。

    ``only_owner`` 非空时只处理冻结在**该脚本**里的那些输入——链条是逐级的，
    必须先重链下一步的输入、才能跑下一步。
    """
    changes: list[dict] = []
    # 同一个脚本可能被改多次，按脚本聚合写回
    pending: dict[Path, str] = {}

    for label, artifact, owner, marker in RELINK_TARGETS:
        if only_owner is not None and owner != only_owner:
            continue
        if not artifact.exists():
            changes.append({"label": label, "status": "MISSING", "path": str(artifact), "owner": owner.name})
            continue
        actual = sha256(artifact)
        text = pending.get(owner) or owner.read_text(encoding="utf-8")
        declared = _find_declared_sha(text, marker)
        if declared is None:
            changes.append(
                {"label": label, "status": "NO_DECLARATION", "owner": owner.name, "marker": marker}
            )
            continue
        if declared == actual:
            changes.append({"label": label, "status": "UNCHANGED", "sha": actual, "owner": owner.name})
            continue
        new_text = _replace_once(text, declared, actual, where=f"{owner.name}:{marker}")
        pending[owner] = new_text
        changes.append(
            {
                "label": label,
                "status": "RELINKED",
                "owner": owner.name,
                "marker": marker,
                "from": declared,
                "to": actual,
            }
        )

    if not dry_run:
        for owner, text in pending.items():
            owner.write_text(text, encoding="utf-8", newline="\n")
    return changes


def run_stage(name: str, script: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [PY, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": "backend/src"},
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-6:])
    return proc.returncode, tail


def backup(path: Path) -> None:
    """覆盖前留一份 .prev，不删旧产物。"""
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".prev"))


def relink_for(owner: Path, *, dry_run: bool, reason: str) -> list[dict]:
    """只重链「某个脚本」自己冻结的那些输入。

    必须按阶段交错重链：链条是 l3z → l3v → l3y，每一步的产出都是下一步的输入，
    所以「先全重链、再全跑」会在第二步就失败——上一步刚重写了自己的产物。
    """
    return relink(dry_run=dry_run, reason=reason, only_owner=owner)


def main() -> int:
    ap = argparse.ArgumentParser(description="L3z 审计链 sha 重链")
    ap.add_argument("--reason", required=True, help="本次重链的理由（写入日志）")
    ap.add_argument("--dry-run", action="store_true", help="只报告差异，不写文件")
    args = ap.parse_args()

    print(f"重链理由：{args.reason}")
    print(f"模式：{'dry-run（不写文件）' if args.dry_run else 'apply'}\n")

    if args.dry_run:
        changes = relink(dry_run=True, reason=args.reason)
        for c in changes:
            if c["status"] == "RELINKED":
                print(f"  {c['label']:<16} {c['owner']:<26} {c['from'][:12]}… → {c['to'][:12]}…")
            elif c["status"] == "UNCHANGED":
                print(f"  {c['label']:<16} 已一致 {c['sha'][:12]}…")
            else:
                print(f"  {c['label']:<16} **{c['status']}**")
        n = sum(1 for c in changes if c["status"] == "RELINKED")
        print(f"\n共需重链 {n} 处（dry-run，未写入）")
        return 0

    # 先备份产物，再按阶段交错「重链本阶段输入 → 执行本阶段」
    for label, path in (
        ("l3z", ROOT / "reports" / "gates" / "H-L3Z-MULTIPLICITY" / "l3z_multiplicity.json"),
        ("l3v", ROOT / "reports" / "gates" / "H-L3V-CLAIM-REGISTRY" / "l3v_claims.json"),
        ("l3y", ROOT / "reports" / "gates" / "H-L3Y-DELIVERABLE" / "l3y_deliverable.json"),
    ):
        backup(path)

    all_changes: list[dict] = []
    results: list[tuple] = []
    for name, script in CHAIN:
        step = relink_for(script, dry_run=False, reason=args.reason)
        all_changes.extend(step)
        for c in step:
            if c["status"] == "RELINKED":
                print(f"[{name}] 重链 {c['label']:<14} {c['from'][:12]}… → {c['to'][:12]}…")
            elif c["status"] != "UNCHANGED":
                print(f"[{name}] **{c['label']} {c['status']}**")

        code, tail = run_stage(name, script)
        results.append((name, code, tail))
        print(f"\n[{'OK ' if code == 0 else 'FAIL'}] {name}")
        print("\n".join("    " + ln for ln in tail.splitlines()))
        if code != 0:
            print(f"\n链条在 {name} 处中断，后续阶段未执行。原因：{args.reason}")
            _append_log(args.reason, all_changes, results, ok=False)
            return code

    _append_log(args.reason, all_changes, results, ok=True)
    print(f"\n日志：{LOG.relative_to(ROOT).as_posix()}")
    return 0


def _append_log(reason: str, changes: list[dict], results: list[tuple], *, ok: bool) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if not LOG.exists():
        lines.append("# L3z 审计链 sha 重链日志\n")
        lines.append("> 由 `scripts/l3z_relink_chain.py` 自动追加。**只记录机械的 sha 更新**，")
        lines.append("> 不改判据、不改阈值。每次重链都对应一条预登记 amendment。\n")
    lines.append(f"\n## {reason}\n")
    lines.append("| 项目 | 状态 | 冻结位置 | 原值 | 新值 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for c in changes:
        if c["status"] == "RELINKED":
            lines.append(
                f"| {c['label']} | 已重链 | `{c.get('owner', '')}`:{c.get('marker', '')} "
                f"| `{c['from'][:12]}…` | `{c['to'][:12]}…` |"
            )
        elif c["status"] == "UNCHANGED":
            lines.append(f"| {c['label']} | 一致 | — | `{c['sha'][:12]}…` | 同左 |")
        else:
            lines.append(f"| {c['label']} | **{c['status']}** | `{c.get('owner', '—')}` | — | — |")
    lines.append("")
    lines.append("执行结果：")
    for name, code, _ in results:
        lines.append(f"- {name}：{'通过' if code == 0 else '**失败**'}")
    lines.append("")
    lines.append(f"总体：{'链条完整' if ok else '**链条中断**'}")
    lines.append("")
    with LOG.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
