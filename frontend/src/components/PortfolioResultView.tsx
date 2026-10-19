/**
 * 组合回测结果视图。
 *
 * 展示顺序刻意按「先免责、再结论、后细节」排：
 * 1. 覆盖与偏差（区间终点、幸存者偏差、flat 10% 涨跌停）——先说清楚这些数字
 *    是在什么条件下得到的，否则后面的收益率会被当成「当年能赚多少」；
 * 2. 绩效与净值；
 * 3. 被阻挡订单——买不进的部分必须和成交一起露出。只展示成交会让人以为
 *    组合按目标权重完整建仓了；
 * 4. 成交明细与调仓记录；
 * 5. limitations 原文（不转述、不缩写）。
 *
 * 不复用单标的的 EquityChart/PerformanceCards：它们的入参类型是 `BacktestResult`，
 * 组合结果结构不同。为了让同一个组件显示两种结构而放宽类型，会让「哪个字段来自
 * 哪里」无从核对——宁可多一个组件。
 */

import { useRef } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { PortfolioResult } from "../api/types";
import { formatDate, formatInteger, formatMoney, formatPercent } from "../utils/format";
import type { ExportAction } from "../utils/export";
import { chartPngAction, csvAction, jsonAction } from "../utils/export";
import {
  notesTable,
  portfolioBlockedTable,
  portfolioDatasetsTable,
  portfolioFillsTable,
  portfolioStatesTable,
  portfolioSummaryTable,
  portfolioTargetsTable,
} from "../utils/exportTables";
import { MetricCard } from "./MetricCard";
import { ResultExportBar } from "./ResultExportBar";

const MAX_ROWS = 200;

function equitySeries(result: PortfolioResult) {
  return result.states
    .filter((s) => s.total_equity !== null)
    .map((s) => ({ date: s.date, equity: s.total_equity as number }));
}

/** 组合层原因码 → 人话（与 labels.ts 的单标的拒单原因分开：语义不完全相同） */
function blockedReasonLabel(reason: string): string {
  switch (reason) {
    case "insufficient_cash":
      return "资金不足";
    case "no_position":
      return "无可卖持仓";
    case "deferred_sell":
      return "跌停顺延";
    case "unfilled_at_end":
      return "期末仍未成交";
    case "below_one_unit":
      return "不足一手";
    case "limit_up":
      return "涨停放弃买入";
    case "limit_down":
      return "跌停无法卖出";
    case "no_price":
      return "当日无有效价格";
    default:
      return reason;
  }
}

export function PortfolioResultView({ result }: { result: PortfolioResult }) {
  const perf = result.performance;
  const series = equitySeries(result);
  const chartRef = useRef<HTMLDivElement>(null);
  const blockedByReason = result.blocked.reduce<Record<string, number>>((acc, b) => {
    acc[b.reason] = (acc[b.reason] ?? 0) + 1;
    return acc;
  }, {});

  // 文件名自带口径：脱离界面后，光看文件名也知道跑的是什么
  const baseName = [
    "deepquant-portfolio",
    `${result.config.n_symbols}sym`,
    result.period.start_date,
    result.period.end_date,
  ].join("_");

  const exportActions: ExportAction[] = [
    csvAction(baseName, "summary", "绩效摘要 CSV", portfolioSummaryTable(result)),
    csvAction(baseName, "states", "组合逐日状态 CSV", portfolioStatesTable(result.states)),
    csvAction(baseName, "fills", "成交明细 CSV", portfolioFillsTable(result)),
    csvAction(baseName, "blocked", "被阻挡订单 CSV", portfolioBlockedTable(result)),
    csvAction(baseName, "targets", "调仓目标权重 CSV", portfolioTargetsTable(result)),
    csvAction(baseName, "datasets", "数据指纹 CSV", portfolioDatasetsTable(result)),
    csvAction(baseName, "notes", "口径声明 CSV", notesTable(result)),
    chartPngAction(baseName, "equity", "净值曲线 PNG", chartRef),
    jsonAction(baseName, "full", "完整结果 JSON", result),
  ];

  const exportNote =
    "CSV 为纯数据（缺失留空、比例保留原始小数），方便直接进 Excel / pandas；" +
    "完整口径（费用占位、涨跌停、幸存者偏差、数据指纹）请用「完整结果 JSON」。";

  return (
    <div className="portfolio-result" data-testid="portfolio-result">
      <div className="card">
        <h2>组合回测结果</h2>
        <p className="hint">
          {result.config.n_symbols} 只标的 · {result.period.start_date} ~ {result.period.end_date}（
          {formatInteger(result.period.n_trading_days)} 个交易日）· 初始资金{" "}
          {formatMoney(result.config.initial_cash)} ·{" "}
          {result.config.allocation === "weights" ? "按权重记账" : "按整手记账"}
        </p>

        <div className="banner banner-neutral" data-testid="portfolio-coverage">
          <strong>数据覆盖与已知偏差：</strong>
          <p>{result.coverage.note}</p>
        </div>

        {!result.is_complete && (
          <div className="banner banner-warn" role="alert">
            <strong>本次回测不完整：</strong>
            <ul>
              {result.incomplete_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}

        {result.consistency_issues.length > 0 && (
          <div className="banner banner-error" role="alert" data-testid="portfolio-consistency">
            <strong>内部一致性自检未通过（结果不可信）：</strong>
            <ul>
              {result.consistency_issues.slice(0, 5).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}

        {result.data_quality_issues.length > 0 && (
          <div className="banner banner-warn">
            <strong>数据质量提示：</strong>
            <ul>
              {result.data_quality_issues.slice(0, 5).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <ResultExportBar baseName={baseName} actions={exportActions} note={exportNote} />

      <div className="card" data-testid="portfolio-performance">
        <h2>绩效指标</h2>
        {perf === null ? (
          <div className="banner banner-warn" role="alert">
            后端未返回组合绩效（performance = null），无法展示指标。
          </div>
        ) : (
          <>
            {perf.evidence.level === "low" && (
              <div className="banner banner-warn" data-testid="portfolio-evidence-low">
                <strong>证据等级：low</strong>
                <ul>
                  {perf.evidence.reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
                <p className="hint">
                  {formatInteger(perf.evidence.n_rebalance_periods)} 个调仓期、
                  {formatInteger(perf.evidence.n_tradable_assets)} 只可交易标的。
                  这个样本量只能看结构，不足以支撑「策略有效」的结论。
                </p>
              </div>
            )}

            <div className="metric-grid">
              <MetricCard
                label="累计收益"
                value={formatPercent(perf.cumulative_return)}
                explanation="期末权益 / 初始资金 − 1（已扣费用）"
              />
              <MetricCard
                label="年化收益"
                value={formatPercent(perf.annualized_return)}
                explanation="按实际日历天数折算"
              />
              <MetricCard
                label="最大回撤"
                value={formatPercent(perf.max_drawdown_ratio)}
                explanation="基于连续净值（现金 + 市值）"
              />
              <MetricCard
                label="年化波动"
                value={formatPercent(perf.annualized_volatility)}
                explanation="净日收益标准差 × √每年交易日数"
              />
              <MetricCard
                label="夏普比率"
                value={perf.sharpe.value === null ? null : perf.sharpe.value.toFixed(3)}
                explanation={`无风险利率 ${formatPercent(perf.sharpe.risk_free_rate_annual)}（${formatInteger(perf.sharpe.periods_per_year)} 期/年）`}
              />
              <MetricCard
                label="年化换手（单边）"
                value={perf.turnover.one_way_annualized === null ? null : perf.turnover.one_way_annualized.toFixed(2)}
                explanation="（总成交额 / 2）/ 平均权益 / 年数"
              />
              <MetricCard
                label="成本占毛收益"
                value={formatPercent(perf.cost.cost_to_gross_ratio)}
                explanation={`总费用 ${formatMoney(perf.cost.total_fees) ?? "—"}`}
              />
              <MetricCard
                label="期末权益"
                value={formatMoney(perf.final_value)}
                explanation={`总盈亏 ${formatMoney(perf.total_pnl) ?? "—"}`}
              />
            </div>

            {perf.sharpe.value === null && perf.sharpe.unavailable_reason && (
              <p className="hint">夏普不可计算：{perf.sharpe.unavailable_reason}</p>
            )}
            {perf.cost.cost_to_gross_ratio === null && perf.cost.unavailable_reason && (
              <p className="hint">成本占比不可计算：{perf.cost.unavailable_reason}</p>
            )}
          </>
        )}
      </div>

      <div className="card" data-testid="portfolio-equity-chart-card">
        <h2>组合净值曲线</h2>
        {series.length === 0 ? (
          <div className="empty-state">暂无净值数据。</div>
        ) : (
          <>
            <div className="chart-box" ref={chartRef} data-testid="portfolio-equity-chart">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={series} margin={{ top: 10, right: 20, bottom: 5, left: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="date"
                    minTickGap={48}
                    tickFormatter={(v: string) => v.slice(5)}
                  />
                  <YAxis
                    domain={["auto", "auto"]}
                    width={92}
                    tickFormatter={(v: number) => formatMoney(v) ?? ""}
                  />
                  <Tooltip
                    formatter={(v: number) => [formatMoney(v) ?? String(v), "组合权益"]}
                    labelFormatter={(v: string) => `日期 ${v}`}
                  />
                  <Line
                    type="monotone"
                    dataKey="equity"
                    stroke="#2f6fed"
                    dot={false}
                    strokeWidth={1.8}
                    name="组合权益"
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="chart-note">
              这是账户权益（现金 + 持仓市值）的绝对金额曲线，不是收益率，也不是标准化净值。
              停牌期间按最后有效价冻结估值。
            </p>
          </>
        )}
      </div>

      <div className="card" data-testid="portfolio-selection-summary">
        <h2>选股与调仓</h2>
        <ul className="kv-list">
          <li>
            <span className="k">选股口径</span>
            <span>
              {result.selection.selection === "momentum_top"
                ? `截面动量取前 ${(result.selection.top_fraction * 100).toFixed(0)}%`
                : "全池等权"}
              （回看 {result.selection.lookback} 日、跳过 {result.selection.skip} 日）
            </span>
          </li>
          <li>
            <span className="k">调仓频率</span>
            <span>月末最后一个交易日产生信号，次一交易日开盘执行</span>
          </li>
          <li>
            <span className="k">信号期数</span>
            <span>
              {formatInteger(result.selection.n_signal_dates)} 期，其中空仓{" "}
              {formatInteger(result.selection.n_empty_periods)} 期
            </span>
          </li>
          <li>
            <span className="k">每期持仓数</span>
            <span>
              {result.selection.min_holdings ?? "—"} ~ {result.selection.max_holdings ?? "—"}，
              平均 {(result.selection.avg_holdings ?? 0).toFixed(2)} 只
            </span>
          </li>
          <li>
            <span className="k">最少可排序标的</span>
            <span>
              {result.selection.min_assets} 只（不足则当期空仓，不强行凑数）
            </span>
          </li>
        </ul>
        <p className="hint">
          空仓是合法状态（选不出股票就持现金），不是错误。上表「空仓期」偏多时，
          通常是因为早段历史不足或池内可交易标的少于最少可排序标的数。
        </p>
      </div>

      <div className="card" data-testid="portfolio-blocked">
        <h2>
          被阻挡订单（{formatInteger(result.blocked.length)} 笔）
          {Object.keys(blockedByReason).length > 0 && (
            <span className="muted">
              {" "}
              —— {Object.entries(blockedByReason)
                .map(([r, n]) => `${blockedReasonLabel(r)} ${n}`)
                .join("、")}
            </span>
          )}
        </h2>
        {result.blocked.length === 0 ? (
          <div className="empty-state">没有订单被阻挡：每次调仓都按目标权重成交。</div>
        ) : (
          <>
            <p className="hint">
              这些是**没有**成交的订单。列出来是为了让你看到「目标权重没有完全实现」，
              而不是只看到漂亮的成交记录。
            </p>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>标的</th>
                    <th>方向</th>
                    <th>原因</th>
                    <th className="num">请求数量</th>
                    <th className="num">成交数量</th>
                  </tr>
                </thead>
                <tbody>
                  {result.blocked.slice(0, MAX_ROWS).map((b, i) => (
                    <tr key={`${b.date}-${b.symbol}-${b.side}-${i}`}>
                      <td>{formatDate(b.date)}</td>
                      <td className="mono">{b.symbol}</td>
                      <td>{b.side === "BUY" ? "买入" : "卖出"}</td>
                      <td>{blockedReasonLabel(b.reason)}</td>
                      <td className="num">{formatInteger(b.requested_quantity)}</td>
                      <td className="num">{formatInteger(b.filled_quantity)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {result.blocked.length > MAX_ROWS && (
              <p className="hint">仅显示前 {MAX_ROWS} 笔（共 {result.blocked.length} 笔）。</p>
            )}
          </>
        )}
      </div>

      <div className="card" data-testid="portfolio-fills">
        <h2>成交明细（{formatInteger(result.fills.length)} 笔）</h2>
        {result.fills.length === 0 ? (
          <div className="empty-state">
            本次回测没有产生任何成交。若「信号期数」不为 0 而成交为 0，多半是资金不足以
            买入任何标的的一手（看上面的被阻挡订单表）。
          </div>
        ) : (
          <>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>标的</th>
                    <th>方向</th>
                    <th className="num">价格</th>
                    <th className="num">数量</th>
                    <th className="num">成交额</th>
                    <th className="num">费用</th>
                    <th>原因</th>
                  </tr>
                </thead>
                <tbody>
                  {result.fills.slice(0, MAX_ROWS).map((f) => (
                    <tr key={f.fill_id}>
                      <td>{formatDate(f.date)}</td>
                      <td className="mono">{f.symbol}</td>
                      <td>{f.side === "BUY" ? "买入" : "卖出"}</td>
                      <td className="num">{formatMoney(f.price)}</td>
                      <td className="num">{formatInteger(f.quantity)}</td>
                      <td className="num">{formatMoney(f.gross)}</td>
                      <td className="num">{formatMoney(f.fees_total)}</td>
                      <td>{f.reason === "deferred_sell" ? "顺延卖出" : "调仓"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {result.fills.length > MAX_ROWS && (
              <p className="hint">仅显示前 {MAX_ROWS} 笔（共 {result.fills.length} 笔）。</p>
            )}
          </>
        )}
      </div>

      <div className="card" data-testid="portfolio-limitations">
        <h2>这次回测的已知限制</h2>
        <ul className="limitation-list">
          {result.limitations.map((note, i) => (
            <li key={i}>{note}</li>
          ))}
        </ul>
        <p className="hint">
          组合层引擎：<span className="mono">{result.engine.module}</span>，
          涨跌停比例 {(result.engine.price_limit_rate * 100).toFixed(0)}%，
          执行耗时 {result.engine.elapsed_seconds.toFixed(2)} 秒。
          数据指纹见下方「使用到的数据集」。
        </p>
      </div>

      <div className="card" data-testid="portfolio-datasets">
        <h2>使用到的数据集</h2>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>标的</th>
                <th>名称</th>
                <th className="num">行数</th>
                <th>区间</th>
                <th>来源</th>
                <th>原始 CSV 指纹</th>
              </tr>
            </thead>
            <tbody>
              {result.datasets.map((d) => (
                <tr key={d.symbol}>
                  <td className="mono">{d.symbol}</td>
                  <td>{d.display_name ?? "—"}</td>
                  <td className="num">{formatInteger(d.rows)}</td>
                  <td>
                    {d.start_date} ~ {d.end_date}
                  </td>
                  <td className="mono">{d.source_name ?? "—"}</td>
                  <td className="mono">{(d.source_sha256 ?? "").slice(0, 12) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
