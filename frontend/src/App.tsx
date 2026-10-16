/**
 * DeepQuant 回测工作台：应用骨架与全局状态。
 *
 * 布局：顶栏（标题 / 连接状态 / 刷新）→ 左侧配置（数据集 / 策略 / 回测配置）
 * → 右侧结果；后端不可用时显示明确的启动指引与重试入口。
 *
 * 回测执行说明：后端 POST /backtests 是同步接口；本页如实展示“执行中 / 成功 /
 * 失败”三种状态，不伪造进度条、任务 ID 或异步任务系统。请求失败时表单配置
 * 保留在状态中，用户修改后可重新运行。
 */

import { useCallback, useEffect, useState } from "react";
import {
  describeApiError,
  getDatasets,
  getStrategies,
  postBacktest,
  toApiError,
} from "./api/client";
import type { ApiError } from "./api/client";
import type { BacktestResponse, DatasetListResponse, StrategyListResponse } from "./api/types";
import { BackendDownBanner, BackendStatusIndicator } from "./components/BackendStatusBar";
import { BacktestForm } from "./components/BacktestForm";
import { DatasetSelector } from "./components/DatasetSelector";
import { BacktestComparison } from "./components/BacktestComparison";
import { HistoryDetail } from "./components/HistoryDetail";
import { HistoryList } from "./components/HistoryList";
import { StrategyPanel } from "./components/StrategyPanel";
import { ResultView } from "./components/ResultView";
import { useBackendHealth } from "./hooks/useBackendHealth";
import {
  INITIAL_FORM_VALUES,
  validateAndBuildPayload,
} from "./utils/backtestPayload";
import type { BacktestFormValues, FieldErrors } from "./utils/backtestPayload";

type DatasetsState =
  | { status: "loading" }
  | { status: "ready"; data: DatasetListResponse }
  | { status: "error"; error: ApiError };

type StrategiesState =
  | { status: "loading" }
  | { status: "ready"; data: StrategyListResponse }
  | { status: "error"; error: ApiError };

type BacktestState =
  | { status: "idle" }
  | { status: "running" }
  | { status: "success"; response: BacktestResponse; finishedAt: string }
  | { status: "error"; error: ApiError; failedAt: string };

type AppView =
  | { kind: "create" }
  | { kind: "history-list" }
  | { kind: "history-detail"; runId: string }
  | { kind: "compare"; runIds: string[] };

export default function App() {
  const { health, check } = useBackendHealth();
  const [datasets, setDatasets] = useState<DatasetsState>({ status: "loading" });
  const [strategies, setStrategies] = useState<StrategiesState>({ status: "loading" });
  const [form, setForm] = useState<BacktestFormValues>(INITIAL_FORM_VALUES);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [backtest, setBacktest] = useState<BacktestState>({ status: "idle" });
  const [view, setView] = useState<AppView>({ kind: "create" });

  const loadDatasets = useCallback(async () => {
    setDatasets({ status: "loading" });
    try {
      setDatasets({ status: "ready", data: await getDatasets() });
    } catch (err) {
      setDatasets({ status: "error", error: toApiError(err) });
    }
  }, []);

  const loadStrategies = useCallback(async () => {
    setStrategies({ status: "loading" });
    try {
      setStrategies({ status: "ready", data: await getStrategies() });
    } catch (err) {
      setStrategies({ status: "error", error: toApiError(err) });
    }
  }, []);

  const refreshAll = useCallback(() => {
    void check();
    void loadDatasets();
    void loadStrategies();
  }, [check, loadDatasets, loadStrategies]);

  // 挂载时执行一次初始加载（与「刷新数据」按钮行为一致）
  useEffect(() => {
    void check();
    void loadDatasets();
    void loadStrategies();
    // 初始加载仅依赖挂载时机；check/loadDatasets/loadStrategies 均为稳定引用
  }, []);

  const handleSelectDataset = useCallback(
    (symbol: string) => {
      const ds =
        datasets.status === "ready"
          ? (datasets.data.datasets.find((d) => d.symbol === symbol) ?? null)
          : null;
      setForm((prev) => ({
        ...prev,
        datasetSymbol: symbol,
        // 数据集起止日期存在时预填回测区间（仅在为空时，不覆盖用户已填内容）
        startDate: prev.startDate || (ds?.start_date ?? "").slice(0, 10),
        endDate: prev.endDate || (ds?.end_date ?? "").slice(0, 10),
      }));
    },
    [datasets],
  );

  const handleSelectStrategy = useCallback((id: string) => {
    setForm((prev) => ({ ...prev, strategyId: id, paramValues: {} }));
  }, []);

  const handleParamChange = useCallback((name: string, value: string) => {
    setForm((prev) => ({ ...prev, paramValues: { ...prev.paramValues, [name]: value } }));
  }, []);

  const handleRun = useCallback(async () => {
    const strategy =
      strategies.status === "ready"
        ? (strategies.data.strategies.find((s) => s.strategy_id === form.strategyId) ?? null)
        : null;
    const outcome = validateAndBuildPayload(form, strategy);
    if (!outcome.ok) {
      setFieldErrors(outcome.errors);
      return;
    }
    setFieldErrors({});
    setBacktest({ status: "running" });
    try {
      const response = await postBacktest(outcome.payload);
      setBacktest({
        status: "success",
        response,
        finishedAt: new Date().toLocaleString("zh-CN", { hour12: false }),
      });
    } catch (err) {
      // 失败时不清空 form —— 用户配置保留，可修改后重新运行
      setBacktest({
        status: "error",
        error: toApiError(err),
        failedAt: new Date().toLocaleString("zh-CN", { hour12: false }),
      });
    }
  }, [form, strategies]);

  const selectedDataset =
    datasets.status === "ready"
      ? (datasets.data.datasets.find((d) => d.symbol === form.datasetSymbol) ?? null)
      : null;
  const selectedStrategy =
    strategies.status === "ready"
      ? (strategies.data.strategies.find((s) => s.strategy_id === form.strategyId) ?? null)
      : null;

  return (
    <div className="app">
      <header className="app-header">
        <h1>DeepQuant 回测工作台</h1>
        <BackendStatusIndicator health={health} />
        <nav className="app-tabs" role="tablist" aria-label="功能">
          <button
            type="button"
            className={`tab ${view.kind === "create" ? "tab-active" : ""}`}
            role="tab"
            aria-selected={view.kind === "create"}
            data-testid="tab-create"
            onClick={() => setView({ kind: "create" })}
          >
            新建回测
          </button>
          <button
            type="button"
            className={`tab ${
              view.kind === "history-list" ||
              view.kind === "history-detail" ||
              view.kind === "compare"
                ? "tab-active"
                : ""
            }`}
            role="tab"
            aria-selected={
              view.kind === "history-list" ||
              view.kind === "history-detail" ||
              view.kind === "compare"
            }
            data-testid="tab-history"
            onClick={() => setView({ kind: "history-list" })}
          >
            历史回测
          </button>
        </nav>
        <div className="spacer" />
        <button type="button" className="btn" onClick={refreshAll} data-testid="refresh-all">
          刷新数据
        </button>
      </header>

      <BackendDownBanner health={health} onRetry={() => void check()} />

      {view.kind !== "create" ? null : (
        <main className="app-main">
          <aside className="config-panel">
            <DatasetSelector
              state={datasets}
              selectedSymbol={form.datasetSymbol}
              fieldError={fieldErrors.dataset}
              onSelect={handleSelectDataset}
              onRefresh={() => void loadDatasets()}
            />
            <StrategyPanel
              state={strategies}
              selectedId={form.strategyId}
              paramValues={form.paramValues}
              fieldErrors={fieldErrors}
              onSelect={handleSelectStrategy}
              onParamChange={handleParamChange}
              onRefresh={() => void loadStrategies()}
            />
            <BacktestForm
              dataset={selectedDataset}
              strategy={selectedStrategy}
              values={form}
              fieldErrors={fieldErrors}
              running={backtest.status === "running"}
              onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
              onRun={() => void handleRun()}
            />
          </aside>

          <section className="result-panel" aria-label="回测结果">
            {backtest.status === "idle" && (
              <div className="card empty-state" data-testid="result-placeholder">
                在左侧选择数据集与策略、配置回测参数后点击「运行回测」，结果将显示在这里。
              </div>
            )}

            {backtest.status === "running" && (
              <div className="card banner banner-info" role="status" data-testid="result-running">
                正在执行回测（同步请求）…完成后结果会自动展示。请勿重复提交。
              </div>
            )}

            {backtest.status === "error" && (
              <div className="card" data-testid="result-error">
                <div className="banner banner-error" role="alert">
                  <strong>回测请求失败（{backtest.failedAt}）。</strong>
                  <div>{describeApiError(backtest.error)}</div>
                  {backtest.error.details !== undefined && (
                    <details>
                      <summary>错误详情</summary>
                      <pre className="pre-json">
                        {JSON.stringify(backtest.error.details, null, 2)}
                      </pre>
                    </details>
                  )}
                  <div>表单配置已保留，可修改后重新运行。</div>
                </div>
              </div>
            )}

            {backtest.status === "success" && (
              <>
                <div className="banner banner-info" data-testid="result-success-hint">
                  回测完成（{backtest.finishedAt}）。以下结果全部来自后端本次真实返回。
                  {backtest.response.run_id ? (
                    <span>
                      {" "}已自动保存到历史（run_id: {backtest.response.run_id.slice(0, 8)}…）。
                      可前往「历史回测」查看完整记录。
                    </span>
                  ) : null}
                </div>
                <ResultView response={backtest.response} />
              </>
            )}
          </section>
        </main>
      )}

      {view.kind === "history-list" && (
        <main className="app-main app-main-history">
          <HistoryList
            onSelectRun={(runId) => setView({ kind: "history-detail", runId })}
            onCompare={(runIds) => setView({ kind: "compare", runIds })}
          />
        </main>
      )}

      {view.kind === "history-detail" && (
        <main className="app-main app-main-history">
          <HistoryDetail
            runId={view.runId}
            onBack={() => setView({ kind: "history-list" })}
          />
        </main>
      )}

      {view.kind === "compare" && (
        <main className="app-main app-main-history">
          <BacktestComparison
            runIds={view.runIds}
            onBack={() => setView({ kind: "history-list" })}
          />
        </main>
      )}

      <footer className="app-footer">
        DeepQuant 为本地个人量化研究工具：仅支持历史数据模拟回测，不支持实盘交易；
        费用与滑点为模拟占位参数（simulation_placeholder），不代表真实券商费率；
        回测结果仅供研究参考，不构成任何投资建议或未来收益保证。
      </footer>
    </div>
  );
}
