/**
 * 数据集选择组件测试：真实 API 数据展示 / 空态 / invalid_datasets 提示 /
 * 加载与错误态 / 选择回调。
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DatasetSelector } from "./DatasetSelector";
import type { DatasetListResponse } from "../api/types";
import { ApiError } from "../api/client";

const sample: DatasetListResponse = {
  datasets: [
    {
      symbol: "TEST01",
      rows: 60,
      start_date: "2020-01-01",
      end_date: "2020-02-29",
      source_name: "TEST01.csv",
      source_sha256: "a".repeat(64),
      ingested_at: "2020-03-01T08:00:00",
    },
  ],
  invalid_datasets: [
    { metadata_file: "broken.json", symbol: null, reason: "元数据缺少必需字段" },
  ],
};

describe("DatasetSelector", () => {
  it("展示后端返回的数据集与详情（含原始 CSV 指纹说明）", () => {
    render(
      <DatasetSelector
        state={{ status: "ready", data: sample }}
        selectedSymbol="TEST01"
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByText(/TEST01（60 行/)).toBeInTheDocument();
    expect(screen.getByText("数据集详情")).toBeInTheDocument();
    expect(screen.getByText("原始 CSV 指纹")).toBeInTheDocument();
    expect(screen.getByText(/可选回测区间：2020-01-01 ~ 2020-02-29/)).toBeInTheDocument();
  });

  it("invalid_datasets 显示警告，且不进入可选项", () => {
    render(
      <DatasetSelector
        state={{ status: "ready", data: sample }}
        selectedSymbol=""
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByTestId("invalid-datasets")).toHaveTextContent("broken.json");
    expect(screen.getByTestId("invalid-datasets")).toHaveTextContent("元数据缺少必需字段");
    // select 中只有占位项与合法数据集
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2); // 占位 + TEST01
    expect(options[1]!.textContent).toContain("TEST01");
  });

  it("空列表显示空状态（列表不来自硬编码）", () => {
    render(
      <DatasetSelector
        state={{ status: "ready", data: { datasets: [], invalid_datasets: [] } }}
        selectedSymbol=""
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByTestId("datasets-empty")).toBeInTheDocument();
  });

  it("加载失败显示错误与重试按钮，重试触发 onRefresh", async () => {
    const onRefresh = vi.fn();
    render(
      <DatasetSelector
        state={{
          status: "error",
          error: new ApiError("backend_unavailable", "无法连接后端服务"),
        }}
        selectedSymbol=""
        onSelect={vi.fn()}
        onRefresh={onRefresh}
      />,
    );
    expect(screen.getByTestId("datasets-error")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("选择数据集触发 onSelect 回调", async () => {
    const onSelect = vi.fn();
    render(
      <DatasetSelector
        state={{ status: "ready", data: sample }}
        selectedSymbol=""
        onSelect={onSelect}
        onRefresh={vi.fn()}
      />,
    );
    await userEvent.selectOptions(screen.getByTestId("dataset-select"), "TEST01");
    expect(onSelect).toHaveBeenCalledWith("TEST01");
  });

  it("加载中显示加载状态", () => {
    render(
      <DatasetSelector
        state={{ status: "loading" }}
        selectedSymbol=""
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByText("正在加载数据集…")).toBeInTheDocument();
  });
});
