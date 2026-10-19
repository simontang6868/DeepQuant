/**
 * 导出工具测试。
 *
 * 重点锁两类会「静默毁数据」的行为：
 * 1. CSV 转义漏字段 → 整行错位，Excel 里看起来正常但数值全错；
 * 2. 公式注入防护误伤负数 → 数值列变成文本列，后续无法计算。
 */

import { describe, expect, it } from "vitest";

import {
  PngExportError,
  escapeCsvCell,
  exportSvgAsPng,
  slugify,
  timestampTag,
  toCsv,
} from "./export";

describe("escapeCsvCell", () => {
  it("普通文本不加引号", () => {
    expect(escapeCsvCell("贵州茅台")).toBe("贵州茅台");
    expect(escapeCsvCell("600519.SH")).toBe("600519.SH");
  });

  it("含逗号 / 换行 → 整体加引号", () => {
    expect(escapeCsvCell("a,b")).toBe('"a,b"');
    expect(escapeCsvCell("line1\nline2")).toBe('"line1\nline2"');
    expect(escapeCsvCell("cr\rhere")).toBe('"cr\rhere"');
  });

  it("双引号翻倍并整体加引号（RFC 4180）", () => {
    expect(escapeCsvCell('say "hi"')).toBe('"say ""hi"""');
  });

  it("缺失值一律留空，不用 0 或占位符", () => {
    expect(escapeCsvCell(null)).toBe("");
    expect(escapeCsvCell(undefined)).toBe("");
    expect(escapeCsvCell(Number.NaN)).toBe("");
    expect(escapeCsvCell(Number.POSITIVE_INFINITY)).toBe("");
  });

  it("数字原样输出，保留小数与负号（关键：不能被公式防护误伤）", () => {
    expect(escapeCsvCell(-0.1234)).toBe("-0.1234");
    expect(escapeCsvCell(0)).toBe("0");
    expect(escapeCsvCell(1_000_000.5)).toBe("1000000.5");
  });

  it("字符串以公式字符开头时前置单引号", () => {
    expect(escapeCsvCell("=1+1")).toBe("'=1+1");
    expect(escapeCsvCell("+cmd")).toBe("'+cmd");
    expect(escapeCsvCell("@SUM")).toBe("'@SUM");
    expect(escapeCsvCell("-abc")).toBe("'-abc");
  });

  it("中文文本不被误判为公式", () => {
    expect(escapeCsvCell("涨停放弃买入")).toBe("涨停放弃买入");
  });
});

describe("toCsv", () => {
  it("带 UTF-8 BOM（否则 Excel 打开中文乱码）", () => {
    const csv = toCsv({ name: "t", headers: ["标的"], rows: [{ 标的: "贵州茅台" }] });
    expect(csv.charCodeAt(0)).toBe(0xfeff);
  });

  it("表头一行 + 数据行，列顺序以 headers 为准", () => {
    const csv = toCsv({
      name: "t",
      headers: ["b", "a"],
      rows: [{ a: 1, b: 2, c: 99 }],
    });
    const lines = csv.replace(/^\uFEFF/, "").trimEnd().split("\r\n");
    expect(lines).toEqual(["b,a", "2,1"]); // 多余的 c 被忽略
  });

  it("行里缺列时补空，不因为缺键而错位", () => {
    const csv = toCsv({
      name: "t",
      headers: ["a", "b", "c"],
      rows: [{ a: 1, c: 3 }],
    });
    const lines = csv.replace(/^\uFEFF/, "").trimEnd().split("\r\n");
    expect(lines[1]).toBe("1,,3");
  });

  it("负数不被引号包裹也不带前缀（回归：公式防护曾误伤）", () => {
    const csv = toCsv({ name: "t", headers: ["r"], rows: [{ r: -0.1853 }] });
    expect(csv).toContain("-0.1853");
    expect(csv).not.toContain("'-0.1853");
  });
});

describe("slugify", () => {
  it("去掉文件名非法字符", () => {
    expect(slugify("600519.SH")).toBe("600519.SH");
    expect(slugify("ma cross(5,20)")).toBe("ma-cross-5-20");
  });

  it("空串统一返回 na，避免出现连续下划线或空文件名", () => {
    expect(slugify("")).toBe("na");
    expect(slugify("   ")).toBe("na");
  });
});

describe("timestampTag", () => {
  it("格式 YYYYMMDD-HHMMSS，不含冒号（Windows 文件名非法）", () => {
    const date = new Date(2026, 9, 18, 23, 52, 7);
    expect(timestampTag(date)).toBe("20261018-235207");
    expect(timestampTag(date)).not.toContain(":");
  });
});

describe("exportSvgAsPng", () => {
  // 无头环境（jsdom）没有真实的 canvas / 字体渲染，PNG 必然导出失败。
  // 这里锁的是：失败要抛可理解的 PngExportError，而不是静默下载一张空白图。
  it("容器为空时抛 PngExportError", async () => {
    await expect(exportSvgAsPng(null, "x")).rejects.toBeInstanceOf(PngExportError);
  });

  it("容器内没有 SVG 时抛 PngExportError", async () => {
    const div = document.createElement("div");
    document.body.appendChild(div);
    await expect(exportSvgAsPng(div, "x")).rejects.toBeInstanceOf(PngExportError);
    div.remove();
  });

  it("错误信息提示改用 JSON 存档，而不是让用户干等", async () => {
    const div = document.createElement("div");
    document.body.appendChild(div);
    await expect(exportSvgAsPng(div, "x")).rejects.toThrow(/JSON|截图/);
    div.remove();
  });
});
