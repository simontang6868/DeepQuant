"""交易风控闸门：11 条硬限，任何一条不过则拒单。

⚠️ 与 ``deepquant.risk.gateway`` 的区别（**不要合并这两者**）
----------------------------------------------------------
``risk.gateway`` 是 **API 层**的 fail-closed 检查点网关：数据集在不在、字段全不全。
本模块是**交易风控闸门**：授权金额、单票上限、委托笔数、熔断线。

一个是"这个请求能不能进系统"，一个是"这笔单能不能发到市场"。职责不同、失败后果
不同、放行条件不同。合并会让两套判定互相污染，且没人说得清哪条规则属于哪一层。

三条贯穿全模块的硬规矩
--------------------
1. **缺失输入 = 拒单**，不是"跳过这条检查"。
   本项目在闸门上反复犯这一个错：「拿不到数据 ⇒ 不检查 ⇒ 当作通过」。
   所以 ``OrderContext`` 里凡是可以为 ``None`` 的字段，检查器遇到 ``None``
   一律返回 ``MISSING_INPUT``（见验收标准缺陷 P4）。
2. **停在第一个失败**。继续跑后面的检查会产出「缺 A」和「缺 B」两条原因，
   调用方按第一条修完再试又收到第二条，像打地鼠。停下来并记下已通过的检查，
   既够定位又不会误导。
3. **峰值必须能持久化**。熔断判的是"相对峰值的回撤"，峰值一旦在重启后丢失，
   回撤会被重算成 0，熔断永远不会触发（见验收标准缺陷 P5）。
   ``GateState.save/load`` 就是为此存在——它不是便利方法，是功能的一部分。
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass
from typing import Callable, Mapping

from deepquant.broker.models import OrderRequest, Quote
from deepquant.broker.errors import RejectCode

__all__ = [
    "Authorization",
    "NoAuthorization",
    "RiskLimits",
    "GateState",
    "ClockWentBackwards",
    "OrderContext",
    "GateFailure",
    "GateDecision",
    "RiskGate",
]


class NoAuthorization(RuntimeError):
    """授权文件缺失或不可解析。**没有它就等于没有开实盘的许可**。"""


class ClockWentBackwards(RuntimeError):
    """交易日倒退。时钟漂移不得被当成"跨日"。"""


# ---------------------------------------------------------------------------
# 授权（只读）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Authorization:
    """实盘授权。由**用户亲手创建**，agent 不得创建、修改、延期。

    本类只负责**读**。没有提供任何写方法，这是有意的：
    代码里不存在的操作，比"有方法但约定不调用"更难被绕过。
    """

    max_notional: float
    valid_until: dt.date
    boards: frozenset[str]
    raw_path: str = ""

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "Authorization":
        p = pathlib.Path(path)
        if not p.exists():
            raise NoAuthorization(
                f"授权文件不存在：{p}。实盘开关默认关闭，"
                "必须由用户亲手创建该文件后才能下单；agent 不得代为创建。")
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:                       # noqa: BLE001
            raise NoAuthorization(f"授权文件不可解析：{p}（{exc}）") from exc
        for key in ("max_notional", "valid_until", "boards"):
            if key not in d:
                raise NoAuthorization(f"授权文件缺字段 {key!r}：{p}")
        try:
            valid_until = dt.date.fromisoformat(str(d["valid_until"]))
        except ValueError as exc:
            raise NoAuthorization(f"valid_until 不是 ISO 日期：{d['valid_until']!r}") from exc
        if not isinstance(d["boards"], (list, tuple)) or not d["boards"]:
            raise NoAuthorization("boards 必须是非空列表")
        return cls(
            max_notional=float(d["max_notional"]),
            valid_until=valid_until,
            boards=frozenset(str(b) for b in d["boards"]),
            raw_path=str(p),
        )


# ---------------------------------------------------------------------------
# 限额
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RiskLimits:
    """风控参数。**全部无默认值**——每一个都要有人负责。

    为什么不给默认值：给了默认值之后，"这个数是怎么来的"就消失了。
    本项目在成本口径上因为"看起来合理的默认值"错过四次。

    用 :meth:`for_strategy_capital` 按 D1/D5 的公式推，避免手写魔数。
    """

    #: 单票市值上限占策略仓权益的比例。任务书：5 只等权 ⇒ 0.20。
    max_single_position_ratio: float
    #: 单笔委托金额上限（元）。
    max_order_notional: float
    #: 单日委托笔数上限。
    max_daily_orders: int
    #: 委托价相对最新价的最大偏离。任务书：1%。
    max_price_deviation: float
    #: 行情最多可以陈旧多少个自然日。
    max_quote_age_days: int
    #: 熔断线：相对峰值的回撤阈值（D2 = 2.5%）。
    max_drawdown: float
    #: 黑名单（代码集合）。
    blacklist: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not (0 < self.max_single_position_ratio <= 1):
            raise ValueError("max_single_position_ratio 必须在 (0,1]")
        if self.max_order_notional <= 0:
            raise ValueError("max_order_notional 必须为正")
        if self.max_daily_orders < 1:
            raise ValueError("max_daily_orders 必须 >= 1")
        if not (0 < self.max_price_deviation < 1):
            raise ValueError("max_price_deviation 必须在 (0,1)")
        if not (0 < self.max_drawdown < 1):
            raise ValueError("max_drawdown 必须在 (0,1)")

    @classmethod
    def for_strategy_capital(
        cls,
        *,
        stock_capital: float,
        n_hold: int,
        max_price_deviation: float,
        max_quote_age_days: int,
        max_drawdown: float,
        max_daily_orders: int,
        blacklist: frozenset[str] = frozenset(),
    ) -> "RiskLimits":
        """按「策略仓 ÷ 持仓数」推单票与单笔上限。

        ``stock_capital`` 是**策略仓**（D1 的 ``S``），不是账户总额。
        两者混用会让单票上限大一倍——而"大一点"在风控里从来不是好事。
        """
        if stock_capital <= 0 or n_hold < 1:
            raise ValueError("stock_capital 必须为正且 n_hold >= 1")
        per = stock_capital / n_hold
        return cls(
            max_single_position_ratio=1.0 / n_hold,
            max_order_notional=per,
            max_daily_orders=max_daily_orders,
            max_price_deviation=max_price_deviation,
            max_quote_age_days=max_quote_age_days,
            max_drawdown=max_drawdown,
            blacklist=blacklist,
        )


# ---------------------------------------------------------------------------
# 跨委托状态（可持久化）
# ---------------------------------------------------------------------------
@dataclass
class GateState:
    """闸门的跨委托状态。

    ⚠️ ``peak_equity`` 必须落盘（见模块文档第 3 条）。
    """

    peak_equity: float | None = None
    trading_day: int = 0
    daily_order_count: int = 0
    halted: bool = False
    halt_reason: str = ""
    breaker_tripped: bool = False
    breaker_reason: str = ""
    #: 上一轮对账结论。``None`` = 从未对账 ⇒ **视为不一致**（保守）。
    last_reconciliation_ok: bool | None = None
    #: 是否存在状态未知的委托。``True`` ⇒ 禁止新单。
    has_unknown_orders: bool = False

    # ---- 持久化 ----
    def to_dict(self) -> dict:
        return {
            "peak_equity": self.peak_equity,
            "trading_day": self.trading_day,
            "daily_order_count": self.daily_order_count,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "breaker_tripped": self.breaker_tripped,
            "breaker_reason": self.breaker_reason,
            "last_reconciliation_ok": self.last_reconciliation_ok,
            "has_unknown_orders": self.has_unknown_orders,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "GateState":
        return cls(
            peak_equity=d.get("peak_equity"),
            trading_day=int(d.get("trading_day", 0)),
            daily_order_count=int(d.get("daily_order_count", 0)),
            halted=bool(d.get("halted", False)),
            halt_reason=str(d.get("halt_reason", "")),
            breaker_tripped=bool(d.get("breaker_tripped", False)),
            breaker_reason=str(d.get("breaker_reason", "")),
            last_reconciliation_ok=d.get("last_reconciliation_ok"),
            has_unknown_orders=bool(d.get("has_unknown_orders", False)),
        )

    def save(self, path: str | pathlib.Path) -> None:
        """原子写。峰值是这个文件里最要紧的东西。"""
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")
        tmp.replace(p)

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "GateState":
        p = pathlib.Path(path)
        if not p.exists():
            return cls()
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    # ---- 日切 ----
    def roll_day(self, day: int) -> None:
        """跨日重置**当日计数**。

        ⚠️⚠️ 两个刻意不做的事，每一个都对应一个真实故障：

        1. **不重置 ``has_unknown_orders``**。UNKNOWN 是**券商侧的事实**，
           不因为日历翻页而消失。早期版本在这里顺手清了它，效果是：昨天丢了
           一笔回报，今天系统"自动痊愈"继续下单——而账实从那一刻起就错了。
           它只能由 ``apply_rebuild_to_gate_state``（以券商为准重建）清除。
        2. **不重置 ``peak_equity`` 与 ``breaker_tripped``**。这两个与日期无关，
           重置峰值等于把熔断线抹掉（缺陷预登记 P5）。

        ⭐ 日期**倒退**直接抛异常，不重置。时钟漂移若被当成"又跨日了"，
        当日委托笔数会被清零，11 条硬限里第 3 条（单日笔数上限）当场失效。
        """
        if day < self.trading_day:
            raise ClockWentBackwards(
                f"交易日从 {self.trading_day} 倒退到 {day}——时钟漂移。"
                "倒退若被当作跨日，当日委托笔数会被清零（绕过单日笔数上限）")
        if day == self.trading_day:
            return
        self.trading_day = day
        self.daily_order_count = 0
        # has_unknown_orders 刻意保留，见 docstring 第 1 条。


# ---------------------------------------------------------------------------
# 单笔委托的上下文
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OrderContext:
    """评估一笔委托所需的全部输入。

    ⚠️ 可空的字段都是**故意的**：它们代表"当前拿不到"。检查器遇到 ``None``
    必须拒单而不是放行。把 `board` 之类的字段做成空字符串默认值，就等于
    给"不知道"和"是主板"分配了同一个值。
    """

    order: OrderRequest
    #: 最新行情。``None`` = 拿不到。
    quote: Quote | None = None
    #: 策略仓当前总权益（现金 + 持仓市值）。``None`` = 算不出。
    equity: float | None = None
    #: 该标的当前持仓市值（含本笔委托后的预估）。``None`` = 算不出。
    position_value: float | None = None
    #: 板块。``None`` = 判不出。
    board: str | None = None
    #: 是否处于交易时段。``None`` = 判断不了（例如时钟不可信）。
    is_trading_hours: bool | None = None
    #: 当前交易日 YYYYMMDD。``None`` = 不知道。
    trading_day: int | None = None
    #: 停机开关文件是否存在。``None`` = 查不到。
    halt_file_present: bool | None = None


@dataclass(frozen=True)
class GateFailure:
    check: str
    code: RejectCode
    detail: str


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    failure: GateFailure | None = None
    #: 已通过（或已执行）的检查名，按顺序。失败时**不含**失败项之后的检查。
    checked: tuple[str, ...] = ()

    @property
    def code(self) -> RejectCode | None:
        return self.failure.code if self.failure else None


# ---------------------------------------------------------------------------
# 闸门
# ---------------------------------------------------------------------------
CheckFn = Callable[["RiskGate", OrderContext, "Authorization | None"], "GateFailure | None"]


class RiskGate:
    """11 条硬限 + UNKNOWN 委托封锁。

    检查**顺序固定**（``CHECKS``），停在第一个失败。
    """

    def __init__(
        self,
        *,
        limits: RiskLimits,
        state: GateState | None = None,
        authorization: Authorization | None = None,
        state_path: str | pathlib.Path | None = None,
    ) -> None:
        self.limits = limits
        self.state = state or GateState()
        self.auth = authorization
        self.state_path = pathlib.Path(state_path) if state_path else None

    # ------------------------------------------------------------------ 观测
    def observe_equity(self, equity: float, *, today: dt.date | None = None) -> None:
        """记录一次权益观测：更新峰值，必要时触发熔断并落盘。

        峰值只升不降。回撤 = 1 − equity/peak。
        """
        if equity <= 0:
            raise ValueError(f"权益必须为正，收到 {equity}")
        if self.state.peak_equity is None or equity > self.state.peak_equity:
            self.state.peak_equity = equity
        dd = 1.0 - equity / self.state.peak_equity
        # ⚠️ 必须带容差：1 - 19500/20000 在 float64 下是 0.025000000000000022，
        #    直接跟 0.025 比会把「恰好触及阈值」判成「超出」。这与价格偏离检查
        #    用同一套处理：边界比较一律加 1e-12 的相对容差。
        if dd > self.limits.max_drawdown + 1e-12 and not self.state.breaker_tripped:
            self.state.breaker_tripped = True
            self.state.breaker_reason = (
                f"回撤 {dd:.4%} 超过阈值 {self.limits.max_drawdown:.2%}"
                f"（峰值 {self.state.peak_equity:.2f}，当前 {equity:.2f}）")
        self._persist()

    def _persist(self) -> None:
        if self.state_path is not None:
            self.state.save(self.state_path)

    # ------------------------------------------------------------------ 评估
    def evaluate(self, ctx: OrderContext) -> GateDecision:
        """按固定顺序评估。第一个失败即返回。"""
        checked: list[str] = []
        for name, fn in self._checks():
            outcome = fn(self, ctx, self.auth)
            if outcome is not None:
                return GateDecision(allowed=False, failure=outcome, checked=tuple(checked))
            checked.append(name)
        return GateDecision(allowed=True, checked=tuple(checked))

    @property
    def checks(self) -> tuple[tuple[str, CheckFn], ...]:
        """检查顺序。**顺序本身就是规则**：先问"该不该停机"，再问"这笔单合不合规"。"""
        return _CHECKS

    def _checks(self) -> tuple[tuple[str, CheckFn], ...]:
        return _CHECKS

    # ------------------------------------------------------------------ 记账
    def record_order(self, ctx: OrderContext) -> None:
        """委托放行后调用。**只有放行的才计数**——被拒不占额度。"""
        if ctx.trading_day is not None:
            self.state.roll_day(ctx.trading_day)
        self.state.daily_order_count += 1
        self._persist()

    # ------------------------------------------------------------------ 控制
    def halt(self, reason: str) -> None:
        self.state.halted = True
        self.state.halt_reason = reason
        self._persist()

    def resume(self) -> None:
        """解除停机。**不解除熔断**——熔断要人工复核后另行复位。"""
        self.state.halted = False
        self.state.halt_reason = ""
        self._persist()

    def reset_breaker(self) -> None:
        """人工复位熔断。重置后峰值从当前权益重新起算由调用方决定。"""
        self.state.breaker_tripped = False
        self.state.breaker_reason = ""
        self._persist()


# ---------------------------------------------------------------------------
# 检查器实现
# ---------------------------------------------------------------------------
def _missing(check: str, what: str) -> GateFailure:
    """缺失输入的统一出口。

    ⚠️ **必须带具体检查名**：若统一写成 "missing"，台账里就只剩「某处缺输入」，
    事后无法定位是哪一条检查拿不到数据。
    """
    return GateFailure(check, RejectCode.MISSING_INPUT,
                       f"缺少 {what}；按规则「缺失即拒」，不得视为通过")


def _c_halt(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    # 状态里已停机
    if gate.state.halted:
        return GateFailure("halt_switch", RejectCode.HALT_SWITCH,
                           f"停机中：{gate.state.halt_reason or '未填原因'}")
    # 文件开关：查不到就不能当作"没停机"
    if ctx.halt_file_present is None:
        return _missing("halt_switch", "停机开关状态（halt_file_present）")
    if ctx.halt_file_present:
        return GateFailure("halt_switch", RejectCode.HALT_SWITCH, "停机开关文件存在")
    return None


def _c_breaker(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    if gate.state.breaker_tripped:
        return GateFailure("circuit_breaker", RejectCode.CIRCUIT_BREAKER,
                           f"已熔断：{gate.state.breaker_reason or '未填原因'}")
    return None


def _c_authorization(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    if auth is None:
        return GateFailure("authorization", RejectCode.NO_AUTHORIZATION,
                           "未加载授权文件；实盘开关默认关闭")
    if ctx.trading_day is None:
        return _missing("authorization", "交易日（无法判断授权是否过期）")
    try:
        today = dt.datetime.strptime(str(ctx.trading_day), "%Y%m%d").date()
    except ValueError:
        return GateFailure("authorization", RejectCode.MISSING_INPUT,
                           f"交易日格式非法：{ctx.trading_day!r}")
    if today > auth.valid_until:
        return GateFailure("authorization", RejectCode.NOT_AUTHORIZED,
                           f"授权已于 {auth.valid_until} 到期（今天 {today}）")
    if ctx.board is None:
        return _missing("authorization", "板块（无法判断是否在授权范围）")
    if ctx.board not in auth.boards:
        return GateFailure("authorization", RejectCode.NOT_AUTHORIZED,
                           f"板块 {ctx.board} 不在授权范围 {sorted(auth.boards)}")
    notional = ctx.order.shares * ctx.order.limit_price
    if notional > auth.max_notional:
        return GateFailure("authorization", RejectCode.NOT_AUTHORIZED,
                           f"委托金额 {notional:.2f} 超过授权上限 {auth.max_notional:.2f}")
    return None


def _c_reconciliation(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    # None = 从未对账 ⇒ 视为不一致（保守方向）
    if gate.state.last_reconciliation_ok is not True:
        return GateFailure("reconciliation", RejectCode.RECONCILIATION_MISMATCH,
                           "上一轮对账未通过或从未对账；差异未解释前禁止新单")
    return None


def _c_unknown_orders(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    if gate.state.has_unknown_orders:
        return GateFailure("unknown_orders", RejectCode.UNKNOWN_ORDER_PENDING,
                           "存在状态未知的委托；以券商为准重建前禁止新单")
    return None


def _c_trading_hours(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    if ctx.is_trading_hours is None:
        return _missing("trading_hours", "交易时段判断（is_trading_hours）")
    if not ctx.is_trading_hours:
        return GateFailure("trading_hours", RejectCode.NOT_TRADING_HOURS, "当前不在交易时段")
    return None


def _c_blacklist(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    if ctx.order.code in gate.limits.blacklist:
        return GateFailure("blacklist", RejectCode.BLACKLIST,
                           f"{ctx.order.code} 在黑名单内")
    return None


def _c_quote(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    if ctx.quote is None:
        return GateFailure("quote", RejectCode.STALE_QUOTE,
                           "没有行情，无法校验价格；不得跳过价格类检查")
    if ctx.quote.stale:
        return GateFailure("quote", RejectCode.STALE_QUOTE,
                           f"{ctx.order.code} 行情已标记陈旧")
    if ctx.quote.last <= 0:
        return GateFailure("quote", RejectCode.STALE_QUOTE,
                           f"{ctx.order.code} 行情价格非法：{ctx.quote.last}")
    if ctx.trading_day is None:
        return _missing("quote", "交易日（无法判断行情年龄）")
    age = (dt.datetime.strptime(str(ctx.trading_day), "%Y%m%d").date()
           - dt.datetime.strptime(str(ctx.quote.ts), "%Y%m%d").date()).days
    if age > gate.limits.max_quote_age_days:
        return GateFailure("quote", RejectCode.STALE_QUOTE,
                           f"行情已过期 {age} 天（上限 {gate.limits.max_quote_age_days}）")
    return None


def _c_price_deviation(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    # 这一条是缺陷 P4 的正身：拿不到最新价时**必须拒**，不能 `if quote: check()`。
    if ctx.quote is None or ctx.quote.last <= 0:
        return GateFailure("price_deviation", RejectCode.MISSING_INPUT,
                           "没有最新价，无法校验委托价偏离；缺失即拒")
    dev = abs(ctx.order.limit_price - ctx.quote.last) / ctx.quote.last
    if dev > gate.limits.max_price_deviation + 1e-12:
        return GateFailure("price_deviation", RejectCode.PRICE_DEVIATION,
                           f"委托价 {ctx.order.limit_price} 偏离最新价 {ctx.quote.last}"
                           f" 达 {dev:.4%}（上限 {gate.limits.max_price_deviation:.2%}）")
    return None


def _c_position_limit(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    if ctx.equity is None:
        return _missing("position_limit", "策略仓权益（equity）")
    if ctx.position_value is None:
        return _missing("position_limit", "标的持仓市值（position_value）")
    cap = ctx.equity * gate.limits.max_single_position_ratio
    if ctx.position_value > cap + 1e-9:
        return GateFailure("position_limit", RejectCode.POSITION_LIMIT,
                           f"{ctx.order.code} 持仓市值 {ctx.position_value:.2f}"
                           f" 超过单票上限 {cap:.2f}")
    return None


def _c_order_notional(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    notional = ctx.order.shares * ctx.order.limit_price
    if notional > gate.limits.max_order_notional + 1e-9:
        return GateFailure("order_notional", RejectCode.ORDER_NOTIONAL_LIMIT,
                           f"单笔 {notional:.2f} 超过上限 {gate.limits.max_order_notional:.2f}")
    return None


def _c_daily_count(gate: "RiskGate", ctx: OrderContext, auth) -> GateFailure | None:
    if ctx.trading_day is None:
        return _missing("daily_count", "交易日（无法判断当日笔数）")
    # 若上下文跨日，按"当日已发 0 笔"比较；真正归零在 record_order 里做。
    used = gate.state.daily_order_count if gate.state.trading_day == ctx.trading_day else 0
    if used >= gate.limits.max_daily_orders:
        return GateFailure("daily_count", RejectCode.DAILY_ORDER_LIMIT,
                           f"当日已发 {used} 笔，达上限 {gate.limits.max_daily_orders}")
    return None


#: 固定顺序。**改动这里等于改动规则**，必须有对应的测试变更说明。
_CHECKS: tuple[tuple[str, CheckFn], ...] = (
    ("halt_switch", _c_halt),
    ("circuit_breaker", _c_breaker),
    ("authorization", _c_authorization),
    ("reconciliation", _c_reconciliation),
    ("unknown_orders", _c_unknown_orders),
    ("trading_hours", _c_trading_hours),
    ("blacklist", _c_blacklist),
    ("quote", _c_quote),
    ("price_deviation", _c_price_deviation),
    ("position_limit", _c_position_limit),
    ("order_notional", _c_order_notional),
    ("daily_count", _c_daily_count),
)
