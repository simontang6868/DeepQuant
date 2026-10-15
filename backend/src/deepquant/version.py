"""项目版本与结果 schema 版本（唯一来源）。

- ``__version__``：项目（库）版本，与 pyproject.toml 保持一致，用于结果中的引擎版本标识；
- ``RESULT_SCHEMA_VERSION``：回测结果 JSON 的 schema 版本，序列化/反序列化据此校验兼容性。

本模块不依赖任何其它子模块，避免引入循环依赖。
"""

from __future__ import annotations

__version__ = "0.0.1"

# 回测结果 JSON schema 版本（与 BacktestResult.schema_version 默认值一致）
RESULT_SCHEMA_VERSION = "0.1.0"

# 引擎/项目版本标识，写入回测结果以支持可复现性追溯
ENGINE_VERSION = __version__
