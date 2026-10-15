/**
 * 策略选择与参数配置：
 * - 策略列表完全来自 GET /strategies（显式注册表），不硬编码、不支持任意代码；
 * - 参数控件按后端 schema 生成；留空 = 使用后端默认值（placeholder 显示默认值）；
 * - fast/slow 互斥约束提供即时提示；前端校验仅改善交互，后端是最终依据。
 */

import { describeApiError } from "../api/client";
import type { ApiError } from "../api/client";
import type { StrategyListResponse } from "../api/types";
import type { FieldErrors } from "../utils/backtestPayload";
import { crossFieldParamHint } from "../utils/strategyHints";

export type StrategiesState =
  | { status: "loading" }
  | { status: "ready"; data: StrategyListResponse }
  | { status: "error"; error: ApiError };

interface StrategyPanelProps {
  state: StrategiesState;
  selectedId: string;
  paramValues: Record<string, string>;
  fieldErrors: FieldErrors;
  onSelect: (id: string) => void;
  onParamChange: (name: string, value: string) => void;
  onRefresh: () => void;
}

export function StrategyPanel({
  state,
  selectedId,
  paramValues,
  fieldErrors,
  onSelect,
  onParamChange,
  onRefresh,
}: StrategyPanelProps) {
  const strategies = state.status === "ready" ? state.data.strategies : [];
  const selected = strategies.find((s) => s.strategy_id === selectedId) ?? null;

  return (
    <section className="card" aria-label="策略选择">
      <h2>
        2. 选择策略
        <button
          type="button"
          className="btn"
          onClick={onRefresh}
          disabled={state.status === "loading"}
          data-testid="refresh-strategies"
        >
          刷新
        </button>
      </h2>

      {fieldErrors.strategy ? (
        <p className="error-text" role="alert">
          {fieldErrors.strategy}
        </p>
      ) : null}

      {state.status === "loading" && <p className="muted">正在加载策略…</p>}

      {state.status === "error" && (
        <div className="banner banner-error" role="alert" data-testid="strategies-error">
          <strong>策略列表加载失败。</strong>
          <div>{describeApiError(state.error)}</div>
          <button type="button" className="btn" onClick={onRefresh}>
            重试
          </button>
        </div>
      )}

      {state.status === "ready" && strategies.length === 0 && (
        <div className="empty-state" data-testid="strategies-empty">
          后端未注册任何策略，无法提交回测。
        </div>
      )}

      {state.status === "ready" && strategies.length > 0 && (
        <>
          <div className="field">
            <label htmlFor="strategy-select">
              策略<span className="required" aria-hidden>*</span>
            </label>
            <select
              id="strategy-select"
              value={selectedId}
              onChange={(e) => onSelect(e.target.value)}
              data-testid="strategy-select"
            >
              <option value="">请选择策略…</option>
              {strategies.map((s) => (
                <option key={s.strategy_id} value={s.strategy_id}>
                  {s.display_name}（{s.strategy_id} v{s.version}）
                </option>
              ))}
            </select>
          </div>

          {selected && (
            <>
              <p className="hint">{selected.description}</p>

              {Object.entries(selected.params).map(([name, info]) => {
                const value = paramValues[name] ?? "";
                const hint = crossFieldParamHint(selected.params, name, paramValues);
                const error = fieldErrors[`param.${name}`];
                return (
                  <div className="field" key={name}>
                    <label htmlFor={`param-${name}`}>
                      {name}
                      <span className="muted">（默认 {String(info.default)}）</span>
                    </label>
                    <input
                      id={`param-${name}`}
                      type="number"
                      step={info.type.toLowerCase().includes("int") ? 1 : "any"}
                      min={info.minimum ?? undefined}
                      value={value}
                      placeholder={`留空使用默认值 ${String(info.default)}`}
                      onChange={(e) => onParamChange(name, e.target.value)}
                      data-testid={`param-${name}`}
                    />
                    <div className="hint">{info.description}</div>
                    {hint ? <div className="warn-text">{hint}</div> : null}
                    {error ? (
                      <div className="error-text" role="alert">
                        {error}
                      </div>
                    ) : null}
                  </div>
                );
              })}

              <p className="hint">
                前端校验仅用于即时提示；参数最终以后端校验为准。策略只能来自后端注册表，
                不支持填写模块路径或代码。
              </p>
            </>
          )}
        </>
      )}
    </section>
  );
}
