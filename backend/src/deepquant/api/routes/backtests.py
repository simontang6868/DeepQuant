"""POST /backtests：同步执行单标的日线回测。

职责边界（API 薄层）：
1. 请求参数已由 Pydantic 模型校验（未知字段拒绝、数值范围、日期范围）；
2. 按 dataset_symbol 在数据根目录内定位数据集（路径安全由 api.datasets 保证）；
3. 载入数据并复用既有数据质量校验（validate_daily）；
4. 通过显式注册表实例化策略；
5. 构造 BacktestConfig 并调用既有 run_backtest；
6. 复用 result_to_dict 生成响应（不重算绩效、不改写序列化逻辑）；
7. 按请求 save 参数，可选持久化回测结果到 SQLite（不改变回测计算语义）。

本层不撮合、不重算绩效、不改变账户状态，也不向客户端暴露文件系统路径。
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, Query

from deepquant.api.config import get_data_root
from deepquant.api.datasets import ensure_data_root, find_dataset
from deepquant.api.errors import (
    BacktestRejectedError,
    CompareRequestError,
    ConfigInvalidError,
    DatasetUnavailableError,
    RunIntegrityError,
    RunNotFoundError,
)
from deepquant.api.registry import build_strategy
from deepquant.api.schemas import (
    ApiInfo,
    BacktestCompareItem,
    BacktestCompareMetrics,
    BacktestCompareRequest,
    BacktestCompareResponse,
    BacktestIntegrityCheck,
    BacktestIntegrityResponse,
    BacktestReproItem,
    BacktestReproLevel,
    BacktestReproducibilityResponse,
    BacktestRequest,
    BacktestResponse,
    BacktestRunDetailResponse,
    BacktestRunListItem,
    BacktestRunListResponse,
)
from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.data.pipeline import DataValidationError
from deepquant.data.storage.io import read_daily_parquet
from deepquant.data.validation.checks import ERROR, validate_daily
from deepquant.performance.comparison import compare_metadata, result_profile
from deepquant.performance.serialize import result_from_dict, result_to_dict
from deepquant.persistence import (
    RunRecord,
    audit_run_integrity,
    build_reproducibility_checklist,
    count_runs,
    get_db_path,
    get_run_with_document,
    list_runs,
    save_backtest_result,
)
from deepquant.version import __version__

router = APIRouter()


def _load_dataset_frame(processed_path: Path, symbol: str) -> pd.DataFrame:
    """读取已登记数据集；读取失败视为数据集不可用（详情只记录到服务端日志）。"""
    try:
        return read_daily_parquet(processed_path)
    except Exception as exc:
        raise DatasetUnavailableError(
            f"数据集 {symbol!r} 已登记但不可用：数据文件无法读取"
        ) from exc


def _build_config(request: BacktestRequest) -> BacktestConfig:
    """把请求模型映射为 BacktestConfig（字段一一对应，业务语义由配置类校验）。

    exec_price_rule / market_rules / fee_source 使用引擎既定默认值
    （v1 仅支持 next_open 与 A 股规则；费用来源固定为模拟占位标识）。
    """
    try:
        return BacktestConfig(
            symbol=request.dataset_symbol,
            initial_cash=request.initial_cash,
            start_date=None if request.start_date is None else pd.Timestamp(request.start_date),
            end_date=None if request.end_date is None else pd.Timestamp(request.end_date),
            signal_lag=request.signal_lag,
            commission_rate=request.commission_rate,
            min_commission=request.min_commission,
            stamp_duty_rate=request.stamp_duty_rate,
            transfer_fee_rate=request.transfer_fee_rate,
            slippage_buy=request.slippage_buy,
            slippage_sell=request.slippage_sell,
        )
    except ValueError as exc:
        raise ConfigInvalidError(str(exc)) from exc


@router.post("/backtests")
def create_backtest(
    request: BacktestRequest, data_root: Path = Depends(get_data_root)
) -> BacktestResponse:
    """同步执行回测并返回结果（外层 api 元信息 + result_to_dict 原始输出）。"""
    ensure_data_root(data_root)

    dataset_info, processed_path = find_dataset(data_root, request.dataset_symbol)
    df = _load_dataset_frame(processed_path, request.dataset_symbol)

    # 复用既有数据质量校验：ERROR 级问题按数据校验错误处理（400）
    report = validate_daily(df)
    if not report.is_valid:
        detail = "; ".join(f"{i.code}({i.count})" for i in report.issues if i.level == ERROR)
        raise DataValidationError(f"数据集 {request.dataset_symbol!r} 存在严重数据质量问题：{detail}")

    strategy = build_strategy(request.strategy_id, request.strategy_params)
    config = _build_config(request)

    # 测量回测执行耗时（不含数据载入与持久化）
    exec_start = time.perf_counter()
    try:
        result = run_backtest(df, strategy, config)
    except ValueError as exc:
        # 请求合法但执行条件不满足（如回测区间内无行情数据）
        raise BacktestRejectedError(str(exc)) from exc
    duration_ms = int((time.perf_counter() - exec_start) * 1000)

    # 生成正式结果文档（与持久化写入的是同一份，确保 API 返回与存储一致）
    document = result_to_dict(result)

    run_id: str | None = None
    if request.save:
        # 持久化失败：由全局异常处理器映射为 500 persistence_failed；
        # 不得在保存失败时返回成功结果。
        run_id = save_backtest_result(
            get_db_path(),
            result,
            strategy_id=request.strategy_id,
            dataset_symbol=dataset_info.symbol,
            source_sha256=dataset_info.source_sha256,
            duration_ms=duration_ms,
        )

    return BacktestResponse(
        api=ApiInfo(name="deepquant-api", version=__version__),
        result=document,
        run_id=run_id,
    )


def _record_to_list_item(record: RunRecord) -> BacktestRunListItem:
    """把持久化层的 RunRecord 映射为列表响应项（不含完整结果文档）。"""
    return BacktestRunListItem(
        run_id=record.run_id,
        created_at=record.created_at,
        status=record.status,
        symbol=record.symbol,
        strategy_id=record.strategy_id,
        strategy_version=record.strategy_version,
        strategy_params=record.strategy_params,
        dataset_symbol=record.dataset_symbol,
        start_date=record.start_date,
        end_date=record.end_date,
        initial_cash=record.initial_cash,
        source_sha256=record.source_sha256,
        dataset_digest=record.dataset_digest,
        result_digest=record.result_digest,
        is_complete=record.is_complete,
        duration_ms=record.duration_ms,
    )


@router.get("/backtests")
def list_backtest_runs(
    limit: int = Query(default=20, ge=1, le=100, description="单页返回数量（1-100）"),
    offset: int = Query(default=0, ge=0, description="跳过的记录数量（>=0）"),
    symbol: str | None = Query(default=None, description="按标的代码过滤"),
    strategy_id: str | None = Query(default=None, description="按策略标识符过滤"),
    status: str | None = Query(default=None, description="按运行状态过滤"),
) -> BacktestRunListResponse:
    """历史回测列表（按 created_at 降序，run_id 次级升序保证分页稳定）。"""
    db_path = get_db_path()
    # count 与 list 使用完全相同的过滤条件，确保 total 准确
    total = count_runs(db_path, symbol=symbol, strategy_id=strategy_id, status=status)
    records = list_runs(
        db_path,
        limit=limit,
        offset=offset,
        symbol=symbol,
        strategy_id=strategy_id,
        status=status,
    )
    return BacktestRunListResponse(
        items=[_record_to_list_item(r) for r in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/backtests/{run_id}")
def get_backtest_run(run_id: str) -> BacktestRunDetailResponse:
    """历史回测详情：返回元数据与完整结果文档。

    - run_id 不存在 → 404 run_not_found；
    - 元数据存在但结果文档缺失 → 500 run_integrity_error（不伪装为 404）；
    - 结果文档存在但 JSON 损坏 → 500 run_result_corrupted（数据完整性问题，
      由 ResultDocumentCorruptedError 全局处理器映射，与数据库故障区分）；
    - 数据库访问 / SQL 失败 → 500 persistence_failed（与数据完整性问题区分）；
    - 不会重新运行回测，也不会修改已有历史记录。
    """
    result = get_run_with_document(get_db_path(), run_id)
    if result is None:
        raise RunNotFoundError(f"回测运行记录不存在：{run_id!r}")

    record, document = result
    if document is None:
        # 元数据存在但 run_documents 缺失：数据完整性内部错误
        raise RunIntegrityError(
            f"回测运行 {run_id!r} 的元数据存在但结果文档缺失"
        )

    return BacktestRunDetailResponse(
        run_id=record.run_id,
        metadata=_record_to_list_item(record),
        result=document,
    )


@router.get("/backtests/{run_id}/integrity")
def get_backtest_run_integrity(run_id: str) -> BacktestIntegrityResponse:
    """只读完整性审计：重算已保存结果文档的摘要并与持久化摘要比对。

    - run_id 不存在 → 404 run_not_found；
    - 数据库访问错误 → 500（persistence_failed / internal_error），与数据完整性问题区分；
    - 数据完整性问题（摘要不一致 / 文档缺失 / 必需字段缺失 / 无法验证）→ 200，
      由 ``status``（passed / failed / incomplete）与 ``checks`` 明确报告。

    本接口严格只读：不重跑回测、不写入、不修改任何历史记录。
    审计通过仅表示「已保存结果文档与其摘要一致」，不代表策略有效，也不代表回测可复现。
    """
    report = audit_run_integrity(get_db_path(), run_id)
    if report is None:
        raise RunNotFoundError(f"回测运行记录不存在：{run_id!r}")

    return BacktestIntegrityResponse(
        run_id=report.run_id,
        status=report.status,
        checks=[
            BacktestIntegrityCheck(name=c.name, status=c.status, message=c.message)
            for c in report.checks
        ],
        metadata_result_digest=report.metadata_result_digest,
        document_result_digest=report.document_result_digest,
        recomputed_digest=report.recomputed_digest,
        audited_at=report.audited_at,
    )


@router.get("/backtests/{run_id}/reproducibility")
def get_backtest_run_reproducibility(run_id: str) -> BacktestReproducibilityResponse:
    """只读复现信息清单：说明历史回测记录了哪些信息、能否验证、还缺什么。

    与 ``/integrity`` 的职责严格区分：
    - ``/integrity``：判断已保存结果文档与其摘要是否一致；
    - ``/reproducibility``：说明「重现本次回测」所需信息的保存情况与当前复现边界。

    错误分级的语义与详情接口一致：
    - run_id 不存在 → 404 run_not_found；
    - 数据库访问错误 → 500 persistence_failed（与「字段缺失」区分）；
    - 结果文档缺失 / 损坏 → 200，由 ``result_document_state``（missing / corrupted）
      如实说明，不抛异常、不伪装为可复现。

    本接口严格只读：不重跑回测、不写入、不修改任何历史记录，也不假定历史记录
    拥有未保存的字段；缺失信息一律如实标注。完整性审计通过不作为「可复现」的依据。
    """
    checklist = build_reproducibility_checklist(get_db_path(), run_id)
    if checklist is None:
        raise RunNotFoundError(f"回测运行记录不存在：{run_id!r}")

    return BacktestReproducibilityResponse(
        run_id=checklist.run_id,
        generated_at=checklist.generated_at,
        result_document_state=checklist.result_document_state,
        items=[
            BacktestReproItem(
                key=i.key,
                label=i.label,
                category=i.category,
                status=i.status,
                detail=i.detail,
                value=i.value,
            )
            for i in checklist.items
        ],
        levels=[
            BacktestReproLevel(
                level=lv.level,
                status=lv.status,
                basis=lv.basis,
                available_materials=list(lv.available_materials),
                missing_materials=list(lv.missing_materials),
                impact=lv.impact,
            )
            for lv in checklist.levels
        ],
        missing_info=list(checklist.missing_info),
        conclusion=checklist.conclusion,
    )


# --------------------------------------------------------------------------- #
# POST /backtests/compare  历史回测比较（只读，不修改数据库）
# --------------------------------------------------------------------------- #


def _extract_metrics(document: dict) -> BacktestCompareMetrics:
    """从完整结果文档 dict 的 performance 字段提取用于比较的绩效指标。

    缺失指标显式为 null；绝不伪造 0。
    """
    perf = document.get("performance")
    if not isinstance(perf, dict):
        return BacktestCompareMetrics()

    initial_equity = perf.get("initial_equity")
    final_equity = perf.get("final_equity")
    total_pnl = perf.get("total_pnl")
    cumulative_return = perf.get("cumulative_return")
    annualized_return = perf.get("annualized_return")
    annualized_volatility = perf.get("annualized_volatility")
    max_drawdown_amount = perf.get("max_drawdown_amount")
    max_drawdown_ratio = perf.get("max_drawdown_ratio")

    trade_stats = perf.get("trade_stats")
    if isinstance(trade_stats, dict):
        n_orders = trade_stats.get("n_orders")
        n_filled = trade_stats.get("n_filled_orders")
        n_fills = trade_stats.get("n_fills")
        n_closed = trade_stats.get("n_closed_trades")
        win_rate = trade_stats.get("win_rate")
    else:
        n_orders = n_filled = n_fills = n_closed = win_rate = None

    def _num(v):
        # 校验 float/int，排除 NaN/Infinity（allow_inf_nan=False 已在 Pydantic 层）
        if isinstance(v, (int, float)) and v is not bool:
            return v
        return None

    return BacktestCompareMetrics(
        initial_equity=_num(initial_equity),
        final_equity=_num(final_equity),
        total_pnl=_num(total_pnl),
        cumulative_return=_num(cumulative_return),
        annualized_return=_num(annualized_return),
        annualized_volatility=_num(annualized_volatility),
        max_drawdown_amount=_num(max_drawdown_amount),
        max_drawdown_ratio=_num(max_drawdown_ratio),
        n_orders=_num(n_orders),
        n_filled_orders=_num(n_filled),
        n_fills=_num(n_fills),
        n_closed_trades=_num(n_closed),
        win_rate=_num(win_rate),
    )


def _pairwise_key(a: str, b: str) -> str:
    # 排序后用 '||' 连接，保证不依赖顺序
    return "||".join(sorted((a, b)))


@router.post("/backtests/compare")
def compare_backtests(request: BacktestCompareRequest) -> BacktestCompareResponse:
    """历史回测结果比较（只读，不修改任何记录）。

    1. 从已持久化的完整结果文档里提取绩效指标与元数据画像；
    2. 两两调用 ``compare_metadata`` 判断可比性（公平 / 部分 / 不可公平）；
    3. 返回所有 run 的指标 + 两两比较 + 总体 warnings；
    4. 不重新运行回测，不修改数据库。
    """
    db_path = get_db_path()

    # 第一阶段：批量读取（任一缺失立即 404，任一文档缺失立即完整性错误）
    records_and_docs: list[tuple[RunRecord, dict]] = []
    for run_id in request.run_ids:
        result = get_run_with_document(db_path, run_id)
        if result is None:
            raise RunNotFoundError(f"回测运行记录不存在：{run_id!r}")
        record, document = result
        if document is None:
            raise RunIntegrityError(
                f"回测运行 {run_id!r} 的元数据存在但结果文档缺失"
            )
        records_and_docs.append((record, document))

    # 第二阶段：提取指标 + 还原 BacktestResult 用于比较
    items: list[BacktestCompareItem] = []
    results = []
    for record, document in records_and_docs:
        try:
            result = result_from_dict(document)
        except Exception as exc:  # noqa: BLE001 — 任何还原失败都视为完整性错误
            raise RunIntegrityError(
                f"回测运行 {record.run_id!r} 的结果文档无法还原为标准模型：{exc}"
            ) from exc
        results.append((record.run_id, result))
        items.append(
            BacktestCompareItem(
                run_id=record.run_id,
                metadata=_record_to_list_item(record),
                metrics=_extract_metrics(document),
            )
        )

    # 第三阶段：两两比较（复用既有 performance.comparison.compare_metadata）
    pairwise: dict[str, dict] = {}
    warnings: list[str] = []

    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            id_a, res_a = results[i]
            id_b, res_b = results[j]
            cmp_result = compare_metadata(res_a, res_b)
            pairwise[_pairwise_key(id_a, id_b)] = cmp_result
            # 只把对用户有意义的 reasons 放进 warnings（去重）
            for reason in cmp_result.get("reasons", []):
                label = f"{id_a[:8]}… vs {id_b[:8]}…: {reason}"
                if label not in warnings:
                    warnings.append(label)

    return BacktestCompareResponse(items=items, pairwise=pairwise, warnings=warnings)
