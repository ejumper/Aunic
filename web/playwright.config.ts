import { defineConfig, devices } from "@playwright/test";

const host = "127.0.0.1";
const port = 4173;
const wsUrl = "ws://127.0.0.1:8766/ws";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  workers: 1,
  expect: {
    timeout: 5_000,
  },
  use: {
    baseURL: `http://${host}:${port}`,
    trace: "on-first-retry",
  },
  webServer: {
    command:
      `VITE_AUNIC_ALLOW_PROD_WS_URL=true VITE_AUNIC_WS_URL=${wsUrl} ` +
      `npm run build && npm run preview -- --host ${host}`,
    url: `http://${host}:${port}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "Mobile Safari",
      use: { ...devices["iPhone 14"] },
    },
  ],
});
