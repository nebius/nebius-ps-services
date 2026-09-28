#!/usr/bin/env node
// Internal entry point; the public skill interface remains unchanged.
'use strict';
const worker = require('./browser_session.cjs');
const [op, config, input] = process.argv.slice(2);
if (op === '--help' || op === '-h') {
  process.stdout.write('Internal browser worker: serve PRIVATE_CONFIG | request PRIVATE_CONFIG PRIVATE_REQUEST\n');
} else (async () => {
  if (op === 'serve' && config && !input) await worker.serve(config);
  else if (op === 'request' && input) {
    const result = await worker.request(config, input);
    process.stdout.write(JSON.stringify(result) + '\n');
    if (!result.ok) process.exitCode = 2;
  } else throw Error('Invalid internal worker arguments');
})().catch(() => {
  process.stderr.write('Browser worker failed; inspect private configuration and existing session.\n');
  process.exitCode = 2;
});
