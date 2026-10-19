/**
 * 组合回测结果视图测试。
 *
 * 这里守的是「不许只报好消息」：
 * - 证据等级 low、被阻挡订单、数据质量提示、自检失败都必须在页面上出现；
 * - 成交为 0 时不能只显示「完成」，要指向被阻挡订单表；
 * - limitations 原文全部列出（不缩写、不折叠）——用户靠它判断这个数字能信到什么程度。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PortfolioResultView } from "./PortfolioResultView";

// recharts 在 jsdom 里无法测量尺寸（ResponsiveContainer 需要 ResizeObserver），
// 与 DrawdownChart.test.tsx 同一处理：mock 掉图表内核，只验证数据与文案。
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: unknown }) => (
    <div>{children as React.ReactNode}</div>
  ),
  LineChart: ({ children }: { children: unknown }) => <div>{children as React.ReactNode}</div>,
  Line: () => null,
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
}));
import type { PortfolioResult } from "../api/types";

function makeResult(overrides: Partial<PortfolioResult> = {}): PortfolioResult {
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
      note: "2 个数据集覆盖 2017-01-03 ~ 2024-12-31；若标的池是「当前成分股」快照，回看历史存在幸存者偏差",
      symbol_end_dates: { "600519.SH": "2024-12-31", "000858.SZ": "2024-12-31" },
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
      avg_holdings: 1.92,
      min_holdings: 0,
      max_holdings: 2,
      weighting: "equal",
    },
    targets: [{ signal_date: "2019-01-31", weights: { "600519.SH": 1.0 } }],
    states: [
      {
        date: "2019-01-02",
        cash: 1_000_000,
        market_value: 0,
        total_equity: 1_000_000,
        n_positions: 0,
        n_stale_positions: 0,
        stale_weight_ratio: 0,
        low_confidence_valuation: false,
      },
      {
        date: "2019-02-01",
        cash: 100_000,
        market_value: 1_100_000,
        total_equity: 1_200_000,
        n_positions: 1,
        n_stale_positions: 0,
        stale_weight_ratio: 0,
        low_confidence_valuation: false,
      },
    ],
    fills: [
      {
        fill_id: "F1",
        date: "2019-02-01",
        symbol: "600519.SH",
        side: "BUY",
        price: 600,
        quantity: 100,
        gross: 60_000,
        fees_total: 20,
        reason: "rebalance",
      },
    ],
    blocked: [
      {
        date: "2019-03-01",
        symbol: "000858.SZ",
        side: "BUY",
        reason: "limit_up",
        requested_quantity: 300,
        filled_quantity: 0,
      },
      {
        date: "2019-04-01",
        symbol: "000858.SZ",
        side: "SELL",
        reason: "deferred_sell",
        requested_quantity: 200,
        filled_quantity: 0,
      },
    ],
    performance: {
      n_periods: 1456,
      n_returns: 1455,
      initial_value: 1_000_000,
      final_value: 1_275_191.1,
      cumulative_return: 0.275191,
      annualized_return: 0.020989,
      annualized_volatility: 0.297813,
      max_drawdown_ratio: 0.671277,
      max_drawdown_amount: 800_000,
      is_complete: true,
      total_pnl: 275_191.1,
      sharpe: {
        value: 0.2183,
        risk_free_rate_annual: 0,
        periods_per_year: 252,
        n_returns: 1455,
        unavailable_reason: null,
        definition: "mean(r - rf/N) / stdev(r - rf/N, ddof=1) * sqrt(N)",
      },
      turnover: {
        total_traded_amount: 12_000_000,
        average_equity: 1_050_000,
        years: 6.0,
        one_way_annualized: 4.55,
        unavailable_reason: null,
        definition: "（总成交额 / 2）/ 平均权益 / 年数",
      },
      cost: {
        total_fees: 30_000,
        net_profit: 275_191.1,
        gross_profit: 305_191.1,
        cost_to_gross_ratio: 0.0983,
        unavailable_reason: null,
        definition: "总费用 / （净收益 + 总费用）",
      },
      evidence: {
        level: "low",
        n_rebalance_periods: 71,
        n_tradable_assets: 2,
        reasons: ["可交易标的数 2 < 100，截面分散化不足、单只极端值影响过大"],
      },
    },
    data_quality_issues: [],
    limitations: [
      "涨跌停按组合层单一标量 10% 执行：组合层 PriceLimit 无 per-symbol 通道",
      "标的池来自已导入的数据快照，若为「当前成分股」回看历史则存在幸存者偏差",
      "组合按目标**权重**记账（allocation=weights），不受整手约束",
    ],
    is_complete: true,
    incomplete_reasons: [],
    consistency_issues: [],
    ...overrides,
  };
}

describe("PortfolioResultView", () => {
  it("展示区间、标的数与资金", () => {
    render(<PortfolioResultView result={makeResult()} />);
    const head = screen.getByText(/只标的/);
    expect(head).toHaveTextContent("2 只标的");
    expect(head).toHaveTextContent("2019-01-02 ~ 2024-12-31");
  });

  it("展示数据覆盖与偏差说明", () => {
    render(<PortfolioResultView result={makeResult()} />);
    expect(screen.getByTestId("portfolio-coverage")).toHaveTextContent("幸存者偏差");
  });

  it("展示绩效指标（含夏普与换手）", () => {
    render(<PortfolioResultView result={makeResult()} />);
    const card = screen.getByTestId("portfolio-performance");
    expect(card).toHaveTextContent("累计收益");
    expect(card).toHaveTextContent("夏普比率");
    expect(card).toHaveTextContent("年化换手（单边）");
    expect(card).toHaveTextContent("成本占毛收益");
  });

  it("证据等级 low 必须显式警告，并说明样本量", () => {
    render(<PortfolioResultView result={makeResult()} />);
    const banner = screen.getByTestId("portfolio-evidence-low");
    expect(banner).toHaveTextContent("low");
    expect(banner).toHaveTextContent("不足以支撑");
  });

  it("被阻挡订单按原因分类展示，且译成中文", () => {
    render(<PortfolioResultView result={makeResult()} />);
    const card = screen.getByTestId("portfolio-blocked");
    expect(card).toHaveTextContent("2 笔");
    expect(card).toHaveTextContent("涨停放弃买入");
    expect(card).toHaveTextContent("跌停顺延");
    // 说明它不是装饰：目标权重没有完全实现
    expect(card).toHaveTextContent("目标权重没有完全实现");
  });

  it("成交明细展示笔数与字段", () => {
    render(<PortfolioResultView result={makeResult()} />);
    const card = screen.getByTestId("portfolio-fills");
    expect(card).toHaveTextContent("1 笔");
    expect(card).toHaveTextContent("600519.SH");
  });

  it("零成交时给出指向被阻挡订单的提示", () => {
    render(<PortfolioResultView result={makeResult({ fills: [] })} />);
    expect(screen.getByTestId("portfolio-fills")).toHaveTextContent("没有产生任何成交");
    expect(screen.getByTestId("portfolio-fills")).toHaveTextContent("被阻挡订单");
  });

  it("limitations 原文全部列出（不折叠）", () => {
    render(<PortfolioResultView result={makeResult()} />);
    const card = screen.getByTestId("portfolio-limitations");
    expect(card).toHaveTextContent("per-symbol");
    expect(card).toHaveTextContent("整手");
    expect(card).toHaveTextContent("幸存者偏差");
  });

  it("自检失败时给出红色告警（结果不可信）", () => {
    render(
      <PortfolioResultView
        result={makeResult({ consistency_issues: ["2022-03-09 权益 1.00 != 现金+市值 2.00"] })}
      />,
    );
    const box = screen.getByTestId("portfolio-consistency");
    expect(box).toHaveTextContent("结果不可信");
  });

  it("页面不出现自检告警时说明没有一致性问题", () => {
    render(<PortfolioResultView result={makeResult()} />);
    expect(screen.queryByTestId("portfolio-consistency")).toBeNull();
  });

  it("performance 为 null 时如实说明，而不是显示 0", () => {
    render(<PortfolioResultView result={makeResult({ performance: null })} />);
    expect(screen.getByTestId("portfolio-performance")).toHaveTextContent("无法展示指标");
  });

  it("不完整回测列出原因", () => {
    render(
      <PortfolioResultView
        result={makeResult({ is_complete: false, incomplete_reasons: ["2020-02-03 组合权益无法估值"] })}
      />,
    );
    expect(screen.getByText(/本次回测不完整/)).toBeInTheDocument();
    expect(screen.getByText("2020-02-03 组合权益无法估值")).toBeInTheDocument();
  });

  it("展示用到数据集的文件指纹（便于复核）", () => {
    render(<PortfolioResultView result={makeResult()} />);
    const card = screen.getByTestId("portfolio-datasets");
    expect(card).toHaveTextContent("贵州茅台");
    expect(card).toHaveTextContent("aaaaaaaaaaaa");
  });
});

describe("PortfolioResultView 导出", () => {
  // jsdom 没有实现 URL.createObjectURL，下载路径需要 mock 才能断言
  const created: Blob[] = [];

  beforeEach(() => {
    created.length = 0;
    URL.createObjectURL = vi.fn((blob: Blob) => {
      created.push(blob);
      return "blob:mock-url";
    }) as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("提供绩效 / 成交 / 状态 / 指纹 / PNG / JSON 六类导出", () => {
    render(<PortfolioResultView result={makeResult()} />);
    for (const key of ["summary", "states", "fills", "blocked", "equity", "full"]) {
      expect(screen.getByTestId(`export-${key}`)).toBeInTheDocument();
    }
  });

  it("点导出会真的产出一个 Blob 并给出反馈", async () => {
    render(<PortfolioResultView result={makeResult()} />);
    await userEvent.click(screen.getByTestId("export-summary"));
    expect(created.length).toBeGreaterThan(0);
    expect(screen.getByTestId("export-feedback")).toHaveTextContent("已导出");
  });

  it("说明 CSV 与 JSON 的分工，避免用户拿 CSV 当存档", () => {
    render(<PortfolioResultView result={makeResult()} />);
    expect(screen.getByText(/完整口径/)).toBeInTheDocument();
  });
});
