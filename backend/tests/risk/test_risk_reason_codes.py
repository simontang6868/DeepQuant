"""原因码表测试：可枚举、可检索、无临时码。

《推进计划》T2 的验收标准之一是「每次拒绝均有原因码且可在工件中检索」。
这条只有在原因码是**受控常量**时才能成立：随手拼的字符串无法统计、无法分流、
也无法在 T1 工件里做等值检索。本文件钉住这个前提。
"""

from __future__ import annotations

import pytest

from deepquant.risk.reason_codes import (
    CATEGORY_INTERNAL,
    REASON_CODES,
    REQUIRED_CATEGORIES,
    assert_codes_complete,
    coverage,
    reason,
)


def test_all_required_categories_have_codes() -> None:
    """四类检查点每类至少一个码——这是「覆盖率 100%」的分母。"""
    report = coverage()
    assert report["missing_categories"] == []
    assert report["ratio"] == 1.0
    for category in REQUIRED_CATEGORIES:
        assert report["by_category"][category], f"类别 {category} 没有任何原因码"


def test_codes_are_unique_and_consistent() -> None:
    assert len(REASON_CODES) == len(set(REASON_CODES))
    for key, item in REASON_CODES.items():
        assert key == item.code


def test_every_code_has_valid_status_and_summary() -> None:
    for item in REASON_CODES.values():
        assert 400 <= item.status_code < 600, item.code
        assert item.summary.strip(), item.code


def test_internal_category_exists_for_failures() -> None:
    """网关自身故障与「无法校验」必须有码，否则失败无法归因。"""
    codes = [c for c in REASON_CODES.values() if c.category == CATEGORY_INTERNAL]
    assert {c.code for c in codes} >= {"checkpoint_unverifiable", "gateway_internal_error"}


def test_unknown_code_raises() -> None:
    """禁止临时造码：拼错即报错，而不是造出一个查不到原因的拒绝。"""
    with pytest.raises(KeyError, match="未登记的原因码"):
        reason("some_ad_hoc_code")


def test_self_check_passes() -> None:
    assert_codes_complete()


def test_status_codes_follow_documented_convention() -> None:
    """身份/状态类 403/409，字段/限额类 422，体积 413，无法校验 503。

    口径固定下来，前端才能按状态码分流；改口径要同时改本测试与文档。
    """
    assert reason("identity_missing").status_code == 403
    assert reason("identity_not_allowed").status_code == 403
    assert reason("state_dataset_unavailable").status_code == 409
    assert reason("field_missing").status_code == 422
    assert reason("field_empty").status_code == 422
    assert reason("limit_cash_range").status_code == 422
    assert reason("limit_body_too_large").status_code == 413
    assert reason("checkpoint_unverifiable").status_code == 503
