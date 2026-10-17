import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server only. Requests to /api go to the Python book, which reads SQLite.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
