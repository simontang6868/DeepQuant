"""板块与交易权限：账户能买哪些板块的股票。

## 为什么必须显式建模

2026-11-03 用户确认：**账户没有创业板交易权限**。这不是策略参数、也不是回测口径
——它属于"账户能做什么"的执行层事实。不显式建它的后果是：目标持仓里出现 300/301
时，规划器照样生成买单，发出去被券商拒掉，而且拒单原因埋在券商报文里；
更糟的形态是"没人发现整个月的仓位因此缺一块"。

## 判定表（按 6 位代码前缀）

| 前缀 | 板块 | 说明 |
| --- | --- | --- |
| 600 601 603 605 | 沪市主板 | |
| 000 001 002 003 | 深市主板 | 002 原中小板，2021 年并入深主板 |
| 300 301 | **创业板** | 需创业板权限（2 年经验 + 10 万资产） |
| 688 689 | 科创板 | 需科创板权限（2 年经验 + 50 万资产） |
| 43 83 87 92 | 北交所 | 需北交所权限 |

未知前缀一律 ``ValueError``：宁可规划失败让人来问，也不能默认放行。

## 失败关闭（fail-closed）

``BoardPermissions.from_config`` 只接受显式列出的板块名；**空列表 = 什么都不许买**。
"没配置"不等于"全部允许"——那正是权限闸最危险的形态。因此规划入口必须显式传入；
不传（``permissions=None``）时行为与旧版兼容，但 ``PlanResult.permission_unchecked``
为 ``True``，Phase 3 的发单入口必须断言它为 ``False``（见 ``require_checked``）。

## 卖出永远不受权限闸限制

权限闸只拦**买入**。持仓的卖出不受板块权限限制（且账户不可能持有无权限板块的
股票——它当初就买不进来）。把卖出也拦住会让"退出持仓"变成不可能，那是事故。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

__all__ = ["Board", "BoardPermissions", "board_of", "require_checked",
           "substitute_targets"]


class Board(str, Enum):
    """A 股板块。``value`` 即 ``BoardPermissions.from_config`` 接受的配置名。"""

    SH_MAIN = "sh_main"
    SZ_MAIN = "sz_main"
    CHINEXT = "chinext"
    STAR = "star"
    BJ = "bj"


#: 前缀 → 板块。按**最长前缀优先**没有意义（各前缀互不重叠），
#: 但必须防止 "30" 这类过短前缀误匹配——所以整表只用完整的 3 位/2 位前缀。
_PREFIX_BOARD: tuple[tuple[str, Board], ...] = (
    ("605", Board.SH_MAIN),
    ("603", Board.SH_MAIN),
    ("601", Board.SH_MAIN),
    ("600", Board.SH_MAIN),
    ("301", Board.CHINEXT),
    ("300", Board.CHINEXT),
    ("689", Board.STAR),
    ("688", Board.STAR),
    ("003", Board.SZ_MAIN),
    ("002", Board.SZ_MAIN),
    ("001", Board.SZ_MAIN),
    ("000", Board.SZ_MAIN),
    ("920", Board.BJ),
    ("87", Board.BJ),
    ("83", Board.BJ),
    ("43", Board.BJ),
)

BOARD_LABELS: dict[Board, str] = {
    Board.SH_MAIN: "沪市主板",
    Board.SZ_MAIN: "深市主板",
    Board.CHINEXT: "创业板",
    Board.STAR: "科创板",
    Board.BJ: "北交所",
}


def board_of(code: str) -> Board:
    """6 位代码 → 板块。未知前缀抛错（失败关闭）。"""
    if not code or not code.isdigit() or len(code) != 6:
        raise ValueError(f"code 必须是 6 位数字，收到 {code!r}")
    for prefix, board in _PREFIX_BOARD:
        if code.startswith(prefix):
            return board
    raise ValueError(f"未知板块前缀：{code}（不猜，停下问）")


@dataclass(frozen=True)
class BoardPermissions:
    """账户的板块交易权限。**只约束买入**；卖出永不拦截。"""

    allowed: frozenset[Board]
    #: 为什么是这套权限（进台账/审计日志，不进代码注释——配置会变，理由要跟着走）
    source: str = ""

    def __post_init__(self) -> None:
        for b in self.allowed:
            if not isinstance(b, Board):
                raise ValueError(f"allowed 里混入了非 Board 值：{b!r}")

    @classmethod
    def from_config(cls, boards, source: str = "") -> "BoardPermissions":
        """从配置名构造。未知名字抛错——失败关闭，不做"忽略未知项"的宽容。"""
        names = list(boards)
        unknown = [b for b in names if b not in Board._value2member_map_]
        if unknown:
            raise ValueError(f"未知板块名：{unknown}（合法值：{[b.value for b in Board]}）")
        return cls(allowed=frozenset(Board(b) for b in names), source=source)

    def allows(self, code: str) -> bool:
        return board_of(code) in self.allowed

    def reason_if_denied(self, code: str) -> str:
        b = board_of(code)
        return (f"无{BOARD_LABELS[b]}交易权限（board={b.value}；"
                f"允许={sorted(x.value for x in self.allowed)}；source={self.source or '未注明'}）")

    def describe(self) -> str:
        return ("允许：" + (", ".join(BOARD_LABELS[b] for b in sorted(self.allowed,
                  key=lambda x: list(Board))) or "（无）"))


def require_checked(result) -> None:
    """发单入口的守卫：规划结果必须显式过权限闸。

    ``PlanResult.permission_unchecked`` 为 ``True`` 说明规划时没传权限——
    Phase 3 把订单送进券商适配层之前必须断言它为 ``False``，
    否则停（宁可不出单，也不发一笔"不知道能不能买"的单）。
    """
    if getattr(result, "permission_unchecked", False):
        raise RuntimeError(
            "规划结果未通过板块权限检查（permissions=None）。"
            "Phase 3 发单前必须显式传入 BoardPermissions——不传就停。")


def substitute_targets(ranked: Sequence[str], permissions: BoardPermissions,
                       n: int = 5) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """按排名顺延补足：跳过无权限标的，取前 ``n`` 个**可投**标的。

    ⚠️ **这不是 U-2b 的主规则，是第二道防线。** 主规则（2026-11-03 用户拍板）：
    实盘目标口径采用 **d4 严格（沪深主板）**——在目标生成层（p13_forward 的
    ``build_d4_targets``/``BOARD_D4_STRICT``）就把创业板和科创板排除在池外，
    台账 ``target_holds_d4`` 自动记录。本函数只兜底：即便上游漏了、名单里
    混进无权限板块，也在这里拦下并可投不足时**失败关闭**（宁可缺位也不放宽
    权限——放宽权限是账户决定，不是执行层决定）。

    引用边界（必带）：历史回测（sp4-v1 等）的股票池**包含**创业板与科创板——
    d4 严格口径下的组合收益/回撤**没有回测背书**，不得引用 sp4-v1 的数字描述它；
    要量化 d4 口径的表现必须走新预登记（换池 = 新实验）。

    权重不变：仍是每只 ``target_weight``。返回 (选中, 被跳过)，被跳过的进台账。

    >>> p = BoardPermissions.from_config(["sh_main", "sz_main"])
    >>> substitute_targets(["600990", "301329", "600191", "300001", "002012", "603977"], p)
    (('600990', '600191', '002012', '603977'), ('301329', '300001'))
    """
    if n <= 0:
        raise ValueError(f"n 必须为正，收到 {n}")
    picked: list[str] = []
    skipped: list[str] = []
    for code in ranked:
        if len(picked) >= n:
            break
        if permissions.allows(code):
            picked.append(code)
        else:
            skipped.append(code)
    if len(picked) < n:
        raise RuntimeError(
            f"可投标的不足：排名表 {len(ranked)} 只中只有 {len(picked)} 只可投"
            f"（目标 {n} 只，被权限拦下 {len(skipped)} 只）。"
            "宁可缺位也不放宽权限——放宽权限是账户决定，不是执行层决定。")
    return tuple(picked), tuple(skipped)
