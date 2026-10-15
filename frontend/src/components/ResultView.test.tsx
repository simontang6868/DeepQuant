/**
 * 结果视图测试：不完整回测警告 / 空订单成交状态 / 模拟费率声明 /
 * 数据质量与限制 / 可复现性 digest 语义。图表组件 mock 掉（jsdom 无尺寸）。
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ResultView } from "./ResultView";
import type { BacktestResponse, BacktestResult } from "../api/types";

vi.mock("./EquityChart", () => ({ EquityChart: () => <div data-testid="mock-equity-chart" /> }));
vi.mock("./DailyReturnsChart", () => ({
  DailyReturnsChart: () => <div data-testid="mock-daily-returns-chart" />,
}));

function makeResult(patch: Partial<BacktestResult> = {}): BacktestResult {
  return {
    schema_version: "0.1.0",
    engine_version: "0.1.0",
    config: {
      symbol: "TEST01",
      initial_cash: 100000,
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
    dataset: {
      dataset_id: "TEST01",
      n_rows: 60,
      columns: ["open", "high", "low", "close", "volume"],
      date_range: { start: "2020-01-01", end: "2020-02-29" },
      digest: "sha256:abc123",
      digest_algorithm: "sha256",
      canonicalization: {},
    },
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
    ...patch,
  };
}

const response: BacktestResponse = {
  api: { name: "deepquant-api", version: "0.1.0" },
  result: makeResult(),
};

describe("ResultView", () => {
  it("展示回测概要（区间 / 策略 / 执行规则 / 市场规则 / 结果状态）", () => {
    render(<ResultView response={response} />);
    expect(screen.getAllByText(/2020-01-01 ~ 2020-02-29/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/ma_cross v1.0.0/).length).toBeGreaterThan(0);
    expect(screen.getByText(/次日开盘价（next_open）/)).toBeInTheDocument();
    expect(
      screen.getByText(
        (_content, element) =>
          element?.tagName === "TD" &&
          element.textContent === "CN_STOCK_A（每手 100 股，T+1）",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("完整（is_complete = true）")).toBeInTheDocument();
  });

  it("模拟费率声明出现且明确 simulation_placeholder 非真实费率", () => {
    render(<ResultView response={response} />);
    expect(screen.getByText(/费用与滑点声明/)).toBeInTheDocument();
    expect(screen.getByText(/simulation_placeholder/)).toBeInTheDocument();
    expect(screen.getByText(/不代表任何真实券商费率/)).toBeInTheDocument();
  });

  it("orders / fills 为空时显示空状态", () => {
    render(<ResultView response={response} />);
    expect(screen.getByText("本次回测没有产生任何订单。")).toBeInTheDocument();
    expect(screen.getByText("本次回测没有产生任何成交。")).toBeInTheDocument();
  });

  it("is_complete = false 时突出警告并展示原因", () => {
    render(
      <ResultView
        response={{
          api: response.api,
          result: makeResult({
            is_complete: false,
            incomplete_reasons: ["部分交易日缺少收盘价"],
          }),
        }}
      />,
    );
    const banner = screen.getByTestId("incomplete-banner");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent("结果不完整");
    expect(banner).toHaveTextContent("部分交易日缺少收盘价");
  });

  it("data_quality_issues 与 limitations 如实展示", () => {
    render(
      <ResultView
        response={{
          api: response.api,
          result: makeResult({
            data_quality_issues: ["2 个交易日成交量缺失"],
            limitations: ["单标的回测，不支持组合"],
          }),
        }}
      />,
    );
    expect(screen.getByTestId("dq-issues")).toHaveTextContent("2 个交易日成交量缺失");
    expect(screen.getByTestId("limitations")).toHaveTextContent("单标的回测，不支持组合");
  });

  it("可复现性面板区分处理后数据摘要与原始 CSV 指纹语义", () => {
    render(<ResultView response={response} />);
    expect(screen.getAllByText(/sha256:abc123/).length).toBeGreaterThan(0);
    expect(screen.getByText(/它不是导入时原始 CSV 文件的指纹/)).toBeInTheDocument();
  });
});
