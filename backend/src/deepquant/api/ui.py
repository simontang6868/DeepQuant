"""把前端构建产物挂进 API 进程 —— 「双击就能用」的单端口形态。

为什么单独一个模块、且默认关闭
-------------------------------
`backend` 的 API 契约（`/health` `/datasets` `/strategies` `/backtests`）是测试与
前端共同依赖的东西，不该因为「想让用户少开一个终端」而改变。所以：

- **默认不挂载**：不设置 ``DEEPQUANT_SERVE_UI=1`` 时，行为与以往逐字节一致，
  未知路径照旧 404，全部既有测试不受影响；
- 设置后才挂载 ``frontend/dist`` 到 ``/``，并启用 ``/api`` 前缀剥离，
  使前端构建产物里的 ``/api/xxx`` 请求在同一端口命中真实路由。

``/api`` 前缀的存在不是为了好看：前端的开发代理把 ``/api`` 转发到后端并去掉前缀
（见 `frontend/vite.config.ts`），构建产物里写死的是 ``/api/...``。单端口形态下没有
代理，只能由服务端把前缀剥掉 —— 否则要么改前端构建配置（产物只能用于单端口），
要么开 CORS（多一个可被任意网页调用的面）。剥前缀是三者里唯一不引入新暴露面的做法。

挂载静态目录会带来三个副作用，本模块逐个处理：

1. **``/`` 之下的挂载会吞掉所有未命中路径**。Starlette 的路由是「先命中先返回」，
   ``Mount("/")`` 排在最后但能匹配任意路径，于是 ``/api`` 这种「前缀本身」的请求
   会被它接住并返回 ``index.html``（200 + HTML）。一个「探活」脚本会就此认定
   API 可用。→ 前缀恰好等于 ``/api`` 时直接返回 JSON 404，不落到静态资源。
2. **尾斜杠不再触发 307**。``/api/backtests/`` 交给路由层时会重定向到 ``/backtests``，
   但 ``Mount("/")`` 先接住了它（404）。→ 剥前缀时顺手归一化尾斜杠。
3. **静态资源没有缓存策略**。``dist`` 里的资源名是内容哈希，可以永久缓存；
   而 ``index.html`` 一旦被浏览器启发式缓存，重建后会指向已删除的旧哈希文件，
   表现为白屏。→ 用中间件显式区分两类缓存语义。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: 开启单端口 UI 的环境变量。值需为 1/true/yes/on 之一（大小写不敏感）。
UI_ENV_VAR = "DEEPQUANT_SERVE_UI"

#: 前端构建产物里请求的固定前缀，单端口形态下由 `StripApiPrefix` 剥掉。
API_PREFIX = "/api"

#: 裸 ``/api`` 的响应体，与 FastAPI 自身的 404 结构保持一致。
_NOT_FOUND_BODY = b'{"detail":"Not Found"}'

#: 内容哈希资源可以永久缓存；其余（含 index.html）必须每次回源校验。
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
REVALIDATE_CACHE = "no-cache"

_TRUE_VALUES = {"1", "true", "yes", "on"}


def ui_dist_dir() -> Path:
    """前端构建产物目录（由本文件位置锚定，不依赖启动时的工作目录）。"""
    return Path(__file__).resolve().parents[4] / "frontend" / "dist"


def ui_enabled() -> bool:
    """是否显式要求挂载 UI。"""
    return os.environ.get(UI_ENV_VAR, "").strip().lower() in _TRUE_VALUES


def _normalize_api_path(path: str) -> str | None:
    """把 ``/api/xxx`` 归一化成真实路由路径；``/api`` 本身返回 ``None``。

    - ``/api/health`` → ``/health``
    - ``/api/backtests/`` → ``/backtests``（避免 ``Mount("/")`` 抢在 307 之前返回 404）
    - ``/api`` / ``/api/`` → ``None``（不是页面，也不是路由）
    """
    rest = path[len(API_PREFIX):] or "/"
    if rest == "/":
        return None
    if rest.endswith("/"):
        rest = rest.rstrip("/") or "/"
    return rest


async def _reply_json_404(send, *, with_body: bool) -> None:  # noqa: ANN001 - ASGI send
    """直接回一个与 FastAPI 一致的 JSON 404。

    这里不走 ``JSONResponse``：本类是被 ``add_middleware`` 装上的纯 ASGI 中间件，
    在中间件里构造 Response 需要自己处理 ``scope`` 与 ``receive``，反而更容易出错。
    """
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(_NOT_FOUND_BODY)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    await send({"type": "http.response.start", "status": 404, "headers": headers})
    await send(
        {"type": "http.response.body", "body": _NOT_FOUND_BODY if with_body else b""}
    )


class StripApiPrefix:
    """把 ``/api/xxx`` 改写成 ``/xxx`` 的纯 ASGI 中间件。

    只改 ``path``/``raw_path``，不动请求体、不缓冲响应 —— 回测响应可达数百 KB，
    缓冲型中间件会让「同步回测 0.3 秒」变成「先攒完再吐」。
    """

    def __init__(self, app) -> None:  # noqa: ANN001 - ASGI app 是可调用对象
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path != API_PREFIX and not path.startswith(API_PREFIX + "/"):
            await self.app(scope, receive, send)
            return

        stripped = _normalize_api_path(path)
        if stripped is None:
            if scope["type"] == "websocket":  # pragma: no cover - 当前无 ws 路由
                await send({"type": "websocket.close", "code": 1008})
                return
            await _reply_json_404(send, with_body=scope.get("method") != "HEAD")
            return

        scope = dict(scope)
        scope["path"] = stripped
        scope["raw_path"] = stripped.encode("utf-8")
        await self.app(scope, receive, send)


class StaticCachePolicy:
    """给静态产物补上缓存语义。

    ``/assets/*`` 的文件名带内容哈希，改内容必然改名，所以可以永久缓存；
    其余（``index.html``、``favicon.svg`` 等）必须每次回源，否则重建后页面会
    指向已不存在的旧哈希文件，表现为「白屏，但服务器日志全是 200」。
    """

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        cache = (
            IMMUTABLE_CACHE
            if scope.get("path", "").startswith("/assets/")
            else REVALIDATE_CACHE
        )

        async def send_with_cache(message):  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", cache.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache)


def mount_ui(app: FastAPI, dist_dir: Path | None = None) -> bool:
    """按需把前端构建产物挂到 ``/``。返回是否真的挂载。

    三种「不挂载」都**不抛异常**：没开开关、产物不存在、产物缺 ``index.html``。
    启动脚本会在挂载失败时明确提示「先构建前端」，这比在 import 阶段炸掉更有用 ——
    用户看到的是「后端起来了但界面没开」，而不是一整页 traceback。
    """
    if not ui_enabled():
        return False

    dist = (dist_dir or ui_dist_dir()).resolve()
    if not (dist / "index.html").is_file():
        # 显式要了 UI 却拿不到产物：留一条 warning，避免「起了但没界面」无从排查。
        logger.warning(
            "已设置 %s=1，但 %s 下没有 index.html；本次不挂载界面。"
            "请先在 frontend/ 执行 npm run build。",
            UI_ENV_VAR,
            dist,
        )
        return False

    from fastapi.staticfiles import StaticFiles

    # 中间件按「后加入者在外层」组装：先加缓存策略，再加前缀剥离，
    # 这样剥离先生效，缓存策略看到的是归一化后的路径（/assets/... 判断才准）。
    app.add_middleware(StaticCachePolicy)
    app.add_middleware(StripApiPrefix)
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="ui")
    return True
