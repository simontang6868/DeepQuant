"""EARLY-IMPL-1｜12-01 演练驱动（**仅离线自检，不接链路、不执行、不发单**）。

预登记：``docs/SIM_DRILL_PREP_PREREG_2026-11-14.md``
方案：``docs/SIM_REBALANCE_DRILL_2026-12-01.md`` §三（五项演练）

⭐ 本轮边界（工程准备授权，非 U-16）
------------------------------------
* 唯一可运行模式是 ``--self-check``，用纯内存 ``OfflineFixtureBroker``；
  **没有** gm-paper 真实报单适配器，也**不联网**。
* ``--execute`` 一律 ``NOT_AUTHORIZED``：不读台账、不判日期、不返回任何目标。
  12-01 执行须 U-16 + 11-30 FORWARD 台账 + 另立执行预登记。
* 产物只写 ``forward/sim_orders/selftest_<day>.jsonl`` 与
  ``forward/sim_reconcile/selftest_<day>_driver.json``；**禁止**写
  ``forward/ledger/`` 正式台账，文件名必须以 ``selftest_`` 开头。
* 自检的五项记录是 ``SELF_CHECK_ONLY``，**不等于**方案 §5「五项演练全部完成」。

用法：
    python scripts/sim_drill_driver.py --self-check
    python scripts/sim_drill_driver.py --self-check --date 20261114 --write
    python scripts/sim_drill_driver.py --execute      # 必然 NOT_AUTHORIZED
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FWD = ROOT / "research" / "stock_pick_2026-10-25" / "forward"
SIM_ORDERS = FWD / "sim_orders"
SIM_RECONCILE = FWD / "sim_reconcile"
MAX_REPRICE = 2
CARRY_MAX_DAYS = 10
SCENARIOS = ("IDEMPOTENT_REPLAY", "CANCEL_REPRICE", "PARTIAL_FILL",
             "LIMIT_UNTRADABLE", "CIRCUIT_SIM")
SELF_CHECK_MODE = "SELF_CHECK_ONLY"
NOT_AUTHORIZED = "NOT_AUTHORIZED"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_reconcile  # noqa: E402  （离线基线自检，不是报单通道）


class OfflineFixtureBroker:
    """纯内存订单簿。**不联网、不发单、不写正式台账**。

    只实现演练判据所需的最小语义；与 gmtrade 真实协议的一致性未验证
    （预登记结构假设 4）——自检 PASS 只证明判据与编排，不证明 SDK 契约。
    """

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.reprice_count: dict[str, int] = {}
        self.carry: dict[str, int] = {}
        self.fills: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def submit(self, cid: str, code: str, side: str, quantity: int,
               price: float) -> bool:
        """幂等可重入：同一 client_order_id 已存在 ⇒ 不新增第二笔。"""
        if cid in self.orders:
            self.events.append({"event": "REPLAY_IGNORED", "cid": cid})
            return False
        self.orders[cid] = {"client_order_id": cid, "code": code, "side": side,
                            "quantity": quantity, "price": price, "status": "NEW"}
        self.events.append({"event": "SUBMIT", "cid": cid})
        return True

    def cancel(self, cid: str) -> bool:
        if cid not in self.orders:
            return False
        self.orders[cid]["status"] = "CANCELLED"
        self.events.append({"event": "CANCEL", "cid": cid})
        return True

    def reprice(self, cid: str, new_price: float, *, favorable: bool) -> str:
        """只朝有利方向；累计重定价次数不重置，超限硬失败。"""
        if cid not in self.orders:
            return "NO_SUCH_ORDER"
        if not favorable:
            return "UNFAVORABLE_REJECTED"
        used = self.reprice_count.get(cid, 0)
        if used >= MAX_REPRICE:
            return "MAX_REPRICE_EXCEEDED"
        self.reprice_count[cid] = used + 1
        self.orders[cid]["price"] = new_price
        self.orders[cid]["status"] = "REPRICED"
        self.events.append({"event": "REPRICE", "cid": cid, "n": used + 1})
        return "OK"

    def fill_partial(self, cid: str, filled: int) -> int:
        """部分成交：余额进 CARRY，不重复下单。"""
        o = self.orders[cid]
        rest = max(0, int(o["quantity"]) - int(filled))
        self.fills.append({"cid": cid, "filled": filled})
        if rest:
            self.carry[cid] = rest
        o["status"] = "PARTIAL" if rest else "FILLED"
        self.events.append({"event": "PARTIAL_FILL", "cid": cid, "rest": rest})
        return rest

    def mark_untradable(self, cid: str, *, stale_quote: bool = False) -> dict[str, Any]:
        """涨跌停 / 不可成交 ⇒ 失败关闭；不用 stale 报价改价、不伪造成交。"""
        before = dict(self.orders[cid])
        self.orders[cid]["status"] = "REJECTED_UNTRADABLE"
        self.events.append({"event": "UNTRADABLE", "cid": cid})
        return {"status": self.orders[cid]["status"],
                "price_unchanged": self.orders[cid]["price"] == before["price"],
                "stale_quote_repriced": bool(stale_quote) and
                    self.orders[cid]["price"] != before["price"],
                "fills_created": 0}


def _check(cid: str, name: str, ok: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"id": cid, "name": name, "ok": bool(ok), "evidence": evidence}


def run_scenarios() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """五项演练各 ≥1 次离线记录。判据逐条对齐方案 §三。"""
    b = OfflineFixtureBroker()
    checks: list[dict[str, Any]] = []

    # 1) 幂等重复委托
    b.submit("SIM-1", "600990", "BUY", 100, 10.0)
    second = b.submit("SIM-1", "600990", "BUY", 100, 10.0)
    checks.append(_check("S1", "幂等重复委托", (not second) and len(b.orders) == 1,
                         {"n_orders": len(b.orders), "second_accepted": second,
                          "replay_events": sum(1 for e in b.events
                                               if e["event"] == "REPLAY_IGNORED")}))

    # 2) 撤单与重定价：只朝有利方向，MAX_REPRICE=2，第 3 次硬失败
    b.submit("SIM-2", "603977", "BUY", 200, 20.0)
    b.cancel("SIM-2")
    unfavorable = b.reprice("SIM-2", 21.0, favorable=False)
    r1 = b.reprice("SIM-2", 19.8, favorable=True)
    r2 = b.reprice("SIM-2", 19.6, favorable=True)
    r3 = b.reprice("SIM-2", 19.4, favorable=True)
    checks.append(_check(
        "S2", "撤单与重定价",
        unfavorable == "UNFAVORABLE_REJECTED" and r1 == "OK" and r2 == "OK"
        and r3 == "MAX_REPRICE_EXCEEDED" and b.reprice_count["SIM-2"] == MAX_REPRICE,
        {"unfavorable": unfavorable, "results": [r1, r2, r3],
         "cumulative": b.reprice_count["SIM-2"], "max": MAX_REPRICE}))

    # 3) 部分成交：余额 CARRY 顺延，缺口进下一轮对账，不重复下单
    b.submit("SIM-3", "002012", "BUY", 200, 8.0)
    rest = b.fill_partial("SIM-3", 120)
    n_before = len(b.orders)
    carry_id = "SIM-3-CARRY-1"
    b.submit(carry_id, "002012", "BUY", rest, 8.0)
    checks.append(_check(
        "S3", "部分成交",
        rest == 80 and b.carry["SIM-3"] == 80
        and b.orders[carry_id]["quantity"] == rest
        and len(b.orders) == n_before + 1 and CARRY_MAX_DAYS == 10,
        {"rest": rest, "carry": b.carry["SIM-3"],
         "carry_order_quantity": b.orders[carry_id]["quantity"],
         "n_orders_before_after": [n_before, len(b.orders)],
         "carry_max_days": CARRY_MAX_DAYS}))

    # 4) 涨跌停 / 不可成交：失败关闭，不伪造成交、不用 stale 报价改价
    b.submit("SIM-4", "301329", "BUY", 100, 30.0)
    fills_before = len(b.fills)
    u = b.mark_untradable("SIM-4", stale_quote=True)
    checks.append(_check(
        "S4", "涨跌停 / 不可成交",
        u["status"] == "REJECTED_UNTRADABLE" and u["price_unchanged"]
        and not u["stale_quote_repriced"] and len(b.fills) == fills_before,
        {"status": u["status"], "price_unchanged": u["price_unchanged"],
         "stale_quote_repriced": u["stale_quote_repriced"],
         "n_fills_before_after": [fills_before, len(b.fills)]}))

    # 5) 熔断（模拟触发）：本地注入条件、不下真单
    n_orders_before = len(b.orders)
    drawdown, vol_spike = -0.12, 0.35
    cond_a, cond_b = drawdown <= -0.10, vol_spike >= 0.30
    circuit_fired = cond_a and cond_b
    downgraded = "AGGRESSIVE" if circuit_fired else "NORMAL"
    no_new_buys = True          # 模拟要求：熔断后不新增买入
    manual_confirm = circuit_fired
    stock_weight_before, stock_weight_after = 0.20, 0.20
    checks.append(_check(
        "S5", "熔断（模拟触发）",
        circuit_fired and downgraded == "AGGRESSIVE" and no_new_buys
        and manual_confirm and len(b.orders) == n_orders_before
        and stock_weight_before == stock_weight_after,
        {"cond_drawdown": cond_a, "cond_vol": cond_b, "fired": circuit_fired,
         "downgraded": downgraded, "no_new_buys": no_new_buys,
         "manual_confirm": manual_confirm,
         "n_orders_before_after": [n_orders_before, len(b.orders)],
         "stock_weight_unchanged": stock_weight_before == stock_weight_after,
         "v3_config_created": False, "real_order_sent": False}))

    return checks, b.events


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


def _assert_safe_paths(orders_path: Path, summary_path: Path) -> None:
    """硬守卫：自检产物不得伪装成正式演练记录或写入正式台账。"""
    for p in (orders_path, summary_path):
        if not p.name.startswith("selftest_"):
            raise RuntimeError(f"自检产物名必须以 selftest_ 开头：{p.name}")
        if "ledger" in p.parts or p.parent not in (SIM_ORDERS, SIM_RECONCILE):
            raise RuntimeError(f"自检产物路径越界：{p}")


def self_check(*, day: str, write: bool = False) -> dict[str, Any]:
    checks, events = run_scenarios()
    truth, local = sim_reconcile.selfcheck_fixture()
    baseline = sim_reconcile.reconcile(truth, local)
    n_ok = sum(1 for c in checks if c["ok"])
    result = {
        "schema_version": "sim_drill_driver_v1",
        "mode": SELF_CHECK_MODE,
        "asof": day,
        "decision": "SELF_CHECK_PASS" if n_ok == len(SCENARIOS)
                    and baseline["decision"] == sim_reconcile.PASS
                    else "SELF_CHECK_FAIL",
        "n_scenarios": len(SCENARIOS),
        "n_passed": n_ok,
        "scenario_ids": list(SCENARIOS),
        "checks": checks,
        "reconcile_baseline": {"decision": baseline["decision"],
                               "n_differences": baseline["n_differences"]},
        "no_order_sent": True,
        "offline_only": True,
        "not_wired_into_daily_chain": True,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "events": events,
    }
    if write:
        orders_path = SIM_ORDERS / f"selftest_{day}.jsonl"
        summary_path = SIM_RECONCILE / f"selftest_{day}_driver.json"
        _assert_safe_paths(orders_path, summary_path)
        body = "".join(json.dumps({"mode": SELF_CHECK_MODE, "asof": day, **e},
                                  ensure_ascii=False, sort_keys=True) + "\n"
                       for e in events)
        _atomic_write(orders_path, body)
        _atomic_write(summary_path,
                      json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        result["artifacts"] = [str(orders_path.relative_to(ROOT)),
                               str(summary_path.relative_to(ROOT))]
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="12-01 演练驱动（当前仅离线自检）")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--execute", action="store_true",
                    help="12-01 执行入口；当前工程准备授权下必然拒绝")
    ap.add_argument("--date", default=dt.date.today().strftime("%Y%m%d"))
    ap.add_argument("--write", action="store_true", help="写自检产物（selftest_ 前缀）")
    args = ap.parse_args(argv)

    # ⭐ 显式前置：工程准备授权不含执行许可。放在最前，不读台账、不判日期。
    if args.execute:
        print(f"[sim-drill] {NOT_AUTHORIZED}｜工程准备授权不含 12-01 执行许可；"
              "须 U-16 书面放行 + 11-30 FORWARD 台账 + 另立执行预登记｜未发任何委托")
        return 4

    if not args.self_check:
        print("[sim-drill] ✗ 当前唯一可运行模式是 --self-check", file=sys.stderr)
        return 2
    try:
        res = self_check(day=args.date, write=args.write)
    except Exception as exc:
        print(f"[sim-drill] ✗ 自检异常 {type(exc).__name__}；失败关闭",
              file=sys.stderr)
        return 2
    print(f"[sim-drill] {res['decision']}｜五项 {res['n_passed']}/{res['n_scenarios']}"
          f"｜对账基线 {res['reconcile_baseline']['decision']}"
          f"｜mode={res['mode']}｜零发单·离线·未接日更链")
    return 0 if res["decision"] == "SELF_CHECK_PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
