"""API 请求 / 响应的 Pydantic 模型。

约定：
- 请求模型 ``extra="forbid"``：未知字段一律拒绝，避免静默忽略拼错的参数；
- 标识符（dataset_symbol / strategy_id）使用与数据集白名单一致的字符约束，
  在参数校验阶段即排除路径分隔符、盘符与 ``..``；
- 数值字段禁止 NaN / Infinity（allow_inf_nan=False）；
- 响应中的 ``result`` 原样承载 ``result_to_dict`` 输出（既有 JSON 契约不变），
  API 元信息放在外层 ``api`` 字段，不侵入结果文档。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from deepquant.backtest.config import (
    SIMULATION_COMMISSION_RATE,
    SIMULATION_MIN_COMMISSION,
    SIMULATION_SLIPPAGE_BUY,
    SIMULATION_SLIPPAGE_SELL,
    SIMULATION_STAMP_DUTY_RATE,
    SIMULATION_TRANSFER_FEE_RATE,
)

# 与 api.datasets.SYMBOL_PATTERN 保持一致（Pydantic 用字符串形式）
IDENTIFIER_REGEX = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
# 列表元素级别的完整匹配版本（复用同一份字符集，避免两处漂移）
_IDENTIFIER_FULL = re.compile(IDENTIFIER_REGEX)


# --------------------------------------------------------------------------- #
# GET /datasets
# --------------------------------------------------------------------------- #


class DatasetEntry(BaseModel):
    """单个可用数据集的对外摘要（字段来自已落盘元数据）。"""

    symbol: str
    rows: int = Field(description="清洗后的日线行数（回测实际使用的数据量）")
    start_date: str
    end_date: str
    source_name: str | None = None
    source_sha256: str | None = Field(
        default=None, description="导入时原始 CSV 文件的 SHA-256 指纹"
    )
    ingested_at: str | None = None
    display_name: str | None = Field(
        default=None,
        description="标的显示名称（如「平安银行」）；缺失时前端只显示 symbol",
    )


class InvalidDatasetEntry(BaseModel):
    """未通过校验的元数据条目（只暴露文件名与通用原因）。"""

    metadata_file: str
    symbol: str | None = None
    reason: str


class UnavailableDatasetEntry(BaseModel):
    """导入时被闸门拒绝的标的：解释「为什么列表里没有它」。

    与 ``InvalidDatasetEntry`` 的区别（两者不是一回事，混起来会误导）：
    - ``invalid_datasets``：元数据**已存在**但坏了（文件缺失/字段非法），是数据仓库的现场状态；
    - ``unavailable_datasets``：**从未入库**，导入时就被拒绝，是历史判定结果。
    本字段只作解释，不参与任何计算，也不能被选为回测标的。
    """

    symbol: str
    display_name: str | None = Field(default=None, description="导入清单里记录的标的名称")
    reason: str = Field(description="导入时的拒绝原因码（如 invalid_price(131)）")
    checked_at: str | None = Field(default=None, description="导入清单生成时间（ISO-8601）")


class DatasetCoverage(BaseModel):
    """数据覆盖声明：把「区间为什么到这里为止」写进接口，而不是只写在文档里。"""

    start_date: str | None = Field(default=None, description="全部可用数据集里最早的起始日")
    end_date: str | None = Field(default=None, description="全部可用数据集里最晚的结束日")
    n_datasets: int
    n_unavailable: int
    note: str = Field(description="覆盖范围与已知偏差的说明（含池快照与幸存者偏差提示）")


class DatasetListResponse(BaseModel):
    datasets: list[DatasetEntry]
    invalid_datasets: list[InvalidDatasetEntry]
    unavailable_datasets: list[UnavailableDatasetEntry] = Field(
        default_factory=list,
        description="导入时被拒绝、因此不在可选列表里的标的（解释性信息）",
    )
    coverage: DatasetCoverage | None = Field(
        default=None, description="数据覆盖范围与偏差说明；为空表示未计算"
    )


# --------------------------------------------------------------------------- #
# GET /strategies
# --------------------------------------------------------------------------- #


class StrategyParamInfo(BaseModel):
    type: str
    default: Any
    description: str
    minimum: float | None = None


class StrategyInfo(BaseModel):
    strategy_id: str
    display_name: str
    version: str
    description: str
    params: dict[str, StrategyParamInfo]


class StrategyListResponse(BaseModel):
    strategies: list[StrategyInfo]


# --------------------------------------------------------------------------- #
# POST /backtests
# --------------------------------------------------------------------------- #


class BacktestRequest(BaseModel):
    """同步回测请求。

    费用与滑点参数的默认值为**模拟占位参数**（仅用于开发/测试/模拟），
    不代表真实券商费率；响应中通过 config.fee_source 标明实际来源。
    """

    model_config = ConfigDict(extra="forbid")

    dataset_symbol: str = Field(pattern=IDENTIFIER_REGEX, description="已登记的数据集标识")
    strategy_id: str = Field(pattern=IDENTIFIER_REGEX, description="策略注册表中的标识符")
    strategy_params: dict[str, Any] = Field(default_factory=dict)
    initial_cash: float = Field(gt=0, allow_inf_nan=False, description="初始资金")
    start_date: date | None = Field(default=None, description="回测起始日（闭区间）")
    end_date: date | None = Field(default=None, description="回测结束日（闭区间）")
    signal_lag: int = Field(default=1, ge=1, description="信号延迟（>= 1 个交易日）")
    save: bool = Field(
        default=True,
        description="是否将本次回测结果持久化到历史记录（默认 true）。"
        "为 false 时执行回测但不写入历史记录，响应 run_id 为 null。",
    )

    # --- 费用与滑点（模拟占位默认值，可在请求中显式覆盖）---
    commission_rate: float = Field(default=SIMULATION_COMMISSION_RATE, ge=0, allow_inf_nan=False)
    min_commission: float = Field(default=SIMULATION_MIN_COMMISSION, ge=0, allow_inf_nan=False)
    stamp_duty_rate: float = Field(default=SIMULATION_STAMP_DUTY_RATE, ge=0, allow_inf_nan=False)
    transfer_fee_rate: float = Field(default=SIMULATION_TRANSFER_FEE_RATE, ge=0, allow_inf_nan=False)
    slippage_buy: float = Field(default=SIMULATION_SLIPPAGE_BUY, ge=0, allow_inf_nan=False)
    slippage_sell: float = Field(default=SIMULATION_SLIPPAGE_SELL, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_date_range(self) -> BacktestRequest:
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        return self


class ApiInfo(BaseModel):
    """响应外层元信息（不侵入既有结果 JSON 契约）。"""

    name: str
    version: str


class BacktestResponse(BaseModel):
    api: ApiInfo
    result: dict[str, Any] = Field(
        description="result_to_dict 的原始输出（schema_version/engine_version/config/period/"
        "strategy/dataset/orders/fills/account_states/performance/reproducibility 等）"
    )
    run_id: str | None = Field(
        default=None,
        description="本次回测运行的唯一标识。save=true 且保存成功时非空；"
        "save=false 时为 null。新增字段，不影响旧客户端。",
    )


# --------------------------------------------------------------------------- #
# GET /backtests  历史回测列表
# --------------------------------------------------------------------------- #


class BacktestRunListItem(BaseModel):
    """历史回测列表中的单条元数据摘要（不包含完整结果文档）。"""

    run_id: str
    created_at: str
    status: str
    symbol: str
    strategy_id: str | None
    strategy_version: str | None
    strategy_params: dict[str, Any] | None = None
    dataset_symbol: str
    start_date: str | None = None
    end_date: str | None = None
    initial_cash: float
    source_sha256: str | None = None
    dataset_digest: str | None = None
    result_digest: str | None = None
    is_complete: bool
    duration_ms: int | None = None


class BacktestRunListResponse(BaseModel):
    """历史回测列表响应。"""

    items: list[BacktestRunListItem]
    total: int = Field(description="符合过滤条件的总记录数（非当前页数量）")
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# GET /backtests/{run_id}  历史回测详情
# --------------------------------------------------------------------------- #


class BacktestRunDetailResponse(BaseModel):
    """历史回测详情响应：元数据 + 完整结果文档。"""

    run_id: str
    metadata: BacktestRunListItem
    result: dict[str, Any] = Field(description="已保存的完整回测结果文档（result_to_dict 输出）")


# --------------------------------------------------------------------------- #
# GET /backtests/{run_id}/integrity  只读完整性审计
# --------------------------------------------------------------------------- #


class BacktestIntegrityCheck(BaseModel):
    """单项完整性检查结果。

    ``status`` 三态：passed / failed / incomplete。
    - failed：存在明确的完整性问题（如摘要不一致、文档缺失）；
    - incomplete：必要信息缺失，无法完成该检查（**不等于**校验通过）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    status: str
    message: str


class BacktestIntegrityResponse(BaseModel):
    """只读完整性审计响应。

    仅描述「已保存结果文档与其摘要是否一致」与「必需字段是否齐全」，
    不表示策略结果正确、也不表示回测可复现（后者见结果文档的 reproducibility）。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str = Field(description="passed / failed / incomplete")
    checks: list[BacktestIntegrityCheck]
    metadata_result_digest: str | None = None
    document_result_digest: str | None = None
    recomputed_digest: str | None = None
    audited_at: str = Field(description="本次审计的执行时间（UTC ISO-8601）")


# --------------------------------------------------------------------------- #
# GET /backtests/{run_id}/reproducibility  只读复现信息清单
# --------------------------------------------------------------------------- #


class BacktestReproItem(BaseModel):
    """复现清单中的单项：某类信息的保存 / 可验证状态。

    ``status`` 取值（前后端一致）：
    - available：记录中存在；
    - missing：记录中缺失；
    - verifiable：能依据现有材料验证；
    - not_verifiable：缺少必要材料，无法验证；
    - not_archived：原始数据或必要文件未归档；
    - not_recorded：历史运行时没有保存相关信息；
    - not_applicable：该项不适用。

    注意：字段存在不等于内容真实可靠；摘要存在不等于原始文件存在；
    缺少必要证据时一律如实标注，绝不推断为「已验证」。
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    category: str
    status: str
    detail: str
    value: str | None = None


class BacktestReproLevel(BaseModel):
    """某一层级（data / code / environment）的复现能力评估。

    ``status`` 取值：full（具备）/ partial（部分具备）/ none（不具备）/
    undetermined（无法判断）。缺少关键证据时不得标记为 full。
    """

    model_config = ConfigDict(extra="forbid")

    level: str = Field(description="data / code / environment")
    status: str = Field(description="full / partial / none / undetermined")
    basis: str
    available_materials: list[str]
    missing_materials: list[str]
    impact: str


class BacktestReproducibilityResponse(BaseModel):
    """只读复现信息清单响应。

    明确区分三件事（不得混为一谈）：
    1. 记录了哪些信息（items.status）；
    2. 能否被验证（items.status / levels）；
    3. 凭现有材料能否重新执行（levels）。

    只读：不重跑回测、不写入、不修改历史记录。``result_document_state`` 仅表示
    结果文档能否被读取（ok / missing / corrupted）；完整性审计结论见 /integrity。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    generated_at: str = Field(description="本清单的生成时间（UTC ISO-8601）")
    result_document_state: str = Field(description="ok / missing / corrupted（结果文档可读性）")
    items: list[BacktestReproItem]
    levels: list[BacktestReproLevel]
    missing_info: list[str] = Field(description="影响复现的关键缺失信息摘要")
    conclusion: str = Field(description="复现能力结论（不宣称完全可复现）")


# --------------------------------------------------------------------------- #
# POST /backtests/compare  历史回测比较
# --------------------------------------------------------------------------- #

MAX_COMPARE_RUNS = 5  # 最多比较 5 条
MIN_COMPARE_RUNS = 2  # 至少 2 条


class BacktestCompareRequest(BaseModel):
    """比较请求：run_ids 顺序即展示顺序（稳定对应 response.items）。"""

    model_config = ConfigDict(extra="forbid")

    run_ids: list[str] = Field(
        ...,
        min_length=MIN_COMPARE_RUNS,
        max_length=MAX_COMPARE_RUNS,
        description="至少 2 条、最多 5 条待比较的 run_id",
    )

    @model_validator(mode="after")
    def _validate_ids(self) -> "BacktestCompareRequest":
        # 空字符串 / 首尾空白 / 重复 均不允许
        cleaned = [s.strip() for s in self.run_ids]
        if any(s == "" for s in cleaned):
            raise ValueError("run_ids 中不得包含空字符串")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("run_ids 不得重复")
        self.run_ids = cleaned
        return self


class BacktestCompareMetrics(BaseModel):
    """从完整结果文档的 performance 中提取、用于比较的绩效指标。

    缺失指标显式为 null；绝不伪造 0。
    """

    model_config = ConfigDict(extra="forbid")

    initial_equity: float | None = None
    final_equity: float | None = None
    total_pnl: float | None = None
    cumulative_return: float | None = None
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    max_drawdown_amount: float | None = None
    max_drawdown_ratio: float | None = None
    n_orders: int | None = None
    n_filled_orders: int | None = None
    n_fills: int | None = None
    n_closed_trades: int | None = None
    win_rate: float | None = None


class BacktestCompareItem(BaseModel):
    """单条回测在比较响应中的项。"""

    run_id: str
    metadata: BacktestRunListItem
    metrics: BacktestCompareMetrics


class BacktestCompareResponse(BaseModel):
    """历史回测比较响应。"""

    model_config = ConfigDict(extra="forbid")

    items: list[BacktestCompareItem]
    # 两两比较结果（键为 "a_id||b_id"，值为 compare_metadata() 的输出）
    pairwise: dict[str, dict[str, Any]] = Field(
        description="两两可比性分析；键为 sorted run_id 用 '||' 连接",
    )
    # 整体警告：存在多少对不可公平比较 / 关键配置差异
    warnings: list[str] = Field(
        description="面向用户的差异说明，不包含服务端内部细节",
    )


# --------------------------------------------------------------------------- #
# POST /portfolio-backtests  多标的组合回测
# --------------------------------------------------------------------------- #

#: 组合至少 2 只（1 只不是组合，是单标的——那应该走 /backtests）
MIN_PORTFOLIO_SYMBOLS = 2
#: 上限与预登记池规模同量级；再大不是算不动，而是「等权 top 20%」会退化成指数
MAX_PORTFOLIO_SYMBOLS = 60


class PortfolioBacktestRequest(BaseModel):
    """组合回测请求。

    默认值就是 ``scripts/gate1_run.py`` 的主实验口径（月度调仓 / 动量 60·5 /
    等权 top 20% / min_assets=5 / 初始资金 100 万），因此界面用默认值跑出来的
    结果与研究报告同口径。改任何一个字段都会在响应 ``limitations`` 里标注偏差。
    """

    model_config = ConfigDict(extra="forbid")

    dataset_symbols: list[str] = Field(
        ...,
        min_length=MIN_PORTFOLIO_SYMBOLS,
        max_length=MAX_PORTFOLIO_SYMBOLS,
        description="标的池（已登记的数据集标识），至少 2 只，不允许重复",
    )
    initial_cash: float = Field(gt=0, allow_inf_nan=False, description="组合初始资金")

    selection: Literal["momentum_top", "equal_weight_all"] = Field(
        default="momentum_top",
        description="选股口径：momentum_top=截面动量取前 top_fraction；"
        "equal_weight_all=全池等权（此时 top_fraction 被强制为 1.0）",
    )
    top_fraction: float = Field(
        default=0.2, gt=0, le=1, allow_inf_nan=False, description="每期取前多少比例（等权）"
    )
    lookback: int = Field(default=60, ge=2, le=250, description="动量回看窗口（交易日）")
    skip: int = Field(
        default=5, ge=0, le=60, description="窗口末端跳过的交易日（剔除短期反转）"
    )
    min_assets: int = Field(
        default=5,
        ge=1,
        description="可参与排序的标的少于此数时当期空仓（预登记 §5 = 5）",
    )
    max_weight_per_asset: float | None = Field(
        default=None,
        gt=0,
        le=1,
        allow_inf_nan=False,
        description="单标的上限；None 表示不限制（等权时即 1/k）",
    )
    allocation: Literal["weights", "lots"] = Field(
        default="weights",
        description="weights=按权重记账（主口径）；lots=整手约束（可执行性检验）",
    )

    start_date: date | None = Field(default=None, description="回测起始日（闭区间）")
    end_date: date | None = Field(default=None, description="回测结束日（闭区间）")

    # --- 费用与滑点（与 /backtests 同一组模拟占位默认值）---
    fee_source: str = Field(
        default="simulation_placeholder",
        min_length=1,
        max_length=64,
        description="费用来源标注；默认 simulation_placeholder 表示模拟占位费率",
    )
    commission_rate: float = Field(default=SIMULATION_COMMISSION_RATE, ge=0, allow_inf_nan=False)
    min_commission: float = Field(default=SIMULATION_MIN_COMMISSION, ge=0, allow_inf_nan=False)
    stamp_duty_rate: float = Field(default=SIMULATION_STAMP_DUTY_RATE, ge=0, allow_inf_nan=False)
    transfer_fee_rate: float = Field(
        default=SIMULATION_TRANSFER_FEE_RATE, ge=0, allow_inf_nan=False
    )
    slippage_buy: float = Field(default=SIMULATION_SLIPPAGE_BUY, ge=0, allow_inf_nan=False)
    slippage_sell: float = Field(default=SIMULATION_SLIPPAGE_SELL, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_combination(self) -> "PortfolioBacktestRequest":
        cleaned = [s.strip() for s in self.dataset_symbols]
        if any(s == "" for s in cleaned):
            raise ValueError("dataset_symbols 中不得包含空字符串")
        # 逐个校验标识符字符集：路径分隔符、盘符、``..`` 在这一步就被拒，
        # 不会走到文件系统再靠 resolve 兜底（纵深防御，两道都要在）。
        bad = [s for s in cleaned if not _IDENTIFIER_FULL.match(s)]
        if bad:
            raise ValueError(f"dataset_symbols 含非法标识符：{bad[:3]}")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("dataset_symbols 不得重复（同一只标的不能占两个权重位）")
        self.dataset_symbols = cleaned

        if self.skip >= self.lookback:
            raise ValueError(
                f"skip({self.skip}) 必须小于 lookback({self.lookback})，否则窗口两端点位置重叠"
            )
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError("start_date 不能晚于 end_date")
        # 池子比 min_assets 还小时，整段回测会「每期空仓」——那看起来像一条
        # 平稳的现金曲线，而不是一个错误。宁可在这里拒绝，也不让用户拿到
        # 一份「跑了但什么都没买」的结果。
        if self.min_assets > len(self.dataset_symbols):
            raise ValueError(
                f"min_assets={self.min_assets} 大于标的数 {len(self.dataset_symbols)}："
                "每期都凑不满可排序标的，整段回测会全程空仓。"
                "请多选标的，或把 min_assets 降到不超过标的数。"
            )
        return self


class PortfolioBacktestResponse(BaseModel):
    """组合回测响应：外层 API 元信息 + 结果文档（结构见 api/portfolio.py）。"""

    api: ApiInfo
    result: dict[str, Any] = Field(
        description="schema_version=portfolio-v1；含 config/period/coverage/datasets/"
        "selection/targets/states/fills/blocked/performance/limitations 等"
    )
