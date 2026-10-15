"""API 路径与标识符安全回归测试。

验证：路径穿越、绝对路径、盘符、反斜杠、URL 编码绕过、符号链接逃逸、
元数据文件名注入都不能读取数据根目录之外的文件；
API 不接受任何客户端提供的文件系统路径。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

try:
    import _winapi  # 仅 Windows 存在（venv 模块同款 junction 创建原语，免管理员权限）
except ImportError:  # 非 Windows 平台
    _winapi = None

from conftest import ingest_dataset
from deepquant.api.datasets import _resolve_within


def test_resolve_within_rejects_outside_paths(data_root) -> None:
    """路径解析守卫（符号链接拦截依赖的原语）：根目录外路径一律返回 None。"""
    inside = data_root / "processed" / "x.parquet"
    assert _resolve_within(data_root, inside) == inside.resolve()
    assert _resolve_within(data_root, data_root / ".." / "outside.parquet") is None
    assert _resolve_within(data_root, data_root / "sub" / ".." / ".." / "x") is None
    # 根目录外的绝对路径（含盘符 / POSIX 根）一律拒绝
    outside_abs = Path(data_root.anchor) / "definitely_not_in_root_x9q"
    assert _resolve_within(data_root, outside_abs) is None


MALICIOUS_SYMBOLS = [
    "../secret",  # 相对路径穿越
    "..\\..\\secret",  # Windows 反斜杠穿越
    "..",  # 上级目录
    "C:/Windows/win.ini",  # Windows 绝对路径 + 盘符
    "C:\\Windows\\win.ini",
    "/etc/passwd",  # POSIX 绝对路径
    "a/b",  # 含路径分隔符
    "a\\b",
    " space",  # 非法首字符
    "",  # 空标识符
]


@pytest.mark.parametrize("symbol", MALICIOUS_SYMBOLS)
def test_malicious_dataset_symbol_rejected(client_with_dataset, valid_backtest_payload, symbol) -> None:
    """非法数据集标识符：422（在校验阶段拦截，不触碰文件系统）。"""
    payload = {**valid_backtest_payload, "dataset_symbol": symbol}
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 422


def test_url_encoded_traversal_rejected(client_with_dataset, valid_backtest_payload) -> None:
    """URL 编码形式的路径穿越：解码后仍因字符白名单被 422 拦截。"""
    payload = {**valid_backtest_payload, "dataset_symbol": "..%2F..%2Fsecret"}
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 422


def test_unregistered_but_valid_symbol_is_404_not_file_access(client_with_dataset, valid_backtest_payload) -> None:
    """合法但未登记的标识符：404，不发生任何文件读取。"""
    payload = {**valid_backtest_payload, "dataset_symbol": "VALID99"}
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"


def test_metadata_absolute_processed_path_rejected(client, data_root) -> None:
    """元数据中的绝对路径文件名：识别为无效数据集，不访问根目录外文件。"""
    metadata_dir = data_root / "metadata"
    metadata_dir.mkdir(exist_ok=True)
    payload = {
        "symbol": "EVIL01",
        "files": {"processed": "C:/Windows/win.ini"},
        "rows": {"processed": 1},
        "date_range": {"start": "2020-01-01", "end": "2020-01-02"},
    }
    (metadata_dir / "EVIL01.meta.json").write_text(json.dumps(payload), encoding="utf-8")

    body = client.get("/datasets").json()
    assert body["datasets"] == []
    assert body["invalid_datasets"][0]["symbol"] == "EVIL01"

    response = client.post(
        "/backtests",
        json={"dataset_symbol": "EVIL01", "strategy_id": "ma_cross", "initial_cash": 10000},
    )
    assert response.status_code == 404


def test_metadata_relative_processed_path_rejected(client, data_root) -> None:
    """元数据中的相对路径文件名（含 ..）：识别为无效数据集。"""
    metadata_dir = data_root / "metadata"
    metadata_dir.mkdir(exist_ok=True)
    payload = {
        "symbol": "EVIL02",
        "files": {"processed": "../../outside.parquet"},
        "rows": {"processed": 1},
        "date_range": {"start": "2020-01-01", "end": "2020-01-02"},
    }
    (metadata_dir / "EVIL02.meta.json").write_text(json.dumps(payload), encoding="utf-8")

    body = client.get("/datasets").json()
    assert body["datasets"] == []
    assert any(d["symbol"] == "EVIL02" for d in body["invalid_datasets"])


def test_symlink_escape_blocked(client, data_root, tmp_path, valid_backtest_payload) -> None:
    """符号链接逃逸：processed 内的符号链接指向根目录外文件时，解析后被拦截。"""
    ingest_dataset(data_root, tmp_path, "TEST01")

    outside = tmp_path / "outside.parquet"
    real = data_root / "processed" / "TEST01.daily.parquet"
    outside.write_bytes(real.read_bytes())

    link = data_root / "processed" / "LINK01.daily.parquet"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前环境不允许创建符号链接：{exc}")

    # 为元数据准备一个指向符号链接的条目（文件名本身合法）
    metadata_dir = data_root / "metadata"
    payload = {
        "symbol": "LINK01",
        "files": {"processed": "LINK01.daily.parquet"},
        "rows": {"processed": 60},
        "date_range": {"start": "2020-01-01", "end": "2020-02-29"},
    }
    (metadata_dir / "LINK01.meta.json").write_text(json.dumps(payload), encoding="utf-8")

    # resolve 后目标在根目录之外：必须被拦截，不能作为可用数据集
    body = client.get("/datasets").json()
    assert all(d["symbol"] != "LINK01" for d in body["datasets"])

    response = client.post(
        "/backtests",
        json={**valid_backtest_payload, "dataset_symbol": "LINK01"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_unavailable"


def test_junction_escape_blocked(client, data_root, tmp_path, valid_backtest_payload) -> None:
    """目录联接（junction）逃逸：Windows 免管理员的 reparse point 真实验证。

    说明（如实标注）：junction 只能指向目录，而符号链接可指向文件；本测试通过
    「数据文件名本身是指向根目录外目录的 junction」复现同一攻击面——路径
    resolve 后目标越出数据根目录，必须被拦截。它验证的是真实文件系统
    reparse point 行为，不是符号链接文件的完整等价物；符号链接文件测试见
    test_symlink_escape_blocked（需要符号链接权限或 Linux 环境）。
    """
    if _winapi is None:
        pytest.skip("非 Windows 平台：目录联接不适用（符号链接测试在 Linux 覆盖）")
    ingest_dataset(data_root, tmp_path, "TEST01")

    outside = tmp_path / "outside_dir"
    outside.mkdir()
    link = data_root / "processed" / "LINK01.daily.parquet"
    try:
        _winapi.CreateJunction(str(outside), str(link))
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前环境不允许创建目录联接：{exc}")

    # 元数据文件名本身合法，仅数据文件路径解析后越出根目录
    metadata_dir = data_root / "metadata"
    payload = {
        "symbol": "LINK01",
        "files": {"processed": "LINK01.daily.parquet"},
        "rows": {"processed": 60},
        "date_range": {"start": "2020-01-01", "end": "2020-02-29"},
    }
    (metadata_dir / "LINK01.meta.json").write_text(json.dumps(payload), encoding="utf-8")

    # resolve 后目标在根目录之外：必须被拦截，不能作为可用数据集
    body = client.get("/datasets").json()
    assert all(d["symbol"] != "LINK01" for d in body["datasets"])
    assert any(d["symbol"] == "LINK01" for d in body["invalid_datasets"])

    response = client.post(
        "/backtests",
        json={**valid_backtest_payload, "dataset_symbol": "LINK01"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_unavailable"


def test_strategy_id_injection_rejected(client_with_dataset, valid_backtest_payload) -> None:
    """策略标识符注入（路径 / 导入语法）：422 或 404，绝不动态加载代码。"""
    for bad in ("../engine", "deepquant.backtest.engine", "__import__('os')"):
        payload = {**valid_backtest_payload, "strategy_id": bad}
        response = client_with_dataset.post("/backtests", json=payload)
        assert response.status_code in (404, 422)


def test_error_responses_have_consistent_structure(client_with_dataset, valid_backtest_payload) -> None:
    """各类错误响应结构一致：{"error": {"code", "message", ...}}。"""
    cases = [
        ({**valid_backtest_payload, "dataset_symbol": "NOPE"}, 404),
        ({**valid_backtest_payload, "strategy_id": "nope"}, 404),
        ({**valid_backtest_payload, "initial_cash": -1}, 422),
        ({**valid_backtest_payload, "start_date": "2030-01-01", "end_date": "2030-02-01"}, 400),
    ]
    for payload, expected_status in cases:
        response = client_with_dataset.post("/backtests", json=payload)
        assert response.status_code == expected_status
        error = response.json()["error"]
        assert isinstance(error["code"], str) and error["code"]
        assert isinstance(error["message"], str) and error["message"]
