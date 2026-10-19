"""DeepQuant 一键启动器：起后端 + 挂前端 + 开浏览器。

设计取舍（为什么不是「再写个 docker-compose」）
------------------------------------------------
用户的场景是「在自己电脑上双击一个东西，然后能用」。所以：

1. **不引入新依赖**：只用标准库 + 已装好的 uvicorn。没有 npm 也能跑（没有前端产物时
   退化为纯 API，并在控制台说明缺什么）。
2. **不改成 dev server**：直接跑构建产物（`frontend/dist`），端口单一，不需要同时开两个
   终端、不需要 Vite 代理。前端产物里的 `/api/...` 由 `deepquant.api.ui` 剥前缀转成真路由。
3. **端口冲突不让用户猜**：默认 8123，被占用就往后找空端口，并把最终地址打印出来。
   若占用者**本身就是一个 DeepQuant**，则不另起实例，直接把浏览器指过去 —— 双击两次
   不该得到两个进程抢同一个 SQLite。
4. **启动完再开浏览器**：轮询 `/health` 成功后才 `webbrowser.open`，避免用户先看到
   「无法连接后端」。
5. **绑非本机地址时明确警告**：本应用没有认证，任何能访问该端口的人都能跑回测、
   读历史记录。这个后果必须打印出来，不能靠用户自己想到。

用法::

    python scripts/start_deepquant.py                 # 默认 8123，自动开浏览器
    python scripts/start_deepquant.py --port 9000     # 指定端口
    python scripts/start_deepquant.py --no-browser    # 只起服务
    python scripts/start_deepquant.py --check         # 只做自检，不启动
    python scripts/start_deepquant.py --new-instance  # 即使已有实例也另起一个
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
FRONTEND_DIR = REPO_ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"

DEFAULT_PORT = 8123
PORT_SCAN_LIMIT = 20
HEALTH_POLL_TIMEOUT_S = 25.0

#: 与 `deepquant.api.app` 里的 ``FastAPI(title=...)`` 保持一致，用于识别「本应用」。
API_TITLE = "DeepQuant API"

#: 与 `deepquant.api.buildinfo.BUILD_HEADER` 一致（有测试钉住两者相等）。
BUILD_HEADER = "X-DeepQuant-Build"

#: 只监听这些地址时，服务仅本机可达，不需要额外提醒。
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


# --------------------------------------------------------------------------- #
# 环境探测（纯函数，测试直接打这些）
# --------------------------------------------------------------------------- #


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """尝试绑定来判断端口是否可用。

    不用 ``connect_ex`` 探测：本机可能装了「连得上但不是我」的服务，
    那种情况下照样会 bind 失败，不如直接试绑。

    也不设 ``SO_REUSEADDR``：Windows 上它允许两个 socket 绑同一端口（劫持语义），
    会让「其实被占用」被误判成空闲，接着 uvicorn 抛 WinError 10048 —— 用户看到的是
    「启动失败」而不是「自动换端口」。宁可把 TIME_WAIT 的端口判为忙（保守方向）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(start: int = DEFAULT_PORT, *, limit: int = PORT_SCAN_LIMIT) -> int:
    """从 ``start`` 起找第一个空闲端口；找不到返回 ``start``（交由 uvicorn 报错）。"""
    for port in range(start, start + limit):
        if is_port_free(port):
            return port
    return start


def find_npm() -> str | None:
    """找 npm（Windows 上是 npm.cmd）。找不到返回 None，不抛。"""
    for name in ("npm", "npm.cmd"):
        path = shutil.which(name)
        if path:
            return path
    return None


def dist_ready(dist_dir: Path | None = None) -> bool:
    """前端产物是否可用（必须有 index.html，空目录不算）。"""
    return ((dist_dir or DIST_DIR) / "index.html").is_file()


def resolve_data_root(repo_root: Path | None = None) -> Path:
    """确定行情数据集根目录。

    优先级：环境变量 → 仓库 ``data/``（若已登记数据集）→ ``backend/data/``。
    为什么要挑：API 内置默认是 ``backend/data/``，但**已导入的数据集在仓库根 ``data/``**
    （``metadata/*.meta.json`` + ``processed/*.parquet``）。不挑就会得到一个「界面正常、
    但数据集下拉框是空的」的应用 —— 那种状态最容易被误判成「软件坏了」。
    """
    env = os.environ.get("DEEPQUANT_DATA_ROOT")
    if env and env.strip():
        return Path(env).expanduser().resolve()

    root = repo_root or REPO_ROOT
    for candidate in (root / "data", root / "backend" / "data"):
        if candidate.is_dir() and any(candidate.glob("metadata/*.meta.json")):
            return candidate.resolve()
    return (root / "data").resolve()


def build_frontend(*, timeout: int = 600) -> bool:
    """没有产物时尝试构建。没装 node/npm 就返回 False，由调用方给出人话提示。"""
    npm = find_npm()
    if npm is None:
        return False
    try:
        subprocess.run(  # noqa: S603 - 参数固定，无 shell
            [npm, "run", "build"],
            cwd=str(FRONTEND_DIR),
            check=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return dist_ready()


def wait_for_health(port: int, *, timeout: float = HEALTH_POLL_TIMEOUT_S) -> bool:
    """轮询 /health 直到成功或超时。"""
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:  # noqa: S310 - 本机固定地址
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.25)
    return False


def is_deepquant_running(port: int, *, timeout: float = 1.5) -> bool:
    """判断该端口上跑的是不是**本应用**（而不只是「有东西在监听」）。

    只看 ``/health`` 返回 200 不够：局域网里任何一个健康检查接口都会满足这个条件，
    于是「端口被别的服务占了」会被误判成「DeepQuant 已在运行」，浏览器被导向一个
    陌生页面。所以要求 ``/openapi.json`` 的 ``info.title`` 精确等于本应用的标题。
    """
    url = f"http://127.0.0.1:{port}/openapi.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 本机固定地址
            if resp.status != 200:
                return False
            doc = json.loads(resp.read())
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False
    info = doc.get("info") if isinstance(doc, dict) else None
    return isinstance(info, dict) and info.get("title") == API_TITLE


def open_browser(url: str) -> None:
    """尽力打开浏览器；失败不影响服务（无桌面环境、无默认浏览器都会走到这里）。"""
    with contextlib.suppress(Exception):
        import webbrowser

        webbrowser.open(url)


def local_build_id() -> str | None:
    """当前磁盘代码的构建指纹；后端不可导入时返回 None。"""
    try:
        from deepquant.api.buildinfo import build_id

        return build_id()
    except Exception:  # noqa: BLE001 - 指纹只用于「要不要复用」，失败就当作未知
        return None


def probe_instance(port: int, *, timeout: float = 1.5) -> tuple[bool, str | None]:
    """探测端口上的实例，返回 (是否本应用, 对方构建指纹)。

    指纹来自 ``X-DeepQuant-Build`` 响应头。旧版本没有这个头，此时回落到
    ``/openapi.json`` 的标题识别——能认出「是本应用」，但认不出「是哪一份代码」，
    于是返回 ``None``，调用方按「版本不一致」处理（保守方向）。
    """
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 本机固定地址
            if resp.status != 200:
                return False, None
            build = resp.headers.get(BUILD_HEADER)
    except (urllib.error.URLError, OSError, TimeoutError):
        return False, None
    if build:
        return True, build
    return is_deepquant_running(port, timeout=timeout), None


def serves_ui(port: int, *, timeout: float = 1.5) -> bool:
    """该实例是否真的在 `/` 上提供界面（200 + text/html）。

    为什么复用前要查这个：同一个进程也可以用 ``uvicorn deepquant.api.app:app`` 起，
    那时**构建指纹完全一致**，但 ``DEEPQUANT_SERVE_UI`` 没设，`/` 是 404。
    只比指纹就复用，用户会打开一个「只有 API、没有界面」的地址，然后判定软件坏了。
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as resp:  # noqa: S310
            content_type = resp.headers.get("content-type") or ""
            return resp.status == 200 and content_type.startswith("text/html")
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def check_existing_instance(port: int) -> str:
    """端口上已有实例时该怎么办：``none`` / ``reuse`` / ``stale`` / ``no-ui``。

    ``stale`` = 是本应用、但不是这份代码（必须另起，否则用户对着旧界面以为修复没生效）。
    ``no-ui`` = 是这份代码、但没挂界面（同样必须另起，否则用户打开只看到 JSON 404）。
    """
    running, remote_build = probe_instance(port)
    if not running:
        return "none"
    local_build = local_build_id()
    if local_build is None or remote_build != local_build:
        return "stale"
    if not serves_ui(port):
        return "no-ui"
    return "reuse"




# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #


def _banner(port: int, ui: bool, data_root: Path, db_path: Path, host: str = "127.0.0.1") -> str:
    shown_host = "127.0.0.1" if host in LOOPBACK_HOSTS else host
    base = f"http://{shown_host}:{port}"
    lines = [
        "",
        "=" * 66,
        "  DeepQuant 已启动",
        "=" * 66,
        f"  界面      {base}" if ui else "  界面      未启用（无前端构建产物）",
        f"  API 文档  {base}/docs",
        f"  健康检查  {base}/health",
        f"  数据集    {data_root}",
        f"  历史记录  {db_path}",
        "",
        "  按 Ctrl+C 停止",
    ]
    if host not in LOOPBACK_HOSTS:
        lines += [
            "",
            "  [!] 已监听 " + host + "：同网段任何人都能访问本应用。",
            "      本应用没有登录与权限控制，可读取全部回测记录、发起任意回测。",
            "      仅在可信网络中使用；只想自己用请删掉 --host 参数。",
        ]
    lines += ["=" * 66, ""]
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DeepQuant 一键启动（单端口，含前端界面）")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"首选端口（默认 {DEFAULT_PORT}）")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.add_argument("--no-build", action="store_true", help="前端产物缺失时不要自动构建")
    p.add_argument("--check", action="store_true", help="只自检环境，不启动服务")
    p.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    p.add_argument(
        "--new-instance",
        action="store_true",
        help="即使目标端口上已有 DeepQuant 也另起一个实例（默认直接复用已有实例）",
    )
    return p.parse_args(argv)


def _prepare(
    argv: Sequence[str] | None = None, *, args: argparse.Namespace | None = None
) -> tuple[argparse.Namespace, bool, int]:
    """公共前置：解析参数、确保前端产物、选端口。返回 (args, ui_ready, port)。"""
    args = args or _parse_args(argv)

    ui_ready = dist_ready()
    if not ui_ready and not args.no_build and not args.check:
        print("[BUILD] 前端产物缺失，尝试构建 frontend/dist ……")
        ui_ready = build_frontend()
        print("[BUILD] 构建成功" if ui_ready else "[BUILD] 构建失败或缺少 npm，界面将不可用")

    # --check 只报告，不启动：应当诊断**用户问的那个端口**，而不是「我会改用哪个端口」。
    # （否则输出里会出现「8123 已被占用，改用 8124」，紧接着又说「端口 8124 空闲」，
    #   读起来像自相矛盾。）
    if args.check:
        return args, ui_ready, args.port

    port = pick_port(args.port)
    if port != args.port:
        print(f"[PORT ] {args.port} 已被占用，改用 {port}")
    return args, ui_ready, port


def main(argv: Sequence[str] | None = None) -> int:
    # 输出被重定向到文件/管道时，Python 默认按块缓冲：启动提示会压到最后才出现，
    # 用户（或排障的人）看到的是一个「什么都不打印」的窗口。改为行缓冲。
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(line_buffering=True)

    args = _parse_args(argv)

    if str(BACKEND_SRC) not in sys.path:
        sys.path.insert(0, str(BACKEND_SRC))

    # 已有一个**同一份代码**的本应用在跑：直接复用，不再起第二个进程。
    # 为什么重要：两个实例共用同一个 SQLite 与同一个端口区间，用户会看到两个窗口、
    # 两份互不可见的历史写入，还会以为「软件出问题了」。
    # 为什么必须比指纹：改完代码后旧进程还在跑，复用会把用户带回旧界面，
    # 而用户以为自己看到的是修复后的版本 —— 这比多起一个进程更难排查。
    if not args.check and not args.new_instance:
        state = check_existing_instance(args.port)
        if state == "reuse":
            url = f"http://127.0.0.1:{args.port}"
            print(f"[REUSE] {args.port} 端口上已有一个同版本 DeepQuant 在运行：{url}")
            print("        已直接指向该实例；如需再起一个，请加 --new-instance")
            if not args.no_browser:
                open_browser(url)
            return 0
        if state == "stale":
            print(f"[STALE] {args.port} 端口上的 DeepQuant 与当前文件不是同一版本，将另起一个实例。")
            print("        请回到原来那个命令行窗口按 Ctrl+C 关闭旧实例，再刷新浏览器。")
        elif state == "no-ui":
            print(f"[NOUI ] {args.port} 端口上的 DeepQuant 没有启用界面（可能由裸 uvicorn 启动），将另起一个实例。")

    args, ui_ready, port = _prepare(args=args)

    if not args.check and not is_port_free(port):
        lo, hi = args.port, args.port + PORT_SCAN_LIMIT - 1
        print(f"[FATAL] 端口 {lo}~{hi} 全部被占用，无法启动。")
        print("        请先关闭占用程序，或用 --port 指定一个空闲端口。")
        return 1

    # 数据根目录/数据库沿用既有默认解析（backend/data、backend/data/backtests.db），
    # 只把「是否挂 UI」这一个开关交给启动器。
    if ui_ready:
        os.environ["DEEPQUANT_SERVE_UI"] = "1"
    else:
        os.environ.pop("DEEPQUANT_SERVE_UI", None)

    try:
        import uvicorn
        from deepquant.api.app import create_app
        from deepquant.persistence.connection import get_db_path
    except Exception as exc:  # noqa: BLE001 - 自检失败要给出可读原因
        print(f"[FATAL] 导入失败：{exc}")
        print("        请确认已在项目 venv 中运行，或先 pip install -r backend/requirements.txt")
        return 2

    # 数据集根目录必须在建 app / 接受请求之前定下来：API 每次请求都会读这个环境变量。
    data_root = resolve_data_root()
    os.environ["DEEPQUANT_DATA_ROOT"] = str(data_root)
    db_path = get_db_path()

    if args.check:
        app = create_app()
        # 注意：不要用 app.routes 列路径。当前 FastAPI 版本把 include_router 包成
        # `_IncludedRouter`（无 path 属性），直接读会只看到 /docs 等自带路由，
        # 看上去像「业务路由没注册」。openapi() 是唯一可靠的清单来源。
        paths = sorted(app.openapi().get("paths", {}))
        n_datasets = len(list(data_root.glob("metadata/*.meta.json")))
        # 真正的挂载结果，而不是「dist 里有没有 index.html」——这两件事中间还差
        # 一次 `mount_ui` 调用，必须验证它真的生效了。
        ui_mounted = any(getattr(r, "name", None) == "ui" for r in app.routes)
        strips_api = any(
            getattr(getattr(m, "cls", None), "__name__", "") == "StripApiPrefix"
            for m in app.user_middleware
        )
        port_note = (
            "已有 DeepQuant 在运行（将复用）"
            if is_deepquant_running(port)
            else ("被其他程序占用" if not is_port_free(port) else "空闲")
        )
        print("[CHECK] 前端产物      ", "OK" if ui_ready else "缺失（界面不可用）")
        print("[CHECK] 单端口挂载    ", "OK" if ui_mounted else "未挂载（界面不可用）")
        print("[CHECK] /api 前缀剥离 ", "OK" if strips_api else "未启用")
        print("[CHECK] 数据根目录    ", data_root, "OK" if data_root.is_dir() else "不存在")
        print("[CHECK] 已登记数据集  ", n_datasets, "个")
        print("[CHECK] 数据库        ", db_path)
        print("[CHECK] 端口          ", port, f"（{port_note}）")
        print("[CHECK] API 路由      ", ", ".join(paths))
        return 0 if (ui_ready and ui_mounted and n_datasets > 0) else 1

    app = create_app()
    print(_banner(port, ui_ready, data_root, db_path, host=args.host))

    if not args.no_browser:
        def _open_when_ready() -> None:
            if wait_for_health(port):
                open_browser(f"http://127.0.0.1:{port}")
            else:
                print("[WARN ] 健康检查未在超时内成功，未自动打开浏览器")

        threading.Thread(target=_open_when_ready, daemon=True).start()

    try:
        uvicorn.run(app, host=args.host, port=port, log_level="info")
    except SystemExit as exc:  # uvicorn 绑定失败时走 sys.exit(1)
        code = exc.code if isinstance(exc.code, int) else 1
        if code != 0:
            print(f"[FATAL] 服务异常退出（code={code}）。端口 {port} 可能刚刚被其他程序占用。")
        return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
