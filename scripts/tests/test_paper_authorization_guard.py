"""PA-GUARD-1 授权校验器测试（六项校验 + 失败关闭 + 边界）。

⭐ 哈希 oracle 由**独立算术**硬编码，不是被测函数生成的；
测试里的 body_hash 是另一份独立实现，并先与 oracle 对账。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "paper_authorization_guard.py"
spec = importlib.util.spec_from_file_location("paguard", SRC)
assert spec and spec.loader
pag = importlib.util.module_from_spec(spec)
sys.modules["paguard"] = pag
spec.loader.exec_module(pag)

#: 独立算出的常量（不是调用 guard.compute_hash 得到的）
ORACLE = "ed96374cc6c2b7887db82fcd54d7a081421fe5904bdf0e836d4ec12b6297cff2"
NOW = "2026-12-01T09:35:00+08:00"
BASE = {
    "schema_version": "paper_auth_v1",
    "channel": "gm-paper",
    "account_ref": "REF_ONLY_NOT_A_CREDENTIAL",
    "max_notional": 100000,
    "not_after": "2026-12-02T23:59:00+08:00",
    "scopes": ["query_cash", "query_positions", "query_execution_reports",
               "sim_rebalance_order"],
    "statement_ref": "3207bff41a58",
    "issued_by": "ACCOUNT_HOLDER",
    "issued_at": "2026-11-09T11:16:00+08:00",
}


def body_hash(body):
    """测试侧独立实现，不调用被测模块的任何函数。"""
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def doc(**over):
    body = {**BASE}
    forced = over.pop("sha256", None)   # 允许构造「哈希格式非法」的失败关闭用例
    body.update(over)
    return {**body, "sha256": forced if forced is not None else body_hash(body)}


def run(d, *, now=NOW, action="sim_rebalance_order", notional=4000,
        cumulative=0, available=100000):
    return pag.check(doc=d, now=now, action=action, notional=notional,
                     cumulative=cumulative, available=available)


def test_independent_hash_matches_oracle():
    assert body_hash(BASE) == ORACLE
    assert pag.compute_hash(BASE) == ORACLE  # 期望来自 oracle，不是自证


def test_valid_doc_allows_all_six():
    r = run(doc())
    assert r["decision"] == pag.ALLOW and r["reason"] is None
    assert all(r["checks"].values()) and len(r["checks"]) == 6


# ---------------------------------------------------------------- 六项各独立失败

@pytest.mark.parametrize("over,kw,reason,key", [
    ({"schema_version": "paper_auth_v0"}, {}, "SCHEMA", "schema"),
    ({"channel": "live"}, {}, "CHANNEL", "channel"),
    ({}, {"now": "2026-12-03T00:00:00+08:00"}, "EXPIRY", "expiry"),
    ({"scopes": ["query_cash"]}, {}, "SCOPE", "scope"),
    ({}, {"cumulative": 99000}, "NOTIONAL", "notional"),
])
def test_each_gate_fails_independently(over, kw, reason, key):
    r = run(doc(**over), **kw)
    assert r["decision"] == pag.DENY and r["reason"] == reason
    assert r["checks"][key] is False
    assert all(v for k, v in r["checks"].items() if k != key), "其余五项必须仍通过"


def test_hash_tamper_is_detected():
    d = doc()
    d["sha256"] = "0" * 64
    r = run(d)
    assert r["decision"] == pag.DENY and r["reason"] == "HASH"
    assert all(v for k, v in r["checks"].items() if k != "hash")


def test_scope_must_be_subset_of_frozen_enum():
    d = doc(scopes=["query_cash", "withdraw_all"])
    assert run(d)["reason"] == "SCOPE"


def test_expiry_boundary_is_inclusive():
    assert run(doc(), now="2026-12-02T23:59:00+08:00")["decision"] == pag.ALLOW
    assert run(doc(), now="2026-12-02T23:59:01+08:00")["reason"] == "EXPIRY"


def test_notional_dual_check_single_and_cumulative():
    d = doc(max_notional=100000)
    assert run(d, notional=100000, cumulative=1)["reason"] == "NOTIONAL"
    assert run(d, notional=100001)["reason"] == "NOTIONAL"
    assert run(d, notional=100000, cumulative=0)["decision"] == pag.ALLOW
    assert run(d, available=50000)["reason"] == "NOTIONAL"


# ---------------------------------------------------------------- 失败关闭

@pytest.mark.parametrize("d,kw", [
    (None, {}),                                     # 文件缺失/不可解析
    ({k: v for k, v in doc().items() if k != "channel"}, {}),   # 缺字段
    (doc(sha256="bad"), {}),                        # 哈希格式错
    (doc(sha256="A" * 64), {}),                    # 非小写 hex
    (doc(not_after="2026-12-02 23:59:00"), {}),    # naive 时间，不猜时区
    (doc(), {"now": "not-a-date"}),                # now 不可解析
    (doc(), {"available": 0}),
    (doc(), {"available": -1}),
    (doc(), {"notional": 0}),
    (doc(), {"cumulative": -1}),
    (doc(scopes=[]), {}),
    (doc(scopes=None), {}),
    (doc(max_notional="abc"), {}),
    ("not-a-dict", {}),
])
def test_fail_closed_is_deny_no_auth(d, kw):
    r = run(d, **kw)
    assert r["decision"] == pag.DENY and r["reason"] == pag.NO_AUTH
    assert r["checks"] == {}, "失败关闭不得输出半截六项表"


def test_load_doc_rejects_bom_and_bad_json(tmp_path):
    p = tmp_path / "a.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(doc()).encode("utf-8"))
    assert pag.load_doc(p) is None
    p.write_text("{oops", encoding="utf-8")
    assert pag.load_doc(p) is None
    p.write_text("[]", encoding="utf-8")
    assert pag.load_doc(p) is None
    assert pag.load_doc(tmp_path / "missing.json") is None


# ---------------------------------------------------------------- CLI 与边界

def test_cli_check_and_compute_hash(tmp_path):
    p = tmp_path / "paper_authorization.json"   # 测试 fixture，非生产授权文件
    p.write_text(json.dumps(doc(), ensure_ascii=False), encoding="utf-8", newline="\n")
    py = sys.executable
    r = subprocess.run([py, str(SRC), "--compute-hash", "--file", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == ORACLE

    r = subprocess.run([py, str(SRC), "--check", "--file", str(p),
                        "--action", "sim_rebalance_order", "--notional", "4000",
                        "--available", "100000", "--now", NOW],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "ALLOW" in r.stdout
    assert "REF_ONLY_NOT_A_CREDENTIAL" not in r.stdout   # 不回显文件内容
    assert ORACLE not in r.stdout                        # 不回显哈希值本身


def test_cli_check_requires_explicit_now(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps(doc()), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SRC), "--check", "--file", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "--now" in r.stderr


def test_guard_source_has_no_order_calls_or_sdk():
    src = SRC.read_text(encoding="utf-8")
    assert "import gmtrade" not in src and "from gmtrade" not in src
    assert "import xtquant" not in src
    import re
    assert re.search(r"\border_[A-Za-z_][A-Za-z0-9_]*\s*\(", src) is None
