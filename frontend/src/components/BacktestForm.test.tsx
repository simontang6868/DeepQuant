/**
 * 回测配置表单测试：非法配置阻止并显示错误摘要 / 提交前摘要 / 运行按钮状态 /
 * 越界日期提示。
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BacktestForm } from "./BacktestForm";
import type { DatasetEntry, StrategyInfo } from "../api/types";

const dataset: DatasetEntry = {
  symbol: "TEST01",
  rows: 60,
  start_date: "2020-01-01",
  end_date: "2020-02-29",
  source_name: "TEST01.csv",
  source_sha256: null,
  ingested_at: null,
};

const strategy: StrategyInfo = {
  strategy_id: "ma_cross",
  display_name: "双均线交叉",
  version: "1.0.0",
  description: "d",
  params: {
    fast_window: { type: "integer", default: 5, description: "fast", minimum: 1 },
    slow_window: { type: "integer", default: 20, description: "slow", minimum: 2 },
  },
};

const baseValues = {
  datasetSymbol: "TEST01",
  strategyId: "ma_cross",
  paramValues: {},
  startDate: "2020-01-01",
  endDate: "2020-02-29",
  initialCash: "100000",
};

function renderForm(overrides: Record<string, unknown> = {}) {
  const props = {
    dataset,
    strategy,
    values: baseValues,
    fieldErrors: {},
    running: false,
    onChange: vi.fn(),
    onRun: vi.fn(),
    ...overrides,
  };
  render(<BacktestForm {...props} />);
  return props;
}

describe("BacktestForm", () => {
  it("展示提交前配置摘要（数据集 / 策略 / 参数默认 / 区间 / 资金 / 模拟费率声明）", () => {
    renderForm();
    expect(screen.getByText("提交前配置摘要")).toBeInTheDocument();
    expect(screen.getByText("TEST01")).toBeInTheDocument();
    expect(screen.getByText(/双均线交叉（ma_cross）/)).toBeInTheDocument();
    expect(screen.getByText(/fast_window=5（默认），slow_window=20（默认）/)).toBeInTheDocument();
    expect(
      screen.getByText(/使用后端默认执行规则与模拟占位费率\/滑点（fee_source =/),
    ).toBeInTheDocument();
  });

  it("初始资金 ≤ 0 的错误进入错误摘要", () => {
    renderForm({
      fieldErrors: { initial_cash: "初始资金必须大于 0" },
    });
    expect(screen.getByTestId("form-errors")).toHaveTextContent("初始资金必须大于 0");
  });

  it("开始日期晚于结束日期的错误进入错误摘要", () => {
    renderForm({ fieldErrors: { end_date: "开始日期不能晚于结束日期" } });
    expect(screen.getByTestId("form-errors")).toHaveTextContent("开始日期不能晚于结束日期");
  });

  it("日期超出数据集区间显示黄色提示但不阻断", () => {
    renderForm({ values: { ...baseValues, endDate: "2021-12-31" } });
    expect(screen.getAllByText(/在数据集覆盖区间/).length).toBeGreaterThan(0);
    expect(screen.getByTestId("run-backtest")).toBeEnabled();
  });

  it("点击运行触发 onRun", async () => {
    const props = renderForm();
    await userEvent.click(screen.getByTestId("run-backtest"));
    expect(props.onRun).toHaveBeenCalledTimes(1);
  });

  it("执行中按钮禁用并显示同步等待提示（不伪造异步）", () => {
    renderForm({ running: true });
    const btn = screen.getByTestId("run-backtest");
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("回测执行中…（同步请求，请勿重复提交）");
    expect(screen.getByTestId("running-hint")).toBeInTheDocument();
  });

  it("修改初始资金触发 onChange", async () => {
    const props = renderForm();
    const input = screen.getByTestId("initial-cash");
    await userEvent.clear(input);
    await userEvent.type(input, "2");
    expect(props.onChange).toHaveBeenCalled();
  });
});
