"""SQLite 持久化层（回测运行记录与结果文档）。

公共入口：
- :func:`get_db_path` / :func:`connect` / :func:`init_schema`：连接与 schema；
- :func:`save_backtest_result`：保存一次成功回测；
- :func:`get_run` / :func:`get_run_document` / :func:`list_runs`：读取；
- :func:`result_digest`：结果文档摘要；
- :func:`audit_run_integrity`：只读完整性审计（重算摘要并比对）；
- :func:`build_reproducibility_checklist`：只读复现信息清单；
- :class:`PersistenceError` / :class:`ResultDocumentCorruptedError` /
  :class:`RunRecord` / :class:`IntegrityReport` / :class:`ReproChecklist`：异常与记录模型。
"""

from deepquant.persistence.connection import (
    DB_PATH_ENV_VAR,
    SCHEMA_VERSION,
    connect,
    get_connection,
    get_db_path,
    init_schema,
)
from deepquant.persistence.repro_checklist import (
    DOC_CORRUPTED,
    DOC_MISSING,
    DOC_OK,
    ITEM_AVAILABLE,
    ITEM_MISSING,
    ITEM_NOT_APPLICABLE,
    ITEM_NOT_ARCHIVED,
    ITEM_NOT_RECORDED,
    ITEM_NOT_VERIFIABLE,
    ITEM_VERIFIABLE,
    LEVEL_FULL,
    LEVEL_NONE,
    LEVEL_PARTIAL,
    LEVEL_UNDETERMINED,
    ReproChecklist,
    ReproItem,
    ReproLevel,
    build_reproducibility_checklist,
)
from deepquant.persistence.signal_store import (
    SIGNAL_EVENTS_TABLE,
    count_signal_events,
    list_signal_events,
    save_signal_events,
)
from deepquant.persistence.store import (
    INTEGRITY_FAILED,
    INTEGRITY_INCOMPLETE,
    INTEGRITY_PASSED,
    RUN_STATUS_SUCCEEDED,
    IntegrityCheck,
    IntegrityReport,
    PersistenceError,
    ResultDocumentCorruptedError,
    RunRecord,
    audit_run_integrity,
    count_runs,
    get_run,
    get_run_document,
    get_run_with_document,
    list_runs,
    result_digest,
    save_backtest_result,
)

__all__ = [
    "DB_PATH_ENV_VAR",
    "DOC_CORRUPTED",
    "DOC_MISSING",
    "DOC_OK",
    "INTEGRITY_FAILED",
    "INTEGRITY_INCOMPLETE",
    "INTEGRITY_PASSED",
    "ITEM_AVAILABLE",
    "ITEM_MISSING",
    "ITEM_NOT_APPLICABLE",
    "ITEM_NOT_ARCHIVED",
    "ITEM_NOT_RECORDED",
    "ITEM_NOT_VERIFIABLE",
    "ITEM_VERIFIABLE",
    "LEVEL_FULL",
    "LEVEL_NONE",
    "LEVEL_PARTIAL",
    "LEVEL_UNDETERMINED",
    "RUN_STATUS_SUCCEEDED",
    "SCHEMA_VERSION",
    "SIGNAL_EVENTS_TABLE",
    "IntegrityCheck",
    "IntegrityReport",
    "PersistenceError",
    "ReproChecklist",
    "ReproItem",
    "ReproLevel",
    "ResultDocumentCorruptedError",
    "RunRecord",
    "audit_run_integrity",
    "build_reproducibility_checklist",
    "connect",
    "count_runs",
    "count_signal_events",
    "get_connection",
    "get_db_path",
    "get_run",
    "get_run_document",
    "get_run_with_document",
    "init_schema",
    "list_runs",
    "list_signal_events",
    "result_digest",
    "save_backtest_result",
    "save_signal_events",
]
