import { test, expect, chromium } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

const input = JSON.parse(await fs.readFile(process.env.IMPLEMENT_BROWSER_INPUT, 'utf8'));
const output = process.env.IMPLEMENT_BROWSER_OUTPUT;

test('owned task board browser acceptance', async () => {
  let browser, context;
  const checks = [];
  let record = input.record;
  const result = { ...input, checks, cleanup: false, browser_version: '', status: 'FAIL' };
  try {
    try { browser = await chromium.launch({ channel: 'chrome', headless: true }); }
    catch (error) { result.status = 'PARTIAL'; throw error; }
    result.browser_version = browser.version();
    context = await browser.newContext({ serviceWorkers: 'block' });
    await context.routeWebSocket('**', socket => socket.close());
    await context.route('**/*', route => {
      const request = new URL(route.request().url());
      return request.origin === new URL(input.url).origin ? route.continue() : route.abort();
    });
    await context.tracing.start({ screenshots: true, snapshots: true });
    const page = await context.newPage();
    await page.goto(input.url);
    await expect(page.getByRole('heading', { name: 'Task board', exact: true })).toBeVisible();
    checks.push('frontend');
    if (input.phase === 'before-restart') {
      const title = 'Implement ' + input.generation_id + ' ' + input.attempt_id;
      await page.getByRole('textbox', { name: 'Task title', exact: true }).fill(title);
      await page.getByRole('button', { name: 'Add task', exact: true }).click();
      const row = page.locator('[data-task-id]').filter({ hasText: title });
      await expect(row).toHaveCount(1);
      const id = await row.getAttribute('data-task-id');
      expect(id).toMatch(/^[1-9][0-9]*$/);
      record = { id: Number(id), title, completed: true };
      checks.push('create');
      const checkbox = row.getByRole('checkbox', { name: 'Complete task', exact: true });
      const [response] = await Promise.all([
        page.waitForResponse(response => {
          const url = new URL(response.url());
          return url.origin === new URL(input.url).origin && url.pathname === `/api/tasks/${id}`
            && response.request().method() === 'PATCH';
        }),
        checkbox.check(),
      ]);
      expect(response.ok()).toBeTruthy();
      await expect(checkbox).toBeChecked();
      checks.push('update');
    }
    await page.reload();
    const persisted = page.locator(`[data-task-id="${record.id}"]`);
    await expect(persisted).toContainText(record.title);
    await expect(persisted.getByRole('checkbox', { name: 'Complete task', exact: true })).toBeChecked();
    checks.push('reload');
    await page.screenshot({ path: path.join(output, 'page.png') });
    result.record = record;
    result.status = 'PASS';
  } finally {
    try {
      if (context) await context.tracing.stop({ path: path.join(output, 'trace.zip') });
    } finally {
      try { if (context) await context.close(); }
      finally {
        if (browser) await browser.close();
        result.cleanup = true;
        await fs.writeFile(path.join(output, 'assertions.json'), JSON.stringify(result));
      }
    }
  }
});
