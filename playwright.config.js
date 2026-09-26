import { defineConfig, devices } from "@playwright/test";

const PORT = 18799;

export default defineConfig({
  testDir: "./tests/web",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "uv run tbot-web",
    env: { TBOT_ROOT: "tests/fixtures/unit_repo", TBOT_WEB_PORT: String(PORT), TBOT_WEB_TOKEN: "" },
    url: `http://127.0.0.1:${PORT}/api/control/units`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
