import { defineConfig } from "@playwright/test";

// e2e сценария демо: бэкенд отдаёт API и собранный фронтенд (make e2e делает build).
const PORT = Number(process.env.E2E_PORT ?? 8765);

export default defineConfig({
  testDir: "e2e",
  // Reduced motion: no typing effect and no row animations, the steps do not wait for them.
  // Cold start: the first agent reply (catalog warm-up) can exceed the default 5 s (#602).
  expect: { timeout: 20_000 },
  use: { baseURL: `http://localhost:${PORT}`, reducedMotion: "reduce" },
  webServer: {
    // Live sources off, as in backend/tests/conftest.py: the scenario sees the same synthetic
    // hotels and trains with or without network (#569).
    command: `cd ../backend && LIVE_HOTELS=0 LIVE_TRAINS=0 uv run --frozen uvicorn api.main:app --port ${PORT}`,
    url: `http://localhost:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
