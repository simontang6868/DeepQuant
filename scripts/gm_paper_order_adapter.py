#!/usr/bin/env python3
"""R8 §5 / ``PA-GM-PROBE-1``：gm-paper **报单探针适配器**（独立类，只实现 + 离线测试）。

## ⭐ 三道闸门，任何一道不过都不发单

1. **订单规则**：d4 严格口径（沪深主板）／非 ST／流动性达标／限价（**禁市价**）／
   一手金额 ≤ 单只预算／恰好 2 只各 1 手／旧台账 5 笔计划单整组禁用（含 `301329`）。
2. **授权闸门**：每次动作前调用 ``paper_authorization_guard.check`` 判 ``ALLOW``。
   ⭐ 本模块**不创建、不修改、不代填**任何授权文件；只读入已存在的 doc 交给守卫判定。
3. **执行闸门**：⭐ ``--execute`` **恒返回 ``NOT_AUTHORIZED``**（rc=4），
   直到用户另行书面授权探针。探针授权 ≠ U-16；本模块**不执行**、**不向任何账户报单**。

``broker`` 只是可注入的**接口契约占位**：离线测试注入假实现，但执行闸门恒关 ⇒
**它永远不会被调用**。真实 SDK 下单函数名只作为字符串常量登记在
``SDK_ORDER_FUNCTIONS``，本模块**不 import、不调用**任何下单函数（隔离检查器可核）。

## 幂等

``client_order_id = "PROBE-" + sha256(f"{probe_id}|{code}|{side}|{day}|{lots}")[:24]``
同一四元组重放得到同一个 cid ⇒ 不产生重复委托。

## 启动校验

启动时核对冻结预登记 ``docs/GM_PAPER_PROBE_PREREG_2026-11-15.md`` 的 sha256 与
硬编码常量、以及全部参数常量；不一致 ⇒ ``HALT:PREREG_MISMATCH``（失败关闭）。

## 用法

    python scripts/gm_paper_order_adapter.py --self-check   # 纯离线，临时文件，不碰真实授权文件
    python scripts/gm_paper_order_adapter.py --execute      # 恒 NOT_AUTHORIZED rc=4

⚠️ ``StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY``｜``MaturityLevel=DESIGN_VALIDATED_OFFLINE``
⚠️ 探针结果排除 G2／S4／一切经济性判断；11-30 前账户须回空仓。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_authorization_guard as guard  # noqa: E402

#: 冻结预登记（**先于本文件第一行代码冻结**）。
PREREG_REL = "docs/GM_PAPER_PROBE_PREREG_2026-11-15.md"
PREREG_SHA256 = "ebe51bec9202f4660d9b568d924f706b0a85f742426d1c0db8a0eb43d5a8e502"

#: 参数硬编码清单（与预登记 §0 逐项一致；改一个字都必须新预登记）。
N_SYMBOLS = 2
LOTS_PER_SYMBOL = 1
SHARES_PER_LOT = 100
BUDGET_PER_SYMBOL = 4000          # 元，10 万 × 20% ÷ 5
MAX_NOTIONAL = 100000             # 元
PROBE_TAG = "PROBE"
AUTH_ACTION = "sim_rebalance_order"
ORDER_TYPE_LIMIT = "LIMIT"
NOT_AUTHORIZED = "NOT_AUTHORIZED"

#: 旧台账（11-13 影子对账）5 笔计划单整组禁用；其中 301329 为创业板。
LEGACY_FORBIDDEN_CODES = frozenset({
    "600990", "603977", "002012", "301329", "600191",
})
#: d4 严格口径：沪深主板。排除创业板 300/301、科创板 688/689、北交所 8/4。
ALLOWED_BOARD_PREFIXES = ("60", "00")

#: 真实 SDK 下单函数名（**只登记契约名，不 import、不调用**）。
SDK_ORDER_FUNCTIONS = ("order_volume", "order_value", "order_percent",
                       "order_target_value", "order_cancel")


class Halt(RuntimeError):
    """失败关闭：预登记/参数不一致一律停机。"""


def verify_prereg() -> str:
    """启动校验：预登记 sha256 与冻结常量逐位一致。"""
    p = ROOT / PREREG_REL
    if not p.exists():
        raise Halt("PREREG_MISSING")
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if h != PREREG_SHA256:
        raise Halt("PREREG_MISMATCH")
    canonical = (N_SYMBOLS, LOTS_PER_SYMBOL, SHARES_PER_LOT, BUDGET_PER_SYMBOL,
                 MAX_NOTIONAL, PROBE_TAG, AUTH_ACTION, ORDER_TYPE_LIMIT)
    if canonical != (2, 1, 100, 4000, 100000, "PROBE",
                     "sim_rebalance_order", "LIMIT"):
        raise Halt("PREREG_PARAMS_MISMATCH")
    return h


def normalize_code(code: str) -> str | None:
    """取 6 位代码；带 ``SHSE.`` / ``SZSE.`` 前缀也接受。不做任何猜测补齐。"""
    s = str(code or "").strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s if len(s) == 6 and s.isdigit() else None


def board_ok(code: str) -> bool:
    c = normalize_code(code)
    if c is None:
        return False
    if c in LEGACY_FORBIDDEN_CODES:
        return False
    if not c.startswith(ALLOWED_BOARD_PREFIXES):
        return False
    return not c.startswith(("300", "301", "688", "689", "8", "4"))


def client_order_id(*, probe_id: str, code: str, side: str,
                    day: str, lots: int) -> str:
    """幂等键：同一四元组恒定。独立算术（hashlib），不调用被测函数。"""
    c = normalize_code(code)
    raw = f"{probe_id}|{c}|{side}|{day}|{lots}".encode("utf-8")
    return PROBE_TAG + "-" + hashlib.sha256(raw).hexdigest()[:24]


@dataclass(frozen=True)
class OrderRequest:
    probe_id: str
    day: str                  # YYYYMMDD
    code: str
    side: str                 # BUY / SELL
    price: float | None       # None ⇒ 市价 ⇒ 拒绝
    lots: int
    notional: float           # 名义金额（元）
    is_st: bool
    liquidity_ok: bool
    n_symbols: int
    available: float
    cumulative: float
    now: str                  # 必须带时区
    auth_path: Path


def _frac(v: float | int | str, label: str) -> Fraction:
    try:
        return Fraction(str(v))
    except Exception as exc:  # noqa: BLE001
        raise Halt(f"BAD_{label}") from exc


def validate_order(req: OrderRequest) -> dict:
    """订单规则：任一不满足 ⇒ DENY（失败关闭，不猜、不补默认值）。"""
    checks = {
        "limit_price": req.price is not None,
        "no_market_order": req.price is not None,
        "lots": req.lots == LOTS_PER_SYMBOL,
        "board_d4": board_ok(req.code),
        "not_st": not req.is_st,
        "liquidity": bool(req.liquidity_ok),
        "n_symbols": req.n_symbols == N_SYMBOLS,
        "budget": False,
        "notional": False,
        "day": len(req.day) == 8 and req.day.isdigit(),
        "side": req.side in ("BUY", "SELL"),
        "probe_id": bool(str(req.probe_id).strip()),
        "now_tz": ("+08:00" in req.now or req.now.endswith("Z")),
    }
    try:
        price = _frac(req.price, "PRICE")
        notional = _frac(req.notional, "NOTIONAL")
        per_symbol = price * SHARES_PER_LOT * req.lots
        checks["budget"] = (price > 0 and per_symbol <= BUDGET_PER_SYMBOL
                            and notional <= BUDGET_PER_SYMBOL)
        checks["notional"] = (notional > 0 and notional <= BUDGET_PER_SYMBOL
                              and notional <= MAX_NOTIONAL)
    except Halt:
        checks["budget"] = checks["notional"] = False
    failed = [k for k, v in checks.items() if not v]
    return {"ok": not failed, "failed": failed, "checks": checks}


class GmPaperProbeAdapter:
    """独立适配器类。`broker` 只作契约占位，**执行闸门恒关 ⇒ 从不被调用**。"""

    def __init__(self, broker=None):
        self.broker = broker
        self.broker_calls = 0

    def evaluate(self, req: OrderRequest) -> dict:
        """只做判定与意图构造，**不报单**。返回 ALLOW / DENY。"""
        verify_prereg()
        order = validate_order(req)
        if not order["ok"]:
            return {"decision": guard.DENY, "reason": "ORDER_RULE:" + ",".join(order["failed"]),
                    "order_checks": order["checks"], "intent": None}

        # ⭐ 授权闸门：每次动作前必须经 paper_authorization_guard 判 ALLOW。
        doc = guard.load_doc(Path(req.auth_path))
        if doc is None:
            return {"decision": guard.DENY, "reason": "NO_AUTH",
                    "order_checks": order["checks"], "intent": None}
        verdict = guard.check(doc=doc, now=req.now, action=AUTH_ACTION,
                              notional=req.notional, cumulative=req.cumulative,
                              available=req.available)
        if verdict["decision"] != guard.ALLOW:
            return {"decision": guard.DENY,
                    "reason": "AUTH_" + str(verdict.get("reason") or "DENIED"),
                    "order_checks": order["checks"], "auth_checks": verdict.get("checks", {}),
                    "intent": None}

        cid = client_order_id(probe_id=req.probe_id, code=req.code, side=req.side,
                              day=req.day, lots=req.lots)
        intent = {
            "client_order_id": cid,
            "code": normalize_code(req.code),
            "side": req.side,
            "quantity": req.lots * SHARES_PER_LOT,
            "price": str(_frac(req.price, "PRICE")),
            "status": "INTENT_ONLY_NOT_SENT",
            "fee": "0",
            "fill_id": None,
            "order_type": ORDER_TYPE_LIMIT,
            "mode": PROBE_TAG,
        }
        return {"decision": guard.ALLOW, "reason": None,
                "order_checks": order["checks"], "auth_checks": verdict.get("checks", {}),
                "intent": intent, "broker_called": False}

    def execute(self, req: OrderRequest) -> dict:
        """⭐ 执行闸门恒关：即使 evaluate 判 ALLOW，也**绝不调用 broker**。"""
        r = self.evaluate(req)
        if r["decision"] != guard.ALLOW:
            return r
        return {"decision": NOT_AUTHORIZED,
                "reason": "PROBE_NOT_AUTHORIZED（探针授权 ≠ U-16；--execute 恒拒绝）",
                "intent_constructed": True, "broker_called": False,
                "note": "零真实委托；LIVE_ENABLED=false 不变"}


def _auth_doc(*, channel="gm-paper", schema="paper_auth_v1",
              not_after="2026-12-02T23:59:00+08:00", scopes=None,
              max_notional=100000, tamper=False) -> dict:
    """离线夹具：构造一个哈希自洽的授权 doc。**不落盘真实授权文件**。"""
    d = {"schema_version": schema, "channel": channel,
         "account_ref": "SELFTEST", "max_notional": max_notional,
         "not_after": not_after,
         "scopes": list(scopes if scopes is not None else guard.FROZEN_SCOPES),
         "statement_ref": "3207bff41a58", "issued_by": "SELFTEST",
         "issued_at": "2026-11-14T22:40:00+08:00"}
    d["sha256"] = guard.compute_hash(d)
    if tamper:
        d["sha256"] = "0" * 64
    return d


def _write_doc(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def _req(auth: Path, **kw) -> OrderRequest:
    base = dict(probe_id="PROBE-SELFTEST", day="20261117", code="600990",
                side="BUY", price=31.50, lots=1, notional=3150.0,
                is_st=False, liquidity_ok=True, n_symbols=2,
                available=100000.0, cumulative=0.0,
                now="2026-11-17T09:35:00+08:00", auth_path=auth)
    # ⚠️ 600990 属旧台账整组禁用；自检默认换成合规主板标的。
    base["code"] = "600519"
    base.update(kw)
    return OrderRequest(**base)


def self_check() -> dict:
    """纯离线自检：临时目录、假授权文件、假 broker；**不碰真实授权文件**。"""
    verify_prereg()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        good = d / "auth_ok.json"
        _write_doc(good, _auth_doc())
        adapter = GmPaperProbeAdapter(broker=object())

        ok = adapter.evaluate(_req(good))
        assert ok["decision"] == guard.ALLOW, ok
        assert ok["broker_called"] is False
        cid = ok["intent"]["client_order_id"]
        assert cid.startswith("PROBE-") and len(cid) == 30
        # 独立算术：cid 期望值不由被测函数推导
        raw = f"PROBE-SELFTEST|600519|BUY|20261117|1".encode("utf-8")
        assert cid == "PROBE-" + hashlib.sha256(raw).hexdigest()[:24]
        assert adapter.evaluate(_req(good))["intent"]["client_order_id"] == cid

        # ⭐ 每条 DENY：六项授权校验各一 + 订单规则
        denies = {
            "schema": d / "a1.json", "channel": d / "a2.json",
            "expiry": d / "a3.json", "scope": d / "a4.json",
            "notional": d / "a5.json", "hash": d / "a6.json",
        }
        _write_doc(denies["schema"], _auth_doc(schema="paper_auth_v0"))
        _write_doc(denies["channel"], _auth_doc(channel="other"))
        _write_doc(denies["expiry"], _auth_doc(not_after="2026-11-01T00:00:00+08:00"))
        _write_doc(denies["scope"], _auth_doc(scopes=["query_cash"]))
        _write_doc(denies["notional"], _auth_doc(max_notional=100))
        _write_doc(denies["hash"], _auth_doc(tamper=True))
        for key, p in denies.items():
            assert adapter.evaluate(_req(p))["decision"] == guard.DENY, key

        rule_denies = {
            "market_order": _req(good, price=None),
            "big_notional": _req(good, price=99.0, notional=9900.0),
            "gem_board": _req(good, code="301329"),
            "star_board": _req(good, code="688001"),
            "legacy_group": _req(good, code="603977"),
            "st": _req(good, is_st=True),
            "illiquid": _req(good, liquidity_ok=False),
            "two_lots": _req(good, lots=2),
            "one_symbol": _req(good, n_symbols=1),
        }
        for key, r in rule_denies.items():
            assert adapter.evaluate(r)["decision"] == guard.DENY, key

        # ⭐ 执行闸门：有效请求仍恒 NOT_AUTHORIZED，broker 从不被调用
        ex = adapter.execute(_req(good))
        assert ex["decision"] == NOT_AUTHORIZED and ex["broker_called"] is False
        assert adapter.broker_calls == 0

    return {"decision": "PASS", "statistical_verdict": "ENGINEERING_SELF_CHECK_ONLY",
            "maturity_level": "DESIGN_VALIDATED_OFFLINE",
            "checks": ["prereg_sha", "idempotent_cid", "auth_deny_6of6",
                       "order_rule_deny_9of9", "execute_not_authorized",
                       "broker_never_called"],
            "n_real_orders": 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="gm-paper 探针适配器（只实现 + 离线测试，不执行）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--self-check", action="store_true")
    g.add_argument("--execute", action="store_true",
                   help="恒 NOT_AUTHORIZED；不加载请求、不发单")
    args = ap.parse_args(argv)
    try:
        verify_prereg()
    except Halt as e:
        print(f"[probe] HALT:{e}")
        return 3
    if args.execute:
        # ⭐ 不构造请求、不读授权文件、不接触 broker：执行闸门在最前面。
        print(json.dumps({"decision": NOT_AUTHORIZED,
                          "reason": "探针书面授权尚未发出；探针授权 ≠ U-16",
                          "n_real_orders": 0}, ensure_ascii=False))
        return 4
    try:
        print(json.dumps(self_check(), ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[probe] HALT:SELF_CHECK_FAILED:{type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
