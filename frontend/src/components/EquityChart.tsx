/**
 * 账户资产曲线（Recharts）。
 *
 * 数据来源：performance.equity_curve 的 total_equity（账户总资产 = 现金 + 持仓市值）。
 * 明确标注：这是资产金额曲线，不是标准化净值，也不是收益率曲线。
 * 绘图数据按日期升序排列（utils/chartData.ts 纯函数）。
 */

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BacktestResult } from "../api/types";
import { toEquitySeries } from "../utils/chartData";
import { formatMoney } from "../utils/format";

export function EquityChart({ result }: { result: BacktestResult }) {
  const series = toEquitySeries(result.performance?.equity_curve);

  return (
    <div className="card" data-testid="equity-chart-card">
      <h2>账户资产曲线</h2>
      {series.length === 0 ? (
        <div className="empty-state">暂无曲线数据（回测可能未产生任何账户估值点）。</div>
      ) : (
        <>
          <div className="chart-box" data-testid="equity-chart">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={series} margin={{ top: 10, right: 20, bottom: 5, left: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" minTickGap={48} tickFormatter={(v: string) => v.slice(5)} />
                <YAxis
                  domain={["auto", "auto"]}
                  width={92}
                  tickFormatter={(v: number) => formatMoney(v) ?? ""}
                />
                <Tooltip
                  formatter={(value) => formatMoney(Number(value)) ?? String(value)}
                  labelFormatter={(label) => `日期：${label}`}
                />
                <Line
                  type="monotone"
                  dataKey="totalEquity"
                  name="账户总资产"
                  stroke="#2563eb"
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="chart-note">
            纵轴为账户总资产（现金 + 持仓市值），来自 performance.equity_curve 的 total_equity
            字段，按 {series.length} 个估值点绘制；这是资产金额曲线，不是标准化净值，
            也不是收益率曲线。
          </p>
        </>
      )}
    </div>
  );
}
