#!/usr/bin/env python3
"""ECR-P0-SNAPSHOT-1 / R7-3：日更链阶段 rc 的**唯一判定真源**。

``daily_chain.sh`` 的 ``record_stage`` 负责采集 ``name=rc:分类`` 明细，
最终 ``ok`` 由本模块独立判定：关键阶段任一非零 ⇒ ok=false ⇒ 告警 ⇒ 该日不计 G1。
把判定放在可单测的纯函数里，避免「闸门散落在 shell 里、测试只能看文本」。

用法（由 daily_chain.sh 调用）：
    python scripts/stage_rc_gate.py --check "update=0:关键;paper_snapshot=4:关键"
    # rc=0 全绿；rc=1 有关键阶段失败（stdout 打印 JSON 结论）
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

#: 关键阶段：任一非零 ⇒ ok=false。与工程变更单第 3 节清单一一对应。
KEY_STAGES = (
    "update", "is_due", "decision", "universe_snap",
    "paper_snapshot", "shadow_reconcile", "backup",
)
#: 允许忽略的阶段，但**必须**带理由（审查时逐条核）。
IGNORABLE_STAGES = ("heartbeat_ping", "mem_peak")


def parse_stages(raw: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (x.strip() for x in raw.split(";")):
        if not item:
            continue
        head, _, kind = item.partition(":")
        name, _, rc = head.partition("=")
        if not name or not rc:
            raise ValueError(f"阶段记录格式非法：{item!r}")
        try:
            rc_i = int(rc)
        except ValueError as exc:
            raise ValueError(f"阶段 rc 非整数：{item!r}") from exc
        out.append({"name": name, "rc": rc_i, "kind": kind})
    return out


def evaluate(raw: str) -> dict[str, Any]:
    """纯函数：返回 ok / 失败关键阶段 / 忽略项 / 是否告警。"""
    stages = parse_stages(raw)
    failed_key = [s["name"] for s in stages
                  if s["kind"] == "关键" and s["rc"] != 0
                  and s["name"] in KEY_STAGES]
    # 未知阶段一律不静默放过：按关键处理（失败关闭）
    unknown = [s["name"] for s in stages
               if s["name"] not in KEY_STAGES and s["name"] not in IGNORABLE_STAGES]
    ignored = [s["name"] for s in stages
               if s["name"] in IGNORABLE_STAGES and s["rc"] != 0]
    ok = not failed_key and not unknown
    return {"ok": ok, "alert": not ok,
            "failed_key_stages": failed_key,
            "unknown_stages": unknown,
            "ignored_nonzero": ignored,
            "n_stages": len(stages)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", default="", metavar="STAGES")
    args = ap.parse_args(argv)
    try:
        r = evaluate(args.check)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
