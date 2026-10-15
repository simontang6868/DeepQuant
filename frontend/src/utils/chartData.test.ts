import { describe, expect, it } from "vitest";
import { toDailyReturnSeries, toEquitySeries } from "./chartData";
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
