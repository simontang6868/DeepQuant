/**
 * 回测表单校验与请求体构造（纯函数）。
 *
 * 原则：
 * - 前端校验只用于即时提示、阻止明显无效的请求；后端校验仍是最终依据；
 * - 参数留空表示使用后端 schema 默认值；填了才校验；
 * - 费率/滑点/市场规则不在前端暴露配置项（使用后端模拟占位默认值，
 *   fee_source = simulation_placeholder），不得伪造可用的设置项。
 */

import type { BacktestRequestPayload, StrategyInfo } from "../api/types";

export interface BacktestFormValues {
  datasetSymbol: string;
  strategyId: string;
  /** 参数原始输入（字符串）；缺省/空串 = 使用后端默认值 */
  paramValues: Record<string, string>;
  /** YYYY-MM-DD 或空串 */
  startDate: string;
  endDate: string;
  initialCash: string;
}

export type FieldErrors = Record<string, string>;

export const INITIAL_FORM_VALUES: BacktestFormValues = {
  datasetSymbol: "",
  strategyId: "",
  paramValues: {},
  startDate: "",
  endDate: "",
  initialCash: "100000",
};

export type ValidateOutcome =
  | { ok: true; payload: BacktestRequestPayload }
  | { ok: false; errors: FieldErrors };

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function parseIntStrict(raw: string): number | null {
  if (!/^-?\d+$/.test(raw)) return null;
  const n = Number(raw);
  return Number.isSafeInteger(n) ? n : null;
}

function parseFloatStrict(raw: string): number | null {
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** ma_cross 的跨参数约束（快线窗口必须短于慢线窗口），用于即时提示。 */
function crossCheckMaWindows(
  params: Record<string, number>,
  errors: FieldErrors,
): void {
  const fast = params.fast_window;
  const slow = params.slow_window;
  if (fast !== undefined && slow !== undefined && slow <= fast) {
    errors["param.slow_window"] =
      `slow_window 必须大于 fast_window（当前 fast=${fast}，slow=${slow}；后端要求慢线窗口必须长于快线窗口）`;
  }
}

export function validateAndBuildPayload(
  values: BacktestFormValues,
  strategy: StrategyInfo | null,
): ValidateOutcome {
  const errors: FieldErrors = {};

  // --- 数据集 ---
  if (values.datasetSymbol.trim() === "") {
    errors.dataset = "请先选择数据集";
  }

  // --- 策略 ---
  if (values.strategyId.trim() === "" || strategy === null) {
    errors.strategy = "请先选择策略";
  }

  // --- 初始资金 ---
  let initialCash: number | null = null;
  const cashRaw = values.initialCash.trim();
  if (cashRaw === "") {
    errors.initial_cash = "初始资金为必填项";
  } else {
    const parsed = parseFloatStrict(cashRaw);
    if (parsed === null) {
      errors.initial_cash = "初始资金必须是数字";
    } else if (parsed <= 0) {
      errors.initial_cash = "初始资金必须大于 0";
    } else {
      initialCash = parsed;
    }
  }

  // --- 日期区间 ---
  const start = values.startDate.trim();
  const end = values.endDate.trim();
  if (start !== "" && !DATE_RE.test(start)) {
    errors.start_date = "开始日期格式应为 YYYY-MM-DD";
  }
  if (end !== "" && !DATE_RE.test(end)) {
    errors.end_date = "结束日期格式应为 YYYY-MM-DD";
  }
  if (start !== "" && end !== "" && DATE_RE.test(start) && DATE_RE.test(end) && start > end) {
    errors.end_date = "开始日期不能晚于结束日期";
  }

  // --- 策略参数（仅对用户显式填写的值做校验；留空用后端默认值） ---
  const parsedParams: Record<string, number | string | boolean> = {};
  if (strategy !== null) {
    const numericParams: Record<string, number> = {};
    for (const [name, info] of Object.entries(strategy.params)) {
      const raw = (values.paramValues[name] ?? "").trim();
      if (raw === "") continue; // 使用后端默认值
      const type = String(info.type ?? "").toLowerCase();
      if (type.includes("int")) {
        const n = parseIntStrict(raw);
        if (n === null) {
          errors[`param.${name}`] = `${name} 必须是整数`;
          continue;
        }
        if (info.minimum !== null && typeof info.minimum === "number" && n < info.minimum) {
          errors[`param.${name}`] = `${name} 不能小于最小值 ${info.minimum}`;
          continue;
        }
        parsedParams[name] = n;
        numericParams[name] = n;
      } else if (type.includes("float") || type.includes("number")) {
        const n = parseFloatStrict(raw);
        if (n === null) {
          errors[`param.${name}`] = `${name} 必须是数字`;
          continue;
        }
        if (info.minimum !== null && typeof info.minimum === "number" && n < info.minimum) {
          errors[`param.${name}`] = `${name} 不能小于最小值 ${info.minimum}`;
          continue;
        }
        parsedParams[name] = n;
        numericParams[name] = n;
      } else if (type.includes("bool")) {
        if (raw === "true" || raw === "false") {
          parsedParams[name] = raw === "true";
        } else {
          errors[`param.${name}`] = `${name} 必须是 true 或 false`;
        }
      } else {
        parsedParams[name] = raw;
      }
    }
    crossCheckMaWindows(numericParams, errors);
  }

  if (Object.keys(errors).length > 0) {
    return { ok: false, errors };
  }

  const payload: BacktestRequestPayload = {
    dataset_symbol: values.datasetSymbol.trim(),
    strategy_id: values.strategyId.trim(),
    strategy_params: parsedParams,
    initial_cash: initialCash as number,
  };
  if (start !== "") payload.start_date = start;
  if (end !== "") payload.end_date = end;

  return { ok: true, payload };
}
