/**
 * 图表数据变换（纯函数，便于单元测试）。
 *
 * 原则：只做排序/过滤缺失值等展示前变换，不修改、不推算任何业务数值；
 * 输出字段明确标注来源，避免把账户资产误称为净值或收益率。
 */

import type {
  DailyReturnPoint,
  EquityCurvePoint,
} from "../api/types";
import { isPresentNumber } from "./format";

export interface EquitySeriesPoint {
  date: string;
  totalEquity: number;
}

/**
 * performance.equity_curve → 图表序列。
 * - 过滤 total_equity 缺失/非有限数的点（后端允许 null）；
 * - 按日期升序排列（ISO 日期字符串的字典序即时间序）。
 */
export function toEquitySeries(
  curve: EquityCurvePoint[] | null | undefined,
): EquitySeriesPoint[] {
  if (!Array.isArray(curve)) return [];
  return curve
    .filter((p) => p && typeof p.date === "string" && isPresentNumber(p.total_equity))
    .map((p) => ({ date: p.date, totalEquity: p.total_equity as number }))
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}

export interface DailyReturnSeriesPoint {
  date: string;
  value: number;
}

/**
 * performance.daily_returns → 图表序列。
 * value 为后端计算的小数日收益率；缺失/非有限数的点过滤掉。
 */
export function toDailyReturnSeries(
  points: DailyReturnPoint[] | null | undefined,
): DailyReturnSeriesPoint[] {
  if (!Array.isArray(points)) return [];
  return points
    .filter((p) => p && typeof p.date === "string" && isPresentNumber(p.value))
    .map((p) => ({ date: p.date, value: p.value as number }))
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}
