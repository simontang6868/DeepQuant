/**
 * 统一 API 客户端。
 *
 * 设计要点：
 * - 所有请求集中于此，组件不得直接散落 fetch；
 * - 基地址由 VITE_API_BASE_URL 配置；缺省走 Vite 开发代理（/api → 后端），
 *   避免本地开发配置 CORS；
 * - 错误统一抛出 ApiError，kind 区分：网络不可达 / HTTP 错误 / 结构不符 / 超时；
 * - 对后端统一错误结构 { error: { code, message, details } } 安全解析：
 *   字段存在且类型正确才采用，否则退化为通用 HTTP 错误信息；
 * - 不向后端发送任何文件路径或代码；不吞掉非 2xx 响应。
 */

import type {
  BacktestCompareRequestPayload,
  BacktestCompareResponse,
  BacktestIntegrityResponse,
  BacktestReproducibilityResponse,
  BacktestRequestPayload,
  BacktestResponse,
  BacktestRunDetailResponse,
  BacktestRunListResponse,
  DatasetListResponse,
  StrategyListResponse,
} from "./types";

export type ApiErrorKind =
  /** 网络连接失败 / 后端未启动 */
  | "backend_unavailable"
  /** HTTP 错误响应（4xx/5xx，含后端统一 error 结构） */
  | "http_error"
  /** 返回结构不符合预期 */
  | "invalid_response"
  /** 请求超时（本地 AbortController） */
  | "timeout";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly code?: string;
  readonly details?: unknown;

  constructor(
    kind: ApiErrorKind,
    message: string,
    options: { status?: number; code?: string; details?: unknown } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
  }
}

/** 把任意抛出的错误归一化为 ApiError（保守归类为连接失败）。 */
export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError(
    "backend_unavailable",
    err instanceof Error ? err.message : "未知错误",
  );
}

/**
 * API 基地址：
 * - 未设置 VITE_API_BASE_URL → "/api"（走 Vite 开发代理，见 vite.config.ts）；
 * - 设置后直连，例如 "http://127.0.0.1:8123"。
 */
const rawBase = (import.meta.env.VITE_API_BASE_URL ?? "").trim();
export const API_BASE_URL = (rawBase === "" ? "/api" : rawBase).replace(/\/+$/, "");
/** 是否处于代理模式（用于界面提示后端地址的真实含义） */
export const IS_PROXY_MODE = API_BASE_URL === "/api";

const HEALTH_TIMEOUT_MS = 8_000;
const LIST_TIMEOUT_MS = 15_000;
// 同步回测接口：本地 2500 个交易日约 0.3 秒，留足余量；超时只表示“结果未返回”，
// 不代表后端已取消执行。
const BACKTEST_TIMEOUT_MS = 120_000;

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  timeoutMs?: number;
  timeoutMessage?: string;
}

async function request<T>(
  path: string,
  validate: (data: unknown) => T,
  options: RequestOptions = {},
): Promise<T> {
  const {
    method = "GET",
    body,
    timeoutMs = LIST_TIMEOUT_MS,
    timeoutMessage = `请求超时（超过 ${Math.round(timeoutMs / 1000)} 秒未收到响应）`,
  } = options;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (err) {
    // 鸭子类型检测 AbortError（DOMException 在部分运行时不继承 Error）
    const errName = (err as { name?: string } | null)?.name;
    if (errName === "AbortError") {
      throw new ApiError("timeout", timeoutMessage);
    }
    throw new ApiError(
      "backend_unavailable",
      "无法连接后端服务，请确认后端已启动并监听正确端口。",
    );
  } finally {
    clearTimeout(timer);
  }

  // 统一读文本再解析：既兼容错误响应体，也能安全处理空响应。
  const text = await res.text().catch(() => "");
  let data: unknown;
  if (text.trim() !== "") {
    try {
      data = JSON.parse(text);
    } catch {
      data = undefined; // 交由下方结构校验处理
    }
  }

  if (!res.ok) {
    const errBody = (
      data as { error?: { code?: unknown; message?: unknown; details?: unknown } } | undefined
    )?.error;
    if (errBody !== undefined && typeof errBody === "object" && errBody !== null) {
      const code = typeof errBody.code === "string" ? errBody.code : undefined;
      const message =
        typeof errBody.message === "string" && errBody.message !== ""
          ? errBody.message
          : `后端返回 HTTP ${res.status}`;
      throw new ApiError("http_error", message, {
        status: res.status,
        code,
        details: errBody.details,
      });
    }
    throw new ApiError("http_error", `后端返回 HTTP ${res.status}（响应不是预期的错误结构）`, {
      status: res.status,
    });
  }

  if (data === undefined) {
    throw new ApiError("invalid_response", "后端返回内容不是合法 JSON");
  }
  return validate(data);
}

function expectObject(data: unknown, what: string): Record<string, unknown> {
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    throw new ApiError("invalid_response", `${what}应为对象`);
  }
  return data as Record<string, unknown>;
}

function expectArray(value: unknown, what: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new ApiError("invalid_response", `${what}应为数组`);
  }
  return value;
}

// --------------------------------------------------------------------------- //
// 四个接口
// --------------------------------------------------------------------------- //

export async function getHealth(): Promise<{ status: string }> {
  return request(
    "/health",
    (data) => {
      const obj = expectObject(data, "/health 响应");
      if (typeof obj.status !== "string") {
        throw new ApiError("invalid_response", "/health 响应缺少 status 字段");
      }
      return { status: obj.status };
    },
    { timeoutMs: HEALTH_TIMEOUT_MS, timeoutMessage: "健康检查超时" },
  );
}

export async function getDatasets(): Promise<DatasetListResponse> {
  return request("/datasets", (data) => {
    const obj = expectObject(data, "/datasets 响应");
    expectArray(obj.datasets, "/datasets 响应的 datasets 字段");
    expectArray(obj.invalid_datasets, "/datasets 响应的 invalid_datasets 字段");
    return obj as unknown as DatasetListResponse;
  });
}

export async function getStrategies(): Promise<StrategyListResponse> {
  return request("/strategies", (data) => {
    const obj = expectObject(data, "/strategies 响应");
    expectArray(obj.strategies, "/strategies 响应的 strategies 字段");
    return obj as unknown as StrategyListResponse;
  });
}

export async function postBacktest(payload: BacktestRequestPayload): Promise<BacktestResponse> {
  return request(
    "/backtests",
    (data) => {
      const obj = expectObject(data, "/backtests 响应");
      const result = obj.result;
      if (typeof result !== "object" || result === null || Array.isArray(result)) {
        throw new ApiError("invalid_response", "/backtests 响应缺少 result 对象");
      }
      return obj as unknown as BacktestResponse;
    },
    {
      method: "POST",
      body: payload,
      timeoutMs: BACKTEST_TIMEOUT_MS,
      timeoutMessage:
        "回测请求超时：同步回测可能仍在后端执行，本次结果不会自动返回；请稍后重新提交，不要短时间反复提交。",
    },
  );
}

// --------------------------------------------------------------------------- //
// Step 6.3：历史回测列表 & 详情
// --------------------------------------------------------------------------- //

export interface ListBacktestRunsParams {
  limit?: number;
  offset?: number;
  symbol?: string | null;
  strategy_id?: string | null;
  status?: string | null;
}

/** 构建 query 字符串，自动跳过 null/undefined/空字符串值。 */
function buildQuery(params: Record<string, unknown>): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (entries.length === 0) return "";
  return "?" + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join("&");
}

export async function getBacktestRuns(
  params: ListBacktestRunsParams = {},
): Promise<BacktestRunListResponse> {
  const query = buildQuery({
    limit: params.limit,
    offset: params.offset,
    symbol: params.symbol,
    strategy_id: params.strategy_id,
    status: params.status,
  });
  return request(`/backtests${query}`, (data) => {
    const obj = expectObject(data, "/backtests 列表响应");
    expectArray(obj.items, "/backtests 列表响应的 items 字段");
    if (typeof obj.total !== "number") {
      throw new ApiError("invalid_response", "/backtests 列表响应缺少 total 字段");
    }
    return obj as unknown as BacktestRunListResponse;
  });
}

export async function getBacktestRunDetail(runId: string): Promise<BacktestRunDetailResponse> {
  return request(`/backtests/${encodeURIComponent(runId)}`, (data) => {
    const obj = expectObject(data, `/backtests/${runId} 详情响应`);
    expectObject(obj.metadata, `/backtests/${runId} 详情响应缺少 metadata 对象`);
    expectObject(obj.result, `/backtests/${runId} 详情响应缺少 result 对象`);
    return obj as unknown as BacktestRunDetailResponse;
  });
}

// --------------------------------------------------------------------------- //
// Step 6.7：只读完整性审计
// --------------------------------------------------------------------------- //

export async function getBacktestRunIntegrity(
  runId: string,
): Promise<BacktestIntegrityResponse> {
  return request(`/backtests/${encodeURIComponent(runId)}/integrity`, (data) => {
    const obj = expectObject(data, `/backtests/${runId}/integrity 响应`);
    expectArray(obj.checks, `/backtests/${runId}/integrity 响应的 checks 字段`);
    if (typeof obj.status !== "string") {
      throw new ApiError("invalid_response", `/backtests/${runId}/integrity 响应缺少 status 字段`);
    }
    return obj as unknown as BacktestIntegrityResponse;
  });
}

// --------------------------------------------------------------------------- //
// Step 6.8：只读复现信息清单
// --------------------------------------------------------------------------- //

export async function getBacktestRunReproducibility(
  runId: string,
): Promise<BacktestReproducibilityResponse> {
  return request(`/backtests/${encodeURIComponent(runId)}/reproducibility`, (data) => {
    const obj = expectObject(data, `/backtests/${runId}/reproducibility 响应`);
    expectArray(obj.items, `/backtests/${runId}/reproducibility 响应的 items 字段`);
    expectArray(obj.levels, `/backtests/${runId}/reproducibility 响应的 levels 字段`);
    if (typeof obj.result_document_state !== "string") {
      throw new ApiError(
        "invalid_response",
        `/backtests/${runId}/reproducibility 响应缺少 result_document_state 字段`,
      );
    }
    return obj as unknown as BacktestReproducibilityResponse;
  });
}

// --------------------------------------------------------------------------- //
// Step 6.5：历史回测比较
// --------------------------------------------------------------------------- //

export async function postBacktestCompare(
  payload: BacktestCompareRequestPayload,
): Promise<BacktestCompareResponse> {
  return request(
    "/backtests/compare",
    (data) => {
      const obj = expectObject(data, "/backtests/compare 响应");
      expectArray(obj.items, "/backtests/compare 响应的 items 字段");
      return obj as unknown as BacktestCompareResponse;
    },
    {
      method: "POST",
      body: payload,
    },
  );
}

// --------------------------------------------------------------------------- //
// 错误文案
// --------------------------------------------------------------------------- //

/** 把 ApiError 转成面向用户的中文说明（不展示堆栈与服务器内部路径）。 */
export function describeApiError(err: ApiError): string {
  switch (err.kind) {
    case "backend_unavailable":
      return `无法连接后端服务${err.message ? `：${err.message}` : "。"}`;
    case "timeout":
      return err.message;
    case "invalid_response":
      return `后端返回了不符合预期的数据结构${err.message ? `：${err.message}` : ""}`;
    case "http_error":
      return err.code
        ? `后端返回错误 ${err.code}：${err.message}`
        : `后端返回 HTTP ${err.status ?? "?"}：${err.message}`;
  }
}
