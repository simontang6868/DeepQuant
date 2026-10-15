"""DeepQuant API 应用入口。

当前路由：
- GET  /health     健康检查（契约保持不变）；
- GET  /datasets   可用数据集列表；
- GET  /strategies 策略注册表及参数说明；
- POST /backtests  同步回测（复用既有引擎与序列化契约）。

API 层只做参数验证、数据集解析、策略选择、调用引擎、异常映射与响应输出；
回测撮合、绩效计算与结果序列化统一复用 backtest / performance 模块的既有实现。
"""

from fastapi import FastAPI

from deepquant.api.errors import register_exception_handlers
from deepquant.api.routes import backtests, datasets, health, strategies
from deepquant.version import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="DeepQuant API", version=__version__)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(datasets.router)
    app.include_router(strategies.router)
    app.include_router(backtests.router)
    return app


app = create_app()
