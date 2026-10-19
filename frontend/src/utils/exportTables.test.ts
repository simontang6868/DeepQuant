/**
 * 导出表格构造测试。
 *
 * 与界面展示的区别必须守住：
 * - 百分比字段导出**原始小数**（0.1234），不是 "12.34%"；
 * - 缺失值导出**空**，不是 "暂无"、不是 0；
 * - 嵌套结构（fees / position）展平成列，否则导出来是 `[object Object]`。
 */

import { describe, expect, it } from "vitest";

import type {
  AccountStateInfo,
  BacktestResult,
  FillInfo,
  OrderInfo,
  PerformanceInfo,
  PortfolioResult,
  PortfolioStateRow,
} from "../api/types";
import {
  accountStatesTable,
  fillsTable,
  notesTable,
  ordersTable,
  portfolioBlockedTable,
  portfolioDatasetsTable,
  portfolioFillsTable,
  portfolioStatesTable,
  portfolioSummaryTable,
  portfolioTargetsTable,
  summaryTable,
} from "./exportTables";

function makePerformance(patch: Partial<PerformanceInfo> = {}): PerformanceInfo {
  return {
    initial_equity: 100_000,
    final_equity: 81_472.8,
    total_pnl: -18_527.2,
    cumulative_return: -0.1853,
    daily_returns: [],
    equity_curve: [],
    max_drawdown_amount: 30_000,
    max_drawdown_ratio: 0.3,
    max_drawdown_peak_date: "2021-02-10T00:00:00",
    max_drawdown_trough_date: "2022-03-09T00:00:00",
    drawdown_observation_count: 100,
    annualized_return: -0.04,
    annualized_volatility: 0.25,
    trade_stats: {
      n_orders: 10,
      n_filled_orders: 6,
      n_unfilled_orders: 4,
      n_fills: 6,
      buy_gross_amount: 50_000,
      sell_gross_amount: 40_000,
      total_fees: 120.5,
      n_closed_trades: 3,
      n_winning_trades: 1,
      n_losing_trades: 2,
      n_breakeven_trades: 0,
      win_rate: 0.3333,
      open_position_quantity: 0,
    },
    periods_per_year: 252,
    is_complete: true,
    incomplete_reasons: [],
    unavailable_metrics: [],
    ...patch,
  };
}

function makeResult(patch: Partial<BacktestResult> = {}): BacktestResult {
  const fill: FillInfo = {
    fill_id: "F1",
    order_id: "O1",
    symbol: "TEST01",
    side: "BUY",
    fill_date: "2020-01-03T00:00:00",
    price: 10.5,
    quantity: 100,
    fees: { commission: 5, stamp_duty: 0, transfer_fee: 0.01 },
  };
  const order: OrderInfo = {
    order_id: "O1",
    symbol: "TEST01",
    side: "BUY",
    signal_date: "2020-01-02T00:00:00",
    order_date: "2020-01-02T00:00:00",
    planned_exec_date: "2020-01-03T00:00:00",
    quantity: 100,
    status: "FILLED",
    reason: null,
    fill_id: "F1",
  };
  const state: AccountStateInfo = {
    date: "2020-01-03T00:00:00",
    cash: 98_948.49,
    position: { symbol: "TEST01", quantity: 100, bought_today: 100, avg_cost: 10.51 },
    close: 10.6,
    market_value: 1060,
    total_equity: 100_008.49,
    valuation_status: "OK",
  };
  return {
    schema_version: "0.1.0",
    engine_version: "0.1.0",
    config: {
      symbol: "TEST01",
      initial_cash: 100_000,
      start_date: "2020-01-01T00:00:00",
      end_date: "2020-02-29T00:00:00",
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
    period: { start: "2020-01-01T00:00:00", end: "2020-02-29T00:00:00" },
    strategy: {
      name: "ma_cross",
      version: "1.0.0",
      params: { fast_window: 5, slow_window: 20 },
      params_available: true,
      identified: true,
      reproducible: true,
      notes: [],
    },
    dataset: null,
    orders: [order],
    fills: [fill],
    account_states: [state],
    data_quality_issues: [],
    limitations: [],
    is_complete: true,
    incomplete_reasons: [],
    performance: makePerformance(),
    reproducibility: {
      inputs_identifiable: true,
      process_reproducible: true,
      missing: [],
      notes: [],
    },
    ...patch,
  };
}

describe("summaryTable", () => {
  it("绩效缺失时返回空行，而不是造一行 0", () => {
    const table = summaryTable(makeResult({ performance: null }));
    expect(table.rows).toEqual([]);
    expect(table.headers.length).toBeGreaterThan(0);
  });

  it("收益率为原始小数（不做百分比换算）", () => {
    const table = summaryTable(makeResult());
    const row = table.rows.find((r) => r["指标"] === "累计收益率");
    expect(row?.["数值"]).toBe(-0.1853);
  });

  it("不可计算指标与不完整原因一并带上", () => {
    const table = summaryTable(
      makeResult({
        performance: makePerformance({
          unavailable_metrics: ["sharpe_ratio"],
          incomplete_reasons: ["收益率样本不足"],
        }),
      }),
    );
    const text = table.rows.map((r) => String(r["数值"])).join(" ");
    expect(text).toContain("sharpe_ratio");
    expect(text).toContain("收益率样本不足");
  });
});

describe("fillsTable", () => {
  it("fees 展平成三列，成交额 = 价 × 量", () => {
    const table = fillsTable(makeResult());
    expect(table.headers).toContain("佣金");
    expect(table.rows[0]["成交额"]).toBeCloseTo(1050, 6);
    expect(table.rows[0]["费用合计"]).toBeCloseTo(5.01, 6);
  });

  it("日期只保留 YYYY-MM-DD", () => {
    const table = fillsTable(makeResult());
    expect(table.rows[0]["日期"]).toBe("2020-01-03");
  });
});

describe("ordersTable", () => {
  it("未成交订单的拒单原因保留，缺失时留空", () => {
    const table = ordersTable(
      makeResult({
        orders: [
          {
            order_id: "O2",
            symbol: "TEST01",
            side: "SELL",
            signal_date: "2020-02-01T00:00:00",
            order_date: "2020-02-01T00:00:00",
            planned_exec_date: "2020-02-02T00:00:00",
            quantity: 100,
            status: "REJECTED",
            reason: "insufficient_cash",
            fill_id: null,
          },
        ],
      }),
    );
    expect(table.rows[0]["原因"]).toBe("insufficient_cash");
    expect(table.rows[0]["成交ID"]).toBe("");
  });
});

describe("accountStatesTable", () => {
  it("嵌套 position 展平，null 估值留空", () => {
    const table = accountStatesTable(
      makeResult({
        account_states: [
          {
            date: "2020-02-03T00:00:00",
            cash: 100_000,
            position: { symbol: "TEST01", quantity: 0, bought_today: 0, avg_cost: null },
            close: null,
            market_value: null,
            total_equity: null,
            valuation_status: "STALE",
          },
        ],
      }),
    );
    expect(table.rows[0]["持仓成本"]).toBe("");
    expect(table.rows[0]["总资产"]).toBe("");
    expect(table.rows[0]["估值状态"]).toBe("STALE");
  });
});

// --------------------------------------------------------------------------- //
// 组合
// --------------------------------------------------------------------------- //

function makePortfolioResult(patch: Partial<PortfolioResult> = {}): PortfolioResult {
  return {
    schema_version: "portfolio-v1",
    engine: {
      module: "deepquant.backtest.portfolio.run_portfolio_backtest",
      price_limit_rate: 0.1,
      elapsed_seconds: 4.87,
    },
    config: {
      symbols: ["600519.SH", "000858.SZ"],
      n_symbols: 2,
      initial_cash: 1_000_000,
      allocation: "weights",
      lot_size: 100,
      max_weight_per_asset: null,
      selection: "momentum_top",
      top_fraction: 0.2,
      lookback: 60,
      skip: 5,
      min_assets: 2,
      fee_source: "simulation_placeholder",
      commission_rate: 0.00025,
      min_commission: 5,
      stamp_duty_rate: 0.0005,
      transfer_fee_rate: 0.00001,
      slippage_buy: 0.0005,
      slippage_sell: 0.0005,
    },
    period: { start_date: "2019-01-02", end_date: "2024-12-31", n_trading_days: 1456 },
    coverage: {
      note: "覆盖声明",
      symbol_end_dates: { "600519.SH": "2024-12-31" },
    },
    datasets: [
      {
        symbol: "600519.SH",
        display_name: "贵州茅台",
        rows: 1943,
        start_date: "2017-01-03",
        end_date: "2024-12-31",
        source_name: "600519.SH.qfq.csv",
        source_sha256: "a".repeat(64),
      },
    ],
    selection: {
      selection: "momentum_top",
      top_fraction: 0.2,
      min_assets: 2,
      lookback: 60,
      skip: 5,
      rebalance: "month_end",
      n_signal_dates: 72,
      n_empty_periods: 3,
      n_non_empty_periods: 69,
      avg_holdings: 5.2,
      min_holdings: 4,
      max_holdings: 6,
      weighting: "equal_weight",
    },
    targets: [
      { signal_date: "2019-01-31T00:00:00", weights: { "600519.SH": 0.5, "000858.SZ": 0.5 } },
    ],
    states: [
      {
        date: "2019-01-02T00:00:00",
        cash: 1_000_000,
        market_value: 0,
        total_equity: 1_000_000,
        n_positions: 0,
        n_stale_positions: 0,
        stale_weight_ratio: 0,
        low_confidence_valuation: false,
      },
    ],
    fills: [
      {
        fill_id: "PF1",
        date: "2019-02-01T00:00:00",
        symbol: "600519.SH",
        side: "BUY",
        price: 700.5,
        quantity: 100,
        gross: 70_050,
        fees_total: 25.3,
        reason: "rebalance",
      },
    ],
    blocked: [
      {
        date: "2019-03-01T00:00:00",
        symbol: "000858.SZ",
        side: "BUY",
        reason: "limit_up",
        requested_quantity: 100,
        filled_quantity: 0,
      },
    ],
    performance: {
      n_periods: 1456,
      n_returns: 1455,
      initial_value: 1_000_000,
      final_value: 2_687_600,
      cumulative_return: 1.6876,
      annualized_return: 0.1866,
      annualized_volatility: 0.2231,
      max_drawdown_ratio: 0.4572,
      max_drawdown_amount: 800_000,
      is_complete: true,
      total_pnl: 1_687_600,
      sharpe: {
        value: 0.812,
        risk_free_rate_annual: 0.02,
        periods_per_year: 252,
        n_returns: 1455,
        unavailable_reason: null,
        definition: "年化",
      },
      turnover: {
        total_traded_amount: 1_000_000,
        average_equity: 1_800_000,
        years: 6,
        one_way_annualized: 0.92,
        unavailable_reason: null,
        definition: "单边",
      },
      cost: {
        total_fees: 12_000,
        net_profit: 1_675_600,
        gross_profit: 1_687_600,
        cost_to_gross_ratio: 0.0071,
        unavailable_reason: null,
        definition: "费用/毛利",
      },
      evidence: {
        level: "low",
        n_rebalance_periods: 72,
        n_tradable_assets: 51,
        reasons: ["可交易标的 51 只 < 100 只"],
      },
    },
    data_quality_issues: [],
    limitations: [],
    is_complete: true,
    incomplete_reasons: [],
    consistency_issues: [],
    ...patch,
  };
}

describe("portfolioSummaryTable", () => {
  it("带上夏普 / 换手 / 成本 / 证据等级", () => {
    const table = portfolioSummaryTable(makePortfolioResult());
    const labels = table.rows.map((r) => String(r["指标"]));
    expect(labels).toContain("夏普比率");
    expect(labels).toContain("年化单边换手率");
    expect(labels).toContain("成本占毛利比");
    expect(labels).toContain("证据等级");
  });

  it("夏普不可用时给出原因而不是留空", () => {
    const result = makePortfolioResult();
    const perf = result.performance as NonNullable<PortfolioResult["performance"]>;
    perf.sharpe = { ...perf.sharpe, value: null, unavailable_reason: "收益率样本不足 30 个" };
    const table = portfolioSummaryTable(result);
    const row = table.rows.find((r) => r["指标"] === "夏普比率");
    expect(row?.["数值"]).toBe("");
    expect(String(row?.["说明"])).toContain("收益率样本不足 30 个");
  });

  it("performance 为 null 时返回空行", () => {
    expect(portfolioSummaryTable(makePortfolioResult({ performance: null })).rows).toEqual([]);
  });
});

describe("portfolioTargetsTable", () => {
  it("展开成长表：一行一只标的", () => {
    const table = portfolioTargetsTable(makePortfolioResult());
    expect(table.rows).toHaveLength(2);
    expect(table.rows[0]["调仓日"]).toBe("2019-01-31");
    expect(table.rows[0]["目标权重"]).toBe(0.5);
  });
});

describe("portfolioStatesTable", () => {
  it("布尔值转成 true/false 文本，避免导出后语义丢失", () => {
    const rows: PortfolioStateRow[] = [
      {
        date: "2019-01-02T00:00:00",
        cash: 1,
        market_value: 2,
        total_equity: 3,
        n_positions: 1,
        n_stale_positions: 0,
        stale_weight_ratio: 0,
        low_confidence_valuation: false,
      },
    ];
    expect(portfolioStatesTable(rows).rows[0]["低置信估值"]).toBe("false");
  });
});

describe("portfolioFillsTable / portfolioBlockedTable", () => {
  it("成交明细保留后端给的成交额与费用", () => {
    const table = portfolioFillsTable(makePortfolioResult());
    expect(table.rows[0]["成交额"]).toBe(70_050);
    expect(table.rows[0]["费用合计"]).toBe(25.3);
  });

  it("被阻挡订单的请求量与实际成交量都在", () => {
    const table = portfolioBlockedTable(makePortfolioResult());
    expect(table.rows[0]["请求数量"]).toBe(100);
    expect(table.rows[0]["实际成交"]).toBe(0);
    expect(table.rows[0]["原因"]).toBe("limit_up");
  });
});

describe("portfolioDatasetsTable", () => {
  it("保留完整指纹（不截断，导出就是用来核对的）", () => {
    const table = portfolioDatasetsTable(makePortfolioResult());
    expect(table.rows[0]["原始CSV指纹"]).toBe("a".repeat(64));
  });
});

describe("notesTable", () => {
  it("把局限性 / 数据质量 / 不完整 / 一致性问题分类列出", () => {
    const table = notesTable(
      makePortfolioResult({
        limitations: ["费用为占位参数"],
        data_quality_issues: ["某标的缺失 2 日"],
        incomplete_reasons: ["末尾无法估值"],
        consistency_issues: ["权益不等"],
      }),
    );
    const categories = table.rows.map((r) => String(r["类别"]));
    expect(categories).toContain("局限声明");
    expect(categories).toContain("数据质量问题");
    expect(categories).toContain("不完整原因");
    expect(categories).toContain("一致性问题");
  });

  it("全空时返回空行，不造内容", () => {
    expect(notesTable(makePortfolioResult()).rows).toEqual([]);
  });
});
