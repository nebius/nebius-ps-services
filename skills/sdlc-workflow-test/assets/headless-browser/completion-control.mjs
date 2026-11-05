import { expect } from '@playwright/test';

// Shared by the frozen acceptance oracle and its isolated control regressions.
export async function completeTask(page, id) {
  const control = page.locator(`[data-task-id="${id}"]`)
    .getByRole('checkbox', { name: 'Complete task', exact: true });
  await expect(control).not.toBeChecked();
  await control.click();
  // A saved-state control may stay unchecked until asynchronous confirmation.
  // Re-resolve the locator after rendering; never repeat the mutation.
  await expect(control).toBeChecked();
}
