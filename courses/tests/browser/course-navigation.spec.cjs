'use strict';

const {test, expect} = require('playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

const root = path.resolve(__dirname, '../..');
const courses = fs.readdirSync(root).filter(name =>
  fs.existsSync(path.join(root, name, 'reference/course.json'))
);
const pages = ['index.html', 'lab-guide.html', ...courses.map(name => `${name}/index.html`)];

for (const width of [1440, 390, 320]) {
  test.describe(`${width}px`, () => {
    test.use({viewport: {width, height: 1000}});

    for (const file of pages) {
      test(`${file} keyboard destinations`, async ({page}) => {
        await page.goto(pathToFileURL(path.join(root, file)).href);
        await page.keyboard.press('Tab');
        await expect(page.locator('.skip-link')).toBeFocused();

        for (const selector of [
          '.course-switcher > summary',
          'aside nav > details > summary',
        ]) {
          const control = page.locator(selector).first();
          if (await control.count()) {
            await control.focus();
            await control.press('Enter');
            await control.press('Enter');
          }
        }
        const disclosures = page.locator('aside details');
        for (let index = 0; index < await disclosures.count(); index++) {
          const disclosure = disclosures.nth(index);
          if (await disclosure.getAttribute('open') === null) {
            const summary = disclosure.locator(':scope > summary');
            await summary.focus();
            await summary.press('Enter');
          }
        }
        const destinations = await page.locator('aside a[href^="#"]').evaluateAll(links =>
          links.map(link => link.getAttribute('href')));

        for (const href of destinations) {
          const link = page.locator(`aside a[href="${href}"]`).first();
          // Rapid focus/Enter must not leave an older scroll travelling to the TOC.
          await link.focus();
          await expect(link).toBeFocused();
          await link.press('Enter');
          await expect.poll(() => page.evaluate(() => location.hash)).toBe(href);
          const section = page.locator(href);
          const heading = section.locator('h2,h3').first();
          await expect(await heading.count() ? heading : section).toBeInViewport();
        }
      });
    }
  });
}
