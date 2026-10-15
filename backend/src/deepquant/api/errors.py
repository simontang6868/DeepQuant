"""API 统一异常类型与异常处理器。

约定：
- 所有错误响应统一为 ``{"error": {"code", "message", "details?"}}`` 结构；
- 面向客户端的 message 不包含服务器绝对路径、堆栈或敏感配置；
- 未预期异常记录完整服务端日志，但对客户端只返回通用提示；
- 已知业务异常按语义映射到 422 / 404 / 400 / 503，未预期异常为 500。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from deepquant.data.loaders.csv_loader import DataLoadError
from deepquant.data.pipeline import DataValidationError
from deepquant.performance.json_encoding import SerializationError

logger = logging.getLogger("deepquant.api")


class ApiError(Exception):
    """API 业务异常基类：携带 HTTP 状态码与稳定错误码。"""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class DataRootUnavailableError(ApiError):
    """数据根目录不存在或不可访问（服务端配置问题）。"""

    status_code = 503
    code = "data_root_unavailable"


class DatasetNotFoundError(ApiError):
    """请求的数据集未登记。"""

    status_code = 404
    code = "dataset_not_found"


class DatasetUnavailableError(ApiError):
    """数据集已登记但不可用（元数据无效或数据文件缺失/不可读）。"""

    status_code = 404
    code = "dataset_unavailable"


class StrategyNotFoundError(ApiError):
    """请求的策略标识符未注册。"""

    status_code = 404
    code = "strategy_not_found"


class StrategyParamsError(ApiError):
    """策略参数不合法（类型错误、未知参数或违反策略约束）。"""

    status_code = 422
    code = "invalid_strategy_params"


class ConfigInvalidError(ApiError):
    """回测配置不合法（BacktestConfig 校验未通过）。"""

    status_code = 422
    code = "invalid_config"


class BacktestRejectedError(ApiError):
    """请求合法，但回测在给定条件下无法执行（如区间内无行情数据）。"""

    status_code = 400
    code = "backtest_rejected"


def _error_body(code: str, message: str, details: Any = None) -> dict:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    """注册统一异常处理器（保持错误响应结构一致）。"""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("API 错误 %s：%s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 只提取安全字段（loc/msg/type），避免把异常对象或原始输入整体回传
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_body("invalid_request", "请求参数不合法", details),
        )

    @app.exception_handler(DataValidationError)
    async def _handle_data_validation(request: Request, exc: DataValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_error_body("data_validation_failed", str(exc)),
        )

    @app.exception_handler(DataLoadError)
    async def _handle_data_load(request: Request, exc: DataLoadError) -> JSONResponse:
        # DataLoadError 消息可能包含文件路径，仅记录到服务端日志，不回传客户端
        logger.warning("数据载入失败：%s", exc)
        return JSONResponse(
            status_code=400,
            content=_error_body("data_load_failed", "数据无法按约定载入（详见服务端日志）"),
        )

    @app.exception_handler(SerializationError)
    async def _handle_serialization(request: Request, exc: SerializationError) -> JSONResponse:
        # 结果序列化失败属于服务端问题，不得吞掉或伪装成成功响应
        logger.error("回测结果序列化失败：%s", exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("serialization_failed", "回测结果无法序列化为标准 JSON"),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未预期的内部错误：%s", exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "服务器内部错误"),
        )
