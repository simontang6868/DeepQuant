/**
 * 可复现性与技术信息（折叠面板）。
 *
 * 语义必须区分（遵守后端字段含义）：
 * - result.dataset.digest：对「规范化后的完整输入数据」计算的确定性摘要
 *   （处理后数据摘要），不是原始 CSV 文件指纹；
 * - 数据集列表里的 source_sha256：导入时原始 CSV 文件的 SHA-256。
 */

import type { ApiInfo, BacktestResult } from "../api/types";
import { boolLabel, formatDate, formatInteger } from "../utils/format";

export function ReproducibilityPanel({
  result,
  api,
}: {
  result: BacktestResult;
  api: ApiInfo;
}) {
  const r = result.reproducibility;
  return (
    <details className="panel" data-testid="reproducibility-panel">
      <summary>可复现性与技术信息（版本 / 数据摘要 / 判定）</summary>
      <div className="details-body">
        <h3>可复现性判定（reproducibility）</h3>
        <ul className="kv-list">
          <li>
            <span className="k">输入可识别</span>
            <span>{boolLabel(r?.inputs_identifiable)}</span>
          </li>
          <li>
            <span className="k">过程可复现</span>
            <span>{boolLabel(r?.process_reproducible)}</span>
          </li>
          {r && r.missing.length > 0 && (
            <li>
              <span className="k">缺失项</span>
              <span>{r.missing.join("、")}</span>
            </li>
          )}
          {r && r.notes.length > 0 && (
            <li>
              <span className="k">说明</span>
              <span>{r.notes.join("；")}</span>
            </li>
          )}
        </ul>

        <h3>数据集摘要（result.dataset）</h3>
        <p className="hint">
          digest 是对规范化后的完整输入数据（列顺序 / 日期格式 / 数值表示统一后）计算的确定性
          摘要，用于识别输入数据是否变化；它不是导入时原始 CSV 文件的指纹（后者见数据集列表的
          source_sha256）。
        </p>
        <ul className="kv-list">
          <li>
            <span className="k">处理后数据摘要</span>
            <span className="mono">{result.dataset?.digest ?? "暂无"}</span>
          </li>
          <li>
            <span className="k">摘要算法</span>
            <span>{result.dataset?.digest_algorithm ?? "暂无"}</span>
          </li>
          <li>
            <span className="k">处理后台数</span>
            <span>{result.dataset ? formatInteger(result.dataset.n_rows) : "暂无"}</span>
          </li>
          <li>
            <span className="k">数据日期范围</span>
            <span>
              {result.dataset?.date_range
                ? `${formatDate(result.dataset.date_range.start) ?? "?"} ~ ${
                    formatDate(result.dataset.date_range.end) ?? "?"
                  }`
                : "暂无"}
            </span>
          </li>
          <li>
            <span className="k">数据列</span>
            <span>{result.dataset?.columns?.join(", ") ?? "暂无"}</span>
          </li>
        </ul>

        <h3>策略与版本信息</h3>
        <ul className="kv-list">
          <li>
            <span className="k">API</span>
            <span>
              {api.name} v{api.version}
            </span>
          </li>
          <li>
            <span className="k">结果 schema 版本</span>
            <span>{result.schema_version}</span>
          </li>
          <li>
            <span className="k">引擎版本</span>
            <span>{result.engine_version}</span>
          </li>
          <li>
            <span className="k">策略标识</span>
            <span>
              {result.strategy?.name ?? "暂无"}
              {result.strategy?.version ? ` v${result.strategy.version}` : ""}
              （标识完整：{boolLabel(result.strategy?.identified)}；参数可序列化：
              {boolLabel(result.strategy?.params_available)}）
            </span>
          </li>
          {result.strategy && result.strategy.notes.length > 0 && (
            <li>
              <span className="k">策略说明</span>
              <span>{result.strategy.notes.join("；")}</span>
            </li>
          )}
        </ul>

        <details>
          <summary>原始 JSON（dataset / strategy / reproducibility）</summary>
          <pre className="pre-json">
            {JSON.stringify(
              {
                dataset: result.dataset,
                strategy: result.strategy,
                reproducibility: result.reproducibility,
              },
              null,
              2,
            )}
          </pre>
        </details>
      </div>
    </details>
  );
}
