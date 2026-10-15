/**
 * 后端连接状态测试：三态展示 + 未连接时的地址、启动指引与重试。
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BackendDownBanner, BackendStatusIndicator } from "./BackendStatusBar";
import type { BackendHealth } from "../hooks/useBackendHealth";
import { ApiError } from "../api/client";

describe("BackendStatusIndicator", () => {
  it("检查中", () => {
    const health: BackendHealth = { status: "checking", error: null, lastCheckedAt: null };
    render(<BackendStatusIndicator health={health} />);
    expect(screen.getByText("正在检查后端连接…")).toBeInTheDocument();
  });

  it("已连接", () => {
    const health: BackendHealth = {
      status: "ok",
      error: null,
      lastCheckedAt: "10:00:00",
    };
    render(<BackendStatusIndicator health={health} />);
    expect(screen.getByText(/后端已连接/)).toBeInTheDocument();
  });

  it("未连接", () => {
    const health: BackendHealth = { status: "down", error: null, lastCheckedAt: null };
    render(<BackendStatusIndicator health={health} />);
    expect(screen.getByText("后端未连接")).toBeInTheDocument();
  });
});

describe("BackendDownBanner", () => {
  it("展示当前后端地址与启动指引", () => {
    const health: BackendHealth = {
      status: "down",
      error: new ApiError("backend_unavailable", "无法连接后端服务"),
      lastCheckedAt: "10:00:00",
    };
    render(<BackendDownBanner health={health} onRetry={vi.fn()} />);
    const banner = screen.getByTestId("backend-down-banner");
    expect(banner).toHaveTextContent("无法连接后端服务");
    expect(banner).toHaveTextContent("当前后端地址");
    expect(banner).toHaveTextContent("uvicorn deepquant.api.app:app --port 8123");
  });

  it("已连接时不显示横幅", () => {
    const health: BackendHealth = { status: "ok", error: null, lastCheckedAt: null };
    render(<BackendDownBanner health={health} onRetry={vi.fn()} />);
    expect(screen.queryByTestId("backend-down-banner")).not.toBeInTheDocument();
  });

  it("点击重试触发 onRetry", async () => {
    const onRetry = vi.fn();
    const health: BackendHealth = { status: "down", error: null, lastCheckedAt: null };
    render(<BackendDownBanner health={health} onRetry={onRetry} />);
    await userEvent.click(screen.getByTestId("retry-backend"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
