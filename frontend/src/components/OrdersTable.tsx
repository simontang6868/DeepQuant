/**
 * 订单明细表（orders）。
 * 方向/状态使用中文标签并保留原始值；空列表显示明确空状态；
 * 订单数 ≠ 成交数，两表分开展示。
 */

import type { OrderInfo } from "../api/types";
import { formatDate, formatInteger } from "../utils/format";
import { orderStatusLabel, sideLabel } from "../utils/labels";

export function OrdersTable({ orders }: { orders: OrderInfo[] }) {
  return (
    <div className="card" data-testid="orders-card">
      <h2>订单明细（orders，共 {orders.length} 条）</h2>
      {orders.length === 0 ? (
        <div className="empty-state">本次回测没有产生任何订单。</div>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>订单 ID</th>
                <th>标的</th>
                <th>方向</th>
                <th className="num">数量</th>
                <th>状态</th>
                <th>信号日期</th>
                <th>下单日期</th>
                <th>计划执行日</th>
                <th>原因</th>
                <th>成交 ID</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.order_id}>
                  <td className="mono">{o.order_id}</td>
                  <td>{o.symbol}</td>
                  <td>{sideLabel(o.side)}</td>
                  <td className="num">{formatInteger(o.quantity)}</td>
                  <td>
                    <span className={`badge badge-${o.status}`}>{orderStatusLabel(o.status)}</span>
                  </td>
                  <td>{formatDate(o.signal_date) ?? "—"}</td>
                  <td>{formatDate(o.order_date) ?? "—"}</td>
                  <td>{formatDate(o.planned_exec_date) ?? "—"}</td>
                  <td>{o.reason ?? "—"}</td>
                  <td className="mono">{o.fill_id ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
