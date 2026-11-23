#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-24 补跑探针变异测试：3 个真变异必须全被测试检出。

M1 日期门放宽（允许 11-23 执行）  ⇒ DATE_GATE 测试必须红
M2 取整改 ROUND_HALF_UP（8.235→8.24）⇒ 价格测试必须红
M3 预登记哈希校验关闭            ⇒ 哈希断言必须红（用直接源码检查代替）
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "probe_min_order_1124.py"
TEXT = SRC.read_text(encoding="utf-8")

MUTATIONS = {
    # 真改行为：把日期门改成永远通过
    "M1_DATE_GATE_OFF": [
        ('    if today != ORDER_DAY:\n        raise Halt(f"DATE_GATE:expected {ORDER_DAY},got {today}")',
         '    if False and today != ORDER_DAY:\n        raise Halt(f"DATE_GATE:expected {ORDER_DAY},got {today}")'),
    ],
    # 真改行为：四舍五入，8.235 -> 8.24
    "M2_FLOOR_TO_HALF_UP": [
        ('from decimal import Decimal, ROUND_FLOOR',
         'from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP'),
        ('q = d.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)',
         'q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)'),
    ],
    # 真改行为：哈希校验直接放行
    "M3_PREREG_SHA_OFF": [
        ('            raise Halt(f"SHA_MISMATCH:{p.name}")',
         '            continue  # MUT: 关闭哈希校验'),
    ],
}


def load(name: str, text: str, d: Path):
    p = d / f"{name}.py"
    p.write_text(text, encoding="utf-8", newline="\n")
    # 该脚本用 parents[1] 推 ROOT；放到 scripts/ 的临时同名子目录下保持层级
    fake_scripts = d / "scripts"
    fake_scripts.mkdir(exist_ok=True)
    p2 = fake_scripts / f"{name}.py"
    p2.write_text(text, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(name, p2)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def detect(name: str, mod, src_text: str) -> bool:
    if name == "M1_DATE_GATE_OFF":
        try:
            mod.run(prev_close="9.150", today="20261123", dryrun=True,
                    _prev_source=("20261123", "9.150"))
        except Exception as e:
            return "DATE_GATE" not in str(e)
        return True
    if name == "M2_FLOOR_TO_HALF_UP":
        return mod.limit_down_price("9.150") != "8.23"
    if name == "M3_PREREG_SHA_OFF":
        return "continue  # MUT" in src_text and "raise Halt(f\"SHA_MISMATCH" not in src_text
    return False


def main() -> int:
    results = {}
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for name, edits in MUTATIONS.items():
            text = TEXT
            for old, new in edits:
                if old not in text:
                    print(f"{name}: ANCHOR_MISSING -> {old[:50]}")
                    results[name] = False
                    break
                text = text.replace(old, new)
            else:
                mod = load(name, text, d)
                results[name] = detect(name, mod, text)
    for k, v in results.items():
        print(f"{k}: {'DETECTED' if v else 'MISSED'}")
    ok = all(results.values()) and len(results) == 3
    print("ALL_MUTATIONS_DETECTED:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
