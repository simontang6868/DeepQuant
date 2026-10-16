/**
 * HistoryDetail 组件测试。
 *
 * 覆盖：加载中 / 正常详情渲染 / 404 不存在 / 完整性错误 / 5xx / 返回按钮。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { BacktestResult } from "../api/types";

const detailMock = vi.fn();
const integrityMock = vi.fn();
const reproMock = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getBacktestRunDetail: (runId: string) => detailMock(runId),
    getBacktestRunIntegrity: (runId: string) => integrityMock(runId),
    getBacktestRunReproducibility: (runId: string) => reproMock(runId),
  };
});

import { HistoryDetail } from "./HistoryDetail";
import { ApiError } from "../api/client";

function makeResult(): BacktestResult {
  return {
    schema_version: "0.1.0",
    engine_version: "0.1.0",
    config: {
      symbol: "000001.SZ",
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
      name: "ma_cross", version: "1.0.0", params: { fast_window: 5 },
      params_available: true, identified: true, reproducible: true, notes: [],
    },
    dataset: {
      dataset_id: "TEST01", n_rows: 60,
      columns: ["open", "high", "low", "close", "volume"],
      date_range: { start: "2020-01-01", end: "2020-02-29" },
      digest: "sha256:abc123", digest_algorithm: "sha256", canonicalization: {},
    },
    orders: [], fills: [], account_states: [],
    data_quality_issues: [], limitations: [],
    is_complete: true, incomplete_reasons: [],
    performance: null,
    reproducibility: { inputs_identifiable: true, process_reproducible: true, missing: [], notes: [] },
  };
}

const SAMPLE_META = {
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
  detailMock.mockReset();
  integrityMock.mockReset();
  reproMock.mockReset();
});

beforeEach(() => {
  // 默认：完整性审计通过、复现清单可读取（各用例可覆盖）
  integrityMock.mockResolvedValue(PASSED_INTEGRITY);
  reproMock.mockResolvedValue(SAMPLE_REPRO);
});

const SAMPLE_REPRO = {
  run_id: SAMPLE_META.run_id,
  generated_at: "2026-01-01T00:00:02.000000Z",
  result_document_state: "ok",
  items: [
    { key: "run_id", label: "回测标识（run_id）", category: "backtest_identity", status: "available", detail: "记录中保存了唯一 run_id", value: SAMPLE_META.run_id },
    { key: "source_sha256", label: "原始数据指纹（source_sha256）", category: "data_source", status: "not_verifiable", detail: "已记录指纹，但原始文件未归档", value: "sha256:aaa" },
    { key: "dataset_digest", label: "处理后数据摘要（dataset_digest）", category: "data_source", status: "not_verifiable", detail: "处理后数据摘要，非原始文件指纹", value: "sha256:bbb" },
    { key: "raw_market_file", label: "原始行情文件", category: "data_source", status: "not_archived", detail: "系统不归档原始行情文件", value: null },
    { key: "strategy_code_version", label: "策略代码版本", category: "strategy", status: "not_recorded", detail: "未保存策略源代码或快照", value: null },
    { key: "runtime_environment", label: "历史运行环境（Python / 依赖 / 操作系统）", category: "engine", status: "not_recorded", detail: "未保存运行环境信息", value: null },
  ],
  levels: [
    { level: "data", status: "partial", basis: "记录了处理摘要与导入指纹", available_materials: ["处理后数据摘要（dataset_digest）"], missing_materials: ["原始行情数据文件（未归档）"], impact: "无法重建回测输入并重跑" },
    { level: "code", status: "partial", basis: "记录了策略标识与参数", available_materials: ["策略标识（strategy_id）"], missing_materials: ["策略源代码 / 版本快照 / Git commit"], impact: "存在代码变更导致结果不可比的风险" },
    { level: "environment", status: "partial", basis: "记录了引擎版本", available_materials: ["回测引擎版本"], missing_materials: ["Python 版本", "依赖包版本", "操作系统"], impact: "无法重建当时的运行环境" },
  ],
  missing_info: ["原始行情数据文件", "策略源代码 / 版本快照", "运行环境（Python / 依赖 / 操作系统）"],
  conclusion:
    "复现能力受限：结果可读取、部分溯源信息可查，但当前材料不足以保证数据级 / 代码级 / 环境级的完整复现。完整性审计通过仅表示结果文档与摘要一致，不代表可复现。",
};

const PASSED_INTEGRITY = {
  run_id: SAMPLE_META.run_id,
  status: "passed",
  checks: [
    { name: "document_present", status: "passed", message: "结果文档存在" },
    { name: "result_digest_consistent", status: "passed", message: "重新计算的摘要与已保存摘要一致" },
    { name: "digests_match_across_tables", status: "passed", message: "元数据与文档摘要一致" },
    { name: "required_fields_present", status: "passed", message: "结果文档必需字段齐全" },
  ],
  metadata_result_digest: "sha256:ccc",
  document_result_digest: "sha256:ccc",
  recomputed_digest: "sha256:ccc",
  audited_at: "2026-01-01T00:00:01.000000Z",
};

async function waitForLoadingDone() {
  await waitFor(() => expect(screen.queryByTestId("detail-loading")).toBeNull());
}

async function renderReady() {
  detailMock.mockResolvedValueOnce({
    run_id: SAMPLE_META.run_id,
    metadata: SAMPLE_META,
    result: makeResult(),
  });
  render(<HistoryDetail runId={SAMPLE_META.run_id} onBack={() => {}} />);
  await waitForLoadingDone();
}

describe("HistoryDetail", () => {
  it("加载中状态显示", async () => {
    let resolve!: (v: unknown) => void;
    const pending = new Promise((r) => { resolve = r; });
    detailMock.mockReturnValue(pending);
    render(<HistoryDetail runId="abc123" onBack={() => {}} />);
    expect(screen.getByTestId("detail-loading")).toBeInTheDocument();
    resolve({ run_id: "abc", metadata: SAMPLE_META, result: makeResult() });
    await waitForLoadingDone();
  });

  it("正常详情显示 metadata + result 复用 ResultView", async () => {
    detailMock.mockResolvedValueOnce({ run_id: SAMPLE_META.run_id, metadata: SAMPLE_META, result: makeResult() });
    render(<HistoryDetail runId={SAMPLE_META.run_id} onBack={() => {}} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("detail-meta")).toBeInTheDocument();
    expect(screen.getByTestId("detail-result")).toBeInTheDocument();
    // ResultView 概要区：使用 config.symbol
    expect(screen.getByTestId("result-view")).toBeInTheDocument();
    expect(screen.getByText("000001.SZ")).toBeInTheDocument();
  });

  it("返回按钮调用 onBack", async () => {
    detailMock.mockResolvedValueOnce({ run_id: SAMPLE_META.run_id, metadata: SAMPLE_META, result: makeResult() });
    const onBack = vi.fn();
    render(<HistoryDetail runId={SAMPLE_META.run_id} onBack={onBack} />);
    await waitForLoadingDone();
    await userEvent.click(screen.getByTestId("detail-back"));
    expect(onBack).toHaveBeenCalled();
  });

  it("404 run_not_found → 明确不存在状态", async () => {
    detailMock.mockRejectedValueOnce(new ApiError("http_error", "不存在", { status: 404, code: "run_not_found" }));
    render(<HistoryDetail runId="missing" onBack={() => {}} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("detail-not-found")).toBeInTheDocument();
  });

  it("500 run_integrity_error → 完整性错误状态", async () => {
    detailMock.mockRejectedValueOnce(new ApiError("http_error", "文档缺失", { status: 500, code: "run_integrity_error" }));
    render(<HistoryDetail runId="broken" onBack={() => {}} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("detail-integrity")).toBeInTheDocument();
  });

  it("500 run_result_corrupted → 结果文档损坏状态（区别于文档缺失）", async () => {
    detailMock.mockRejectedValueOnce(
      new ApiError("http_error", "结果文档已损坏", { status: 500, code: "run_result_corrupted" }),
    );
    render(<HistoryDetail runId="corrupt" onBack={() => {}} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("detail-corrupted")).toBeInTheDocument();
    expect(screen.getByTestId("detail-corrupted")).toHaveTextContent("历史结果文档已损坏");
    // 不应误显示为「文档缺失」状态
    expect(screen.queryByTestId("detail-integrity")).toBeNull();
  });

  it("其他 5xx → 通用服务端错误", async () => {
    detailMock.mockRejectedValueOnce(new ApiError("http_error", "数据库连接失败", { status: 500 }));
    render(<HistoryDetail runId="x" onBack={() => {}} />);
    await waitForLoadingDone();
    expect(screen.getByTestId("detail-error")).toBeInTheDocument();
  });
});

describe("HistoryDetail 结果完整性审计（Step 6.7）", () => {
  it("审计通过 → 显示通过状态，且明确不代表策略有效/可复现", async () => {
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("integrity-passed")).toBeInTheDocument());
    const passed = screen.getByTestId("integrity-passed");
    expect(passed).toHaveTextContent("完整性检查通过");
    expect(passed).toHaveTextContent("不代表策略有效或回测可复现");
  });

  it("审计失败 → 显示完整性问题并列出失败项", async () => {
    integrityMock.mockResolvedValue({
      ...PASSED_INTEGRITY,
      status: "failed",
      checks: [
        { name: "result_digest_consistent", status: "failed", message: "重新计算的摘要与已保存摘要不一致" },
      ],
    });
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("integrity-failed")).toBeInTheDocument());
    expect(screen.getByTestId("integrity-failed")).toHaveTextContent("检测到完整性问题");
    expect(screen.getByTestId("integrity-failed")).toHaveTextContent("result_digest_consistent");
  });

  it("审计信息不足 → 明确区分「信息不足」与「数据损坏」", async () => {
    integrityMock.mockResolvedValue({
      ...PASSED_INTEGRITY,
      status: "incomplete",
      checks: [
        { name: "digests_match_across_tables", status: "incomplete", message: "缺少元数据或文档摘要，无法比较" },
      ],
    });
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("integrity-incomplete")).toBeInTheDocument());
    const block = screen.getByTestId("integrity-incomplete");
    expect(block).toHaveTextContent("信息不足，无法完成全部检查");
    expect(block).toHaveTextContent("缺少必要信息不等于数据损坏");
  });

  it("审计不可用 → 显示不可用状态，且不影响详情展示", async () => {
    integrityMock.mockRejectedValue(new ApiError("backend_unavailable", "无法连接"));
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("integrity-unavailable")).toBeInTheDocument());
    // 详情主体仍然完整展示
    expect(screen.getByTestId("detail-meta")).toBeInTheDocument();
    expect(screen.getByTestId("result-view")).toBeInTheDocument();
  });

  it("审计不阻塞详情：详情先渲染，完整性状态独立更新", async () => {
    await renderReady();
    // 详情立即可见
    expect(screen.getByTestId("detail-meta")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("integrity-passed")).toBeInTheDocument());
    expect(screen.getByTestId("integrity-card")).toBeInTheDocument();
  });
});

describe("HistoryDetail 只读复现信息清单（Step 6.8）", () => {
  it("正常展示：数据 / 代码 / 环境三级与结论", async () => {
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("repro-card")).toBeInTheDocument());

    // 三级复现能力卡片
    expect(screen.getByTestId("repro-level-data")).toBeInTheDocument();
    expect(screen.getByTestId("repro-level-code")).toBeInTheDocument();
    expect(screen.getByTestId("repro-level-environment")).toBeInTheDocument();
    // 均为「部分具备」，不是「具备」
    expect(screen.getByTestId("repro-level-status-data")).toHaveTextContent("部分具备");
    expect(screen.getByTestId("repro-level-status-code")).toHaveTextContent("部分具备");
    expect(screen.getByTestId("repro-level-status-environment")).toHaveTextContent("部分具备");
    expect(screen.queryByText("具备")).toBeNull();

    // 关键缺失信息
    expect(screen.getByTestId("repro-missing")).toHaveTextContent("原始行情数据文件");
    // 结论强调受限，不宣称完全可复现
    expect(screen.getByTestId("repro-conclusion")).toHaveTextContent("复现能力受限");
  });

  it("原始数据指纹与处理后数据摘要被区分展示", async () => {
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("repro-item-source_sha256")).toBeInTheDocument());
    const source = screen.getByTestId("repro-item-source_sha256");
    const dataset = screen.getByTestId("repro-item-dataset_digest");
    expect(source).toHaveTextContent("原始数据指纹");
    expect(dataset).toHaveTextContent("处理后数据摘要");
    expect(source).toHaveTextContent("无法验证");
    expect(dataset).toHaveTextContent("无法验证");
  });

  it("未归档 / 未记录项明确标记（不用默认值冒充）", async () => {
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("repro-item-raw_market_file")).toBeInTheDocument());
    expect(screen.getByTestId("repro-item-raw_market_file")).toHaveTextContent("未归档");
    expect(screen.getByTestId("repro-item-strategy_code_version")).toHaveTextContent("未记录");
    expect(screen.getByTestId("repro-item-runtime_environment")).toHaveTextContent("未记录");
  });

  it("完整性通过但复现能力不足时，界面不宣称完全可复现", async () => {
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("integrity-passed")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId("repro-conclusion")).toBeInTheDocument());
    // 完整性通过
    expect(screen.getByTestId("integrity-passed")).toHaveTextContent("完整性检查通过");
    // 复现能力仍受限
    expect(screen.getByTestId("repro-level-status-code")).toHaveTextContent("部分具备");
    expect(screen.queryByText("完全可复现")).toBeNull();
  });

  it("清单加载失败 → 显示不可用，且详情与完整性仍可用", async () => {
    reproMock.mockRejectedValue(new ApiError("http_error", "数据库故障", { status: 500 }));
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("repro-unavailable")).toBeInTheDocument());
    // 详情主体仍完整
    expect(screen.getByTestId("detail-meta")).toBeInTheDocument();
    expect(screen.getByTestId("result-view")).toBeInTheDocument();
    // 完整性状态独立，仍正常显示
    await waitFor(() => expect(screen.getByTestId("integrity-passed")).toBeInTheDocument());
  });

  it("结果文档损坏 → 清单显示文档状态提示，仍不崩溃", async () => {
    reproMock.mockResolvedValue({
      ...SAMPLE_REPRO,
      result_document_state: "corrupted",
    });
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("repro-doc-state")).toBeInTheDocument());
    expect(screen.getByTestId("repro-doc-state")).toHaveTextContent("结果文档已损坏");
    // 页面仍可用
    expect(screen.getByTestId("detail-meta")).toBeInTheDocument();
  });

  it("旧记录缺少字段：清单仍可用并标记缺失", async () => {
    reproMock.mockResolvedValue({
      ...SAMPLE_REPRO,
      items: [
        { key: "source_sha256", label: "原始数据指纹（source_sha256）", category: "data_source", status: "missing", detail: "未记录原始数据指纹", value: null },
        { key: "engine_version", label: "回测引擎版本", category: "engine", status: "missing", detail: "未记录引擎版本", value: null },
      ],
      levels: [
        { level: "data", status: "none", basis: "未记录任何数据摘要", available_materials: [], missing_materials: ["原始行情数据文件（未归档）"], impact: "无法识别输入数据" },
      ],
    });
    await renderReady();
    await waitFor(() => expect(screen.getByTestId("repro-item-source_sha256")).toBeInTheDocument());
    expect(screen.getByTestId("repro-item-source_sha256")).toHaveTextContent("缺失");
    expect(screen.getByTestId("repro-level-status-data")).toHaveTextContent("不具备");
    expect(screen.getByTestId("detail-meta")).toBeInTheDocument();
  });
});

