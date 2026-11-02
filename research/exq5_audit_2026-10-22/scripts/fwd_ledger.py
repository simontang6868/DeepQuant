#!/usr/bin/env python
"""纸面前瞻台账引擎（EXQ5_FORWARD_VALIDATION_PROTOCOL 的可执行实现）。

设计来源：
- 拒单码表与固定决策顺序：港大 HKUDS pt-v1.1（参考件 incoming/vibe_trading_ref/）
- Decimal 金额纪律：同上（禁止 float、禁止内置 round）
- 费率口径：本项目阶段 O（BASE 无最低佣金 / CONSERVATIVE 含 5 元下限）
- T+1 / 整手 / 涨跌停：项目 V1 口径 + 港大 cn_a 规则

铁律：不接券商、不下单、不联网；只做纸面撮合与记账。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
LOT = 100

#: 固定决策顺序（先命中先返回），与 pt-v1.1 REJECTION_ORDER 一致
REJECTION_ORDER = (
    "unknown_symbol",
    "non_trading_day",
    "suspended",
    "corporate_action_unresolved",
    "lot_size",
    "limit_up",
    "limit_down",
    "limit_not_marketable",
    "t_plus_1",
    "insufficient_position",
    "insufficient_cash",
)


def money(x) -> Decimal:
    return Decimal(str(x))


def q2(x: Decimal) -> Decimal:
    """2 位小数 ROUND_HALF_UP（禁用内置 round：2.675 → 2.68）。"""
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class FeeModel:
    name: str
    commission_rate: Decimal
    commission_min: Decimal      # 0 表示不设下限
    stamp_tax_sell: Decimal
    transfer_fee: Decimal


BASE = FeeModel("BASE", Decimal("0.00025"), Decimal("0"), Decimal("0.0005"), Decimal("0.00001"))
CONSERVATIVE = FeeModel("CONSERVATIVE", Decimal("0.00025"), Decimal("5"), Decimal("0.0005"),
                        Decimal("0.00001"))
MODELS = {"BASE": BASE, "CONSERVATIVE": CONSERVATIVE}


def fee(model: FeeModel, side: str, qty: Decimal, price: Decimal) -> Decimal:
    """单笔费用：佣金（可含下限）+ 过户费双边 + 印花税仅卖出。"""
    notional = qty * price
    comm = notional * model.commission_rate
    if model.commission_min > 0 and comm < model.commission_min:
        comm = model.commission_min
    total = comm + notional * model.transfer_fee
    if side == "sell":
        total += notional * model.stamp_tax_sell
    return q2(total)


def board_ratio(code: str) -> Decimal:
    """涨跌停比例：主板 10%、创业板/科创板 20%、北交所 30%。"""
    plain = code.split(".")[0]
    if plain.startswith(("300", "301", "688", "689")):
        return Decimal("0.20")
    if len(plain) == 6 and plain.startswith(("4", "8", "920")):
        return Decimal("0.30")
    return Decimal("0.10")


def limit_band(prev_close: Decimal, code: str, is_st: bool = False) -> tuple[Decimal, Decimal]:
    """由前收盘推涨跌停价；ST 用 5% 覆盖（表不可得时的启发式，禁止伪造）。"""
    ratio = Decimal("0.05") if is_st else board_ratio(code)
    base = money(prev_close)
    if base <= 0:
        raise ValueError("prev_close 必须为正")
    return q2(base * (Decimal(1) + ratio)), q2(base * (Decimal(1) - ratio))


@dataclass
class Position:
    qty: Decimal = Decimal(0)
    bought_today: Decimal = Decimal(0)


@dataclass
class Account:
    cash: Decimal
    positions: dict[str, Position] = field(default_factory=dict)

    def pos(self, code: str) -> Position:
        return self.positions.setdefault(code, Position())


@dataclass(frozen=True)
class Bar:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


def decide(side: str, code: str, qty: Decimal, price: Decimal, acct: Account,
           bar: Bar | None, prev_close: Decimal | None, is_st: bool = False,
           is_trading_day: bool = True, corporate_action_unresolved: bool = False,
           model: FeeModel = BASE) -> str | None:
    """按 REJECTION_ORDER 返回第一个命中码；None 表示通过。

    检查顺序与码表一致：unknown_symbol → non_trading_day → suspended →
    corporate_action_unresolved → lot_size → limit_up → limit_down →
    limit_not_marketable → t_plus_1 → insufficient_position → insufficient_cash
    """
    if bar is None and prev_close is None:
        return "unknown_symbol"
    if not is_trading_day:
        return "non_trading_day"
    if bar is None:
        return "suspended"
    if corporate_action_unresolved:
        return "corporate_action_unresolved"
    q = money(qty)
    if q <= 0:
        return "lot_size"
    if side == "buy" and q % LOT != 0:
        return "lot_size"
    band = limit_band(prev_close, code, is_st) if prev_close is not None else (None, None)
    up, down = band
    if side == "buy" and up is not None and money(price) >= up:
        return "limit_up"
    if side == "sell" and down is not None and money(price) <= down:
        return "limit_down"
    pos = acct.pos(code)
    if side == "sell":
        if q > pos.qty:
            return "insufficient_position"
        if q > pos.qty - pos.bought_today:
            return "t_plus_1"
    else:
        cost = q2(q * money(price)) + fee(model, "buy", q, money(price))
        if cost > acct.cash:
            return "insufficient_cash"
    return None


def canonical(obj) -> str:
    """确定性 JSON：键排序、紧凑分隔、非 ASCII 原样（禁用时间戳/随机量）。"""
    def norm(x):
        if isinstance(x, dict):
            return {str(k): norm(v) for k, v in sorted(x.items())}
        if isinstance(x, (list, tuple)):
            return [norm(v) for v in x]
        if isinstance(x, Decimal):
            return str(x)
        return x
    return json.dumps(norm(obj), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def state_hash(obj) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


# ==================== 台账行 ====================

LEDGER_FIELDS = ["signal_date", "exec_date", "n_pool", "n_keep", "gross_alpha_pp",
                 "fee_total", "net_pp", "cash", "uninvested", "n_rejected",
                 "rejected_breakdown", "state_hash"]


def ledger_row(*, signal_date: str, exec_date: str, n_pool: int, n_keep: int,
               gross_alpha_pp: float, fee_total: Decimal, net_pp: float,
               cash: Decimal, initial_cash: Decimal, rejected: dict[str, int],
               snapshot: dict) -> dict:
    return {
        "signal_date": signal_date,
        "exec_date": exec_date,
        "n_pool": n_pool,
        "n_keep": n_keep,
        "gross_alpha_pp": round(gross_alpha_pp, 6),
        "fee_total": str(q2(fee_total)),
        "net_pp": round(net_pp, 6),
        "cash": str(q2(cash)),
        "uninvested": round(float(q2(cash) / money(initial_cash)), 6),
        "n_rejected": int(sum(rejected.values())),
        "rejected_breakdown": json.dumps({k: v for k, v in sorted(rejected.items()) if v},
                                         ensure_ascii=False, sort_keys=True),
        "state_hash": state_hash(snapshot),
    }


# ==================== 干跑（机制验证，非新实验） ====================

def _read_exec(path: str) -> dict[str, Bar]:
    out = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split(",")
            if len(parts) < 6:
                continue
            d, o, h, lo, c = parts[0], parts[1], parts[2], parts[3], parts[4]
            try:
                out[d] = Bar(money(o), money(h), money(lo), money(c))
            except Exception:
                continue
    return out


class Calendar:
    def __init__(self, days: list[str]):
        self.days = days
        self.idx = {d: i for i, d in enumerate(days)}

    def prev(self, d: str) -> str | None:
        i = self.idx.get(d)
        return self.days[i - 1] if i and i > 0 else None

    def last_of_month(self, ym: str, not_after: str | None = None) -> str | None:
        cand = [d for d in self.days if d.startswith(ym) and (not_after is None or d <= not_after)]
        return cand[-1] if cand else None


def dry_run(month: str, n: int, cash: Decimal, model: FeeModel) -> dict:
    """用真实行情 + 合成等权组合验证撮合机制（不是新实验、不产生 alpha 结论）。"""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    exec_dir = os.path.join(root, "data", "exec_all")
    files = sorted(f for f in os.listdir(exec_dir) if f.endswith(".exec.csv"))
    if not files:
        raise SystemExit("exec_all 为空")
    # 交易日历取最活跃代码
    cal_days = sorted(_read_exec(os.path.join(exec_dir, files[0])).keys())
    cal = Calendar(cal_days)
    entry = cal.last_of_month(month)
    if entry is None:
        raise SystemExit(f"{month} 无交易日")
    # 下一月
    y, m = int(month[:4]), int(month[5:7])
    ym2 = f"{y + (m == 12)}-{(m % 12) + 1:02d}"
    exit_ = cal.last_of_month(ym2)
    if exit_ is None:
        raise SystemExit(f"{ym2} 无交易日（数据可能未覆盖）")

    picked, rejected = [], {"unknown_symbol": 0}
    per_code: dict[str, dict] = {}
    for fn in files:
        if len(picked) >= n:
            break
        code = fn.replace(".exec.csv", "")
        bars = _read_exec(os.path.join(exec_dir, fn))
        b_entry, b_exit = bars.get(entry), bars.get(exit_)
        prev_d = cal.prev(entry)
        b_prev = bars.get(prev_d) if prev_d else None
        if b_entry is None or b_exit is None or b_prev is None:
            rejected["unknown_symbol"] += 1
            continue
        picked.append(code)
        per_code[code] = {"bars": bars, "entry": b_entry, "exit": b_exit,
                          "prev": b_prev}

    acct = Account(cash=cash, positions={})
    fees_total = Decimal(0)
    per = cash / Decimal(len(picked)) if picked else Decimal(0)
    for code in picked:
        rec = per_code[code]
        price = rec["entry"].close
        qty = (per / price / LOT).to_integral_value(rounding="ROUND_FLOOR") * LOT
        r = decide("buy", code, qty, price, acct, rec["entry"], rec["prev"].close, model=model)
        if r:
            rejected[r] = rejected.get(r, 0) + 1
            continue
        f = fee(model, "buy", qty, price)
        acct.cash -= q2(qty * price) + f
        fees_total += f
        pos = acct.pos(code)
        pos.qty += qty
        pos.bought_today += qty

    # 次日（模拟 T+1）：清空 bought_today
    for pos in acct.positions.values():
        pos.bought_today = Decimal(0)

    cash_after_buy = acct.cash          # ⚠️ 投入额必须在建仓后取，不能用卖出后的现金
    invested = cash - cash_after_buy

    for code in list(acct.positions):
        rec = per_code[code]
        q = acct.pos(code).qty
        price = rec["exit"].close
        r = decide("sell", code, q, price, acct, rec["exit"], rec["bars"].get(cal.prev(exit_), rec["entry"]).close, model=model)
        if r:
            rejected[r] = rejected.get(r, 0) + 1
            continue
        f = fee(model, "sell", q, price)
        acct.cash += q2(q * price) - f
        fees_total += f
        acct.pos(code).qty = Decimal(0)

    invested = cash - cash_after_buy
    snapshot = {"month": month, "entry": entry, "exit": exit_, "n": len(picked),
                "cash": str(q2(cash)), "fees": str(q2(fees_total)),
                "model": model.name}
    n_held = sum(1 for p in acct.positions.values() if p.qty > 0)
    row = ledger_row(signal_date=entry, exec_date=exit_,
                     n_pool=len(picked), n_keep=n_held,
                     gross_alpha_pp=0.0, fee_total=fees_total, net_pp=0.0,
                     cash=acct.cash, initial_cash=cash, rejected=rejected,
                     snapshot=snapshot)
    return {"month": month, "entry": entry, "exit": exit_, "n_picked": len(picked),
            "n_positions": n_held, "cash_end": str(q2(acct.cash)),
            "invested": str(q2(invested)), "fees": str(q2(fees_total)),
            "rejected": rejected, "row": row}


def main(argv=None):
    ap = argparse.ArgumentParser(description="纸面前瞻台账引擎（干跑模式）")
    ap.add_argument("--dry-run", action="store_true", help="机制干跑（合成组合，非新实验）")
    ap.add_argument("--month", default="2026-08", help="干跑月份 YYYY-MM")
    ap.add_argument("--n", type=int, default=50, help="合成持仓只数")
    ap.add_argument("--cash", default="30000000", help="初始资金（元）")
    ap.add_argument("--model", default="BASE", choices=sorted(MODELS),
                    help="费率口径 BASE / CONSERVATIVE")
    args = ap.parse_args(argv)
    if not args.dry_run:
        ap.print_help()
        return 0
    res = dry_run(args.month, args.n, money(args.cash), MODELS[args.model])
    inv = money(res["invested"])
    fee_rate = float(money(res["fees"]) / inv) * 100 if inv > 0 else 0.0
    print(f"机制干跑 {res['month']}：{res['entry']} → {res['exit']}")
    print(f"  合成持仓 {res['n_picked']} 只，期末持仓 {res['n_positions']} 只")
    print(f"  建仓投入 {res['invested']} 元（占 {float(inv / money(args.cash)) * 100:.1f}%），"
          f"期末现金 {res['cash_end']} 元，往返费用 {res['fees']} 元（占投入 {fee_rate:.3f}%）")
    print(f"  拒单 {json.dumps(res['rejected'], ensure_ascii=False, sort_keys=True)}")
    print(f"  state_hash {res['row']['state_hash'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
