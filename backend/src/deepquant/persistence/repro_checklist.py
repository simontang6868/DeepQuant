"""只读复现信息清单：说明历史回测记录了哪些信息、能验证什么、还缺什么。

严格只读：只读取已保存的元数据与结果文档，不重跑回测、不写入、不修改历史记录，
也不修改数据库 schema。**不**把「完整性审计通过」等同为「可复现」。

回答三个不同的问题（不得混为一谈）：
1. 记录了哪些信息（available / missing / not_recorded / not_archived）；
2. 这些信息能否被验证（verifiable / not_verifiable）；
3. 凭现有材料能否重新执行并获得可比结果（数据级 / 代码级 / 环境级）。

判断完全基于记录的**实际字段**，缺失一律如实标注，不自动填充默认值，不臆造字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from deepquant.persistence.store import (
    ResultDocumentCorruptedError,
    RunRecord,
    _now_utc_iso,
    get_run,
    get_run_document,
)

# --------------------------------------------------------------------------- #
# 状态枚举（前后端保持一致）
# --------------------------------------------------------------------------- #

ITEM_AVAILABLE = "available"  # 记录中存在
ITEM_MISSING = "missing"  # 记录中缺失
ITEM_VERIFIABLE = "verifiable"  # 能依据现有材料验证
ITEM_NOT_VERIFIABLE = "not_verifiable"  # 缺少必要材料，无法验证
ITEM_NOT_ARCHIVED = "not_archived"  # 原始数据或必要文件未归档
ITEM_NOT_RECORDED = "not_recorded"  # 历史运行时没有保存相关信息
ITEM_NOT_APPLICABLE = "not_applicable"  # 该项不适用

# 三级复现能力状态
LEVEL_FULL = "full"  # 具备
LEVEL_PARTIAL = "partial"  # 部分具备
LEVEL_NONE = "none"  # 不具备
LEVEL_UNDETERMINED = "undetermined"  # 无法判断

# 结果文档可读性（与完整性审计的摘要比对不同，仅表示能否读到文档）
DOC_OK = "ok"
DOC_MISSING = "missing"
DOC_CORRUPTED = "corrupted"


@dataclass(frozen=True)
class ReproItem:
    """清单中的单项：某类复现信息的保存 / 可验证状态。"""

    key: str
    label: str
    category: str
    status: str
    detail: str
    value: str | None = None


@dataclass(frozen=True)
class ReproLevel:
    """某一层级（数据 / 代码 / 环境）的复现能力评估。"""

    level: str
    status: str
    basis: str
    available_materials: tuple[str, ...]
    missing_materials: tuple[str, ...]
    impact: str


@dataclass(frozen=True)
class ReproChecklist:
    """一次只读复现信息清单。"""

    run_id: str
    generated_at: str
    result_document_state: str  # ok / missing / corrupted
    items: tuple[ReproItem, ...]
    levels: tuple[ReproLevel, ...]
    missing_info: tuple[str, ...]
    conclusion: str


def _read_document(db_path: Path, run_id: str) -> tuple[dict | None, str]:
    """读取结果文档并返回 (document, state)。损坏不抛，转为状态报告。"""
    try:
        document = get_run_document(db_path, run_id)
    except ResultDocumentCorruptedError:
        return None, DOC_CORRUPTED
    if document is None:
        return None, DOC_MISSING
    return document, DOC_OK


def _as_dict(value) -> dict | None:
    return value if isinstance(value, dict) else None


def _bool_param_available(record: RunRecord, document: dict | None) -> bool:
    if record.strategy_params:
        return True
    strategy = _as_dict((document or {}).get("strategy"))
    return bool(strategy and strategy.get("params") is not None)


def build_reproducibility_checklist(
    db_path: Path, run_id: str
) -> ReproChecklist | None:
    """构建只读复现信息清单；run 不存在返回 ``None``（API 层映射为 404）。

    数据库访问错误仍抛 ``PersistenceError``（与「字段缺失」区分）。
    """
    record = get_run(db_path, run_id)
    if record is None:
        return None

    document, doc_state = _read_document(db_path, run_id)
    config = _as_dict((document or {}).get("config"))
    dataset = _as_dict((document or {}).get("dataset"))
    strategy = _as_dict((document or {}).get("strategy"))

    items: list[ReproItem] = []

    # --- 回测身份 ---
    items.append(
        ReproItem("run_id", "回测标识（run_id）", "backtest_identity", ITEM_AVAILABLE,
                  "记录中保存了唯一 run_id，可据此唯一定位本次回测", record.run_id)
    )
    items.append(
        ReproItem("created_at", "创建时间", "backtest_identity", ITEM_AVAILABLE,
                  "记录中保存了回测创建时间", record.created_at)
    )
    items.append(
        ReproItem("symbol", "标的", "backtest_identity", ITEM_AVAILABLE,
                  "记录中保存了实际回测标的", record.symbol)
    )
    has_period = record.start_date is not None and record.end_date is not None
    items.append(
        ReproItem("period", "回测区间", "backtest_identity",
                  ITEM_AVAILABLE if has_period else ITEM_MISSING,
                  "记录中保存了实际回测起止区间" if has_period else "未记录回测起止区间",
                  f"{record.start_date} ~ {record.end_date}" if has_period else None)
    )

    # --- 初始资金 ---
    items.append(
        ReproItem("initial_cash", "初始资金", "capital", ITEM_AVAILABLE,
                  "记录中保存了初始资金", f"{record.initial_cash}")
    )

    # --- 策略身份 ---
    items.append(
        ReproItem("strategy_id", "策略标识", "strategy",
                  ITEM_AVAILABLE if record.strategy_id else ITEM_MISSING,
                  "记录中保存了策略注册表标识符" if record.strategy_id else "未记录策略标识",
                  record.strategy_id)
    )
    items.append(
        ReproItem("strategy_version", "策略版本", "strategy",
                  ITEM_AVAILABLE if record.strategy_version else ITEM_MISSING,
                  "记录中保存了策略版本标识" if record.strategy_version else "未记录策略版本",
                  record.strategy_version)
    )
    params_available = _bool_param_available(record, document)
    items.append(
        ReproItem("strategy_params", "策略参数", "strategy",
                  ITEM_AVAILABLE if params_available else ITEM_MISSING,
                  "记录中保存了当时使用的策略参数" if params_available else "未记录策略参数",
                  None)
    )

    # --- 策略代码（结构性缺口：系统不保存策略源码/版本快照）---
    items.append(
        ReproItem("strategy_code_version", "策略代码版本", "strategy", ITEM_NOT_RECORDED,
                  "系统不保存策略源代码、Git commit 或可验证的源码快照；"
                  "仅有 strategy_id/version 标识，无法验证当时实际执行的代码")
    )

    # --- 数据来源 ---
    items.append(
        ReproItem("raw_market_file", "原始行情文件", "data_source", ITEM_NOT_ARCHIVED,
                  "系统不归档原始行情文件，仅保存导入指纹；原始文件能否重新获取取决于外部数据源")
    )
    items.append(
        ReproItem("source_sha256", "原始数据指纹（source_sha256）", "data_source",
                  ITEM_NOT_VERIFIABLE if record.source_sha256 else ITEM_MISSING,
                  "已记录导入时原始 CSV 的 SHA-256 指纹，但原始文件未归档，无法重新核验；"
                  "指纹存在不等于原始数据文件存在"
                  if record.source_sha256 else "未记录原始数据指纹",
                  record.source_sha256)
    )
    items.append(
        ReproItem("dataset_digest", "处理后数据摘要（dataset_digest）", "data_source",
                  ITEM_NOT_VERIFIABLE if record.dataset_digest else ITEM_MISSING,
                  "已记录清洗/规范化后回测输入数据的确定性摘要，但与原始数据指纹含义不同，"
                  "且数据文件未归档，尚不能据此恢复原始文件"
                  if record.dataset_digest else "未记录处理后数据摘要",
                  record.dataset_digest)
    )
    canonicalization = _as_dict((dataset or {}).get("canonicalization"))
    has_processing = bool(canonicalization) and doc_state == DOC_OK
    items.append(
        ReproItem("data_processing_rules", "数据处理规则", "data_source",
                  ITEM_AVAILABLE if has_processing else ITEM_MISSING,
                  "结果文档记录了列顺序 / 日期格式 / 数值表示 / 排序等规范化规则"
                  if has_processing else "未记录（或结果文档不可读）数据处理规则")
    )

    # --- 执行与成本（来自结果文档 config）---
    has_exec = bool(config and config.get("exec_price_rule") is not None) and doc_state == DOC_OK
    items.append(
        ReproItem("execution_rules", "执行规则（信号时点 / 成交价 / 延迟）", "execution",
                  ITEM_AVAILABLE if has_exec else ITEM_MISSING,
                  "结果文档记录了成交价格规则与信号延迟（描述级，可追溯）"
                  if has_exec else "未记录（或结果文档不可读）执行规则")
    )
    has_market = bool(config and _as_dict(config.get("market_rules"))) and doc_state == DOC_OK
    items.append(
        ReproItem("market_rules", "市场规则", "execution",
                  ITEM_AVAILABLE if has_market else ITEM_MISSING,
                  "结果文档记录了市场规则名称 / 最小变动单位 / T+1 / 涨跌停等描述"
                  if has_market else "未记录（或结果文档不可读）市场规则")
    )
    cost_keys = (
        "commission_rate", "min_commission", "stamp_duty_rate",
        "transfer_fee_rate", "slippage_buy", "slippage_sell",
    )
    has_costs = bool(config) and any(config.get(k) is not None for k in cost_keys) and doc_state == DOC_OK
    items.append(
        ReproItem("costs", "交易成本（佣金 / 印花税 / 过户费 / 滑点）", "execution",
                  ITEM_AVAILABLE if has_costs else ITEM_MISSING,
                  "结果文档记录了实际适用的费用与滑点配置"
                  if has_costs else "未记录（或结果文档不可读）交易成本配置")
    )
    has_assumptions = bool(config and "assumptions" in config) and doc_state == DOC_OK
    items.append(
        ReproItem("assumptions", "假设条件", "execution",
                  ITEM_AVAILABLE if has_assumptions else ITEM_MISSING,
                  "结果文档记录了回测假设与限制" if has_assumptions else "未记录（或结果文档不可读）假设条件")
    )

    # --- 引擎与结果结构 ---
    items.append(
        ReproItem("engine_version", "回测引擎版本", "engine",
                  ITEM_AVAILABLE if record.engine_version else ITEM_MISSING,
                  "记录中保存了执行回测时的引擎版本" if record.engine_version else "未记录引擎版本",
                  record.engine_version)
    )
    items.append(
        ReproItem("schema_version", "结果结构版本（schema_version）", "engine",
                  ITEM_AVAILABLE if record.schema_version else ITEM_MISSING,
                  "记录中保存了结果 schema 版本" if record.schema_version else "未记录 schema 版本",
                  record.schema_version)
    )
    items.append(
        ReproItem("runtime_environment", "历史运行环境（Python / 依赖 / 操作系统）", "engine",
                  ITEM_NOT_RECORDED,
                  "历史回测未保存 Python 版本、依赖包版本或操作系统信息，无法还原当时的运行环境")
    )

    # --- 三级复现能力 ---
    data_available: list[str] = []
    data_missing: list[str] = []
    if record.source_sha256:
        data_available.append("原始数据指纹（source_sha256）")
    if record.dataset_digest:
        data_available.append("处理后数据摘要（dataset_digest）")
    if has_processing:
        data_available.append("数据处理规则")
    if has_period:
        data_available.append("回测区间")
    data_missing.append("原始行情数据文件（未归档）")
    data_missing.append("可核验的原始/处理后数据文件")

    if not record.dataset_digest and not record.source_sha256:
        data_status = LEVEL_NONE
        data_basis = "未记录任何数据摘要，无法识别输入数据"
    else:
        data_status = LEVEL_PARTIAL
        data_basis = "记录了输入数据的确定性摘要（处理后）与导入指纹，但没有归档原始行情文件"

    code_available: list[str] = []
    code_missing: list[str] = []
    if record.strategy_id:
        code_available.append("策略标识（strategy_id）")
    if record.strategy_version:
        code_available.append("策略版本")
    if params_available:
        code_available.append("策略参数")
    code_missing.append("策略源代码 / 版本快照 / Git commit")

    code_status = LEVEL_PARTIAL if record.strategy_id else LEVEL_NONE
    code_basis = (
        "记录了策略标识、版本与参数，但没有保存策略源代码或可验证的代码快照"
        if record.strategy_id
        else "未记录策略标识，无法定位当时执行的策略"
    )

    env_available: list[str] = []
    env_missing: list[str] = []
    if record.engine_version:
        env_available.append("回测引擎版本")
    if record.schema_version:
        env_available.append("结果 schema 版本")
    env_missing.append("Python 版本")
    env_missing.append("依赖包版本")
    env_missing.append("操作系统")

    env_status = LEVEL_PARTIAL if record.engine_version else LEVEL_NONE
    env_basis = (
        "记录了回测引擎版本与结果 schema 版本，但没有保存运行环境信息"
        if record.engine_version
        else "未记录引擎版本，无法识别执行环境"
    )

    levels = (
        ReproLevel(
            level="data",
            status=data_status,
            basis=data_basis,
            available_materials=tuple(data_available),
            missing_materials=tuple(data_missing),
            impact="可识别输入数据是否变化，但在缺少原始文件的情况下无法重建回测输入并重跑",
        ),
        ReproLevel(
            level="code",
            status=code_status,
            basis=code_basis,
            available_materials=tuple(code_available),
            missing_materials=tuple(code_missing),
            impact="可定位策略与参数，但无法确认当时执行的具体代码，存在代码变更导致结果不可比的风险",
        ),
        ReproLevel(
            level="environment",
            status=env_status,
            basis=env_basis,
            available_materials=tuple(env_available),
            missing_materials=tuple(env_missing),
            impact="可确认引擎与 schema 版本，但无法重建当时的 Python / 依赖环境",
        ),
    )

    missing_info: list[str] = ["原始行情数据文件", "策略源代码 / 版本快照", "运行环境（Python / 依赖 / 操作系统）"]
    if not has_period:
        missing_info.append("回测区间")
    if not record.strategy_version:
        missing_info.append("策略版本")

    if doc_state == DOC_OK:
        conclusion = (
            "复现能力受限：结果可读取、部分溯源信息可查，但当前材料不足以保证数据级 / "
            "代码级 / 环境级的完整复现。完整性审计通过仅表示结果文档与摘要一致，不代表可复现。"
        )
    elif doc_state == DOC_MISSING:
        conclusion = (
            "复现能力受限：结果文档缺失，只能依赖元数据；数据 / 代码 / 环境级复现信息均不完整。"
        )
    else:  # corrupted
        conclusion = (
            "复现能力受限：结果文档损坏，无法读取其中的策略 / 数据集 / 配置信息；"
            "请勿据此判断结果是否正确。"
        )

    return ReproChecklist(
        run_id=record.run_id,
        generated_at=_now_utc_iso(),
        result_document_state=doc_state,
        items=tuple(items),
        levels=levels,
        missing_info=tuple(missing_info),
        conclusion=conclusion,
    )
