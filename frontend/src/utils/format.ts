/**
 * 展示格式化工具。
 *
 * 关键约定：
 * - null / undefined / 非有限数 → 返回 null（界面显示“暂无数据”），绝不虚构数值；
 * - 0 是有效数值，正常展示，不与缺失混淆；
 * - 收益率/回撤类字段为小数（0.1234 = 12.34%），展示时 ×100；
 * - 金额/数量直接按数字格式化，不添加未经确认的货币符号。
 */

export function isPresentNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** 小数比率 → 百分数字符串（0.1234 → "12.34%"）；无效输入返回 null。 */
export function formatPercent(ratio: number | null | undefined, digits = 2): string | null {
  if (!isPresentNumber(ratio)) return null;
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** 数字 → 千分位字符串（1234.5 → "1,234.50"）；无效输入返回 null。 */
export function formatNumber(value: number | null | undefined, digits = 2): string | null {
  if (!isPresentNumber(value)) return null;
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 金额（单位与数据集/配置一致，不虚构货币符号）。 */
export function formatMoney(value: number | null | undefined): string | null {
  return formatNumber(value, 2);
}

/** 整数（数量/笔数）。 */
export function formatInteger(value: number | null | undefined): string | null {
  if (!isPresentNumber(value)) return null;
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

/**
 * 日期展示：取 ISO 字符串的 YYYY-MM-DD 部分（后端日期为无时区 ISO-8601）。
 * 缺失或格式不合法返回 null，交由调用方显示“暂无”。
 */
export function formatDate(value: string | null | undefined): string | null {
  if (typeof value !== "string" || value === "") return null;
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(value);
  return m ? m[1] : null;
}

/** 日期时间展示（如导入时间）；解析失败返回原文本，避免丢信息。 */
export function formatDateTime(value: string | null | undefined): string | null {
  if (typeof value !== "string" || value === "") return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

/** 长指纹截断显示（title 中保留完整值）。 */
export function truncateMiddle(text: string, keep = 8): string {
  if (text.length <= keep * 2 + 3) return text;
  return `${text.slice(0, keep)}…${text.slice(-keep)}`;
}

/** 布尔值 → 是/否；null/undefined → 暂无。 */
export function boolLabel(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return "暂无";
  return value ? "是" : "否";
}
