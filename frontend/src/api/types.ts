/**
 * 后端 API 类型定义（与 backend OpenAPI / Pydantic 模型对齐）。
 *
 * 契约来源（Step 5.7 已核实的后端实现）：
 * - GET  /datasets   → DatasetListResponse（api/schemas.py）
 * - GET  /strategies → StrategyListResponse（api/schemas.py）
 * - POST /backtests  → BacktestResponse，result 为 result_to_dict 原样输出
 *   （performance/serialize.py，performance 可能为 null）。
 *
 * 注意：本文件为手写类型，字段与后端实际实现保持一致；后端契约变化时需同步更新。
 */

// --------------------------------------------------------------------------- //
// GET /datasets
// --------------------------------------------------------------------------- //

export interface DatasetEntry {
  symbol: string;
  /** 清洗后的日线行数（回测实际使用的数据量） */
  rows: number;
  start_date: string;
  end_date: string;
  source_name: string | null;
  /** 导入时原始 CSV 文件的 SHA-256 指纹（注意：不是处理后数据的摘要） */
  source_sha256: string | null;
  ingested_at: string | null;
}

export interface InvalidDatasetEntry {
  metadata_file: string;
  symbol: string | null;
  reason: string;
}

export interface DatasetListResponse {
  datasets: DatasetEntry[];
  invalid_datasets: InvalidDatasetEntry[];
}

// --------------------------------------------------------------------------- //
// GET /strategies
// --------------------------------------------------------------------------- //

export interface StrategyParamInfo {
  type: string;
  default: unknown;
  description: string;
  minimum: number | null;
}

export interface StrategyInfo {
  strategy_id: string;
  display_name: string;
  version: string;
  description: string;
  params: Record<string, StrategyParamInfo>;
}

export interface StrategyListResponse {
  strategies: StrategyInfo[];
}

// --------------------------------------------------------------------------- //
// POST /backtests 请求体
// --------------------------------------------------------------------------- //

export interface BacktestRequestPayload {
  dataset_symbol: string;
  strategy_id: string;
  strategy_params: Record<string, number | string | boolean>;
  initial_cash: number;
  /** YYYY-MM-DD；不传则由后端使用数据集全区间 */
  start_date?: string;
  end_date?: string;
  /** 信号延迟（>=1 个交易日）；不传则使用后端默认值 1 */
  signal_lag?: number;
  /** 是否持久化到历史记录；不传则后端默认 true（Step 6.2 新增） */
  save?: boolean;
}

// --------------------------------------------------------------------------- //
// POST /backtests 响应（result = result_to_dict 输出）
// --------------------------------------------------------------------------- //

export interface ApiInfo {
  name: string;
  version: string;
}

export interface MarketRulesInfo {
  name: string;
  lot_size: number;
  t_plus_one: boolean;
  price_limit: { rate: number; tick_size: number } | null;
}

export interface BacktestConfigInfo {
  symbol: string;
  initial_cash: number;
  start_date: string | null;
  end_date: string | null;
  exec_price_rule: string;
  signal_lag: number;
  market_rules: MarketRulesInfo;
  commission_rate: number;
  min_commission: number;
  stamp_duty_rate: number;
  transfer_fee_rate: number;
  slippage_buy: number;
  slippage_sell: number;
  fee_source: string;
  assumptions: string[];
}

export interface OrderInfo {
  order_id: string;
  symbol: string;
  side: string;
  signal_date: string;
  order_date: string;
  planned_exec_date: string;
  quantity: number;
  status: string;
  reason: string | null;
  fill_id: string | null;
}

export interface FeesInfo {
  commission: number;
  stamp_duty: number;
  transfer_fee: number;
}

export interface FillInfo {
  fill_id: string;
  order_id: string;
  symbol: string;
  side: string;
  fill_date: string;
  price: number;
  quantity: number;
  fees: FeesInfo;
}

export interface PositionInfo {
  symbol: string;
  quantity: number;
  bought_today: number;
  avg_cost: number | null;
}

export interface AccountStateInfo {
  date: string;
  cash: number;
  position: PositionInfo;
  close: number | null;
  market_value: number | null;
  total_equity: number | null;
  valuation_status: string;
}

export interface DailyReturnPoint {
  date: string;
  equity: number | null;
  prev_equity: number | null;
  value: number | null;
}

export interface EquityCurvePoint {
  date: string;
  total_equity: number | null;
}

export interface TradeStatsInfo {
  n_orders: number;
  n_filled_orders: number;
  n_unfilled_orders: number;
  n_fills: number;
  buy_gross_amount: number;
  sell_gross_amount: number;
  total_fees: number;
  n_closed_trades: number;
  n_winning_trades: number;
  n_losing_trades: number;
  n_breakeven_trades: number;
  win_rate: number | null;
  open_position_quantity: number;
}

export interface PerformanceInfo {
  initial_equity: number;
  final_equity: number | null;
  total_pnl: number | null;
  cumulative_return: number | null;
  daily_returns: DailyReturnPoint[];
  equity_curve: EquityCurvePoint[];
  max_drawdown_amount: number | null;
  max_drawdown_ratio: number | null;
  max_drawdown_peak_date: string | null;
  max_drawdown_trough_date: string | null;
  drawdown_observation_count: number;
  annualized_return: number | null;
  annualized_volatility: number | null;
  trade_stats: TradeStatsInfo;
  periods_per_year: number;
  is_complete: boolean;
  incomplete_reasons: string[];
  unavailable_metrics: string[];
}

/** result.strategy：capture_strategy_metadata 输出（performance/reproducibility.py） */
export interface StrategyMetaInfo {
  name: string | null;
  version: string | null;
  params: unknown;
  params_available: boolean;
  identified: boolean;
  reproducible: boolean;
  notes: string[];
}

/** result.dataset：dataset_summary 输出（performance/reproducibility.py） */
export interface DatasetMetaInfo {
  dataset_id: string | null;
  n_rows: number;
  columns: string[];
  date_range: { start: string; end: string } | null;
  /** 处理后输入数据的确定性摘要（非原始 CSV 文件指纹） */
  digest: string;
  digest_algorithm: string;
  canonicalization: Record<string, string>;
}

/** result.reproducibility：reproducibility_report 输出 */
export interface ReproducibilityInfo {
  inputs_identifiable: boolean;
  process_reproducible: boolean;
  missing: string[];
  notes: string[];
}

export interface BacktestResult {
  schema_version: string;
  engine_version: string;
  config: BacktestConfigInfo;
  period: { start: string | null; end: string | null };
  strategy: StrategyMetaInfo | null;
  dataset: DatasetMetaInfo | null;
  orders: OrderInfo[];
  fills: FillInfo[];
  account_states: AccountStateInfo[];
  data_quality_issues: string[];
  limitations: string[];
  is_complete: boolean;
  incomplete_reasons: string[];
  performance: PerformanceInfo | null;
  reproducibility: ReproducibilityInfo;
}

export interface BacktestResponse {
  api: ApiInfo;
  result: BacktestResult;
  /** 本次回测运行标识；save=false 时为 null（Step 6.2 新增，可选字段） */
  run_id?: string | null;
}

// --------------------------------------------------------------------------- //
// Step 6.3：历史回测列表 & 详情（后端 GET /backtests + GET /backtests/{run_id}）
// --------------------------------------------------------------------------- //

export interface BacktestRunListItem {
  run_id: string;
  created_at: string;
  status: string;
  symbol: string;
  strategy_id: string | null;
  strategy_version: string | null;
  strategy_params: Record<string, unknown> | null;
  dataset_symbol: string;
  start_date: string | null;
  end_date: string | null;
  initial_cash: number;
  source_sha256: string | null;
  dataset_digest: string | null;
  result_digest: string | null;
  is_complete: boolean;
  duration_ms: number | null;
}

export interface BacktestRunListResponse {
  items: BacktestRunListItem[];
  /** 符合过滤条件的总记录数（非当前页数量） */
  total: number;
  limit: number;
  offset: number;
}

export interface BacktestRunDetailResponse {
  run_id: string;
  metadata: BacktestRunListItem;
  /** 完整历史结果；结构与 BacktestResponse.result 相同 */
  result: BacktestResult;
}

// --------------------------------------------------------------------------- //
// Step 6.7：只读完整性审计（GET /backtests/{run_id}/integrity）
// --------------------------------------------------------------------------- //

/** 审计三态：passed=已执行检查均通过；failed=存在明确完整性问题；incomplete=信息不足无法完成全部检查 */
export type BacktestIntegrityStatus = "passed" | "failed" | "incomplete";

export interface BacktestIntegrityCheck {
  name: string;
  /** 单项检查状态（passed / failed / incomplete） */
  status: BacktestIntegrityStatus | string;
  message: string;
}

export interface BacktestIntegrityResponse {
  run_id: string;
  status: BacktestIntegrityStatus | string;
  checks: BacktestIntegrityCheck[];
  metadata_result_digest: string | null;
  document_result_digest: string | null;
  recomputed_digest: string | null;
  /** 本次审计执行时间（UTC ISO-8601） */
  audited_at: string;
}

// --------------------------------------------------------------------------- //
// Step 6.8：只读复现信息清单（GET /backtests/{run_id}/reproducibility）
// --------------------------------------------------------------------------- //

/**
 * 单项复现信息的保存 / 可验证状态。
 * - available：记录中存在；
 * - missing：记录中缺失；
 * - verifiable：能依据现有材料验证；
 * - not_verifiable：缺少必要材料，无法验证；
 * - not_archived：原始数据或必要文件未归档；
 * - not_recorded：历史运行时没有保存相关信息；
 * - not_applicable：该项不适用。
 */
export type BacktestReproItemStatus =
  | "available"
  | "missing"
  | "verifiable"
  | "not_verifiable"
  | "not_archived"
  | "not_recorded"
  | "not_applicable";

export interface BacktestReproItem {
  key: string;
  label: string;
  category: string;
  status: BacktestReproItemStatus | string;
  detail: string;
  value: string | null;
}

/** 某一层级（数据 / 代码 / 环境）的复现能力状态 */
export type BacktestReproLevelStatus = "full" | "partial" | "none" | "undetermined";

export interface BacktestReproLevel {
  /** data / code / environment */
  level: string;
  status: BacktestReproLevelStatus | string;
  basis: string;
  available_materials: string[];
  missing_materials: string[];
  impact: string;
}

export interface BacktestReproducibilityResponse {
  run_id: string;
  generated_at: string;
  /** 结果文档可读性：ok / missing / corrupted */
  result_document_state: string;
  items: BacktestReproItem[];
  levels: BacktestReproLevel[];
  missing_info: string[];
  conclusion: string;
}

// --------------------------------------------------------------------------- //
// Step 6.5：历史回测比较（POST /backtests/compare）
// --------------------------------------------------------------------------- //

export interface BacktestCompareRequestPayload {
  /** 至少 2 条、最多 5 条 run_id；顺序决定响应顺序 */
  run_ids: string[];
}

export interface BacktestCompareMetrics {
  initial_equity: number | null;
  final_equity: number | null;
  total_pnl: number | null;
  cumulative_return: number | null;
  annualized_return: number | null;
  annualized_volatility: number | null;
  max_drawdown_amount: number | null;
  max_drawdown_ratio: number | null;
  n_orders: number | null;
  n_filled_orders: number | null;
  n_fills: number | null;
  n_closed_trades: number | null;
  win_rate: number | null;
}

export interface BacktestCompareItem {
  run_id: string;
  metadata: BacktestRunListItem;
  metrics: BacktestCompareMetrics;
}

/** compare_metadata() 输出的结构（performance/comparison.py）。 */
export interface BacktestComparePair {
  fairly_comparable: boolean;
  identical: Record<string, unknown>;
  differences: Record<string, [unknown, unknown]>;
  reasons: string[];
  strategy_differs: boolean;
}

export interface BacktestCompareResponse {
  items: BacktestCompareItem[];
  /** 两两比较；键为 sorted run_id 用 "||" 连接 */
  pairwise: Record<string, BacktestComparePair>;
  /** 面向用户的警示（含不可公平比较 / 关键差异） */
  warnings: string[];
}
