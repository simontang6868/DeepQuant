"""一键启动器（`scripts/start_deepquant.py`）的契约测试。

这个脚本是用户唯一会双击的东西，它出错的表现往往不是报错，而是**看起来起来了但不对**：

- 端口判断错 → uvicorn 抛 WinError 10048，用户只看到崩溃；
- 数据根挑错 → 界面正常、数据集下拉框空着，被当成「软件坏了」；
- 产物缺失却仍宣称有界面 → 用户打开浏览器看到 404。

所以这里测的是上面三件事的判定逻辑，不启动真实服务、不占端口。
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "start_deepquant.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("start_deepquant_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()


# --------------------------------------------------------------------------- #
# 1. 端口选择
# --------------------------------------------------------------------------- #


def test_pick_port_skips_busy_ports(monkeypatch):
    busy = {8123, 8124}
    monkeypatch.setattr(launcher, "is_port_free", lambda port, host="127.0.0.1": port not in busy)
    assert launcher.pick_port(8123) == 8125


def test_pick_port_returns_start_when_all_busy(monkeypatch):
    """全忙时返回首选端口，让 uvicorn 自己报错——比编一个「随机端口」更好排查。"""
    monkeypatch.setattr(launcher, "is_port_free", lambda port, host="127.0.0.1": False)
    assert launcher.pick_port(8123, limit=1) == 8123


def test_is_port_free_detects_listening_socket():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert launcher.is_port_free(port) is False


# --------------------------------------------------------------------------- #
# 2. 数据根目录
# --------------------------------------------------------------------------- #


def _fake_repo(tmp_path: Path, *, with_datasets: bool = True) -> Path:
    root = tmp_path / "repo"
    (root / "backend" / "data").mkdir(parents=True)
    if with_datasets:
        (root / "data" / "metadata").mkdir(parents=True)
        (root / "data" / "metadata" / "600519.SH.meta.json").write_text("{}", encoding="utf-8")
    return root


def test_resolve_data_root_prefers_repo_data_with_datasets(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPQUANT_DATA_ROOT", raising=False)
    root = _fake_repo(tmp_path)
    assert launcher.resolve_data_root(root) == (root / "data").resolve()


def test_resolve_data_root_honours_env(tmp_path, monkeypatch):
    custom = tmp_path / "elsewhere"
    monkeypatch.setenv("DEEPQUANT_DATA_ROOT", str(custom))
    assert launcher.resolve_data_root(tmp_path) == custom.resolve()


def test_resolve_data_root_falls_back_when_no_datasets(tmp_path, monkeypatch):
    """仓库 data/ 里没有已登记数据集时，回落到 <repo>/data（而不是报错）。"""
    monkeypatch.delenv("DEEPQUANT_DATA_ROOT", raising=False)
    root = _fake_repo(tmp_path, with_datasets=False)
    assert launcher.resolve_data_root(root) == (root / "data").resolve()


# --------------------------------------------------------------------------- #
# 3. 前端产物判定
# --------------------------------------------------------------------------- #


def test_dist_ready_requires_index_html(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    assert launcher.dist_ready(dist) is False
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    assert launcher.dist_ready(dist) is True


def test_prepare_respects_no_build(tmp_path, monkeypatch):
    """``--no-build`` 下产物缺失不得偷偷跑 npm（构建可能耗时数分钟）。"""
    monkeypatch.setattr(launcher, "DIST_DIR", tmp_path / "missing-dist")
    monkeypatch.setattr(launcher, "is_port_free", lambda port, host="127.0.0.1": True)

    def _boom(**kwargs):  # pragma: no cover - 走到这里就是失败
        raise AssertionError("--no-build 下不应触发构建")

    monkeypatch.setattr(launcher, "build_frontend", _boom)
    args, ui_ready, port = launcher._prepare(["--no-build", "--port", "8899"])
    assert args.no_build is True
    assert ui_ready is False
    assert port == 8899


# --------------------------------------------------------------------------- #
# 4. 参数与环境
# --------------------------------------------------------------------------- #


def test_default_args():
    args = launcher._parse_args([])
    assert args.port == launcher.DEFAULT_PORT
    assert args.no_browser is False
    assert args.host == "127.0.0.1"


def test_repo_paths_are_anchored_to_file_location():
    """路径必须由文件位置推导：双击 .bat 时的当前目录是随机的。"""
    assert (launcher.REPO_ROOT / "backend" / "src").is_dir()
    assert launcher.DIST_DIR.parent == launcher.FRONTEND_DIR


def test_find_npm_returns_optional_path():
    found = launcher.find_npm()
    assert found is None or Path(found).name.lower().startswith("npm")


# --------------------------------------------------------------------------- #
# 5. 实例复用与启动失败（2026-10-18 复审新增）
#
# 双击两次是真实会发生的动作。此前第二次会静默换端口再起一个进程：两个窗口、
# 共用同一个 SQLite、历史列表互相看不到对方的写入，用户只会觉得「软件乱了」。
# 判定「是不是本应用」也不能只看 /health 200 —— 任何服务都能满足。
# --------------------------------------------------------------------------- #


def _serve_once(payload: bytes, content_type: str = "application/json", headers: dict | None = None):
    """起一个只回固定内容的本地 HTTP 服务，返回 (port, shutdown)。"""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server 约定
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # noqa: D401 - 静音
            return

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1], srv.shutdown


def test_is_deepquant_running_accepts_only_our_openapi_title():
    import json

    port, stop = _serve_once(
        json.dumps({"openapi": "3.1.0", "info": {"title": "DeepQuant API"}}).encode()
    )
    try:
        assert launcher.is_deepquant_running(port) is True
    finally:
        stop()


def test_is_deepquant_running_rejects_other_services():
    """别的服务也可能 /health 200 —— 只看状态码就会把用户导向陌生页面。"""
    import json

    port, stop = _serve_once(
        json.dumps({"openapi": "3.0.0", "info": {"title": "Some Other App"}}).encode()
    )
    try:
        assert launcher.is_deepquant_running(port) is False
    finally:
        stop()


def test_is_deepquant_running_rejects_non_json_response():
    port, stop = _serve_once(b"<html>hello</html>", content_type="text/html")
    try:
        assert launcher.is_deepquant_running(port) is False
    finally:
        stop()


def test_is_deepquant_running_false_when_nothing_listens():
    """关闭的端口要快速返回 False，不能挂在连接超时上拖慢启动。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert launcher.is_deepquant_running(port, timeout=0.5) is False


def test_main_reuses_existing_instance(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(launcher, "check_existing_instance", lambda port: "reuse")
    monkeypatch.setattr(launcher, "open_browser", opened.append)

    assert launcher.main([]) == 0
    assert opened == [f"http://127.0.0.1:{launcher.DEFAULT_PORT}"]


def test_main_reuse_respects_no_browser(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(launcher, "check_existing_instance", lambda port: "reuse")
    monkeypatch.setattr(launcher, "open_browser", opened.append)

    assert launcher.main(["--no-browser"]) == 0
    assert opened == []


def test_new_instance_flag_bypasses_reuse(monkeypatch):
    args = launcher._parse_args(["--new-instance"])
    assert args.new_instance is True
    assert launcher._parse_args([]).new_instance is False


def test_main_reports_all_ports_busy_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "check_existing_instance", lambda port: "none")
    monkeypatch.setattr(launcher, "dist_ready", lambda dist=None: True)
    monkeypatch.setattr(launcher, "is_port_free", lambda port, host="127.0.0.1": False)

    assert launcher.main(["--no-build"]) == 1
    out = capsys.readouterr().out
    assert "全部被占用" in out
    assert "--port" in out


def test_banner_warns_when_binding_non_loopback():
    """绑 0.0.0.0 等于把「无认证的回测台」发给同网段所有人，必须显式警告。"""
    from pathlib import Path

    warned = launcher._banner(8123, True, Path("d"), Path("x.db"), host="0.0.0.0")
    assert "同网段" in warned and "没有登录" in warned

    local = launcher._banner(8123, True, Path("d"), Path("x.db"), host="127.0.0.1")
    assert "同网段" not in local


# --------------------------------------------------------------------------- #
# 6. 版本一致性（2026-10-18 复审新增）
#
# 「端口上有一个 DeepQuant」不等于「端口上跑的是这份代码」。改完之后复用旧进程，
# 用户看到的是旧界面，却以为修复没生效——这类问题比多起一个进程难查得多。
# --------------------------------------------------------------------------- #


def test_build_header_constant_matches_backend():
    """启动器里的字面量必须与后端常量一致，否则识别永远失败（假 stale）。"""
    from deepquant.api.buildinfo import BUILD_HEADER

    assert launcher.BUILD_HEADER == BUILD_HEADER


def test_probe_instance_reads_build_header():
    port, stop = _serve_once(b'{"status":"ok"}', headers={"X-DeepQuant-Build": "abc123"})
    try:
        running, build = launcher.probe_instance(port)
        assert running is True
        assert build == "abc123"
    finally:
        stop()


def test_probe_instance_returns_unknown_build_for_legacy_server():
    """旧版本没有这个头：能认出是本应用，但认不出是哪份代码。"""
    import json

    port, stop = _serve_once(
        json.dumps({"info": {"title": "DeepQuant API"}}).encode()
    )
    try:
        running, build = launcher.probe_instance(port)
        assert running is True
        assert build is None
    finally:
        stop()


def test_probe_instance_ignores_unrelated_server():
    port, stop = _serve_once(b"ok", content_type="text/plain")
    try:
        assert launcher.probe_instance(port) == (False, None)
    finally:
        stop()


def test_check_existing_instance_reuse_only_on_equal_build(monkeypatch):
    monkeypatch.setattr(launcher, "local_build_id", lambda: "same")
    monkeypatch.setattr(launcher, "serves_ui", lambda port, **kw: True)

    monkeypatch.setattr(launcher, "probe_instance", lambda port, **kw: (True, "same"))
    assert launcher.check_existing_instance(8123) == "reuse"

    monkeypatch.setattr(launcher, "probe_instance", lambda port, **kw: (True, "other"))
    assert launcher.check_existing_instance(8123) == "stale"

    monkeypatch.setattr(launcher, "probe_instance", lambda port, **kw: (True, None))
    assert launcher.check_existing_instance(8123) == "stale", "认不出指纹时必须保守地另起"

    monkeypatch.setattr(launcher, "probe_instance", lambda port, **kw: (False, None))
    assert launcher.check_existing_instance(8123) == "none"


def test_check_existing_instance_rejects_same_build_without_ui(monkeypatch):
    """同版本但用裸 uvicorn 起的实例：指纹一致、`/` 却是 404，复用等于给用户一个 JSON 页面。"""
    monkeypatch.setattr(launcher, "local_build_id", lambda: "same")
    monkeypatch.setattr(launcher, "probe_instance", lambda port, **kw: (True, "same"))
    monkeypatch.setattr(launcher, "serves_ui", lambda port, **kw: False)
    assert launcher.check_existing_instance(8123) == "no-ui"


def test_serves_ui_requires_html():
    port, stop = _serve_once(b"<!doctype html><title>x</title>", content_type="text/html; charset=utf-8")
    try:
        assert launcher.serves_ui(port) is True
    finally:
        stop()

    port, stop = _serve_once(b'{"detail":"Not Found"}', content_type="application/json")
    try:
        assert launcher.serves_ui(port) is False, "JSON 404 不是界面"
    finally:
        stop()


def test_build_id_changes_with_override(monkeypatch):
    from deepquant.api import buildinfo

    buildinfo.build_id.cache_clear()
    try:
        monkeypatch.setenv(buildinfo.BUILD_ENV_VAR, "build-A")
        assert buildinfo.build_id() == "build-A"
        buildinfo.build_id.cache_clear()
        monkeypatch.setenv(buildinfo.BUILD_ENV_VAR, "build-B")
        assert buildinfo.build_id() == "build-B"
    finally:
        buildinfo.build_id.cache_clear()
        monkeypatch.delenv(buildinfo.BUILD_ENV_VAR, raising=False)


def test_check_mode_reports_requested_port_not_alternative(monkeypatch):
    """`--check` 诊断的是用户问的那个端口，不能先说「改用 8124」再说「8124 空闲」。"""
    monkeypatch.setattr(launcher, "dist_ready", lambda dist=None: True)
    monkeypatch.setattr(launcher, "is_port_free", lambda port, host="127.0.0.1": port != 8123)
    args, ui_ready, port = launcher._prepare(["--check", "--port", "8123"])
    assert args.check is True
    assert ui_ready is True
    assert port == 8123
