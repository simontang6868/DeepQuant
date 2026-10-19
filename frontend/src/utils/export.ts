/**
 * 结果导出工具：CSV / PNG / JSON 存档。
 *
 * 三条设计原则，都是吃过亏之后定的：
 *
 * 1. **文件脱离界面后必须还能辨认口径**。
 *    文件名自带标的（或池规模）、策略、区间、时间戳，不叫 `export.csv`——
 *    三个月后打开一份 `export (3).csv` 没有任何人知道它是什么。
 * 2. **CSV 保持纯净，口径声明走 JSON**。
 *    CSV 只放表头 + 数据行，方便 pandas / Excel 直接读；完整的 config、
 *    limitations、数据指纹只在 JSON 存档里。混在一起会两头不讨好：
 *    注释行让 `pd.read_csv` 错位，纯数据又丢失了「这是模拟费率」这个前提。
 * 3. **PNG 拿不到就明确失败，不下载一张空白图**。
 *    SVG → canvas 在无头环境（jsdom）里必然失败，此时抛错并由界面提示，
 *    而不是安静地存下一个 0 字节或全白的文件。
 */

import type { RefObject } from "react";

/** 会被 Excel / WPS 当成公式执行的起始字符（CSV 注入）。 */
const FORMULA_PREFIXES = new Set(["=", "+", "-", "@", "\t", "\r"]);

/** CSV 单元格里的危险字符：逗号、引号、换行。 */
function needsQuoting(value: string): boolean {
  return /[",\r\n]/.test(value);
}

/**
 * 转义单个单元格。
 *
 * - `"` → `""`（RFC 4180）；
 * - 含分隔符或换行 → 整体加引号；
 * - 以公式字符开头 → 前置 `'`，防止表格软件把它当公式执行。
 *   这不是我们的数据在攻击谁，而是导出文件常会流转到别人的机器。
 */
export function escapeCsvCell(value: unknown): string {
  if (value === null || value === undefined) return "";

  // 数字与布尔不是公式注入的载体：表格软件只对**文本**单元格开头的
  // `= + - @` 求值。更关键的是，负收益率（如 -0.1234）开头正好是 `-`，
  // 若一并加前缀就会把它变成文本列——那才是真的毁数据。
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : "";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }

  let text = String(value);
  if (FORMULA_PREFIXES.has(text.charAt(0))) {
    text = `'${text}`;
  }
  if (needsQuoting(text)) {
    text = `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

/** 表格数据：表头 + 行（每行是「列名 → 值」的字典，缺列自动补空）。 */
export interface CsvTable {
  /** 文件名主体（自动加 .csv，不含目录） */
  name: string;
  headers: string[];
  rows: Array<Record<string, unknown>>;
}

/** 导出条上的一个按钮：标识 + 文案 + 执行体。 */
export interface ExportAction {
  /** 稳定标识，用于 React key、忙碌态判断与文件名后缀 */
  key: string;
  /** 按钮文案，如「成交明细 CSV」 */
  label: string;
  /** 点击执行；抛错时由导出条统一提示 */
  run: () => void | Promise<void>;
}

/**
 * 生成 CSV 文本（带 UTF-8 BOM，否则 Excel 打开中文是乱码）。
 *
 * 行内顺序以 `headers` 为准，行字典里多余的键被忽略、缺失的键补空，
 * 因此调用方不必为了导出去凑一个严格对齐的二维数组。
 */
export function toCsv(table: CsvTable): string {
  const lines: string[] = [table.headers.map(escapeCsvCell).join(",")];
  for (const row of table.rows) {
    lines.push(table.headers.map((h) => escapeCsvCell(row[h])).join(","));
  }
  return `\uFEFF${lines.join("\r\n")}\r\n`;
}

/** 触发一次浏览器下载；返回是否真的拿到了对象 URL（无头环境为 false）。 */
function triggerDownload(blob: Blob, filename: string): boolean {
  const url = URL.createObjectURL(blob);
  if (!url) return false;
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    return true;
  } finally {
    // 立刻 revoke 会让部分浏览器的下载拿不到数据，交给下一轮事件循环
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}

/** 下载文本文件（CSV / JSON）。 */
export function downloadTextFile(
  filename: string,
  text: string,
  mime = "text/plain;charset=utf-8",
): boolean {
  return triggerDownload(new Blob([text], { type: mime }), filename);
}

/** 下载 CSV 表格。 */
export function downloadCsv(table: CsvTable): boolean {
  return downloadTextFile(`${table.name}.csv`, toCsv(table), "text/csv;charset=utf-8");
}

/** 下载 JSON 存档（完整结果 + 口径声明）。 */
export function downloadJson(filename: string, payload: unknown): boolean {
  return downloadTextFile(
    `${filename}.json`,
    JSON.stringify(payload, null, 2),
    "application/json;charset=utf-8",
  );
}

export class PngExportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PngExportError";
  }
}

/** 图片导出失败时统一给出的替代方案。 */
const PNG_FALLBACK_HINT = "可改用「导出结果 JSON」留档，或截图保存。";

export interface PngExportOptions {
  /** 放大倍数（2 = 视网膜清晰度） */
  scale?: number;
  /** 背景色；默认白色，透明底在深色查看器里会变成黑底 */
  background?: string;
}

/**
 * 把页面上已渲染的 recharts SVG 导出为 PNG。
 *
 * 走 SVG 序列化 → Image → canvas，而不是引入绘图库：图已经在页面上画好了，
 * 再画一遍只是多一处可能不一致的地方。
 *
 * 两个必须处理的坑：
 * - **字体**：recharts 的 `<text>` 靠 CSS 继承字体，序列化成独立 SVG 后
 *   这层继承没了，中文会掉回默认衬线体。所以显式把当前 computed 字体栈
 *   写进 `<style>`。
 * - **尺寸**：`ResponsiveContainer` 下的 SVG 常常没有显式 width/height，
 *   光有 viewBox；不补齐的话画出来是 0×0 或按默认 300×150 裁切。
 *
 * 失败时抛 `PngExportError`，由调用方决定如何提示。
 */
export async function exportSvgAsPng(
  container: HTMLElement | null,
  filename: string,
  options: PngExportOptions = {},
): Promise<void> {
  const { scale = 2, background = "#ffffff" } = options;

  // 所有失败路径都带上替代方案：用户看到报错时最需要的不是原因，
  // 是「那我怎么把结果存下来」。
  if (!container) {
    throw new PngExportError(`未找到图表容器（页面可能尚未完成渲染）。${PNG_FALLBACK_HINT}`);
  }

  const svg = container.querySelector("svg");
  if (!svg) {
    throw new PngExportError(`图表尚未渲染出 SVG 内容，无法导出图片。${PNG_FALLBACK_HINT}`);
  }

  const rect = svg.getBoundingClientRect();
  const width = Math.round(rect.width) || svg.clientWidth || 960;
  const height = Math.round(rect.height) || svg.clientHeight || 320;
  if (width <= 0 || height <= 0) {
    throw new PngExportError("图表尺寸为 0，无法导出图片");
  }

  const cloned = svg.cloneNode(true) as SVGSVGElement;
  cloned.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  cloned.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
  cloned.setAttribute("width", String(width));
  cloned.setAttribute("height", String(height));

  // 字体：从真实渲染结果里取，写死成一份字体栈，序列化后不依赖 CSS 继承
  let fontFamily = "system-ui, -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif";
  try {
    const computed = window.getComputedStyle(svg).fontFamily;
    if (computed && computed.trim()) {
      fontFamily = computed;
    }
  } catch {
    // jsdom 等环境下 getComputedStyle 可能不可用，用默认字体栈兜底
  }
  const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
  style.textContent = `svg, svg text, svg tspan { font-family: ${fontFamily}; }`;
  cloned.insertBefore(style, cloned.firstChild);

  const serialized = new XMLSerializer().serializeToString(cloned);
  const svgBlob = new Blob([serialized], { type: "image/svg+xml;charset=utf-8" });
  const objectUrl = URL.createObjectURL(svgBlob);

  try {
    const image = await loadImage(objectUrl);

    const canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    const context = canvas.getContext("2d");
    if (!context) {
      throw new PngExportError(`当前浏览器不支持 canvas 2d，无法导出图片。${PNG_FALLBACK_HINT}`);
    }
    context.fillStyle = background;
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const pngBlob = await canvasToBlob(canvas);
    if (!pngBlob) {
      throw new PngExportError(`图片编码失败（canvas.toBlob 返回空）。${PNG_FALLBACK_HINT}`);
    }
    if (!triggerDownload(pngBlob, `${filename}.png`)) {
      throw new PngExportError(
        `当前环境不支持文件下载（无头 / 受限浏览器）。${PNG_FALLBACK_HINT}`,
      );
    }
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () =>
      reject(
        new PngExportError(
          "图表导出失败：浏览器无法把图表转成图片（无头环境或受限画布常见）。" +
            PNG_FALLBACK_HINT,
        ),
      );
    image.src = src;
  });
}

function canvasToBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => {
    if (typeof canvas.toBlob !== "function") {
      resolve(null);
      return;
    }
    canvas.toBlob((blob) => resolve(blob), "image/png");
  });
}

/**
 * 时间戳：`20261018-235200`（本地时间，精确到秒）。
 *
 * 不用 ISO 字符串：里面的 `:` 在 Windows 文件名里非法，会让下载静默失败。
 */
export function timestampTag(date: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  );
}

/**
 * 表格 → 导出动作。
 *
 * 下载不可用时抛错而不是静默返回：点了「导出」却什么都没存下来，
 * 比看到一条报错糟糕得多（用户会以为已经保存好了）。
 */
export function csvAction(
  baseName: string,
  key: string,
  label: string,
  table: Omit<CsvTable, "name">,
): ExportAction {
  return {
    key,
    label,
    run: () => {
      if (!downloadCsv({ name: `${baseName}_${key}`, ...table })) {
        throw new Error("当前环境不支持文件下载（无头浏览器或受限环境）。");
      }
    },
  };
}

/** JSON 存档 → 导出动作（唯一保留完整口径的导出方式）。 */
export function jsonAction(
  baseName: string,
  key: string,
  label: string,
  payload: unknown,
): ExportAction {
  return {
    key,
    label,
    run: () => {
      if (!downloadJson(`${baseName}_${key}`, payload)) {
        throw new Error("当前环境不支持文件下载（无头浏览器或受限环境）。");
      }
    },
  };
}

/** 图表 → PNG 导出动作。容器为空或无法编码时抛 `PngExportError`。 */
export function chartPngAction(
  baseName: string,
  key: string,
  label: string,
  container: RefObject<HTMLElement | null>,
  options?: PngExportOptions,
): ExportAction {
  return {
    key,
    label,
    run: () => exportSvgAsPng(container.current, `${baseName}_${key}`, options),
  };
}

/** 把任意文本压成文件名安全的片段（空串统一返回 `na`）。 */
export function slugify(text: string): string {
  // `\\/:*?"<>|` 在 Windows 文件名里非法；空白与 `(),` 一并当分隔符，
  // 否则 `ma cross(5,20)` 这种策略名会带上括号，跨系统拷贝时还要再转义一次。
  const cleaned = text
    .trim()
    .replace(/[\\/:*?"<>|(),\s]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "");
  return cleaned === "" ? "na" : cleaned;
}
