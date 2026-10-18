/**
 * 数据集选择：
 * - 列表完全来自 GET /datasets 真实响应，不硬编码；
 * - 展示 symbol / 名称 / rows / 起止日期 / 来源 / 原始 CSV 指纹 / 导入时间；
 * - 数据集多时提供筛选（按代码或名称），因为 50 只池接进来后滚一个长下拉框不可用；
 * - 当前已选项永远不会被筛选隐藏（否则 <select> 的 value 不在 options 里，会显示成空白）；
 * - invalid_datasets 只展示后端允许公开的信息（文件名 + 原因），不展示绝对路径；
 * - invalid_datasets 中的数据集不进入可选项。
 */

import { useMemo, useState } from "react";

import { describeApiError } from "../api/client";
import type { ApiError } from "../api/client";
import type { DatasetListResponse } from "../api/types";
import { formatDate, formatDateTime, formatInteger, truncateMiddle } from "../utils/format";

/** 超过这个数量才显示筛选框：3~5 只时多一个输入框只是噪音。 */
const FILTER_THRESHOLD = 12;

export type DatasetsState =
  | { status: "loading" }
  | { status: "ready"; data: DatasetListResponse }
  | { status: "error"; error: ApiError };

interface DatasetSelectorProps {
  state: DatasetsState;
  selectedSymbol: string;
  fieldError?: string;
  onSelect: (symbol: string) => void;
  onRefresh: () => void;
}

function datasetLabel(dataset: { symbol: string; display_name?: string | null }): string {
  return dataset.display_name ? `${dataset.display_name} · ${dataset.symbol}` : dataset.symbol;
}

export function DatasetSelector({
  state,
  selectedSymbol,
  fieldError,
  onSelect,
  onRefresh,
}: DatasetSelectorProps) {
  const datasets = state.status === "ready" ? state.data.datasets : [];
  const invalid = state.status === "ready" ? state.data.invalid_datasets : [];
  const selected = datasets.find((d) => d.symbol === selectedSymbol) ?? null;
  const [query, setQuery] = useState("");

  const showFilter = datasets.length > FILTER_THRESHOLD;
  const { visible, matchedCount } = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return { visible: datasets, matchedCount: datasets.length };
    const matched = datasets.filter(
      (d) =>
        d.symbol.toLowerCase().includes(keyword) ||
        (d.display_name ?? "").toLowerCase().includes(keyword),
    );
    // 已选项被筛掉时仍保留在列表里：否则 <select value> 找不到对应 option，
    // 下拉框会显示成空白，看起来像「什么都没选」，而表单里其实带着上一只的代码。
    const items = selected && !matched.includes(selected) ? [selected, ...matched] : matched;
    return { visible: items, matchedCount: matched.length };
  }, [datasets, query, selected]);

  return (
    <section className="card" aria-label="数据集选择">
      <h2>
        1. 选择数据集
        <button
          type="button"
          className="btn"
          onClick={onRefresh}
          disabled={state.status === "loading"}
          data-testid="refresh-datasets"
        >
          刷新
        </button>
      </h2>

      {fieldError ? <p className="error-text" role="alert">{fieldError}</p> : null}

      {state.status === "loading" && <p className="muted">正在加载数据集…</p>}

      {state.status === "error" && (
        <div className="banner banner-error" role="alert" data-testid="datasets-error">
          <strong>数据集列表加载失败。</strong>
          <div>{describeApiError(state.error)}</div>
          <button type="button" className="btn" onClick={onRefresh}>
            重试
          </button>
        </div>
      )}

      {state.status === "ready" && datasets.length === 0 && (
        <div className="empty-state" data-testid="datasets-empty">
          暂无可用数据集。请先在后端数据根目录导入数据集（参见 README 的「数据集准备」说明），
          然后点击「刷新」。
        </div>
      )}

      {state.status === "ready" && datasets.length > 0 && (
        <div className="field">
          <label htmlFor="dataset-select">
            数据集<span className="required" aria-hidden>*</span>
          </label>
          {showFilter && (
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="按代码或名称筛选，例如 600519 或 茅台"
              aria-label="筛选数据集"
              data-testid="dataset-filter"
            />
          )}
          <select
            id="dataset-select"
            value={selectedSymbol}
            onChange={(e) => onSelect(e.target.value)}
            data-testid="dataset-select"
          >
            <option value="">请选择数据集…</option>
            {visible.map((d) => (
              <option key={d.symbol} value={d.symbol}>
                {datasetLabel(d)}（{formatInteger(d.rows)} 行，{formatDate(d.start_date)} ~{" "}
                {formatDate(d.end_date)}）
              </option>
            ))}
          </select>
          <p className="hint" data-testid="dataset-count">
            共 {datasets.length} 个可用数据集
            {query.trim()
              ? `（筛选匹配 ${matchedCount} 个${visible.length > matchedCount ? "，含当前已选项" : ""}）`
              : ""}
          </p>
          {query.trim() && matchedCount === 0 && (
            <p className="muted" data-testid="dataset-no-match">
              没有匹配的数据集，请换个关键词或清空筛选。
            </p>
          )}
        </div>
      )}

      {invalid.length > 0 && (
        <div className="banner banner-warn" data-testid="invalid-datasets">
          <strong>以下元数据文件未通过校验，不能用于回测（invalid_datasets）：</strong>
          <ul>
            {invalid.map((item, i) => (
              <li key={i}>
                <span className="mono">{item.metadata_file}</span>
                {item.symbol ? `（symbol: ${item.symbol}）` : ""}：{item.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {selected && (
        <div className="selected-detail" data-testid="dataset-detail">
          <h3>数据集详情</h3>
          <ul className="kv-list">
            {selected.display_name && (
              <li>
                <span className="k">名称</span>
                <span data-testid="dataset-display-name">{selected.display_name}</span>
              </li>
            )}
            <li>
              <span className="k">标识（symbol）</span>
              <span>{selected.symbol}</span>
            </li>
            <li>
              <span className="k">数据行数</span>
              <span>{formatInteger(selected.rows) ?? "暂无"}</span>
            </li>
            <li>
              <span className="k">起始日期</span>
              <span>{formatDate(selected.start_date) ?? "暂无"}</span>
            </li>
            <li>
              <span className="k">结束日期</span>
              <span>{formatDate(selected.end_date) ?? "暂无"}</span>
            </li>
            <li>
              <span className="k">数据来源</span>
              <span>{selected.source_name ?? "暂无"}</span>
            </li>
            <li>
              <span className="k">原始 CSV 指纹</span>
              <span className="mono" title={selected.source_sha256 ?? undefined}>
                {selected.source_sha256 ? truncateMiddle(selected.source_sha256) : "暂无"}
              </span>
            </li>
            <li>
              <span className="k">导入时间</span>
              <span>{selected.ingested_at ? formatDateTime(selected.ingested_at) : "暂无"}</span>
            </li>
          </ul>
          <p className="hint">
            可选回测区间：{formatDate(selected.start_date) ?? "?"} ~{" "}
            {formatDate(selected.end_date) ?? "?"}。原始 CSV 指纹（source_sha256）是导入时
            原始文件的 SHA-256，与回测结果中的处理后数据摘要（digest）含义不同。
          </p>
        </div>
      )}
    </section>
  );
}
