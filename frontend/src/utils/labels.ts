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

/**
 * 委托被拒原因的中文说明。
 *
 * 为什么需要这层：`insufficient_cash` 这类代码直接摆给用户看时，读起来像「程序出错了」，
 * 而真实含义是「这笔委托在你给的资金下根本下不出去」——尤其茅台这类高价股，
 * 10 万本金连 1 手都买不起，全程 0 成交，曲线是一条横线。
 */
export function orderRejectReasonLabel(reason: string): string {
  switch (reason) {
    case "insufficient_cash":
      return "资金不足，无法买入 1 手";
    case "insufficient_position":
      return "无可卖持仓";
    case "already_holding":
      return "已持有该标的，不支持加仓";
    case "no_price":
      return "当日无可成交价格";
    default:
      return reason;
  }
}
