"""审计与留痕（T1）：run card 式工件的结构、存储、检索与自动记录。

模块分工：

- :mod:`deepquant.audit.run_card`：工件结构（五元组）、脱敏、规范化摘要；
- :mod:`deepquant.audit.store`：追加式分片存储、哈希链校验、回放查询；
- :mod:`deepquant.audit.operations`：核心操作注册表、覆盖率核算、业务侧记录器；
- :mod:`deepquant.audit.middleware`：接口层自动记录（默认关闭）。

设计约束（来自《推进计划》T1）：

1. 五元组完整率 100%——缺字段直接报错，不用空值顶替；
2. 工件不可篡改——追加式写入 + 哈希链，任何改动都会破坏后续摘要；
3. 回放 < 5 秒——索引先过滤、按需加载分片；
4. 不存储敏感凭证明文——构造阶段递归脱敏，只留字段路径。

本包**不**实现风控拒绝语义（属 T2），也**不**负责外部锚定（需调用方把
:func:`deepquant.audit.store.AuditStore.last_sha256` 记到不可改写的地方）。
"""

from __future__ import annotations

from deepquant.audit.middleware import (
    AUDIT_HEADER,
    OPERATION_ROUTES,
    OPERATOR_HEADER,
    SESSION_HEADER,
    AuditMiddleware,
    resolve_operation,
)
from deepquant.audit.operations import (
    AUDIT_DIR_ENV,
    AUDIT_STRICT_ENV,
    CORE_OPERATIONS,
    AuditConfigurationError,
    OperationSpec,
    assert_coverage_complete,
    audit_enabled,
    audit_root,
    coverage,
    record_operation,
    replay,
)
from deepquant.audit.run_card import (
    CARD_VERSION,
    GENESIS_SHA256,
    REDACTED,
    REQUIRED_FIELDS,
    AuditSchemaError,
    CompletenessReport,
    build_card,
    card_digest,
    completeness,
    now_iso,
    redact,
    validate_card,
    verify_card,
)
from deepquant.audit.store import (
    AuditChainError,
    AuditStore,
    AuditStoreError,
    ChainReport,
    chain_fingerprint,
    open_store,
    verify_shard_digests,
)

__all__ = [
    "AUDIT_DIR_ENV",
    "AUDIT_HEADER",
    "AUDIT_STRICT_ENV",
    "AuditChainError",
    "AuditConfigurationError",
    "AuditMiddleware",
    "AuditSchemaError",
    "AuditStore",
    "AuditStoreError",
    "CARD_VERSION",
    "CORE_OPERATIONS",
    "ChainReport",
    "CompletenessReport",
    "GENESIS_SHA256",
    "OPERATION_ROUTES",
    "OPERATOR_HEADER",
    "OperationSpec",
    "REDACTED",
    "REQUIRED_FIELDS",
    "SESSION_HEADER",
    "assert_coverage_complete",
    "audit_enabled",
    "audit_root",
    "build_card",
    "card_digest",
    "chain_fingerprint",
    "completeness",
    "coverage",
    "now_iso",
    "open_store",
    "record_operation",
    "redact",
    "replay",
    "resolve_operation",
    "validate_card",
    "verify_card",
    "verify_shard_digests",
]
