/**
 * 枚举值中文标签映射：已知值给中文并保留原始值，未知值原样展示（不猜测）。
 */

export function sideLabel(side: string): string {
  switch (side) {
    case "buy":
      return "买入（buy）";
    case "sell":
      return "卖出（sell）";
    default:
      return side;
  }
}

export function orderStatusLabel(status: string): string {
  switch (status) {
    case "pending":
      return "待执行（pending）";
    case "filled":
      return "已全部成交（filled）";
    case "rejected":
      return "已拒绝（rejected）";
    case "expired":
      return "已失效（expired）";
    default:
      return status;
  }
}

export function valuationStatusLabel(status: string): string {
  switch (status) {
    case "ok":
      return "正常（ok）";
    case "missing_close":
      return "缺少收盘价（missing_close）";
    default:
      return status;
  }
}

export function execPriceRuleLabel(rule: string): string {
  switch (rule) {
    case "next_open":
      return "次日开盘价（next_open）";
    default:
      return rule;
  }
}
