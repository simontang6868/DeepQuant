/**
 * 组合回测配置面板：多选标的 + 选股口径 + 参数。
 *
 * 为什么标的用「可搜索的多选框」而不是原生 multiple select：
 * 50+ 项的原生多选（Ctrl+点击）在真实使用里几乎不可用，而且**选错了看不出来**。
 * 这里用显式 checkbox 列表，并在上方常驻「已选 N 只」与筛选计数。
 *
 * 关于 unavailable_datasets：导入时被闸门拒掉的标的**从未入库**，所以它
 * 既不在可选列表里、也不是「坏了的数据集」。如果不说明，用户会以为软件漏导入。
 * 这里单独列出来（不可勾选），并保留原因码。
 */

import { useMemo, useState } from "react";

import { describeApiError } from "../api/client";
import type { DatasetsState } from "./DatasetSelector";
import type { PortfolioFieldErrors, PortfolioFormValues } from "../utils/portfolioPayload";
import { minAssetsHint } from "../utils/portfolioPayload";
import { formatInteger } from "../utils/format";

interface PortfolioPanelProps {
  datasets: DatasetsState;
  values: PortfolioFormValues;
  errors: PortfolioFieldErrors;
  running: boolean;
  onChange: (patch: Partial<PortfolioFormValues>) => void;
  onToggleSymbol: (symbol: string) => void;
  onSelectAll: () => void;
  onClearSymbols: () => void;
  onRun: () => void;
  onRefreshDatasets: () => void;
}

export function PortfolioPanel({
  datasets,
  values,
  errors,
  running,
  onChange,
  onToggleSymbol,
  onSelectAll,
  onClearSymbols,
  onRun,
  onRefreshDatasets,
}: PortfolioPanelProps) {
  const [query, setQuery] = useState("");

  const all = datasets.status === "ready" ? datasets.data.datasets : [];
  const unavailable =
    datasets.status === "ready" ? (datasets.data.unavailable_datasets ?? []) : [];
  const coverage = datasets.status === "ready" ? (datasets.data.coverage ?? null) : null;

  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return all;
    return all.filter(
      (d) =>
        d.symbol.toLowerCase().includes(keyword) ||
        (d.display_name ?? "").toLowerCase().includes(keyword),
    );
  }, [all, query]);

  const selectedSet = useMemo(() => new Set(values.symbols), [values.symbols]);
  const hint = minAssetsHint(values.symbols.length, Number(values.minAssets) || 0);

  return (
    <div className="card" data-testid="portfolio-panel">
      <h2>标的池（组合回测）</h2>

      {datasets.status === "loading" && <p className="muted">正在加载数据集…</p>}
      {datasets.status === "error" && (
        <div className="banner banner-error" role="alert">
          <p>{describeApiError(datasets.error)}</p>
          <button type="button" className="btn" onClick={onRefreshDatasets}>
            重试
          </button>
        </div>
      )}

      {datasets.status === "ready" && (
        <>
          <p className="hint" data-testid="portfolio-dataset-count">
            共 {all.length} 个可用数据集 · 已选 <strong>{values.symbols.length}</strong> 只
            {coverage ? ` · 数据覆盖 ${coverage.start_date} ~ ${coverage.end_date}` : ""}
          </p>

          {coverage && (
            <p className="hint" data-testid="portfolio-coverage-note">
              {coverage.note}
            </p>
          )}

          <div className="portfolio-picker-tools">
            <input
              type="search"
              placeholder="筛选代码或名称…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="筛选标的"
              data-testid="portfolio-symbol-filter"
            />
            <button type="button" className="btn" onClick={onSelectAll} data-testid="portfolio-select-all">
              全选
            </button>
            <button type="button" className="btn" onClick={onClearSymbols} data-testid="portfolio-clear">
              清空
            </button>
          </div>

          {errors.symbols && <p className="error-text">{errors.symbols}</p>}

          <ul className="symbol-picker" data-testid="portfolio-symbol-list">
            {visible.map((d) => (
              <li key={d.symbol}>
                <label>
                  <input
                    type="checkbox"
                    checked={selectedSet.has(d.symbol)}
                    onChange={() => onToggleSymbol(d.symbol)}
                    data-testid={`symbol-${d.symbol}`}
                  />
                  <span className="symbol-name">
                    {d.display_name ? `${d.display_name} · ${d.symbol}` : d.symbol}
                  </span>
                  <span className="muted">{formatInteger(d.rows)} 行</span>
                </label>
              </li>
            ))}
          </ul>
          {visible.length === 0 && (
            <p className="muted" data-testid="portfolio-no-match">
              没有匹配的标的，请换个关键词。
            </p>
          )}

          {unavailable.length > 0 && (
            <div className="banner banner-neutral" data-testid="portfolio-unavailable">
              <strong>以下标的在导入时被数据闸门拒绝，因此不在可选列表里：</strong>
              <ul>
                {unavailable.map((u) => (
                  <li key={u.symbol}>
                    {u.display_name ? `${u.display_name} · ` : ""}
                    <span className="mono">{u.symbol}</span> —— {u.reason}
                  </li>
                ))}
              </ul>
              <p className="hint">
                拒绝原因是导入时的判定（不是实时校验）。数据不修就直接用，会得到
                一份与研究报告不对应的行情，所以宁可导入失败也不修价。
              </p>
            </div>
          )}
        </>
      )}

      <h3>选股口径</h3>
      <div className="field">
        <label htmlFor="portfolio-selection">选股方式</label>
        <select
          id="portfolio-selection"
          value={values.selection}
          onChange={(e) =>
            onChange({ selection: e.target.value as PortfolioFormValues["selection"] })
          }
          data-testid="portfolio-selection"
        >
          <option value="momentum_top">截面动量取前 N%（Gate 1 主实验口径）</option>
          <option value="equal_weight_all">全池等权（不做筛选）</option>
        </select>
      </div>

      {values.selection === "momentum_top" ? (
        <div className="field">
          <label htmlFor="portfolio-top">
            每期取前<span className="required" aria-hidden>*</span>
          </label>
          <input
            id="portfolio-top"
            type="number"
            min={1}
            max={100}
            step={1}
            value={values.topFractionPercent}
            onChange={(e) => onChange({ topFractionPercent: e.target.value })}
            data-testid="portfolio-top-fraction"
          />
          {errors.topFractionPercent && (
            <p className="error-text">{errors.topFractionPercent}</p>
          )}
          <p className="hint">
            取前 {values.topFractionPercent || "?"}% 的标的等权持有；动量口径为 60 个交易日回看、跳过最近 5 日。
          </p>
        </div>
      ) : (
        <p className="hint">
          全池等权：每个调仓日买入池内全部可交易标的，权重相同。它放弃了动量筛选，
          对池子构成变化完全暴露——适合当「不做选股」的基准，不适合当策略结论。
        </p>
      )}

      <div className="field">
        <label htmlFor="portfolio-min-assets">
          最少可排序标的<span className="required" aria-hidden>*</span>
        </label>
        <input
          id="portfolio-min-assets"
          type="number"
          min={1}
          step={1}
          value={values.minAssets}
          onChange={(e) => onChange({ minAssets: e.target.value })}
          data-testid="portfolio-min-assets"
        />
        {errors.minAssets && <p className="error-text">{errors.minAssets}</p>}
        {!errors.minAssets && hint && (
          <p className={hint.level === "warn" ? "warn-text" : "hint"} data-testid="portfolio-min-assets-hint">
            {hint.text}
          </p>
        )}
      </div>

      <div className="field">
        <label htmlFor="portfolio-allocation">记账方式</label>
        <select
          id="portfolio-allocation"
          value={values.allocation}
          onChange={(e) =>
            onChange({ allocation: e.target.value as PortfolioFormValues["allocation"] })
          }
          data-testid="portfolio-allocation"
        >
          <option value="weights">按权重（主口径，不受整手约束）</option>
          <option value="lots">按整手（100 股/手，检验可执行性）</option>
        </select>
        <p className="hint">
          「按权重」允许零碎股数，回答「这个策略理论上赚多少」；
          「按整手」只能买卖 100 股整数倍，回答「这些股数真买得进吗」。
        </p>
      </div>

      <h3>资金与区间</h3>
      <div className="field">
        <label htmlFor="portfolio-cash">
          初始资金<span className="required" aria-hidden>*</span>
        </label>
        <input
          id="portfolio-cash"
          type="number"
          min={1}
          step={10000}
          value={values.initialCash}
          onChange={(e) => onChange({ initialCash: e.target.value })}
          data-testid="portfolio-initial-cash"
        />
        {errors.initialCash && <p className="error-text">{errors.initialCash}</p>}
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="portfolio-start">开始日期</label>
          <input
            id="portfolio-start"
            type="date"
            value={values.startDate}
            onChange={(e) => onChange({ startDate: e.target.value })}
            data-testid="portfolio-start-date"
          />
          {errors.startDate && <p className="error-text">{errors.startDate}</p>}
        </div>
        <div className="field">
          <label htmlFor="portfolio-end">结束日期</label>
          <input
            id="portfolio-end"
            type="date"
            value={values.endDate}
            onChange={(e) => onChange({ endDate: e.target.value })}
            data-testid="portfolio-end-date"
          />
          {errors.endDate && <p className="error-text">{errors.endDate}</p>}
        </div>
      </div>
      <p className="hint">
        留空表示用数据覆盖的全区间。区间上限由数据决定（不是接口限制）：
        超出数据终点的部分会被真实行情截断，结果里的区间是**实际**区间。
      </p>

      <button
        type="button"
        className="btn btn-primary"
        onClick={onRun}
        disabled={running}
        data-testid="portfolio-run"
      >
        {running ? "组合回测执行中…" : "运行组合回测"}
      </button>
      {running && (
        <p className="hint">
          同步接口：51 只 × 6 年实测约 5 秒。期间页面不会显示进度条——
          这里没有任务队列，不伪造进度。
        </p>
      )}
    </div>
  );
}
