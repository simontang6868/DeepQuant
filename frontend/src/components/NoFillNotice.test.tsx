/**
 * 「0 成交」提示的行为测试。
 *
 * 这组件存在的唯一理由是：**零成交看起来像零收益，而不是像买不起**。
 * 所以测试要钉住三件事：该出的时候出（有委托、零成交）、不该出的时候不出
 * （有成交 / 没有委托 / performance 缺失）、以及说清原因（资金不足）。
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { NoFillNotice } from "./NoFillNotice";
import type { BacktestResult, OrderInfo } from "../api/types";

function makeOrder(id: string, side: string, reason: string | null): OrderInfo {
  return {
    order_id: id,
    symbol: "600519.SH",
    side,
    signal_date: "2021-03-31T00:00:00",
    order_date: "2021-03-31T00:00:00",
    planned_exec_date: "2021-04-01T00:00:00",
    quantity: 100,
    status: "rejected",
    reason,
    fill_id: null,
  };
}

function makeResult(options: {
  nOrders: number;
  nFilled: number;
  orders?: OrderInfo[];
  initialCash?: number;
}): BacktestResult {
  return {
    schema_version: "0.1.0",
    engine_version: "0.1.0",
    config: {
      symbol: "600519.SH",
      initial_cash: options.initialCash ?? 100000,
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
    orders: options.orders ?? [],
    fills: [],
    account_states: [],
    data_quality_issues: [],
    limitations: [],
    is_complete: true,
    incomplete_reasons: [],
    performance: {
      initial_equity: options.initialCash ?? 100000,
      final_equity: options.initialCash ?? 100000,
      total_pnl: 0,
      cumulative_return: 0,
      daily_returns: [],
      equity_curve: [],
      max_drawdown_amount: 0,
      max_drawdown_ratio: 0,
      max_drawdown_peak_date: null,
      max_drawdown_trough_date: null,
      drawdown_observation_count: 0,
      annualized_return: 0,
      annualized_volatility: null,
      trade_stats: {
        n_orders: options.nOrders,
        n_filled_orders: options.nFilled,
        n_unfilled_orders: options.nOrders - options.nFilled,
        n_fills: options.nFilled,
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
      periods_per_year: 244,
      is_complete: true,
      incomplete_reasons: [],
      unavailable_metrics: [],
    },
    reproducibility: {
      inputs_identifiable: true,
      process_reproducible: true,
      missing: [],
      notes: [],
    },
  };
}

describe("NoFillNotice", () => {
  it("有委托但零成交时给出提示与原因分布", () => {
    const result = makeResult({
      nOrders: 3,
      nFilled: 0,
      orders: [
        makeOrder("O1", "buy", "insufficient_cash"),
        makeOrder("O2", "buy", "insufficient_cash"),
        makeOrder("O3", "sell", "insufficient_position"),
      ],
    });
    render(<NoFillNotice result={result} />);

    const notice = screen.getByTestId("no-fill-notice");
    expect(notice.textContent).toContain("0 笔成交");
    expect(notice.textContent).toContain("资金不足");
    expect(notice.textContent).toContain("2 笔");
    expect(notice.textContent).toContain("无可卖持仓");
  });

  it("明确说清「不是没有信号，而是没成交」", () => {
    const result = makeResult({
      nOrders: 1,
      nFilled: 0,
      orders: [makeOrder("O1", "buy", "insufficient_cash")],
    });
    render(<NoFillNotice result={result} />);
    expect(screen.getByTestId("no-fill-notice").textContent).toContain("不是「策略没有产生信号」");
  });

  it("资金不足时给出可执行建议并回显初始资金", () => {
    const result = makeResult({
      nOrders: 1,
      nFilled: 0,
      orders: [makeOrder("O1", "buy", "insufficient_cash")],
      initialCash: 100000,
    });
    render(<NoFillNotice result={result} />);
    const text = screen.getByTestId("no-fill-notice").textContent ?? "";
    expect(text).toContain("提高初始资金");
    expect(text).toContain("100,000");
  });

  it("有成交时不出现", () => {
    const result = makeResult({ nOrders: 2, nFilled: 1, orders: [makeOrder("O1", "buy", null)] });
    render(<NoFillNotice result={result} />);
    expect(screen.queryByTestId("no-fill-notice")).toBeNull();
  });

  it("没有委托时不出现（那是「策略没信号」，属于另一种情况）", () => {
    render(<NoFillNotice result={makeResult({ nOrders: 0, nFilled: 0 })} />);
    expect(screen.queryByTestId("no-fill-notice")).toBeNull();
  });
});
