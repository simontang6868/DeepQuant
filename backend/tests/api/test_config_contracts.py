"""api/config.py 契约与不变量测试（A16#7，本地不提 PR）。

补既有 API 测试（test_backtests_api.py / conftest.py / test_backtest_history_api.py）未覆盖的守卫分支
（coverage 实测 9 stmts / 3 miss，缺口集中在 ``DEEPQUANT_DATA_ROOT`` 被显式设置的分支）：
- 未设置 → 回落到以模块位置锚定的默认根 ``<repo>/backend/data``，且解析为绝对路径；
- 显式设置（含相对路径） → 返回该路径 ``expanduser().resolve()`` 后的绝对路径；
- 空白/空字符串 → 视同未设置，回落默认（不得把空白当成真实目录）；
- ``~`` 必须被 ``expanduser`` 展开；
- 默认根与启动目录（cwd）无关（模块文件位置锚定）；
- 安全契约：``get_data_root()`` 不接受任何参数，数据根只能源自环境变量，API 绝不接受客户端提供的文件系统路径。

全部为语义断言：校验具体被违反的不变量与返回的稳定结果，不靠「只断言不报错」。
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from deepquant.api import config
from deepquant.api.config import DATA_ROOT_ENV_VAR, get_data_root


@pytest.fixture
def clean_env(monkeypatch):
    """保证每个用例从「未设置 DEEPQUANT_DATA_ROOT」开始，且用例间互不污染。"""
    monkeypatch.delenv(DATA_ROOT_ENV_VAR, raising=False)
    yield
    monkeypatch.delenv(DATA_ROOT_ENV_VAR, raising=False)


# ---------- 1. 默认根：模块位置锚定，与 cwd 无关 ----------
def test_default_root_anchored_to_module_not_cwd(clean_env, tmp_path):
    """未设置环境变量时，根目录必须锚定到 api/config.py 的文件位置，而非启动目录。

    契约来自模块 docstring：默认根『以本文件位置锚定，不依赖当前工作目录』。
    """
    expected = (Path(config.__file__).resolve().parents[3] / "data").resolve()
    assert get_data_root() == expected
    # cwd 无关性：切到任意目录后再取，结果必须不变
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert get_data_root() == expected
    finally:
        os.chdir(cwd)


# ---------- 2. 默认根：解析为绝对路径 ----------
def test_default_root_is_absolute(clean_env):
    """默认根必须是绝对路径（resolve 规范化），不得是相对路径。"""
    root = get_data_root()
    assert root.is_absolute()
    assert root == config._DEFAULT_DATA_ROOT.resolve()


# ---------- 3. 显式设置：返回 env 路径的 expanduser().resolve() ----------
def test_explicit_root_resolved_and_absolute(clean_env, monkeypatch):
    """设置 DEEPQUANT_DATA_ROOT 时，必须返回该路径 ``expanduser().resolve()`` 后的绝对路径。

    语义：环境变量是数据根的唯一可信来源；返回路径必须被规范化（绝对、无冗余分隔符）。
    """
    monkeypatch.setenv(DATA_ROOT_ENV_VAR, "/tmp/dq/root")
    result = get_data_root()
    assert result.is_absolute()
    assert result == Path("/tmp/dq/root").resolve()


# ---------- 4. 相对路径 env 也会被 resolve 成绝对 ----------
def test_relative_env_root_resolved_against_cwd(clean_env, monkeypatch):
    """相对路径形式的 env 值必须按 cwd 解析为绝对路径（resolve 语义），不得原样返回。"""
    monkeypatch.setenv(DATA_ROOT_ENV_VAR, "some/relative/dir")
    result = get_data_root()
    assert result.is_absolute()
    assert result == Path("some/relative/dir").resolve()


# ---------- 5. 空白 / 空字符串 视为未设置 ----------
@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_whitespace_env_falls_back_to_default(clean_env, monkeypatch, value):
    """DEEPQUANT_DATA_ROOT 为空白/空串时，必须回落默认根，不得把空白当真实目录。"""
    monkeypatch.setenv(DATA_ROOT_ENV_VAR, value)
    assert get_data_root() == config._DEFAULT_DATA_ROOT.resolve()


# ---------- 6. expanduser：~ 必须展开 ----------
def test_tilde_expanded(clean_env, monkeypatch):
    """env 值中的 ~ 必须被 expanduser 展开为用户主目录。"""
    monkeypatch.setenv(DATA_ROOT_ENV_VAR, "~/dq_data")
    result = get_data_root()
    assert result == (Path.home() / "dq_data").resolve()
    assert result.is_absolute()


# ---------- 7. 确定性：同输入同输出 ----------
def test_deterministic_for_same_env(clean_env, monkeypatch):
    """同一环境变量下多次调用必须返回相等的路径（无隐藏状态）。"""
    monkeypatch.setenv(DATA_ROOT_ENV_VAR, "/tmp/dq/x")
    assert get_data_root() == get_data_root()


# ---------- 8. 安全契约：不接收任何参数，根只能源自环境变量 ----------
def test_data_root_derives_only_from_env_var(clean_env):
    """安全红线：``get_data_root`` 不接受任何参数（签名零形参），

    数据根只能由 ``DEEPQUANT_DATA_ROOT`` 环境变量推导，API 绝不接受客户端提供的文件系统路径。
    函数签名有参即破坏『API 不接受客户端文件系统路径』契约。
    """
    params = inspect.signature(get_data_root).parameters
    assert list(params.keys()) == []
    # 常量名称即文档契约的一部分
    assert DATA_ROOT_ENV_VAR == "DEEPQUANT_DATA_ROOT"
