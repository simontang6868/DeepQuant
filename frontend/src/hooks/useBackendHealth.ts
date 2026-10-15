/**
 * 后端健康检查 Hook：启动时检查一次，不自动轮询（避免无意义的持续请求）；
 * 失败后由用户点击「重试」再次检查。
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, getHealth, toApiError } from "../api/client";

export type HealthStatus = "checking" | "ok" | "down";

export interface BackendHealth {
  status: HealthStatus;
  error: ApiError | null;
  lastCheckedAt: string | null;
}

export function useBackendHealth(): { health: BackendHealth; check: () => Promise<void> } {
  const [health, setHealth] = useState<BackendHealth>({
    status: "checking",
    error: null,
    lastCheckedAt: null,
  });

  const check = useCallback(async () => {
    setHealth((prev) => ({ ...prev, status: "checking", error: null }));
    const checkedAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    try {
      const h = await getHealth();
      if (h.status === "ok") {
        setHealth({ status: "ok", error: null, lastCheckedAt: checkedAt });
      } else {
        setHealth({
          status: "down",
          error: new ApiError("invalid_response", `/health 返回了非预期状态：${h.status}`),
          lastCheckedAt: checkedAt,
        });
      }
    } catch (err) {
      setHealth({ status: "down", error: toApiError(err), lastCheckedAt: checkedAt });
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  return { health, check };
}
