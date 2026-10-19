"""API 统一异常处理器契约测试（A16#6，补 api/errors.py 未被覆盖的 6 行守卫）。

目标行：159-160（DataLoadError 处理器）/ 168-169（SerializationError 处理器）
/ 201-202（未预期异常兜底处理器）。

这些处理器已注册但既有的 happy-path 测试从不触发；本文件用最小 FastAPI app
（只注册异常处理器 + 三条主动抛异常的路由）精确命中它们，并校验两条契约：

1. 状态码映射正确（DataLoadError→400 / SerializationError→500 / 未预期→500）；
2. 安全契约——处理器回传给客户端的 message 是**固定安全文案**，原始异常里可能
   携带的文件路径 / 堆栈 / 内部细节**不得**泄漏到响应体（仅记录到服务端日志）。

全部为语义断言，未改 src/ 一行。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deepquant.api.errors import register_exception_handlers
from deepquant.data.loaders.csv_loader import DataLoadError
from deepquant.performance.json_encoding import SerializationError


def _make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-data-load")
    def _raise_data_load() -> None:
        # 模拟 csv_loader 抛出的错误：消息里带文件路径与系统细节
        raise DataLoadError(
            "无法载入 C:\\secret\\portfolio.csv：权限不足（errno=13）"
        )

    @app.get("/raise-serialization")
    def _raise_serialization() -> None:
        # 模拟结果序列化失败：消息带具体编码细节
        raise SerializationError(
            "json 编码失败：NaN / Infinity 不可序列化（字段=net_value）"
        )

    @app.get("/raise-unexpected")
    def _raise_unexpected() -> None:
        # 模拟任何未预期的运行时异常
        raise RuntimeError(
            "内部状态损坏 traceback=/opt/app/db.sock connection refused"
        )

    return app


def test_data_load_error_returns_400_with_safe_message() -> None:
    """DataLoadError → 400，且不得把原始文件路径/细节回传客户端。"""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/raise-data-load")

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "data_load_failed"
    # 安全契约：原始消息中的敏感细节必须被剥离
    assert "C:\\secret\\portfolio.csv" not in body["error"]["message"]
    assert "权限不足" not in body["error"]["message"]
    assert "errno" not in body["error"]["message"]
    # 回传的是固定安全文案
    assert body["error"]["message"] == "数据无法按约定载入（详见服务端日志）"
    # 无 details 字段
    assert "details" not in body["error"]


def test_serialization_error_returns_500_with_safe_message() -> None:
    """SerializationError → 500，且不得把编码细节回传客户端。"""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/raise-serialization")

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "serialization_failed"
    assert "NaN" not in body["error"]["message"]
    assert "net_value" not in body["error"]["message"]
    assert body["error"]["message"] == "回测结果无法序列化为标准 JSON"
    assert "details" not in body["error"]


def test_unexpected_exception_returns_500_generic_message() -> None:
    """任何未预期异常 → 500，且必须是通用文案，不得泄漏内部细节。"""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/raise-unexpected")

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal_error"
    # 安全契约：原始异常文本不得泄漏
    assert "内部状态损坏" not in body["error"]["message"]
    assert "connection refused" not in body["error"]["message"]
    assert "db.sock" not in body["error"]["message"]
    assert body["error"]["message"] == "服务器内部错误"
    assert "details" not in body["error"]


def test_error_body_omits_details_when_none() -> None:
    """_error_body：details 为 None 时响应体不得出现 details 字段。

    这条锁定错误响应结构契约——details 仅在有内容时出现，避免空 details 噪声。
    """
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # DataLoadError 处理器以 details=None 调用 _error_body
    resp = client.get("/raise-data-load")
    assert "details" not in resp.json()["error"]
