"""审计工件（run card）结构与摘要测试。

覆盖《推进计划》T1 的两条硬性要求：
- 五元组完整率 100%：任一字段缺失/为空即报错，不允许用空值顶替；
- 不存储敏感凭证明文：脱敏在构造阶段完成，只留字段路径。
另覆盖摘要的确定性（同内容同摘要、改一字节即变）与五元组完整率统计口径。
"""

from __future__ import annotations

import json

import pytest

from deepquant.audit.run_card import (
    CARD_VERSION,
    GENESIS_SHA256,
    REDACTED,
    AuditSchemaError,
    build_card,
    card_digest,
    completeness,
    redact,
    validate_card,
    verify_card,
)

OCCURRED_AT = "2026-10-27T02:00:00.000000+00:00"


def _card(**overrides):
    kwargs = dict(
        action="backtest.run",
        operator="ops@example",
        occurred_at=OCCURRED_AT,
        basis={"rule": "冻结配置 sp4_config_v1", "params": {"fast": 5, "slow": 20}},
        outcome={"status": "ok", "status_code": 200},
        caveats=["仅覆盖日频，不含盘中执行"],
        subject={"symbol": "TEST01"},
        inputs={"dataset_digest": "sha256:abc"},
        env={"package_version": "0.0.1"},
        seq=1,
    )
    kwargs.update(overrides)
    return build_card(**kwargs)


# --------------------------------------------------------------------------- #
# 五元组必填
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["operator", "occurred_at", "basis", "outcome", "caveats"])
def test_missing_required_field_raises(field: str) -> None:
    """五元组缺任一项即报错——不允许静默补空值。"""
    card = _card()
    card.pop(field)
    with pytest.raises(AuditSchemaError, match="必需字段"):
        validate_card(card)


def test_none_required_field_raises() -> None:
    """字段存在但为 None 视为缺失（`None` 与「空」不同，不能当已记录）。"""
    card = _card()
    card["basis"] = None
    with pytest.raises(AuditSchemaError, match="必需字段"):
        validate_card(card)


def test_empty_operator_raises() -> None:
    with pytest.raises(AuditSchemaError, match="operator"):
        _card(operator="   ")


def test_caveats_must_be_list_not_string() -> None:
    """`caveats` 传字符串是最常见的误用，必须报错而不是当成单条。"""
    with pytest.raises(AuditSchemaError, match="caveats"):
        _card(caveats="仅覆盖日频")


def test_caveats_empty_list_allowed() -> None:
    """确实没有已知限制时允许空列表：与「没写」区分开。"""
    card = _card(caveats=[])
    assert card["caveats"] == []


def test_caveats_element_must_be_non_empty() -> None:
    with pytest.raises(AuditSchemaError, match="caveats"):
        _card(caveats=["ok", "   "])


def test_invalid_occurred_at_raises() -> None:
    with pytest.raises(AuditSchemaError, match="occurred_at"):
        _card(occurred_at="2026/10/27 10:00")


def test_basis_must_be_mapping() -> None:
    with pytest.raises(AuditSchemaError, match="basis"):
        _card(basis=["rule"])


# --------------------------------------------------------------------------- #
# 摘要确定性
# --------------------------------------------------------------------------- #
def test_same_content_same_digest() -> None:
    """同内容两次构造摘要一致（键顺序不影响）。"""
    a = build_card(
        action="backtest.run",
        operator="ops",
        occurred_at=OCCURRED_AT,
        basis={"b": 2, "a": 1},
        outcome={"status": "ok"},
        caveats=[],
        seq=1,
    )
    b = build_card(
        action="backtest.run",
        operator="ops",
        occurred_at=OCCURRED_AT,
        basis={"a": 1, "b": 2},
        outcome={"status": "ok"},
        caveats=[],
        seq=1,
    )
    assert a["card_sha256"] == b["card_sha256"]


def test_changing_one_field_changes_digest() -> None:
    a = _card()
    b = _card(outcome={"status": "rejected", "status_code": 400})
    assert a["card_sha256"] != b["card_sha256"]


def test_card_digest_excludes_self_field() -> None:
    """摘要必须排除 `card_sha256` 自身，否则无法自洽。"""
    card = _card()
    assert card_digest(card) == card["card_sha256"]
    assert verify_card(card)[0] is True


def test_verify_detects_field_tampering() -> None:
    card = _card()
    card["operator"] = "someone-else"
    ok, message = verify_card(card)
    assert ok is False
    assert "摘要不一致" in message


def test_card_version_and_genesis() -> None:
    card = _card()
    assert card["card_version"] == CARD_VERSION
    assert card["prev_sha256"] == GENESIS_SHA256
    assert len(card["card_sha256"]) == 64


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #
def test_redact_by_key_name() -> None:
    payload = {
        "user": "ops",
        "password": "P@ssw0rd!",
        "api_key": "abcd1234",
        "nested": {"session_id": "s-1", "phone": "13800000000"},
        "list": [{"access_token": "tok-value-xyz"}],
    }
    cleaned, paths = redact(payload)
    text = json.dumps(cleaned, ensure_ascii=False)
    # 逐个比对**值**：不能只做子串搜索——键名本身含 "token"/"tok"，会把键名当泄漏。
    for secret in ("P@ssw0rd!", "abcd1234", "s-1", "13800000000", "tok-value-xyz"):
        assert f'"{secret}"' not in text
    assert cleaned["user"] == "ops"  # 非敏感字段保留
    assert cleaned["password"] == REDACTED
    assert cleaned["nested"]["phone"] == REDACTED
    assert cleaned["list"][0]["access_token"] == REDACTED
    assert set(paths) >= {"password", "api_key", "nested.session_id", "nested.phone", "list[0].access_token"}


def test_redact_by_value_prefix() -> None:
    cleaned, paths = redact({"header": "Bearer abc.def.ghi"})
    assert cleaned["header"] == REDACTED
    assert paths == ["header"]


def test_build_card_redacts_and_records_paths() -> None:
    """令牌误入 basis 时：落盘无明文，且 caveat 说明脱敏过（否则占位符无法解释）。"""
    card = _card(basis={"rule": "x", "token": "super-secret-token"})
    serialized = json.dumps(card, ensure_ascii=False)
    assert "super-secret-token" not in serialized
    assert card["basis"]["token"] == REDACTED
    assert any("脱敏" in c and "basis.token" in c for c in card["caveats"])


# --------------------------------------------------------------------------- #
# 完整率统计
# --------------------------------------------------------------------------- #
def test_completeness_counts_cards_not_fields() -> None:
    complete_card = _card()
    broken = json.loads(json.dumps(complete_card))
    broken.pop("outcome")
    report = completeness([complete_card, broken])
    assert report.n_cards == 2
    assert report.n_complete == 1
    assert report.ratio == 0.5
    assert report.incomplete[0]["missing"] == ["outcome"]


def test_completeness_empty_is_full_ratio() -> None:
    assert completeness([]).ratio == 1.0
