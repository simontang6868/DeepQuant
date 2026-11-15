"""EARLY-IMPL-1｜成交回报对账器（外部真值优先，纯函数 + CLI）。

预登记：``docs/SIM_DRILL_PREP_PREREG_2026-11-14.md``
方案：``docs/SIM_REBALANCE_DRILL_2026-12-01.md`` §二

⭐ 三条纪律
----------
1. **外部真值优先**：期望值一律来自 gm-paper 回报 / 未完结委托 / 持仓 / 资金，
   本地台账只作被比对对象。不拿自己的输出自证。
2. **空输入 ≠ 无差异**：外部回报缺失 / 空 / 不可解析 / 必需字段缺失，一律
   ``NO_INPUTS`` 停机。本机项目已 12 次复发「没检出 ≠ 没问题」。
3. **失败关闭**：任一差异即 ``HALT:*``，逐条留痕，不自动忽略、不重跑凑通过。

⚠️ 金额全部用 ``Fraction(str(x))`` 独立算术，不引入浮点容差常量
（避免「容差拍脑袋」与「按产物存储精度定下限」两类已复发错误）。
⚠️ 本模块不联网、不读凭证、不调用任何下单接口、不写正式台账。

用法：
    python scripts/sim_reconcile.py --truth T.json --local L.json --out R.json
    python scripts/sim_reconcile.py --self-check
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "sim_reconcile_v1"
PASS = "PASS"
NO_INPUTS = "NO_INPUTS"
RC = {PASS: 0, NO_INPUTS: 4, "ERROR": 2}

#: 标准名 -> SDK 候选字段名。**显式映射，不猜协议**；找不到即 NO_INPUTS。
FIELD_MAP: dict[str, tuple[str, ...]] = {
    "client_order_id": ("cl_ord_id", "client_order_id"),
    "code": ("symbol", "code"),
    "side": ("side",),
    "quantity": ("volume", "filled_volume", "quantity"),
    "price": ("price", "filled_price"),
    "status": ("status", "exec_type", "ord_status"),
    "fee": ("commission", "fee"),
    "fill_id": ("exec_id", "fill_id"),
}
REQUIRED_ORDER_FIELDS = tuple(FIELD_MAP)
POS_MAP = {"code": ("symbol", "code"),
           "quantity": ("volume", "quantity"),
           "available": ("available", "available_volume")}
CASH_KEYS = ("available", "frozen")


def _pick(row: dict[str, Any], cands: tuple[str, ...]) -> Any | None:
    for c in cands:
        if c in row and row[c] is not None:
            return row[c]
    return None


def _frac(v: Any, label: str) -> Fraction:
    """十进制字符串 → 精确分数；不可解析 ⇒ 失败关闭（不吞异常）。"""
    try:
        return Fraction(str(v))
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise ValueError(f"{label} 不是可解析数值：{v!r}") from exc


def normalize_orders(rows: Any, label: str
                     ) -> tuple[dict[str, dict[str, Any]], str | None]:
    """把原始回报行聚合成 ``cid -> 累计量``；缺任一必需字段 ⇒ 不可用。

    同一 ``client_order_id`` 多笔 = 部分成交 ⇒ 累计 quantity / fee，
    prices 与 fill_ids 保留**全部**，用于逐笔价格与重复回报检测。
    """
    if not isinstance(rows, list):
        return {}, f"{label} 不是列表"
    out: dict[str, dict[str, Any]] = {}
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            return {}, f"{label}[{i}] 不是对象"
        vals: dict[str, Any] = {}
        for std, cands in FIELD_MAP.items():
            v = _pick(raw, cands)
            if v is None:
                return {}, (f"{label}[{i}] 缺必需字段 {std}"
                            f"（候选 {list(cands)}；实际 {sorted(raw)}）")
            vals[std] = v
        cid = str(vals["client_order_id"])
        if not cid:
            return {}, f"{label}[{i}] client_order_id 为空"
        try:
            qty = _frac(vals["quantity"], f"{label}[{i}].quantity")
            price = _frac(vals["price"], f"{label}[{i}].price")
            fee = _frac(vals["fee"], f"{label}[{i}].fee")
        except ValueError as exc:
            return {}, str(exc)
        if cid not in out:
            # SDK 的 symbol 形如 SHSE.600990；与本地 6 位代码对齐，
            # 与 paper_account_adapter.normalize_symbol 同一口径（不是放宽判据）。
            out[cid] = {"code": str(vals["code"]).rsplit(".", 1)[-1],
                        "side": str(vals["side"]),
                        "status": str(vals["status"]), "prices": [], "fill_ids": [],
                        "quantity": Fraction(0), "fee": Fraction(0)}
        rec = out[cid]
        rec["prices"].append(price)
        rec["fill_ids"].append(str(vals["fill_id"]))
        rec["quantity"] += qty
        rec["fee"] += fee
    return out, None


def normalize_positions(rows: Any, label: str
                        ) -> tuple[dict[str, tuple[Fraction, Fraction]], str | None]:
    if not isinstance(rows, list):
        return {}, f"{label} 不是列表"
    out: dict[str, tuple[Fraction, Fraction]] = {}
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            return {}, f"{label}[{i}] 不是对象"
        vals: dict[str, Any] = {}
        for std, cands in POS_MAP.items():
            v = _pick(raw, cands)
            if v is None:
                return {}, f"{label}[{i}] 缺字段 {std}（候选 {list(cands)}）"
            vals[std] = v
        try:
            q = _frac(vals["quantity"], f"{label}[{i}].quantity")
            a = _frac(vals["available"], f"{label}[{i}].available")
        except ValueError as exc:
            return {}, str(exc)
        out[str(vals["code"]).rsplit(".", 1)[-1]] = (q, a)
    return out, None


def cash_values(cash: Any, label: str) -> tuple[dict[str, Fraction], str | None]:
    """``balance`` 用独立算术 available+frozen（不依赖 SDK 是否有 total 字段）。"""
    if not isinstance(cash, dict):
        return {}, f"{label} 不是对象"
    vals: dict[str, Fraction] = {}
    for k in CASH_KEYS:
        v = _pick(cash, (k,))
        if v is None:
            return {}, f"{label} 缺字段 {k}"
        try:
            vals[k] = _frac(v, f"{label}.{k}")
        except ValueError as exc:
            return {}, str(exc)
    vals["balance"] = vals["available"] + vals["frozen"]
    return vals, None


def _d(kind: str, key: str, expected: Any, actual: Any) -> dict[str, Any]:
    """差异条目：expected 来自外部真值，actual 来自本地台账。"""
    return {"kind": kind, "key": key,
            "expected": str(expected), "actual": str(actual)}


def _result(decision: str, diffs: list[dict[str, Any]], *,
            n_external: int = 0, n_local: int = 0,
            mode: str = "SELF_CHECK_ONLY") -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "decision": decision,
            "mode": mode, "n_differences": len(diffs), "differences": diffs,
            "n_external_orders": n_external, "n_local_orders": n_local,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def reconcile(truth: Any, local: Any, *, mode: str = "SELF_CHECK_ONLY") -> dict[str, Any]:
    """四类对象全量比对。**纯函数**：不读文件、不联网、不改状态。

    判定顺序冻结：输入可用性 → 委托集合双向差集 → 逐字段 → 成交累计/重复回报
    → 费用 → 持仓 → 资金 → PASS。任一步停机即返回，不继续掩盖后续差异。
    """
    # ---- 1. 输入可用性：NO_INPUTS 与「无差异」是两条不同代码路径 ----
    if not isinstance(truth, dict) or not isinstance(local, dict):
        return _result(NO_INPUTS, [_d("INPUT", "top_level", "dict",
                                      f"truth={type(truth).__name__},local={type(local).__name__}")],
                       mode=mode)
    missing = [k for k in ("cash", "positions", "execution_reports",
                           "unfinished_orders") if k not in truth]
    if missing:
        return _result(NO_INPUTS, [_d("INPUT", "truth_keys", "all present",
                                      f"missing={missing}")], mode=mode)
    if not isinstance(truth["unfinished_orders"], list):
        return _result(NO_INPUTS, [_d("INPUT", "unfinished_orders", "list",
                                      type(truth["unfinished_orders"]).__name__)], mode=mode)
    if (not isinstance(truth["execution_reports"], list)
            or not truth["execution_reports"]
            or not isinstance(local.get("orders"), list)
            or not local["orders"]):
        return _result(NO_INPUTS, [_d("INPUT", "orders",
                                      "external and local both non-empty lists",
                                      "at least one is empty or not a list")], mode=mode)

    ext, why = normalize_orders(truth["execution_reports"], "external")
    if why:
        return _result(NO_INPUTS, [_d("INPUT", "external.normalize", "valid", why)], mode=mode)
    loc, why = normalize_orders(local["orders"], "local")
    if why:
        return _result(NO_INPUTS, [_d("INPUT", "local.normalize", "valid", why)], mode=mode)
    ext_pos, why = normalize_positions(truth["positions"], "external.positions")
    if why:
        return _result(NO_INPUTS, [_d("INPUT", "external.positions", "valid", why)], mode=mode)
    loc_pos, why = normalize_positions(local.get("positions"), "local.positions")
    if why:
        return _result(NO_INPUTS, [_d("INPUT", "local.positions", "valid", why)], mode=mode)
    ext_cash, why = cash_values(truth["cash"], "external.cash")
    if why:
        return _result(NO_INPUTS, [_d("INPUT", "external.cash", "valid", why)], mode=mode)
    loc_cash, why = cash_values(local.get("cash"), "local.cash")
    if why:
        return _result(NO_INPUTS, [_d("INPUT", "local.cash", "valid", why)], mode=mode)

    n_ext, n_loc = len(ext), len(loc)

    # ---- 2. 委托集合：双向差集 ----
    if set(ext) != set(loc):
        only_ext = sorted(set(ext) - set(loc))
        only_loc = sorted(set(loc) - set(ext))
        return _result("HALT:ORDER_SET",
                       [_d("ORDER_SET", "client_order_id",
                           f"external_only={only_ext}", f"local_only={only_loc}")],
                       n_external=n_ext, n_local=n_loc, mode=mode)

    # ---- 3. 逐字段：代码 / 方向 / 状态 / 逐笔价格 ----
    for cid in sorted(ext):
        e, l = ext[cid], loc[cid]
        for fld in ("code", "side", "status"):
            if e[fld] != l[fld]:
                return _result("HALT:FIELD", [_d("FIELD", f"{cid}.{fld}", e[fld], l[fld])],
                               n_external=n_ext, n_local=n_loc, mode=mode)
        if e["prices"] != l["prices"]:
            return _result("HALT:FIELD", [_d("FIELD", f"{cid}.prices",
                                             e["prices"], l["prices"])],
                           n_external=n_ext, n_local=n_loc, mode=mode)

    # ---- 4. 成交累计与重复回报 ----
    for cid in sorted(ext):
        if ext[cid]["quantity"] != loc[cid]["quantity"]:
            return _result("HALT:FILL", [_d("FILL", f"{cid}.filled_quantity",
                                            ext[cid]["quantity"], loc[cid]["quantity"])],
                           n_external=n_ext, n_local=n_loc, mode=mode)
    all_fill_ids = [f for cid in sorted(ext) for f in ext[cid]["fill_ids"]]
    if len(all_fill_ids) != len(set(all_fill_ids)):
        dup = sorted({f for f in all_fill_ids if all_fill_ids.count(f) > 1})
        return _result("HALT:FILL", [_d("FILL", "duplicate_fill_id", "unique", dup)],
                       n_external=n_ext, n_local=n_loc, mode=mode)

    # ---- 5. 费用 ----
    for cid in sorted(ext):
        if ext[cid]["fee"] != loc[cid]["fee"]:
            return _result("HALT:FEE", [_d("FEE", f"{cid}.fee",
                                           ext[cid]["fee"], loc[cid]["fee"])],
                           n_external=n_ext, n_local=n_loc, mode=mode)

    # ---- 6. 持仓 / 可用数 ----
    if ext_pos != loc_pos:
        return _result("HALT:POSITION", [_d("POSITION", "positions",
                                            {k: [str(x) for x in v] for k, v in ext_pos.items()},
                                            {k: [str(x) for x in v] for k, v in loc_pos.items()})],
                       n_external=n_ext, n_local=n_loc, mode=mode)

    # ---- 7. 资金 available / frozen / balance ----
    if ext_cash != loc_cash:
        return _result("HALT:CASH", [_d("CASH", k, ext_cash[k], loc_cash[k])
                                     for k in sorted(ext_cash) if ext_cash[k] != loc_cash[k]],
                       n_external=n_ext, n_local=n_loc, mode=mode)

    return _result(PASS, [], n_external=n_ext, n_local=n_loc, mode=mode)


def _atomic_write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def selfcheck_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """离线基线：外部真值与本地台账**完全一致**，用于自检应得 PASS。"""
    order = {"cl_ord_id": "SIM-SELFCHECK-001", "symbol": "SHSE.600990",
             "side": "1", "volume": 100, "price": 10.0,
             "status": "3", "commission": 5.0, "exec_id": "F-001"}
    local_order = {"client_order_id": "SIM-SELFCHECK-001", "code": "600990",
                   "side": "1", "quantity": 100, "price": 10.0,
                   "status": "3", "fee": 5.0, "fill_id": "F-001"}
    truth = {"cash": {"available": 99000.0, "frozen": 0.0},
             "positions": [{"symbol": "SHSE.600990", "volume": 100, "available": 100}],
             "execution_reports": [order], "unfinished_orders": []}
    local = {"orders": [local_order],
             "cash": {"available": 99000.0, "frozen": 0.0},
             "positions": [{"code": "600990", "quantity": 100, "available": 100}]}
    return truth, local


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="成交回报对账（外部真值优先，不下单）")
    ap.add_argument("--truth", help="外部真值 JSON（paper_external_truth_v1）")
    ap.add_argument("--local", help="本地演练台账 JSON（orders/cash/positions）")
    ap.add_argument("--out", help="结果 JSON 输出路径")
    ap.add_argument("--differences-out", help="差异逐条留痕 JSONL（不写正式 corrections.jsonl）")
    ap.add_argument("--mode", default="SELF_CHECK_ONLY")
    ap.add_argument("--self-check", action="store_true", help="用离线基线跑一次，零文件写入")
    args = ap.parse_args(argv)

    try:
        if args.self_check:
            truth, local = selfcheck_fixture()
        else:
            if not args.truth or not args.local:
                print("[sim-reconcile] ✗ 缺 --truth / --local", file=sys.stderr)
                return RC["ERROR"]
            truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
            local = json.loads(Path(args.local).read_text(encoding="utf-8"))
        res = reconcile(truth, local, mode=args.mode)
    except Exception as exc:
        print(f"[sim-reconcile] ✗ 未预期异常 {type(exc).__name__}；失败关闭",
              file=sys.stderr)
        return RC["ERROR"]

    print(f"[sim-reconcile] {res['decision']}｜差异 {res['n_differences']} 条"
          f"｜外部委托 {res['n_external_orders']} 本地 {res['n_local_orders']}"
          f"｜mode={res['mode']}")
    if args.out:
        _atomic_write(Path(args.out),
                      json.dumps(res, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    if args.differences_out and res["differences"]:
        _atomic_write(Path(args.differences_out),
                      "".join(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n"
                              for d in res["differences"]))
    if res["decision"] == PASS:
        return RC[PASS]
    if res["decision"] == NO_INPUTS:
        return RC[NO_INPUTS]
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
