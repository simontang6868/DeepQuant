/**
 * 绩效卡片测试：真实字段展示 / 0 与缺失的区分 / 缺字段不崩溃 /
 * performance 为 null 的降级 / 不可用指标转述。
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PerformanceCards } from "./PerformanceCards";
import type { BacktestResult } from "../api/types";

function makeResult(perf: Partial<BacktestResult["performance"]> | null): BacktestResult {
  const base: BacktestResult = {
    schema_version: "0.1.0",
    engine_version: "0.1.0",
    config: {
      symbol: "TEST01",
      initial_cash: 100000,
      start_date: null,
      end_date: null,
      exec_price_rule: "next_open",
      signal_lag: 1,
      market_rules: { name: "CN_STOCK_A", lot_size: 100, t_plus_one: true, price_limit: null },
      commission_rate: 0.0003,
      min_commission: 5,
      stamp_duty_rate: 0.001,
      transfer_fee_rate: 0.00001,
      slippage_buy: 0.001,
      slippage_sell: 0.001,
      fee_source: "simulation_placeholder",
      assumptions: [],
    },
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
    performance: null,
    reproducibility: {
      inputs_identifiable: true,
      process_reproducible: true,
      missing: [],
      notes: [],
    },
  };
  if (perf !== null) {
    base.performance = {
      initial_equity: 100000,
      final_equity: null,
      total_pnl: null,
      cumulative_return: null,
      daily_returns: [],
      equity_curve: [],
      max_drawdown_amount: null,
      max_drawdown_ratio: null,
      max_drawdown_peak_date: null,
      max_drawdown_trough_date: null,
      drawdown_observation_count: 0,
      annualized_return: null,
      annualized_volatility: null,
      trade_stats: {
        n_orders: 0,
        n_filled_orders: 0,
        n_unfilled_orders: 0,
        n_fills: 0,
        buy_gross_amount: 0,
        sell_gross_amount: 0,
        total_fees: 0,
        n_closed_trades: 0,
        n_winning_trades: 0,
        n_losing_trades: 0,
        n_breakeven_trades: 0,
        win_rate: null,
        open_position_quantity: 0,
      },
      periods_per_year: 252,
      is_complete: true,
      incomplete_reasons: [],
      unavailable_metrics: [],
      ...perf,
    } as BacktestResult["performance"];
  }
  return base;
}

describe("PerformanceCards", () => {
  it("展示真实绩效值：小数收益率 ×100 显示为百分数", () => {
    render(<PerformanceCards result={makeResult({ cumulative_return: 0.1234 })} />);
    expect(screen.getByText("12.34%")).toBeInTheDocument();
  });

  it("0 是有效数值：显示 0.00% 而非“暂无数据”", () => {
    render(<PerformanceCards result={makeResult({ cumulative_return: 0 })} />);
    expect(screen.getByText("0.00%")).toBeInTheDocument();
  });

  it("缺失字段显示“暂无数据”（不虚构）", () => {
    render(<PerformanceCards result={makeResult({ cumulative_return: null })} />);
    expect(screen.getAllByText("暂无数据").length).toBeGreaterThan(0);
  });

  it("胜率来自后端 trade_stats.win_rate", () => {
    render(
      <PerformanceCards
        result={makeResult({
          trade_stats: {
            n_orders: 4,
            n_filled_orders: 3,
            n_unfilled_orders: 1,
            n_fills: 3,
            buy_gross_amount: 50000,
            sell_gross_amount: 52000,
            total_fees: 123.45,
            n_closed_trades: 2,
            n_winning_trades: 1,
            n_losing_trades: 1,
            n_breakeven_trades: 0,
            win_rate: 0.5,
            open_position_quantity: 100,
          },
        })}
      />,
    );
    expect(screen.getByText("50.00%")).toBeInTheDocument();
    expect(screen.getByText("123.45")).toBeInTheDocument();
  });

  it("unavailable_metrics 如实转述", () => {
    render(<PerformanceCards result={makeResult({ unavailable_metrics: ["annualized_return"] })} />);
    expect(screen.getByText(/annualized_return/)).toBeInTheDocument();
  });

  it("performance.is_complete = false 时展示原因", () => {
    render(
      <PerformanceCards
        result={makeResult({ is_complete: false, incomplete_reasons: ["回测区间不足"] })}
      />,
    );
    expect(screen.getByText(/performance.is_complete = false/)).toBeInTheDocument();
    expect(screen.getByText("回测区间不足")).toBeInTheDocument();
  });

  it("performance 为 null 时不崩溃并显示不可用提示", () => {
    render(<PerformanceCards result={makeResult(null)} />);
    expect(screen.getByText(/后端未返回绩效数据/)).toBeInTheDocument();
  });
});
