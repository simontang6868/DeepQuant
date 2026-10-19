/**
 * 结果导出条：一组「点一下就下载」的按钮 + 统一的状态反馈。
 *
 * 它自己不知道要导出什么——表格怎么拼、图表去哪找，都由调用方决定。
 * 这样单标的和组合两套结构完全不同的结果可以共用同一套交互与错误提示，
 * 不会各自长出一份「点完没反应」的实现。
 *
 * 关键约定：
 * - 导出失败必须**说出来**。浏览器下载会被拦截、无头环境没有 canvas，
 *   静默失败会让用户以为已经存好了；
 * - 并发点击只保留最后一次反馈，不做排队（导出是本地行为，快到不需要队列）。
 */

import { useState } from "react";
import type { ExportAction } from "../utils/export";

export type { ExportAction };

interface ResultExportBarProps {
  /** 归档文件名前缀（含标的/策略/区间/时间戳） */
  baseName: string;
  actions: ExportAction[];
  /** 按钮下方的说明，用于交代口径 */
  note?: string;
}

type Feedback = { kind: "ok" | "error"; text: string } | null;

export function ResultExportBar({ baseName, actions, note }: ResultExportBarProps) {
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);

  const handleClick = async (action: ExportAction) => {
    setBusyKey(action.key);
    setFeedback(null);
    try {
      await action.run();
      setFeedback({ kind: "ok", text: `已导出：${baseName}_${action.key}` });
    } catch (error) {
      setFeedback({
        kind: "error",
        text: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div className="card" data-testid="result-export-bar">
      <h2>导出结果</h2>
      <div className="export-actions">
        {actions.map((action) => (
          <button
            key={action.key}
            type="button"
            className="btn"
            onClick={() => void handleClick(action)}
            disabled={busyKey !== null}
            data-testid={`export-${action.key}`}
          >
            {busyKey === action.key ? "导出中…" : action.label}
          </button>
        ))}
      </div>
      {feedback && (
        <p
          className={`export-feedback ${feedback.kind === "error" ? "export-feedback-error" : ""}`}
          role={feedback.kind === "error" ? "alert" : "status"}
          data-testid="export-feedback"
        >
          {feedback.text}
        </p>
      )}
      {note && <p className="chart-note">{note}</p>}
    </div>
  );
}
