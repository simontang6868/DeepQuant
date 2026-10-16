/**
 * 最大回撤展示。
 *
 * 当前后端 API 只返回最大回撤指标（金额 / 比例 / 峰谷日期 / 观测数），
 * 未提供逐日回撤时间序列 → 不伪造回撤曲线，只展示指标并明确说明。
 */

import type { BacktestResult } from "../api/types";
import { MetricCard } from "./MetricCard";
import { formatDate, formatInteger, formatMoney, formatPercent } from "../utils/format";

export function DrawdownCard({ result }: { result: BacktestResult }) {
  const perf = result.performance;

  return (
    <div className="card" data-testid="drawdown-card">
      <h2>最大回撤</h2>
      <div className="metric-grid">
        <MetricCard
          label="最大回撤金额"
          value={perf ? formatMoney(perf.max_drawdown_amount) : null}
          explanation="账户资产从峰值到谷值的最大金额损失"
        />
        <MetricCard
          label="最大回撤比例"
          value={perf ? formatPercent(perf.max_drawdown_ratio) : null}
          explanation="最大回撤金额相对峰值的比例（后端计算值，小数 × 100 显示）"
        />
        <MetricCard
          label="回撤峰值日期"
          value={perf ? formatDate(perf.max_drawdown_peak_date) : null}
          explanation="最大回撤起点的资产峰值日期"
        />
        <MetricCard
          label="回撤谷值日期"
          value={perf ? formatDate(perf.max_drawdown_trough_date) : null}
          explanation="最大回撤终点的资产谷值日期"
        />
        <MetricCard
          label="回撤观测交易日数"
          value={perf ? formatInteger(perf.drawdown_observation_count) : null}
          explanation="参与回撤统计的交易日数量"
        />
      </div>
      <p className="chart-note">
        说明：上述为后端直接返回的最大回撤汇总指标（金额 / 比例 / 峰谷日期）。下方「回撤曲线」
        由已保存的 performance.equity_curve 在前端派生绘制，曲线最低点对应本卡片的最大回撤比例。
        二者计算口径均为「（历史峰值权益 − 当日权益）/ 历史峰值权益」。
      </p>
    </div>
  );
}
