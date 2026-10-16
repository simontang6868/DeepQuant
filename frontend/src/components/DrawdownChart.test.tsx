/**
 * DrawdownChart 组件测试。
 *
 * recharts 在 jsdom 中无法测量尺寸，因此 mock 掉图表内核，只验证：
 * - 数据来源（equity_curve）与派生计算（toDrawdownSeries，已单独测）；
 * - 空序列 / 单点 / 多点的空状态与说明文案；
 * - 与后端 max_drawdown_ratio 一致性提示；
 * - performance 为 null 时不崩溃。
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { DrawdownChart } from "./DrawdownChart";
import type { BacktestResult } from "../api/types";

// mock recharts 内核：仅保留容器，不真正绘图
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: unknown }) => <div>{children as React.ReactNode}</div>,
  AreaChart: ({ children }: { children: unknown }) => <div>{children as React.ReactNode}</div>,
  Area: () => null,
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
}));

function makeResult(
  equityCurve: BacktestResult["performance"] extends infer P
    ? P extends { equity_curve: infer C }
      ? C
      : never
    : never,
  maxDrawdownRatio: number | null = null,
): BacktestResult {
  return {
    schema_version: "0.1.0",
    engine_version: "0.1.0",
    config: {} as BacktestResult["config"],
    period: { start: null, end: null },
    strategy: null,
    dataset: null,
    orders: [],
    fills: [],
    account_states: [],
    data_quality_issues: [],
    limitations: [],
    is_complete: true,
    incomplete_reasons: [],
    performance: {
      initial_equity: 100000,
      final_equity: 110000,
      total_pnl: 10000,
      cumulative_return: 0.1,
      daily_returns: [],
      equity_curve: equityCurve,
      max_drawdown_amount: null,
      max_drawdown_ratio: maxDrawdownRatio,
      max_drawdown_peak_date: null,
      max_drawdown_trough_date: null,
      drawdown_observation_count: 0,
      annualized_return: null,
      annualized_volatility: null,
      trade_stats: {} as BacktestResult["performance"] extends { trade_stats: infer T } ? T : never,
      periods_per_year: 252,
      is_complete: true,
      incomplete_reasons: [],
      unavailable_metrics: [],
    },
    reproducibility: {} as BacktestResult["reproducibility"],
  };
}

describe("DrawdownChart", () => {
  it("空权益序列显示明确空状态", () => {
    render(<DrawdownChart result={makeResult([])} />);
    expect(screen.getByText(/暂无回撤曲线数据/)).toBeInTheDocument();
    expect(screen.queryByTestId("drawdown-chart")).toBeNull();
  });

  it("performance 为 null 时显示空状态且不崩溃", () => {
    const result = makeResult([]);
    result.performance = null;
    render(<DrawdownChart result={result} />);
    expect(screen.getByText(/暂无回撤曲线数据/)).toBeInTheDocument();
  });

  it("单个权益点渲染图表容器与说明", () => {
    render(<DrawdownChart result={makeResult([{ date: "2020-01-01", total_equity: 100 }])} />);
    expect(screen.getByTestId("drawdown-chart")).toBeInTheDocument();
    expect(screen.getByText(/共 1 个估值点/)).toBeInTheDocument();
  });

  it("多个权益点渲染并标注点数", () => {
    const curve = [
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: 110 },
      { date: "2020-01-03", total_equity: 99 },
    ];
    render(<DrawdownChart result={makeResult(curve)} />);
    expect(screen.getByTestId("drawdown-chart")).toBeInTheDocument();
    expect(screen.getByText(/共 3 个估值点/)).toBeInTheDocument();
  });

  it("与后端 max_drawdown_ratio 一致时不显示一致性警告", () => {
    const curve = [
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: 110 },
      { date: "2020-01-03", total_equity: 99 }, // 回撤 (110-99)/110=0.1
    ];
    render(<DrawdownChart result={makeResult(curve, 0.1)} />);
    expect(screen.queryByText(/略有出入/)).toBeNull();
  });

  it("与后端 max_drawdown_ratio 不一致时显示一致性提示", () => {
    const curve = [
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: 110 },
      { date: "2020-01-03", total_equity: 99 },
    ];
    render(<DrawdownChart result={makeResult(curve, 0.5)} />);
    expect(screen.getByText(/略有出入/)).toBeInTheDocument();
  });

  it("说明文案明确回撤定义且不混淆当前/最大回撤", () => {
    render(
      <DrawdownChart
        result={makeResult(
          [
            { date: "2020-01-01", total_equity: 100 },
            { date: "2020-01-02", total_equity: 95 },
          ],
          0.05,
        )}
      />,
    );
    expect(screen.getByText(/历史峰值权益/)).toBeInTheDocument();
    expect(screen.getByText(/曲线最低点即「最大回撤」/)).toBeInTheDocument();
  });
});
