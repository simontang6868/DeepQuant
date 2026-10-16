/**
 * API 客户端测试：成功响应 / 网络错误 / 后端统一错误结构 / 非预期结构 /
 * HTTP 错误但非预期结构 / 超时。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getBacktestRunDetail, getBacktestRunIntegrity, getBacktestRuns, getDatasets, getHealth, getStrategies, postBacktest } from "./client";
import type { BacktestRequestPayload } from "./types";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("getHealth", () => {
  it("成功响应返回 status 字段", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "ok" }));
    await expect(getHealth()).resolves.toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/health"),
      expect.anything(),
    );
  });

  it("返回结构不符合预期时抛 invalid_response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { hello: "world" }));
    const err = await getHealth().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).kind).toBe("invalid_response");
  });
});

describe("getDatasets", () => {
  it("返回真实 API 数据", async () => {
    const body = {
      datasets: [
        {
          symbol: "TEST01",
          rows: 60,
          start_date: "2020-01-01",
          end_date: "2020-02-29",
          source_name: "TEST01.csv",
          source_sha256: "abc",
          ingested_at: "2020-03-01T00:00:00",
        },
      ],
      invalid_datasets: [{ metadata_file: "bad.json", symbol: null, reason: "缺少字段" }],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(200, body));
    await expect(getDatasets()).resolves.toEqual(body);
  });

  it("缺少 datasets 数组时抛 invalid_response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { foo: [] }));
    const err = await getDatasets().catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("invalid_response");
  });
});

describe("getStrategies", () => {
  it("返回策略列表", async () => {
    const body = {
      strategies: [
        {
          strategy_id: "ma_cross",
          display_name: "双均线交叉",
          version: "1.0.0",
          description: "d",
          params: {},
        },
      ],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(200, body));
    await expect(getStrategies()).resolves.toEqual(body);
  });
});

describe("postBacktest", () => {
  const payload: BacktestRequestPayload = {
    dataset_symbol: "TEST01",
    strategy_id: "ma_cross",
    strategy_params: { fast_window: 5, slow_window: 20 },
    initial_cash: 100000,
  };

  it("以 POST + JSON 体调用 /backtests 并返回结果", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { api: { name: "deepquant-api", version: "0.1.0" }, result: {} }),
    );
    await postBacktest(payload);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/backtests");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });

  it("result 缺失时抛 invalid_response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { api: {} }));
    const err = await postBacktest(payload).catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("invalid_response");
  });
});

describe("错误处理", () => {
  it("fetch 拒绝（后端未启动）→ backend_unavailable", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const err = await getHealth().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).kind).toBe("backend_unavailable");
  });

  it("后端统一错误结构 → 提取 code 与 message", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(404, {
        error: { code: "dataset_not_found", message: "数据集不存在", details: null },
      }),
    );
    const err = await getDatasets().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.kind).toBe("http_error");
    expect(apiErr.status).toBe(404);
    expect(apiErr.code).toBe("dataset_not_found");
    expect(apiErr.message).toBe("数据集不存在");
  });

  it("422 校验错误结构同样被解析", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(422, {
        error: { code: "invalid_request", message: "请求字段不合法", details: [{ loc: ["body", "initial_cash"], msg: "must be > 0", type: "greater_than" }] },
      }),
    );
    const err = await getStrategies().catch((e: unknown) => e);
    expect((err as ApiError).code).toBe("invalid_request");
  });

  it("HTTP 错误但响应不是预期结构 → 通用 http_error", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(500, "Internal Server Error"));
    const err = await getHealth().catch((e: unknown) => e);
    const apiErr = err as ApiError;
    expect(apiErr.kind).toBe("http_error");
    expect(apiErr.status).toBe(500);
    expect(apiErr.code).toBeUndefined();
  });

  it("200 但响应不是合法 JSON → invalid_response", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: async () => "<html>not json</html>",
    } as unknown as Response);
    const err = await getHealth().catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("invalid_response");
  });

  it("请求超时 → timeout", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementationOnce(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          (init.signal as AbortSignal).addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    const promise = getHealth().catch((e: unknown) => e); // 立即挂上处理，避免未处理拒绝
    await vi.advanceTimersByTimeAsync(10_000);
    const err = await promise;
    vi.useRealTimers();
    expect((err as ApiError).kind).toBe("timeout");
  });
});

// ---------------------------------------------------------------------------
// Step 6.4：历史列表 & 详情 API 客户端测试
// ---------------------------------------------------------------------------

const SAMPLE_ITEM = {
  run_id: "abc123",
  created_at: "2026-01-01T00:00:00.000000Z",
  status: "succeeded",
  symbol: "000001.SZ",
  strategy_id: "ma_cross",
  strategy_version: "0.1.0",
  strategy_params: { fast_window: 5, slow_window: 20 },
  dataset_symbol: "TEST01",
  start_date: "2020-01-01",
  end_date: "2020-02-09T00:00:00",
  initial_cash: 100000,
  source_sha256: "sha256:a",
  dataset_digest: "sha256:b",
  result_digest: "sha256:c",
  is_complete: true,
  duration_ms: 1234,
};

const SAMPLE_LIST_RESPONSE = {
  items: [SAMPLE_ITEM],
  total: 1,
  limit: 20,
  offset: 0,
};

const SAMPLE_DETAIL_RESPONSE = {
  run_id: "abc123",
  metadata: SAMPLE_ITEM,
  result: {
    schema_version: "1",
    engine_version: "1",
    config: { symbol: "000001.SZ" },
    period: { start: null, end: null },
    strategy: null,
    dataset: null,
    orders: [],
    fills: [],
    account_states: [],
    data_quality_issues: [],
    limitations: [],
    is_complete: true,
    incomplete_reasons: [],
    performance: null,
    reproducibility: { inputs_identifiable: true, process_reproducible: true, missing: [], notes: [] },
  },
};

describe("getBacktestRuns", () => {
  it("默认参数构造正确 URL", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SAMPLE_LIST_RESPONSE));
    await getBacktestRuns();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/backtests",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("带筛选和分页参数", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SAMPLE_LIST_RESPONSE));
    await getBacktestRuns({ limit: 50, offset: 40, symbol: "000001.SZ", status: "succeeded" });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("limit=50");
    expect(url).toContain("offset=40");
    expect(url).toContain("symbol=000001.SZ");
    expect(url).toContain("status=succeeded");
    expect(url).toContain("?");
  });

  it("空字符串筛选不发送无意义参数", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SAMPLE_LIST_RESPONSE));
    await getBacktestRuns({ symbol: "", limit: 20 });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).not.toContain("symbol=");
  });

  it("空响应列表 → invalid_response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { items: "not-array", total: 0 }));
    const err = await getBacktestRuns().catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("invalid_response");
  });
});

describe("getBacktestRunDetail", () => {
  it("正确请求 run_id", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SAMPLE_DETAIL_RESPONSE));
    const runId = "xyz-run-id-abc";
    await getBacktestRunDetail(runId);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/backtests/xyz-run-id-abc",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("对 run_id 做 URL 编码", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SAMPLE_DETAIL_RESPONSE));
    await getBacktestRunDetail("has/slash&x");
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("has%2Fslash%26x");
  });

  it("HTTP 404 → http_error with code", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(404, { error: { code: "run_not_found", message: "不存在" } }),
    );
    const err = await getBacktestRunDetail("missing").catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).code).toBe("run_not_found");
  });

  it("HTTP 500 完整性错误 → http_error", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(500, { error: { code: "run_integrity_error", message: "不完整" } }),
    );
    const err = await getBacktestRunDetail("broken").catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).code).toBe("run_integrity_error");
  });

  it("网络失败 → backend_unavailable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    const err = await getBacktestRunDetail("abc").catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("backend_unavailable");
  });
});

// ---------------------------------------------------------------------------
// Step 6.7：只读完整性审计 API 客户端测试
// ---------------------------------------------------------------------------

const SAMPLE_INTEGRITY_RESPONSE = {
  run_id: "abc123",
  status: "passed",
  checks: [{ name: "document_present", status: "passed", message: "结果文档存在" }],
  metadata_result_digest: "sha256:c",
  document_result_digest: "sha256:c",
  recomputed_digest: "sha256:c",
  audited_at: "2026-01-01T00:00:01.000000Z",
};

describe("getBacktestRunIntegrity", () => {
  it("正确请求 run_id 的 integrity 路径并对 run_id 编码", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, SAMPLE_INTEGRITY_RESPONSE));
    await getBacktestRunIntegrity("has/slash&x");
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("/backtests/has%2Fslash%26x/integrity");
    expect(url).toContain("integrity");
  });

  it("缺少 status 字段 → invalid_response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { checks: [] }));
    const err = await getBacktestRunIntegrity("abc").catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("invalid_response");
  });

  it("checks 非数组 → invalid_response", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "passed", checks: "no" }));
    const err = await getBacktestRunIntegrity("abc").catch((e: unknown) => e);
    expect((err as ApiError).kind).toBe("invalid_response");
  });

  it("404 → http_error with run_not_found", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(404, { error: { code: "run_not_found", message: "不存在" } }),
    );
    const err = await getBacktestRunIntegrity("missing").catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).code).toBe("run_not_found");
  });
});
