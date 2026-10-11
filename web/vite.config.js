import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Hiring site. `npm run dev` proxies the API to `python main.py hire --no-browser` (port 8000).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
