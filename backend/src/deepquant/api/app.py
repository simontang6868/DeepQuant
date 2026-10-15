"""DeepQuant API 应用入口。

第一阶段只提供最小可运行的服务，用于验证环境与测试链路是否打通。
后续各业务模块（data / strategies / backtest / performance）通过 api 薄层暴露。
"""

from fastapi import FastAPI

app = FastAPI(title="DeepQuant API", version="0.0.1")


@app.get("/health")
def health() -> dict[str, str]:
    """健康检查：返回服务存活状态。"""
    return {"status": "ok"}
