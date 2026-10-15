import { describe, expect, it } from "vitest";
import { crossFieldParamHint } from "./strategyHints";
import type { StrategyParamInfo } from "../api/types";

const intParam: StrategyParamInfo = {
  type: "integer",
  default: 5,
  description: "窗口",
  minimum: 1,
};

const params = { fast_window: intParam, slow_window: intParam };

describe("crossFieldParamHint", () => {
  it("slow ≤ fast 时给出提示", () => {
    expect(
      crossFieldParamHint(params, "slow_window", { fast_window: "25", slow_window: "20" }),
    ).toContain("必须大于 fast_window");
  });

  it("slow > fast 无提示", () => {
    expect(
      crossFieldParamHint(params, "slow_window", { fast_window: "5", slow_window: "20" }),
    ).toBeNull();
  });

  it("任一参数留空（使用后端默认值）不提示", () => {
    expect(crossFieldParamHint(params, "slow_window", { fast_window: "25" })).toBeNull();
    expect(crossFieldParamHint(params, "slow_window", {})).toBeNull();
  });

  it("非数字输入不提示（交由字段校验处理）", () => {
    expect(
      crossFieldParamHint(params, "slow_window", { fast_window: "abc", slow_window: "20" }),
    ).toBeNull();
  });

  it("策略无 fast/slow 参数时不提示", () => {
    expect(crossFieldParamHint({}, "slow_window", { fast_window: "1", slow_window: "0" })).toBeNull();
  });
});
