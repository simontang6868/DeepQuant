import { describe, expect, it } from "vitest";
import { INITIAL_FORM_VALUES, validateAndBuildPayload } from "./backtestPayload";
import type { BacktestFormValues } from "./backtestPayload";
import type { StrategyInfo } from "../api/types";

const maCross: StrategyInfo = {
  strategy_id: "ma_cross",
  display_name: "双均线交叉",
  version: "1.0.0",
  description: "双均线交叉策略",
  params: {
    fast_window: {
      type: "integer",
      default: 5,
      description: "快线窗口，必须小于 slow_window",
      minimum: 1,
    },
    slow_window: {
      type: "integer",
      default: 20,
      description: "慢线窗口，必须大于 fast_window",
      minimum: 2,
    },
  },
};

function makeValues(patch: Partial<BacktestFormValues> = {}): BacktestFormValues {
  return { ...INITIAL_FORM_VALUES, ...patch };
}

describe("validateAndBuildPayload", () => {
  it("合法配置生成正确 payload（参数留空使用后端默认值语义）", () => {
    const outcome = validateAndBuildPayload(
      makeValues({
        datasetSymbol: "TEST01",
        strategyId: "ma_cross",
        paramValues: { fast_window: "5", slow_window: "20" },
        startDate: "2020-01-01",
        endDate: "2020-02-29",
        initialCash: "100000",
      }),
      maCross,
    );
    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      expect(outcome.payload).toEqual({
        dataset_symbol: "TEST01",
        strategy_id: "ma_cross",
        strategy_params: { fast_window: 5, slow_window: 20 },
        initial_cash: 100000,
        start_date: "2020-01-01",
        end_date: "2020-02-29",
      });
    }
  });

  it("参数留空 → strategy_params 为空对象（后端用默认值）", () => {
    const outcome = validateAndBuildPayload(
      makeValues({ datasetSymbol: "TEST01", strategyId: "ma_cross" }),
      maCross,
    );
    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      expect(outcome.payload.strategy_params).toEqual({});
      expect(outcome.payload.start_date).toBeUndefined();
      expect(outcome.payload.end_date).toBeUndefined();
    }
  });

  it("未选数据集 / 未选策略被阻止", () => {
    const outcome = validateAndBuildPayload(makeValues(), maCross);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.errors.dataset).toBeTruthy();
      expect(outcome.errors.strategy).toBeTruthy();
    }
  });

  it("初始资金非数字 / ≤ 0 被阻止", () => {
    for (const cash of ["abc", "0", "-5"]) {
      const outcome = validateAndBuildPayload(
        makeValues({ datasetSymbol: "TEST01", strategyId: "ma_cross", initialCash: cash }),
        maCross,
      );
      expect(outcome.ok).toBe(false);
      if (!outcome.ok) expect(outcome.errors.initial_cash).toBeTruthy();
    }
  });

  it("开始日期晚于结束日期被阻止", () => {
    const outcome = validateAndBuildPayload(
      makeValues({
        datasetSymbol: "TEST01",
        strategyId: "ma_cross",
        startDate: "2020-03-01",
        endDate: "2020-01-01",
      }),
      maCross,
    );
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.errors.end_date).toContain("不能晚于");
  });

  it("参数小于最小值被阻止（fast_window 最小 1）", () => {
    const outcome = validateAndBuildPayload(
      makeValues({
        datasetSymbol: "TEST01",
        strategyId: "ma_cross",
        paramValues: { fast_window: "0" },
      }),
      maCross,
    );
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.errors["param.fast_window"]).toContain("最小值 1");
  });

  it("参数非整数被阻止", () => {
    const outcome = validateAndBuildPayload(
      makeValues({
        datasetSymbol: "TEST01",
        strategyId: "ma_cross",
        paramValues: { fast_window: "5.5" },
      }),
      maCross,
    );
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.errors["param.fast_window"]).toContain("整数");
  });

  it("slow_window ≤ fast_window 被跨参数校验阻止", () => {
    const outcome = validateAndBuildPayload(
      makeValues({
        datasetSymbol: "TEST01",
        strategyId: "ma_cross",
        paramValues: { fast_window: "25", slow_window: "20" },
      }),
      maCross,
    );
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.errors["param.slow_window"]).toContain("必须大于 fast_window");
    }
  });

  it("初始资金为 0.5 这类小数合法（> 0 即可）", () => {
    const outcome = validateAndBuildPayload(
      makeValues({ datasetSymbol: "TEST01", strategyId: "ma_cross", initialCash: "0.5" }),
      maCross,
    );
    expect(outcome.ok).toBe(true);
  });
});
