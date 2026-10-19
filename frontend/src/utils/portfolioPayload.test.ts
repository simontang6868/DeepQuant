/**
 * 组合回测表单校验测试（纯函数，不渲染组件）。
 *
 * 重点不是「能不能填对」，而是「能不能拦住注定会给出误导性结果的请求」：
 * - min_assets > 标的数 → 整段空仓（后端也会 422，前端不该先发出去）；
 * - 只选 1 只 → 那是单标的，应走另一个接口；
 * - 全池等权 → 后端强制 top_fraction=1.0，前端不能提交 20% 却显示「全池等权」。
 */

import { describe, expect, it } from "vitest";

import {
  PORTFOLIO_DEFAULTS,
  minAssetsHint,
  validateAndBuildPortfolioPayload,
} from "./portfolioPayload";
import type { PortfolioFormValues } from "./portfolioPayload";

function make(overrides: Partial<PortfolioFormValues> = {}): PortfolioFormValues {
  return {
    ...PORTFOLIO_DEFAULTS,
    symbols: ["AAA01", "AAA02", "AAA03", "AAA04", "AAA05"],
    ...overrides,
  };
}

describe("validateAndBuildPortfolioPayload", () => {
  it("默认值即可通过：默认就是 Gate 1 口径", () => {
    const outcome = validateAndBuildPortfolioPayload(make());
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.payload).toMatchObject({
      dataset_symbols: ["AAA01", "AAA02", "AAA03", "AAA04", "AAA05"],
      initial_cash: 1_000_000,
      selection: "momentum_top",
      top_fraction: 0.2,
      lookback: 60,
      skip: 5,
      min_assets: 5,
      allocation: "weights",
    });
    // 未填日期时不能提交空字符串（后端 date 类型不接受 ""）
    expect(outcome.payload.start_date).toBeUndefined();
    expect(outcome.payload.end_date).toBeUndefined();
  });

  it("少于 2 只标的被拦下，并指向单标的接口", () => {
    const outcome = validateAndBuildPortfolioPayload(make({ symbols: ["AAA01"] }));
    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.errors.symbols).toContain("至少");
  });

  it("min_assets 大于标的数被拦下（否则整段空仓）", () => {
    const outcome = validateAndBuildPortfolioPayload(
      make({ symbols: ["AAA01", "AAA02"], minAssets: "5" }),
    );
    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.errors.minAssets).toContain("全程空仓");
  });

  it("min_assets 非整数被拦下", () => {
    const outcome = validateAndBuildPortfolioPayload(make({ minAssets: "2.5" }));
    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(outcome.errors.minAssets).toContain("整数");
  });

  it("初始资金必须为正", () => {
    expect(validateAndBuildPortfolioPayload(make({ initialCash: "0" })).ok).toBe(false);
    expect(validateAndBuildPortfolioPayload(make({ initialCash: "-1" })).ok).toBe(false);
    expect(validateAndBuildPortfolioPayload(make({ initialCash: "" })).ok).toBe(false);
  });

  it("选股比例换算成小数，并拒绝越界值", () => {
    const ok = validateAndBuildPortfolioPayload(make({ topFractionPercent: "35" }));
    expect(ok.ok).toBe(true);
    if (ok.ok) expect(ok.payload.top_fraction).toBeCloseTo(0.35, 10);

    expect(validateAndBuildPortfolioPayload(make({ topFractionPercent: "0" })).ok).toBe(false);
    expect(validateAndBuildPortfolioPayload(make({ topFractionPercent: "101" })).ok).toBe(false);
  });

  it("全池等权时强制 top_fraction=1.0（不受界面里的百分比影响）", () => {
    const outcome = validateAndBuildPortfolioPayload(
      make({ selection: "equal_weight_all", topFractionPercent: "20" }),
    );
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.payload.top_fraction).toBe(1.0);
  });

  it("全池等权时百分比填错也不阻塞（该字段已不生效）", () => {
    const outcome = validateAndBuildPortfolioPayload(
      make({ selection: "equal_weight_all", topFractionPercent: "" }),
    );
    expect(outcome.ok).toBe(true);
  });

  it("日期格式与先后顺序被校验", () => {
    const badFormat = validateAndBuildPortfolioPayload(make({ startDate: "2020/01/02" }));
    expect(badFormat.ok).toBe(false);

    const reversed = validateAndBuildPortfolioPayload(
      make({ startDate: "2024-01-01", endDate: "2020-01-01" }),
    );
    expect(reversed.ok).toBe(false);
    if (reversed.ok) return;
    expect(reversed.errors.endDate).toContain("不能早于");
  });

  it("填了日期才带上日期字段", () => {
    const outcome = validateAndBuildPortfolioPayload(
      make({ startDate: "2020-01-02", endDate: "2023-12-29" }),
    );
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.payload.start_date).toBe("2020-01-02");
    expect(outcome.payload.end_date).toBe("2023-12-29");
  });

  it("一次性报出所有字段错误（不是遇到第一个就返回）", () => {
    const outcome = validateAndBuildPortfolioPayload(
      make({ symbols: [], initialCash: "0", minAssets: "0" }),
    );
    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    expect(Object.keys(outcome.errors).length).toBeGreaterThanOrEqual(3);
  });
});

describe("minAssetsHint", () => {
  it("标的不足 2 只时不提示（由 symbols 错误负责）", () => {
    expect(minAssetsHint(1, 5)).toBeNull();
  });

  it("min_assets 超过标的数 → 警告", () => {
    const hint = minAssetsHint(3, 5);
    expect(hint?.level).toBe("warn");
    expect(hint?.text).toContain("无法提交");
  });

  it("min_assets 低于预登记 5 → 警告口径不同", () => {
    const hint = minAssetsHint(10, 2);
    expect(hint?.level).toBe("warn");
    expect(hint?.text).toContain("预登记");
  });

  it("满足预登记口径 → 提示正常", () => {
    const hint = minAssetsHint(10, 5);
    expect(hint?.level).toBe("ok");
  });
});
