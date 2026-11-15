#!/usr/bin/env python3
"""R8 §2：更正台账 ``corrections.jsonl`` 的**旁路哈希链**（逐行累计）。

## 为什么要有这条链

`corrections.jsonl` 是「邮件锁时点、防事后补记」的证据。但机外备份里若只有一份
不断增长的 JSONL，审阅者无法区分「这一行当时就在」和「后来追加/改写」。
本脚本**不改写既有行**，另建一个旁路文件，逐行累计哈希；链头（最新累计哈希）
随每日状态邮件外发，形成第二条独立的时点锚。

## 链的定义（冻结口径，改动即新口径）

    令 L_i = 源文件第 i 行的**原始字节**（不含换行符；不规范化、不去 CR、不改编码）
    genesis = "0" * 64
    line_hash_i   = sha256(L_i)
    chain_head_i  = sha256( (chain_head_{i-1} + "\\n").encode("utf-8") + L_i )
    chain_head_0  = genesis

旁路文件每行一个 JSON 对象：`{"seq": i, "line_sha256": ..., "chain_head": ...}`。
**链头 = 最后一行的 chain_head**（链条最新端点）。

## 失败关闭

- 源文件为空 / 不可读 ⇒ FAIL，不当「0 行 PASS」；
- 链文件缺失、空、不可解析、行数不符、任一行哈希不符 ⇒ FAIL；
- ``--head`` 拿不到链头 ⇒ ``CHAIN_UNAVAILABLE``，由邮件脚本显式标注，不静默、不伪造。

## 用法

    python scripts/corrections_hashchain.py --build   --source F --chain C
    python scripts/corrections_hashchain.py --verify  --source F --chain C
    python scripts/corrections_hashchain.py --head    --chain C
    python scripts/corrections_hashchain.py --self-check

⚠️ ``--build`` **只读源文件**、只写旁路链；**绝不改写** ``corrections.jsonl``。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FWD = ROOT / "research" / "stock_pick_2026-10-25" / "forward"
DEFAULT_SOURCE = FWD / "corrections.jsonl"
DEFAULT_CHAIN = FWD / "corrections.hashchain.jsonl"

GENESIS = "0" * 64
SCHEMA = "corrections_hashchain_v1"
UNAVAILABLE = "CHAIN_UNAVAILABLE"


def split_lines(raw: bytes) -> list[bytes]:
    """按 LF 切行，保留行内原始字节（含可能的 CR）；丢弃末尾空串。"""
    if not raw:
        return []
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def source_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic_write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".hashchain.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build(*, source: Path = DEFAULT_SOURCE, chain: Path = DEFAULT_CHAIN) -> dict:
    """从源文件生成旁路链。**不改写源文件**。"""
    raw = source.read_bytes()
    lines = split_lines(raw)
    if not lines:
        raise ValueError("源文件为空 ⇒ 失败关闭，不生成空链")
    prev = GENESIS
    rows: list[dict] = []
    for i, line in enumerate(lines, 1):
        lh = hashlib.sha256(line).hexdigest()
        head = hashlib.sha256((prev + "\n").encode("utf-8") + line).hexdigest()
        rows.append({"seq": i, "line_sha256": lh, "chain_head": head})
        prev = head
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    _atomic_write(chain, body)
    return {"decision": "BUILT", "schema": SCHEMA, "n_lines": len(rows),
            "chain_head": prev, "source_sha256": source_sha256(raw),
            "source": str(source), "chain": str(chain)}


def verify(*, source: Path = DEFAULT_SOURCE, chain: Path = DEFAULT_CHAIN) -> dict:
    """**独立复算**：不调用 build()，直接用 hashlib 逐行比对。"""
    raw = source.read_bytes()
    lines = split_lines(raw)
    if not lines:
        return {"decision": "FAIL", "reason": "SOURCE_EMPTY"}
    if not chain.exists():
        return {"decision": "FAIL", "reason": "CHAIN_MISSING"}
    text = chain.read_text(encoding="utf-8")
    rows_text = [x for x in text.splitlines() if x.strip()]
    if len(rows_text) != len(lines):
        return {"decision": "FAIL", "reason": "LINE_COUNT_MISMATCH",
                "n_source": len(lines), "n_chain": len(rows_text)}
    prev = GENESIS
    for i, (line, row_text) in enumerate(zip(lines, rows_text), 1):
        try:
            rec = json.loads(row_text)
        except Exception:
            return {"decision": "FAIL", "reason": "CHAIN_ROW_UNPARSABLE", "seq": i}
        exp_lh = hashlib.sha256(line).hexdigest()
        exp_head = hashlib.sha256((prev + "\n").encode("utf-8") + line).hexdigest()
        if rec.get("seq") != i or rec.get("line_sha256") != exp_lh \
                or rec.get("chain_head") != exp_head:
            return {"decision": "FAIL", "reason": "CHAIN_MISMATCH", "seq": i,
                    "expected_head": exp_head, "actual_head": rec.get("chain_head")}
        prev = exp_head
    return {"decision": "PASS", "n_lines": len(lines), "chain_head": prev,
            "source_sha256": source_sha256(raw)}


def head(*, chain: Path = DEFAULT_CHAIN) -> str:
    """返回链头；任何异常一律抛，调用方标注 CHAIN_UNAVAILABLE。"""
    if not chain.exists():
        raise FileNotFoundError("链文件不存在")
    rows = [x for x in chain.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        raise ValueError("链文件为空")
    rec = json.loads(rows[-1])
    h = str(rec.get("chain_head") or "")
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError("链头不是 64 位小写 hex")
    return h


def self_check() -> dict:
    """离线自检：build→verify→head，并逐条证明「真改行为」能被检出。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src, ch = d / "corrections.jsonl", d / "corrections.hashchain.jsonl"
        orig = b'{"a": 1}\n{"b": 2}\n{"c": 3}\n'
        src.write_bytes(orig)
        b = build(source=src, chain=ch)
        assert src.read_bytes() == orig, "build 不得改写源文件"
        v = verify(source=src, chain=ch)
        assert v["decision"] == "PASS" and v["n_lines"] == 3
        assert head(chain=ch) == b["chain_head"] == v["chain_head"]
        # 独立算术：第 2 行的累计头必须等于手写公式（不调用 build/verify 推期望）
        l1, l2 = b'{"a": 1}', b'{"b": 2}'
        h1 = hashlib.sha256((GENESIS + "\n").encode() + l1).hexdigest()
        h2 = hashlib.sha256((h1 + "\n").encode() + l2).hexdigest()
        rows = [json.loads(x) for x in ch.read_text(encoding="utf-8").splitlines()]
        assert rows[1]["chain_head"] == h2, "逐行累计与独立算术不符"
        # 真行为 ①：删掉链最后一行 ⇒ 行数不符 ⇒ FAIL
        ch.write_text("".join(x + "\n" for x in
                              ch.read_text(encoding="utf-8").splitlines()[:-1]),
                      encoding="utf-8", newline="\n")
        assert verify(source=src, chain=ch)["reason"] == "LINE_COUNT_MISMATCH"
        # 真行为 ②：篡改源第 2 行但**不重建链** ⇒ CHAIN_MISMATCH at seq 2
        build(source=src, chain=ch)
        src.write_bytes(b'{"a": 1}\n{"b": 999}\n{"c": 3}\n')
        tam = verify(source=src, chain=ch)
        assert (tam["decision"] == "FAIL" and tam["reason"] == "CHAIN_MISMATCH"
                and tam["seq"] == 2), tam
        # 真行为 ③：合法追加一行但链未更新 ⇒ 行数不符（防「静默追加」）
        src.write_bytes(orig + b'{"d": 4}\n')
        assert verify(source=src, chain=ch)["reason"] in ("LINE_COUNT_MISMATCH",
                                                          "CHAIN_MISMATCH")
    return {"decision": "PASS", "checks": ["no_source_mutation", "verify_pass",
                                           "head_matches", "independent_arithmetic",
                                           "missing_row_fails", "tamper_fails",
                                           "silent_append_fails"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="更正台账旁路哈希链（只读源、不改写既有行）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--build", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--head", action="store_true")
    g.add_argument("--self-check", action="store_true")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--chain", default=str(DEFAULT_CHAIN))
    args = ap.parse_args(argv)
    src, ch = Path(args.source), Path(args.chain)
    try:
        if args.self_check:
            r = self_check()
            print(json.dumps(r, ensure_ascii=False))
            return 0
        if args.build:
            r = build(source=src, chain=ch)
            print(json.dumps(r, ensure_ascii=False))
            return 0
        if args.verify:
            r = verify(source=src, chain=ch)
            print(json.dumps(r, ensure_ascii=False))
            return 0 if r["decision"] == "PASS" else 1
        print(head(chain=ch))
        return 0
    except Exception as e:  # noqa: BLE001
        if args.head:
            print(UNAVAILABLE)
        else:
            print(json.dumps({"decision": "FAIL", "reason": type(e).__name__},
                             ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
