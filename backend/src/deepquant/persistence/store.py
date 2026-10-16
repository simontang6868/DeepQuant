"""回测运行记录的持久化存储。

核心职责：
- 把一次成功的 ``BacktestResult`` 保存到 SQLite；
- 计算稳定的 ``result_digest``；
- 读取运行记录（用于历史列表 / 详情，后续阶段扩展）。

严格区分的三个摘要字段（不得互相替代）：
- ``source_sha256``：原始 CSV 文件字节的 SHA-256（来自数据集元数据）；
- ``dataset_digest``：清洗和规范化后的回测输入数据内容摘要（来自 ``dataset_summary``）；
- ``result_digest``：完整回测结果文档规范化后的摘要（本模块计算）。

本模块不实现撮合、不修改回测结果、不引入异步。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from deepquant.backtest.models import BacktestResult
from deepquant.performance.serialize import config_summary, result_to_dict
from deepquant.persistence.connection import connect, init_schema

# 运行状态（本阶段仅持久化成功运行；失败的回测请求不会被保存）
RUN_STATUS_SUCCEEDED = "succeeded"

# 结果摘要规范化的 JSON 规则：按键排序、无空白、UTF-8
_DIGEST_SEPARATORS = (",", ":")


class PersistenceError(Exception):
    """持久化失败（数据库不可写、连接错误、事务失败等）。

    不吞掉底层异常；由调用方（API 层）映射为合适的 HTTP 错误。
    """


class ResultDocumentCorruptedError(PersistenceError):
    """历史结果文档存在但无法解析为 JSON 对象（数据完整性问题，非数据库故障）。

    与数据库访问失败（``PersistenceError``）明确区分：
    - 本异常表示「读到的字节无法解析 / 顶层不是对象」，即结果文档已损坏；
    - API 层应把它映射为明确的完整性错误，而不是无差别的通用 500。
    消息不包含文件系统绝对路径。
    """


@dataclass(frozen=True)
class RunRecord:
    """一条回测运行记录的元数据摘要。"""

    run_id: str
    created_at: str
    status: str
    symbol: str
    strategy_id: str | None
    strategy_version: str | None
    strategy_params: dict | None
    dataset_symbol: str
    dataset_digest: str | None
    source_sha256: str | None
    start_date: str | None
    end_date: str | None
    initial_cash: float
    engine_version: str | None
    schema_version: str | None
    is_complete: bool
    result_digest: str | None
    duration_ms: int | None


def result_digest(document: dict) -> str:
    """对完整结果文档计算稳定的 SHA-256 摘要。

    规范化规则：
    - 输入必须是 ``result_to_dict`` 的输出（已是 JSON 安全结构）；
    - 使用 ``json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False)``
      得到确定性的紧凑 JSON 字符串；
    - 对 UTF-8 字节取 SHA-256，前缀 ``sha256:``。

    注意：不得直接使用 ``str(document)`` 或非确定性的 JSON 表示作为摘要。
    """
    if not isinstance(document, dict):
        raise PersistenceError(
            f"result_digest 需要 dict 类型的结果文档，收到 {type(document)!r}"
        )
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=_DIGEST_SEPARATORS
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _now_utc_iso() -> str:
    """UTC 当前时间，ISO-8601 格式（无时区缩写，用 Z 标记 UTC）。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _to_json_text(value) -> str:
    """把值序列化为 JSON 文本（用于存入 TEXT 列）。"""
    return json.dumps(value, ensure_ascii=False)


def _decode_document(document_json: str) -> dict:
    """把已保存的结果文档 JSON 文本解析为 dict。

    仅做结构解析，不做完整性校验（摘要比对由 ``audit_run_integrity`` 负责）。

    - JSON 无法解析，或顶层不是对象 → 抛 ``ResultDocumentCorruptedError``
      （明确的数据完整性问题，不是数据库故障）；
    - 消息只含异常类型与结构说明，不包含文件系统路径。
    """
    try:
        parsed = json.loads(document_json)
    except (TypeError, ValueError) as exc:
        raise ResultDocumentCorruptedError(
            f"已保存的结果文档无法解析为 JSON：{type(exc).__name__}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ResultDocumentCorruptedError(
            f"已保存的结果文档不是 JSON 对象（实际为 {type(parsed).__name__}）"
        )
    return parsed


@contextmanager
def _open_conn(db_path: Path):
    """打开连接并初始化 schema，统一处理连接/初始化阶段的 sqlite3 错误。

    - 连接或 schema 初始化失败时抛出 PersistenceError；
    - 退出时始终关闭连接；
    - 调用方负责在 with 块内自行控制事务（``with conn``）。
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = connect(db_path)
        init_schema(conn)
        yield conn
    except sqlite3.Error as exc:
        raise PersistenceError(f"数据库连接或初始化失败：{exc}") from exc
    finally:
        if conn is not None:
            conn.close()


def save_backtest_result(
    db_path: Path,
    result: BacktestResult,
    *,
    strategy_id: str,
    dataset_symbol: str,
    source_sha256: str | None,
    duration_ms: int,
) -> str:
    """把一次成功的回测结果保存到数据库，返回 run_id。

    事务边界：``strategy_runs`` 与 ``run_documents`` 在**同一个事务**中写入；
    任一写入失败则整体回滚，不会留下「标记成功但无完整结果」的记录。

    参数：
    - ``result``：已计算完成的 BacktestResult（含 performance）；
    - ``strategy_id``：策略注册表标识符（来自请求）；
    - ``dataset_symbol``：数据集标识符（来自请求，等于 config.symbol）；
    - ``source_sha256``：原始 CSV 文件指纹（来自数据集元数据，可为 None）；
    - ``duration_ms``：回测执行耗时（毫秒）。
    """
    # 生成正式结果文档（复用既有序列化入口，确保与 API 返回一致）
    document = result_to_dict(result)
    digest = result_digest(document)
    run_id = uuid.uuid4().hex
    created_at = _now_utc_iso()

    strategy_meta = result.strategy or {}
    dataset_meta = result.dataset or {}

    # 区分运行状态（status）与结果完整性（is_complete）：二者不是同一概念
    row = (
        run_id,
        created_at,
        RUN_STATUS_SUCCEEDED,
        result.config.symbol,
        strategy_id,
        strategy_meta.get("version"),
        _to_json_text(strategy_meta.get("params")),
        dataset_symbol,
        dataset_meta.get("digest"),
        source_sha256,
        result.period_start.isoformat() if result.period_start is not None else None,
        result.period_end.isoformat() if result.period_end is not None else None,
        float(result.config.initial_cash),
        _to_json_text(config_summary(result.config)),
        result.engine_version,
        result.schema_version,
        1 if result.is_complete else 0,
        _to_json_text(list(result.incomplete_reasons)),
        int(duration_ms),
        digest,
        None,  # error_code：成功运行无错误
        None,  # error_message
    )

    document_json = _to_json_text(document)

    # 连接与 schema 初始化同样可能失败（路径无效、数据库被占用等）；
    # _open_conn 统一把连接/初始化阶段的 sqlite3 错误包装为 PersistenceError。
    try:
        with _open_conn(db_path) as conn, conn:  # conn: 连接生命周期；内层 conn: 事务（COMMIT/ROLLBACK）
            conn.execute(
                """
                INSERT INTO strategy_runs (
                    run_id, created_at, status, symbol, strategy_id,
                    strategy_version, strategy_params_json, dataset_symbol,
                    dataset_digest, source_sha256, start_date, end_date,
                    initial_cash, config_json, engine_version, schema_version,
                    is_complete, incomplete_reasons_json, duration_ms,
                    result_digest, error_code, error_message
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                row,
            )
            conn.execute(
                """
                INSERT INTO run_documents (run_id, document_json, result_digest)
                VALUES (?, ?, ?)
                """,
                (run_id, document_json, digest),
            )
    except PersistenceError:
        raise
    except sqlite3.Error as exc:
        raise PersistenceError(f"保存回测结果失败：{exc}") from exc

    return run_id


def _row_to_record(row: sqlite3.Row) -> RunRecord:
    raw_params = row["strategy_params_json"]
    try:
        params = json.loads(raw_params) if raw_params is not None else None
    except (TypeError, ValueError):
        # JSON 损坏或非 dict：降级为 None，记录本身仍可返回（历史数据容错）
        params = None
    if not isinstance(params, dict):
        params = None
    return RunRecord(
        run_id=row["run_id"],
        created_at=row["created_at"],
        status=row["status"],
        symbol=row["symbol"],
        strategy_id=row["strategy_id"],
        strategy_version=row["strategy_version"],
        strategy_params=params,
        dataset_symbol=row["dataset_symbol"],
        dataset_digest=row["dataset_digest"],
        source_sha256=row["source_sha256"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        initial_cash=float(row["initial_cash"]),
        engine_version=row["engine_version"],
        schema_version=row["schema_version"],
        is_complete=bool(row["is_complete"]),
        result_digest=row["result_digest"],
        duration_ms=row["duration_ms"],
    )


def get_run(db_path: Path, run_id: str) -> RunRecord | None:
    """按 run_id 读取运行记录元数据（不含完整文档）。"""
    try:
        with _open_conn(db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM strategy_runs WHERE run_id = ?", (run_id,)
            )
            row = cursor.fetchone()
            return _row_to_record(row) if row is not None else None
    except PersistenceError:
        raise
    except sqlite3.Error as exc:
        raise PersistenceError(f"读取运行记录失败：{exc}") from exc


def get_run_document(db_path: Path, run_id: str) -> dict | None:
    """按 run_id 读取完整结果文档。

    - 记录不存在 / 文档行缺失 → ``None``；
    - 文档存在但 JSON 损坏或顶层非对象 → 抛 ``ResultDocumentCorruptedError``
      （数据完整性问题，与数据库访问失败区分）。
    """
    try:
        with _open_conn(db_path) as conn:
            cursor = conn.execute(
                "SELECT document_json FROM run_documents WHERE run_id = ?", (run_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return _decode_document(row["document_json"])
    except PersistenceError:
        raise
    except sqlite3.Error as exc:
        raise PersistenceError(f"读取结果文档失败：{exc}") from exc


def _build_where(
    *, symbol: str | None, strategy_id: str | None, status: str | None
) -> tuple[str, list]:
    """根据可选过滤条件构造 WHERE 子句与参数列表。

    列名硬编码（非用户输入），用户值仅通过 ``?`` 参数传入，杜绝 SQL 注入。
    """
    clauses: list[str] = []
    params: list = []
    if symbol is not None:
        clauses.append("symbol = ?")
        params.append(symbol)
    if strategy_id is not None:
        clauses.append("strategy_id = ?")
        params.append(strategy_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_runs(
    db_path: Path,
    *,
    limit: int = 100,
    offset: int = 0,
    symbol: str | None = None,
    strategy_id: str | None = None,
    status: str | None = None,
) -> list[RunRecord]:
    """列出运行记录。

    排序：``created_at`` 降序；``created_at`` 相同时以 ``run_id`` 升序作为次级排序，
    保证分页顺序稳定。支持按 symbol / strategy_id / status 过滤（全部可选）。
    """
    if limit < 1:
        raise ValueError("limit 必须 >= 1")
    if offset < 0:
        raise ValueError("offset 必须 >= 0")

    where, params = _build_where(symbol=symbol, strategy_id=strategy_id, status=status)
    sql = (
        f"SELECT * FROM strategy_runs{where} "
        "ORDER BY created_at DESC, run_id ASC LIMIT ? OFFSET ?"
    )
    params = params + [limit, offset]

    try:
        with _open_conn(db_path) as conn:
            cursor = conn.execute(sql, params)
            return [_row_to_record(row) for row in cursor.fetchall()]
    except PersistenceError:
        raise
    except sqlite3.Error as exc:
        raise PersistenceError(f"列出运行记录失败：{exc}") from exc


def count_runs(
    db_path: Path,
    *,
    symbol: str | None = None,
    strategy_id: str | None = None,
    status: str | None = None,
) -> int:
    """统计符合过滤条件的运行记录总数（与 list_runs 使用相同过滤条件）。"""
    where, params = _build_where(symbol=symbol, strategy_id=strategy_id, status=status)
    sql = f"SELECT COUNT(*) AS cnt FROM strategy_runs{where}"
    try:
        with _open_conn(db_path) as conn:
            row = conn.execute(sql, params).fetchone()
            return int(row["cnt"])
    except PersistenceError:
        raise
    except sqlite3.Error as exc:
        raise PersistenceError(f"统计运行记录失败：{exc}") from exc


def get_run_with_document(
    db_path: Path, run_id: str
) -> tuple[RunRecord, dict | None] | None:
    """按 run_id 读取运行元数据与完整结果文档。

    使用 LEFT JOIN 在一次查询中同时取两表，可靠区分三种情况：
    - 返回 ``None``：strategy_runs 中不存在该 run_id（404）；
    - 返回 ``(record, None)``：元数据存在但 run_documents 缺失（数据完整性问题）；
    - 返回 ``(record, document)``：正常完整记录；
    - 文档存在但 JSON 损坏或顶层非对象 → 抛 ``ResultDocumentCorruptedError``。
    """
    sql = (
        "SELECT sr.*, rd.document_json AS document_json "
        "FROM strategy_runs sr LEFT JOIN run_documents rd ON sr.run_id = rd.run_id "
        "WHERE sr.run_id = ?"
    )
    try:
        with _open_conn(db_path) as conn:
            row = conn.execute(sql, (run_id,)).fetchone()
            if row is None:
                return None
            record = _row_to_record(row)
            document = (
                _decode_document(row["document_json"]) if row["document_json"] is not None else None
            )
            return record, document
    except PersistenceError:
        raise
    except sqlite3.Error as exc:
        raise PersistenceError(f"读取运行详情失败：{exc}") from exc


# --------------------------------------------------------------------------- #
# 只读完整性审计
# --------------------------------------------------------------------------- #

# 审计状态（三态语义，不得把「无法验证」当作「验证通过」）
INTEGRITY_PASSED = "passed"
INTEGRITY_FAILED = "failed"
INTEGRITY_INCOMPLETE = "incomplete"

# 结果文档必需字段：缺失即为结构损坏；可选字段缺失不算损坏
_REQUIRED_DOCUMENT_FIELDS: tuple[str, ...] = ("schema_version", "engine_version", "config")


@dataclass(frozen=True)
class IntegrityCheck:
    """单项完整性检查结果。"""

    name: str
    status: str  # INTEGRITY_PASSED / INTEGRITY_FAILED / INTEGRITY_INCOMPLETE
    message: str


@dataclass(frozen=True)
class IntegrityReport:
    """一次只读完整性审计的汇总结果。

    ``status`` 语义：
    - ``passed``：已执行的检查均通过；
    - ``failed``：存在明确的完整性问题；
    - ``incomplete``：必要信息缺失，无法完成全部检查（不等于通过，也不等于损坏）。
    """

    run_id: str
    status: str
    checks: tuple[IntegrityCheck, ...]
    metadata_result_digest: str | None
    document_result_digest: str | None
    recomputed_digest: str | None
    audited_at: str


def _aggregate_integrity_status(checks: list[IntegrityCheck]) -> str:
    """按「failed 优先、其次 incomplete」汇总整体状态。"""
    if any(c.status == INTEGRITY_FAILED for c in checks):
        return INTEGRITY_FAILED
    if any(c.status == INTEGRITY_INCOMPLETE for c in checks):
        return INTEGRITY_INCOMPLETE
    return INTEGRITY_PASSED


def audit_run_integrity(db_path: Path, run_id: str) -> IntegrityReport | None:
    """只读完整性审计：校验已保存结果文档与其摘要的一致性。

    严格只读：不修改任何历史记录、不重跑回测、不写入新结果。

    错误类型明确区分：
    - run 不存在 → 返回 ``None``（由 API 层映射为 404）；
    - 数据库访问 / 读取错误 → 抛 ``PersistenceError``（与数据完整性问题区分）；
    - 数据完整性问题 → 通过 ``checks`` 的 ``status`` 报告，不抛异常。

    摘要校验复用与保存时**完全相同**的规范化逻辑（``result_digest`` 对解析后的
    结果文档重算），避免因序列化差异造成错误告警。
    """
    try:
        with _open_conn(db_path) as conn:
            row = conn.execute(
                "SELECT sr.result_digest AS meta_digest, "
                "rd.document_json AS document_json, rd.result_digest AS doc_digest "
                "FROM strategy_runs sr "
                "LEFT JOIN run_documents rd ON sr.run_id = rd.run_id "
                "WHERE sr.run_id = ?",
                (run_id,),
            ).fetchone()
    except PersistenceError:
        raise
    except sqlite3.Error as exc:
        raise PersistenceError(f"完整性审计失败：{exc}") from exc

    if row is None:
        return None

    meta_digest = row["meta_digest"]
    doc_digest = row["doc_digest"]
    document_json = row["document_json"]
    checks: list[IntegrityCheck] = []
    recomputed: str | None = None

    # 检查 1：结果文档存在（缺失 = 明确完整性问题，不是「无法验证」）
    if document_json is None:
        checks.append(
            IntegrityCheck("document_present", INTEGRITY_FAILED, "元数据存在但结果文档缺失")
        )
        return IntegrityReport(
            run_id=run_id,
            status=INTEGRITY_FAILED,
            checks=tuple(checks),
            metadata_result_digest=meta_digest,
            document_result_digest=None,
            recomputed_digest=None,
            audited_at=_now_utc_iso(),
        )
    checks.append(IntegrityCheck("document_present", INTEGRITY_PASSED, "结果文档存在"))

    # 检查 2：JSON 可解析且为对象
    document: dict | None = None
    try:
        parsed = json.loads(document_json)
        if not isinstance(parsed, dict):
            raise ValueError("结果文档不是 JSON 对象")
    except (TypeError, ValueError) as exc:
        checks.append(
            IntegrityCheck(
                "document_json_parseable",
                INTEGRITY_FAILED,
                f"结果文档 JSON 不可解析：{exc}",
            )
        )
    else:
        document = parsed
        checks.append(
            IntegrityCheck("document_json_parseable", INTEGRITY_PASSED, "结果文档 JSON 可解析")
        )

    # 检查 3：重新计算摘要并与持久化摘要比较（真正的内容完整性校验）
    if document is not None:
        recompute_error: PersistenceError | None = None
        try:
            recomputed = result_digest(document)
        except PersistenceError as exc:
            recompute_error = exc

        if recompute_error is not None:
            checks.append(
                IntegrityCheck(
                    "result_digest_consistent",
                    INTEGRITY_INCOMPLETE,
                    f"无法重新计算摘要：{recompute_error}",
                )
            )
        elif doc_digest is None:
            checks.append(
                IntegrityCheck(
                    "result_digest_consistent",
                    INTEGRITY_INCOMPLETE,
                    "结果文档未记录摘要，无法校验",
                )
            )
        elif recomputed == doc_digest:
            checks.append(
                IntegrityCheck(
                    "result_digest_consistent",
                    INTEGRITY_PASSED,
                    "重新计算的摘要与已保存摘要一致",
                )
            )
        else:
            checks.append(
                IntegrityCheck(
                    "result_digest_consistent",
                    INTEGRITY_FAILED,
                    "重新计算的摘要与已保存摘要不一致（结果文档可能已被修改）",
                )
            )

    # 检查 4：元数据与结果文档的摘要一致
    if meta_digest is None or doc_digest is None:
        checks.append(
            IntegrityCheck(
                "digests_match_across_tables",
                INTEGRITY_INCOMPLETE,
                "缺少元数据或文档摘要，无法比较",
            )
        )
    elif meta_digest == doc_digest:
        checks.append(
            IntegrityCheck("digests_match_across_tables", INTEGRITY_PASSED, "元数据与文档摘要一致")
        )
    else:
        checks.append(
            IntegrityCheck("digests_match_across_tables", INTEGRITY_FAILED, "元数据与文档摘要不一致")
        )

    # 检查 5：结果文档必需字段齐全（缺失必需字段 = 结构损坏；可选字段缺失不算）
    if document is not None:
        missing = [f for f in _REQUIRED_DOCUMENT_FIELDS if f not in document]
        if missing:
            checks.append(
                IntegrityCheck(
                    "required_fields_present",
                    INTEGRITY_FAILED,
                    f"结果文档缺少必需字段：{', '.join(missing)}",
                )
            )
        else:
            checks.append(
                IntegrityCheck(
                    "required_fields_present", INTEGRITY_PASSED, "结果文档必需字段齐全"
                )
            )

    return IntegrityReport(
        run_id=run_id,
        status=_aggregate_integrity_status(checks),
        checks=tuple(checks),
        metadata_result_digest=meta_digest,
        document_result_digest=doc_digest,
        recomputed_digest=recomputed,
        audited_at=_now_utc_iso(),
    )


__all__ = [
    "INTEGRITY_FAILED",
    "INTEGRITY_INCOMPLETE",
    "INTEGRITY_PASSED",
    "RUN_STATUS_SUCCEEDED",
    "IntegrityCheck",
    "IntegrityReport",
    "PersistenceError",
    "ResultDocumentCorruptedError",
    "RunRecord",
    "audit_run_integrity",
    "count_runs",
    "get_run",
    "get_run_document",
    "get_run_with_document",
    "list_runs",
    "result_digest",
    "save_backtest_result",
]
