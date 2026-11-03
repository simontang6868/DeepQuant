"""``MockBroker``：可注入故障的券商替身。

它在整条链路里的位置
--------------------
``OrderPlanner → RiskGate → **BrokerAdapter** → FillReconciler → Ledger``

它扮演的是**券商**，不是市场。所以它**不做撮合**——价格可及性、参与率、队列位置
属于 ``eventloop.matching``（回测口径）。这里只回答"券商收到这笔单之后会怎样"：
接受、拒单、部分成交、回报丢了、回报重复了、超时了。

两处混用会立刻出问题：如果 MockBroker 自己按 bar 判能不能成交，那它就成了第二个
撮合模型，与回测的那套在细节上必然分叉，而分叉点没人会发现。

为什么必须是确定性的
--------------------
故障注入测试要能回答"**改动之后结论变没变**"。若 Mock 里有随机数或 wall clock，
同一个用例两次跑出不同结果，就分不清是代码变了还是随机了。

因此本模块：
* 不使用 ``random``、不使用 ``time.time()``、不使用任何真实时钟；
* ``fill_id`` 由 ``client_order_id`` 与成交序号**确定性派生**；
* 同一剧本 + 同一调用序列 ⇒ 结果逐位相同（``test_replay_is_bitwise_identical`` 钉住）。

🔴 「回报重复」为什么必须复用同一个 ``fill_id``
--------------------------------------------
这是本模块最容易被写错的地方。如果重复回报生成新的 ``fill_id``，
上层去重就失效了：同一笔成交会被记两次，持仓凭空多一倍，而且**日志里完全正常**
（两个不同的 fill_id，看起来就是两笔成交）。

所以 ``duplicate_fills`` 的实现是「把**同一条** ``Fill`` 再放进列表一次」，
而不是「再成交一次」。验收标准缺陷 P1 预登记的就是这件事。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import compute_fees
from deepquant.broker.base import BrokerAdapter
from deepquant.broker.errors import (
    BrokerTimeout,
    DuplicateOrder,
    OrderRejected,
    RejectCode,
)
from deepquant.broker.models import (
    BrokerOrder,
    BrokerOrderStatus,
    Cash,
    Fill,
    OrderRequest,
    Position,
    Quote,
    TradingCalendar,
)
from deepquant.market.ashare import round_down_to_lot
from deepquant.market.rules import Side

__all__ = ["FaultScript", "MockBroker"]


@dataclass(frozen=True)
class FaultScript:
    """故障剧本。以**股票代码**为键（同一时刻同一标的多笔委托的情况不在本阶段范围）。

    ⚠️ 全部字段默认「无故障」。空剧本 = 正常券商，不做任何特殊处理。
    """

    #: 直接拒单，附带原因码。
    reject: Mapping[str, RejectCode] = field(default_factory=dict)
    #: 成交比例（0~1）。< 1 会产生 ``PARTIALLY_FILLED``。缺省视为 1.0。
    fill_ratio: Mapping[str, float] = field(default_factory=dict)
    #: 成交但**回报丢失**：委托状态停在 ``UNKNOWN``，``get_fills`` 看不到。
    drop_fills: frozenset[str] = field(default_factory=frozenset)
    #: 回报**重复**：同一 ``fill_id`` 在 ``get_fills`` 中返回两次。
    duplicate_fills: frozenset[str] = field(default_factory=frozenset)
    #: 请求超时：抛 ``BrokerTimeout``，且委托已在券商侧落地为 ``UNKNOWN``。
    timeout: frozenset[str] = field(default_factory=frozenset)
    #: 行情陈旧：``get_quote`` 返回 ``stale=True``。
    stale_quotes: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for code, ratio in self.fill_ratio.items():
            if not (0.0 <= ratio <= 1.0):
                raise ValueError(f"fill_ratio 必须在 [0,1]，{code} 给了 {ratio}")


class MockBroker(BrokerAdapter):
    """确定性券商替身。

    ``cost_config`` **必填**：费用必须经 ``backtest.costs.compute_fees`` 计算
    （验收标准 2.6-b）。给一个默认值等于在这里再定义一份费率，而本项目在成本口径
    上已经错过四次。
    """

    def __init__(
        self,
        *,
        cost_config: BacktestConfig,
        cash: Cash,
        positions: Sequence[Position] = (),
        quotes: Mapping[str, Quote] | None = None,
        calendar: TradingCalendar | None = None,
        script: FaultScript | None = None,
        connected: bool = True,
    ) -> None:
        self._cfg = cost_config
        self._cash = cash
        self._positions: dict[str, Position] = {p.code: p for p in positions}
        self._quotes: dict[str, Quote] = dict(quotes or {})
        self._calendar = calendar or TradingCalendar()
        self._script = script or FaultScript()
        self._connected = connected

        self._orders: dict[str, BrokerOrder] = {}
        #: client_order_id → 成交列表（**未经去重**，重复回报会真的出现两条）。
        self._fills: dict[str, list[Fill]] = {}
        self._fill_seq = 0
        self._clock = 0

    # ------------------------------------------------------------------ 内部
    def _tick(self) -> int:
        """逻辑时钟。每次对外动作 +1，用于「时间不得倒退」的断言。"""
        self._clock += 1
        return self._clock

    def _require_connected(self) -> None:
        if not self._connected:
            from deepquant.broker.errors import NotConnected
            raise NotConnected("MockBroker 处于断连状态（connected=False）")

    def _apply_fill(self, req: OrderRequest, shares: int, price: float) -> Fill:
        """落一笔成交，并同步更新持仓与资金。"""
        fees = compute_fees(req.side, price, shares, self._cfg)
        gross = price * shares
        p = self._positions.get(req.code, Position(code=req.code))
        if req.side is Side.BUY:
            # T+1：买入当日不可卖，所以只加 quantity，不加 available。
            new_p = Position(
                code=req.code,
                quantity=p.quantity + shares,
                available=p.available,
                avg_cost=((p.avg_cost * p.quantity) + gross) / (p.quantity + shares)
                if (p.quantity + shares) else 0.0,
            )
            self._cash = Cash(
                available=self._cash.available - gross - fees.total,
                frozen=self._cash.frozen,
                total=self._cash.total - gross - fees.total,
            )
        else:
            new_p = Position(
                code=req.code,
                quantity=p.quantity - shares,
                available=p.available - shares,
                avg_cost=p.avg_cost,
            )
            self._cash = Cash(
                available=self._cash.available + gross - fees.total,
                frozen=self._cash.frozen,
                total=self._cash.total + gross - fees.total,
            )
        self._positions[req.code] = new_p
        self._fill_seq += 1
        return Fill(
            fill_id=f"{req.client_order_id}-f{self._fill_seq}",
            client_order_id=req.client_order_id,
            code=req.code,
            side=req.side,
            shares=shares,
            price=price,
            fees=fees,
            ts=self._tick(),
        )

    def _record_reject(self, req: OrderRequest, code: RejectCode, detail: str) -> None:
        """落一条废单记录，然后抛出。**必须**抛——静默返回 REJECTED 会被上层当成功。"""
        rejected = BrokerOrder(
            client_order_id=req.client_order_id,
            broker_order_id=None, code=req.code, side=req.side,
            shares=req.shares, limit_price=req.limit_price,
            status=BrokerOrderStatus.REJECTED,
            reject_code=code.value, reject_text=detail,
            updated_ts=self._tick(),
        )
        self._orders[req.client_order_id] = rejected
        raise OrderRejected(code, detail, order=rejected)

    # ------------------------------------------------------------------ 只读
    def get_positions(self) -> Sequence[Position]:
        self._require_connected()
        return tuple(self._positions[c] for c in sorted(self._positions))

    def get_cash(self) -> Cash:
        self._require_connected()
        return self._cash

    def get_orders(self, *, since_ts: int | None = None) -> Sequence[BrokerOrder]:
        self._require_connected()
        rows = [self._orders[k] for k in sorted(self._orders)]
        if since_ts is not None:
            rows = [o for o in rows if o.updated_ts > since_ts]
        return tuple(rows)

    def get_fills(self, *, since_ts: int | None = None) -> Sequence[Fill]:
        """⚠️ **不去重**：``duplicate_fills`` 会让同一 ``fill_id`` 出现两次。

        去重是调用方（``FillReconciler``）的职责——把重复藏在这里，
        上层就永远学不到"回报可能重复"这件事。
        """
        self._require_connected()
        rows: list[Fill] = []
        for cid in sorted(self._fills):
            rows.extend(self._fills[cid])
        if since_ts is not None:
            rows = [f for f in rows if f.ts > since_ts]
        return tuple(rows)

    def get_quote(self, code: str) -> Quote:
        self._require_connected()
        q = self._quotes.get(code)
        if q is None:
            # 拿不到就报错，不返回 0 或上一笔价格。
            raise LookupError(f"MockBroker 没有 {code} 的行情——请先注入 quotes")
        if code in self._script.stale_quotes:
            return Quote(code=q.code, last=q.last, ts=q.ts, stale=True)
        return q

    def get_calendar(self) -> TradingCalendar:
        self._require_connected()
        return self._calendar

    # ------------------------------------------------------------------ 写
    def place_order(self, request: OrderRequest) -> BrokerOrder:
        """提交委托。四种结果：幂等返回 / 拒单 / 超时 / 正常或部分成交。"""
        self._require_connected()

        # ---- 1. 幂等：同一 client_order_id ----
        existing = self._orders.get(request.client_order_id)
        if existing is not None:
            same = (
                existing.code == request.code
                and existing.side == request.side
                and existing.shares == request.shares
                and abs(existing.limit_price - request.limit_price) < 1e-9
            )
            if same:
                return existing
            raise DuplicateOrder(
                f"{request.client_order_id} 已存在且内容不同："
                f"既有 {existing.code}/{existing.side.value}/{existing.shares}@{existing.limit_price}，"
                f"本次 {request.code}/{request.side.value}/{request.shares}@{request.limit_price}。"
                "同一幂等键代表两个意图，必须人工介入。")

        # ---- 2. 券商侧的资金/持仓校验（真实柜台也会做，不能只靠上层风控） ----
        # 上层 RiskGate 已经查过一遍，但券商同样会拒——这里如实复现，
        # 否则「风控放过但券商拒」这条路在 Mock 里永远走不到。
        if request.side is Side.BUY:
            need = request.limit_price * request.shares
            need += compute_fees(request.side, request.limit_price, request.shares, self._cfg).total
            if need > self._cash.available + 1e-9:
                self._record_reject(request, RejectCode.INSUFFICIENT_CASH,
                                    f"需要 {need:.2f} 元，可用 {self._cash.available:.2f}")
        else:
            held = self._positions.get(request.code, Position(code=request.code))
            if request.shares > held.available:
                self._record_reject(request, RejectCode.INSUFFICIENT_POSITION,
                                    f"委托 {request.shares} 股，可卖 {held.available} 股")

        # ---- 3. 拒单（剧本注入） ----
        code_reject = self._script.reject.get(request.code)
        if code_reject is not None:
            self._record_reject(request, code_reject, f"{request.code} 被拒（剧本注入）")

        # ---- 4. 超时：委托已落地但状态未知 ----
        if request.code in self._script.timeout:
            unknown = BrokerOrder(
                client_order_id=request.client_order_id,
                broker_order_id=f"B{len(self._orders) + 1:06d}",
                code=request.code, side=request.side,
                shares=request.shares, limit_price=request.limit_price,
                status=BrokerOrderStatus.UNKNOWN,
                updated_ts=self._tick(),
            )
            self._orders[request.client_order_id] = unknown
            raise BrokerTimeout(
                f"{request.code} 请求超时；委托已在券商侧落地但状态未知"
                f"（client_order_id={request.client_order_id}）")

        # ---- 5. 正常或部分成交 ----
        ratio = self._script.fill_ratio.get(request.code, 1.0)
        lot = self._cfg.market_rules.lot_size
        target = (round_down_to_lot(int(request.shares * ratio), lot)
                  if ratio < 1.0 else request.shares)
        if target <= 0:
            order = BrokerOrder(
                client_order_id=request.client_order_id,
                broker_order_id=f"B{len(self._orders) + 1:06d}",
                code=request.code, side=request.side,
                shares=request.shares, limit_price=request.limit_price,
                status=BrokerOrderStatus.ACCEPTED,
                updated_ts=self._tick(),
            )
            self._orders[request.client_order_id] = order
            return order

        fill = self._apply_fill(request, target, request.limit_price)

        if request.code in self._script.drop_fills:
            # 成交真实发生了（持仓已变），但回报没回来 ⇒ 状态只能记 UNKNOWN。
            order = BrokerOrder(
                client_order_id=request.client_order_id,
                broker_order_id=f"B{len(self._orders) + 1:06d}",
                code=request.code, side=request.side,
                shares=request.shares, limit_price=request.limit_price,
                status=BrokerOrderStatus.UNKNOWN,
                filled_shares=target, avg_fill_price=request.limit_price,
                updated_ts=self._tick(),
            )
            self._orders[request.client_order_id] = order
            # 成交不进 _fills：模拟"回报丢失"。持仓已变而回报不可见，
            # 这正是对账能发现的差异来源。
            self._fills[request.client_order_id] = []
            return order

        self._fills[request.client_order_id] = [fill]
        if request.code in self._script.duplicate_fills:
            # 🔴 复用**同一条** Fill（同 fill_id），不是再成交一次。
            self._fills[request.client_order_id].append(fill)

        status = (BrokerOrderStatus.FILLED if target == request.shares
                  else BrokerOrderStatus.PARTIALLY_FILLED)
        order = BrokerOrder(
            client_order_id=request.client_order_id,
            broker_order_id=f"B{len(self._orders) + 1:06d}",
            code=request.code, side=request.side,
            shares=request.shares, limit_price=request.limit_price,
            status=status, filled_shares=target, avg_fill_price=request.limit_price,
            updated_ts=self._tick(),
        )
        self._orders[request.client_order_id] = order
        return order

    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        """撤单。已成交/已拒的委托**撤不掉**，返回其真实状态而不是抛异常。"""
        self._require_connected()
        o = self._orders.get(client_order_id)
        if o is None:
            raise LookupError(f"未知委托 {client_order_id}")
        if o.status in (BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED,
                        BrokerOrderStatus.REJECTED):
            return o
        cancelled = BrokerOrder(
            client_order_id=o.client_order_id, broker_order_id=o.broker_order_id,
            code=o.code, side=o.side, shares=o.shares, limit_price=o.limit_price,
            status=BrokerOrderStatus.CANCELLED,
            filled_shares=o.filled_shares, avg_fill_price=o.avg_fill_price,
            updated_ts=self._tick(),
        )
        self._orders[client_order_id] = cancelled
        return cancelled
