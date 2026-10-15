/**
 * 成交明细表（fills）：与订单分开展示；费用三列来自 fill.fees（模拟费率计算）。
 */

import type { FillInfo } from "../api/types";
import { formatDate, formatInteger, formatMoney } from "../utils/format";
import { sideLabel } from "../utils/labels";

export function FillsTable({ fills }: { fills: FillInfo[] }) {
  return (
    <div className="card" data-testid="fills-card">
      <h2>成交明细（fills，共 {fills.length} 笔）</h2>
      {fills.length === 0 ? (
        <div className="empty-state">本次回测没有产生任何成交。</div>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>成交 ID</th>
                <th>订单 ID</th>
                <th>方向</th>
                <th>成交日期</th>
                <th className="num">成交价格</th>
                <th className="num">数量</th>
                <th className="num">佣金</th>
                <th className="num">印花税</th>
                <th className="num">过户费</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((f) => (
                <tr key={f.fill_id}>
                  <td className="mono">{f.fill_id}</td>
                  <td className="mono">{f.order_id}</td>
                  <td>{sideLabel(f.side)}</td>
                  <td>{formatDate(f.fill_date) ?? "—"}</td>
                  <td className="num">{formatMoney(f.price)}</td>
                  <td className="num">{formatInteger(f.quantity)}</td>
                  <td className="num">{formatMoney(f.fees.commission)}</td>
                  <td className="num">{formatMoney(f.fees.stamp_duty)}</td>
                  <td className="num">{formatMoney(f.fees.transfer_fee)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {fills.length > 0 && (
        <p className="hint">费用按模拟占位费率计算（simulation_placeholder），非真实券商费率。</p>
      )}
    </div>
  );
}
