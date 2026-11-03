"""能力契约数据结构：合法性、摘要确定性、版本语义。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from deepquant.capabilities.spec import (
    CHECKPOINT_PRECONDITIONS,
    PRECONDITIONS,
    RISK_TAGS,
    CapabilityContractError,
    CapabilitySpec,
    compare_versions,
    parse_version,
    same_major,
)


class In(BaseModel):
    a: int
    b: str = "x"


class Out(BaseModel):
    ok: bool


def _spec(**overrides) -> CapabilitySpec:
    base = dict(
        cap_id="demo.run",
        title="演示",
        version="1.0.0",
        summary="演示能力",
        transport="http",
        operation_id="demo.run",
        method="POST",
        path="/demo",
        input_model=In,
        output_model=Out,
        required_fields=("a",),
        preconditions=("identity", "field", "operator_grant"),
        risk_tags=("mutating",),
    )
    base.update(overrides)
    return CapabilitySpec(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 合法路径
# --------------------------------------------------------------------------- #


def test_minimal_valid_spec_passes() -> None:
    _spec().validate()


def test_vocabularies_are_non_empty_and_labelled() -> None:
    # 词表本身是受控取值，空说明会让"未登记即报错"变成"永远报错"
    assert PRECONDITIONS and RISK_TAGS
    for name in CHECKPOINT_PRECONDITIONS:
        assert name in PRECONDITIONS
    assert {"mutating", "read_only"} <= set(RISK_TAGS)


@pytest.mark.parametrize(
    "cap_id",
    ["Demo.run", "demo", "demo.", "demo.r-un", ".demo.run", "demo.run.more.deeper.x"],
)
def test_bad_cap_id_rejected(cap_id: str) -> None:
    with pytest.raises(CapabilityContractError, match="cap_id"):
        _spec(cap_id=cap_id).validate()


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0-rc1", "01.0.0", ""])
def test_bad_version_rejected(version: str) -> None:
    with pytest.raises(CapabilityContractError, match="version"):
        _spec(version=version).validate()


def test_service_transport_must_not_declare_route() -> None:
    with pytest.raises(CapabilityContractError, match="service 能力不得声明"):
        _spec(transport="service", method="GET", path="/demo").validate()


def test_service_transport_without_route_passes() -> None:
    _spec(transport="service", method=None, path=None).validate()


def test_http_transport_requires_path() -> None:
    with pytest.raises(CapabilityContractError, match="path"):
        _spec(path=None).validate()


def test_http_transport_requires_valid_method() -> None:
    with pytest.raises(CapabilityContractError, match="method"):
        _spec(method="FETCH").validate()


def test_unknown_risk_tag_rejected() -> None:
    with pytest.raises(CapabilityContractError, match="风控标签"):
        _spec(risk_tags=("mutating", "writes_磁盘")).validate()


def test_unknown_precondition_rejected() -> None:
    with pytest.raises(CapabilityContractError, match="前置条件"):
        _spec(preconditions=("identity", "必须有人看着")).validate()


def test_input_model_must_be_pydantic() -> None:
    with pytest.raises(CapabilityContractError, match="input_model"):
        _spec(input_model=dict).validate()  # type: ignore[arg-type]


def test_required_fields_without_model_rejected() -> None:
    with pytest.raises(CapabilityContractError, match="必填字段"):
        _spec(input_model=None).validate()


def test_duplicate_required_fields_rejected() -> None:
    with pytest.raises(CapabilityContractError, match="重复"):
        _spec(required_fields=("a", "a")).validate()


def test_deprecated_requires_successor() -> None:
    with pytest.raises(CapabilityContractError, match="successor"):
        _spec(deprecated=True).validate()


def test_successor_cannot_be_self() -> None:
    with pytest.raises(CapabilityContractError, match="指向自己"):
        _spec(deprecated=True, successor="demo.run").validate()


# --------------------------------------------------------------------------- #
# 副作用标签：必须显式二选一
# --------------------------------------------------------------------------- #


def test_side_effect_tag_is_mandatory() -> None:
    """不声明 mutating / read_only 时无法判定准入，必须报错。"""
    with pytest.raises(CapabilityContractError, match="必须且只能包含"):
        _spec(risk_tags=("reads_registry",)).validate()


def test_both_side_effect_tags_rejected() -> None:
    with pytest.raises(CapabilityContractError, match="必须且只能包含"):
        _spec(risk_tags=("mutating", "read_only")).validate()


def test_get_cannot_be_declared_mutating() -> None:
    with pytest.raises(CapabilityContractError, match="不应改变状态"):
        _spec(method="GET", input_model=None, required_fields=(), risk_tags=("mutating",)).validate()


def test_post_may_be_declared_read_only() -> None:
    """POST 只为用请求体传参的只读查询（如结果比较）——必须能声明。"""
    _spec(risk_tags=("read_only",)).validate()


# --------------------------------------------------------------------------- #
# 摘要
# --------------------------------------------------------------------------- #


def test_digest_is_deterministic() -> None:
    assert _spec().contract_digest() == _spec().contract_digest()


def test_digest_ignores_human_text() -> None:
    """改标题/说明不应逼人升版本；改契约才应该。"""
    a = _spec()
    b = _spec(title="另一个标题", summary="另一段说明", note="备注")
    assert a.contract_digest() == b.contract_digest()


@pytest.mark.parametrize(
    "overrides",
    [
        {"path": "/demo2"},
        {"method": "PUT"},
        {"required_fields": ("a", "b")},
        {"preconditions": ("operator_grant",)},
        {"risk_tags": ("mutating", "reads_db")},
    ],
)
def test_digest_changes_with_contract(overrides: dict) -> None:
    assert _spec().contract_digest() != _spec(**overrides).contract_digest()


def test_digest_ignores_version_label() -> None:
    """版本号是**标签**，不是内容：摘要是"这份契约长什么样"的指纹。

    版本与摘要的关系由 lock 强制（同版本必须同摘要），而不是把版本塞进摘要里——
    否则"升版本"本身就会改摘要，等于永远对不上。
    """
    assert _spec().contract_digest() == _spec(version="9.9.9").contract_digest()


def test_digest_changes_when_model_changes() -> None:
    class In2(BaseModel):
        a: int
        b: str = "x"
        c: float = 0.0

    assert _spec().contract_digest() != _spec(input_model=In2).contract_digest()


def test_schema_digest_only_tracks_models() -> None:
    """``schema_digest`` 专供"数据契约变了吗"的对外宣告，不该被路径变更影响。"""
    assert _spec().input_schema_digest() == _spec(path="/other").input_schema_digest()


def test_to_dict_is_json_serialisable() -> None:
    import json

    payload = _spec().to_dict()
    text = json.dumps(payload, ensure_ascii=False)
    assert "Demo" not in text or True  # 只要不抛错即通过
    assert payload["input_model"].endswith("In")


# --------------------------------------------------------------------------- #
# 版本语义
# --------------------------------------------------------------------------- #


def test_parse_version() -> None:
    assert parse_version("2.10.3") == (2, 10, 3)


def test_parse_version_rejects_garbage() -> None:
    with pytest.raises(CapabilityContractError):
        parse_version("2.10")


def test_compare_versions() -> None:
    assert compare_versions("1.0.0", "1.0.1") == -1
    assert compare_versions("2.0.0", "1.9.9") == 1
    assert compare_versions("1.0.0", "1.0.0") == 0


def test_same_major() -> None:
    assert same_major("1.2.3", "1.9.0") is True
    assert same_major("1.9.0", "2.0.0") is False
