import { defineConfig } from "@playwright/test";

// e2e сценария демо: бэкенд отдаёт API и собранный фронтенд (make e2e делает build).
const PORT = Number(process.env.E2E_PORT ?? 8765);

export default defineConfig({
  testDir: "e2e",
  use: { baseURL: `http://localhost:${PORT}` },
  webServer: {
    command: `cd ../backend && uv run --frozen uvicorn api.main:app --port ${PORT}`,
    url: `http://localhost:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
