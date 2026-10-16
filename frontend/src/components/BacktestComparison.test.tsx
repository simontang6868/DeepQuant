/**
 * BacktestComparison 组件测试（mock postBacktestCompare）。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../api/client";

const compareMock = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    postBacktestCompare: (payload: unknown) => compareMock(payload),
  };
});

import { BacktestComparison } from "./BacktestComparison";

const BASE_META_A = {
  run_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  created_at: "2026-01-01T08:00:00.000000Z",
  status: "succeeded",
  symbol: "000001.SZ",
  strategy_id: "ma_cross",
  strategy_version: "0.1.0",
  strategy_params: { fast_window: 5, slow_window: 20 },
  dataset_symbol: "TEST01",
  start_date: "2020-01-01",
  end_date: "2020-06-30T00:00:00",
  initial_cash: 100000,
  source_sha256: "sha256:aaa",
  dataset_digest: "sha256:bbb",
  result_digest: "sha256:ccc",
  is_complete: true,
  duration_ms: 1234,
};

const BASE_META_B = {
  ...BASE_META_A,
  run_id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  created_at: "2026-01-02T09:00:00.000000Z",
  strategy_params: { fast_window: 10, slow_window: 30 },
};

const METRICS_A = {
  initial_equity: 100000,
  final_equity: 115234.56,
  total_pnl: 15234.56,
  cumulative_return: 0.1523,
  annualized_return: 0.31,
  annualized_volatility: 0.18,
  max_drawdown_amount: -5230.12,
  max_drawdown_ratio: -0.045,
  n_orders: 25,
  n_filled_orders: 24,
  n_fills: 24,
  n_closed_trades: 24,
  win_rate: 0.55,
};

const METRICS_B = { ...METRICS_A, final_equity: 108000, cumulative_return: 0.08, win_rate: 0.48 };

function makeResponse(overrides: Record<string, unknown> = {}) {
  return {
    items: [
      { run_id: BASE_META_A.run_id, metadata: BASE_META_A, metrics: METRICS_A },
      { run_id: BASE_META_B.run_id, metadata: BASE_META_B, metrics: METRICS_B },
    ],
    pairwise: {
      [["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"].sort().join("||")]: {
        fairly_comparable: true,
        identical: {},
        differences: {
          strategy_params: [
            JSON.stringify({ fast_window: 5, slow_window: 20 }),
            JSON.stringify({ fast_window: 10, slow_window: 30 }),
          ],
        },
        reasons: [],
        strategy_differs: true,
      },
    },
    warnings: ["策略参数不同，请关注这一点。"],
    ...overrides,
  };
}

afterEach(() => {
  compareMock.mockReset();
});

describe("BacktestComparison", () => {
  it("加载状态正确显示并请求后端", async () => {
    compareMock.mockResolvedValueOnce(makeResponse());
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    expect(screen.getByTestId("compare-loading")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId("compare-loading")).toBeNull();
    });
    expect(compareMock).toHaveBeenCalledWith({
      run_ids: ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
    });
  });

  it("成功时渲染概览表", async () => {
    compareMock.mockResolvedValueOnce(makeResponse());
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-overview")).toBeInTheDocument();
    });
    // 元数据（两个 run 都是 000001.SZ，所以出现多次）
    expect(screen.getAllByText("000001.SZ").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("ma_cross").length).toBeGreaterThanOrEqual(1);
  });

  it("成功时渲染绩效对比表", async () => {
    compareMock.mockResolvedValueOnce(makeResponse());
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-metrics")).toBeInTheDocument();
    });
    // 指标标签
    expect(screen.getByText("总收益率")).toBeInTheDocument();
    expect(screen.getByText("最大回撤")).toBeInTheDocument();
    // A 的 15.23% / B 的 8.00%
    expect(screen.getByText("15.23%")).toBeInTheDocument();
    expect(screen.getByText("8.00%")).toBeInTheDocument();
  });

  it("缺失绩效指标显示「暂无数据」", async () => {
    const metricsMissing = { ...METRICS_A, win_rate: null, max_drawdown_ratio: null };
    compareMock.mockResolvedValueOnce(
      makeResponse({
        items: [
          { run_id: BASE_META_A.run_id, metadata: BASE_META_A, metrics: metricsMissing },
          { run_id: BASE_META_B.run_id, metadata: BASE_META_B, metrics: METRICS_B },
        ],
      }),
    );
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-metrics")).toBeInTheDocument();
    });
    // 空状态渲染，不应该是 0% 或 0.00
    const missingCells = screen.getAllByText("暂无数据");
    expect(missingCells.length).toBeGreaterThanOrEqual(2);
  });

  it("差异表正确展示", async () => {
    compareMock.mockResolvedValueOnce(makeResponse());
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-diff-list")).toBeInTheDocument();
    });
    expect(screen.getByText("策略参数")).toBeInTheDocument();
  });

  it("所有配置一致时显示无差异提示", async () => {
    compareMock.mockResolvedValueOnce(
      makeResponse({
        pairwise: {
          [["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"].sort().join("||")]: {
            fairly_comparable: true,
            identical: {},
            differences: {},
            reasons: [],
            strategy_differs: false,
          },
        },
      }),
    );
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-diff-none")).toBeInTheDocument();
    });
  });

  it("警示块正确展示", async () => {
    compareMock.mockResolvedValueOnce(makeResponse());
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-warnings")).toBeInTheDocument();
    });
    expect(screen.getByText("策略参数不同，请关注这一点。")).toBeInTheDocument();
  });

  it("无可比性警示时显示正提示", async () => {
    compareMock.mockResolvedValueOnce(
      makeResponse({
        warnings: [],
        pairwise: {
          [["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"].sort().join("||")]: {
            fairly_comparable: true,
            identical: {},
            differences: {},
            reasons: [],
            strategy_differs: false,
          },
        },
      }),
    );
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-warn-none")).toBeInTheDocument();
    });
  });

  it("请求 404 时明确提示不存在", async () => {
    compareMock.mockRejectedValueOnce(
      new ApiError("http_error", "run xxx 不存在", {
        status: 404,
        code: "not_found",
        details: { message: "run xxx 不存在" },
      }),
    );
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-error")).toBeInTheDocument();
    });
    expect(screen.getByText(/部分历史回测不存在/)).toBeInTheDocument();
  });

  it("5xx 错误显示失败提示", async () => {
    compareMock.mockRejectedValueOnce(
      new ApiError("http_error", "后端返回 HTTP 500", { status: 500 }),
    );
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-error")).toBeInTheDocument();
    });
  });

  it("onBack 返回按钮可点击", async () => {
    compareMock.mockResolvedValueOnce(makeResponse());
    const backHandler = vi.fn();
    render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={backHandler}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("compare-overview")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByText("← 返回历史列表"));
    expect(backHandler).toHaveBeenCalledTimes(1);
  });

  it("runIds 变化时重新请求（不被旧响应覆盖）", async () => {
    let responseIndex = 0;
    compareMock.mockImplementation((_p: unknown) => {
      const idx = responseIndex++;
      if (idx === 0) {
        // 旧响应慢返回
        return new Promise((resolve) => {
          setTimeout(() => resolve(makeResponse()), 500);
        });
      }
      // 新响应快返回
      return Promise.resolve(makeResponse({ warnings: ["新请求已完成"] }));
    });

    const { rerender } = render(
      <BacktestComparison
        runIds={["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}
        onBack={() => {}}
      />,
    );

    // 立刻 rerender 换 runIds → 组件应递增 versionRef 忽略旧响应
    rerender(
      <BacktestComparison
        runIds={["cccccccccccccccccccccccccccccccc", "dddddddddddddddddddddddddddddddd"]}
        onBack={() => {}}
      />,
    );

    // 等待新响应 → 应展示新的 warnings，旧响应到达后不应覆盖
    await waitFor(() => {
      expect(screen.getByText("新请求已完成")).toBeInTheDocument();
    });
  });
});
