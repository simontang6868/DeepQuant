/**
 * 数据质量与回测限制提示：
 * - is_complete = false → 顶部突出错误横幅 + incomplete_reasons；
 * - data_quality_issues / limitations 如实列出；
 * - performance 层面的不完整与不可用指标也在此提示。
 */

import type { BacktestResult } from "../api/types";

export function DataQualityNotice({ result }: { result: BacktestResult }) {
  const perf = result.performance;
  return (
    <div data-testid="data-quality-notice">
      {result.is_complete === false && (
        <div className="banner banner-error" role="alert" data-testid="incomplete-banner">
          <strong>注意：本次回测结果不完整（is_complete = false），以下结论可能不可靠。</strong>
          {result.incomplete_reasons.length > 0 && (
            <ul>
              {result.incomplete_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {result.data_quality_issues.length > 0 && (
        <div className="banner banner-warn" role="alert" data-testid="dq-issues">
          <strong>数据质量问题提示（data_quality_issues）：</strong>
          <ul>
            {result.data_quality_issues.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {result.limitations.length > 0 && (
        <div className="banner banner-neutral" data-testid="limitations">
          <strong>回测限制说明（limitations）：</strong>
          <ul>
            {result.limitations.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {perf !== null && !perf.is_complete && perf.incomplete_reasons.length > 0 && (
        <div className="banner banner-warn" role="alert">
          <strong>绩效指标不完整（performance.is_complete = false）：</strong>
          <ul>
            {perf.incomplete_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
