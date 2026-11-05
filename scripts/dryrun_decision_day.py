#!/usr/bin/env python
"""决策日端到端纸面演练（DRYRUN）。

## 它回答什么问题

「2026-11-30 那天真的跑起来，会发生什么？」

具体是五件在纸面上看不出、只有串起来才会暴露的事：
1. d4 口径把目标名单削掉几只（10-30 期 5 只里有 1 只是创业板）；
2. 10 万 × 20% ÷ 5 = 4,000 元/只 时，**几百元一股的票连一手都买不起**；
3. 整手取整后实际投入多少、剩多少现金；
4. 09:35 发单 → 10:30/14:30 改价 → 收盘顺延，这条时间轴串起来是否自洽；
5. 5 元最低佣金在这个规模上占多少（这是 R4-8 校准的落点）。

## 它**不**做什么

- 不写台账、不动 `forward/`（只读既有产物 + 行情）；
- 不下单（``LIVE_ENABLED=false``，全程只有 ``LoopAction`` 对象）；
- 不预测收益。这是**执行可行性**演练，不是回测。

## 数据来源（全部本地、可复核）

- 目标名单：`forward/ledger/20261030.json → target_holds`（真实产物，非编造）
- 价格：`data/exec_all/<code>.<ex>.exec.csv`（真不复权日线）
- 交易日历：`forward/trade_calendar.json`（sha256 自校验）
- 配置：`forward/sp4_config_v2.json`（20% 仓 / 10 万）

用法：
    python scripts/dryrun_decision_day.py
    python scripts/dryrun_decision_day.py --decision 20261030 --capital 100000 --weight 0.20
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "backend" / "src"))

from deepquant.broker.models import AccountSnapshot, Position, Quote  # noqa: E402
from deepquant.execution.board import BoardPermissions, board_of  # noqa: E402
from deepquant.execution.order_loop import Action, LoopState, tick  # noqa: E402
from deepquant.execution.planner import PlanInput, make_client_order_id, plan_orders  # noqa: E402
from deepquant.execution.rate_limit import RateLimiter  # noqa: E402
from deepquant.execution.session import DEFAULT_TIMES, OrderTracker  # noqa: E402
from deepquant.market.rules import Side  # noqa: E402

SP = REPO / "research" / "stock_pick_2026-10-25"
FWD = SP / "forward"
EXEC = REPO / "data" / "exec_all"

#: 券商实盘费用（R4-8 已确认「有 5 元最低」）。
COMMISSION_RATE = 0.00025
MIN_COMMISSION = 5.00
STAMP_RATE = 0.0005        # 仅卖出
TRANSFER_RATE = 0.00001    # 双边

#: 整手敏感度换算用的两个锚（都不是本脚本算出来的，来自既有产物）：
#: ① 20% 仓、100% 投入、d4aff + 4,000 元/只口径下的组合年化 = 8.11%
#:    （R4-8 成本校准后，见 docs/BROKER_STATEMENT_CALIBRATION_2026-11-03.md §4）
#: ② 闲置现金按年化 3% 计息（同 sp4 回测的现金假设）
ANN_FULL_INVESTED = 0.0811
CASH_ANN = 0.03


def load_price(code: str, day: str) -> tuple[float, float] | None:
    """取 ``day`` 的 (收盘价, 0) —— 返回 None 表示该日无数据。"""
    for ex in (".SH", ".SZ", ".BJ"):
        fp = EXEC / f"{code}{ex}.exec.csv"
        if not fp.is_file():
            continue
        with fp.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["date"].replace("-", "") == day:
                    c = float(row["close"])
                    return (c, 0.0) if c > 0 else None
    return None


def last_price_on_or_before(code: str, day: str) -> tuple[str, float] | None:
    """取 ``day`` 当日或之前最近一个有数据的 (日期, 收盘价)。"""
    for ex in (".SH", ".SZ", ".BJ"):
        fp = EXEC / f"{code}{ex}.exec.csv"
        if not fp.is_file():
            continue
        best: tuple[str, float] | None = None
        with fp.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d = row["date"].replace("-", "")
                if d <= day:
                    c = float(row["close"])
                    if c > 0:
                        best = (d, c)
                else:
                    break
        if best:
            return best
    return None


def lot_cost(price: float, lot: int = 100) -> float:
    return price * lot


def commission(amount: float) -> float:
    return max(amount * COMMISSION_RATE, MIN_COMMISSION)


def roundtrip_cost(amount: float) -> tuple[float, float]:
    """买入 + 卖出的显性费用合计（元）与其占成交额比。"""
    buy = commission(amount) + amount * TRANSFER_RATE
    sell = commission(amount) + amount * STAMP_RATE + amount * TRANSFER_RATE
    return buy + sell, (buy + sell) / amount if amount else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="决策日端到端纸面演练（DRYRUN）")
    ap.add_argument("--decision", default="20261030")
    ap.add_argument("--exec-day", default="20261102",
                    help="模拟的执行日。⚠️ 必须是**有行情的历史日**"
                         "（默认 20261102 = 10-30 的次一交易日）；"
                         "用未来日会让所有标的被判 stale 而无单可发")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--weight", type=float, default=0.20)
    ap.add_argument("--n-hold", type=int, default=5)
    ap.add_argument("--strict-stale", action="store_true",
                    help="严格按 stale 判定（本地行情只到 10-30，"
                         "与执行日不同即算陈旧）。默认关闭：演练用最近价近似，"
                         "但会在输出里注明取价日")
    ap.add_argument("--lot-sweep", action="store_true",
                    help="额外打印「整手浪费 vs 本金」敏感度表（只读，不改本次结论）")
    args = ap.parse_args(argv)

    print("=" * 78)
    print(f"DRYRUN 决策日纸面演练 —— 决策 {args.decision}，执行 {args.exec_day}")
    print(f"本金 {args.capital:,.0f} 元 ｜ 仓位 {args.weight:.0%} ｜ 持仓 {args.n_hold} 只")
    print("=" * 78)

    # ---- 1. 台账目标（真实产物）----
    led = FWD / "ledger" / f"{args.decision}.json"
    if not led.is_file():
        print(f"[✗] 台账不存在：{led}")
        return 1
    rec = json.loads(led.read_text(encoding="utf-8"))
    targets = rec.get("target_holds") or []
    if not targets:
        print(f"[✗] 台账里没有 target_holds（{sorted(rec)[:6]}…）")
        return 1
    print(f"\n① 台账目标（主口径 top{len(targets)}）：{targets}")

    # ---- 2. d4 权限闸（U-2b）----
    perm = BoardPermissions.from_config(["sh_main", "sz_main"], source="U-2b 2026-11-03")
    kept, blocked = [], []
    for c in targets:
        b = board_of(c)
        if perm.allows(c):
            kept.append((c, b.value))
        else:
            blocked.append((c, b.value))
    print(f"② d4 闸：保留 {[(c, b) for c, b in kept]}")
    if blocked:
        print(f"   拦下 {[(c, b) for c, b in blocked]}  ← 无该板块权限（U-2b）")
    else:
        print("   拦下 [] （本期目标全在沪深主板）")
    if len(kept) < args.n_hold:
        print(f"   ⚠️ 可投标的 {len(kept)} < 目标 {args.n_hold}：按 d4 口径本应"
              f"用 p13 的 target_holds_d4（顺延补位），此处不补——演练只复现"
              f"「被拦之后还剩几只」这一事实。")

    # ---- 3. 预算与整手 ----
    sleeve = args.capital * args.weight
    # ⚠️ 分母是**实际可投只数**（d4 补位后有几只就分几份），不是策略设定的 5。
    # 这个选择直接影响整手浪费：每只预算越小，整手取整丢掉的现金越多。
    n_alloc = max(len(kept), 1)
    per_stock = sleeve / n_alloc
    print(f"\n③ 预算：策略仓 {sleeve:,.0f} 元 ÷ 可投 {n_alloc} 只 = 每只 {per_stock:,.0f} 元")
    print(f"   （策略设定 {args.n_hold} 只；若按 {args.n_hold} 只均分会是 "
          f"{sleeve / args.n_hold:,.0f} 元/只，整手浪费更大——见 ⑦ 对照）")

    rows, quotes, total_payable = [], {}, 0.0
    for code, _ in kept:
        got = last_price_on_or_before(code, args.exec_day)
        if got is None:
            print(f"   {code}: ✗ 无行情（停牌或数据缺失）")
            rows.append((code, None, 0, 0.0, "无行情"))
            continue
        d, price = got
        lots = int(per_stock // lot_cost(price)) if price > 0 else 0
        shares = lots * 100
        amount = shares * price
        affordable = shares > 0
        if affordable:
            total_payable += amount
        # 演练默认**不**把"取价日 ≠ 执行日"判成 stale，否则本地行情（只到 10-30）
        # 会让所有单都发不出去。真实运行时 stale 由数据源判定，不由本脚本猜。
        quotes[code] = Quote(code=code, last=price, ts=int(d),
                             stale=bool(args.strict_stale and d != args.exec_day))
        rows.append((code, price, shares, amount,
                     "可买" if affordable else "一手超预算"))

    print(f"   {'代码':8s}{'取价日':>10s}{'收盘':>8s}{'手':>5s}{'股数':>7s}"
          f"{'金额':>12s}  判定")
    for (code, price, shares, amount, verdict), (c2, _) in zip(rows, kept):
        dd = quotes.get(code).ts if code in quotes else "—"
        ps = f"{price:.2f}" if price else "—"
        print(f"   {code:8s}{str(dd):>10s}{ps:>8s}{shares // 100:>5d}{shares:>7d}"
              f"{amount:>12,.0f}  {verdict}")
    n_buy = sum(1 for r in rows if r[2] > 0)
    print(f"   可买 {n_buy}/{len(kept)} 只，实际投入 {total_payable:,.0f} 元"
          f"（占策略仓 {total_payable / sleeve:.1%}）")
    off = {c: q.ts for c, q in quotes.items() if q.ts != int(args.exec_day)}
    if off:
        print(f"   ⚠️ 取价日 ≠ 执行日（{len(off)} 只）：{off}")
        print(f"      本地行情只到最新交易日；演练用最近收盘价近似执行日开盘价，"
              f"真实运行时由数据源判 stale（--strict-stale 可复现严格模式）")

    # ---- 4. 成本核算 ----
    print(f"\n④ 成本（5 元最低佣金已确认）：")
    for (code, price, shares, amount, verdict) in rows:
        if shares <= 0:
            continue
        c, ratio = roundtrip_cost(amount)
        print(f"   {code}: 成交额 {amount:>9,.0f} → 往返费用 {c:>6.2f} 元"
              f" = {ratio:.3%}（单边 {ratio / 2:.3%}）")
    if rows:
        amt = [r[3] for r in rows if r[2] > 0]
        if amt:
            avg = sum(amt) / len(amt)
            _, ar = roundtrip_cost(avg)
            print(f"   平均：每只 {avg:,.0f} 元，往返 {ar:.3%}"
                  f"（回测假设 0.102% 的 {ar / 0.00102:.1f} 倍）")

    # ---- 5. 时间轴演练（09:35 → 10:30 → 14:30 → 收盘）----
    print(f"\n⑤ 时间轴（执行日 {args.exec_day}）：")
    buys = [(c, s, p) for (c, p, s, a, v) in rows if s > 0]
    if not buys:
        print("   无可买标的，时间轴为空。")
        return 0

    snap_cash = args.capital - sleeve          # 未投入的 80% 现金不算策略仓
    snap = AccountSnapshot(cash=type("C", (), {"total": sleeve, "available": sleeve})(),
                           positions=())
    inp = PlanInput(
        decision=args.exec_day,
        targets=[c for c, _, _ in buys],
        snapshot=snap,
        quotes=quotes,
        target_weight=1.0 / len(buys),        # 策略仓内部等权（与 ③ 的分母一致）
        permissions=perm,
    )
    try:
        plan = plan_orders(inp)
    except Exception as e:                    # noqa: BLE001 - 演练要看到真实失败
        print(f"   [✗] planner 抛错：{type(e).__name__}: {e}")
        return 1
    print(f"   规划：{len(plan.orders)} 单，跳过 {len(plan.skipped)} 项"
          f"（权限未检查标志 permission_unchecked={plan.permission_unchecked}）")
    if plan.skipped:
        for s in plan.skipped[:8]:
            print(f"     跳过 {s.code} {s.side.value if s.side else '-'}：{s.reason}")

    st = LoopState(limiter=RateLimiter(rate_per_second=2.0, burst=2))
    cal = json.loads((FWD / "trade_calendar.json").read_text(encoding="utf-8"))["dates"]
    cal = [str(d) for d in cal]
    d0 = dt.date(int(args.exec_day[:4]), int(args.exec_day[4:6]), int(args.exec_day[6:]))

    def drain(t0: dt.datetime, *, quotes_, max_rounds: int = 20,
              step_s: float = 1.0) -> list:
        """在一个时点内反复 tick，直到没有 PLACE/REPRICE（真实执行器的形态）。

        ⚠️ 这一步不能省：限速器的 burst 限制了单轮笔数，只 tick 一次会把
        后面的订单拖到下一个时点——演练第一次跑就撞上了（burst=2、一轮 4 笔，
        两笔被拖到 14:40 才发出）。
        """
        out, rounds = [], 0
        for i in range(max_rounds):
            acts = tick(now=t0 + dt.timedelta(seconds=i * step_s), plan=plan.orders,
                        state=st, quotes=quotes_, price_limits={}, trading_days=cal)
            rounds += 1
            out.extend(acts)
            if not any(a.kind in (Action.PLACE, Action.REPRICE) for a in acts):
                break
        if rounds > 1:
            print(f"     （本时点共 {rounds} 轮才把令牌用完 —— 限速器 burst="
                  f"{st.limiter.burst}，单轮上限即等于它）")
        return out

    def show(label: str, acts) -> None:
        print(f"   ── {label} ──")
        for a in acts:
            extra = f" @{a.limit_price:.2f}" if a.limit_price else ""
            print(f"     {a.kind.value:8s} {a.client_order_id:28s}{extra}  {a.reason}")

    # 09:34（不该发）
    show("09:34 开盘前", tick(now=dt.datetime.combine(d0, dt.time(9, 34)),
                              plan=plan.orders, state=st, quotes=quotes,
                              price_limits={}, trading_days=cal))
    # 09:35（发单；时点内多轮直到发完）
    show("09:35 发单窗口开启（时点内多轮）",
         drain(dt.datetime.combine(d0, dt.time(9, 35)), quotes_=quotes))
    # 10:30（第一次改价；模拟价格上浮 1%）
    q_up = {c: Quote(code=c, last=q.last * 1.01, ts=q.ts, stale=False)
            for c, q in quotes.items()}
    show("10:30 第一次改价（模拟价 +1%，时点内多轮）",
         drain(dt.datetime.combine(d0, dt.time(10, 30)), quotes_=q_up))
    # 14:30（第二次改价）
    q_up2 = {c: Quote(code=c, last=q.last * 1.02, ts=q.ts, stale=False)
             for c, q in quotes.items()}
    show("14:30 第二次改价（模拟价 +2%，时点内多轮）",
         drain(dt.datetime.combine(d0, dt.time(14, 30)), quotes_=q_up2))
    # 14:40（应已被 2 次上限挡住）
    show("14:40 再想改价（应被 2.2-d 挡住）",
         drain(dt.datetime.combine(d0, dt.time(14, 40)), quotes_=q_up2, max_rounds=3))
    # 收盘（未成交 → 顺延）
    show("15:05 收盘清账（假定全部未成交）",
         tick(now=dt.datetime.combine(d0, dt.time(15, 5)), plan=plan.orders,
              state=st, at_close=True, day=args.exec_day, trading_days=cal,
              quotes=quotes, price_limits={}))

    # ---- 6. 结论 ----
    reps = [(k, v.reprice_count) for k, v in st.trackers.items()]
    carries = [(k, v.carry_days, v.first_day) for k, v in st.trackers.items()]
    print(f"\n⑥ 结果")
    print(f"   改价次数：{reps}")
    print(f"   顺延状态：{carries}")
    print(f"   令牌：授予 {st.limiter.granted} / 拒绝 {st.limiter.denied}")
    print(f"   现金：策略仓 {sleeve:,.0f}，实际投入 {total_payable:,.0f}，"
          f"闲置 {sleeve - total_payable:,.0f}（{1 - total_payable / sleeve:.1%}）")
    # ---- ⑦ 对照：按策略设定的 5 只均分（每只 4,000 元）----
    # ⚠️ 这里必须把两笔"没投出去的钱"分开：
    #    ① 整手取整丢掉的（每只内部剩的零头）
    #    ② 因为可投只数不足而整份没分配的（缺 1 只 = 4,000 元没动）
    # 第一版把两者混在一起除以策略仓，得出"闲置 27.1%"——
    # 那是拿错分母（4 只的钱除以 5 只的预算），会把整手问题夸大成 3 倍。
    print(f"\n⑦ 对照：按策略设定 {args.n_hold} 只均分（每只 {sleeve / args.n_hold:,.0f} 元）")
    if kept:
        p2 = sleeve / args.n_hold
        tot2 = 0.0
        for code, _ in kept:
            got = last_price_on_or_before(code, args.exec_day)
            if got is None:
                continue
            price = got[1]
            lots = int(p2 // lot_cost(price))
            tot2 += lots * 100 * price
        allocated = p2 * len(kept)
        missing = args.n_hold - len(kept)
        unalloc = sleeve - allocated
        print(f"   预算分配：已分 {len(kept)} 只 × {p2:,.0f} = {allocated:,.0f} 元；"
              f"缺 {missing} 只 → 未分配 {unalloc:,.0f} 元")
        if allocated > 0:
            print(f"   ① 整手浪费：{allocated - tot2:,.0f} / {allocated:,.0f} = "
                  f"{1 - tot2 / allocated:.1%}（在已分配额度内）")
        print(f"   ② 缺只未分配：{unalloc:,.0f} 元 = 策略仓的 "
              f"{unalloc / sleeve:.1%}（11-30 起 d4 会补位到 5 只，此项应归零）")
        print(f"   合计投入 {tot2:,.0f} / {sleeve:,.0f} = {tot2 / sleeve:.1%}"
              f"（闲置 {sleeve - tot2:,.0f} 元 = {1 - tot2 / sleeve:.1%}）")
        print(f"   ⚠️ 回测没有整手约束，把策略仓当成 100% 已投入——"
              f"这部分闲置在实盘不产生策略收益，是**未建模的结构性偏差**。")

    # ---- ⑧ 整手浪费 × 本金 敏感度（只读；不改上面任何结论）----
    if args.lot_sweep:
        print(f"\n⑧ 整手浪费对每只预算的敏感度（用本次 {len(kept)} 只的真实收盘价）")
        print("   假设：① d4 补位后仍为 %d 只、等额预算；② 补位那只的整手"
              "浪费率 = 已知各只均值；" % args.n_hold)
        print(f"        ③ 满额投入时组合年化 {ANN_FULL_INVESTED:+.2%}（R4-8 校准），"
              f"闲置现金按 {CASH_ANN:.0%} 计息")
        print("   ④ 不同预算下的换手/费率按同一比例缩放——本表**不**建模这一点，"
              "只算整手")
        prices = []
        for code, _ in kept:
            got = last_price_on_or_before(code, args.exec_day)
            if got is not None:
                prices.append(got[1])
        if not prices:
            print("   [跳过] 取不到任何收盘价")
        else:
            # 满额投入的组合年化反推策略仓年化（把现金腿剥掉）
            s_net = ((ANN_FULL_INVESTED - (1 - args.weight) * CASH_ANN)
                     / args.weight)
            print(f"\n   本金(万元) | 每只预算(元) | 整手投入率 | 期望投入率 | 混合年化 | 距 8%")
            for cap_w in (5, 10, 15, 20, 30, 50, 100):
                per = cap_w * 10_000 * args.weight / args.n_hold
                inv = sum(int(per // lot_cost(p)) * 100 * p for p in prices)
                util = inv / (per * len(prices))          # 已分配额度内的投入率
                # 解析期望：余数在 [0, 一手金额) 内均匀 ⇒ E[浪费] = 一手金额 / 2
                e_waste = sum(lot_cost(p) / 2 for p in prices) / len(prices) / per
                ann = ((1 - args.weight) * CASH_ANN
                       + args.weight * (util * s_net + (1 - util) * CASH_ANN))
                ann_e = ((1 - args.weight) * CASH_ANN
                         + args.weight * ((1 - e_waste) * s_net + e_waste * CASH_ANN))
                print(f"   {cap_w:>9.0f} | {per:>11,.0f} | {util:>9.1%} | "
                      f"{1 - e_waste:>9.1%} | {ann:>7.2%} | {(ann - 0.08) * 100:+.2f}pp"
                      f"   (期望 {ann_e:.2%})")
            print("   ⚠️ 「整手投入率」是**单月单次抽样**（余数落在 [0,一手) 的哪一段看运气），"
                  "故附「期望投入率」。")
            print("   ⚠️ 价格是单月快照、整手浪费率随标的价格波动；"
                  "本表是**算术**，不是新回测。")

    print("\n[DRYRUN] 本次未写任何台账、未发任何单。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
