/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * 后端 API 基地址（可选）。
   * - 未设置：走 Vite 开发代理（/api → http://127.0.0.1:8123，见 vite.config.ts）；
   * - 设置示例：VITE_API_BASE_URL=http://127.0.0.1:8123（直连，绕过代理）。
   */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
