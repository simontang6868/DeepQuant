#!/usr/bin/env python3
"""R8 §2：``corrections_hashchain.py`` 的真变异测试（每条必须真改行为）。

用法： python scripts/r8_hashchain_mutation_test.py
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "corrections_hashchain.py"
ORIGINAL = SRC.read_text(encoding="utf-8")
ORIG_LINES = b'{"a": 1}\n{"b": 2}\n{"c": 3}\n'


def load(text: str, tag: str, tmp: Path):
    p = ROOT / "scripts" / f"_mut_r1_{tag}.py"
    p.write_text(text, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(f"_mut_r1_{tag}", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return p, mod


def pair(tmp, tag):
    src = tmp / f"src_{tag}.jsonl"
    ch = tmp / f"chain_{tag}.jsonl"
    src.write_bytes(ORIG_LINES)
    return src, ch


def m1_no_cumulative(mod, tmp, tag):
    """累计哈希退化成逐行独立 ⇒ verify 应 FAIL（原 PASS）。"""
    src, ch = pair(tmp, tag)
    mod.build(source=src, chain=ch)
    return mod.verify(source=src, chain=ch)["decision"] == "FAIL"


def m2_line_hash_unchecked(mod, tmp, tag):
    """篡改链里 line_sha256 ⇒ 变异体仍 PASS（原 FAIL）。"""
    src, ch = pair(tmp, tag)
    mod.build(source=src, chain=ch)
    rows = [json.loads(x) for x in ch.read_text(encoding="utf-8").splitlines()]
    rows[0]["line_sha256"] = "0" * 64
    ch.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                  encoding="utf-8", newline="\n")
    return mod.verify(source=src, chain=ch)["decision"] == "PASS"


def m3_line_count_unchecked(mod, tmp, tag):
    """删掉链最后一行 ⇒ 变异体仍 PASS（原 LINE_COUNT_MISMATCH）。"""
    src, ch = pair(tmp, tag)
    mod.build(source=src, chain=ch)
    kept = ch.read_text(encoding="utf-8").splitlines()[:-1]
    ch.write_text("".join(x + "\n" for x in kept), encoding="utf-8", newline="\n")
    return mod.verify(source=src, chain=ch)["decision"] == "PASS"


def m4_head_reads_first_row(mod, tmp, tag):
    """head 取第一行 ⇒ 返回旧链头（真改行为）。"""
    src, ch = pair(tmp, tag)
    mod.build(source=src, chain=ch)
    rows = [json.loads(x) for x in ch.read_text(encoding="utf-8").splitlines()]
    return mod.head(chain=ch) == rows[0]["chain_head"] and len(rows) > 1


def m5_build_injects_extra_row(mod, tmp, tag):
    """build 偷偷多加一行 ⇒ 链与源行数不符（原 PASS）。"""
    src, ch = pair(tmp, tag)
    mod.build(source=src, chain=ch)
    return mod.verify(source=src, chain=ch)["decision"] == "FAIL"


def m6_seq_unchecked(mod, tmp, tag):
    """篡改 seq ⇒ 变异体仍 PASS（原 FAIL）。"""
    src, ch = pair(tmp, tag)
    mod.build(source=src, chain=ch)
    rows = [json.loads(x) for x in ch.read_text(encoding="utf-8").splitlines()]
    rows[1]["seq"] = 99
    ch.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                  encoding="utf-8", newline="\n")
    return mod.verify(source=src, chain=ch)["decision"] == "PASS"


MUTATIONS = [
    ("M1_NO_CUMULATIVE",
     '\n        head = hashlib.sha256((prev + "\\n").encode("utf-8") + line).hexdigest()',
     '\n        head = hashlib.sha256(line).hexdigest()', m1_no_cumulative),
    ("M2_LINE_HASH_OFF",
     "exp_lh = hashlib.sha256(line).hexdigest()",
     'exp_lh = rec.get("line_sha256")', m2_line_hash_unchecked),
    ("M3_COUNT_OFF",
     "if len(rows_text) != len(lines):", "if False:", m3_line_count_unchecked),
    ("M4_HEAD_FIRST_ROW", "rec = json.loads(rows[-1])",
     "rec = json.loads(rows[0])", m4_head_reads_first_row),
    ("M5_EXTRA_ROW",
     '    raw = source.read_bytes()\n    lines = split_lines(raw)\n    if not lines:\n        raise ValueError("源文件为空 ⇒ 失败关闭，不生成空链")',
     '    raw = source.read_bytes() + b\'{"x": 9}\\n\'\n    lines = split_lines(raw)\n    if not lines:\n        raise ValueError("源文件为空 ⇒ 失败关闭，不生成空链")',
     m5_build_injects_extra_row),
    ("M6_SEQ_OFF", "if rec.get(\"seq\") != i or rec.get(\"line_sha256\") != exp_lh \\",
     'if False or rec.get("line_sha256") != exp_lh \\', m6_seq_unchecked),
]


def main() -> int:
    created: list[Path] = []
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # 基线：原实现上每条变异都不得「检出」
            _, base = load(ORIGINAL, "baseline", tmp)
            created.append(ROOT / "scripts" / "_mut_r1_baseline.py")
            for i, (name, _o, _n, check) in enumerate(MUTATIONS):
                if check(base, tmp, f"base{i}"):
                    print(f"[MUT] FAIL 基线 {name} 被判为变异体 ⇒ 检查器写错")
                    return 1
            print(f"[MUT] 基线自检 OK：{len(MUTATIONS)} 条均不触发\n")

            detected = 0
            for i, (name, old, new, check) in enumerate(MUTATIONS, 1):
                cnt = ORIGINAL.count(old)
                if cnt != 1:
                    print(f"[MUT] FAIL {name}：锚点出现 {cnt} 次（须恰好 1 次）")
                    return 1
                variant = ORIGINAL.replace(old, new, 1)
                p, mod = load(variant, f"m{i}", tmp)
                created.append(p)
                ok = check(mod, tmp, f"m{i}")
                print(f"  {name:24s} {'DETECTED' if ok else 'MISSED  '}")
                detected += int(ok)
            total = len(MUTATIONS)
            print(f"\n[MUT] 真变异检出 {detected}/{total}")
            if detected != total:
                print("[MUT] FAIL 有变异未被检出")
                return 1
            print("[MUT] PASS 全部真改行为且被检出")
            return 0
    finally:
        for p in created:
            try:
                p.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
