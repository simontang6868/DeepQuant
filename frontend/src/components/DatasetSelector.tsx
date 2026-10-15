/**
 * 数据集选择：
 * - 列表完全来自 GET /datasets 真实响应，不硬编码；
 * - 展示 symbol / rows / 起止日期 / 来源 / 原始 CSV 指纹 / 导入时间；
 * - invalid_datasets 只展示后端允许公开的信息（文件名 + 原因），不展示绝对路径；
 * - invalid_datasets 中的数据集不进入可选项。
 */

import { describeApiError } from "../api/client";
import type { ApiError } from "../api/client";
import type { DatasetListResponse } from "../api/types";
import { formatDate, formatDateTime, formatInteger, truncateMiddle } from "../utils/format";

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
          <select
            id="dataset-select"
            value={selectedSymbol}
            onChange={(e) => onSelect(e.target.value)}
            data-testid="dataset-select"
          >
            <option value="">请选择数据集…</option>
            {datasets.map((d) => (
              <option key={d.symbol} value={d.symbol}>
                {d.symbol}（{formatInteger(d.rows)} 行，{formatDate(d.start_date)} ~{" "}
                {formatDate(d.end_date)}）
              </option>
            ))}
          </select>
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
