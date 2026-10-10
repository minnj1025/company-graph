import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // 3D 라이브러리는 거의 바뀌지 않는다. 따로 묶어 두면 화면 코드를 고쳐 배포해도 방문자의 브라우저가 이 묶음은 다시 받지 않는다
        manualChunks(id) {
          if (id.includes("node_modules/three")) return "three";
          if (id.includes("node_modules")) return "vendor";
        },
      },
    },
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    // 화면은 /api 로만 부른다. 개발 중에는 FastAPI(8000)로 넘긴다
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
