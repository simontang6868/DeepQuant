"""EARLY-IMPL-1 提前准备的验收测试（预登记 P1–P5 + 硬边界）。

⭐ 纪律：期望值来自**独立算术 / 产物**，不调用被测函数自身；「空输入」
与「无差异」是两条不同路径，必须分别证伪。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import paper_account_adapter as adapter  # noqa: E402
import sim_drill_driver as driver  # noqa: E402
import sim_reconcile as rec  # noqa: E402

PY = sys.executable
PREREG = ROOT / "docs" / "SIM_DRILL_PREP_PREREG_2026-11-14.md"
AUTH = (ROOT / "research" / "stock_pick_2026-10-25" / "forward"
        / "authorization_statements" / "EARLY_IMPL_statement_2026-11-14.md")


def baseline() -> tuple[dict, dict]:
    return rec.selfcheck_fixture()


def ext_order(truth: dict) -> dict:
    return truth["execution_reports"][0]


def loc_order(local: dict) -> dict:
    return local["orders"][0]


# ---------------------------------------------------------------- P1 / 基线


def test_baseline_is_pass():
    t, l = baseline()
    r = rec.reconcile(t, l)
    assert r["decision"] == rec.PASS
    assert r["n_differences"] == 0
    assert r["mode"] == "SELF_CHECK_ONLY"


@pytest.mark.parametrize("kind,expected", [
    ("order_set_extra_local", "HALT:ORDER_SET"),
    ("field_code", "HALT:FIELD"),
    ("field_side", "HALT:FIELD"),
    ("field_status", "HALT:FIELD"),
    ("field_price", "HALT:FIELD"),
    ("fill_quantity", "HALT:FILL"),
    ("fill_duplicate", "HALT:FILL"),
    ("fee", "HALT:FEE"),
    ("position", "HALT:POSITION"),
    ("cash", "HALT:CASH"),
])
def test_p1_each_injected_difference_is_detected(kind, expected):
    """每条注入差异必须独立产生 HALT 与对应原因码；n_detected/n_injected=1.00。"""
    t, l = baseline()
    if kind == "order_set_extra_local":
        extra = copy.deepcopy(loc_order(l))
        extra["client_order_id"] = "SIM-EXTRA"
        l["orders"].append(extra)
    elif kind == "field_code":
        loc_order(l)["code"] = "000001"
    elif kind == "field_side":
        loc_order(l)["side"] = "2"
    elif kind == "field_status":
        loc_order(l)["status"] = "4"
    elif kind == "field_price":
        loc_order(l)["price"] = 10.01
    elif kind == "fill_quantity":
        loc_order(l)["quantity"] = 99
    elif kind == "fill_duplicate":
        # 两臂各加一条同 fill_id 的回报，数量与费用同步 ⇒ 只在重复检测处停机
        e2 = copy.deepcopy(ext_order(t))
        t["execution_reports"].append(e2)
        l2 = copy.deepcopy(loc_order(l))
        l["orders"].append(l2)
    elif kind == "fee":
        loc_order(l)["fee"] = 1.0
    elif kind == "position":
        l["positions"][0]["quantity"] = 99
    elif kind == "cash":
        l["cash"]["available"] = 98000.0
    r = rec.reconcile(t, l)
    assert r["decision"] == expected, (kind, r)
    assert r["n_differences"] >= 1
    # 期望值来自外部真值，actual 来自本地台账（方向不可颠倒）
    assert all("expected" in d and "actual" in d for d in r["differences"])


def test_p1_detection_ratio_is_one():
    """P1 可算量：10 条注入差异逐条检出。"""
    cases = ["order_set_extra_local", "field_code", "field_side", "field_status",
             "field_price", "fill_quantity", "fill_duplicate", "fee", "position", "cash"]
    detected = 0
    for kind in cases:
        t, l = baseline()
        if kind == "order_set_extra_local":
            extra = copy.deepcopy(loc_order(l))
            extra["client_order_id"] = "SIM-EXTRA"
            l["orders"].append(extra)
        elif kind == "field_code":
            loc_order(l)["code"] = "000001"
        elif kind == "field_side":
            loc_order(l)["side"] = "2"
        elif kind == "field_status":
            loc_order(l)["status"] = "4"
        elif kind == "field_price":
            loc_order(l)["price"] = 10.01
        elif kind == "fill_quantity":
            loc_order(l)["quantity"] = 99
        elif kind == "fill_duplicate":
            t["execution_reports"].append(copy.deepcopy(ext_order(t)))
            l["orders"].append(copy.deepcopy(loc_order(l)))
        elif kind == "fee":
            loc_order(l)["fee"] = 1.0
        elif kind == "position":
            l["positions"][0]["quantity"] = 99
        elif kind == "cash":
            l["cash"]["available"] = 98000.0
        if rec.reconcile(t, l)["decision"].startswith("HALT:"):
            detected += 1
    assert detected / len(cases) == 1.0


# ---------------------------------------------------------------- P2 / 空输入


@pytest.mark.parametrize("kind", [
    "truth_none", "missing_keys", "empty_reports", "empty_local",
    "report_missing_fee", "unfinished_not_list", "local_positions_missing",
])
def test_p2_empty_or_broken_inputs_are_no_inputs(kind):
    """空/坏输入 ⇒ NO_INPUTS 停机，绝不是 PASS 或「无差异」。"""
    t, l = baseline()
    if kind == "truth_none":
        t = None
    elif kind == "missing_keys":
        t.pop("execution_reports")
    elif kind == "empty_reports":
        t["execution_reports"] = []
    elif kind == "empty_local":
        l["orders"] = []
    elif kind == "report_missing_fee":
        ext_order(t).pop("commission")
    elif kind == "unfinished_not_list":
        t["unfinished_orders"] = None
    elif kind == "local_positions_missing":
        l.pop("positions")
    r = rec.reconcile(t, l)
    assert r["decision"] == rec.NO_INPUTS, (kind, r)


def test_p2_four_canonical_inputs_ratio():
    """P2 可算量：四类空/坏输入 4/4 停机。"""
    kinds = ["missing_keys", "empty_reports", "empty_local", "report_missing_fee"]
    n = 0
    for kind in kinds:
        t, l = baseline()
        if kind == "missing_keys":
            t.pop("execution_reports")
        elif kind == "empty_reports":
            t["execution_reports"] = []
        elif kind == "empty_local":
            l["orders"] = []
        elif kind == "report_missing_fee":
            ext_order(t).pop("commission")
        if rec.reconcile(t, l)["decision"] == rec.NO_INPUTS:
            n += 1
    assert n / 4 == 1.0


# ---------------------------------------------------------------- P4 / 执行闸门


def test_p4_execute_is_always_denied():
    p = subprocess.run([PY, str(SCRIPTS / "sim_drill_driver.py"), "--execute"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 4
    assert "NOT_AUTHORIZED" in p.stdout
    assert "未发任何委托" in p.stdout


def test_p4_execute_even_with_selfcheck_flag_is_denied():
    p = subprocess.run([PY, str(SCRIPTS / "sim_drill_driver.py"),
                        "--execute", "--self-check"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 4
    assert "NOT_AUTHORIZED" in p.stdout


def test_p4_no_mode_is_error():
    p = subprocess.run([PY, str(SCRIPTS / "sim_drill_driver.py")],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2


# ---------------------------------------------------------------- P5 / 五项演练


def test_p5_five_scenarios_each_have_a_record():
    checks, events = driver.run_scenarios()
    assert len(checks) == 5
    assert tuple(c["id"] for c in checks) == tuple(f"S{i}" for i in range(1, 6))
    assert all(c["ok"] for c in checks), [c for c in checks if not c["ok"]]
    assert len(events) >= 5
    # 熔断必须明确「未下真单、未建 v3、未改 stock_weight」
    s5 = checks[4]["evidence"]
    assert s5["real_order_sent"] is False
    assert s5["v3_config_created"] is False
    assert s5["stock_weight_unchanged"] is True


def test_p5_selfcheck_mode_is_not_a_real_drill():
    r = driver.self_check(day="20261114", write=False)
    assert r["decision"] == "SELF_CHECK_PASS"
    assert r["mode"] == "SELF_CHECK_ONLY"
    assert r["no_order_sent"] is True and r["offline_only"] is True
    assert r["not_wired_into_daily_chain"] is True
    assert r["reconcile_baseline"]["decision"] == rec.PASS


def test_p5_path_guard_rejects_ledger_and_non_selftest_names():
    with pytest.raises(RuntimeError):
        driver._assert_safe_paths(driver.SIM_ORDERS / "20261201.jsonl",
                                  driver.SIM_RECONCILE / "selftest_x_driver.json")
    with pytest.raises(RuntimeError):
        driver._assert_safe_paths(driver.FWD / "ledger" / "selftest_x.jsonl",
                                  driver.SIM_RECONCILE / "selftest_x_driver.json")


# ---------------------------------------------------------------- 硬边界与冻结


@pytest.mark.parametrize("rel", [
    "scripts/paper_account_adapter.py",
    "scripts/sim_reconcile.py",
    "scripts/sim_drill_driver.py",
])
def test_no_order_calls_in_any_new_or_modified_script(rel):
    pattern = re.compile(r"\border_[A-Za-z_][A-Za-z0-9_]*\s*\(")
    assert pattern.findall((ROOT / rel).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("rel", [PREREG, AUTH])
def test_prereg_and_authorization_are_frozen(rel):
    raw = rel.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    frozen = Path(str(rel) + ".sha256").read_text(encoding="utf-8").split()[0]
    assert hashlib.sha256(raw).hexdigest() == frozen


def test_artifacts_are_lf_and_not_in_ledger():
    orders = ROOT / "research/stock_pick_2026-10-25/forward/sim_orders/selftest_20261114.jsonl"
    summary = ROOT / "research/stock_pick_2026-10-25/forward/sim_reconcile/selftest_20261114_driver.json"
    if not orders.is_file() or not summary.is_file():
        pytest.skip("自检产物尚未写入（--write 生成）")
    for p in (orders, summary):
        b = p.read_bytes()
        assert b"\r\n" not in b and not b.startswith(b"\xef\xbb\xbf")
        assert "ledger" not in p.parts
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["mode"] == "SELF_CHECK_ONLY"


# ---------------------------------------------------------------- 适配器只读真值


class _FakeGM:
    def __init__(self, *, with_reports: bool = True):
        self._with_reports = with_reports
        self.calls: list[str] = []

    def set_token(self, v): self.calls.append("set_token")
    def set_endpoint(self, v): self.calls.append("set_endpoint")
    def account(self, **kw):
        self.calls.append("account")
        return object()
    def login(self, a): self.calls.append("login")
    def get_cash(self):
        self.calls.append("get_cash")
        return {"available": 99000.0, "frozen": 0.0}
    def get_positions(self):
        self.calls.append("get_positions")
        return [{"symbol": "SHSE.600990", "volume": 100, "available": 100}]
    def get_execution_reports(self):
        self.calls.append("get_execution_reports")
        return [{"cl_ord_id": "SIM-1", "symbol": "SHSE.600990", "side": "1",
                 "volume": 100, "price": 10.0, "status": "3",
                 "commission": 5.0, "exec_id": "F-1"}]
    def get_unfinished_orders(self):
        self.calls.append("get_unfinished_orders")
        return []


def test_adapter_read_external_truth_is_read_only():
    gm = _FakeGM()
    truth = adapter.read_external_truth(gm, {"token": "x", "account_id": "y",
                                             "endpoint": "z"})
    assert set(truth) == {"cash", "positions", "execution_reports", "unfinished_orders"}
    assert len(truth["execution_reports"]) == 1
    assert not any(c.startswith("order_") or "place" in c or "submit" in c
                   for c in gm.calls), gm.calls
    rec_obj = adapter.build_truth_record("20261114", truth, collected_at="T")
    assert rec_obj["schema_version"] == adapter.TRUTH_SCHEMA_VERSION
    assert rec_obj["fields_seen"]["execution_reports"] == sorted(
        truth["execution_reports"][0])
    # fields_seen 只能有字段名，不能带值
    assert "SIM-1" not in json.dumps(rec_obj["fields_seen"])


def test_adapter_missing_getter_fails_closed():
    class Bare:
        def set_token(self, v): pass
        def set_endpoint(self, v): pass
        def account(self, **kw): return object()
        def login(self, a): pass
        def get_cash(self): return {"available": 1.0, "frozen": 0.0}
        def get_positions(self): return []
    with pytest.raises(adapter.AdapterError, match="不猜协议"):
        adapter.read_external_truth(Bare(), {"token": "x", "account_id": "y",
                                             "endpoint": "z"})


class _ProtoLike:
    """复现 2026-11-14 云端 --truth-probe rc=2 的 protobuf 行为。

    DESCRIPTOR.fields 给出业务字段；``Extensions`` 属性访问抛 AttributeError。
    ``dir()`` 遍历会撞异常 ⇒ 必须按 DESCRIPTOR.fields 读。
    """
    DESCRIPTOR = SimpleNamespace(fields=[SimpleNamespace(name=n) for n in (
        "cl_ord_id", "symbol", "side", "volume", "price", "exec_type",
        "commission", "exec_id", "account_id")])

    def __init__(self):
        self.cl_ord_id = "SIM-PROTO-1"
        self.symbol = "SHSE.600990"
        self.side = "1"
        self.volume = 100
        self.price = 10.0
        self.exec_type = "3"
        self.commission = 5.0
        self.exec_id = "F-PROTO-1"
        self.account_id = "SECRET_ACCT"

    @property
    def Extensions(self):
        raise AttributeError("protobuf descriptor attribute")


def test_as_plain_uses_protobuf_descriptor_not_dir():
    d = adapter._as_plain(_ProtoLike())
    assert "Extensions" not in d and "DESCRIPTOR" not in d
    assert d["cl_ord_id"] == "SIM-PROTO-1"
    assert d["commission"] == 5.0


def test_external_truth_survives_real_protobuf_shape():
    class ProtoGM(_FakeGM):
        def get_execution_reports(self):
            return [_ProtoLike()]
    truth = adapter.read_external_truth(
        ProtoGM(), {"token": "x", "account_id": "y", "endpoint": "z"})
    r = adapter.build_truth_record("20261201", truth, collected_at="T")
    assert r["execution_reports"][0]["cl_ord_id"] == "SIM-PROTO-1"
    assert "SECRET_ACCT" not in json.dumps(r, ensure_ascii=False)
    assert r["redacted_fields"] == ["account_id"]


def test_cash_balance_is_independent_arithmetic():
    vals, why = rec.cash_values({"available": 99000.5, "frozen": 12.25}, "x")
    assert why is None
    assert vals["balance"] == Fraction("99000.5") + Fraction("12.25")
    assert vals["available"] == Fraction("99000.5")


#: 2026-11-14 云端只读探测（/opt/quant/.venv-gm，未登录、未下单）实测的
#: ``gmtrade.api.ExecRpt`` 字段名。固化为常量，供 FIELD_MAP 对齐复核。
GM_EXECRPT_FIELDS = frozenset({
    "account_id", "account_name", "amount", "cl_ord_id", "commission", "cost",
    "created_at", "exec_id", "exec_type", "ord_rej_reason",
    "ord_rej_reason_detail", "order_id", "position_effect", "price", "side",
    "strategy_id", "symbol", "volume",
})


def test_field_map_matches_real_gmtrade_execrpt():
    """预登记结构假设 1 的实证：每个标准字段都能在真实 SDK 字段里找到候选。"""
    for std, cands in rec.FIELD_MAP.items():
        assert any(c in GM_EXECRPT_FIELDS for c in cands), (std, cands)
    # status 在成交回报里实际取 exec_type（执行类型，非订单终态）——
    # 语义对齐须 12-01 用真实回报确认，见实施记录。
    assert "status" not in GM_EXECRPT_FIELDS
    assert "exec_type" in rec.FIELD_MAP["status"]


def test_truth_record_redacts_account_identity():
    """SDK 对象带 account_id 等身份字段；落盘记录必须脱敏且不丢业务字段。"""
    truth = {
        "cash": {"available": 1.0, "frozen": 0.0, "account_id": "SECRET_ACCT"},
        "positions": [{"symbol": "SHSE.600990", "volume": 100,
                       "available": 100, "account_id": "SECRET_ACCT"}],
        "execution_reports": [{"cl_ord_id": "SIM-1", "symbol": "SHSE.600990",
                               "side": "1", "volume": 100, "price": 10.0,
                               "exec_type": "3", "commission": 5.0,
                               "exec_id": "F-1", "account_id": "SECRET_ACCT",
                               "strategy_id": "SECRET_STRAT"}],
        "unfinished_orders": [{"cl_ord_id": "SIM-2", "status": "1",
                               "account_name": "SECRET_NAME"}],
    }
    r = adapter.build_truth_record("20261201", truth, collected_at="T")
    blob = json.dumps(r, ensure_ascii=False)
    for secret in ("SECRET_ACCT", "SECRET_STRAT", "SECRET_NAME"):
        assert secret not in blob
    assert r["redacted_fields"] == ["account_id", "account_name", "strategy_id"]
    assert r["execution_reports"][0]["cl_ord_id"] == "SIM-1"
    assert r["execution_reports"][0]["commission"] == 5.0
