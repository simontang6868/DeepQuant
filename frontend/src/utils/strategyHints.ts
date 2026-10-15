/**
 * ma_cross 的跨参数即时提示（纯函数，便于测试）。
 *
 * 目前后端注册表中只有 ma_cross，其 fast_window / slow_window 存在
 * 「slow 必须大于 fast」的相互约束（来自参数 description 与后端校验）。
 * 该检查按参数名识别，只在两个参数同时可解析为整数时给出提示。
 */

import type { StrategyParamInfo } from "../api/types";

export function crossFieldParamHint(
  params: Record<string, StrategyParamInfo>,
  currentName: string,
  paramValues: Record<string, string>,
): string | null {
  if (!("fast_window" in params) || !("slow_window" in params)) return null;
  // 提示只挂在 slow_window 字段下（它是必须更大的一方），避免两个输入框重复显示
  if (currentName !== "slow_window") return null;

  const fastRaw = (paramValues.fast_window ?? "").trim();
  const slowRaw = (paramValues.slow_window ?? "").trim();
  if (fastRaw === "" || slowRaw === "") return null; // 留空 = 后端默认值，默认组合合法

  const fast = Number(fastRaw);
  const slow = Number(slowRaw);
  if (!Number.isFinite(fast) || !Number.isFinite(slow)) return null;

  if (slow <= fast) {
    return `slow_window（${slow}）必须大于 fast_window（${fast}）：慢线窗口必须长于快线窗口`;
  }
  return null;
}
