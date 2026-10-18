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

// --------------------------------------------------------------------------- //
// 50 只池接进来之后的两件事：名称与筛选
// --------------------------------------------------------------------------- //

function makeDatasets(n: number, names: Record<string, string> = {}) {
  return Array.from({ length: n }, (_, i) => {
    const code = `${600000 + i}.SH`;
    return {
      symbol: code,
      rows: 1000 + i,
      start_date: "2017-01-03",
      end_date: "2024-12-31",
      source_name: `${code}.qfq.csv`,
      source_sha256: "b".repeat(64),
      ingested_at: "2026-10-18T20:00:00",
      display_name: names[code] ?? null,
    };
  });
}

describe("DatasetSelector 大数据集列表", () => {
  it("有名称时显示「名称 · 代码」，无名称时只显示代码", () => {
    render(
      <DatasetSelector
        state={{
          status: "ready",
          data: {
            datasets: makeDatasets(2, { "600000.SH": "浦发银行" }),
            invalid_datasets: [],
          },
        }}
        selectedSymbol="600001.SH"
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    const options = screen.getAllByRole("option");
    expect(options[1]!.textContent).toContain("浦发银行 · 600000.SH");
    // 名称缺失不编造，回落到代码
    expect(options[2]!.textContent).toContain("600001.SH");
    expect(options[2]!.textContent).not.toContain("·");
  });

  it("数据集较少时不显示筛选框（避免噪音）", () => {
    render(
      <DatasetSelector
        state={{
          status: "ready",
          data: { datasets: makeDatasets(12), invalid_datasets: [] },
        }}
        selectedSymbol=""
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("dataset-filter")).toBeNull();
    expect(screen.getByTestId("dataset-count")).toHaveTextContent("共 12 个可用数据集");
  });

  it("数据集较多时可按名称筛选，并提示匹配数", async () => {
    render(
      <DatasetSelector
        state={{
          status: "ready",
          data: {
            datasets: makeDatasets(30, { "600001.SH": "贵州茅台" }),
            invalid_datasets: [],
          },
        }}
        selectedSymbol=""
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    await userEvent.type(screen.getByTestId("dataset-filter"), "茅台");
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2); // 占位 + 600001.SH
    expect(options[1]!.textContent).toContain("600001.SH");
    expect(screen.getByTestId("dataset-count")).toHaveTextContent("筛选匹配 1 个");
  });

  it("筛掉的当前已选项仍保留，否则下拉框会显示成空白", async () => {
    render(
      <DatasetSelector
        state={{
          status: "ready",
          data: {
            datasets: makeDatasets(30, { "600001.SH": "贵州茅台" }),
            invalid_datasets: [],
          },
        }}
        selectedSymbol="600005.SH"
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    await userEvent.type(screen.getByTestId("dataset-filter"), "茅台");
    const select = screen.getByTestId("dataset-select") as HTMLSelectElement;
    expect(select.value).toBe("600005.SH");
    expect(screen.getByTestId("dataset-count")).toHaveTextContent("含当前已选项");
    // 提示语不能假装匹配数为 2
    expect(screen.getByTestId("dataset-count")).toHaveTextContent("筛选匹配 1 个");
  });

  it("无匹配时给出提示，且不假装有结果", async () => {
    render(
      <DatasetSelector
        state={{
          status: "ready",
          data: {
            datasets: makeDatasets(30, { "600001.SH": "贵州茅台" }),
            invalid_datasets: [],
          },
        }}
        selectedSymbol=""
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    await userEvent.type(screen.getByTestId("dataset-filter"), "不存在的标的");
    expect(screen.getByTestId("dataset-no-match")).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(1); // 只剩占位
  });

  it("详情面板展示名称（有名称时）", () => {
    render(
      <DatasetSelector
        state={{
          status: "ready",
          data: {
            datasets: makeDatasets(2, { "600001.SH": "贵州茅台" }),
            invalid_datasets: [],
          },
        }}
        selectedSymbol="600001.SH"
        onSelect={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByTestId("dataset-display-name")).toHaveTextContent("贵州茅台");
  });
});
