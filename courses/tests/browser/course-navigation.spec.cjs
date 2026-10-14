'use strict';

const {test, expect} = require('playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

const root = path.resolve(__dirname, '../..');
const courses = fs.readdirSync(root).filter(name =>
  fs.existsSync(path.join(root, name, 'reference/course.json'))
);
const pages = ['lab-guide.html', ...courses.map(name => `${name}/index.html`)];

for (const width of [1440, 390, 320]) {
  test.describe(`${width}px`, () => {
    test.use({viewport: {width, height: 1000}});

    for (const file of pages) {
      test(`${file} keyboard destinations`, async ({page}) => {
        await page.goto(pathToFileURL(path.join(root, file)).href);
        await page.keyboard.press('Tab');
        await expect(page.locator('.skip-link')).toBeFocused();

        const menu = page.locator('.course-switcher > summary');
        await menu.focus();
        await menu.press('Enter');
        await menu.press('Enter');
        const contents = page.locator('aside nav > details > summary').first();
        await contents.focus();
        await contents.press('Enter');
        await contents.press('Enter');

        const destinations = file === 'lab-guide.html'
          ? await page.locator('aside a[href^="#"]').evaluateAll(links =>
            links.map(link => link.getAttribute('href')))
          : ['#next-steps', '#guide-glossary', '#official-references'];

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
