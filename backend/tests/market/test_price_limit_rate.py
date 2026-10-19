"""涨跌幅比例解析测试（B18 / D2 修复的纯函数部分）。

被锁定的三件事：
1. 板块差异必须按代码前缀解析，不能一律 10%（会虚构创业板/科创板的涨停）；
2. ``off`` 必须返回 None 而不是 0——0 会让「±0%」被算成每天都封板；
3. 非法 mode / 非法代码必须报错，不能静默退化成某个默认值。
"""

from __future__ import annotations

import pytest

from deepquant.market.rules import (
    PRICE_LIMIT_20PCT_PREFIXES,
    PRICE_LIMIT_MODES,
    a_share_price_limit_rate,
)


@pytest.mark.parametrize(
    "code",
    ["600519.SH", "000001.SZ", "601318.SH", "600900.SH", "002594.SZ"],
)
def test_main_board_is_ten_percent(code: str) -> None:
    assert a_share_price_limit_rate(code) == 0.10


@pytest.mark.parametrize(
    "code",
    ["300750.SZ", "301029.SZ", "688111.SH", "689009.SH"],
)
def test_growth_and_star_board_is_twenty_percent(code: str) -> None:
    assert a_share_price_limit_rate(code) == 0.20


def test_prefixes_constant_covers_growth_and_star() -> None:
    """常量本身要被锁住：删掉 301 或 688 会让对应板块静默退回 10%。"""
    assert set(PRICE_LIMIT_20PCT_PREFIXES) == {"300", "301", "688", "689"}


def test_flat_mode_forces_ten_percent() -> None:
    assert a_share_price_limit_rate("300750.SZ", mode="flat") == 0.10


def test_off_mode_returns_none_not_zero() -> None:
    """off 必须是 None（不判定），不能是 0.0（每天封板）。"""
    assert a_share_price_limit_rate("300750.SZ", mode="off") is None


def test_modes_constant_matches_supported() -> None:
    assert set(PRICE_LIMIT_MODES) == {"board", "flat", "off"}


def test_accepts_bare_six_digit_code() -> None:
    assert a_share_price_limit_rate("300750") == 0.20
    assert a_share_price_limit_rate("600519") == 0.10


def test_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        a_share_price_limit_rate("600519.SH", mode="whatever")


def test_rejects_unparseable_code() -> None:
    with pytest.raises(ValueError, match="6 位代码"):
        a_share_price_limit_rate("60051.SH")
    with pytest.raises(ValueError, match="6 位代码"):
        a_share_price_limit_rate("ABCDEF.SH")
