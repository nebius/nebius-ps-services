import { test, expect } from '@playwright/test';
import { completeTask } from './completion-control.mjs';

// This models a save-confirmation boundary; it does not certify an application,
// API or database. Each test gets a fresh Playwright context and an empty page.
async function fixture(page, options = {}) {
  await page.setContent('<li data-task-id="1"><input type="checkbox" aria-label="Complete task"></li>');
  await page.evaluate(({ delayed = false, remount = false, never = false,
    wrongRow = false, completed = false }) => {
    window.saved = { '1': completed, '2': false };
    window.requests = 0;
    const control = document.querySelector('input');
    control.checked = completed;
    control.addEventListener('change', () => {
      window.requests += 1;
      control.checked = window.saved['1'];
      control.disabled = true;
      const confirm = () => {
        window.saved[wrongRow ? '2' : '1'] = true;
        const updated = remount ? control.cloneNode() : control;
        updated.checked = window.saved['1'];
        updated.disabled = false;
        if (remount) control.replaceWith(updated);
      };
      if (!never) {
        // Fixture-owned response latency, never an oracle sleep or action retry.
        if (delayed) setTimeout(confirm, 500);
        else confirm();
      }
    });
  }, options);
}

test('immediate confirmation requires one action', async ({ page }) => {
  await fixture(page);
  await completeTask(page, '1');
  expect(await page.evaluate(() => window.saved['1'])).toBe(true);
  expect(await page.evaluate(() => window.requests)).toBe(1);
});

for (const remount of [false, true]) {
  test(`delayed saved state is accepted with remount=${remount}`, async ({ page }) => {
    await fixture(page, { delayed: true, remount });
    await completeTask(page, '1');
    expect(await page.evaluate(() => window.saved['1'])).toBe(true);
    expect(await page.evaluate(() => window.requests)).toBe(1);
  });
}

test('a save that never confirms is rejected without retrying the action', async ({ page }) => {
  await fixture(page, { never: true });
  await expect(completeTask(page, '1')).rejects.toThrow();
  expect(await page.evaluate(() => window.saved['1'])).toBe(false);
  expect(await page.evaluate(() => window.requests)).toBe(1);
});

test('confirmation for another record cannot satisfy the selected control', async ({ page }) => {
  await fixture(page, { wrongRow: true });
  await expect(completeTask(page, '1')).rejects.toThrow();
  expect(await page.evaluate(() => window.saved['1'])).toBe(false);
  expect(await page.evaluate(() => window.saved['2'])).toBe(true);
  expect(await page.evaluate(() => window.requests)).toBe(1);
});

test('an already-completed precondition is rejected without mutation', async ({ page }) => {
  await fixture(page, { completed: true });
  await expect(completeTask(page, '1')).rejects.toThrow();
  expect(await page.evaluate(() => window.requests)).toBe(0);
});
