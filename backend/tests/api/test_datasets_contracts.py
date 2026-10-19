"""api/datasets.py 契约与不变量测试（A16#5，本地不提 PR）。

补 HTTP 级 ``test_datasets_api.py`` 未覆盖的 9 行守卫分支（coverage 实测 112 stmts / 9 miss）：
- symbol 白名单（路径穿越防御地基）；
- ``files.processed`` 必须是纯文件名（路径穿越防御）；
- 元数据必须是 JSON 对象（数组/标量被拒）；
- ``symbol`` 字段本身必须符合标识符规范（内部不变量）；
- ``rows.processed`` 必须是非负整数（且不是 bool）；
- ``date_range`` 必须是 dict，且 ``start``/``end`` 必须是字符串；
- ``find_dataset`` 拒绝非法 symbol（404 契约）；
- ``_resolve_within`` 在 ``resolve()`` 抛 OS 错误时必须吞掉并返回 ``None``（目录穿越兜底）。

全部为语义断言：校验具体被违反的不变量与返回的稳定原因，不靠「只断言不报错」。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from deepquant.api import datasets as ds
from deepquant.api.datasets import discover_datasets, find_dataset, is_valid_symbol
from deepquant.api.errors import DatasetNotFoundError


# ---------- 1. is_valid_symbol 边界（白名单契约） ----------
def test_is_valid_symbol_rejects_path_injection() -> None:
    """symbol 白名单必须排除路径分隔符、盘符、``..``（目录穿越防御的地基）。"""
    assert is_valid_symbol("600519.SH") is True
    assert is_valid_symbol("abc123") is True
    # 白名单仅允许「字母/数字开头，后续仅含字母/数字/./_/-」：
    # 因此路径分隔符、盘符、空格、前置非字母数字均被拒；单字符与「..」也因首字符
    # 须为字母/数字而被拒（这也是目录穿越防御的地基）。
    for bad in ["", "..", "../etc", "a/b", "C:\\x", "_x", " a", "a ", None, 123]:
        assert is_valid_symbol(bad) is False, f"应拒绝: {bad!r}"


# ---------- 2. files.processed 必须是纯文件名（路径穿越防御） ----------
def test_metadata_processed_non_str_is_invalid(tmp_path) -> None:
    """``files.processed`` 非字符串 → 元数据无效，不得伪装成正常数据集。"""
    root = tmp_path.resolve()
    meta = root / "metadata"
    meta.mkdir()
    payload = {
        "symbol": "BAD02",
        "files": {"processed": 123},  # 非字符串 → _is_plain_filename 返回 False
        "rows": {"processed": 10},
        "date_range": {"start": "2020-01-01", "end": "2020-01-10"},
    }
    (meta / "BAD02.meta.json").write_text(json.dumps(payload), encoding="utf-8")
    valid, invalid = discover_datasets(root)
    assert valid == []
    assert len(invalid) == 1
    assert invalid[0].symbol == "BAD02"
    assert "processed" in invalid[0].reason


def test_metadata_processed_empty_filename_is_invalid(tmp_path) -> None:
    """``files.processed`` 为空串 → 同样视为非法文件名。"""
    root = tmp_path.resolve()
    meta = root / "metadata"
    meta.mkdir()
    payload = {
        "symbol": "BAD02b",
        "files": {"processed": ""},
        "rows": {"processed": 10},
        "date_range": {"start": "2020-01-01", "end": "2020-01-10"},
    }
    (meta / "BAD02b.meta.json").write_text(json.dumps(payload), encoding="utf-8")
    valid, invalid = discover_datasets(root)
    assert valid == []
    assert invalid[0].symbol == "BAD02b"
    assert invalid[0].reason  # 非空原因，且不是正常数据集


# ---------- 3. 元数据必须是 JSON 对象（非数组/标量） ----------
def test_metadata_json_array_is_invalid(tmp_path) -> None:
    """合法 JSON 但非对象（如数组）→ 元数据无效，归 invalid_datasets。"""
    root = tmp_path.resolve()
    meta = root / "metadata"
    meta.mkdir()
    (meta / "BAD03.meta.json").write_text("[1, 2, 3]", encoding="utf-8")
    valid, invalid = discover_datasets(root)
    assert valid == []
    assert invalid[0].symbol == "BAD03"
    assert "JSON 对象" in invalid[0].reason


# ---------- 4. symbol 字段本身必须符合标识符规范（元数据内部不变量） ----------
def test_parse_metadata_symbol_violates_spec_is_rejected() -> None:
    """``_parse_metadata`` 内部：symbol 字段非法但恰等于文件名时，必须判不符合标识符规范。

    （集成路径下该情形被文件名守卫挡住，这里直接验证内部不变量。）
    """
    data = {
        "symbol": "!!!bad",  # 非法标识符
        "files": {"processed": "X.daily.parquet"},
        "rows": {"processed": 1},
        "date_range": {"start": "2020-01-01", "end": "2020-01-02"},
    }
    result = ds._parse_metadata(data, expected_symbol="!!!bad")
    assert isinstance(result, str)
    assert "标识符规范" in result


# ---------- 5. rows.processed 必须是非负整数（且不是 bool） ----------
def test_metadata_rows_processed_not_int_is_invalid(tmp_path) -> None:
    """``rows.processed`` 为字符串/负数/bool → 元数据无效。"""
    root = tmp_path.resolve()
    meta = root / "metadata"
    meta.mkdir(parents=True, exist_ok=True)
    for bad_rows, tag in [
        ({"processed": "x"}, "str"),
        ({"processed": -1}, "neg"),
        ({"processed": True}, "bool"),
    ]:
        payload = {
            "symbol": f"BAD05_{tag}",  # 必须与文件名一致，否则先命中「symbol 不一致」
            "files": {"processed": f"BAD05_{tag}.daily.parquet"},
            "rows": bad_rows,
            "date_range": {"start": "2020-01-01", "end": "2020-01-10"},
        }
        (meta / f"BAD05_{tag}.meta.json").write_text(json.dumps(payload), encoding="utf-8")
    valid, invalid = discover_datasets(root)
    assert valid == []
    assert len(invalid) == 3
    assert all("行数" in i.reason for i in invalid)


# ---------- 6. date_range 必须是 dict 且 start/end 为字符串 ----------
def test_metadata_date_range_not_dict_is_invalid(tmp_path) -> None:
    """``date_range`` 非 dict → 元数据无效。"""
    root = tmp_path.resolve()
    meta = root / "metadata"
    meta.mkdir()
    payload = {
        "symbol": "BAD06",
        "files": {"processed": "BAD06.daily.parquet"},
        "rows": {"processed": 1},
        "date_range": "2020-01-01..2020-01-10",
    }
    (meta / "BAD06.meta.json").write_text(json.dumps(payload), encoding="utf-8")
    valid, invalid = discover_datasets(root)
    assert valid == []
    assert "日期范围无效" in invalid[0].reason


def test_metadata_date_range_end_not_str_is_invalid(tmp_path) -> None:
    """``date_range.start``/``end`` 必须都是字符串 → end 为 int 时无效。"""
    root = tmp_path.resolve()
    meta = root / "metadata"
    meta.mkdir()
    payload = {
        "symbol": "BAD07",
        "files": {"processed": "BAD07.daily.parquet"},
        "rows": {"processed": 1},
        "date_range": {"start": "2020-01-01", "end": 20200110},
    }
    (meta / "BAD07.meta.json").write_text(json.dumps(payload), encoding="utf-8")
    valid, invalid = discover_datasets(root)
    assert valid == []
    assert "日期范围无效" in invalid[0].reason


# ---------- 7. find_dataset 拒绝非法 symbol（404 契约） ----------
def test_find_dataset_rejects_invalid_symbol(tmp_path) -> None:
    """symbol 非法（含空格/分隔符）→ 直接抛 DatasetNotFoundError，不触碰文件系统。"""
    root = tmp_path.resolve()
    with pytest.raises(DatasetNotFoundError):
        find_dataset(root, "BAD SYM")


# ---------- 8. _resolve_within 必须吞掉 resolve 的 OS 错误（防穿越兜底） ----------
def test_resolve_within_swallows_os_error(tmp_path) -> None:
    """``candidate.resolve()`` 抛 OSError（如目录结构损坏）时，必须返回 ``None`` 而非上抛，

    使该数据集被判定为不可用，而非引发 500。
    """
    root = tmp_path.resolve()
    candidate = root / "processed" / "x.parquet"
    with patch.object(Path, "resolve", side_effect=OSError("boom")):
        assert ds._resolve_within(root, candidate) is None
