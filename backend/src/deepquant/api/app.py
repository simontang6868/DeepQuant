"""DeepQuant API 应用入口。

当前路由：
- GET  /health     健康检查（契约保持不变）；
- GET  /datasets   可用数据集列表；
- GET  /strategies 策略注册表及参数说明；
- POST /backtests  同步回测（复用既有引擎与序列化契约）；
- POST /portfolio-backtests 多标的组合回测（复用组合记账层，见 api/portfolio.py）。

API 层只做参数验证、数据集解析、策略选择、调用引擎、异常映射与响应输出；
回测撮合、绩效计算与结果序列化统一复用 backtest / performance 模块的既有实现。

四个横切中间件（均默认关闭，未配置环境变量时行为与之前完全一致）：

- ``RiskGateMiddleware``（T2 fail-closed 网关）：在业务执行**之前**判定身份/限额/
  状态/字段四类检查，拒绝时给出可检索的原因码；
- ``CapabilityGateMiddleware``（T4 能力闸门）：判定"这个能力是否开放、这个人是否
  被授权、版本是否兼容"，拒绝原因码写入同一份工件；
- ``SafetyMiddleware``（T5 安全层）：熔断判定 + 在飞请求计数（退出清理要靠它验证清空）；
- ``AuditMiddleware``（T1 全流程留痕）：把每次操作的工件落盘，含三层的原因码。

装配顺序有意义：**审计最外层 → 安全 → 能力 → 风控最内层**（后添加者位于外层）。
这样被任一前置层拒绝的请求同样会落卡；若反过来，拒绝事件不会留下任何工件，
而"没有记录"与"没人操作过"长得一模一样。该顺序由
``tests/risk/test_risk_gateway_api.py``、``tests/capabilities/test_cap_middleware.py``
与 ``tests/safety/test_safety_middleware.py`` 用真实请求断言，不靠注释保证。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from deepquant.api.buildinfo import BuildHeaderMiddleware, build_id
from deepquant.api.config import get_data_root
from deepquant.api.datasets import find_dataset
from deepquant.api.errors import (
    DatasetNotFoundError,
    DatasetUnavailableError,
    register_exception_handlers,
)
from deepquant.api.registry import STRATEGY_REGISTRY
from deepquant.api.routes import backtests, datasets, health, portfolio, strategies
from deepquant.api.ui import mount_ui
from deepquant.audit.middleware import AuditMiddleware
from deepquant.capabilities.middleware import CapabilityGateMiddleware
from deepquant.persistence import get_db_path, get_run
from deepquant.risk.middleware import RiskGateMiddleware, RiskLookups
from deepquant.safety.lifecycle import (
    assert_clean_start,
    begin_session,
    end_session,
    run_cleanup,
    safety_state_enabled,
)
from deepquant.safety.middleware import SafetyMiddleware
from deepquant.version import __version__

#: 数据集查询时视为「不可用」的异常——其余异常一律上抛，由风控判为「无法校验」。
_DATASET_UNAVAILABLE_ERRORS = (DatasetNotFoundError, DatasetUnavailableError)


def _effective_data_root(app: FastAPI) -> Path:
    """解析当前生效的数据根目录。

    中间件不在 FastAPI 依赖注入链上，拿不到 ``Depends(get_data_root)`` 的结果。
    这里显式先看 ``app.dependency_overrides``（测试用它把数据根重定向到临时目录），
    再回落到真实解析函数。若不看 override，测试中的合法请求会被风控误判成
    「数据集不存在」——那是把测试脚手架的差异当成业务故障。
    """
    override = app.dependency_overrides.get(get_data_root)
    provider = override if override is not None else get_data_root
    return provider()


def _risk_lookups(app: FastAPI) -> RiskLookups:
    """装配风控状态检查点所需的查询能力。

    三个查询遵循同一约定：**"不存在"返回 None，"查不了"抛异常**。
    两者必须分开——数据库连不上时返回 None 会被记成「run_id 不存在」，
    把基础设施故障伪装成用户输入错误，是最容易误导排查的错法。
    """

    def dataset_lookup(symbol: str):
        try:
            info, _path = find_dataset(_effective_data_root(app), symbol)
            return info
        except _DATASET_UNAVAILABLE_ERRORS:
            return None

    def strategy_lookup():
        return STRATEGY_REGISTRY

    def run_lookup(run_id: str):
        return get_run(get_db_path(), run_id)

    return RiskLookups(
        dataset_lookup=dataset_lookup,
        strategy_lookup=strategy_lookup,
        run_lookup=run_lookup,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动门 + 退出清理（T5）。

    为什么放在 lifespan 而不是 import 时
    ------------------------------------
    ``import`` 只表示"代码被加载"，不表示"服务要开始对外服务"。在 import 时拒绝
    启动，会让所有 import 本模块的工具（脚本、测试、离线任务）一起挂掉；而
    lifespan 的 startup 恰好是"这个进程真的要开始处理请求"的那一刻，也是 uvicorn
    收到 SIGTERM 后会走到的 shutdown 点。**崩溃（SIGKILL/断电）不会走这里**——
    正因如此才有 ``session.json``：它由 startup 写下、由成功清理删除，
    下次启动看到它就说明上次没走钩子。

    清理失败时**不删 session**，从而阻止下次启动；这一点绝不能"宽容"：
    半释放的资源上继续写，会把一次可诊断的失败变成一次不可诊断的数据污染。
    """
    if not safety_state_enabled():
        # 未配置安全目录且处于测试进程：跳过启动门与清理（理由见 lifecycle 同名函数）
        yield
        return

    assert_clean_start()  # 脏状态（崩溃残留/清理失败）直接拒绝启动
    begin_session()
    try:
        yield
    finally:
        report = run_cleanup("lifespan_shutdown")
        if report.ok:
            end_session()
        else:
            # 保留 session.json 与 cleanup_required.json，下次启动会被拒
            print(
                "[safety] 退出清理未通过，已保留标记以阻止下次启动；"
                f"证据见 {report.log_path}",
                flush=True,
            )
        # 进程可能还要继续被使用（测试/同进程重建应用）：解除 draining，
        # 否则本进程后续所有请求都会被 503。生产里此刻本就要退出，重置无副作用。
        from deepquant.safety.lifecycle import INFLIGHT

        INFLIGHT.reset()


def create_app() -> FastAPI:
    app = FastAPI(title="DeepQuant API", version=__version__, lifespan=_lifespan)
    register_exception_handlers(app)
    # 每个响应带构建指纹：启动器据此判断「端口上那个实例是不是这份代码」。
    # 只加响应头，不改任何响应体与路径，契约保持原样。
    app.add_middleware(BuildHeaderMiddleware, build=build_id())
    # T2 风控网关（内层）：仅在 DEEPQUANT_RISK_MODE=observe/enforce 时生效。
    app.add_middleware(RiskGateMiddleware, lookups=_risk_lookups(app))
    # T4 能力闸门（中间层）：仅在 DEEPQUANT_CAPABILITY_MODE=observe/enforce 时生效。
    # 位于风控**外层**：能力关闭/未授权比"请求本身不合法"更前置，被关掉的功能
    # 不该先跑一遍数据库查询再拒绝。位于审计**内层**：拒绝同样要留痕。
    app.add_middleware(CapabilityGateMiddleware)
    # T5 安全层（熔断层）：管"系统整体是否暂停"（熔断）与在飞计数。
    # 位于能力外层：整个功能被熔断时不必再逐能力判定；位于审计内层：熔断拒绝也留痕。
    app.add_middleware(SafetyMiddleware)
    # T1 审计（外层）：仅在设置 DEEPQUANT_AUDIT_DIR 时生效。
    # 未设置时中间件立刻放行（不读请求体、不建目录、不加响应头），
    # 既有部署与测试的行为完全不变；详见 deepquant.audit.middleware 模块说明。
    app.add_middleware(AuditMiddleware)
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
