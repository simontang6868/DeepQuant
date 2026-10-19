/**
 * 导出条测试。
 *
 * 核心是「失败必须说出来」：点了导出却什么都没存下来，是这里唯一不能犯的错。
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ResultExportBar } from "./ResultExportBar";
import type { ExportAction } from "../utils/export";

function renderBar(actions: ExportAction[], note?: string) {
  return render(
    <ResultExportBar baseName="deepquant_demo" actions={actions} note={note} />,
  );
}

describe("ResultExportBar", () => {
  it("为每个动作渲染一个按钮", () => {
    renderBar([
      { key: "summary", label: "绩效摘要 CSV", run: vi.fn() },
      { key: "fills", label: "成交明细 CSV", run: vi.fn() },
    ]);
    expect(screen.getByTestId("export-summary")).toHaveTextContent("绩效摘要 CSV");
    expect(screen.getByTestId("export-fills")).toHaveTextContent("成交明细 CSV");
  });

  it("点击触发对应动作", async () => {
    const run = vi.fn();
    renderBar([{ key: "summary", label: "绩效摘要 CSV", run }]);
    await userEvent.click(screen.getByTestId("export-summary"));
    expect(run).toHaveBeenCalledOnce();
  });

  it("成功后给出包含文件名的提示", async () => {
    renderBar([{ key: "summary", label: "绩效摘要 CSV", run: vi.fn() }]);
    await userEvent.click(screen.getByTestId("export-summary"));
    const feedback = screen.getByTestId("export-feedback");
    expect(feedback).toHaveTextContent("已导出：deepquant_demo_summary");
    expect(feedback).not.toHaveAttribute("role", "alert");
  });

  it("失败时把原因显示出来并标为 alert", async () => {
    renderBar([
      {
        key: "equity",
        label: "净值曲线 PNG",
        run: () => {
          throw new Error("图表导出失败：浏览器无法把图表转成图片。");
        },
      },
    ]);
    await userEvent.click(screen.getByTestId("export-equity"));
    const feedback = screen.getByTestId("export-feedback");
    expect(feedback).toHaveTextContent("图表导出失败");
    expect(feedback).toHaveAttribute("role", "alert");
  });

  it("异步动作进行中禁用按钮，避免重复点击", async () => {
    let release: (() => void) | undefined;
    const run = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    renderBar([{ key: "summary", label: "绩效摘要 CSV", run }]);
    const button = screen.getByTestId("export-summary");
    await userEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("导出中…");
    release?.();
  });

  it("展示口径说明", () => {
    renderBar([{ key: "summary", label: "绩效摘要 CSV", run: vi.fn() }], "CSV 为纯数据。");
    expect(screen.getByText("CSV 为纯数据。")).toBeInTheDocument();
  });
});
