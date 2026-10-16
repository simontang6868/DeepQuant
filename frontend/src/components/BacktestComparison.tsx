/**
 * 历史回测结果比较组件（Step 6.5）。
 *
 * - 使用 props 传入的 runIds 向 POST /backtests/compare 请求只读比较结果；
 * - 支持四种状态：加载中 / 成功 / 404 / 其他错误；
 * - 加载前清空内部 state，配合 App.tsx 的版本切换逻辑避免旧请求覆盖新数据；
 * - 顶部展示「比较对象概览」，中间展示「绩效对比表」，下方展示「配置差异」
 *   和「可比性警示」；
 * - 缺失绩效字段显示「暂无数据」，绝不虚构 0；
 * - 所有提示/差异来自后端统一规则（pairwise.reasons / warnings），前端不自行推导。
 */

import { useEffect, useRef, useState } from "react";
import {
  describeApiError,
  postBacktestCompare,
  toApiError,
  type ApiError,
} from "../api/client";
import type { BacktestCompareResponse } from "../api/types";
import {
  formatDateTime,
  formatDate,
  formatInteger,
  formatMoney,
  formatNumber,
  formatPercent,
  truncateMiddle,
} from "../utils/format";

interface BacktestComparisonProps {
  /** 要比较的 run_id 列表（至少 2 条；顺序决定响应顺序）。 */
  runIds: string[];
  /** 返回历史列表，通常用来重新选择。 */
  onBack: () => void;
}

type CompareState =
  | { status: "loading" }
  | { status: "ready"; data: BacktestCompareResponse }
  | { status: "error"; error: ApiError };

/** 绩效字段 → 展示标签 + 格式化方式；顺序决定表格行顺序。
 * direction 仅用于视觉高亮最优值（不构成投资建议）：
 * - "higher-better": 收益率、胜率、最终权益等越大越优；
 * - "lower-better": 回撤、波动率等越小越优；
 * - "neutral": 不参与最优/最差高亮（如订单数、初始资金）。
 */
type MetricDirection = "higher-better" | "lower-better" | "neutral";
const METRIC_ROWS: ReadonlyArray<{
  key: keyof import("../api/types").BacktestCompareMetrics;
  label: string;
  format: (v: number | null) => string | null;
  direction: MetricDirection;
}> = [
  { key: "initial_equity", label: "初始资金", format: formatMoney, direction: "neutral" },
  { key: "final_equity", label: "最终账户权益", format: formatMoney, direction: "higher-better" },
  { key: "total_pnl", label: "总盈亏", format: formatMoney, direction: "higher-better" },
  { key: "cumulative_return", label: "总收益率", format: formatPercent, direction: "higher-better" },
  { key: "annualized_return", label: "年化收益率", format: formatPercent, direction: "higher-better" },
  { key: "annualized_volatility", label: "年化波动率", format: formatPercent, direction: "lower-better" },
  { key: "max_drawdown_amount", label: "最大回撤（金额）", format: formatMoney, direction: "lower-better" },
  { key: "max_drawdown_ratio", label: "最大回撤", format: formatPercent, direction: "lower-better" },
  { key: "n_orders", label: "订单数", format: formatInteger, direction: "neutral" },
  { key: "n_filled_orders", label: "成交订单", format: formatInteger, direction: "neutral" },
  { key: "n_fills", label: "成交笔数", format: formatInteger, direction: "neutral" },
  { key: "n_closed_trades", label: "已平仓交易", format: formatInteger, direction: "neutral" },
  { key: "win_rate", label: "胜率", format: formatPercent, direction: "higher-better" },
];

/** 元数据字段 → 展示标签；顺序决定概览行顺序。 */
const OVERVIEW_ROWS: ReadonlyArray<{
  key: keyof import("../api/types").BacktestRunListItem;
  label: string;
}> = [
  { key: "created_at", label: "创建时间" },
  { key: "symbol", label: "股票" },
  { key: "strategy_id", label: "策略" },
  { key: "strategy_version", label: "策略版本" },
  { key: "start_date", label: "开始日期" },
  { key: "end_date", label: "结束日期" },
  { key: "initial_cash", label: "初始资金" },
  { key: "result_digest", label: "结果摘要" },
];

export function BacktestComparison({ runIds, onBack }: BacktestComparisonProps) {
  const [state, setState] = useState<CompareState>({ status: "loading" });
  const versionRef = useRef(0);

  useEffect(() => {
    versionRef.current += 1;
    const localVersion = versionRef.current;
    setState({ status: "loading" });

    let cancelled = false;
    (async () => {
      try {
        const data = await postBacktestCompare({ run_ids: runIds });
        if (cancelled || localVersion !== versionRef.current) return;
        setState({ status: "ready", data });
      } catch (err) {
        if (cancelled || localVersion !== versionRef.current) return;
        setState({ status: "error", error: toApiError(err) });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [runIds.join("||")]);

  // ============ 加载中 ============
  if (state.status === "loading") {
    return (
      <div className="card" data-testid="compare-loading">
        <div className="banner banner-info" role="status">
          正在加载 {runIds.length} 条历史回测的比较结果…
        </div>
        <div className="history-back">
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            ← 返回历史列表
          </button>
        </div>
      </div>
    );
  }

  // ============ 错误 ============
  if (state.status === "error") {
    const err = state.error;
    const isMissing = err.kind === "http_error" && err.status === 404;
    return (
      <div className="card" data-testid="compare-error">
        <div className={`banner ${isMissing ? "banner-warn" : "banner-error"}`} role="alert">
          <strong>
            {isMissing ? "部分历史回测不存在。" : "比较请求失败。"}
          </strong>
          <div>{describeApiError(err)}</div>
          {err.details !== undefined && (
            <details>
              <summary>错误详情</summary>
              <pre className="pre-json">{JSON.stringify(err.details, null, 2)}</pre>
            </details>
          )}
        </div>
        <div className="history-back">
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            ← 返回历史列表重新选择
          </button>
        </div>
      </div>
    );
  }

  const { data } = state;
  if (data.items.length === 0) {
    return (
      <div className="card" data-testid="compare-empty">
        <div className="banner banner-info">后端返回空比较结果。</div>
        <div className="history-back">
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            ← 返回历史列表
          </button>
        </div>
      </div>
    );
  }

  // ============ 正常渲染 ============
  return (
    <div className="compare-view" data-testid="compare-view">
      <div className="compare-header">
        <h2>历史回测比较（{data.items.length} 条）</h2>
        <button type="button" className="btn btn-ghost" onClick={onBack}>
          ← 返回历史列表
        </button>
      </div>

      <OverviewTable items={data.items} />
      <MetricsTable items={data.items} />
      <PairwiseDiff items={data.items} pairwise={data.pairwise} />
      <WarningsBlock warnings={data.warnings} pairwise={data.pairwise} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// 子组件
// --------------------------------------------------------------------------- //

function OverviewTable({ items }: { items: BacktestCompareResponse["items"] }) {
  const headers = items.map((it) => (
    <th key={it.run_id} className="compare-col">
      <div className="compare-col-title">
        <code className="run-id" title={it.run_id}>
          {truncateMiddle(it.run_id, 6)}
        </code>
      </div>
      <div className="compare-col-sub">
        {it.metadata.symbol || "—"} · {it.metadata.strategy_id || "—"}
      </div>
    </th>
  ));

  return (
    <section className="card" aria-label="比较对象概览">
      <h3>比较对象概览</h3>
      <div className="table-scroll">
        <table className="data-table compare-table" data-testid="compare-overview">
          <thead>
            <tr>
              <th className="compare-label-col">字段</th>
              {headers}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="compare-label-col">回测 ID</td>
              {items.map((it) => (
                <td key={it.run_id}>
                  <code title={it.run_id}>{truncateMiddle(it.run_id, 6)}</code>
                </td>
              ))}
            </tr>
            {OVERVIEW_ROWS.map((row) => (
              <tr key={row.key}>
                <td className="compare-label-col">{row.label}</td>
                {items.map((it) => (
                  <td key={it.run_id}>
                    {renderOverviewCell(row.key, it.metadata[row.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function renderOverviewCell(key: string, value: unknown): React.ReactNode {
  if (value === null || value === undefined || value === "") return "暂无数据";
  switch (key) {
    case "created_at":
      return formatDateTime(value as string) ?? "暂无数据";
    case "start_date":
    case "end_date":
      return formatDate(value as string) ?? "暂无数据";
    case "initial_cash":
      return formatMoney(value as number) ?? "暂无数据";
    case "result_digest":
      return truncateMiddle(String(value), 10);
    case "strategy_params":
      return JSON.stringify(value);
    default:
      return String(value);
  }
}

function MetricsTable({ items }: { items: BacktestCompareResponse["items"] }) {
  const headers = items.map((it) => (
    <th key={it.run_id} className="compare-col">
      <code>{truncateMiddle(it.run_id, 6)}</code>
    </th>
  ));

  return (
    <section className="card" aria-label="绩效对比">
      <h3>绩效对比</h3>
      <div className="table-scroll">
        <table className="data-table compare-table" data-testid="compare-metrics">
          <thead>
            <tr>
              <th className="compare-label-col">指标</th>
              {headers}
            </tr>
          </thead>
          <tbody>
            {METRIC_ROWS.map((row) => {
              // 计算本行最优值（仅比较非 null 的数值；全部缺失则不高亮）
              const present = items
                .map((it) => it.metrics[row.key])
                .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
              let best: number | null = null;
              if (present.length > 1 && row.direction !== "neutral") {
                best =
                  row.direction === "higher-better" ? Math.max(...present) : Math.min(...present);
              }
              return (
                <tr key={row.key}>
                  <td className="compare-label-col">{row.label}</td>
                  {items.map((it) => {
                    const raw = it.metrics[row.key];
                    const formatted = row.format(raw);
                    const isBest =
                      best !== null &&
                      typeof raw === "number" &&
                      Number.isFinite(raw) &&
                      Math.abs(raw - best) < 1e-12;
                    return (
                      <td
                        key={it.run_id}
                        className={[
                          formatted === null ? "cell-missing" : "",
                          isBest ? "cell-best" : "",
                        ].join(" ").trim() || undefined}
                      >
                        {formatted ?? "暂无数据"}
                        {isBest ? <span className="best-tag">最优</span> : null}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="chart-note">
        标注「最优」的单元格仅表示在本次所选回测中该指标按数值方向（收益/胜率越大越优，
        回撤/波动越小越优）的相对最优，不代表策略具备真实未来盈利能力，亦不构成投资建议；
        可比性请以「可比性提示」与「配置差异」为准。
      </p>
    </section>
  );
}

function PairwiseDiff({
  items,
  pairwise,
}: {
  items: BacktestCompareResponse["items"];
  pairwise: BacktestCompareResponse["pairwise"];
}) {
  // 生成所有两两组合的键
  const pairs: Array<{ key: string; a: string; b: string }> = [];
  for (let i = 0; i < items.length; i += 1) {
    for (let j = i + 1; j < items.length; j += 1) {
      const ids = [items[i].run_id, items[j].run_id].sort();
      pairs.push({ key: ids.join("||"), a: items[i].run_id, b: items[j].run_id });
    }
  }

  const differingPairs = pairs.filter((p) => {
    const pair = pairwise[p.key];
    return pair && Object.keys(pair.differences).length > 0;
  });

  if (differingPairs.length === 0) {
    return (
      <section className="card" aria-label="配置差异" data-testid="compare-diff-none">
        <h3>配置差异</h3>
        <div className="banner banner-info">所有比较记录的核心配置完全一致。</div>
      </section>
    );
  }

  return (
    <section className="card" aria-label="配置差异">
      <h3>配置差异（{differingPairs.length} 对记录）</h3>
      <div data-testid="compare-diff-list">
        {differingPairs.map((p) => {
          const pair = pairwise[p.key];
          const aShort = truncateMiddle(p.a, 6);
          const bShort = truncateMiddle(p.b, 6);
          return (
            <div key={p.key} className="diff-block">
              <div className="diff-title">
                <code>{aShort}</code> vs <code>{bShort}</code>
                {!pair.fairly_comparable && (
                  <span className="badge badge-warn">不可直接比较</span>
                )}
              </div>
              <table className="data-table compare-table compare-diff-table">
                <thead>
                  <tr>
                    <th className="compare-label-col">字段</th>
                    <th><code>{aShort}</code></th>
                    <th><code>{bShort}</code></th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(pair.differences).map(([field, [va, vb]]) => (
                    <tr key={field}>
                      <td className="compare-label-col">{fieldLabel(field)}</td>
                      <td>{formatDiffValue(va)}</td>
                      <td>{formatDiffValue(vb)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function WarningsBlock({
  warnings,
  pairwise,
}: {
  warnings: string[];
  pairwise: BacktestCompareResponse["pairwise"];
}) {
  // 如果后端没给 warnings，但存在不可公平比较的 pair，自动拼接 pair.reasons
  let finalWarnings = warnings;
  if (finalWarnings.length === 0) {
    const fallback: string[] = [];
    Object.values(pairwise).forEach((p) => {
      if (!p.fairly_comparable && Array.isArray(p.reasons)) {
        p.reasons.forEach((r) => {
          if (!fallback.includes(r)) fallback.push(r);
        });
      }
    });
    finalWarnings = fallback;
  }

  if (finalWarnings.length === 0) {
    return (
      <section className="card" aria-label="可比性提示" data-testid="compare-warn-none">
        <h3>可比性提示</h3>
        <div className="banner banner-info">核心配置一致，可直接比较绩效指标。</div>
      </section>
    );
  }

  return (
    <section className="card" aria-label="可比性提示" data-testid="compare-warnings">
      <h3>可比性提示</h3>
      <ul className="compare-warnings">
        {finalWarnings.map((w, i) => (
          <li key={i} className="warn-item">
            {w}
          </li>
        ))}
      </ul>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// 小工具
// --------------------------------------------------------------------------- //

/** 后端 compare_metadata 输出的字段名 → 中文标签。 */
function fieldLabel(field: string): string {
  const map: Record<string, string> = {
    symbol: "股票代码",
    strategy_id: "策略标识符",
    strategy_version: "策略版本",
    strategy_params: "策略参数",
    start_date: "回测开始日期",
    end_date: "回测结束日期",
    initial_cash: "初始资金",
    source_sha256: "原始数据摘要",
    dataset_digest: "清洗后数据摘要",
  };
  return map[field] ?? field;
}

/** 差异值格式化：处理 null / 日期 / 摘要长文本。 */
function formatDiffValue(v: unknown): React.ReactNode {
  if (v === null || v === undefined || v === "") return <em className="missing">暂无</em>;
  if (typeof v === "number") return formatNumber(v, 2) ?? String(v);
  if (typeof v === "object") return <code>{JSON.stringify(v)}</code>;
  if (typeof v === "string" && v.length > 40) return <code>{truncateMiddle(v, 10)}</code>;
  return String(v);
}
