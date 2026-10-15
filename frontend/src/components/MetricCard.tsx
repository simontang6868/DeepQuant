/**
 * 指标卡片：只展示传入的真实值；null/undefined 显示“暂无数据”，
 * 0 是有效数值照常展示（不与缺失混淆）。
 */

interface MetricCardProps {
  label: string;
  /** 已格式化的展示值；null → 暂无数据 */
  value: string | null;
  /** 指标的简短中文解释 */
  explanation?: string;
}

export function MetricCard({ label, value, explanation }: MetricCardProps) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      {value === null ? (
        <div className="metric-value muted">暂无数据</div>
      ) : (
        <div className="metric-value">{value}</div>
      )}
      {explanation ? <div className="metric-explain">{explanation}</div> : null}
    </div>
  );
}
