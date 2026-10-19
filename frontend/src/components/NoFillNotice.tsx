/**
 * 「0 成交」提示。
 *
 * 为什么单独一个组件：这看起来像「策略没信号」，实际是**委托全部被拒**——
 * 最常见的原因是整手买入买不起（例如茅台 1 手约 20 万元，10 万本金永远下不出去），
 * 结果是净值全程等于初始资金、收益 0%。用户看到一条横线时，如果没有这行说明，
 * 合理的第一反应是「软件坏了」或「这个策略没有收益」，两种判断都是错的。
 *
 * 只在「有委托但零成交」时出现，不干扰正常结果。
 */

import type { BacktestResult } from "../api/types";
import { formatMoney, formatInteger } from "../utils/format";
import { orderRejectReasonLabel } from "../utils/labels";

export function NoFillNotice({ result }: { result: BacktestResult }) {
  const stats = result.performance?.trade_stats;
  if (!stats || stats.n_orders === 0 || stats.n_filled_orders > 0) return null;

  const counts = new Map<string, number>();
  for (const order of result.orders) {
    if (order.status !== "rejected") continue;
    const key = order.reason ?? "未知原因";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const breakdown = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([reason, n]) => `${orderRejectReasonLabel(reason)}：${formatInteger(n)} 笔`)
    .join("；");

  const lotSize = result.config.market_rules.lot_size;
  const cashShortage = counts.has("insufficient_cash");

  return (
    <div className="banner banner-warn" role="alert" data-testid="no-fill-notice">
      <strong>本次回测 0 笔成交，净值全程等于初始资金。</strong>
      <div>
        共 {formatInteger(stats.n_orders)} 笔委托全部未成交
        {breakdown ? `（${breakdown}）` : ""}。
      </div>
      <div>
        原因不是「策略没有产生信号」——信号已产生，是委托没能成交。本模型按{" "}
        {formatInteger(lotSize)} 股整手买入，买入 1 手所需资金 = 价格 × {formatInteger(lotSize)}{" "}
        股（另加费用），因此初始资金较小时，高价股可能一笔都买不进。
      </div>
      {cashShortage ? (
        <div>
          当前初始资金 {formatMoney(result.config.initial_cash) ?? "—"}
          ，不足以买入 1 手：可提高初始资金、改选价格更低的标的，或调整参数后重跑。
        </div>
      ) : null}
    </div>
  );
}
