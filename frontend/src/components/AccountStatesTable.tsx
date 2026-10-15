/**
 * 账户逐日状态表（account_states）。
 * 只展示后端实际返回字段：现金 / 持仓 / 收盘价 / 市值 / 总资产 / 估值状态；
 * 估值状态 = missing_close 时该行给出可见提示（不只靠颜色）。
 */

import type { AccountStateInfo } from "../api/types";
import { formatDate, formatInteger, formatMoney } from "../utils/format";
import { valuationStatusLabel } from "../utils/labels";

export function AccountStatesTable({ states }: { states: AccountStateInfo[] }) {
  return (
    <div data-testid="account-states">
      {states.length === 0 ? (
        <div className="empty-state">本次回测没有产生任何账户状态记录。</div>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>日期</th>
                <th className="num">现金</th>
                <th className="num">持仓数量</th>
                <th className="num">持仓均价</th>
                <th className="num">收盘价</th>
                <th className="num">持仓市值</th>
                <th className="num">总资产</th>
                <th>估值状态</th>
              </tr>
            </thead>
            <tbody>
              {states.map((s, i) => (
                <tr key={`${s.date}-${i}`}>
                  <td>{formatDate(s.date) ?? "—"}</td>
                  <td className="num">{formatMoney(s.cash)}</td>
                  <td className="num">{formatInteger(s.position?.quantity ?? null) ?? "—"}</td>
                  <td className="num">{formatMoney(s.position?.avg_cost ?? null) ?? "—"}</td>
                  <td className="num">{formatMoney(s.close) ?? "—"}</td>
                  <td className="num">{formatMoney(s.market_value) ?? "—"}</td>
                  <td className="num">{formatMoney(s.total_equity) ?? "—"}</td>
                  <td>
                    {valuationStatusLabel(s.valuation_status)}
                    {s.valuation_status === "missing_close" ? "（该日总资产不可靠）" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
