import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

// Synthetic verifier regressions, separate from application acceptance stages.
export default defineConfig({
  testDir: fileURLToPath(new URL('.', import.meta.url)),
  testMatch: 'completion-control.spec.mjs',
  workers: 1,
  retries: 0,
  timeout: 10_000,
  globalTimeout: 60_000,
  expect: { timeout: 2_000 },
  forbidOnly: true,
  use: { channel: 'chrome', headless: true },
});
