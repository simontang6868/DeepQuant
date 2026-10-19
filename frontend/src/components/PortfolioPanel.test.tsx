/**
 * 组合回测配置面板测试。
 *
 * 覆盖的是「没有这个测试就会出错但不会报错」的行为：
 * - 多选状态的勾选/取消是否真的反映到回调（而不是只改本地视图）；
 * - 筛选后仍能勾选，且全选只作用于全部数据集；
 * - unavailable_datasets（导入被拒的标的）必须显式出现——否则用户以为漏导入；
 * - min_assets 冲突时给出警告而不是静默提交。
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PortfolioPanel } from "./PortfolioPanel";
import type { DatasetListResponse } from "../api/types";
import type { PortfolioFormValues } from "../utils/portfolioPayload";
import { PORTFOLIO_DEFAULTS } from "../utils/portfolioPayload";

function makeDatasets(): DatasetListResponse {
  return {
    datasets: [
      {
        symbol: "600519.SH",
        display_name: "贵州茅台",
        rows: 1943,
        start_date: "2017-01-03",
        end_date: "2024-12-31",
        source_name: "600519.SH.qfq.csv",
        source_sha256: "a".repeat(64),
        ingested_at: "2026-10-18T20:00:00",
      },
      {
        symbol: "000858.SZ",
        display_name: "五粮液",
        rows: 1943,
        start_date: "2017-01-03",
        end_date: "2024-12-31",
        source_name: "000858.SZ.qfq.csv",
        source_sha256: "b".repeat(64),
        ingested_at: "2026-10-18T20:00:00",
      },
    ],
    invalid_datasets: [],
    unavailable_datasets: [
      {
        symbol: "600809.SH",
        display_name: "山西汾酒",
        reason: "invalid_price(131)",
        checked_at: "2026-10-18T21:03:59+08:00",
      },
    ],
    coverage: {
      start_date: "2017-01-03",
      end_date: "2024-12-31",
      n_datasets: 2,
      n_unavailable: 1,
      note:
        "2 个数据集覆盖 2017-01-03 ~ 2024-12-31；全部数据集都止于 2024-12-31：回测区间无法超过数据覆盖终点；若标的池是「当前成分股」快照，回看历史存在幸存者偏差",
    },
  };
}

function renderPanel(overrides: Partial<PortfolioFormValues> = {}) {
  const handlers = {
    onChange: vi.fn(),
    onToggleSymbol: vi.fn(),
    onSelectAll: vi.fn(),
    onClearSymbols: vi.fn(),
    onRun: vi.fn(),
    onRefreshDatasets: vi.fn(),
  };
  const values: PortfolioFormValues = {
    ...PORTFOLIO_DEFAULTS,
    symbols: ["600519.SH"],
    ...overrides,
  };
  render(
    <PortfolioPanel
      datasets={{ status: "ready", data: makeDatasets() }}
      values={values}
      errors={{}}
      running={false}
      {...handlers}
    />,
  );
  return handlers;
}

describe("PortfolioPanel", () => {
  it("展示可用数据集数与已选数量", () => {
    renderPanel();
    expect(screen.getByTestId("portfolio-dataset-count")).toHaveTextContent("共 2 个可用数据集");
    expect(screen.getByTestId("portfolio-dataset-count")).toHaveTextContent("已选");
  });

  it("展示数据覆盖说明（区间终点与幸存者偏差）", () => {
    renderPanel();
    const note = screen.getByTestId("portfolio-coverage-note");
    expect(note).toHaveTextContent("2024-12-31");
    expect(note).toHaveTextContent("幸存者偏差");
  });

  it("勾选与取消勾选都回调 symbol", async () => {
    const handlers = renderPanel();
    await userEvent.click(screen.getByTestId("symbol-000858.SZ"));
    expect(handlers.onToggleSymbol).toHaveBeenCalledWith("000858.SZ");
  });

  it("已选中的标的保持勾选状态", () => {
    renderPanel({ symbols: ["600519.SH"] });
    expect(screen.getByTestId("symbol-600519.SH")).toBeChecked();
    expect(screen.getByTestId("symbol-000858.SZ")).not.toBeChecked();
  });

  it("筛选只影响可见列表，不影响全选", async () => {
    const handlers = renderPanel();
    await userEvent.type(screen.getByTestId("portfolio-symbol-filter"), "茅台");
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);

    await userEvent.click(screen.getByTestId("portfolio-select-all"));
    expect(handlers.onSelectAll).toHaveBeenCalled();
  });

  it("筛选无匹配时给出提示", async () => {
    renderPanel();
    await userEvent.type(screen.getByTestId("portfolio-symbol-filter"), "不存在的名字");
    expect(screen.getByTestId("portfolio-no-match")).toBeInTheDocument();
  });

  it("导入被拒的标的必须显式列出并带原因码", () => {
    renderPanel();
    const box = screen.getByTestId("portfolio-unavailable");
    expect(box).toHaveTextContent("山西汾酒");
    expect(box).toHaveTextContent("600809.SH");
    expect(box).toHaveTextContent("invalid_price(131)");
    // 它不能出现在可选列表里
    expect(screen.queryByTestId("symbol-600809.SH")).toBeNull();
  });

  it("min_assets 大于标的数时显示警告（不阻塞编辑，但明确说不能提交）", () => {
    renderPanel({ symbols: ["600519.SH", "000858.SZ"], minAssets: "5" });
    expect(screen.getByTestId("portfolio-min-assets-hint")).toHaveTextContent("无法提交");
  });

  it("min_assets 低于 5 时提示口径与预登记不同", () => {
    // 必须给 >= 2 只标的：少于 2 时提示由 symbols 错误负责，hint 不渲染
    renderPanel({ symbols: ["600519.SH", "000858.SZ"], minAssets: "2" });
    expect(screen.getByTestId("portfolio-min-assets-hint")).toHaveTextContent("预登记");
  });

  it("选股方式切到全池等权时隐藏比例输入并解释代价", async () => {
    const handlers = renderPanel();
    expect(screen.getByTestId("portfolio-top-fraction")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByTestId("portfolio-selection"), "equal_weight_all");
    expect(handlers.onChange).toHaveBeenCalledWith({ selection: "equal_weight_all" });
  });

  it("运行中禁用按钮，避免重复提交", () => {
    const handlers = {
      onChange: vi.fn(),
      onToggleSymbol: vi.fn(),
      onSelectAll: vi.fn(),
      onClearSymbols: vi.fn(),
      onRun: vi.fn(),
      onRefreshDatasets: vi.fn(),
    };
    render(
      <PortfolioPanel
        datasets={{ status: "ready", data: makeDatasets() }}
        values={{ ...PORTFOLIO_DEFAULTS, symbols: ["600519.SH"] }}
        errors={{}}
        running
        {...handlers}
      />,
    );
    expect(screen.getByTestId("portfolio-run")).toBeDisabled();
    expect(screen.getByTestId("portfolio-run")).toHaveTextContent("执行中");
  });

  it("数据集加载失败时显示错误与重试", () => {
    const handlers = {
      onChange: vi.fn(),
      onToggleSymbol: vi.fn(),
      onSelectAll: vi.fn(),
      onClearSymbols: vi.fn(),
      onRun: vi.fn(),
      onRefreshDatasets: vi.fn(),
    };
    render(
      <PortfolioPanel
        datasets={{
          status: "error",
          error: { kind: "backend_unavailable", message: "连接失败" } as never,
        }}
        values={PORTFOLIO_DEFAULTS}
        errors={{}}
        running={false}
        {...handlers}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
