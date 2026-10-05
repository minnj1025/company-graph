import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 화면은 /api 로만 부른다. 개발 중에는 FastAPI(8000)로 넘긴다
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
