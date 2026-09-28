import { test, expect, chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Independent verifier oracle: never copied into the product's TDD test tree.
const input = JSON.parse(fs.readFileSync(process.env.SDLC_BROWSER_INPUT, 'utf8'));
const output = process.env.SDLC_BROWSER_OUTPUT;
const observations = {
  schema: 'agentic-sdlc/browser-observations-v1',
  verification_id: input.verification_id,
  stage: input.stage,
  attempt_id: input.attempt_id,
  headless: true,
  browser: 'chrome',
  actions: [],
  screenshots: [],
};

function save() {
  fs.writeFileSync(path.join(output, 'observations.json'), JSON.stringify(observations, null, 2), { mode: 0o600 });
}
function observed(action) {
  observations.actions.push({ action, at: new Date().toISOString() });
  save();
}

async function checkpoint(name, record) {
  const request = path.join(output, 'request-' + name + '.json');
  fs.writeFileSync(request + '.pending', JSON.stringify(record), { mode: 0o600 });
  fs.renameSync(request + '.pending', request);
  await expect.poll(() => fs.existsSync(path.join(output, 'response-' + name + '.json')), { timeout: 20_000 }).toBe(true);
  const response = JSON.parse(fs.readFileSync(path.join(output, 'response-' + name + '.json'), 'utf8'));
  expect(response.ok).toBe(true);
}

test('owned browser acceptance', async () => {
  const server = await chromium.launchServer({ channel: 'chrome', headless: true });
  const process = server.process();
  fs.writeFileSync(path.join(output, 'browser-process.json'), JSON.stringify({
    pid: process.pid,
    args: process.spawnargs,
  }), { mode: 0o600 });
  let browser;
  let context;
  try {
    browser = await chromium.connect(server.wsEndpoint());
    observations.browser_version = browser.version();
    context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
    const page = await context.newPage();
    page.setDefaultTimeout(10_000);
    page.setDefaultNavigationTimeout(30_000);
    const screenshot = async (name) => {
      const filename = name + '.png';
      await page.screenshot({ path: path.join(output, filename), fullPage: true });
      observations.screenshots.push(filename);
      observed('screenshot:' + name);
    };

    // The capability probe changes no product-owned state.
    if (input.stage === 'capability-discovery') {
      await page.setContent(`<main><h1>${input.verification_id}</h1><button>Verify readiness</button><output></output></main>`);
      await page.getByRole('button', { name: 'Verify readiness' }).evaluate(button => {
        button.addEventListener('click', () => document.querySelector('output').textContent = 'Ready');
      });
      await page.getByRole('button', { name: 'Verify readiness' }).click();
      await expect(page.locator('output')).toHaveText('Ready');
      observed('capability-dom-interaction');
      await screenshot('capability');
      return;
    }

    // Local acceptance cannot make external requests, including redirects/CDNs.
    const origin = new URL(input.endpoint).origin;
    await context.route('**/*', route => new URL(route.request().url()).origin === origin
      ? route.continue() : route.abort('blockedbyclient'));
    await page.goto(input.endpoint);
    await expect(page.getByRole('heading', { name: 'Task board', exact: true })).toBeVisible();
    observed('open-loopback-url');
    if (input.stage === 'uat-after-restart') {
      const record = input.record;
      await page.getByRole('button', { name: 'Completed', exact: true }).click();
      const row = page.locator(`[data-task-id="${record.id}"]`);
      await expect(row).toContainText(record.title);
      await expect(row.getByRole('checkbox', { name: 'Complete task', exact: true })).toBeChecked();
      observations.record = record;
      await checkpoint('post-restart', record);
      observed('observe-post-restart-persistence');
      await screenshot('post-restart');
      return;
    }

    await expect(page.locator('[data-task-id]')).toHaveCount(0);
    observed('observe-empty-state');
    await screenshot('empty-state');
    await page.getByRole('button', { name: 'Add task', exact: true }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.locator('[data-task-id]')).toHaveCount(0);
    observed('submit-blank-title');
    await checkpoint('blank', null);
    observed('verify-no-database-row');
    await screenshot('validation-error');
    const title = 'SDLC ' + input.verification_id + ' ' + input.attempt_id;
    await page.getByRole('textbox', { name: 'Task title', exact: true }).fill(title);
    await page.getByRole('button', { name: 'Add task', exact: true }).click();
    const row = page.locator('[data-task-id]').filter({ hasText: title });
    await expect(row).toHaveCount(1);
    const id = await row.getAttribute('data-task-id');
    expect(id).toMatch(/^[1-9][0-9]*$/);
    observations.record = { id, title, completed: false };
    observed('create-unique-task');
    observed('observe-created-task');
    await screenshot('created-task');
    await checkpoint('created', observations.record);
    observed('correlate-api-database');
    await page.reload();
    await expect(page.locator(`[data-task-id="${id}"]`)).toContainText(title);
    observed('refresh-and-observe-persistence');
    await page.locator(`[data-task-id="${id}"]`).getByRole('checkbox', { name: 'Complete task', exact: true }).check();
    observations.record.completed = true;
    observed('complete-task');
    await checkpoint('completed', observations.record);
    observed('correlate-completed-state');
    await page.getByRole('button', { name: 'Active', exact: true }).click();
    await expect(page.locator(`[data-task-id="${id}"]`)).toHaveCount(0);
    observed('filter-active');
    await page.getByRole('button', { name: 'Completed', exact: true }).click();
    await expect(page.locator(`[data-task-id="${id}"]`)).toContainText(title);
    observed('filter-completed');
    await screenshot('completed-filter');
  } finally {
    try {
      if (context) await context.tracing.stop({ path: path.join(output, 'trace.zip') });
    } finally {
      try { if (context) await context.close(); } finally {
        try { if (browser) await browser.close(); } finally { await server.close(); }
      }
    }
    observed('close-browser');
  }
});
