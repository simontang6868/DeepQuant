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

export interface DrawdownSeriesPoint {
  date: string;
  /** 当日回撤比例 = (历史峰值权益 - 当日权益) / 历史峰值权益；非正数（0 表示未回撤） */
  drawdownRatio: number;
}

/**
 * 从已保存的 performance.equity_curve 推导逐日回撤比例序列（纯前端可视化计算，不修改后端）。
 *
 * 计算口径：遍历按日期升序的权益点，维护「截至当日的历史峰值权益」 peak；
 *   drawdownRatio = (peak - total_equity) / peak
 * - peak > 0 才计算；peak <= 0 时点跳过（避免除零 / 负值峰值导致失真）。
 * - 结果 drawdownRatio 始终 ≤ 0（0 表示当日创历史新高或无回撤）。
 * - 序列最大绝对值应与后端 max_drawdown_ratio 一致（组件层做容差校验）。
 *
 * 这是「已有权益序列」的派生展示计算，不重新执行回测，不修改引擎、交易规则或 schema。
 */
export function toDrawdownSeries(
  curve: EquityCurvePoint[] | null | undefined,
): DrawdownSeriesPoint[] {
  const equity = toEquitySeries(curve);
  if (equity.length === 0) return [];

  let peak = -Infinity;
  const out: DrawdownSeriesPoint[] = [];
  for (const p of equity) {
    if (p.totalEquity > peak) peak = p.totalEquity;
    if (peak <= 0) continue; // 峰值非正：无法定义比例回撤，跳过该点
    const ratio = (peak - p.totalEquity) / peak; // >= 0
    out.push({ date: p.date, drawdownRatio: ratio === 0 ? 0 : -ratio });
  }
  return out;
}

/**
 * 校验前端推导的最大回撤比例与后端 max_drawdown_ratio 是否一致（相对容差）。
 * 用于在回撤曲线图旁提示数据一致性；不一致时返回 false，组件展示警示但不阻断。
 */
export function isDrawdownConsistent(
  series: DrawdownSeriesPoint[],
  backendMaxDrawdownRatio: number | null | undefined,
): boolean {
  if (!isPresentNumber(backendMaxDrawdownRatio)) return true; // 后端无值时不校验
  if (series.length === 0) return true;
  const frontMin = Math.min(...series.map((p) => p.drawdownRatio)); // 最负 = 最大回撤
  const backend = Math.abs(backendMaxDrawdownRatio);
  if (backend === 0) return Math.abs(frontMin) < 1e-9;
  // 相对误差 < 1e-6 视为一致（浮点计算容差）
  return Math.abs(Math.abs(frontMin) - backend) / backend < 1e-6;
}
