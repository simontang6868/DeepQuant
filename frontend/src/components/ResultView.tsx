/**
 * 回测结果视图：概要 → 数据质量/限制 → 模拟费率声明 → 绩效卡片 →
 * 资产曲线 → 日收益率 → 最大回撤 → 订单/成交/账户状态 → 可复现性。
 * 全部字段来自真实响应，缺失时显示空状态，不虚构数据。
 */

import type { BacktestResponse } from "../api/types";
import { AccountStatesTable } from "./AccountStatesTable";
import { DailyReturnsChart } from "./DailyReturnsChart";
import { DataQualityNotice } from "./DataQualityNotice";
import { DrawdownCard } from "./DrawdownCard";
import { EquityChart } from "./EquityChart";
import { FillsTable } from "./FillsTable";
import { OrdersTable } from "./OrdersTable";
import { PerformanceCards } from "./PerformanceCards";
import { ReproducibilityPanel } from "./ReproducibilityPanel";
import { formatDate, formatMoney } from "../utils/format";
import { execPriceRuleLabel } from "../utils/labels";

function FeeDisclaimer({ feeSource }: { feeSource: string }) {
  const isPlaceholder = feeSource === "simulation_placeholder";
  return (
    <div className={`banner ${isPlaceholder ? "banner-warn" : "banner-neutral"}`} role="note">
      <strong>费用与滑点声明：</strong>
      本次回测的费用来源标记为 <span className="mono">{feeSource}</span>
      {isPlaceholder
        ? "（模拟占位参数）。该费率与滑点仅用于本地研究与模拟，不代表任何真实券商费率；回测结果不构成对未来收益的任何保证。"
        : "。请以后端说明为准确认该费率的实际含义。"}
    </div>
  );
}

export function ResultView({ response }: { response: BacktestResponse }) {
  const { result, api } = response;
  const config = result.config;

  return (
    <div data-testid="result-view">
      <div className="card">
        <h2>回测概要</h2>
        <table className="summary-table">
          <tbody>
            <tr>
              <th>数据集</th>
              <td>
                {config.symbol}（处理后 {result.dataset?.n_rows ?? "?"} 行）
              </td>
            </tr>
            <tr>
              <th>回测区间</th>
              <td>
                {formatDate(result.period.start) ?? "暂无"} ~{" "}
                {formatDate(result.period.end) ?? "暂无"}
              </td>
            </tr>
            <tr>
              <th>策略</th>
              <td>
                {result.strategy?.name ?? "暂无"}
                {result.strategy?.version ? ` v${result.strategy.version}` : ""}（参数：
                <span className="mono">
                  {result.strategy?.params ? JSON.stringify(result.strategy.params) : "暂无"}
                </span>
                ）
              </td>
            </tr>
            <tr>
              <th>初始资金</th>
              <td>{formatMoney(config.initial_cash) ?? "暂无"}</td>
            </tr>
            <tr>
              <th>执行规则</th>
              <td>
                信号后第 {config.signal_lag} 个交易日以
                {execPriceRuleLabel(config.exec_price_rule)}执行
              </td>
            </tr>
            <tr>
              <th>市场规则</th>
              <td>
                {`${config.market_rules.name}（每手 ${config.market_rules.lot_size} 股，T+${
                  config.market_rules.t_plus_one ? "1" : "0"
                }）`}
              </td>
            </tr>
            <tr>
              <th>结果状态</th>
              <td>
                {result.is_complete ? "完整（is_complete = true）" : "不完整（is_complete = false）"}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <DataQualityNotice result={result} />
      <FeeDisclaimer feeSource={config.fee_source} />
      <PerformanceCards result={result} />
      <EquityChart result={result} />
      <DailyReturnsChart result={result} />
      <DrawdownCard result={result} />
      <OrdersTable orders={result.orders} />
      <FillsTable fills={result.fills} />

      <details className="panel" data-testid="account-states-panel">
        <summary>账户逐日状态（account_states，共 {result.account_states.length} 条）</summary>
        <div className="details-body">
          <AccountStatesTable states={result.account_states} />
        </div>
      </details>

      <ReproducibilityPanel result={result} api={api} />
    </div>
  );
}
