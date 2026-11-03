"""部署链路脚本不得硬编码仓库绝对路径（可移植性回归测试）。

背景（2026-11-03 首次云主机部署）：``build_panel.py`` 在 **import 期**执行
``os.makedirs(r"D:\\DeepQuant power by txm\\...")``，is_due 一启动就
PermissionError，整条日更链起不来。本文件钉死三件事：

1. 链路关键脚本（会被 daily_chain / is_due / p13_forward import 或 exec 的）源码里
   不得出现任何一台机器的绝对路径字面量（``D:\\`` / ``D:/`` / ``DeepQuant-main``）；
2. ``build_panel.ROOT`` / ``fill_engine.ROOT`` 在设置了 ``DEEPQUANT_HOME`` 时用它，
   未设置时从 ``__file__`` 推导——两种情况下 QFQ/OUT 都必须落在仓库内；
3. 推导出的根必须是真实仓库根（拿 STATUS.md 当锚点，防止推导层级数被改错）。

运行：
    cd "D:/DeepQuant power by txm/DeepQuant-main"
    PYTHONPATH=scripts python -m pytest scripts/tests/test_no_hardcoded_roots.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
SP5 = _REPO / "research" / "stock_pick_2026-10-25" / "scripts"

#: 链路关键脚本：这些文件任何一个带死路径，日更链都会在某台机器上起不来。
CHAIN_FILES = [
    SP5 / "build_panel.py",
    SP5 / "sp5" / "fill_engine.py",
    SP5 / "sp5" / "p13_forward.py",
    SP5 / "sp5" / "trade_calendar.py",
    _REPO / "scripts" / "rebuild_adj_from_exec.py",
    _REPO / "scripts" / "update_exec_incremental.py",
    _REPO / "scripts" / "verify_qfq_rebuild.py",
    *(_REPO / "deploy" / "bin").glob("*.py"),
]

#: 部署模板同理（路径一律走 __REPO__ 占位）
DEPLOY_TEMPLATES = [
    *(_REPO / "deploy" / "systemd").glob("*"),
    _REPO / "deploy" / "logrotate" / "deepquant",
    _REPO / "deploy" / "env.example",
]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("f", CHAIN_FILES, ids=lambda p: p.name)
def test_chain_file_has_no_hardcoded_root(f):
    assert f.is_file(), f"链路文件不存在：{f}"
    src = f.read_text(encoding="utf-8")
    for bad in ("DeepQuant-main", "D:\\", "D:/"):
        assert bad not in src, f"{f.name} 含硬编码根路径字面量 {bad!r}"


@pytest.mark.parametrize("f", DEPLOY_TEMPLATES, ids=lambda p: p.name)
def test_deploy_templates_use_repo_placeholder(f):
    src = f.read_text(encoding="utf-8")
    assert "/opt/quant" not in src or "__REPO__" in src, \
        f"{f.name} 写死了部署目录而没用 __REPO__ 占位"
    assert "/opt/deepquant" not in src, f"{f.name} 残留旧部署目录 /opt/deepquant"


def test_build_panel_root_env_override(tmp_path, monkeypatch):
    """DEEPQUANT_HOME 必须优先，且 QFQ/OUT 指向它下面。"""
    fake = tmp_path / "opt" / "quant"
    (fake / "data").mkdir(parents=True)
    monkeypatch.setenv("DEEPQUANT_HOME", str(fake))
    mod = _load("build_panel_env", SP5 / "build_panel.py")
    assert Path(mod.ROOT) == fake
    assert Path(mod.QFQ) == fake / "data" / "qfq_all"
    assert Path(mod.OUT) == fake / "research" / "stock_pick_2026-10-25" / "data"


def test_build_panel_root_derives_to_repo(monkeypatch):
    """未设 DEEPQUANT_HOME 时由 __file__ 推导，且推导结果必须是真仓库根。"""
    monkeypatch.delenv("DEEPQUANT_HOME", raising=False)
    mod = _load("build_panel_derive", SP5 / "build_panel.py")
    assert Path(mod.ROOT) == _REPO, "推导层级数错了（锚点：STATUS.md 所在目录）"
    assert (_REPO / "STATUS.md").is_file()


def test_fill_engine_root_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "opt" / "quant"
    (fake / "data").mkdir(parents=True)
    monkeypatch.setenv("DEEPQUANT_HOME", str(fake))
    mod = _load("fill_engine_env", SP5 / "sp5" / "fill_engine.py")
    assert Path(mod.QFQ_DIR) == fake / "data" / "qfq_all"
    assert Path(mod.EXEC_DIR) == fake / "data" / "exec_all"


def test_fill_engine_root_derives_to_repo(monkeypatch):
    monkeypatch.delenv("DEEPQUANT_HOME", raising=False)
    mod = _load("fill_engine_derive", SP5 / "sp5" / "fill_engine.py")
    assert Path(mod.ROOT) == _REPO


def test_deepquant_home_env_is_honoured_by_daily_chain():
    """daily_chain.sh 的默认仓库根必须能被 DEEPQUANT_HOME 覆盖。"""
    src = (_REPO / "deploy" / "bin" / "daily_chain.sh").read_text(encoding="utf-8")
    assert 'DEEPQUANT_HOME:-' in src
