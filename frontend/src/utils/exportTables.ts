/**
 * 把回测结果转成可导出的表格。
 *
 * 与界面展示分开的**唯一理由**：导出的文件要给 Excel / pandas 接着算，
 * 所以这里只扁平化成二维表，不做「暂无」文案替换、不做百分比格式化——
 * 缺失值一律留空，数值一律保留原始小数。
 *
 * 界面里把 0.1234 显示成 "12.34%" 是对的；导出文件里这么干就是毁数据。
 */

import type {
  BacktestResult,
  PortfolioResult,
  PortfolioStateRow,
} from "../api/types";
import { formatDate } from "./format";

/** 缺失值统一留空（不用 0、不用 "-"，避免被当成有效数据参与计算）。 */
function cell(value: unknown): string | number {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isFinite(value) ? value : "";
  return String(value);
}

/** 取日期的 YYYY-MM-DD；无法解析时返回空串。 */
function day(value: string | null | undefined): string {
  return formatDate(value) ?? "";
}

// --------------------------------------------------------------------------- //
// 单标的
// --------------------------------------------------------------------------- //

/** 绩效摘要：指标名 / 数值（数值为原始小数，不做百分比换算）。 */
export function summaryTable(result: BacktestResult) {
  const p = result.performance;
  const headers = ["指标", "数值", "说明"];
  if (!p) return { headers, rows: [] };

  const rows: Array<Record<string, unknown>> = [
    { 指标: "初始权益", 数值: cell(p.initial_equity), 说明: "初始资金" },
    { 指标: "期末权益", 数值: cell(p.final_equity), 说明: "现金 + 持仓市值" },
    { 指标: "累计盈亏", 数值: cell(p.total_pnl), 说明: "期末权益 - 初始权益" },
    { 指标: "累计收益率", 数值: cell(p.cumulative_return), 说明: "小数，0.1234 = 12.34%" },
    { 指标: "年化收益率", 数值: cell(p.annualized_return), 说明: "小数" },
    { 指标: "年化波动率", 数值: cell(p.annualized_volatility), 说明: "小数" },
    { 指标: "最大回撤比例", 数值: cell(p.max_drawdown_ratio), 说明: "小数，取正值" },
    { 指标: "最大回撤金额", 数值: cell(p.max_drawdown_amount), 说明: "金额" },
    { 指标: "回撤峰值日", 数值: day(p.max_drawdown_peak_date), 说明: "" },
    { 指标: "回撤谷底日", 数值: day(p.max_drawdown_trough_date), 说明: "" },
    { 指标: "结果完整", 数值: p.is_complete ? "true" : "false", 说明: "" },
  ];

  const t = p.trade_stats;
  if (t) {
    rows.push(
      { 指标: "订单数", 数值: cell(t.n_orders), 说明: "" },
      { 指标: "成交笔数", 数值: cell(t.n_fills), 说明: "" },
      { 指标: "未成交订单数", 数值: cell(t.n_unfilled_orders), 说明: "" },
      { 指标: "买入总额", 数值: cell(t.buy_gross_amount), 说明: "不含费用" },
      { 指标: "卖出总额", 数值: cell(t.sell_gross_amount), 说明: "不含费用" },
      { 指标: "总费用", 数值: cell(t.total_fees), 说明: "佣金+印花税+过户费" },
      { 指标: "平仓交易数", 数值: cell(t.n_closed_trades), 说明: "" },
      { 指标: "胜率", 数值: cell(t.win_rate), 说明: "小数" },
    );
  }

  if (p.unavailable_metrics.length > 0) {
    rows.push({
      指标: "不可计算指标",
      数值: p.unavailable_metrics.join(" / "),
      说明: "样本不足等因素导致",
    });
  }
  if (p.incomplete_reasons.length > 0) {
    rows.push({ 指标: "不完整原因", 数值: p.incomplete_reasons.join(" / "), 说明: "" });
  }

  return { headers, rows };
}

/** 成交明细（fees 展平成三列，amount 为价×量）。 */
export function fillsTable(result: BacktestResult) {
  const headers = [
    "成交ID",
    "订单ID",
    "日期",
    "标的",
    "方向",
    "价格",
    "数量",
    "成交额",
    "佣金",
    "印花税",
    "过户费",
    "费用合计",
  ];
  const rows = result.fills.map((f) => ({
    成交ID: f.fill_id,
    订单ID: f.order_id,
    日期: day(f.fill_date),
    标的: f.symbol,
    方向: f.side,
    价格: cell(f.price),
    数量: cell(f.quantity),
    成交额: cell(f.price * f.quantity),
    佣金: cell(f.fees?.commission),
    印花税: cell(f.fees?.stamp_duty),
    过户费: cell(f.fees?.transfer_fee),
    费用合计: cell(
      (f.fees?.commission ?? 0) + (f.fees?.stamp_duty ?? 0) + (f.fees?.transfer_fee ?? 0),
    ),
  }));
  return { headers, rows };
}

/** 订单明细（含未成交订单的拒单原因）。 */
export function ordersTable(result: BacktestResult) {
  const headers = [
    "订单ID",
    "标的",
    "方向",
    "信号日",
    "下单日",
    "计划执行日",
    "数量",
    "状态",
    "原因",
    "成交ID",
  ];
  const rows = result.orders.map((o) => ({
    订单ID: o.order_id,
    标的: o.symbol,
    方向: o.side,
    信号日: day(o.signal_date),
    下单日: day(o.order_date),
    计划执行日: day(o.planned_exec_date),
    数量: cell(o.quantity),
    状态: o.status,
    原因: cell(o.reason),
    成交ID: cell(o.fill_id),
  }));
  return { headers, rows };
}

/** 账户逐日状态。 */
export function accountStatesTable(result: BacktestResult) {
  const headers = [
    "日期",
    "现金",
    "持仓数量",
    "当日买入",
    "持仓成本",
    "收盘价",
    "持仓市值",
    "总资产",
    "估值状态",
  ];
  const rows = result.account_states.map((s) => ({
    日期: day(s.date),
    现金: cell(s.cash),
    持仓数量: cell(s.position?.quantity),
    当日买入: cell(s.position?.bought_today),
    持仓成本: cell(s.position?.avg_cost),
    收盘价: cell(s.close),
    持仓市值: cell(s.market_value),
    总资产: cell(s.total_equity),
    估值状态: s.valuation_status,
  }));
  return { headers, rows };
}

// --------------------------------------------------------------------------- //
// 组合
// --------------------------------------------------------------------------- //

/** 组合绩效摘要（含夏普 / 换手 / 费用 / 证据等级）。 */
export function portfolioSummaryTable(result: PortfolioResult) {
  const headers = ["指标", "数值", "说明"];
  const p = result.performance;
  if (!p) return { headers, rows: [] };

  const rows: Array<Record<string, unknown>> = [
    { 指标: "初始权益", 数值: cell(p.initial_value), 说明: "初始资金" },
    { 指标: "期末权益", 数值: cell(p.final_value), 说明: "现金 + 持仓市值" },
    { 指标: "累计盈亏", 数值: cell(p.total_pnl), 说明: "" },
    { 指标: "累计收益率", 数值: cell(p.cumulative_return), 说明: "小数，0.1234 = 12.34%" },
    { 指标: "年化收益率", 数值: cell(p.annualized_return), 说明: "小数" },
    { 指标: "年化波动率", 数值: cell(p.annualized_volatility), 说明: "小数" },
    { 指标: "最大回撤比例", 数值: cell(p.max_drawdown_ratio), 说明: "小数，取正值" },
    { 指标: "最大回撤金额", 数值: cell(p.max_drawdown_amount), 说明: "金额" },
    { 指标: "收益率样本数", 数值: cell(p.n_returns), 说明: "" },
    { 指标: "结果完整", 数值: p.is_complete ? "true" : "false", 说明: "" },
    {
      指标: "夏普比率",
      数值: cell(p.sharpe.value),
      说明:
        p.sharpe.unavailable_reason ??
        `无风险利率 ${p.sharpe.risk_free_rate_annual}，年化周期 ${p.sharpe.periods_per_year}，样本 ${p.sharpe.n_returns}`,
    },
    {
      指标: "年化单边换手率",
      数值: cell(p.turnover.one_way_annualized),
      说明: p.turnover.unavailable_reason ?? p.turnover.definition,
    },
    {
      指标: "成本占毛利比",
      数值: cell(p.cost.cost_to_gross_ratio),
      说明: p.cost.unavailable_reason ?? p.cost.definition,
    },
  ];

  rows.push({
    指标: "证据等级",
    数值: p.evidence.level,
    说明: p.evidence.reasons.join(" / ") || "无附加说明",
  });

  const sel = result.selection;
  rows.push(
    { 指标: "调仓次数", 数值: cell(sel.n_signal_dates), 说明: sel.rebalance },
    { 指标: "空仓期数", 数值: cell(sel.n_empty_periods), 说明: "可排序标的不足 min_assets" },
    { 指标: "平均持仓只数", 数值: cell(sel.avg_holdings), 说明: sel.weighting },
  );

  return { headers, rows };
}

/** 组合逐日状态。 */
export function portfolioStatesTable(states: PortfolioStateRow[]) {
  const headers = [
    "日期",
    "现金",
    "持仓市值",
    "总资产",
    "持仓只数",
    "停牌只数",
    "停牌权重占比",
    "低置信估值",
  ];
  const rows = states.map((s) => ({
    日期: day(s.date),
    现金: cell(s.cash),
    持仓市值: cell(s.market_value),
    总资产: cell(s.total_equity),
    持仓只数: cell(s.n_positions),
    停牌只数: cell(s.n_stale_positions),
    停牌权重占比: cell(s.stale_weight_ratio),
    低置信估值: s.low_confidence_valuation ? "true" : "false",
  }));
  return { headers, rows };
}

/** 组合成交明细。 */
export function portfolioFillsTable(result: PortfolioResult) {
  const headers = ["成交ID", "日期", "标的", "方向", "价格", "数量", "成交额", "费用合计", "备注"];
  const rows = result.fills.map((f) => ({
    成交ID: f.fill_id,
    日期: day(f.date),
    标的: f.symbol,
    方向: f.side,
    价格: cell(f.price),
    数量: cell(f.quantity),
    成交额: cell(f.gross),
    费用合计: cell(f.fees_total),
    备注: cell(f.reason),
  }));
  return { headers, rows };
}

/** 组合被阻挡订单（只展示成交会让人以为按目标权重完整建仓了）。 */
export function portfolioBlockedTable(result: PortfolioResult) {
  const headers = ["日期", "标的", "方向", "原因", "请求数量", "实际成交"];
  const rows = result.blocked.map((b) => ({
    日期: day(b.date),
    标的: b.symbol,
    方向: b.side,
    原因: b.reason,
    请求数量: cell(b.requested_quantity),
    实际成交: cell(b.filled_quantity),
  }));
  return { headers, rows };
}

/** 组合每期目标权重（长表：一行一只标的）。 */
export function portfolioTargetsTable(result: PortfolioResult) {
  const headers = ["调仓日", "标的", "目标权重"];
  const rows = result.targets.flatMap((t) =>
    Object.entries(t.weights).map(([symbol, weight]) => ({
      调仓日: day(t.signal_date),
      标的: symbol,
      目标权重: cell(weight),
    })),
  );
  return { headers, rows };
}

/** 组合数据指纹（逐标的来源与 SHA-256，供核对口径）。 */
export function portfolioDatasetsTable(result: PortfolioResult) {
  const headers = ["标的", "名称", "行数", "起始日", "结束日", "来源文件", "原始CSV指纹"];
  const rows = result.datasets.map((d) => ({
    标的: d.symbol,
    名称: cell(d.display_name),
    行数: cell(d.rows),
    起始日: day(d.start_date),
    结束日: day(d.end_date),
    来源文件: cell(d.source_name),
    原始CSV指纹: cell(d.source_sha256),
  }));
  return { headers, rows };
}

/** 口径声明清单（limitations / 数据质量问题 / 不完整原因）。 */
export function notesTable(result: PortfolioResult) {
  const headers = ["类别", "内容"];
  const rows: Array<Record<string, unknown>> = [];
  const push = (category: string, items: string[]) => {
    for (const item of items) rows.push({ 类别: category, 内容: item });
  };
  push("局限声明", result.limitations);
  push("数据质量问题", result.data_quality_issues);
  push("不完整原因", result.incomplete_reasons);
  push("一致性问题", result.consistency_issues);
  return { headers, rows };
}
