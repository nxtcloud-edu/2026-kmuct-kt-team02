import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  // S3 정적 호스팅에 dist/를 그대로 올린다. 상대 경로로 빌드해야 하위 경로 배포에서도 자원을 찾는다.
  base: "./",
  build: { outDir: "dist", sourcemap: false },
  server: { port: 5173 },
});
