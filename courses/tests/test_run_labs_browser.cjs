'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {Sessions, actions, loopback, outputPath, clipCheck} = require('../skills/run-labs/scripts/browser_session.cjs');
const {addRows, compactClip, number, visibleRowRect} = require('../skills/run-labs/scripts/grafana_capture.cjs');

test('display:contents rows use all cell bounds and reject partial visibility', () => {
  const saved = Object.fromEntries(['innerWidth', 'innerHeight', 'getComputedStyle'].map(k => [k, globalThis[k]]));
  globalThis.innerWidth = 1920; globalThis.innerHeight = 1080;
  globalThis.getComputedStyle = p => p.style;
  const rect = (left, top, width, height) => ({left, top, width, height, right: left + width, bottom: top + height});
  const panel = {parentElement: null, style: {overflowX: 'hidden', overflowY: 'hidden'}, getBoundingClientRect: () => rect(300, 150, 1500, 500)};
  const row = {parentElement: panel, style: {display: 'contents', overflowX: 'visible', overflowY: 'visible'}, getBoundingClientRect: () => rect(0, 0, 0, 0)};
  let boxes = [rect(320, 200, 450, 30), rect(770, 200, 450, 30), rect(1220, 200, 450, 30)];
  row.querySelectorAll = () => boxes.map(b => ({parentElement: row, getBoundingClientRect: () => b}));
  try {
    assert.deepEqual(visibleRowRect(row), {x: 320, y: 200, width: 1350, height: 30});
    boxes[2] = rect(1700, 200, 450, 30); assert.equal(visibleRowRect(row), null);
    boxes[2] = rect(1220, 640, 450, 30); assert.equal(visibleRowRect(row), null);
    boxes[2] = rect(1220, 200, 0, 0); assert.equal(visibleRowRect(row), null);
    boxes = []; assert.equal(visibleRowRect(row), null);
  } finally {
    for (const [k, v] of Object.entries(saved)) {if (v === undefined) delete globalThis[k]; else globalThis[k] = v;}
  }
});

test('one browser, one authentication per isolated service context', async () => {
  let launches = 0, credentials = 0; const contexts = [];
  const chromium = {launch: async () => {
    launches++;
    return {newContext: async options => {
      const page = {address: '', setDefaultTimeout() {}, async goto(url) {this.address = url;}, url() {return this.address;}, isClosed() {return false;}, async waitForFunction() {}};
      const context = {options, page, async route() {}, async newPage() {return page;}, async close() {}};
      contexts.push(context); return context;
    }, async close() {}};
  }};
  const services = Object.fromEntries(['systems', 'compute', 'grafana'].map((key, i) => [key, {url: `http://127.0.0.1:${3000 + i}/`} ]));
  const sessions = new Sessions({services}, chromium, () => {credentials++; return {username: 'test', password: 'private'};});
  const first = await sessions.get('systems'); assert.equal(await sessions.get('systems'), first);
  await sessions.get('compute'); await sessions.get('grafana');
  assert.equal(launches, 1); assert.equal(credentials, 3); assert.equal(contexts.length, 3);
  assert.equal(contexts[0].options.httpCredentials.origin, 'http://127.0.0.1:3000');
  assert(!Object.hasOwn(contexts[2].options, 'httpCredentials'));
  first.page.address = 'http://127.0.0.1:3000/login';
  await assert.rejects(sessions.get('systems'), /expired/);
  assert.equal(launches, 1); await sessions.close();
});

test('navigation batches do not inject a wait after each key and reject arbitrary actions', async () => {
  const keys = [], waits = [];
  const page = {keyboard: {press: async k => keys.push(k)}, waitForTimeout: async n => waits.push(n)};
  await actions(page, [{type: 'key', key: 'Home'}, {type: 'key', key: 'ArrowDown'}, {type: 'wait', ms: 250}]);
  assert.deepEqual(keys, ['Home', 'ArrowDown']); assert.deepEqual(waits, [250]);
  await assert.rejects(actions(page, [{type: 'evaluate', script: 'bad'}]), /Unknown/);
  await assert.rejects(actions(page, [{type: 'wait', ms: 60000}]), /Bounded/);
});

test('external origins, credentials in URL, symlinks and existing outputs are rejected', () => {
  for (const value of ['https://example.com', 'http://user:pass@localhost', 'file:///tmp/a']) assert.throws(() => loopback(value));
  assert.equal(loopback('http://127.0.0.1:1234').hostname, '127.0.0.1');
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'run-labs-test-')));
  try {
    fs.chmodSync(root, 0o700); const filename = path.join(root, 'shot.png');
    assert.equal(outputPath(root, filename), filename); fs.writeFileSync(filename, 'existing');
    assert.throws(() => outputPath(root, filename)); assert.throws(() => outputPath(root, path.join(root, '../escape.png')));
    fs.symlinkSync(filename, path.join(root, 'link.png')); assert.throws(() => outputPath(root, path.join(root, 'link.png')));
    assert.throws(() => clipCheck({x: 0, y: 0, width: 200, height: 200}, {width: 1920, height: 1080}));
  } finally {fs.rmSync(root, {recursive: true});}
});

test('compact capture contains every claimed row at native scale; clipped rows fail', () => {
  const row = {rect: {x: 970, y: 400, width: 900, height: 30}};
  assert.deepEqual(compactClip({x: 960, y: 0, width: 960}, [row], {width: 1920, height: 1080}), {x: 960, y: 0, width: 960, height: 446});
  assert.throws(() => compactClip({x: 960, y: 0, width: 960}, [{rect: {...row.rect, y: 1070}}], {width: 1920, height: 1080}), /hide/);
  assert.throws(() => compactClip({x: 960, y: 0, width: 960}, [{rect: null}], {width: 1920, height: 1080}), /visible/);
});

test('pagination deduplicates identical rows but rejects conflicts and unknown cases', () => {
  const collected = new Map(), row = {cells: ['value-0', '2 ms', '1 ms']};
  assert.equal(addRows(collected, [row], ['value-0', 'value-1']), 1);
  assert.equal(addRows(collected, [row], ['value-0', 'value-1']), 0);
  assert.equal(collected.size, 1); // Missing second case remains missing; no invented coverage.
  assert.throws(() => addRows(collected, [{cells: ['value-0', '9 ms', '1 ms']}], ['value-0']), /Conflicting/);
  assert.throws(() => addRows(collected, [{cells: ['value-9', '2 ms', '1 ms']}], ['value-0']), /Unexpected/);
});

test('formatted numeric conversion is strict', () => {
  assert.equal(number('2.5 ms', {unit: 's'}), 0.0025);
  assert.equal(number('2 MiB', {unit: 'bytes'}), 2097152);
  assert.throws(() => number('2 MB', {unit: 'bytes'}), /conversion/);
  assert.throws(() => number('No data', {unit: 's'}));
});

for (const mode of ['ready', 'blank', 'clipped', 'stale', 'incorrect']) test('qualification capture waits for visible current controls: ' + mode, async () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'run-labs-capture-test-')));
  fs.chmodSync(root, 0o700);
  const dashboard = {uid: 'example', templating: {list: [{name: 'profile', query: 'small,large'}]}};
  const controls = {'Selected comparison generation': [['value', '7']],
    'Correctness of selected results': [['value', 'baseline', '1'], ['value', 'candidate', '1']],
    'Slurm allocation': [['value', 'baseline', '10'], ['value', 'candidate', '11']]};
  let generationScrolls = 0, waits = 0, captured = 0;
  const page = {async goto() {}, on() {}, off() {}, viewportSize: () => ({width: 1920, height: 1080}),
    async waitForTimeout() {waits++;},
    getByText(title) {
      assert.notEqual(title, 'No data', 'Do not inspect unrelated optional panels as required data');
      return {title, async waitFor() {}};
    },
    locator: () => ({filter: ({has}) => ({async count() {return 1;}, async scrollIntoViewIfNeeded() {if (has.title === 'Selected comparison generation') generationScrolls++;},
      getByRole: () => ({async count() {return controls[has.title].length;}, nth: i => ({async evaluate() {
        if (generationScrolls > 1 && (waits < 2 || mode === 'blank')) return null;
        return {x: 320, y: mode === 'clipped' ? 900 : 200 + i * 40, width: 1400, height: 30};
      }, locator: () => ({async count() {return controls[has.title][i].length;}, async allInnerTexts() {
        const values = [...controls[has.title][i]];
        if (generationScrolls > 1 && ((mode === 'stale' && has.title === 'Selected comparison generation') ||
            (mode === 'incorrect' && has.title === 'Correctness of selected results'))) values[values.length - 1] = '0';
        return values;
      }})})})})})};
  const session = {page, origin: 'http://127.0.0.1:3000', context: {request: {get: async () => ({ok: () => true, json: async () => ({dashboard, meta: {folderUid: 'course'}})})}}};
  try {
    const q = {profile: 'small', lab: '01_example', generation: 7, from_ms: 1, to_ms: 2, prefix: 'qualification', metrics: [],
      overview_clip: {x: 310, y: 160, width: 1500, height: 530}, dashboard, folder_uid: 'course', workspace: 'test', results: {baseline: {job: 10}, candidate: {job: 11}}, output: path.join(root, 'observation.json')};
    const run = () => require('../skills/run-labs/scripts/grafana_capture.cjs').run(session, {output_root: root}, q, async () => {
      captured++; assert(waits >= 2); return {path: 'actual.png', sha256: 'a'.repeat(64)};
    });
    if (mode !== 'ready') {
      await assert.rejects(run(), /controls must be visible/);
      assert.equal(captured, 0); assert(!fs.existsSync(q.output));
    } else {
      const result = await run();
      assert.equal(captured, 1); assert.equal(result.screenshots.length, 1);
      assert.equal(result.screenshots[0].visible_controls.correctness.length, 2);
      assert.deepEqual(result.screenshots[0].numeric_checks, []);
      assert.equal(result.visual_review_pending, true);
    }
  } finally {fs.rmSync(root, {recursive: true});}
});
