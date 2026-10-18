"""DeepQuant API 应用入口。

当前路由：
- GET  /health     健康检查（契约保持不变）；
- GET  /datasets   可用数据集列表；
- GET  /strategies 策略注册表及参数说明；
- POST /backtests  同步回测（复用既有引擎与序列化契约）；
- POST /portfolio-backtests 多标的组合回测（复用组合记账层，见 api/portfolio.py）。

API 层只做参数验证、数据集解析、策略选择、调用引擎、异常映射与响应输出；
回测撮合、绩效计算与结果序列化统一复用 backtest / performance 模块的既有实现。
"""

from fastapi import FastAPI

from deepquant.api.buildinfo import BuildHeaderMiddleware, build_id
from deepquant.api.errors import register_exception_handlers
from deepquant.api.routes import backtests, datasets, health, portfolio, strategies
from deepquant.api.ui import mount_ui
from deepquant.version import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="DeepQuant API", version=__version__)
    register_exception_handlers(app)
    # 每个响应带构建指纹：启动器据此判断「端口上那个实例是不是这份代码」。
    # 只加响应头，不改任何响应体与路径，契约保持原样。
    app.add_middleware(BuildHeaderMiddleware, build=build_id())
    app.include_router(health.router)
    app.include_router(datasets.router)
    app.include_router(strategies.router)
    app.include_router(backtests.router)
    app.include_router(portfolio.router)
    # 单端口可用版：仅在 DEEPQUANT_SERVE_UI=1 且 frontend/dist 存在时挂载。
    # 默认关闭，API 契约与既有测试行为不变（见 api/ui.py 模块说明）。
    mount_ui(app)
    return app


app = create_app()
