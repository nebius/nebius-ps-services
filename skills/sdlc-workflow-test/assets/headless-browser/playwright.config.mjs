import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  testDir: fileURLToPath(new URL('.', import.meta.url)),
  testMatch: 'acceptance.spec.mjs',
  workers: 1,
  retries: 0,
  timeout: 60_000,
  globalTimeout: 280_000,
  forbidOnly: true,
  reporter: [['json', { outputFile: process.env.SDLC_BROWSER_OUTPUT + '/test-results.json' }]],
  outputDir: process.env.SDLC_BROWSER_OUTPUT + '/test-artifacts',
});
