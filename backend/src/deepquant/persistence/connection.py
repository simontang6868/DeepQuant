"""SQLite 持久化：连接管理与 schema 初始化。

职责边界：
- 数据库路径解析（环境变量 ``DEEPQUANT_DB_PATH`` 可覆盖，默认 ``backend/data/backtests.db``）；
- 连接创建（启用外键、设置 busy_timeout、row_factory）；
- schema 初始化（幂等，``CREATE TABLE IF NOT EXISTS``，可重复执行）。

本模块只负责连接与 schema，不包含任何回测语义。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# 数据库路径环境变量（与 DEEPQUANT_DATA_ROOT 分离，可独立配置）
DB_PATH_ENV_VAR = "DEEPQUANT_DB_PATH"

# 默认数据库文件：<repo>/backend/data/backtests.db
# 由模块文件位置推导（persistence/connection.py -> parents[3] = backend/），与启动目录无关
_DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "backtests.db"

# SQLite schema 版本（最小可行版本管理，本阶段不引入迁移框架）
SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS strategy_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_id TEXT,
    strategy_version TEXT,
    strategy_params_json TEXT,
    dataset_symbol TEXT NOT NULL,
    dataset_digest TEXT,
    source_sha256 TEXT,
    start_date TEXT,
    end_date TEXT,
    initial_cash REAL NOT NULL,
    config_json TEXT,
    engine_version TEXT,
    schema_version TEXT,
    is_complete INTEGER NOT NULL,
    incomplete_reasons_json TEXT NOT NULL DEFAULT '[]',
    duration_ms INTEGER,
    result_digest TEXT,
    error_code TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS run_documents (
    run_id TEXT PRIMARY KEY REFERENCES strategy_runs(run_id) ON DELETE CASCADE,
    document_json TEXT NOT NULL,
    result_digest TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_runs_created_at ON strategy_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_symbol ON strategy_runs(symbol);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_strategy ON strategy_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_dataset_digest ON strategy_runs(dataset_digest);
"""


def get_db_path() -> Path:
    """返回解析后的数据库文件路径，并确保父目录存在。

    - 环境变量 ``DEEPQUANT_DB_PATH`` 优先；
    - 未设置时使用 ``<repo>/backend/data/backtests.db``；
    - 父目录不存在时安全创建；
    - 路径经 ``expanduser()`` + ``resolve()`` 规范化。
    """
    raw = os.environ.get(DB_PATH_ENV_VAR)
    path = Path(raw) if raw and raw.strip() else _DEFAULT_DB_PATH
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect(db_path: Path) -> sqlite3.Connection:
    """打开 SQLite 连接并启用外键约束。

    - ``timeout=30.0``：等待锁的最长时间（秒），避免被占用时立即失败；
    - ``PRAGMA foreign_keys=ON``：外键约束必须在**每个连接**上显式启用；
    - ``PRAGMA busy_timeout=30000``：与 timeout 配合的 busy 等待（毫秒）；
    - ``row_factory=sqlite3.Row``：按列名访问结果。
    """
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """初始化数据库 schema（幂等，可重复执行）。

    使用 ``CREATE TABLE IF NOT EXISTS``，不会因表已存在而失败，也不会重建或覆盖已有表。
    """
    with conn:
        conn.executescript(_SCHEMA_SQL)
        # 记录当前 schema 版本（INSERT OR IGNORE：首次插入，后续不再修改）
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """便捷接口：解析路径 → 打开连接 → 初始化 schema。

    调用方负责关闭返回的连接。
    """
    path = get_db_path() if db_path is None else db_path
    conn = connect(path)
    init_schema(conn)
    return conn
