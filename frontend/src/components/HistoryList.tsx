/**
 * 历史回测列表组件。
 *
 * 展示后端 GET /backtests 返回的历史记录列表：
 * - 筛选：symbol / strategy_id / status（可选，后端全部参数化处理）；
 * - 分页：上一页 / 下一页 / 总记录数 / 当前页提示；
 * - 排序遵循后端默认（created_at DESC, run_id ASC 稳定次级）；
 * - 列表不包含完整 result 文档；
 * - 状态：加载中 / 空历史 / 列表加载失败；
 * - 切换筛选时递增版本号，防旧请求覆盖新结果。
 *
 * Step 6.5 增量：
 * - 多选：checkbox 列；跨筛选/翻页保留（只存 run_id Set）；上限 5 条；
 * - 「比较所选记录」按钮（≥ 2 条 enable）；「清空选择」按钮。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  describeApiError,
  getBacktestRuns,
  toApiError,
  type ApiError,
} from "../api/client";
import type { BacktestRunListItem, BacktestRunListResponse } from "../api/types";
import { boolLabel, formatDateTime, formatInteger, formatMoney, truncateMiddle } from "../utils/format";

const DEFAULT_LIMIT = 20;
const MAX_COMPARE_RUNS = 5;
const MAX_PAGES_HINT = "（分页仅覆盖前 100 条查询上限，后续需用更精准的筛选缩小范围）";

interface HistoryListProps {
  /** 用户点击某条记录的「查看详情」时触发（由外层 App 负责渲染详情）。 */
  onSelectRun: (runId: string) => void;
  /** 用户点击「比较所选记录」时触发（run_ids 顺序 = 勾选顺序）。 */
  onCompare?: (runIds: string[]) => void;
}

interface Filters {
  symbol: string;
  strategy_id: string;
  status: string;
}

type LoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: BacktestRunListResponse }
  | { status: "error"; error: ApiError };

function _buildListPayload(filters: Filters, offset: number): {
  limit: number;
  offset: number;
  symbol?: string;
  strategy_id?: string;
  status?: string;
} {
  return {
    limit: DEFAULT_LIMIT,
    offset,
    symbol: filters.symbol || undefined,
    strategy_id: filters.strategy_id || undefined,
    status: filters.status || undefined,
  };
}

export function HistoryList({ onSelectRun, onCompare }: HistoryListProps) {
  const [filters, setFilters] = useState<Filters>({ symbol: "", strategy_id: "", status: "" });
  const [submittedFilters, setSubmittedFilters] = useState<Filters>({
    symbol: "",
    strategy_id: "",
    status: "",
  });
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<LoadState>({ status: "idle" });

  // Step 6.5：多选状态
  // - selected = 勾选的 run_id Set；只存 ID → 跨筛选/翻页自然保留
  // - selectedOrder = 按用户勾选先后排序的列表（传给比较 API，顺序决定响应顺序）
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectedOrder, setSelectedOrder] = useState<string[]>([]);

  // 请求版本号：避免筛选快速切换时旧响应覆盖新结果
  const versionRef = useRef(0);

  const loadPage = useCallback(
    async (filtersToUse: Filters, off: number) => {
      const version = ++versionRef.current;
      setState({ status: "loading" });
      try {
        const data = await getBacktestRuns(_buildListPayload(filtersToUse, off));
        if (version === versionRef.current) {
          setState({ status: "ready", data });
        }
      } catch (err) {
        if (version === versionRef.current) {
          setState({ status: "error", error: toApiError(err) });
        }
      }
    },
    [],
  );

  useEffect(() => {
    void loadPage(submittedFilters, offset);
  }, [submittedFilters, offset, loadPage]);

  const totalPages = useMemo(() => {
    if (state.status !== "ready") return 1;
    return Math.max(1, Math.ceil(state.data.total / state.data.limit));
  }, [state]);
  const currentPage = Math.floor(offset / DEFAULT_LIMIT) + 1;
  const isFirstPage = offset === 0;
  const isLastPage = state.status === "ready" && offset + state.data.limit >= state.data.total;

  const handleQuery = () => {
    setSubmittedFilters(filters);
    setOffset(0);
  };

  const handleClear = () => {
    const empty: Filters = { symbol: "", strategy_id: "", status: "" };
    setFilters(empty);
    setSubmittedFilters(empty);
    setOffset(0);
  };

  // Step 6.5：多选 toggle
  const canSelectMore = selected.size < MAX_COMPARE_RUNS;
  const toggleSelect = (runId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) {
        next.delete(runId);
        setSelectedOrder((order) => order.filter((id) => id !== runId));
      } else {
        if (next.size >= MAX_COMPARE_RUNS) return prev; // 已到上限，拒绝更多
        next.add(runId);
        setSelectedOrder((order) => [...order, runId]);
      }
      return next;
    });
  };

  const handleCompare = () => {
    if (onCompare && selectedOrder.length >= 2) {
      onCompare(selectedOrder);
    }
  };

  const handleClearSelection = () => {
    setSelected(new Set());
    setSelectedOrder([]);
  };

  return (
    <div className="history-list" data-testid="history-list">
      <div className="card history-filters">
        <h2>筛选</h2>
        <div className="filter-row">
          <label>
            <span>股票代码</span>
            <input
              type="text"
              value={filters.symbol}
              placeholder="如 000001.SZ"
              onChange={(e) => setFilters((f) => ({ ...f, symbol: e.target.value }))}
            />
          </label>
          <label>
            <span>策略标识符</span>
            <input
              type="text"
              value={filters.strategy_id}
              placeholder="如 ma_cross"
              onChange={(e) => setFilters((f) => ({ ...f, strategy_id: e.target.value }))}
            />
          </label>
          <label>
            <span>状态</span>
            <select
              value={filters.status}
              onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
            >
              <option value="">全部</option>
              <option value="succeeded">succeeded</option>
            </select>
          </label>
          <div className="filter-actions">
            <button type="button" className="btn" onClick={handleQuery} data-testid="filter-apply">
              查询
            </button>
            <button type="button" className="btn btn-secondary" onClick={handleClear} data-testid="filter-clear">
              清空
            </button>
          </div>
        </div>
      </div>

      {state.status === "loading" && (
        <div className="card" role="status" data-testid="history-loading">
          正在加载历史回测…
        </div>
      )}

      {state.status === "error" && (
        <div className="card banner banner-error" role="alert" data-testid="history-error">
          <strong>历史列表加载失败。</strong>
          <div>{describeApiError(state.error)}</div>
          <div>请确认后端服务正常，或稍后重试。</div>
        </div>
      )}

      {state.status === "ready" && (
        <>
          {state.data.total === 0 ? (
            <div className="card empty-state" data-testid="history-empty">
              当前筛选条件下没有历史回测记录。运行回测时使用默认保存（save=true）或在请求中显式传入 save=true，
              结果会自动进入历史列表。
            </div>
          ) : (
            <div className="card" data-testid="history-table-card">
              <div className="history-table-meta">
                <span>共 <strong>{formatInteger(state.data.total) ?? state.data.total}</strong> 条</span>
                <span>
                  第 <strong>{currentPage}</strong> / {formatInteger(totalPages) ?? totalPages} 页，每页 {state.data.limit} 条
                  {state.data.limit >= 100 && MAX_PAGES_HINT}
                </span>
              </div>

              {/* Step 6.5：多选操作条 */}
              {onCompare && (
                <div className="history-compare-bar" data-testid="compare-bar">
                  <span
                    className="compare-selection-info"
                    data-testid="compare-selection-info"
                  >
                    已选 <strong>{selected.size}</strong> / {MAX_COMPARE_RUNS} 条
                    {canSelectMore ? "" : "（已达上限）"}
                  </span>
                  <div className="compare-actions">
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={selected.size < 2}
                      onClick={handleCompare}
                      data-testid="compare-selected"
                    >
                      比较所选记录
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      disabled={selected.size === 0}
                      onClick={handleClearSelection}
                      data-testid="compare-clear-selection"
                    >
                      清空选择
                    </button>
                  </div>
                </div>
              )}

              <table className="history-table" data-testid="history-table">
                <thead>
                  <tr>
                    {onCompare && <th className="col-select" aria-label="选择"></th>}
                    <th>run_id</th>
                    <th>创建时间</th>
                    <th>标的</th>
                    <th>策略</th>
                    <th>回测区间</th>
                    <th>初始资金</th>
                    <th>状态</th>
                    <th>完整性</th>
                    <th>耗时</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {state.data.items.map((item) => (
                    <HistoryRow
                      key={item.run_id}
                      item={item}
                      onSelect={onSelectRun}
                      selected={selected.has(item.run_id)}
                      canSelectMore={canSelectMore}
                      showSelectColumn={Boolean(onCompare)}
                      onToggleSelect={onCompare ? toggleSelect : undefined}
                    />
                  ))}
                </tbody>
              </table>

              <div className="pagination" data-testid="history-pagination">
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={isFirstPage}
                  onClick={() => setOffset((o) => Math.max(0, o - DEFAULT_LIMIT))}
                  data-testid="page-prev"
                >
                  上一页
                </button>
                <span className="page-indicator">
                  第 {currentPage} / {formatInteger(totalPages) ?? totalPages} 页
                </span>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={isLastPage}
                  onClick={() => setOffset((o) => o + DEFAULT_LIMIT)}
                  data-testid="page-next"
                >
                  下一页
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function HistoryRow({
  item,
  onSelect,
  selected,
  canSelectMore,
  showSelectColumn,
  onToggleSelect,
}: {
  item: BacktestRunListItem;
  onSelect: (runId: string) => void;
  selected: boolean;
  canSelectMore: boolean;
  showSelectColumn: boolean;
  onToggleSelect?: (runId: string) => void;
}) {
  const strategyLabel =
    item.strategy_id && item.strategy_version
      ? `${item.strategy_id} (${item.strategy_version})`
      : item.strategy_id ?? "暂无";
  const range =
    item.start_date && item.end_date
      ? `${item.start_date.slice(0, 10)} ~ ${item.end_date.slice(0, 10)}`
      : item.start_date
        ? item.start_date.slice(0, 10)
        : item.end_date
          ? item.end_date.slice(0, 10)
          : "暂无";
  const durationLabel =
    item.duration_ms !== null && item.duration_ms !== undefined ? `${item.duration_ms} ms` : "暂无";

  const selectDisabled = !selected && !canSelectMore;

  return (
    <tr data-run-id={item.run_id} data-selected={selected ? "true" : "false"}>
      {showSelectColumn && (
        <td className="col-select">
          <input
            type="checkbox"
            checked={selected}
            disabled={selectDisabled}
            onChange={() => onToggleSelect?.(item.run_id)}
            aria-label={`选择 run ${truncateMiddle(item.run_id)}`}
            data-testid={`select-${item.run_id}`}
          />
        </td>
      )}
      <td>
        <span className="mono" title={item.run_id}>
          {truncateMiddle(item.run_id)}
        </span>
      </td>
      <td>{formatDateTime(item.created_at) ?? "暂无"}</td>
      <td>{item.symbol}</td>
      <td>{strategyLabel}</td>
      <td>{range}</td>
      <td>{formatMoney(item.initial_cash) ?? "暂无"}</td>
      <td>
        <span className={`tag tag-${item.status === "succeeded" ? "ok" : "neutral"}`}>
          {item.status}
        </span>
      </td>
      <td>
        <span className={`tag ${item.is_complete ? "tag-ok" : "tag-warn"}`}>
          {boolLabel(item.is_complete)}
        </span>
      </td>
      <td>{durationLabel}</td>
      <td>
        <button
          type="button"
          className="btn btn-primary btn-small"
          onClick={() => onSelect(item.run_id)}
          data-testid={`view-detail-${item.run_id}`}
        >
          查看详情
        </button>
      </td>
    </tr>
  );
}
