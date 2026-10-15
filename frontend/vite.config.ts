/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 后端本地开发端口：前端开发代理默认把 /api 转发到 http://127.0.0.1:8123
// （不修改后端、无需后端配置 CORS）。如后端使用其他端口，请同步修改此处，
// 或在 frontend/.env.local 中设置 VITE_API_BASE_URL=http://127.0.0.1:<端口> 直连。
const BACKEND_DEV_TARGET = "http://127.0.0.1:8123";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: BACKEND_DEV_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: false,
  },
});
