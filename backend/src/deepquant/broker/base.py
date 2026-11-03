"""``BrokerAdapter``：与券商交互的唯一接口。

为什么先定接口、后接券商
------------------------
D3 只定到「中原证券」，接口类型（QMT / PTrade / 官方 API）、准入门槛、收费
**全部未核实**。如果先写某一家的实现，等于把"这家券商能做什么"当成"券商能做什么"，
换券商时上层全部要改。

所以本层只声明**与券商无关的 8 项能力**，并且：
1. 接口里**不出现任何券商专有名词**（``xtquant`` / ``ptrade`` / ``qmt`` 等），
   由测试 ``test_no_vendor_vocabulary`` 守住；
2. 所有方法都**返回事实，不返回推测**。拿不到就抛异常，不返回 ``None`` 让调用方猜；
3. 状态不确定时必须能表达 ``UNKNOWN``，不许收敛成 ``ACCEPTED``。

⚠️ 与 ``deepquant.risk.gateway`` 的区别（**不要合并这两者**）
----------------------------------------------------------
``risk.gateway`` 是 **API 层的 fail-closed 检查点网关**：数据集在不在、字段全不全。
本层的 ``execution.risk_gate.RiskGate`` 是**交易风控闸门**：授权金额、单票上限、
委托笔数、熔断线。

一个是"这个请求能不能进系统"，一个是"这笔单能不能发到市场"。职责不同、
失败后果不同、放行条件不同。合并会让两套判定互相污染。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from deepquant.broker.models import (
    AccountSnapshot,
    BrokerOrder,
    Cash,
    Fill,
    OrderRequest,
    Position,
    Quote,
    TradingCalendar,
)

__all__ = ["BrokerAdapter"]


class BrokerAdapter(ABC):
    """券商适配器抽象。8 项能力，缺一不可。

    实现方约定：

    * **只读方法不得有副作用**。``get_*`` 系列可以被反复调用、可以被对账循环调用，
      如果它顺手撤单或改状态，对账本身就成了事故源。
    * **写方法必须幂等**。``place_order`` 收到已存在的 ``client_order_id`` 时，
      内容相同则返回既有委托、内容不同则抛 ``DuplicateOrder``。
    * **时间戳用逻辑时钟**（``ts: int``），不是 wall clock。回放测试要能重现同一结论。
    """

    # ------------------------------------------------------------------ 只读
    @abstractmethod
    def get_positions(self) -> Sequence[Position]:
        """当前持仓。**以券商为准**，本地缓存不得覆盖它。"""

    @abstractmethod
    def get_cash(self) -> Cash:
        """当前资金。"""

    @abstractmethod
    def get_orders(self, *, since_ts: int | None = None) -> Sequence[BrokerOrder]:
        """委托列表。``since_ts`` 用于增量拉取（含状态变更）。"""

    @abstractmethod
    def get_fills(self, *, since_ts: int | None = None) -> Sequence[Fill]:
        """成交回报。

        ⚠️ 同一 ``fill_id`` 可能出现多次（券商重发）。**调用方负责去重**——
        本层不做去重，因为"重复"与"真的又成交一笔"只能靠 ``fill_id`` 区分，
        而隐藏重复会让上层永远看不到这个区别。
        """

    @abstractmethod
    def get_quote(self, code: str) -> Quote:
        """最新行情。拿不到抛异常，**不返回 0 或上一笔价格**。"""

    @abstractmethod
    def get_calendar(self) -> TradingCalendar:
        """交易日历。未来日期返回 ``None``，不猜测。"""

    # ------------------------------------------------------------------ 写
    @abstractmethod
    def place_order(self, request: OrderRequest) -> BrokerOrder:
        """提交委托。返回券商确认后的委托状态。

        可能的结果只有三种，实现方必须区分：
        * 正常 → 返回 ``PENDING_NEW`` / ``ACCEPTED`` / 已成交 的委托；
        * 明确拒绝 → 抛 :class:`~deepquant.broker.errors.OrderRejected`（带原因码）；
        * **不确定**（超时、回报丢失）→ 返回 ``status=UNKNOWN`` 的委托，
          或抛 :class:`~deepquant.broker.errors.BrokerTimeout`。
          **不得**为了"看起来成功"而返回 ``ACCEPTED``。
        """

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> BrokerOrder:
        """撤单。已成交的委托撤单失败属正常，应返回其真实状态而非抛异常。"""

    # ------------------------------------------------------------------ 便捷
    def snapshot(self, ts: int = 0) -> AccountSnapshot:
        """持仓 + 资金的合并快照。对账的输入。"""
        return AccountSnapshot(cash=self.get_cash(), positions=tuple(self.get_positions()), ts=ts)
