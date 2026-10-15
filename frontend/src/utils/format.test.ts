import { describe, expect, it } from "vitest";
import {
  boolLabel,
  formatDate,
  formatDateTime,
  formatInteger,
  formatMoney,
  formatNumber,
  formatPercent,
  truncateMiddle,
} from "./format";

describe("formatPercent", () => {
  it("小数比率 ×100 显示为百分数", () => {
    expect(formatPercent(0.1234)).toBe("12.34%");
  });

  it("负数保留符号", () => {
    expect(formatPercent(-0.05)).toBe("-5.00%");
  });

  it("0 是有效数值，不是缺失", () => {
    expect(formatPercent(0)).toBe("0.00%");
  });

  it("null/undefined/NaN/Infinity → null（暂无数据）", () => {
    expect(formatPercent(null)).toBeNull();
    expect(formatPercent(undefined)).toBeNull();
    expect(formatPercent(Number.NaN)).toBeNull();
    expect(formatPercent(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("formatNumber / formatMoney / formatInteger", () => {
  it("千分位与固定小数位", () => {
    expect(formatMoney(1234567.891)).toBe("1,234,567.89");
    expect(formatNumber(0)).toBe("0.00");
    expect(formatInteger(2500)).toBe("2,500");
  });

  it("缺失返回 null", () => {
    expect(formatMoney(null)).toBeNull();
    expect(formatInteger(undefined)).toBeNull();
  });
});

describe("formatDate", () => {
  it("取 ISO 字符串的日期部分", () => {
    expect(formatDate("2020-01-01T00:00:00")).toBe("2020-01-01");
    expect(formatDate("2020-01-01")).toBe("2020-01-01");
  });

  it("缺失或非法格式返回 null（不崩溃）", () => {
    expect(formatDate(null)).toBeNull();
    expect(formatDate("")).toBeNull();
    expect(formatDate("not-a-date")).toBeNull();
  });
});

describe("formatDateTime", () => {
  it("合法时间格式化", () => {
    expect(formatDateTime("2020-03-01T00:00:00")).toContain("2020");
  });

  it("解析失败时返回原文本（不丢信息、不崩溃）", () => {
    expect(formatDateTime("weird-but-keep")).toBe("weird-but-keep");
    expect(formatDateTime(null)).toBeNull();
  });
});

describe("其他", () => {
  it("truncateMiddle 保留首尾", () => {
    const hash = "a".repeat(64);
    const out = truncateMiddle(hash, 8);
    expect(out).toMatch(/^a{8}…a{8}$/);
  });

  it("boolLabel 三态", () => {
    expect(boolLabel(true)).toBe("是");
    expect(boolLabel(false)).toBe("否");
    expect(boolLabel(null)).toBe("暂无");
  });
});
