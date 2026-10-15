/**
 * API 客户端测试：成功响应 / 网络错误 / 后端统一错误结构 / 非预期结构 /
 * HTTP 错误但非预期结构 / 超时。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getDatasets, getHealth, getStrategies, postBacktest } from "./client";
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
