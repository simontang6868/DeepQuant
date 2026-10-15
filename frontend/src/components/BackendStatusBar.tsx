/**
 * 顶栏后端连接状态：紧凑指示 + 未连接时的可操作提示。
 * 不伪造健康状态：checking/ok/down 三态均来自真实 /health 结果。
 */

import { API_BASE_URL, IS_PROXY_MODE, describeApiError } from "../api/client";
import type { BackendHealth } from "../hooks/useBackendHealth";

export function BackendStatusIndicator({ health }: { health: BackendHealth }) {
  if (health.status === "checking") {
    return (
      <span className="backend-status" role="status">
        <span className="status-dot checking" aria-hidden />
        正在检查后端连接…
      </span>
    );
  }
  if (health.status === "ok") {
    return (
      <span className="backend-status" role="status">
        <span className="status-dot ok" aria-hidden />
        后端已连接
        {health.lastCheckedAt ? `（${health.lastCheckedAt} 检查）` : ""}
      </span>
    );
  }
  return (
    <span className="backend-status" role="status">
      <span className="status-dot down" aria-hidden />
      后端未连接
    </span>
  );
}

export function BackendDownBanner({
  health,
  onRetry,
}: {
  health: BackendHealth;
  onRetry: () => void;
}) {
  if (health.status !== "down") return null;
  const displayAddress = IS_PROXY_MODE
    ? `${API_BASE_URL}（Vite 开发代理 → http://127.0.0.1:8123）`
    : API_BASE_URL;
  return (
    <div className="banner banner-error" role="alert" data-testid="backend-down-banner">
      <strong>无法连接后端服务。</strong>
      <div>
        当前后端地址：<span className="mono">{displayAddress}</span>
        {health.error ? `（${describeApiError(health.error)}）` : ""}
      </div>
      <div>
        请先启动后端：在 backend 目录执行
        <code> .\.venv\Scripts\python.exe -m uvicorn deepquant.api.app:app --port 8123 </code>
        （前端开发代理默认转发到 8123 端口；如后端使用其他端口，请修改
        frontend/vite.config.ts 的代理目标，或设置 VITE_API_BASE_URL 直连）。
      </div>
      <button type="button" className="btn" onClick={onRetry} data-testid="retry-backend">
        重试连接
      </button>
    </div>
  );
}
