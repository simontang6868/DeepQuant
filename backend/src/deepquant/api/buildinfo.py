"""构建指纹：让「正在运行的那个进程」能自证它跑的是哪一份代码。

为什么需要
----------
启动器（`scripts/start_deepquant.py`）会在目标端口已被本应用占用时**复用**已有实例，
避免双击两次起两个进程抢同一个 SQLite。但「是不是本应用」和「是不是这份代码」是两个
问题：改完代码后旧进程还在跑，复用会把用户带回旧界面，而用户以为自己看到的是新版本。

`/health` 的状态码与 `/openapi.json` 的标题只回答第一个问题，两者都不随代码变化。
所以在每个响应上带一个由**磁盘文件状态**算出的指纹，启动器拿它和本地对比：
一致才复用；不一致（或对方是旧版本、根本没有这个头）就另起一个端口，并提示关闭旧窗口。
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

#: 响应头名称。启动器读它，测试钉它。
BUILD_HEADER = "X-DeepQuant-Build"

#: 允许外部覆盖（打包分发、容器构建时可用固定 ID）。
BUILD_ENV_VAR = "DEEPQUANT_BUILD_ID"

#: 参与指纹的文件后缀与路径。
_SOURCE_SUFFIXES = (".py",)


def repo_root() -> Path:
    """仓库根目录（由本文件位置锚定：backend/src/deepquant/api/buildinfo.py）。"""
    return Path(__file__).resolve().parents[4]


def _collect(root: Path) -> list[str]:
    """收集「改了就该算新版本」的文件状态。

    只覆盖**服务端代码 + 前端产物**：
    - `backend/src/**/*.py`：服务端行为；
    - `frontend/dist/index.html`：界面产物（资源的哈希名写在里面，改产物必改它）。

    刻意**不包含启动脚本**：它是每次双击都新起的进程，改了它不影响已经在跑的服务，
    把它算进指纹只会让「改了个提示文案」也被判成旧版本，端口一路上涨。

    只用 size + mtime_ns，不读内容：目录里两百来个文件，读内容会让启动慢一个量级，
    而「文件被改写」这个事实已经足够区分版本。副作用是「touch 一下也算新版本」，
    方向是保守的（宁可多起一个实例，也不让用户对着旧界面以为修复没生效）。
    """
    items: list[str] = []
    for path in sorted((root / "backend" / "src").rglob("*.py")):
        try:
            st = path.stat()
        except OSError:  # pragma: no cover - 文件在遍历中被删
            continue
        items.append(f"{path.relative_to(root).as_posix()}|{st.st_size}|{st.st_mtime_ns}")

    for extra in (root / "frontend" / "dist" / "index.html",):
        if extra.is_file():
            st = extra.stat()
            items.append(f"{extra.relative_to(root).as_posix()}|{st.st_size}|{st.st_mtime_ns}")
    return items


@lru_cache(maxsize=1)
def build_id() -> str:
    """16 位十六进制指纹。同一次启动内稳定，代码或产物变动后改变。"""
    override = os.environ.get(BUILD_ENV_VAR, "").strip()
    if override:
        return override[:32]
    payload = "\n".join(_collect(repo_root()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class BuildHeaderMiddleware:
    """给每个响应加 ``X-DeepQuant-Build``。

    指纹在构造时算一次并缓存，不在每个请求上 stat 磁盘。
    """

    def __init__(self, app, build: str | None = None) -> None:  # noqa: ANN001
        self.app = app
        self.build = build or build_id()

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        value = self.build.encode("ascii", "ignore")

        async def send_with_header(message):  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != BUILD_HEADER.encode().lower()
                ]
                headers.append((BUILD_HEADER.encode("ascii"), value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_header)
