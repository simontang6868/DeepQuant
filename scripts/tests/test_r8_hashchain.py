"""R8 §2：``corrections_hashchain.py`` 单测（不改写既有行 / 独立算术 / 失败关闭）。"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import corrections_hashchain as c  # noqa: E402

ORIG = b'{"a": 1}\n{"b": 2}\n{"c": 3}\n'


def _mk(tmp_path):
    src = tmp_path / "corrections.jsonl"
    ch = tmp_path / "corrections.hashchain.jsonl"
    src.write_bytes(ORIG)
    return src, ch


def test_self_check_passes():
    assert c.self_check()["decision"] == "PASS"


def test_build_does_not_mutate_source(tmp_path):
    src, ch = _mk(tmp_path)
    before = src.read_bytes()
    r = c.build(source=src, chain=ch)
    assert src.read_bytes() == before
    assert r["n_lines"] == 3 and len(r["chain_head"]) == 64
    assert r["source_sha256"] == hashlib.sha256(before).hexdigest()


def test_chain_matches_independent_arithmetic(tmp_path):
    """期望值由手写 hashlib 公式给出，⭐ 不调用被测模块推导。"""
    src, ch = _mk(tmp_path)
    c.build(source=src, chain=ch)
    rows = [json.loads(x) for x in ch.read_text(encoding="utf-8").splitlines()]
    lines = ORIG.rstrip(b"\n").split(b"\n")
    prev = c.GENESIS
    for i, line in enumerate(lines, 1):
        exp_lh = hashlib.sha256(line).hexdigest()
        exp_head = hashlib.sha256((prev + "\n").encode() + line).hexdigest()
        assert rows[i - 1]["seq"] == i
        assert rows[i - 1]["line_sha256"] == exp_lh
        assert rows[i - 1]["chain_head"] == exp_head
        prev = exp_head
    assert c.head(chain=ch) == prev


def test_verify_pass_on_clean_pair(tmp_path):
    src, ch = _mk(tmp_path)
    c.build(source=src, chain=ch)
    v = c.verify(source=src, chain=ch)
    assert v["decision"] == "PASS" and v["n_lines"] == 3


def test_tampered_source_row_fails(tmp_path):
    src, ch = _mk(tmp_path)
    c.build(source=src, chain=ch)
    src.write_bytes(b'{"a": 1}\n{"b": 999}\n{"c": 3}\n')
    v = c.verify(source=src, chain=ch)
    assert v["decision"] == "FAIL" and v["reason"] == "CHAIN_MISMATCH" and v["seq"] == 2


def test_silent_append_fails(tmp_path):
    src, ch = _mk(tmp_path)
    c.build(source=src, chain=ch)
    src.write_bytes(ORIG + b'{"d": 4}\n')
    assert c.verify(source=src, chain=ch)["decision"] == "FAIL"


def test_missing_chain_row_fails(tmp_path):
    src, ch = _mk(tmp_path)
    c.build(source=src, chain=ch)
    kept = ch.read_text(encoding="utf-8").splitlines()[:-1]
    ch.write_text("".join(x + "\n" for x in kept), encoding="utf-8", newline="\n")
    assert c.verify(source=src, chain=ch)["reason"] == "LINE_COUNT_MISMATCH"


def test_missing_chain_file_fails_closed(tmp_path):
    src, ch = _mk(tmp_path)
    assert c.verify(source=src, chain=ch)["reason"] == "CHAIN_MISSING"
    with pytest.raises(FileNotFoundError):
        c.head(chain=ch)
    assert c.main(["--head", "--chain", str(ch)]) == 2


def test_empty_source_fails_closed(tmp_path):
    src, ch = _mk(tmp_path)
    src.write_bytes(b"")
    with pytest.raises(ValueError):
        c.build(source=src, chain=ch)
    assert c.verify(source=src, chain=ch)["reason"] == "SOURCE_EMPTY"


def test_cr_in_line_is_preserved_not_normalized(tmp_path):
    src, ch = _mk(tmp_path)
    src.write_bytes(b'{"a": 1}\r\n{"b": 2}\r\n')
    c.build(source=src, chain=ch)
    rows = [json.loads(x) for x in ch.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["line_sha256"] == hashlib.sha256(b'{"a": 1}\r').hexdigest()
    assert c.verify(source=src, chain=ch)["decision"] == "PASS"


def test_real_corrections_file_builds_and_verifies(tmp_path):
    """对真实 51 行台账建链（链写到临时目录，不污染项目）。"""
    real = Path(__file__).resolve().parents[2] / "research" / "stock_pick_2026-10-25" / "forward" / "corrections.jsonl"
    if not real.exists():
        pytest.skip("真实台账不存在")
    before = real.read_bytes()
    ch = tmp_path / "chain.jsonl"
    r = c.build(source=real, chain=ch)
    v = c.verify(source=real, chain=ch)
    assert v["decision"] == "PASS" and r["n_lines"] == v["n_lines"]
    assert real.read_bytes() == before, "绝不能改写真实台账"
