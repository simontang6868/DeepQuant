/**
 * HistoryList 组件测试（mock getBacktestRuns，保留 client 其他导出）。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const listMock = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getBacktestRuns: (params: unknown) => listMock(params),
  };
});

import { HistoryList } from "./HistoryList";

const SAMPLE_ITEM = {
  run_id: "abc123def456abc123def456abc123def",
  created_at: "2026-01-01T08:00:00.000000Z",
  status: "succeeded",
  symbol: "000001.SZ",
  strategy_id: "ma_cross",
  strategy_version: "0.1.0",
  strategy_params: { fast_window: 5, slow_window: 20 },
  dataset_symbol: "TEST01",
  start_date: "2020-01-01",
  end_date: "2020-02-09T00:00:00",
  initial_cash: 100000,
  source_sha256: "sha256:aaa",
  dataset_digest: "sha256:bbb",
  result_digest: "sha256:ccc",
  is_complete: true,
  duration_ms: 1234,
};

afterEach(() => {
  listMock.mockReset();
});

async function waitForLoadingDone() {
  await waitFor(() => {
    expect(screen.queryByTestId("history-loading")).toBeNull();
  });
}

/** 读取 compare-bar 上显示的已选数量（从 <strong> 里取，避免 strong 打断 text 匹配）。 */
function getSelectedCount(): number {
  const el = screen.getByTestId("compare-selection-info");
  const strong = el.querySelector("strong");
  return Number(strong?.textContent);
}

describe("HistoryList", () => {
  it("空历史记录显示空状态", async () => {
    listMock.mockResolvedValueOnce({ items: [], total: 0, limit: 20, offset: 0 });
    render(<HistoryList onSelectRun={() => {}} />);
    expect(screen.getByTestId("history-loading")).toBeInTheDocument();
    await waitForLoadingDone();
    expect(screen.getByTestId("history-empty")).toBeInTheDocument();
  });

  it("正常列表渲染并可点击查看详情", async () => {
    listMock.mockResolvedValueOnce({ items: [SAMPLE_ITEM], total: 1, limit: 20, offset: 0 });
    const onSelect = vi.fn();
    render(<HistoryList onSelectRun={onSelect} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("history-table")).toBeInTheDocument();
    expect(screen.getByText("000001.SZ")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("view-detail-abc123def456abc123def456abc123def"));
    expect(onSelect).toHaveBeenCalledWith("abc123def456abc123def456abc123def");
  });

  it("加载失败显示错误提示", async () => {
    listMock.mockRejectedValueOnce(new Error("网络错误"));
    render(<HistoryList onSelectRun={() => {}} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("history-error")).toBeInTheDocument();
  });

  it("筛选与查询触发重新加载", async () => {
    listMock
      .mockResolvedValueOnce({ items: [], total: 0, limit: 20, offset: 0 })
      .mockResolvedValueOnce({ items: [SAMPLE_ITEM], total: 1, limit: 20, offset: 0 });

    render(<HistoryList onSelectRun={() => {}} />);
    await waitForLoadingDone();

    await userEvent.type(screen.getByPlaceholderText("如 000001.SZ"), "000001.SZ");
    await userEvent.click(screen.getByTestId("filter-apply"));
    await waitForLoadingDone();

    expect(listMock).toHaveBeenCalledTimes(2);
    const calledWith = listMock.mock.calls[1][0] as Record<string, unknown>;
    expect(calledWith.symbol).toBe("000001.SZ");
  });

  it("分页：下一页按钮调用正确 offset", async () => {
    const itemBatch = Array.from({ length: 20 }, (_, i) => ({
      ...SAMPLE_ITEM,
      run_id: `item-${i}`,
    }));
    const page2 = Array.from({ length: 3 }, (_, i) => ({
      ...SAMPLE_ITEM,
      run_id: `item-20-${i}`,
    }));
    listMock
      .mockResolvedValueOnce({ items: itemBatch, total: 23, limit: 20, offset: 0 })
      .mockResolvedValueOnce({ items: page2, total: 23, limit: 20, offset: 20 });

    render(<HistoryList onSelectRun={() => {}} />);
    await waitForLoadingDone();

    await userEvent.click(screen.getByTestId("page-next"));
    await waitForLoadingDone();

    expect(listMock).toHaveBeenCalledTimes(2);
    const calledWith = listMock.mock.calls[1][0] as Record<string, unknown>;
    expect(calledWith.offset).toBe(20);
  });

  it("分页边界：末页禁用下一页", async () => {
    listMock.mockResolvedValueOnce({ items: [SAMPLE_ITEM], total: 1, limit: 20, offset: 0 });
    render(<HistoryList onSelectRun={() => {}} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("page-next")).toBeDisabled();
  });

  it("分页边界：首页禁用上一页", async () => {
    listMock.mockResolvedValueOnce({ items: [SAMPLE_ITEM], total: 1, limit: 20, offset: 0 });
    render(<HistoryList onSelectRun={() => {}} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("page-prev")).toBeDisabled();
  });

  // ----------------------------------------------------------------------- //
  // 多选 + 比较（Step 6.5）
  // ----------------------------------------------------------------------- //

  it("未传 onCompare 时不显示选择列", async () => {
    listMock.mockResolvedValueOnce({ items: [SAMPLE_ITEM], total: 1, limit: 20, offset: 0 });
    render(<HistoryList onSelectRun={() => {}} />);
    await waitForLoadingDone();
    expect(screen.queryByTestId("compare-bar")).toBeNull();
    expect(screen.queryByTestId(`select-${SAMPLE_ITEM.run_id}`)).toBeNull();
  });

  it("传 onCompare 时显示选择列和比较条", async () => {
    listMock.mockResolvedValueOnce({ items: [SAMPLE_ITEM], total: 1, limit: 20, offset: 0 });
    render(<HistoryList onSelectRun={() => {}} onCompare={() => {}} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("compare-bar")).toBeInTheDocument();
    expect(screen.getByTestId(`select-${SAMPLE_ITEM.run_id}`)).toBeInTheDocument();
  });

  it("选择与取消选择工作正常", async () => {
    listMock.mockResolvedValueOnce({ items: [SAMPLE_ITEM], total: 1, limit: 20, offset: 0 });
    render(<HistoryList onSelectRun={() => {}} onCompare={() => {}} />);
    await waitForLoadingDone();

    const cb = screen.getByTestId(`select-${SAMPLE_ITEM.run_id}`) as HTMLInputElement;
    expect(cb.checked).toBe(false);
    expect(getSelectedCount()).toBe(0);

    await userEvent.click(cb);
    expect(cb.checked).toBe(true);
    expect(getSelectedCount()).toBe(1);

    await userEvent.click(cb);
    expect(cb.checked).toBe(false);
    expect(getSelectedCount()).toBe(0);
  });

  it("少于 2 条时比较按钮禁用", async () => {
    listMock.mockResolvedValueOnce({ items: [SAMPLE_ITEM], total: 1, limit: 20, offset: 0 });
    render(<HistoryList onSelectRun={() => {}} onCompare={() => {}} />);
    await waitForLoadingDone();

    expect(screen.getByTestId("compare-selected")).toBeDisabled();

    await userEvent.click(screen.getByTestId(`select-${SAMPLE_ITEM.run_id}`));
    // 只有 1 条，仍禁用
    expect(screen.getByTestId("compare-selected")).toBeDisabled();
  });

  it("达到 2 条以上后比较按钮启用并正确回调", async () => {
    const item2 = { ...SAMPLE_ITEM, run_id: "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz" };
    listMock.mockResolvedValueOnce({
      items: [SAMPLE_ITEM, item2],
      total: 2,
      limit: 20,
      offset: 0,
    });
    const onCompare = vi.fn();
    render(<HistoryList onSelectRun={() => {}} onCompare={onCompare} />);
    await waitForLoadingDone();

    await userEvent.click(screen.getByTestId(`select-${SAMPLE_ITEM.run_id}`));
    expect(screen.getByTestId("compare-selected")).toBeDisabled();

    await userEvent.click(screen.getByTestId(`select-${item2.run_id}`));
    expect(screen.getByTestId("compare-selected")).toBeEnabled();

    await userEvent.click(screen.getByTestId("compare-selected"));
    expect(onCompare).toHaveBeenCalledWith([SAMPLE_ITEM.run_id, item2.run_id]);
  });

  it("超过 5 条上限后其他 checkbox 禁用", async () => {
    const items = Array.from({ length: 6 }, (_, i) => ({
      ...SAMPLE_ITEM,
      run_id: `run-${i}-idrun-idrun-idru`,
    }));
    listMock.mockResolvedValueOnce({ items, total: 6, limit: 20, offset: 0 });
    render(<HistoryList onSelectRun={() => {}} onCompare={() => {}} />);
    await waitForLoadingDone();

    for (let i = 0; i < 5; i += 1) {
      await userEvent.click(screen.getByTestId(`select-run-${i}-idrun-idrun-idru`));
    }
    expect(getSelectedCount()).toBe(5);

    // 第 6 条应禁用
    const sixth = screen.getByTestId("select-run-5-idrun-idrun-idru") as HTMLInputElement;
    expect(sixth.disabled).toBe(true);

    // 取消一条后第 6 条恢复可用
    await userEvent.click(screen.getByTestId("select-run-0-idrun-idrun-idru"));
    expect(sixth.disabled).toBe(false);
  });

  it("清空按钮重置所有选择", async () => {
    const item2 = { ...SAMPLE_ITEM, run_id: "yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy" };
    listMock.mockResolvedValueOnce({
      items: [SAMPLE_ITEM, item2],
      total: 2,
      limit: 20,
      offset: 0,
    });
    render(<HistoryList onSelectRun={() => {}} onCompare={() => {}} />);
    await waitForLoadingDone();

    await userEvent.click(screen.getByTestId(`select-${SAMPLE_ITEM.run_id}`));
    await userEvent.click(screen.getByTestId(`select-${item2.run_id}`));
    expect(getSelectedCount()).toBe(2);

    await userEvent.click(screen.getByTestId("compare-clear-selection"));
    expect(getSelectedCount()).toBe(0);
    expect(screen.getByTestId("compare-selected")).toBeDisabled();
  });

  it("翻页后已选记录保留（跨页持久化）", async () => {
    const page1 = Array.from({ length: 20 }, (_, i) => ({
      ...SAMPLE_ITEM,
      run_id: `p1-run-${i}-idrun-idru`,
    }));
    const page2 = Array.from({ length: 3 }, (_, i) => ({
      ...SAMPLE_ITEM,
      run_id: `p2-run-${i}-idrun-idru`,
    }));
    listMock
      .mockResolvedValueOnce({ items: page1, total: 23, limit: 20, offset: 0 })
      .mockResolvedValueOnce({ items: page2, total: 23, limit: 20, offset: 20 });

    render(<HistoryList onSelectRun={() => {}} onCompare={() => {}} />);
    await waitForLoadingDone();

    await userEvent.click(screen.getByTestId("select-p1-run-0-idrun-idru"));
    await userEvent.click(screen.getByTestId("select-p1-run-1-idrun-idru"));
    expect(getSelectedCount()).toBe(2);

    await userEvent.click(screen.getByTestId("page-next"));
    await waitForLoadingDone();

    expect(getSelectedCount()).toBe(2);
  });
});
