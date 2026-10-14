'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

module.exports = {
  testDir: __dirname,
  testMatch: 'course-navigation.spec.cjs',
  outputDir: fs.mkdtempSync(path.join(os.tmpdir(), 'course-navigation-')),
  timeout: 30000,
  workers: 1,
  reporter: 'list',
  use: {
    browserName: 'chromium',
    channel: 'chrome',
    headless: true,
    javaScriptEnabled: false,
    trace: 'retain-on-failure',
  },
};
