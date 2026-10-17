"""序列化两模块的**契约与不变量**测试（A12，纯加法）。

与「分支覆盖」视角互补：这里不追求逐行命中，而是把模块的**对外承诺**钉死 ——
一旦实现悄悄改了摘要格式 / 破坏了确定性 / 开始修改入参 / 让 Decimal 有损，
这些测试会立刻失败。

覆盖两个模块：
- ``performance/json_encoding.py``（JSON 安全编码的地基）
- ``performance/reproducibility.py``（复现性的数据集摘要与策略元数据）

红线相关性：本模块是「结果可序列化 / 输入可识别 / 过程可复现」的地基。
测试只验证机械转换的正确性，不含任何参数选择或研究判断（R2 / R11）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from decimal import Decimal
from enum import Enum

import numpy as np
import pandas as pd
import pytest

from deepquant.performance import reproducibility as rp
from deepquant.performance.json_encoding import (
    SerializationError,
    from_jsonable,
    to_jsonable,
)

# --------------------------------------------------------------------------
# json_encoding：标量契约
# --------------------------------------------------------------------------


def test_none_maps_to_json_null():
    assert to_jsonable(None) is None


@pytest.mark.parametrize(
    "value",
    [0, 1, -7, np.int8(3), np.int64(2**40), np.uint32(9)],
)
def test_integers_become_plain_python_int(value):
    out = to_jsonable(value)
    assert type(out) is int
    assert out == int(value)


@pytest.mark.parametrize("value", [0.5, -1.25, np.float32(2.5), np.float64(3.5)])
def test_floats_become_plain_python_float(value):
    out = to_jsonable(value)
    assert type(out) is float
    assert out == pytest.approx(float(value))


def test_bool_is_not_coerced_to_int():
    """bool 是 int 的子类，若判断顺序写错会得到 1/0 —— 语义会被悄悄改掉。"""
    assert to_jsonable(True) is True
    assert to_jsonable(False) is False
    assert to_jsonable({"flag": False}) == {"flag": False}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_float_is_rejected_with_path(bad):
    with pytest.raises(SerializationError) as exc:
        to_jsonable({"metrics": {"sharpe": bad}})
    assert "$.metrics.sharpe" in str(exc.value)


def test_string_passes_through_unchanged():
    assert to_jsonable("2024-01-01") == "2024-01-01"


# --------------------------------------------------------------------------
# json_encoding：Decimal 无损（复现性的核心，金额不能有二进制浮点误差）
# --------------------------------------------------------------------------


def test_decimal_survives_stdlib_json_roundtrip_exactly():
    """Decimal 必须走标签化表示并**无损**还原，不能退化成 float。"""
    original = Decimal("12345.678901234567890123456789")
    encoded = to_jsonable({"nav": original})

    # 标签化：不是裸字符串，也不是 float
    assert encoded == {"nav": {"__type__": "decimal", "value": str(original)}}

    # 严格 JSON：allow_nan=False 保证没掺 NaN/Infinity
    text = json.dumps(encoded, allow_nan=False)
    decoded = from_jsonable(json.loads(text))

    assert isinstance(decoded["nav"], Decimal)
    assert decoded["nav"] == original  # 精确相等，不是 approx


def test_decimal_inside_nested_containers_is_preserved():
    payload = {"rows": [{"fee": Decimal("0.0001")}, {"fee": Decimal("5")}]}
    decoded = from_jsonable(json.loads(json.dumps(to_jsonable(payload), allow_nan=False)))
    assert decoded["rows"][0]["fee"] == Decimal("0.0001")
    assert decoded["rows"][1]["fee"] == Decimal("5")


def test_dict_with_extra_keys_is_not_mistaken_for_decimal():
    """带 `__type__` 但结构不符的字典不得被误还原成 Decimal。"""
    payload = {"__type__": "decimal", "value": "1.5", "extra": 1}
    assert from_jsonable(payload) == payload
    assert not isinstance(from_jsonable(payload), Decimal)


def test_dict_with_decimal_type_but_only_value_is_decimal():
    payload = {"__type__": "decimal", "value": "2.25"}
    assert from_jsonable(payload) == Decimal("2.25")


# --------------------------------------------------------------------------
# json_encoding：日期 / 容器 / 错误路径
# --------------------------------------------------------------------------


def test_timestamp_becomes_timezone_naive_iso_string():
    assert to_jsonable(pd.Timestamp("2024-03-05 14:30:00")) == "2024-03-05T14:30:00"


def test_date_and_datetime_are_both_accepted():
    assert to_jsonable(pd.Timestamp("2024-03-05").date()) == "2024-03-05T00:00:00"


def test_tuple_becomes_list():
    assert to_jsonable((1, 2, 3)) == [1, 2, 3]


def test_ndarray_becomes_plain_list_of_python_scalars():
    out = to_jsonable(np.array([1, 2, 3], dtype="int64"))
    assert out == [1, 2, 3]
    assert all(type(v) is int for v in out)


def test_set_ordering_is_deterministic_across_calls():
    """集合无序，必须靠 repr 排序保证摘要可复现。"""
    payload = {"a", "b", "c", "10", 10}
    first = to_jsonable(payload)
    for _ in range(5):
        assert to_jsonable(payload) == first
    assert len(first) == len(payload)


def test_dataclass_field_order_is_preserved():
    @dataclasses.dataclass
    class Row:
        zeta: int
        alpha: int

    assert list(to_jsonable(Row(zeta=1, alpha=2)).keys()) == ["zeta", "alpha"]


def test_non_string_dict_key_is_rejected():
    with pytest.raises(SerializationError) as exc:
        to_jsonable({1: "x"})
    assert "字典键必须是字符串" in str(exc.value)


def test_unsupported_type_is_rejected_with_type_in_message():
    with pytest.raises(SerializationError) as exc:
        to_jsonable({"obj": object()})
    assert "$.obj" in str(exc.value)


def test_mixed_nested_structure_is_strict_json_serializable():
    """最关键的端到端保证：产出必须能被标准 JSON 解析器读取且无 NaN/Infinity。"""

    @dataclasses.dataclass
    class Inner:
        when: pd.Timestamp
        fee: Decimal

    payload = {
        "inner": Inner(pd.Timestamp("2024-01-02"), Decimal("0.5")),
        "series": np.array([1.5, 2.5]),
        "tags": {"b", "a"},
        "when": pd.Timestamp("2024-01-01"),
        "missing": None,
    }
    text = json.dumps(to_jsonable(payload), allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text
    assert json.loads(text)["when"] == "2024-01-01T00:00:00"


# --------------------------------------------------------------------------
# reproducibility：摘要格式可独立重算（最强契约）
# --------------------------------------------------------------------------


def _frame(closes: list[float], dates: list[str], *, shuffle: bool = False):
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "close": closes,
            "volume": [1_000.0] * len(closes),
        }
    )
    if shuffle:
        df = df.iloc[::-1].reset_index(drop=True)
    return df


def test_digest_matches_independently_recomputed_payload():
    """不信任实现：按文档手工拼出规范化文本，自己算 sha256 再比对。

    这条同时钉死：表头格式、日期格式、数值格式（repr(float)）、行拼接方式。
    实现若改任一处，摘要就会对不上。
    """
    df = _frame([100.0, 101.5], ["2024-01-02", "2024-01-01"])  # 故意乱序
    # 行序按日期升序重排，因此 01-01 对应 101.5、01-02 对应 100.0
    expected_lines = [
        "date|close|volume",  # date 在前，其余按固定行情序（close 在 volume 前）
        "2024-01-01|101.5|1000.0",
        "2024-01-02|100.0|1000.0",
    ]
    expected = "sha256:" + hashlib.sha256("\n".join(expected_lines).encode()).hexdigest()

    summary = rp.dataset_summary(df)
    assert summary["digest"] == expected


def test_summary_is_invariant_to_column_order():
    a = _frame([1.0, 2.0], ["2024-01-01", "2024-01-02"])
    b = a[["close", "date", "volume"]]  # 列顺序打乱
    assert rp.dataset_summary(a)["digest"] == rp.dataset_summary(b)["digest"]
    # 但 columns 字段本身是规范化后的顺序
    assert rp.dataset_summary(a)["columns"] == rp.dataset_summary(b)["columns"]


def test_summary_is_invariant_to_row_order():
    a = _frame([1.0, 2.0, 3.0], ["2024-01-01", "2024-01-02", "2024-01-03"])
    b = _frame([3.0, 2.0, 1.0], ["2024-01-03", "2024-01-02", "2024-01-01"])
    assert rp.dataset_summary(a)["digest"] == rp.dataset_summary(b)["digest"]


def test_summary_changes_when_content_changes():
    a = _frame([1.0, 2.0], ["2024-01-01", "2024-01-02"])
    b = _frame([1.0, 2.0000001], ["2024-01-01", "2024-01-02"])
    assert rp.dataset_summary(a)["digest"] != rp.dataset_summary(b)["digest"]


def test_summary_does_not_mutate_input():
    """排序只用于摘要，绝不能改动调用方的数据。"""
    df = _frame([3.0, 1.0, 2.0], ["2024-01-03", "2024-01-01", "2024-01-02"])
    before_columns = list(df.columns)
    before_dates = list(df["date"])
    before_closes = list(df["close"])

    rp.dataset_summary(df)

    assert list(df.columns) == before_columns
    assert list(df["date"]) == before_dates
    assert list(df["close"]) == before_closes


def test_date_column_and_datetime_index_are_equivalent():
    """同一份数据，日期放在列里 vs 放在索引里，必须得到同一个摘要。"""
    as_column = _frame([1.0, 2.0], ["2024-01-01", "2024-01-02"])
    as_index = as_column.set_index("date")

    assert rp.dataset_summary(as_column)["digest"] == rp.dataset_summary(as_index)["digest"]


def test_canonical_column_order_puts_price_columns_first_then_alpha():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01"]),
            "zeta": [1.0],
            "volume": [1.0],
            "alpha": [2.0],
            "close": [3.0],
        }
    )
    # 行情列按 CANONICAL_PRICE_COLUMNS 的固定次序（close 在 volume 之前），其余按字母序
    assert rp.dataset_summary(df)["columns"] == ["close", "volume", "alpha", "zeta"]


def test_missing_values_use_stable_nan_token():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "close": [1.0, float("nan")],
        }
    )
    lines = [
        "date|close",
        "2024-01-01|1.0",
        "2024-01-02|NaN",
    ]
    expected = "sha256:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()
    assert rp.dataset_summary(df)["digest"] == expected


def test_summary_metadata_fields():
    df = _frame([1.0, 2.0, 3.0], ["2024-01-01", "2024-01-02", "2024-01-03"])
    summary = rp.dataset_summary(df, dataset_id="my-dataset")

    assert summary["dataset_id"] == "my-dataset"
    assert summary["n_rows"] == 3
    assert summary["date_range"] == {"start": "2024-01-01", "end": "2024-01-03"}
    assert summary["digest_algorithm"] == "sha256"
    assert summary["digest"].startswith("sha256:")


def test_empty_frame_has_null_date_range():
    df = pd.DataFrame({"date": pd.to_datetime([]), "close": pd.Series(dtype="float64")})
    summary = rp.dataset_summary(df)
    assert summary["n_rows"] == 0
    assert summary["date_range"] is None


def test_canonicalization_doc_is_a_copy_not_the_module_constant():
    """返回的是副本，调用方改它不得污染模块级常量。"""
    df = _frame([1.0], ["2024-01-01"])
    summary = rp.dataset_summary(df)
    summary["canonicalization"]["scope"] = "tampered"
    assert rp.dataset_summary(df)["canonicalization"]["scope"] != "tampered"


def test_duplicate_dates_are_rejected():
    df = _frame([1.0, 2.0], ["2024-01-01", "2024-01-01"])
    with pytest.raises(ValueError) as exc:
        rp.dataset_summary(df)
    assert "重复日期" in str(exc.value)


def test_unparsable_dates_are_rejected():
    df = pd.DataFrame({"date": ["2024-01-01", "not-a-date"], "close": [1.0, 2.0]})
    with pytest.raises(ValueError) as exc:
        rp.dataset_summary(df)
    assert "无法解析的日期" in str(exc.value)


def test_non_dataframe_is_rejected():
    with pytest.raises(TypeError):
        rp.dataset_summary([1, 2, 3])  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# reproducibility：策略元数据契约
# --------------------------------------------------------------------------


class _Strategy:
    def __init__(self, name="s", version="1", params=None, describe=None):
        self.name = name
        self.version = version
        self._params = params
        if describe is not None:
            self.describe = describe

    def describe(self):
        return {"name": self.name, "version": self.version, "params": self._params}


def test_fully_identified_strategy_is_reproducible():
    meta = rp.capture_strategy_metadata(_Strategy(params={"n": 10}))
    assert meta["identified"] is True
    assert meta["params_available"] is True
    assert meta["reproducible"] is True
    assert meta["params"] == {"n": 10}
    assert meta["notes"] == []


class _AttrOnlyStrategy:
    """只提供属性、没有 ``describe()`` 的策略（用于隔离属性读取路径）。"""

    def __init__(self, name="s", version="1"):
        self.name = name
        self.version = version


def test_blank_name_is_dropped_when_read_from_attributes():
    """属性路径对空白 name/version 做了 strip 过滤 —— 不得被当成有效标识。"""
    meta = rp.capture_strategy_metadata(_AttrOnlyStrategy(name="   ", version="1"))
    assert meta["name"] is None
    assert meta["identified"] is False
    assert any("缺少策略名称或版本" in n for n in meta["notes"])


def test_blank_name_from_describe_is_kept_verbatim_recorded_not_fixed():
    """如实记录：``describe()`` 路径**没有**做同样的 strip 过滤。

    与上一条的差异：属性路径会把空白名判为缺失，``describe()`` 路径却原样采纳，
    于是 ``identified`` 为 True。两条路径行为不一致，属实现侧判断事项 ——
    **本测试只固化现状，不修改源码**（修 bug 属判断类，交回 B 线提案）。
    """
    meta = rp.capture_strategy_metadata(_Strategy(name="   ", version="1", params={}))
    assert meta["name"] == "   "
    assert meta["identified"] is True


def test_unserializable_params_are_dropped_not_silently_kept():
    strategy = _Strategy(params={"obj": object()})
    meta = rp.capture_strategy_metadata(strategy)
    assert meta["params"] is None
    assert meta["params_available"] is False
    assert any("不可序列化" in n for n in meta["notes"])


def test_describe_failure_is_recorded_and_falls_back_to_attributes():
    def boom():
        raise RuntimeError("boom")

    strategy = _Strategy(name="s", version="1", params={"n": 1})
    strategy.describe = boom

    meta = rp.capture_strategy_metadata(strategy)
    # 属性仍可用，标识不受影响
    assert meta["name"] == "s" and meta["version"] == "1"
    assert any("describe 失败" in n for n in meta["notes"])
    # 参数走不到（来自 describe），因此不可得
    assert meta["params"] is None


def test_strategy_without_any_metadata_is_not_identified():
    class Bare:
        pass

    meta = rp.capture_strategy_metadata(Bare())
    assert meta["identified"] is False
    assert meta["reproducible"] is False
    assert meta["params_available"] is False


# --------------------------------------------------------------------------
# reproducibility：综合判定契约（缺项不得宣称可复现）
# --------------------------------------------------------------------------

_FULL_DATASET = {"digest": "sha256:abc"}
_FULL_STRATEGY = {"identified": True, "params_available": True}
_FULL_CONFIG = {"initial_cash": 100000}


def test_all_present_means_process_reproducible():
    rep = rp.reproducibility_report(
        strategy_meta=_FULL_STRATEGY,
        dataset_meta=_FULL_DATASET,
        config_summary=_FULL_CONFIG,
    )
    assert rep["inputs_identifiable"] is True
    assert rep["process_reproducible"] is True
    assert rep["missing"] == []


def test_missing_dataset_marks_inputs_unidentifiable():
    rep = rp.reproducibility_report(
        strategy_meta=_FULL_STRATEGY, dataset_meta=None, config_summary=_FULL_CONFIG
    )
    assert "数据集摘要" in rep["missing"]
    assert rep["inputs_identifiable"] is False
    assert rep["process_reproducible"] is False
    assert any("未记录数据集内容摘要" in n for n in rep["notes"])


def test_dataset_without_digest_is_not_identifiable():
    rep = rp.reproducibility_report(
        strategy_meta=_FULL_STRATEGY, dataset_meta={"n_rows": 10}, config_summary=_FULL_CONFIG
    )
    assert "数据集摘要 digest" in rep["missing"]
    assert rep["inputs_identifiable"] is False


def test_missing_strategy_metadata_lists_both_identifiers():
    rep = rp.reproducibility_report(
        strategy_meta=None, dataset_meta=_FULL_DATASET, config_summary=_FULL_CONFIG
    )
    # strategy_meta 整体缺失时只记一条总项，不逐项展开
    assert rep["missing"] == ["策略元数据"]
    assert rep["process_reproducible"] is False


def test_missing_config_blocks_reproducibility_but_not_identifiability():
    rep = rp.reproducibility_report(
        strategy_meta=_FULL_STRATEGY, dataset_meta=_FULL_DATASET, config_summary=None
    )
    assert "回测配置" in rep["missing"]
    assert rep["inputs_identifiable"] is True  # 输入仍可识别
    assert rep["process_reproducible"] is False  # 但过程不可复现
    assert any("不保证过程可复现" in n for n in rep["notes"])


def test_report_is_json_serializable():
    """报告本身要能落盘进研究 JSON（严格模式，无 NaN/Infinity）。"""
    rep = rp.reproducibility_report(
        strategy_meta=_FULL_STRATEGY, dataset_meta=None, config_summary=None
    )
    text = json.dumps(to_jsonable(rep), allow_nan=False)
    assert json.loads(text)["process_reproducible"] is False


def test_partially_identified_strategy_lists_each_missing_item():
    """strategy_meta 存在但内部缺项时，必须逐项列出，而不是笼统一条。"""
    rep = rp.reproducibility_report(
        strategy_meta={"identified": False, "params_available": False},
        dataset_meta=_FULL_DATASET,
        config_summary=_FULL_CONFIG,
    )
    assert "策略名称/版本" in rep["missing"]
    assert "策略参数" in rep["missing"]
    assert rep["process_reproducible"] is False


def test_identified_but_params_missing_still_not_reproducible():
    rep = rp.reproducibility_report(
        strategy_meta={"identified": True, "params_available": False},
        dataset_meta=_FULL_DATASET,
        config_summary=_FULL_CONFIG,
    )
    assert rep["inputs_identifiable"] is True  # 输入可识别
    assert rep["process_reproducible"] is False  # 但过程不可复现
    assert rep["missing"] == ["策略参数"]


# --------------------------------------------------------------------------
# 补充契约：非法输入的拒绝，与不稳定类型的确定化回退
# --------------------------------------------------------------------------


class _Color(Enum):
    RED = "red"


def test_numpy_bool_becomes_python_bool():
    out = to_jsonable(np.bool_(True))
    assert type(out) is bool and out is True


def test_enum_is_written_as_its_value_not_its_class_name():
    """枚举必须写稳定的字符串值，不能写 Python 类名（`_Color.RED` 不可移植）。"""
    assert to_jsonable(_Color.RED) == "red"
    assert to_jsonable({"c": _Color.RED}) == {"c": "red"}


def test_non_finite_decimal_is_rejected():
    with pytest.raises(SerializationError) as exc:
        to_jsonable(Decimal("NaN"))
    assert "$" in str(exc.value)


def test_nat_and_timezone_aware_timestamps_are_rejected():
    """无时区约定必须强制：NaT 与带时区时间戳都不得静默通过。"""
    with pytest.raises(SerializationError):
        to_jsonable(pd.NaT)
    with pytest.raises(SerializationError) as exc:
        to_jsonable(pd.Timestamp("2024-01-01", tz="Asia/Shanghai"))
    assert "无时区" in str(exc.value)


def test_frame_without_any_date_source_is_rejected():
    df = pd.DataFrame({"close": [1.0, 2.0]})  # 既无 DatetimeIndex 也无 date 列
    with pytest.raises(ValueError) as exc:
        rp.dataset_summary(df)
    assert "DatetimeIndex 或 date 列" in str(exc.value)


def test_non_numeric_and_missing_cells_get_stable_tokens():
    """异常单元格不得让摘要崩溃，必须有确定表示（否则复现性无从谈起）。"""
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "close": [None, "not-a-number", True],
        }
    )
    lines = ["date|close", "2024-01-01|NaN", "2024-01-02|not-a-number", "2024-01-03|True"]
    expected = "sha256:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()
    assert rp.dataset_summary(df)["digest"] == expected


def test_describe_can_supply_version_when_attribute_is_missing():
    strategy = _AttrOnlyStrategy(name="s", version=None)  # 属性缺 version
    strategy.describe = lambda: {"name": "s", "version": "2.0", "params": {"n": 1}}

    meta = rp.capture_strategy_metadata(strategy)
    assert meta["version"] == "2.0"
    assert meta["identified"] is True
    assert meta["reproducible"] is True
