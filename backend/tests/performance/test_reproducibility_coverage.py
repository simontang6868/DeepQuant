"""``performance`` 可复现性模块覆盖率补强（A12）。

目标：``reproducibility.py`` 88% → ≥95%，``json_encoding.py`` 86% → ≥95%。

A 线红线（本文件严格遵守）：
  1. **只新增测试，不修改 ``src/`` 下任何代码。**
  2. 测出的缺陷只许测出来不许修，用 ``xfail(strict=True)`` 固化。
  3. 断言由设计意图推导，不抄运行结果。

补的都是"防护性分支"：输入类型校验、不可序列化回退、缺失字段标记。
这些分支平时不触发，一旦触发就决定了系统是「给出明确诊断」还是「悄悄产出错结果」，
所以值得单独钉住。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json as _json
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

import numpy as np
import pandas as pd
import pytest

from deepquant.performance.json_encoding import (
    SerializationError,
    from_jsonable,
    to_jsonable,
)
from deepquant.performance.reproducibility import (
    _number_token,
    _resolve_dates_columns,
    capture_strategy_metadata,
    dataset_summary,
    reproducibility_report,
)
from deepquant.strategies.base import Signal, Strategy


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


class Color(Enum):
    RED = "red"
    BLUE = "blue"


@dataclass
class Point:
    x: float
    y: float
    label: str


class _Stub(Strategy):
    """最小可用策略替身。"""

    name = "stub"
    version = "1.2.3"

    @property
    def params(self) -> dict:
        return {"k": 1}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        return pd.Series(int(Signal.HOLD), index=data.index)


class _DescribeRaises(_Stub):
    """``describe()`` 抛异常的策略 —— 触发「无法获取策略标识」分支。"""

    name = "describe_raises"
    version = "2.0.0"

    def describe(self) -> dict:
        raise RuntimeError("describe exploded")


class _NoParams(_Stub):
    """``describe()`` 返回但不含 params —— 触发「未提供参数」分支。"""

    name = "no_params"
    version = "9.9.9"

    def describe(self) -> dict:
        return {"name": "no_params", "version": "9.9.9"}


def _close_frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-02", periods=len(closes))
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000] * len(closes),
        },
        index=idx,
    )


# --------------------------------------------------------------------------- #
# json_encoding：标量各类型分支
# --------------------------------------------------------------------------- #


class TestJsonEncodingScalarBranches:
    def test_numpy_bool_becomes_python_bool(self) -> None:
        out = to_jsonable(np.bool_(True))
        assert out is True
        assert type(out) is bool

    def test_enum_is_unwrapped_to_its_value(self) -> None:
        assert to_jsonable(Color.RED) == "red"

    def test_enum_nested_in_container_is_recursed(self) -> None:
        assert to_jsonable({"c": [Color.BLUE], "d": {"e": Color.RED}}) == {
            "c": ["blue"],
            "d": {"e": "red"},
        }

    def test_non_finite_decimal_raises_with_path(self) -> None:
        with pytest.raises(SerializationError, match="Decimal 必须有限"):
            to_jsonable({"a": Decimal("NaN")})

    def test_nat_date_raises(self) -> None:
        with pytest.raises(SerializationError, match="NaT"):
            to_jsonable(pd.NaT)

    def test_timezone_aware_timestamp_raises(self) -> None:
        with pytest.raises(SerializationError, match="无时区"):
            to_jsonable(pd.Timestamp("2024-01-02 15:00", tz="Asia/Shanghai"))

    def test_plain_date_and_datetime_are_iso_formatted(self) -> None:
        assert to_jsonable(_dt.date(2024, 1, 2)) == "2024-01-02T00:00:00"
        assert to_jsonable(_dt.datetime(2024, 1, 2, 9, 30)) == "2024-01-02T09:30:00"

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(SerializationError, match="不支持的类型"):
            to_jsonable(object())


# --------------------------------------------------------------------------- #
# json_encoding：容器分支
# --------------------------------------------------------------------------- #


class TestJsonEncodingContainerBranches:
    def test_set_is_sorted_by_repr_for_determinism(self) -> None:
        assert to_jsonable({"b", "a"}) == ["a", "b"]

    def test_frozenset_is_supported(self) -> None:
        assert to_jsonable(frozenset({3, 1, 2})) == [1, 2, 3]

    def test_ndarray_becomes_plain_list(self) -> None:
        out = to_jsonable(np.array([1.5, 2.5]))
        assert out == [1.5, 2.5]
        assert isinstance(out, list)
        assert not isinstance(out, np.ndarray)

    def test_dataclass_expands_in_declaration_order(self) -> None:
        out = to_jsonable(Point(x=1.0, y=2.0, label="p"))
        assert out == {"x": 1.0, "y": 2.0, "label": "p"}
        assert list(out.keys()) == ["x", "y", "label"]

    def test_nested_dataclass_in_dict(self) -> None:
        out = to_jsonable({"p": Point(1.0, 2.0, "q")})
        assert out == {"p": {"x": 1.0, "y": 2.0, "label": "q"}}

    def test_non_string_dict_key_raises(self) -> None:
        with pytest.raises(SerializationError, match="字典键必须是字符串"):
            to_jsonable({1: "a"})

    def test_error_path_points_to_the_offending_field(self) -> None:
        with pytest.raises(SerializationError, match=r"\$\.a\.b"):
            to_jsonable({"a": {"b": float("inf")}})

    def test_from_jsonable_recurses_into_lists(self) -> None:
        payload = [{"__type__": "decimal", "value": "1.50"}]
        restored = from_jsonable(payload)
        assert restored == [Decimal("1.50")]

    def test_from_jsonable_passes_scalars_through(self) -> None:
        """标量原样返回（递归终止条件）。"""
        assert from_jsonable(5) == 5
        assert from_jsonable("x") == "x"
        assert from_jsonable(None) is None

    def test_from_jsonable_recurses_into_nested_dicts(self) -> None:
        payload = {"outer": [{"__type__": "decimal", "value": "0.01"}]}
        restored = from_jsonable(payload)
        assert restored == {"outer": [Decimal("0.01")]}

    def test_roundtrip_through_stdlib_json_is_lossless_for_decimals(self) -> None:
        """Decimal 走标签路径后过一遍标准 json，精度不变（不退化成二进制浮点）。"""
        value = Decimal("3.141592653589793238462643383279")
        text = _json.dumps(to_jsonable({"v": value}))
        assert from_jsonable(_json.loads(text))["v"] == value


# --------------------------------------------------------------------------- #
# reproducibility：数值格式化分支（_number_token）
# --------------------------------------------------------------------------- #


class TestNumberTokenBranches:
    """``_number_token`` 决定摘要文本的数值表示，直接决定 digest 是否稳定。"""

    def test_none_becomes_nan_token(self) -> None:
        assert _number_token(None) == "NaN"

    def test_nan_becomes_nan_token(self) -> None:
        assert _number_token(float("nan")) == "NaN"

    def test_python_bool_becomes_true_false(self) -> None:
        assert _number_token(True) == "True"
        assert _number_token(False) == "False"

    def test_numpy_bool_becomes_true_false(self) -> None:
        assert _number_token(np.bool_(True)) == "True"
        assert _number_token(np.bool_(False)) == "False"

    def test_non_numeric_falls_back_to_str(self) -> None:
        """无法转 float 的值：走 str() 兜底，而不是抛异常中断摘要。"""
        assert _number_token("abc") == "abc"

    def test_numeric_string_is_normalized_to_float_repr(self) -> None:
        """数值字符串会被归一成 float 的 repr —— 保证 10 与 10.0 摘要一致。"""
        assert _number_token("10") == repr(10.0)

    def test_plain_object_falls_back_to_str(self) -> None:
        class Marker:
            def __repr__(self) -> str:
                return "MARKER"

        assert _number_token(Marker()) == "MARKER"

    def test_pd_isna_raising_is_swallowed_then_falls_back(self) -> None:
        """``pd.isna`` 对数组类输入返回 ndarray，``if`` 判定会抛 ValueError。

        该分支保证：这类"无法用标量语义判缺失"的值不会被当成缺失，
        也不会抛异常中断摘要，而是继续走 float 转换、失败则用 str 兜底。
        """
        assert _number_token([1, 2]) == "[1, 2]"


# --------------------------------------------------------------------------- #
# reproducibility：输入校验分支
# --------------------------------------------------------------------------- #


class TestResolveDatesColumnsValidation:
    def test_non_dataframe_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="DataFrame"):
            dataset_summary([1, 2, 3])

    def test_missing_date_source_raises_value_error(self) -> None:
        df = pd.DataFrame({"close": [10.0, 11.0]})
        with pytest.raises(ValueError, match="DatetimeIndex 或 date 列"):
            dataset_summary(df)

    def test_unparsable_dates_raise_value_error(self) -> None:
        df = pd.DataFrame({"date": ["2024-01-02", "not-a-date"], "close": [10.0, 11.0]})
        with pytest.raises(ValueError, match="无法解析的日期"):
            dataset_summary(df)

    def test_index_branch_preferred_over_date_column(self) -> None:
        """DatetimeIndex 优先：即使同时存在 date 列也不重复计入。"""
        df = _close_frame([10.0, 11.0, 12.0])
        df = df.copy()
        df["date"] = df.index

        dates, columns = _resolve_dates_columns(df)

        assert len(dates) == 3
        assert "date" in columns   # index 分支下，date 仍是普通列


# --------------------------------------------------------------------------- #
# reproducibility：策略元数据的缺失项标记
# --------------------------------------------------------------------------- #


class TestCaptureStrategyMetadataNotes:
    def test_describe_failure_is_recorded_not_swallowed(self) -> None:
        """``describe()`` 抛异常 ⇒ 记录原因，且不得伪装成"无参数"。"""
        meta = capture_strategy_metadata(_DescribeRaises())

        assert any("describe 失败" in note for note in meta["notes"])
        assert meta["params"] is None
        assert meta["params_available"] is False
        # 名称/版本仍在 ⇒ identified 为 True，不应被 describe 失败牵连
        assert meta["identified"] is True
        assert meta["reproducible"] is False

    def test_missing_params_recorded_as_note(self) -> None:
        """describe() 返回但无 params ⇒ 明确记一条 note，notes 非空。"""
        meta = capture_strategy_metadata(_NoParams())

        assert meta["name"] == "no_params"
        assert meta["params"] is None
        assert meta["params_available"] is False
        assert meta["reproducible"] is False
        assert any("未提供参数" in note for note in meta["notes"])

    def test_unserializable_params_recorded(self) -> None:
        """params 不可序列化 ⇒ 置 None + 记 note，不抛异常。"""

        class Weird(_Stub):
            name = "weird"

            @property
            def params(self) -> dict:
                return {"handler": object()}

        meta = capture_strategy_metadata(Weird())

        assert meta["params"] is None
        assert meta["params_available"] is False
        assert meta["reproducible"] is False
        assert any("不可序列化" in note for note in meta["notes"])


# --------------------------------------------------------------------------- #
# reproducibility：可复现性判定的 missing 清单
# --------------------------------------------------------------------------- #

_DIGEST = "sha256:" + "a" * 64


class TestReproducibilityReportMissing:
    def _strategy(self, **overrides) -> dict:
        base = {"identified": True, "params_available": True}
        base.update(overrides)
        return base

    def test_dataset_meta_without_digest_is_flagged(self) -> None:
        report = reproducibility_report(
            strategy_meta=self._strategy(),
            dataset_meta={"digest": None},
            config_summary={"fee_source": "simulation_placeholder"},
        )

        assert "数据集摘要 digest" in report["missing"]
        assert report["inputs_identifiable"] is False
        assert report["process_reproducible"] is False

    def test_unidentified_strategy_is_flagged(self) -> None:
        report = reproducibility_report(
            strategy_meta=self._strategy(identified=False),
            dataset_meta={"digest": _DIGEST},
            config_summary={"fee_source": "simulation_placeholder"},
        )

        assert "策略名称/版本" in report["missing"]
        assert report["inputs_identifiable"] is False
        assert report["process_reproducible"] is False

    def test_missing_params_is_flagged(self) -> None:
        report = reproducibility_report(
            strategy_meta=self._strategy(params_available=False),
            dataset_meta={"digest": _DIGEST},
            config_summary={"fee_source": "simulation_placeholder"},
        )

        assert "策略参数" in report["missing"]
        assert report["inputs_identifiable"] is True
        assert report["process_reproducible"] is False

    def test_all_present_is_reproducible(self) -> None:
        report = reproducibility_report(
            strategy_meta=self._strategy(),
            dataset_meta={"digest": _DIGEST},
            config_summary={"fee_source": "simulation_placeholder"},
        )

        assert report["missing"] == []
        assert report["inputs_identifiable"] is True
        assert report["process_reproducible"] is True
        assert report["notes"] == []

    def test_empty_config_summary_is_flagged(self) -> None:
        """空 dict 也算缺失 —— 防止用 {} 蒙混过关。"""
        report = reproducibility_report(
            strategy_meta=self._strategy(),
            dataset_meta={"digest": _DIGEST},
            config_summary={},
        )

        assert "回测配置" in report["missing"]
        assert report["process_reproducible"] is False

