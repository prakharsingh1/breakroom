import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python = process.env.BREAKROOM_TEST_PYTHON || path.join(root, '.venv-api/bin/python');
const pythonCommand = "'" + python.replaceAll("'", "'\\''") + "'";
const team = process.env.BREAKROOM_TEAM_E2E === '1';
export default defineConfig({
  testDir: '../../tests/e2e',
  testIgnore: team ? undefined : ['**/team.spec.ts', '**/customer-workspace.spec.ts', '**/accounts.spec.ts'],
  outputDir: '../../output/playwright/test-results',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:3000', actionTimeout: 15_000, browserName: 'chromium', launchOptions: process.env.BREAKROOM_BROWSER_EXECUTABLE ? { executablePath: process.env.BREAKROOM_BROWSER_EXECUTABLE } : undefined, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: [
    ...(process.env.CI ? [{ command: pythonCommand + ' -m uvicorn breakroom_api.team:app --app-dir apps/api --host 127.0.0.1 --port 8001 --workers 1 --no-proxy-headers --no-access-log', cwd: root, url: 'http://127.0.0.1:8001/api/team/health', timeout: 30_000, env: { BREAKROOM_TEAM_DEV_LOGIN: '1', BREAKROOM_TEAM_PUBLIC_ORIGIN: 'http://127.0.0.1:3000', BREAKROOM_TEAM_ENV: 'test', BREAKROOM_TEAM_DB_SCHEMA: 'browser_tests', BREAKROOM_TEAM_RATE_LIMIT: '1000' } }] : []),
    { command: pythonCommand + ' -m uvicorn breakroom_api.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers', cwd: root, url: 'http://127.0.0.1:8000/health', reuseExistingServer: !process.env.CI, timeout: 30_000 },
    { command: 'npm run start', url: 'http://127.0.0.1:3000', reuseExistingServer: !process.env.CI, timeout: 30_000 }
  ]
});
