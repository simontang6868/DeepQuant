/**
 * 策略面板测试：展示后端 schema 与默认值 / fast-slow 即时提示 / 列表为空阻止提交前提。
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrategyPanel } from "./StrategyPanel";
import type { StrategyListResponse } from "../api/types";
import { ApiError } from "../api/client";

const strategies: StrategyListResponse = {
  strategies: [
    {
      strategy_id: "ma_cross",
      display_name: "双均线交叉",
      version: "1.0.0",
      description: "快线上穿慢线买入，下穿卖出。",
      params: {
        fast_window: {
          type: "integer",
          default: 5,
          description: "快线窗口（必须小于 slow_window）",
          minimum: 1,
        },
        slow_window: {
          type: "integer",
          default: 20,
          description: "慢线窗口（必须大于 fast_window）",
          minimum: 2,
        },
      },
    },
  ],
};

function renderPanel(overrides: Record<string, unknown> = {}) {
  const props = {
    state: { status: "ready", data: strategies } as const,
    selectedId: "ma_cross",
    paramValues: {},
    fieldErrors: {},
    onSelect: vi.fn(),
    onParamChange: vi.fn(),
    onRefresh: vi.fn(),
    ...overrides,
  };
  render(<StrategyPanel {...props} />);
  return props;
}

describe("StrategyPanel", () => {
  it("展示策略名称、ID、版本与说明", () => {
    renderPanel();
    expect(screen.getByText(/双均线交叉（ma_cross v1.0.0）/)).toBeInTheDocument();
    expect(screen.getByText("快线上穿慢线买入，下穿卖出。")).toBeInTheDocument();
  });

  it("参数输入留空时 placeholder 显示后端默认值（默认值来自后端 schema）", () => {
    renderPanel();
    expect(screen.getByTestId("param-fast_window")).toHaveAttribute("placeholder", "留空使用默认值 5");
    expect(screen.getByTestId("param-slow_window")).toHaveAttribute("placeholder", "留空使用默认值 20");
    // minimum 约束同步到输入控件
    expect(screen.getByTestId("param-fast_window")).toHaveAttribute("min", "1");
  });

  it("输入 slow ≤ fast 时显示即时跨参数提示", async () => {
    const onParamChange = vi.fn();
    renderPanel({ paramValues: { fast_window: "25", slow_window: "20" }, onParamChange });
    expect(screen.getByText(/slow_window（20）必须大于 fast_window（25）/)).toBeInTheDocument();
  });

  it("合法组合不显示提示；修改参数触发 onParamChange", async () => {
    const onParamChange = vi.fn();
    renderPanel({ paramValues: { fast_window: "5", slow_window: "20" }, onParamChange });
    expect(screen.queryByText(/slow_window（\d+）必须大于 fast_window（/)).not.toBeInTheDocument();
    await userEvent.type(screen.getByTestId("param-fast_window"), "7");
    expect(onParamChange).toHaveBeenCalled();
  });

  it("策略列表为空显示空状态", () => {
    renderPanel({
      state: { status: "ready", data: { strategies: [] } },
      selectedId: "",
    });
    expect(screen.getByTestId("strategies-empty")).toBeInTheDocument();
  });

  it("加载失败显示错误与重试", () => {
    renderPanel({
      state: { status: "error", error: new ApiError("backend_unavailable", "连接失败") },
      selectedId: "",
    });
    expect(screen.getByTestId("strategies-error")).toBeInTheDocument();
  });

  it("展示前端校验仅为提示、后端为最终依据的说明", () => {
    renderPanel();
    expect(screen.getByText(/前端校验仅用于即时提示；参数最终以后端校验为准/)).toBeInTheDocument();
  });
});
