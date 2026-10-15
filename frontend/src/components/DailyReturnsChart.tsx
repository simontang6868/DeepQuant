/**
 * 每日收益率柱状图。
 *
 * 数据来源：performance.daily_returns 的 value（后端按相邻交易日账户总资产
 * 环比计算的小数收益率）。图表纵轴把小数转换为百分数显示（×100），
 * 不改变原始数据；涨跌用颜色区分并配文字说明（不只靠颜色）。
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BacktestResult } from "../api/types";
import { toDailyReturnSeries } from "../utils/chartData";

export function DailyReturnsChart({ result }: { result: BacktestResult }) {
  const series = toDailyReturnSeries(result.performance?.daily_returns);

  return (
    <div className="card" data-testid="daily-returns-card">
      <h2>每日收益率</h2>
      {series.length === 0 ? (
        <div className="empty-state">暂无每日收益率数据。</div>
      ) : (
        <>
          <div className="chart-box" data-testid="daily-returns-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={series} margin={{ top: 10, right: 20, bottom: 5, left: 5 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" minTickGap={48} tickFormatter={(v: string) => v.slice(5)} />
                <YAxis width={70} tickFormatter={(v: number) => `${(v * 100).toFixed(1)}%`} />
                <Tooltip
                  formatter={(value) => `${(Number(value) * 100).toFixed(2)}%`}
                  labelFormatter={(label) => `日期：${label}`}
                />
                <Bar dataKey="value" name="日收益率" isAnimationActive={false}>
                  {series.map((p) => (
                    <Cell key={p.date} fill={p.value >= 0 ? "#16a34a" : "#dc2626"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="chart-note">
            定义：value 为后端按相邻交易日账户总资产环比计算的小数日收益率（正收益为绿色、
            负收益为红色），来自 performance.daily_returns，共 {series.length} 个交易日；
            纵轴单位为 %。
          </p>
        </>
      )}
    </div>
  );
}
