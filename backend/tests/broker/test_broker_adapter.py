"""Phase 2.1：``BrokerAdapter`` 抽象与 ``MockBroker`` 的行为测试。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.1-a ~ 2.1-e。

三条在本文件里被反复验证的性质
------------------------------
1. **接口与券商无关**（2.1-b）：不是"看起来没有厂商名"，而是扫源码字符串。
   一旦有人为了跑通某家 QMT 在抽象层里加了 `xtquant` 分支，这条会立刻红。
2. **故障是剧本驱动的**（2.1-c）：六类故障各一个测试，不合并。
3. **可重放**（2.1-e）：同一剧本 + 同一调用序列 ⇒ 结果逐位相同。
   这是"改代码前后结论可比"的前提，也是本项目全部实验纪律的基础。

⚠️ 「回报重复」那条测试是重点：它必须断言**同一 fill_id 出现两次**，
而不是"成交了两笔"。若 Mock 实现成"再成交一次"，去重逻辑永远测不出来。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.broker import (
    BrokerOrderStatus,
    Cash,
    FaultScript,
    MockBroker,
    OrderRequest,
    Position,
    Quote,
    RejectCode,
    TradingCalendar,
)
from deepquant.broker.base import BrokerAdapter
from deepquant.broker.errors import (
    BrokerTimeout,
    DuplicateOrder,
    OrderRejected,
)
from deepquant.market.rules import Side

BROKER_MODULE_DIR = Path(__import__("deepquant.broker", fromlist=["x"]).__file__).parent


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
@pytest.fixture()
def cfg() -> BacktestConfig:
    """费用口径用项目默认（模拟占位），仅用于结构测试。

    ⚠️ 这些是 `backtest.config` 里的 SIMULATION_* 常量，**不是实盘费率**。
    实盘口径要用 L3ae 的真实综合 0.051%（佣金 0.025% 双边 + 印花 0.05% 仅卖 +
    过户 0.001% 双边），那属于配置，不写死在这里。
    """
    return BacktestConfig(symbol="600000", initial_cash=1_000_000.0)


def _order(cid: str, code: str = "600000", side: Side = Side.BUY,
           shares: int = 100, price: float = 10.0) -> OrderRequest:
    return OrderRequest(client_order_id=cid, code=code, side=side,
                        shares=shares, limit_price=price)


def _broker(cfg: BacktestConfig, **kwargs) -> MockBroker:
    base = dict(
        cost_config=cfg,
        cash=Cash(available=1_000_000.0, frozen=0.0, total=1_000_000.0),
        positions=(Position(code="600000", quantity=1000, available=1000, avg_cost=9.0),),
        quotes={"600000": Quote(code="600000", last=10.0, ts=20261030, stale=False)},
        calendar=TradingCalendar(days=(20261030, 20261102, 20261103)),
    )
    base.update(kwargs)
    return MockBroker(**base)


# ---------------------------------------------------------------------------
# 2.1-a 八项能力齐备
# ---------------------------------------------------------------------------
REQUIRED_METHODS = (
    "get_positions", "get_cash", "get_orders", "get_fills",
    "get_quote", "get_calendar", "place_order", "cancel_order",
)


def test_adapter_declares_all_eight_capabilities():
    """缺一项就不是"能接实盘"的接口。用 `__abstractmethods__` 判，不看文档。"""
    missing = [m for m in REQUIRED_METHODS if not hasattr(BrokerAdapter, m)]
    assert missing == [], f"BrokerAdapter 缺能力：{missing}"
    for m in REQUIRED_METHODS:
        assert getattr(BrokerAdapter, m).__isabstractmethod__, f"{m} 必须是抽象方法"


def test_mock_implements_every_capability(cfg):
    b = _broker(cfg)
    for m in REQUIRED_METHODS:
        assert callable(getattr(b, m))
    # 逐项调一遍，确认不是"有名字但会抛 NotImplementedError"
    assert list(b.get_positions())
    assert b.get_cash().available > 0
    assert b.get_orders() == ()
    assert b.get_fills() == ()
    assert b.get_quote("600000").last == 10.0
    assert b.get_calendar().is_trading_day(20261030) is True


# ---------------------------------------------------------------------------
# 2.1-b 接口层不含券商专有词汇
# ---------------------------------------------------------------------------
VENDOR_TOKENS = (
    "xtquant", "xtdata", "xttrader", "ptrade", "qmt", "miniqmt",
    "easytrader", "ths_trader", "hs_client",
)


def _code_tokens(path: Path) -> tuple[set[str], set[str]]:
    """返回 ``(标识符, 非 docstring 字符串)``。

    ⚠️ 为什么不用 grep 全文：文档里**提到**某个词（"本层不出现 ``xtquant``"）
    与代码里**使用**它（``if vendor == "xtquant"``）是两件事。
    第一版写成 `token in text` 时，两条测试都在描述性 docstring 上误报——
    这正是本项目反复踩的「检查器分不清提到与使用」。

    docstring 单独剔除：``ast.get_docstring`` 取到的串不进返回值，
    但同名的普通字符串常量（真的在代码里比较）仍会被扫到。
    """
    import ast

    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                doc.add(d)
    names: set[str] = set()
    strings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in doc:
                strings.add(node.value)
    return names, strings


def test_no_vendor_vocabulary_in_adapter_layer():
    """抽象层**使用**券商专有名词 = 换券商时上层要改。

    扫的是标识符、import 与非 docstring 字符串常量，不是全文文本。
    """
    offenders = []
    for path in sorted(BROKER_MODULE_DIR.glob("*.py")):
        names, strings = _code_tokens(path)
        blob = " ".join(sorted(names | strings)).lower()
        for tok in VENDOR_TOKENS:
            if tok in blob:
                offenders.append(f"{path.name}: {tok}")
    assert offenders == [], (
        "抽象层出现券商专有名词，换券商时上层会被迫一起改：" + "; ".join(offenders))


def test_adapter_layer_does_not_reimplement_matching():
    """Mock 不做撮合。代码里出现队列位置/参与率/滑点模型就说明它在重造撮合。"""
    names, strings = _code_tokens(BROKER_MODULE_DIR / "mock.py")
    blob = " ".join(sorted(names | strings))
    for token in ("参与率", "队列位置", "price_reachable", "SlippageModel", "queue_position"):
        assert token not in blob, (
            f"mock.py 的代码里出现 {token!r}：撮合属于 eventloop.matching，"
            "在这里再写一套会与回测口径分叉")


def test_code_token_scanner_ignores_prose_but_catches_real_use(tmp_path):
    """检查器自身的变异测试：描述里提到 ≠ 代码里使用。

    没有这条，`_code_tokens` 退化成 grep 也没人会发现。
    """
    p = tmp_path / "sample.py"
    p.write_text(
        '"""本层不出现 xtquant 这类词（这是描述）。"""\n'
        'VENDOR = "xtquant"\n'
        'def f(quote_ratio):\n'
        '    return quote_ratio\n',
        encoding="utf-8",
    )
    names, strings = _code_tokens(p)
    assert "xtquant" not in " ".join(sorted(names)), "docstring 里提到不该被抓"
    assert "xtquant" in " ".join(sorted(strings)), "代码里真的写了就必须抓到"


# ---------------------------------------------------------------------------
# 2.1-c 六类故障
# ---------------------------------------------------------------------------
def test_fault_reject_carries_reason_code(cfg):
    b = _broker(cfg, script=FaultScript(reject={"600000": RejectCode.LIMIT_UP}))
    with pytest.raises(OrderRejected) as ei:
        b.place_order(_order("c1"))
    assert ei.value.code is RejectCode.LIMIT_UP
    # 废单必须留痕，否则对账时无法解释"为什么本地有一笔 REJECTED"
    assert ei.value.order is not None
    assert ei.value.order.status is BrokerOrderStatus.REJECTED
    assert [o.status for o in b.get_orders()] == [BrokerOrderStatus.REJECTED]


def test_fault_partial_fill_stays_partially_filled(cfg):
    b = _broker(cfg, script=FaultScript(fill_ratio={"600000": 0.4}))
    o = b.place_order(_order("c1", shares=1000))
    assert o.status is BrokerOrderStatus.PARTIALLY_FILLED
    assert o.filled_shares == 400
    assert o.filled_shares < o.shares


def test_fault_partial_fill_rounds_down_to_lot(cfg):
    """部分成交也必须是整手。按比例算出 450 股时必须落 400。"""
    b = _broker(cfg, script=FaultScript(fill_ratio={"600000": 0.45}))
    o = b.place_order(_order("c1", shares=1000))
    assert o.filled_shares == 400
    assert o.filled_shares % 100 == 0


def test_fault_dropped_fill_leaves_order_unknown_but_position_moved(cfg):
    """回报丢失：持仓已变、回报不可见、状态只能是 UNKNOWN。

    这三件事同时成立才是"回报丢失"；只把状态改成 UNKNOWN 但持仓没动，
    模拟的是"什么都没发生"，对账永远发现不了差异。
    """
    b = _broker(cfg, script=FaultScript(drop_fills=frozenset({"600000"})))
    before = b.get_positions()[0].quantity
    o = b.place_order(_order("c1", shares=100))
    assert o.status is BrokerOrderStatus.UNKNOWN
    assert b.get_positions()[0].quantity == before + 100     # 持仓确实变了
    assert b.get_fills() == ()                              # 回报确实没回来


def test_fault_duplicate_fill_reuses_same_fill_id(cfg):
    """🔴 重复回报必须复用同一 fill_id（见验收标准缺陷 P1）。

    若实现成"再成交一次"，会得到两个不同 fill_id，上层去重失效、
    持仓凭空翻倍，而日志看上去完全正常。
    """
    b = _broker(cfg, script=FaultScript(duplicate_fills=frozenset({"600000"})))
    b.place_order(_order("c1", shares=100))
    fills = b.get_fills()
    assert len(fills) == 2, "重复回报应当出现两条"
    assert fills[0].fill_id == fills[1].fill_id, "两条必须是同一笔成交"
    assert fills[0].shares == fills[1].shares
    # 持仓只增加一次 —— 重复的是回报，不是成交
    assert b.get_positions()[0].quantity == 1000 + 100


def test_fault_timeout_raises_and_leaves_unknown_order(cfg):
    b = _broker(cfg, script=FaultScript(timeout=frozenset({"600000"})))
    with pytest.raises(BrokerTimeout):
        b.place_order(_order("c1"))
    orders = b.get_orders()
    assert len(orders) == 1 and orders[0].status is BrokerOrderStatus.UNKNOWN
    # 超时不得产生成交
    assert b.get_fills() == ()


def test_fault_stale_quote_is_flagged_not_zeroed(cfg):
    b = _broker(cfg, script=FaultScript(stale_quotes=frozenset({"600000"})))
    q = b.get_quote("600000")
    assert q.stale is True
    assert q.last == 10.0, "陈旧不等于没有价格；把价格改成 0 会让下游算出无穷大回撤"


def test_missing_quote_raises_instead_of_returning_zero(cfg):
    b = _broker(cfg)
    with pytest.raises(LookupError):
        b.get_quote("000001")
    assert True


# ---------------------------------------------------------------------------
# 2.1-d 初始状态必须显式
# ---------------------------------------------------------------------------
def test_initial_state_is_explicit_not_defaulted():
    """`cost_config` 无默认值：给了默认就等于在这里再定义一份费率。"""
    import inspect
    sig = inspect.signature(MockBroker.__init__)
    assert sig.parameters["cost_config"].default is inspect.Parameter.empty


def test_position_rejects_impossible_state():
    """可卖 > 持仓 = T+1 解冻写错了，必须在构造期就炸。"""
    with pytest.raises(ValueError, match="可卖数量"):
        Position(code="600000", quantity=100, available=200)


def test_cash_rejects_inconsistent_parts():
    with pytest.raises(ValueError, match="资金分项"):
        Cash(available=100.0, frozen=0.0, total=50.0)


def test_order_rejected_requires_typed_reason_code():
    """拒单原因必须是枚举。传字符串会让"原因分类"在第一次券商改措辞时断掉。"""
    with pytest.raises(TypeError):
        OrderRejected("limit_up")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2.1-e 可重放
# ---------------------------------------------------------------------------
def _scenario(cfg: BacktestConfig) -> list[tuple]:
    """一段固定脚本：正常买、部分成交卖、重复回报、拒单。"""
    b = _broker(cfg, script=FaultScript(
        fill_ratio={"000001": 0.5},
        duplicate_fills=frozenset({"600000"}),
        reject={"600001": RejectCode.BLACKLIST},
    ))
    out: list[tuple] = []
    for req in (_order("a1", "600000", Side.BUY, 100, 10.0),
                _order("a2", "000001", Side.BUY, 200, 20.0)):
        o = b.place_order(req)
        out.append((o.client_order_id, o.status.value, o.filled_shares, o.avg_fill_price,
                    o.updated_ts))
    with pytest.raises(OrderRejected):
        b.place_order(_order("a3", "600001", Side.BUY, 100, 5.0))
    out.append(tuple((f.fill_id, f.shares, f.price, f.ts) for f in b.get_fills()))
    out.append(tuple((p.code, p.quantity) for p in b.get_positions()))
    out.append(round(b.get_cash().available, 2))
    return out


def test_replay_is_bitwise_identical(cfg):
    """同一剧本 + 同一调用序列 ⇒ 逐位相同。

    若这里失败，说明 Mock 里混进了 wall clock 或随机数，
    之后任何"改代码前后对比"都不再可信。
    """
    assert _scenario(cfg) == _scenario(cfg)


def test_fill_ids_are_derived_not_random(cfg):
    """fill_id 必须由 client_order_id 派生，不能是 uuid/时间戳。"""
    b = _broker(cfg)
    b.place_order(_order("fixed-id-001"))
    fills = b.get_fills()
    assert fills[0].fill_id.startswith("fixed-id-001")
    assert not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-.*", fills[0].fill_id)


# ---------------------------------------------------------------------------
# 幂等（2.2-b 的前置：券商侧也必须幂等）
# ---------------------------------------------------------------------------
def test_same_order_is_idempotent(cfg):
    b = _broker(cfg)
    o1 = b.place_order(_order("same-1"))
    o2 = b.place_order(_order("same-1"))
    assert o1 == o2
    assert len(b.get_fills()) == 1, "重复提交不得重复成交"


def test_same_id_different_content_is_an_incident(cfg):
    b = _broker(cfg)
    b.place_order(_order("same-2", shares=100))
    with pytest.raises(DuplicateOrder, match="两个意图"):
        b.place_order(_order("same-2", shares=200))


# ---------------------------------------------------------------------------
# 券商侧资金/持仓校验（不能只靠上层风控）
# ---------------------------------------------------------------------------
def test_broker_rejects_insufficient_cash(cfg):
    b = _broker(cfg, cash=Cash(available=100.0, frozen=0.0, total=100.0))
    with pytest.raises(OrderRejected) as ei:
        b.place_order(_order("c1", shares=1000, price=10.0))
    assert ei.value.code is RejectCode.INSUFFICIENT_CASH


def test_broker_rejects_selling_more_than_available(cfg):
    b = _broker(cfg)
    with pytest.raises(OrderRejected) as ei:
        b.place_order(_order("c1", side=Side.SELL, shares=2000))
    assert ei.value.code is RejectCode.INSUFFICIENT_POSITION


def test_sell_frees_cash_but_t_plus_one_still_blocks_same_day_resell(cfg):
    """卖出加现金，但当日买入的部分仍不可卖 —— T+1 是账户结构，不是检查。"""
    b = _broker(cfg)
    cash0 = b.get_cash().available
    b.place_order(_order("s1", side=Side.SELL, shares=500, price=10.0))
    assert b.get_cash().available > cash0

    b.place_order(_order("b1", side=Side.BUY, shares=100, price=10.0))
    with pytest.raises(OrderRejected) as ei:
        b.place_order(_order("s2", side=Side.SELL, shares=1000, price=10.0))
    assert ei.value.code is RejectCode.INSUFFICIENT_POSITION


# ---------------------------------------------------------------------------
# 撤单
# ---------------------------------------------------------------------------
def test_cancel_filled_order_returns_truth_not_exception(cfg):
    b = _broker(cfg)
    b.place_order(_order("c1"))
    o = b.cancel_order("c1")
    assert o.status is BrokerOrderStatus.FILLED, "已成交撤不掉，应返回真实状态"


def test_cancel_accepted_order_transitions(cfg):
    b = _broker(cfg, script=FaultScript(fill_ratio={"600000": 0.0}))
    o = b.place_order(_order("c1", shares=100))
    assert o.status is BrokerOrderStatus.ACCEPTED
    assert b.cancel_order("c1").status is BrokerOrderStatus.CANCELLED


def test_disconnected_broker_raises(cfg):
    from deepquant.broker.errors import NotConnected
    b = _broker(cfg, connected=False)
    with pytest.raises(NotConnected):
        b.get_positions()


# ---------------------------------------------------------------------------
# 日历：不知道就说不知道
# ---------------------------------------------------------------------------
def test_calendar_returns_none_outside_known_range(cfg):
    cal = TradingCalendar(days=(20261030, 20261102))
    assert cal.is_trading_day(20261031) is False      # 在范围内且不在集合里
    assert cal.is_trading_day(20261231) is None       # 超出已知范围 ⇒ 不可知
    assert cal.next_trading_day(20261030) == 20261102
    assert cal.next_trading_day(20261102) is None
