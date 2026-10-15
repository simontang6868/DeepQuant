"""API 层运行配置（数据根目录解析）。

数据根目录约定：
- 通过环境变量 ``DEEPQUANT_DATA_ROOT`` 配置；
- 未配置时使用仓库内默认目录 ``backend/data/``（以本文件位置锚定，不依赖当前工作目录）；
- 解析后的路径经 ``resolve()`` 规范化，作为所有数据文件访问的唯一可信根；
- API 不接受客户端提供的任何文件系统路径，数据集只能通过 symbol 标识符引用。
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_ROOT_ENV_VAR = "DEEPQUANT_DATA_ROOT"

# 默认数据根目录：<repo>/backend/data（由模块文件位置推导，与启动目录无关）
_DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[3] / "data"


def get_data_root() -> Path:
    """返回解析后的数据根目录（FastAPI 依赖注入入口，测试可覆盖）。"""
    raw = os.environ.get(DATA_ROOT_ENV_VAR)
    base = Path(raw) if raw and raw.strip() else _DEFAULT_DATA_ROOT
    return base.expanduser().resolve()
