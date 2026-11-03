"""板块权限闸（board.py + planner 集成）的行为测试。

背景：2026-11-03 用户确认**账户没有创业板交易权限**。目标持仓出现 300/301 时，
买入必须被拦下并记 skipped（带板块名），**不得**静默生成买单、也**不得**补位
买下一只——补位就是改组合，那是策略变更，不是执行细节。

本文件钉死的行为（每条对应一种经典错误形态）：
1. 前缀表：300/301→创业板，688/689→科创板，000/001/002/003→深主板，
   600/601/603/605→沪主板，43/83/87/920→北交所；未知前缀抛错（不猜）。
2. from_config 未知板块名抛错；空列表 = 什么都不许买（失败关闭，不是"全放行"）。
3. planner：被拦标的**不占预算**（下一只拿到完整权重）；已持仓的卖出不受限；
   permissions=None 时行为与旧版一致但 permission_unchecked=True。
4. require_checked：permission_unchecked=True 的结果必须被发单入口拒绝。
"""

from __future__ import annotations

import pytest

from deepquant.broker.models import AccountSnapshot, Cash, Position, Quote
from deepquant.execution.board import (
    BOARD_LABELS,
    Board,
    BoardPermissions,
    board_of,
    require_checked,
    substitute_targets,
)
from deepquant.execution.planner import PlanInput, plan_orders
from deepquant.market.rules import Side

DEC = "20261030"


def _q(code: str, last: float) -> Quote:
    return Quote(code=code, last=last, ts=20261030, stale=False)


def _snap(cash: float, positions: list[Position] | None = None) -> AccountSnapshot:
    return AccountSnapshot(
        cash=Cash(available=cash, frozen=0.0, total=cash),
        positions=tuple(positions or ()),
        ts=20261030,
    )


# ---------------------------------------------------------------------------
# 1. 前缀表
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,board", [
    ("600000", Board.SH_MAIN), ("601398", Board.SH_MAIN),
    ("603259", Board.SH_MAIN), ("605111", Board.SH_MAIN),
    ("000001", Board.SZ_MAIN), ("001979", Board.SZ_MAIN),
    ("002594", Board.SZ_MAIN), ("003816", Board.SZ_MAIN),
    ("300750", Board.CHINEXT), ("301269", Board.CHINEXT),
    ("688981", Board.STAR), ("689009", Board.STAR),
    ("832566", Board.BJ), ("833171", Board.BJ),
    ("870357", Board.BJ), ("920002", Board.BJ),
])
def test_board_of_prefix_table(code, board):
    assert board_of(code) is board


@pytest.mark.parametrize("code", ["", "30075", "3007500", "30075X", "30a750"])
def test_board_of_rejects_malformed(code):
    with pytest.raises(ValueError):
        board_of(code)


def test_board_of_rejects_unknown_prefix():
    # "20" 开头不存在于 A 股：宁可抛错也不猜
    with pytest.raises(ValueError, match="未知板块前缀"):
        board_of("200001")


def test_prefix_table_prefixes_are_exact():
    # "30" 这种短前缀会同时吞掉创业板之外的东西吗？表里必须是 3 位或明示的 2 位，
    # 且没有任何前缀是另一前缀的前缀混淆源（30x 全覆盖创业板）。
    prefixes = [p for p, _ in __import__(
        "deepquant.execution.board", fromlist=["_PREFIX_BOARD"])._PREFIX_BOARD]
    assert "30" not in prefixes
    assert "68" not in prefixes          # 688/689 之外还有 680-687 段？不存在，但禁止短前缀兜底
    assert "00" not in prefixes          # 同理禁止 "00" 兜底


# ---------------------------------------------------------------------------
# 2. from_config：失败关闭
# ---------------------------------------------------------------------------
def test_from_config_accepts_known_names():
    p = BoardPermissions.from_config(["sh_main", "sz_main"],
                                     source="账户确认 2026-11-03")
    assert p.allows("600000") and p.allows("000001")
    assert not p.allows("300750") and not p.allows("688981")


def test_from_config_rejects_unknown_name():
    # "gem" 之类的别名不得被宽容忽略——忽略 = 实际权限与配置认知不一致
    with pytest.raises(ValueError, match="未知板块名"):
        BoardPermissions.from_config(["sh_main", "gem"])


def test_empty_config_allows_nothing():
    """空列表 = 什么都不许买。'没配置'绝不能等价于'全部允许'。"""
    p = BoardPermissions.from_config([])
    for code in ("600000", "000001", "300750", "688981"):
        assert not p.allows(code)


def test_describe_lists_chinese_labels():
    p = BoardPermissions.from_config(["sh_main", "sz_main"])
    text = p.describe()
    assert BOARD_LABELS[Board.SH_MAIN] in text and BOARD_LABELS[Board.SZ_MAIN] in text


# ---------------------------------------------------------------------------
# 3. planner 集成
# ---------------------------------------------------------------------------
def _perm_chinext_denied() -> BoardPermissions:
    return BoardPermissions.from_config(
        ["sh_main", "sz_main"], source="用户确认无创业板权限 2026-11-03")


def test_denied_target_is_skipped_with_reason():
    codes = ["600001", "300001", "600002"]
    quotes = {c: _q(c, 10.0) for c in codes}
    r = plan_orders(PlanInput(
        decision=DEC, targets=codes, snapshot=_snap(900_000.0), quotes=quotes,
        target_weight=1 / 3, permissions=_perm_chinext_denied(),
    ))
    assert [o.code for o in r.buys] == ["600001", "600002"], "创业板不得出现在买单里"
    skipped = {s.code: s for s in r.skipped}
    assert "300001" in skipped
    assert "创业板" in skipped["300001"].reason
    assert skipped["300001"].side is Side.BUY
    # 不补位：被拦标的后面不会多出第 4 只
    assert len(r.buys) == 2


def test_denied_target_consumes_no_budget():
    """被拦标的不得吃掉预算：后两只各拿 1/3 权重，股数与无拦截时一致。"""
    codes_a = ["600001", "600002", "600003"]
    codes_b = ["600001", "300001", "600002", "600003"]
    quotes = {c: _q(c, 10.0) for c in set(codes_a + codes_b)}
    ref = plan_orders(PlanInput(
        decision=DEC, targets=codes_a, snapshot=_snap(900_000.0), quotes=quotes,
        target_weight=1 / 3, permissions=_perm_chinext_denied(),
    ))
    got = plan_orders(PlanInput(
        decision=DEC, targets=codes_b, snapshot=_snap(900_000.0), quotes=quotes,
        target_weight=1 / 3, permissions=_perm_chinext_denied(),
    ))
    ref_shares = {o.code: o.shares for o in ref.buys}
    got_shares = {o.code: o.shares for o in got.buys}
    assert got_shares == ref_shares, "拦截不得改变其他标的的目标股数（不占预算）"


def test_sell_path_never_blocked_by_permissions():
    """持仓的卖出不受权限闸限制——退出持仓必须永远可行。"""
    pos = Position(code="300001", quantity=1_000, available=1_000)
    quotes = {"300001": _q("300001", 10.0)}
    r = plan_orders(PlanInput(
        decision=DEC, targets=[], snapshot=_snap(0.0, [pos]), quotes=quotes,
        target_weight=0.2, permissions=BoardPermissions.from_config([]),
    ))
    assert [o.code for o in r.sells] == ["300001"], "空 allowed 也必须能清仓"


def test_none_permissions_keeps_legacy_behaviour_but_flags_unchecked():
    """permissions=None：旧行为保留（生成买单），但必须带 unchecked 标记。"""
    quotes = {"300001": _q("300001", 10.0)}
    r = plan_orders(PlanInput(
        decision=DEC, targets=["300001"], snapshot=_snap(1_000.0), quotes=quotes,
        target_weight=1.0,
    ))
    assert [o.code for o in r.buys] == ["300001"]
    assert r.permission_unchecked is True


def test_checked_result_has_flag_false():
    quotes = {"600001": _q("600001", 10.0), "300001": _q("300001", 10.0)}
    r = plan_orders(PlanInput(
        decision=DEC, targets=["600001", "300001"], snapshot=_snap(2_000.0),
        quotes=quotes, target_weight=0.5, permissions=_perm_chinext_denied(),
    ))
    assert r.permission_unchecked is False


# ---------------------------------------------------------------------------
# 4. require_checked：发单入口守卫
# ---------------------------------------------------------------------------
def test_require_checked_rejects_unchecked():
    quotes = {"600001": _q("600001", 10.0)}
    r = plan_orders(PlanInput(
        decision=DEC, targets=["600001"], snapshot=_snap(1_000.0), quotes=quotes,
        target_weight=1.0,            # 不传 permissions
    ))
    with pytest.raises(RuntimeError, match="权限"):
        require_checked(r)


def test_require_checked_accepts_checked():
    quotes = {"600001": _q("600001", 10.0)}
    r = plan_orders(PlanInput(
        decision=DEC, targets=["600001"], snapshot=_snap(1_000.0), quotes=quotes,
        target_weight=1.0, permissions=_perm_chinext_denied(),
    ))
    require_checked(r)  # 不抛


# ---------------------------------------------------------------------------
# 5. substitute_targets：U-2b 顺延补足规则
# ---------------------------------------------------------------------------
def test_substitute_skips_denied_and_extends():
    """无权限标的被跳过，按原排名顺延补足到 5 只。"""
    ranked = ["600001", "300001", "000001", "301001", "600002", "600003", "600004"]
    picked, skipped = substitute_targets(ranked, _perm_chinext_denied())
    assert picked == ("600001", "000001", "600002", "600003", "600004")
    assert skipped == ("300001", "301001")


def test_substitute_keeps_rank_order():
    """顺序必须保持原排名，不得按板块重排。"""
    ranked = ["600005", "600001", "600002", "600003", "600004"]
    picked, _ = substitute_targets(ranked, _perm_chinext_denied())
    assert picked == tuple(ranked)


def test_substitute_insufficient_raises():
    """可投标的不足：宁可缺位也不放宽权限——放宽是账户决定不是执行层决定。"""
    ranked = ["600001", "300001", "300002"]
    with pytest.raises(RuntimeError, match="可投标的不足"):
        substitute_targets(ranked, _perm_chinext_denied())


def test_substitute_zero_n_raises():
    with pytest.raises(ValueError, match="n 必须为正"):
        substitute_targets(["600001"], _perm_chinext_denied(), n=0)


def test_substitute_exactly_n_ok():
    ranked = ["600001", "300001", "600002", "600003", "600004", "600005"]
    picked, skipped = substitute_targets(ranked, _perm_chinext_denied())
    assert picked == ("600001", "600002", "600003", "600004", "600005")
    assert skipped == ("300001",)
