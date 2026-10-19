"""单端口形态（前端产物挂在 API 进程里）的契约测试。

这块的风险不是「页面打不开」——那是明摆着的。风险是三种**静默改变**：

1. 默认行为被改：不设开关时 `/` 变成 200，未知路径不再 404，既有测试却照样绿；
2. `/api` 前缀把真实路由吃掉：`/api/datasets` 命中静态文件或 404，界面显示「无数据集」；
3. 静态挂载抢在路由前面：`/health` 变成 index.html，健康检查永远「看起来正常」。

所以这里逐条钉住：开关、优先级、前缀剥离、路径逃逸、产物缺失时不崩。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deepquant.api import ui
from deepquant.api.app import create_app


def _make_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>DQ-UI-MARKER</title><script src='/assets/app.js'></script>",
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text("console.log('dq');", encoding="utf-8")
    return dist


@pytest.fixture
def dist(tmp_path):
    return _make_dist(tmp_path)


@pytest.fixture
def mounted(dist, monkeypatch, tmp_path):
    """把 UI 挂到临时产物上，返回 TestClient。

    数据根目录也指向临时目录：`/datasets` 需要它存在才返回 200。若不指定，
    默认值由 ``api/config.py`` 相对包位置推导，在 pip 安装（非源码树）的 CI 环境
    会指向不存在的位置，让「静态文件是否抢占路由」这个命题变成对机器环境的断言。
    这里给一个空目录：数据集列表为空，但路由本身可被正常命中。
    """
    monkeypatch.setenv(ui.UI_ENV_VAR, "1")
    monkeypatch.setattr(ui, "ui_dist_dir", lambda: dist)
    data_root = tmp_path / "data-root"
    data_root.mkdir(exist_ok=True)
    monkeypatch.setenv("DEEPQUANT_DATA_ROOT", str(data_root))
    app = create_app()
    return TestClient(app)


# --------------------------------------------------------------------------- #
# 1. 默认不挂载
# --------------------------------------------------------------------------- #


def test_ui_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ui.UI_ENV_VAR, raising=False)
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 404, "不设开关时 / 必须仍是 404（行为与以往一致）"


def test_mount_ui_returns_false_when_switch_off(monkeypatch):
    monkeypatch.delenv(ui.UI_ENV_VAR, raising=False)
    assert ui.mount_ui(create_app(), dist_dir=Path("whatever")) is False


# --------------------------------------------------------------------------- #
# 2. 开启后：静态产物 + 真实路由同时可用
# --------------------------------------------------------------------------- #


def test_mount_ui_serves_index_and_assets(mounted):
    index = mounted.get("/")
    assert index.status_code == 200
    assert "DQ-UI-MARKER" in index.text
    assert index.headers["content-type"].startswith("text/html")

    asset = mounted.get("/assets/app.js")
    assert asset.status_code == 200
    assert "localhost" not in asset.text
    assert asset.text.startswith("console.log")


def test_mounted_ui_does_not_shadow_api_routes(mounted):
    """静态挂载不得抢在业务路由前面。"""
    for path in ("/health", "/datasets", "/strategies"):
        resp = mounted.get(path)
        assert resp.status_code == 200, path
        assert "text/html" not in resp.headers.get("content-type", ""), path


def test_api_prefix_is_stripped_to_real_routes(mounted):
    """前端产物请求的是 /api/xxx，单端口下必须能命中真实路由。"""
    assert mounted.get("/api/health").json() == {"status": "ok"}
    assert "datasets" in mounted.get("/api/datasets").json()
    assert "strategies" in mounted.get("/api/strategies").json()


def test_api_prefix_works_for_post_as_well(mounted):
    """/api 前缀剥离必须对 POST 生效（回测入口在 POST 上）。"""
    resp = mounted.post("/api/backtests", json={"dataset_symbol": "9" * 6})
    assert resp.status_code == 422, "参数不合法应是 422（说明请求真的路由到了回测接口）"


# --------------------------------------------------------------------------- #
# 3. 边界
# --------------------------------------------------------------------------- #


def test_mount_skipped_when_index_missing(tmp_path, monkeypatch):
    """产物目录存在但没有 index.html → 不挂载、不崩、/ 仍 404。"""
    monkeypatch.setenv(ui.UI_ENV_VAR, "1")
    empty = tmp_path / "dist"
    empty.mkdir()
    monkeypatch.setattr(ui, "ui_dist_dir", lambda: empty)

    assert ui.mount_ui(create_app(), dist_dir=empty) is False
    client = TestClient(create_app())
    assert client.get("/").status_code == 404
    assert client.get("/health").status_code == 200


def test_static_mount_rejects_path_traversal(mounted):
    """构造出的 ../ 不得逃出 dist 目录读到仓库文件。"""
    for probe in ("/../backend/pyproject.toml", "/%2e%2e/backend/pyproject.toml", "/assets/../../README.md"):
        resp = mounted.get(probe)
        assert resp.status_code in (400, 403, 404), f"{probe} 不应返回 {resp.status_code}"
        assert "[project]" not in resp.text


def test_switch_value_parsing(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(ui.UI_ENV_VAR, value)
        assert ui.ui_enabled() is True, value
    for value in ("0", "false", "off", ""):
        monkeypatch.setenv(ui.UI_ENV_VAR, value)
        assert ui.ui_enabled() is False, value
    monkeypatch.delenv(ui.UI_ENV_VAR, raising=False)
    assert ui.ui_enabled() is False
    assert os.environ.get(ui.UI_ENV_VAR) is None


# --------------------------------------------------------------------------- #
# 4. 静态挂载的副作用（2026-10-18 复审新增）
#
# 这三条对应「页面能开、但服务端语义已经错了」的三个点，都不影响肉眼可用性，
# 所以最容易被漏掉：
#   - `/api` 落到 index.html（200 + HTML），探活脚本会误判 API 可用；
#   - `/api/backtests/` 被 Mount("/") 接住返回 404，而路由层本该重定向；
#   - 静态资源没有缓存策略，index.html 被浏览器缓存后重建会白屏。
# --------------------------------------------------------------------------- #


def test_bare_api_prefix_is_json_404_not_index_html(mounted):
    """`/api` 不是页面。返回 index.html 会让「API 探活」得到 200 的错误结论。"""
    for path in ("/api", "/api/"):
        resp = mounted.get(path)
        assert resp.status_code == 404, path
        assert resp.headers["content-type"].startswith("application/json"), path
        assert resp.json() == {"detail": "Not Found"}, path


def test_bare_api_prefix_head_has_no_body(mounted):
    resp = mounted.head("/api")
    assert resp.status_code == 404
    assert resp.content == b""


def test_api_trailing_slash_reaches_real_route(mounted):
    """尾斜杠必须落到真实路由，而不是被静态挂载吃成 404。

    路由层默认会 307 到无斜杠版本，但 `Mount("/")` 先匹配，重定向根本没机会发生。
    公开 API 客户端带尾斜杠很常见，所以这里把行为钉死为「直接命中」。
    """
    assert mounted.get("/api/health/").json() == {"status": "ok"}
    posted = mounted.post("/api/backtests/", json={})
    assert posted.status_code == 422, "应命中回测接口的参数校验，而不是静态 404"


def test_static_cache_headers_are_split_by_hash_semantics(mounted):
    """带内容哈希的资源可永久缓存；index.html 必须每次回源。"""
    index = mounted.get("/")
    assert index.headers["cache-control"] == ui.REVALIDATE_CACHE

    asset = mounted.get("/assets/app.js")
    assert asset.headers["cache-control"] == ui.IMMUTABLE_CACHE


def test_cache_header_present_once(mounted):
    """中间件不得重复追加 cache-control（重复头会让某些代理行为不确定）。"""
    resp = mounted.get("/assets/app.js")
    values = [v for k, v in resp.headers.multi_items() if k.lower() == "cache-control"]
    assert values == [ui.IMMUTABLE_CACHE]


def test_mount_warns_when_switch_on_but_dist_missing(tmp_path, monkeypatch, caplog):
    """显式要界面却拿不到产物，必须留痕——否则现场只有「404」这一条线索。"""
    import logging

    monkeypatch.setenv(ui.UI_ENV_VAR, "1")
    empty = tmp_path / "dist"
    empty.mkdir()
    with caplog.at_level(logging.WARNING, logger=ui.__name__):
        assert ui.mount_ui(create_app(), dist_dir=empty) is False
    assert any("index.html" in rec.getMessage() for rec in caplog.records)


def test_build_header_present_on_api_and_static(mounted):
    """构建指纹要覆盖静态与 API 两类响应——启动器探的是 /health。"""
    from deepquant.api.buildinfo import BUILD_HEADER, build_id

    for path in ("/health", "/", "/api/health"):
        resp = mounted.get(path)
        assert resp.headers.get(BUILD_HEADER) == build_id(), path
