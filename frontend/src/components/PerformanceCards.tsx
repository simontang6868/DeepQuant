/**
 * 绩效指标概览：只展示 performance 中实际存在且有明确语义的字段。
 * - performance 为 null（后端允许）→ 显示“绩效数据不可用”，不虚构；
 * - 缺失字段显示“暂无数据”；0 照常展示；
 * - 小数比率 ×100 显示为百分数；
 * - unavailable_metrics 来自后端标记，如实转述。
 */

import type { BacktestResult } from "../api/types";
import { MetricCard } from "./MetricCard";
import { formatInteger, formatMoney, formatPercent } from "../utils/format";

export function PerformanceCards({ result }: { result: BacktestResult }) {
  const perf = result.performance;

  return (
    <div className="card" data-testid="performance-cards">
      <h2>绩效指标</h2>

      {perf === null ? (
        <div className="banner banner-warn" role="alert">
          后端未返回绩效数据（performance = null），无法展示指标。
        </div>
      ) : (
        <>
          {!perf.is_complete && perf.incomplete_reasons.length > 0 && (
            <div className="banner banner-warn" role="alert">
              <strong>绩效计算不完整（performance.is_complete = false）：</strong>
              <ul>
                {perf.incomplete_reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}

          {perf.unavailable_metrics.length > 0 && (
            <div className="banner banner-neutral">
              后端标记为不可计算的指标（unavailable_metrics）：
              {perf.unavailable_metrics.join("、")}
            </div>
          )}

          <h3>收益概览</h3>
          <div className="metric-grid">
            <MetricCard
              label="初始资金"
              value={formatMoney(perf.initial_equity)}
              explanation="回测期初的账户资产"
            />
            <MetricCard
              label="期末资产"
              value={formatMoney(perf.final_equity)}
              explanation="回测期末的账户资产"
            />
            <MetricCard
              label="累计盈亏"
              value={formatMoney(perf.total_pnl)}
              explanation="期末资产 − 初始资金（含费用影响）"
            />
            <MetricCard
              label="累计收益率"
              value={formatPercent(perf.cumulative_return)}
              explanation="整个回测区间的总收益率（小数 × 100 显示为百分数）"
            />
            <MetricCard
              label="年化收益率"
              value={formatPercent(perf.annualized_return)}
              explanation="按后端计算口径年化后的收益率"
            />
            <MetricCard
              label="年化波动率"
              value={formatPercent(perf.annualized_volatility)}
              explanation="日收益率年化后的标准差，衡量收益波动大小"
            />
          </div>

          <h3>交易统计（后端 trade_stats）</h3>
          <div className="metric-grid">
            <MetricCard
              label="订单总数"
              value={formatInteger(perf.trade_stats.n_orders)}
              explanation="策略产生的订单数量"
            />
            <MetricCard
              label="已成交订单"
              value={formatInteger(perf.trade_stats.n_filled_orders)}
              explanation="至少有一笔成交的订单数量"
            />
            <MetricCard
              label="未成交订单"
              value={formatInteger(perf.trade_stats.n_unfilled_orders)}
              explanation="被拒绝或失效的订单数量"
            />
            <MetricCard
              label="成交笔数"
              value={formatInteger(perf.trade_stats.n_fills)}
              explanation="实际成交（fill）笔数，不等于订单数"
            />
            <MetricCard
              label="已平仓交易"
              value={formatInteger(perf.trade_stats.n_closed_trades)}
              explanation="卖出后了结的完整交易回合数"
            />
            <MetricCard
              label="盈利交易"
              value={formatInteger(perf.trade_stats.n_winning_trades)}
              explanation="已平仓交易中盈利的数量"
            />
            <MetricCard
              label="亏损交易"
              value={formatInteger(perf.trade_stats.n_losing_trades)}
              explanation="已平仓交易中亏损的数量"
            />
            <MetricCard
              label="打平交易"
              value={formatInteger(perf.trade_stats.n_breakeven_trades)}
              explanation="已平仓交易中盈亏持平的数量"
            />
            <MetricCard
              label="胜率"
              value={formatPercent(perf.trade_stats.win_rate)}
              explanation="盈利交易占已平仓交易的比例（后端统计值）"
            />
            <MetricCard
              label="买入成交额"
              value={formatMoney(perf.trade_stats.buy_gross_amount)}
              explanation="所有买入成交的金额合计（不含费用口径以后端为准）"
            />
            <MetricCard
              label="卖出成交额"
              value={formatMoney(perf.trade_stats.sell_gross_amount)}
              explanation="所有卖出成交的金额合计"
            />
            <MetricCard
              label="总费用"
              value={formatMoney(perf.trade_stats.total_fees)}
              explanation="佣金 + 印花税 + 过户费合计（模拟费率计算）"
            />
            <MetricCard
              label="期末未平仓数量"
              value={formatInteger(perf.trade_stats.open_position_quantity)}
              explanation="回测结束时仍持有的股数"
            />
          </div>
          <p className="hint">
            以上指标均直接读取后端返回值，前端不做二次计算；胜率等缺失时显示“暂无数据”。
            费用基于模拟占位参数计算，不代表真实券商费率。
          </p>
        </>
      )}
    </div>
  );
}
