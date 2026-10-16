import { describe, expect, it } from "vitest";
import {
  isDrawdownConsistent,
  toDailyReturnSeries,
  toDrawdownSeries,
  toEquitySeries,
} from "./chartData";
import type { DailyReturnPoint, EquityCurvePoint } from "../api/types";

describe("toEquitySeries", () => {
  it("按日期升序排列（收益曲线按时间顺序展示）", () => {
    const curve: EquityCurvePoint[] = [
      { date: "2020-01-03", total_equity: 103 },
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: 101.5 },
    ];
    const series = toEquitySeries(curve);
    expect(series.map((p) => p.date)).toEqual(["2020-01-01", "2020-01-02", "2020-01-03"]);
    expect(series[0].totalEquity).toBe(100);
  });

  it("过滤 total_equity 缺失（null）的点，不虚构数据", () => {
    const curve: EquityCurvePoint[] = [
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: null },
      { date: "2020-01-03", total_equity: 101 },
    ];
    expect(toEquitySeries(curve)).toHaveLength(2);
  });

  it("空数组 / null / undefined → 空序列", () => {
    expect(toEquitySeries([])).toEqual([]);
    expect(toEquitySeries(null)).toEqual([]);
    expect(toEquitySeries(undefined)).toEqual([]);
  });
});

describe("toDailyReturnSeries", () => {
  it("过滤缺失值并升序排列", () => {
    const points: DailyReturnPoint[] = [
      { date: "2020-01-02", equity: 101, prev_equity: 100, value: 0.01 },
      { date: "2020-01-01", equity: 100, prev_equity: null, value: null },
      { date: "2020-01-03", equity: 99, prev_equity: 101, value: -0.0198 },
    ];
    const series = toDailyReturnSeries(points);
    expect(series.map((p) => p.date)).toEqual(["2020-01-02", "2020-01-03"]);
    expect(series[0].value).toBe(0.01);
  });
});

describe("toDrawdownSeries", () => {
  it("从权益曲线推导逐日回撤比例，与历史峰值比较", () => {
    const curve: EquityCurvePoint[] = [
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: 110 }, // 新峰值 → 回撤 0
      { date: "2020-01-03", total_equity: 99 }, // 相对峰值 110 回撤 (110-99)/110 = 0.10
      { date: "2020-01-04", total_equity: 105 }, // 回撤 (110-105)/110 ≈ 0.0455
    ];
    const series = toDrawdownSeries(curve);
    expect(series).toHaveLength(4);
    expect(series[0].drawdownRatio).toBe(0);
    expect(series[1].drawdownRatio).toBe(0);
    expect(series[2].drawdownRatio).toBeCloseTo(-0.1, 6);
    expect(series[3].drawdownRatio).toBeCloseTo(-0.0454545, 6);
  });

  it("回撤比例始终 ≤ 0（0 表示创历史新高）", () => {
    const curve: EquityCurvePoint[] = [
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: 120 },
      { date: "2020-01-03", total_equity: 150 },
    ];
    const series = toDrawdownSeries(curve);
    expect(series.every((p) => p.drawdownRatio <= 0)).toBe(true);
    expect(series.map((p) => p.drawdownRatio)).toEqual([0, 0, 0]);
  });

  it("过滤 null total_equity 点", () => {
    const curve: EquityCurvePoint[] = [
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: null },
      { date: "2020-01-03", total_equity: 90 },
    ];
    const series = toDrawdownSeries(curve);
    expect(series).toHaveLength(2);
    expect(series[1].drawdownRatio).toBeCloseTo(-0.1, 6);
  });

  it("空 / null / undefined → 空序列", () => {
    expect(toDrawdownSeries([])).toEqual([]);
    expect(toDrawdownSeries(null)).toEqual([]);
    expect(toDrawdownSeries(undefined)).toEqual([]);
  });

  it("权益恒正才能计算比例；峰值非正的点被跳过", () => {
    const curve: EquityCurvePoint[] = [
      { date: "2020-01-01", total_equity: -10 }, // 负权益跳过
      { date: "2020-01-02", total_equity: 100 }, // 新峰值 100
      { date: "2020-01-03", total_equity: 90 }, // 回撤 0.1
    ];
    const series = toDrawdownSeries(curve);
    expect(series).toHaveLength(2);
    expect(series[0].drawdownRatio).toBe(0);
    expect(series[1].drawdownRatio).toBeCloseTo(-0.1, 6);
  });
});

describe("isDrawdownConsistent", () => {
  it("前端最大回撤与后端 max_drawdown_ratio 一致时返回 true", () => {
    const curve: EquityCurvePoint[] = [
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: 110 },
      { date: "2020-01-03", total_equity: 99 },
    ];
    const series = toDrawdownSeries(curve);
    // 前端最大回撤 = (110-99)/110 = 0.1
    expect(isDrawdownConsistent(series, 0.1)).toBe(true);
  });

  it("不一致时返回 false", () => {
    const curve: EquityCurvePoint[] = [
      { date: "2020-01-01", total_equity: 100 },
      { date: "2020-01-02", total_equity: 110 },
      { date: "2020-01-03", total_equity: 99 },
    ];
    const series = toDrawdownSeries(curve);
    expect(isDrawdownConsistent(series, 0.5)).toBe(false);
  });

  it("后端无回撤指标时跳过校验返回 true", () => {
    const series = toDrawdownSeries([{ date: "2020-01-01", total_equity: 100 }]);
    expect(isDrawdownConsistent(series, null)).toBe(true);
    expect(isDrawdownConsistent(series, undefined)).toBe(true);
  });

  it("空序列跳过校验返回 true", () => {
    expect(isDrawdownConsistent([], 0.1)).toBe(true);
  });
});
