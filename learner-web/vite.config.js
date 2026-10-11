import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Learner dashboard (desktop app later). `npm run dev` proxies the API to `python main.py web --no-browser` (port 8001).
export default defineConfig({
  plugins: [react()],
  server: { port: 5174, proxy: { "/api": "http://127.0.0.1:8001" } },
});
