/**
 * 回测配置表单（区间 + 初始资金）与提交前配置摘要。
 *
 * - 字段与 POST /backtests 请求体对齐：初始资金必填 > 0，start ≤ end；
 * - 日期与所选数据集区间协调：默认预填数据集区间，越界给黄色提示（不阻断，
 *   以后端校验为准）；
 * - 执行价格规则、市场规则与费率不在前端暴露配置项（使用后端默认值），
 *   摘要中如实注明“模拟占位费率”。
 */

import type { DatasetEntry, StrategyInfo } from "../api/types";
import type { BacktestFormValues, FieldErrors } from "../utils/backtestPayload";
import { formatMoney } from "../utils/format";

interface BacktestFormProps {
  dataset: DatasetEntry | null;
  strategy: StrategyInfo | null;
  values: BacktestFormValues;
  fieldErrors: FieldErrors;
  running: boolean;
  onChange: (patch: Partial<BacktestFormValues>) => void;
  onRun: () => void;
}

function dateOutOfRange(date: string, rangeStart: string, rangeEnd: string): boolean {
  if (date === "" || rangeStart === "" || rangeEnd === "") return false;
  const d = date.slice(0, 10);
  return d < rangeStart.slice(0, 10) || d > rangeEnd.slice(0, 10);
}

export function BacktestForm({
  dataset,
  strategy,
  values,
  fieldErrors,
  running,
  onChange,
  onRun,
}: BacktestFormProps) {
  const rangeStart = dataset?.start_date ?? "";
  const rangeEnd = dataset?.end_date ?? "";
  const startWarn = dataset && dateOutOfRange(values.startDate, rangeStart, rangeEnd);
  const endWarn = dataset && dateOutOfRange(values.endDate, rangeStart, rangeEnd);
  const errorEntries = Object.entries(fieldErrors);

  const paramSummary =
    strategy === null
      ? "未选择"
      : Object.keys(strategy.params).length === 0
        ? "（该策略无参数）"
        : Object.entries(strategy.params)
            .map(([name, info]) => {
              const raw = (values.paramValues[name] ?? "").trim();
              return `${name}=${raw === "" ? `${String(info.default)}（默认）` : raw}`;
            })
            .join("，");

  return (
    <section className="card" aria-label="回测配置">
      <h2>3. 回测配置</h2>

      {errorEntries.length > 0 && (
        <div className="banner banner-error" role="alert" data-testid="form-errors">
          <strong>配置存在问题，请修正后再运行：</strong>
          <ul>
            {errorEntries.map(([key, msg]) => (
              <li key={key}>{msg}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="field">
        <label htmlFor="initial-cash">
          初始资金<span className="required" aria-hidden>*</span>
        </label>
        <input
          id="initial-cash"
          type="number"
          min="0"
          step="any"
          value={values.initialCash}
          onChange={(e) => onChange({ initialCash: e.target.value })}
          data-testid="initial-cash"
        />
        <div className="hint">必须大于 0；货币单位与数据集/后端配置一致。</div>
        {fieldErrors.initial_cash ? (
          <div className="error-text" role="alert">
            {fieldErrors.initial_cash}
          </div>
        ) : null}
      </div>

      <div className="field">
        <label htmlFor="start-date">开始日期</label>
        <input
          id="start-date"
          type="date"
          value={values.startDate}
          onChange={(e) => onChange({ startDate: e.target.value })}
          data-testid="start-date"
        />
        {startWarn ? (
          <div className="warn-text">
            开始日期在数据集覆盖区间（{rangeStart.slice(0, 10)} ~ {rangeEnd.slice(0, 10)}）之外，
            后端可能返回不完整结果或错误。
          </div>
        ) : null}
        {fieldErrors.start_date ? (
          <div className="error-text" role="alert">
            {fieldErrors.start_date}
          </div>
        ) : null}
      </div>

      <div className="field">
        <label htmlFor="end-date">结束日期</label>
        <input
          id="end-date"
          type="date"
          value={values.endDate}
          onChange={(e) => onChange({ endDate: e.target.value })}
          data-testid="end-date"
        />
        {endWarn ? (
          <div className="warn-text">
            结束日期在数据集覆盖区间（{rangeStart.slice(0, 10)} ~ {rangeEnd.slice(0, 10)}）之外，
            后端可能返回不完整结果或错误。
          </div>
        ) : null}
        {fieldErrors.end_date ? (
          <div className="error-text" role="alert">
            {fieldErrors.end_date}
          </div>
        ) : null}
      </div>

      <h3>提交前配置摘要</h3>
      <table className="summary-table">
        <tbody>
          <tr>
            <th>数据集</th>
            <td>{dataset ? dataset.symbol : "未选择"}</td>
          </tr>
          <tr>
            <th>策略</th>
            <td>{strategy ? `${strategy.display_name}（${strategy.strategy_id}）` : "未选择"}</td>
          </tr>
          <tr>
            <th>策略参数</th>
            <td>{paramSummary}</td>
          </tr>
          <tr>
            <th>回测区间</th>
            <td>
              {values.startDate === "" ? "数据集起始" : values.startDate} ~{" "}
              {values.endDate === "" ? "数据集结束" : values.endDate}
              （留空表示使用数据集全区间）
            </td>
          </tr>
          <tr>
            <th>初始资金</th>
            <td>{formatMoney(Number(values.initialCash)) ?? "未填写"}</td>
          </tr>
          <tr>
            <th>信号延迟</th>
            <td>使用后端默认值（signal_lag = 1 个交易日）</td>
          </tr>
          <tr>
            <th>执行与费用</th>
            <td>
              使用后端默认执行规则与模拟占位费率/滑点（fee_source =
              simulation_placeholder，非真实券商费率）
            </td>
          </tr>
        </tbody>
      </table>

      <button
        type="button"
        className="btn-primary"
        onClick={onRun}
        disabled={running || values.datasetSymbol === "" || values.strategyId === ""}
        data-testid="run-backtest"
      >
        {running ? "回测执行中…（同步请求，请勿重复提交）" : "运行回测"}
      </button>
      {running && (
        <p className="hint" role="status" data-testid="running-hint">
          正在等待后端返回结果（同步接口）；完成后结果将显示在右侧。请勿重复点击。
        </p>
      )}
    </section>
  );
}
