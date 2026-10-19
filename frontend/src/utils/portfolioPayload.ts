/**
 * 组合回测表单：值 → 请求体 + 字段校验（纯函数，便于单测）。
 *
 * 为什么把校验放在这里而不是组件里：
 * 后端一定会再校验一遍（`api/schemas.py`），但两边规则**不能靠人记住**。
 * 前端这层的目的是「不发出注定 422 的请求」，并且把原因说在人话上；
 * 真正的守门仍在后端。因此校验失败时的文案要与后端一致（同一个口径），
 * 特别是 `min_assets > 标的数` 这条——放过去会得到「全程空仓但看起来正常」。
 */

import type { PortfolioBacktestRequestPayload } from "../api/types";

export type PortfolioSelection = "momentum_top" | "equal_weight_all";
export type PortfolioAllocation = "weights" | "lots";

export interface PortfolioFormValues {
  symbols: string[];
  initialCash: string;
  selection: PortfolioSelection;
  /** 百分数（20 表示 top 20%）；提交时除以 100 */
  topFractionPercent: string;
  minAssets: string;
  allocation: PortfolioAllocation;
  startDate: string;
  endDate: string;
}

export type PortfolioFieldErrors = Record<string, string>;

/** 与后端口径一致：预登记 §5 的 min_assets、§3~§4 的 60·5、初始资金 100 万 */
export const PORTFOLIO_DEFAULTS: PortfolioFormValues = {
  symbols: [],
  initialCash: "1000000",
  selection: "momentum_top",
  topFractionPercent: "20",
  minAssets: "5",
  allocation: "weights",
  startDate: "",
  endDate: "",
};

export type PortfolioPayloadOutcome =
  | { ok: true; payload: PortfolioBacktestRequestPayload }
  | { ok: false; errors: PortfolioFieldErrors };

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function parseNumber(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

export function validateAndBuildPortfolioPayload(
  values: PortfolioFormValues,
): PortfolioPayloadOutcome {
  const errors: PortfolioFieldErrors = {};

  // --- 标的池 ---
  if (values.symbols.length < 2) {
    errors.symbols = "组合回测至少要选 2 只标的（只有 1 只请用「新建回测」）";
  }

  // --- 初始资金 ---
  const cash = parseNumber(values.initialCash);
  if (cash === null) {
    errors.initialCash = "请填写初始资金";
  } else if (cash <= 0) {
    errors.initialCash = "初始资金必须大于 0";
  }

  // --- min_assets：必须不超过标的数，否则每期都凑不满 → 全程空仓 ---
  const minAssets = parseNumber(values.minAssets);
  if (minAssets === null) {
    errors.minAssets = "请填写最少可参与排序的标的数";
  } else if (!Number.isInteger(minAssets) || minAssets < 1) {
    errors.minAssets = "必须是 >= 1 的整数";
  } else if (values.symbols.length >= 2 && minAssets > values.symbols.length) {
    errors.minAssets = `min_assets 不能大于标的数（当前 ${values.symbols.length} 只）：否则每期都凑不满，整段回测会全程空仓`;
  }

  // --- top_fraction（仅动量选股时生效）---
  const percent = parseNumber(values.topFractionPercent);
  let topFraction = 0.2;
  if (values.selection === "momentum_top") {
    if (percent === null) {
      errors.topFractionPercent = "请填写选股比例";
    } else if (percent <= 0 || percent > 100) {
      errors.topFractionPercent = "比例必须在 (0, 100] 之间";
    } else {
      topFraction = percent / 100;
    }
  } else {
    // 全池等权时后端强制 1.0；这里也提交 1.0，避免「界面填了 20 但实际全买」
    topFraction = 1.0;
  }

  // --- 区间 ---
  for (const [key, raw] of [
    ["startDate", values.startDate],
    ["endDate", values.endDate],
  ] as const) {
    if (raw !== "" && !DATE_RE.test(raw)) {
      errors[key] = "日期格式应为 YYYY-MM-DD";
    }
  }
  if (
    values.startDate !== "" &&
    values.endDate !== "" &&
    DATE_RE.test(values.startDate) &&
    DATE_RE.test(values.endDate) &&
    values.startDate > values.endDate
  ) {
    errors.endDate = "结束日期不能早于开始日期";
  }

  if (Object.keys(errors).length > 0) {
    return { ok: false, errors };
  }

  const payload: PortfolioBacktestRequestPayload = {
    dataset_symbols: values.symbols,
    initial_cash: cash as number,
    selection: values.selection,
    top_fraction: topFraction,
    lookback: 60,
    skip: 5,
    min_assets: minAssets as number,
    allocation: values.allocation,
  };
  if (values.startDate !== "") payload.start_date = values.startDate;
  if (values.endDate !== "") payload.end_date = values.endDate;
  return { ok: true, payload };
}

/** 把「选中数 < min_assets」这类可自动修的冲突算出来，供界面提示（不阻塞输入）。 */
export function minAssetsHint(
  symbols: number,
  minAssets: number,
): { level: "ok" | "warn"; text: string } | null {
  if (symbols < 2) return null;
  if (minAssets > symbols) {
    return { level: "warn", text: `min_assets=${minAssets} 大于标的数 ${symbols}，无法提交` };
  }
  if (minAssets < 5) {
    return {
      level: "warn",
      text: `min_assets=${minAssets} 低于预登记 §5 的 5：结论口径与研究报告不同`,
    };
  }
  return { level: "ok", text: `每个调仓日至少 ${minAssets} 只可排序才建仓，否则空仓` };
}
