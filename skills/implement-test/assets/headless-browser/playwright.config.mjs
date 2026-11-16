import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
export default defineConfig({
  testDir: fileURLToPath(new URL('.', import.meta.url)),
  testMatch: 'acceptance.spec.mjs',
  workers: 1, retries: 0, timeout: 0,
  reporter: [['line']],
  outputDir: process.env.IMPLEMENT_BROWSER_OUTPUT + '/test-results',
});
