/**
 * 历史回测详情组件。
 *
 * - 使用 run_id 向 GET /backtests/{run_id} 请求完整历史结果；
 * - 详情成功加载后，把后端返回的 result 嵌进一个临时 BacktestResponse 外层，
 *   完全复用既有的 ResultView（绩效卡片 / 权益曲线 / 订单成交 / 可复现性等全部零改动）；
 * - 顶部额外展示历史元数据面板（run_id / 创建时间 / 状态 / 摘要等）；
 * - 明确区分四种状态：加载中 / 404 不存在 / 加载失败 / 正常展示；
 * - 切换不同 run_id 时递增版本号，避免较早请求覆盖最新页面状态；
 * - 渲染前先清空内部 state，保证不会显示上一条 run 的残留数据。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  describeApiError,
  getBacktestRunDetail,
  getBacktestRunIntegrity,
  getBacktestRunReproducibility,
  toApiError,
  type ApiError,
} from "../api/client";
import type {
  ApiInfo,
  BacktestIntegrityResponse,
  BacktestReproducibilityResponse,
  BacktestResponse,
  BacktestRunDetailResponse,
} from "../api/types";
import { ResultView } from "./ResultView";
import { boolLabel, formatDateTime, formatMoney, truncateMiddle } from "../utils/format";

interface HistoryDetailProps {
  runId: string;
  onBack: () => void;
}

type DetailState =
  | { status: "loading" }
  | { status: "not_found" }
  | { status: "integrity_error" }
  | { status: "result_corrupted" }
  | { status: "server_error"; error: ApiError }
  | { status: "ready"; data: BacktestRunDetailResponse };

/** 只读完整性审计的展示状态（与详情加载相互独立，失败不影响详情展示）。 */
type IntegrityState =
  | { status: "loading" }
  | { status: "ready"; data: BacktestIntegrityResponse }
  | { status: "unavailable" };

/** 只读复现信息清单的展示状态（与详情/完整性相互独立，失败不影响详情展示）。 */
type ReproState =
  | { status: "loading" }
  | { status: "ready"; data: BacktestReproducibilityResponse }
  | { status: "unavailable" };

/**
 * 只读完整性审计状态块。
 *
 * 严格区分「结果完整性（文档与摘要是否一致）」与「回测可复现性」（后者见 ReproducibilityPanel）：
 * - 通过 ≠ 策略有效，也 ≠ 可复现；
 * - 信息不足（incomplete）≠ 数据损坏；
 * - 审计不可用时不臆测结论。
 */
function IntegrityStatusCard({
  state,
  runId,
}: {
  state: IntegrityState;
  runId: string;
}) {
  return (
    <div className="card integrity-card" data-testid="integrity-card">
      <h3>结果完整性检查（只读审计）</h3>

      {state.status === "loading" && (
        <div data-testid="integrity-loading" role="status">
          正在检查已保存结果文档的完整性…
        </div>
      )}

      {state.status === "unavailable" && (
        <div className="hint" data-testid="integrity-unavailable">
          完整性检查暂时不可用（不影响结果展示）。未获得审计结论，请勿据此判断结果是否被修改。
        </div>
      )}

      {state.status === "ready" && state.data.status === "passed" && (
        <div className="banner banner-neutral" role="note" data-testid="integrity-passed">
          <strong>完整性检查通过。</strong>
          <div>
            重新计算的摘要与已保存摘要一致，结果文档未被意外修改。
            该结论仅表示「已保存结果与记录摘要一致」，<strong>不代表策略有效或回测可复现</strong>。
          </div>
        </div>
      )}

      {state.status === "ready" && state.data.status === "failed" && (
        <div className="banner banner-error" role="alert" data-testid="integrity-failed">
          <strong>检测到完整性问题。</strong>
          <ul className="check-list">
            {state.data.checks
              .filter((c) => c.status === "failed")
              .map((c) => (
                <li key={c.name}>
                  <span className="mono">{c.name}</span>：{c.message}
                </li>
              ))}
          </ul>
        </div>
      )}

      {state.status === "ready" && state.data.status === "incomplete" && (
        <div className="banner banner-warn" role="alert" data-testid="integrity-incomplete">
          <strong>信息不足，无法完成全部检查。</strong>
          <div>缺少必要信息不等于数据损坏；以下检查项未能完成：</div>
          <ul className="check-list">
            {state.data.checks
              .filter((c) => c.status === "incomplete")
              .map((c) => (
                <li key={c.name}>
                  <span className="mono">{c.name}</span>：{c.message}
                </li>
              ))}
          </ul>
        </div>
      )}

      {state.status === "ready" && (
        <div className="integrity-meta">
          <div>
            审计时间：{formatDateTime(state.data.audited_at) ?? state.data.audited_at}
            {"　"}检查项：{state.data.checks.length}
          </div>
          <div className="mono" title={state.data.document_result_digest ?? undefined}>
            已保存摘要：{state.data.document_result_digest ?? "暂无"}
          </div>
          <div className="mono" title={state.data.recomputed_digest ?? undefined}>
            重新计算摘要：{state.data.recomputed_digest ?? "暂无"}
          </div>
        </div>
      )}

      <p className="hint">
        说明：本检查只针对 run_id <span className="mono">{truncateMiddle(runId, 10)}</span> 已保存结果文档与
        摘要是否一致、必需字段是否齐全；回测是否可复现（输入数据 / 策略版本 / 环境）见下方「可复现性与技术信息」。
      </p>
    </div>
  );
}

/** 单项复现信息的保存 / 可验证状态 → 中文标签。 */
const REPRO_ITEM_STATUS_LABELS: Record<string, string> = {
  available: "已保存",
  missing: "缺失",
  verifiable: "可验证",
  not_verifiable: "无法验证",
  not_archived: "未归档",
  not_recorded: "未记录",
  not_applicable: "不适用",
};

/** 复现能力层级状态 → 中文标签（full / partial / none / undetermined）。 */
const REPRO_LEVEL_STATUS_LABELS: Record<string, string> = {
  full: "具备",
  partial: "部分具备",
  none: "不具备",
  undetermined: "无法判断",
};

const REPRO_LEVEL_NAMES: Record<string, string> = {
  data: "数据级",
  code: "代码级",
  environment: "环境级",
};

/** 结果文档可读性状态 → 中文标签。 */
const REPRO_DOC_STATE_LABELS: Record<string, string> = {
  ok: "结果文档可读取",
  missing: "结果文档缺失",
  corrupted: "结果文档已损坏",
};

/** 单项状态徽章样式：仅对真正可用的正状态用绿色，其余用中性/警示色。 */
function reproItemBadgeClass(status: string): string {
  if (status === "available" || status === "verifiable") return "badge badge-filled";
  if (status === "missing") return "badge badge-rejected";
  if (status === "not_archived" || status === "not_recorded" || status === "not_verifiable") {
    return "badge badge-warn";
  }
  return "badge";
}

/** 复现能力层级徽章样式：full 才用绿色，none 用红色，其余中性/警示。 */
function reproLevelBadgeClass(status: string): string {
  if (status === "full") return "badge badge-filled";
  if (status === "none") return "badge badge-rejected";
  if (status === "partial") return "badge badge-warn";
  return "badge";
}

/**
 * 只读复现信息清单卡片。
 *
 * 严格区分「记录了什么信息」「能否验证」「凭现有材料能否重跑」，
 * 并明确：完整性审计通过 ≠ 可复现；缺失信息如实标注，不用默认值冒充。
 */
function ReproChecklistCard({ state, runId }: { state: ReproState; runId: string }) {
  return (
    <div className="card" data-testid="repro-card">
      <h3>复现信息清单（只读）</h3>

      {state.status === "loading" && (
        <div data-testid="repro-loading" role="status">
          正在汇总复现所需信息…
        </div>
      )}

      {state.status === "unavailable" && (
        <div className="hint" data-testid="repro-unavailable">
          复现信息清单暂时不可用（不影响结果展示）。未获得清单结论，请勿据此判断可复现性。
        </div>
      )}

      {state.status === "ready" && (
        <>
          {state.data.result_document_state !== "ok" && (
            <div className="banner banner-warn" role="alert" data-testid="repro-doc-state">
              <strong>
                {REPRO_DOC_STATE_LABELS[state.data.result_document_state] ??
                  state.data.result_document_state}
              </strong>
              <div>
                仅能依赖元数据与已保存字段判断复现信息；结果文档中的策略 / 数据集 / 配置信息无法读取。
              </div>
            </div>
          )}

          <div className="repro-levels" data-testid="repro-levels">
            {state.data.levels.map((lv) => (
              <div className="repro-level" data-testid={`repro-level-${lv.level}`} key={lv.level}>
                <div className="repro-level-head">
                  <strong>{REPRO_LEVEL_NAMES[lv.level] ?? lv.level}</strong>
                  <span
                    className={reproLevelBadgeClass(lv.status)}
                    data-testid={`repro-level-status-${lv.level}`}
                  >
                    {REPRO_LEVEL_STATUS_LABELS[lv.status] ?? lv.status}
                  </span>
                </div>
                <div className="repro-level-row">判断依据：{lv.basis}</div>
                {lv.available_materials.length > 0 && (
                  <div className="repro-level-row">已具备：{lv.available_materials.join("、")}</div>
                )}
                {lv.missing_materials.length > 0 && (
                  <div className="repro-level-row">缺失：{lv.missing_materials.join("、")}</div>
                )}
                <div className="hint">影响：{lv.impact}</div>
              </div>
            ))}
          </div>

          <details className="panel" data-testid="repro-items">
            <summary>逐项信息状态（{state.data.items.length} 项）</summary>
            <ul className="check-list">
              {state.data.items.map((it) => (
                <li key={it.key} data-testid={`repro-item-${it.key}`}>
                  <span className={reproItemBadgeClass(it.status)}>
                    {REPRO_ITEM_STATUS_LABELS[it.status] ?? it.status}
                  </span>{" "}
                  <span className="k">{it.label}</span>
                  {it.value !== null && (
                    <span className="mono" title={it.value}>
                      {truncateMiddle(it.value, 24)}
                    </span>
                  )}
                  <div className="hint">{it.detail}</div>
                </li>
              ))}
            </ul>
          </details>

          {state.data.missing_info.length > 0 && (
            <div className="banner banner-warn" role="alert" data-testid="repro-missing">
              <strong>关键缺失信息：</strong>
              {state.data.missing_info.join("；")}
            </div>
          )}

          <div className="banner banner-neutral" role="note" data-testid="repro-conclusion">
            {state.data.conclusion}
          </div>
        </>
      )}

      <p className="hint">
        说明：本清单回答「记录了哪些信息 / 能否验证 / 凭现有材料能否重跑」，与上方「结果完整性审计」
        不是同一件事。完整性通过<strong>不等于</strong>结果可复现，也不等于策略有效。
        清单基于 run_id <span className="mono">{truncateMiddle(runId, 10)}</span> 记录中实际保存的字段生成，
        未保存的信息一律标注为缺失 / 未记录，不臆造默认值。
      </p>
    </div>
  );
}

/** 把历史详情 API 响应包装成 ResultView 接受的 BacktestResponse 形态。 */
function toBacktestResponse(detail: BacktestRunDetailResponse): BacktestResponse {
  // api 元信息对 ResultView 来说只是展示用（name + version）；
  // 历史详情中 result.config.symbol / result.engine_version 足够构造一个合理占位。
  const api: ApiInfo = {
    name: "deepquant-history",
    version: detail.result.engine_version ?? detail.metadata.strategy_version ?? "0.0.0",
  };
  return { api, result: detail.result, run_id: detail.run_id };
}

export function HistoryDetail({ runId, onBack }: HistoryDetailProps) {
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const [integrity, setIntegrity] = useState<IntegrityState>({ status: "loading" });
  const [repro, setRepro] = useState<ReproState>({ status: "loading" });
  const versionRef = useRef(0);

  const load = useCallback(async (id: string) => {
    const version = ++versionRef.current;
    setState({ status: "loading" });
    setIntegrity({ status: "loading" });
    setRepro({ status: "loading" });
    try {
      const data = await getBacktestRunDetail(id);
      if (version !== versionRef.current) return;
      setState({ status: "ready", data });
      // 详情加载成功后再执行只读完整性审计；审计失败仅显示「暂时不可用」，不影响详情
      getBacktestRunIntegrity(id)
        .then((result) => {
          if (version === versionRef.current) setIntegrity({ status: "ready", data: result });
        })
        .catch(() => {
          if (version === versionRef.current) setIntegrity({ status: "unavailable" });
        });
      // 复现信息清单独立加载；失败仅显示「暂时不可用」，不影响详情
      getBacktestRunReproducibility(id)
        .then((result) => {
          if (version === versionRef.current) setRepro({ status: "ready", data: result });
        })
        .catch(() => {
          if (version === versionRef.current) setRepro({ status: "unavailable" });
        });
    } catch (err) {
      if (version !== versionRef.current) return;
      setIntegrity({ status: "unavailable" });
      setRepro({ status: "unavailable" });
      const apiErr = toApiError(err);
      if (apiErr.code === "run_not_found" || apiErr.status === 404) {
        setState({ status: "not_found" });
      } else if (apiErr.code === "run_integrity_error") {
        setState({ status: "integrity_error" });
      } else if (apiErr.code === "run_result_corrupted") {
        setState({ status: "result_corrupted" });
      } else {
        setState({ status: "server_error", error: apiErr });
      }
    }
  }, []);

  // run_id 变化时立即加载；依赖 runId，保证切换不同记录会重新请求
  useEffect(() => {
    void load(runId);
  }, [runId, load]);

  if (state.status === "loading") {
    return (
      <div className="card" role="status" data-testid="detail-loading">
        正在加载历史回测结果（{runId.slice(0, 8)}…）…
      </div>
    );
  }

  if (state.status === "not_found") {
    return (
      <div className="card" data-testid="detail-not-found">
        <div className="banner banner-error" role="alert">
          <strong>历史记录不存在（404）。</strong>
          <div>run_id <span className="mono">{runId}</span> 在数据库中未找到。</div>
        </div>
        <button type="button" className="btn" onClick={onBack} data-testid="detail-back-from-404">
          返回历史列表
        </button>
      </div>
    );
  }

  if (state.status === "integrity_error") {
    return (
      <div className="card" data-testid="detail-integrity">
        <div className="banner banner-error" role="alert">
          <strong>历史结果不完整（数据完整性问题）。</strong>
          <div>
            run_id <span className="mono">{runId}</span> 的元数据存在，但完整结果文档缺失。
            这通常表示数据库曾被非正规操作修改或写入事务未完成。
          </div>
        </div>
        <button type="button" className="btn" onClick={onBack}>
          返回历史列表
        </button>
      </div>
    );
  }

  if (state.status === "result_corrupted") {
    return (
      <div className="card" data-testid="detail-corrupted">
        <div className="banner banner-error" role="alert">
          <strong>历史结果文档已损坏（数据完整性问题）。</strong>
          <div>
            run_id <span className="mono">{runId}</span> 的结果文档存在，但内容无法解析为合法的结果结构。
            这通常表示结果文档被非正规操作修改或写入过程异常中断；本系统不会自动修复或重跑该记录。
          </div>
        </div>
        <button type="button" className="btn" onClick={onBack}>
          返回历史列表
        </button>
      </div>
    );
  }

  if (state.status === "server_error") {
    return (
      <div className="card banner banner-error" role="alert" data-testid="detail-error">
        <strong>历史详情加载失败。</strong>
        <div>{describeApiError(state.error)}</div>
        <div>请确认后端服务正常，或稍后重试。</div>
        <button type="button" className="btn" onClick={onBack}>
          返回历史列表
        </button>
      </div>
    );
  }

  const detail = state.data;
  const meta = detail.metadata;

  return (
    <div className="history-detail" data-testid="history-detail">
      <div className="detail-toolbar">
        <button type="button" className="btn btn-secondary" onClick={onBack} data-testid="detail-back">
          ← 返回历史列表
        </button>
        <h2 className="detail-title">历史回测详情 · <span className="mono">{truncateMiddle(detail.run_id, 10)}</span></h2>
      </div>

      <IntegrityStatusCard state={integrity} runId={detail.run_id} />

      <ReproChecklistCard state={repro} runId={detail.run_id} />

      <div className="card detail-meta" data-testid="detail-meta">
        <table className="meta-table">
          <tbody>
            <tr><th>run_id</th><td><span className="mono" title={detail.run_id}>{detail.run_id}</span></td></tr>
            <tr><th>创建时间</th><td>{formatDateTime(meta.created_at) ?? "暂无"}</td></tr>
            <tr><th>状态</th><td>{meta.status}</td></tr>
            <tr><th>标的</th><td>{meta.symbol}</td></tr>
            <tr><th>策略</th><td>
              {meta.strategy_id ?? "暂无"}
              {meta.strategy_version ? ` (${meta.strategy_version})` : ""}
            </td></tr>
            <tr><th>策略参数</th><td>
              {meta.strategy_params
                ? <pre className="pre-json">{JSON.stringify(meta.strategy_params, null, 2)}</pre>
                : "暂无"}
            </td></tr>
            <tr><th>数据集</th><td>{meta.dataset_symbol}</td></tr>
            <tr><th>回测区间</th><td>
              {(meta.start_date ?? "—")} ~ {(meta.end_date ?? "—")}
            </td></tr>
            <tr><th>初始资金</th><td>{formatMoney(meta.initial_cash) ?? "暂无"}</td></tr>
            <tr><th>完整性</th><td>{boolLabel(meta.is_complete)}</td></tr>
            <tr><th>耗时</th><td>{meta.duration_ms !== null ? `${meta.duration_ms} ms` : "暂无"}</td></tr>
            <tr><th>source_sha256</th><td>
              {meta.source_sha256
                ? <span className="mono" title={meta.source_sha256}>{truncateMiddle(meta.source_sha256, 16)}</span>
                : "暂无"}
            </td></tr>
            <tr><th>dataset_digest</th><td>
              {meta.dataset_digest
                ? <span className="mono" title={meta.dataset_digest}>{truncateMiddle(meta.dataset_digest, 16)}</span>
                : "暂无"}
            </td></tr>
            <tr><th>result_digest</th><td>
              {meta.result_digest
                ? <span className="mono" title={meta.result_digest}>{truncateMiddle(meta.result_digest, 16)}</span>
                : "暂无"}
            </td></tr>
          </tbody>
        </table>
      </div>

      <div className="detail-result" data-testid="detail-result">
        <ResultView response={toBacktestResponse(detail)} />
      </div>
    </div>
  );
}
