/**
 * 回撤曲线图（Step 6.6）。
 *
 * 数据来源：前端从已保存的 performance.equity_curve 推导逐日回撤比例
 *   （drawdownRatio = (历史峰值权益 - 当日权益) / 历史峰值权益，≤ 0）。
 *
 * 重要说明：
 * - 本图不重新执行回测，也不修改后端；只对已保存的权益序列做可视化派生计算。
 * - 纵轴为回撤比例（百分数，负值），0 表示当日创历史新高；
 *   曲线最低点对应「最大回撤」，应与 DrawdownCard 的 max_drawdown_ratio 一致
 *   （组件通过 isDrawdownConsistent 做容差校验，不一致时展示提示但不阻断）。
 * - 不得将「当前回撤」与「最大回撤」混为一谈；最大回撤指标卡片仍在 DrawdownCard 中。
 * - 空序列 / 单点 / 异常数据均有明确提示，不伪造曲线。
 */

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BacktestResult } from "../api/types";
import { isDrawdownConsistent, toDrawdownSeries } from "../utils/chartData";
import { formatPercent } from "../utils/format";

export function DrawdownChart({ result }: { result: BacktestResult }) {
  const series = toDrawdownSeries(result.performance?.equity_curve);
  const backendRatio = result.performance?.max_drawdown_ratio;
  const consistent = isDrawdownConsistent(series, backendRatio);

  return (
    <div className="card" data-testid="drawdown-chart-card">
      <h2>回撤曲线</h2>
      {series.length === 0 ? (
        <div className="empty-state">
          暂无回撤曲线数据：回测未产生可用的权益序列（performance.equity_curve 为空或全部为 null）。
        </div>
      ) : (
        <>
          <div className="chart-box" data-testid="drawdown-chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series} margin={{ top: 10, right: 20, bottom: 5, left: 5 }}>
                <defs>
                  <linearGradient id="drawdownGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#dc2626" stopOpacity={0.8} />
                    <stop offset="100%" stopColor="#dc2626" stopOpacity={0.1} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" minTickGap={48} tickFormatter={(v: string) => v.slice(5)} />
                <YAxis
                  width={70}
                  tickFormatter={(v: number) => `${(v * 100).toFixed(1)}%`}
                />
                <Tooltip
                  formatter={(value) => `${(Number(value) * 100).toFixed(2)}%`}
                  labelFormatter={(label) => `日期：${label}`}
                />
                <Area
                  type="monotone"
                  dataKey="drawdownRatio"
                  name="当日回撤比例"
                  stroke="#dc2626"
                  fill="url(#drawdownGradient)"
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <p className="chart-note">
            定义：当日回撤比例 = （截至当日的历史峰值权益 − 当日权益）/ 历史峰值权益，
            由已保存的 performance.equity_curve 在前端派生计算（共 {series.length} 个估值点）；
            纵轴单位为 %，0 表示当日创历史新高，曲线最低点即「最大回撤」。
            {!consistent && (
              <span className="text-warn">
                {" "}注意：前端推导的最大回撤与后端
                {formatPercent(backendRatio) ?? "暂无数据"}
                略有出入，可能因权益序列存在部分缺失点导致，以下方 DrawdownCard 指标卡为准确值。
              </span>
            )}
          </p>
        </>
      )}
    </div>
  );
}
