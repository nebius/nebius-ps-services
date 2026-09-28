// Real Grafana inspector tables, compact at native scale, never composed charts.
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {sha, saveJson, clipCheck} = require('./browser_session.cjs');

function number(text, metric) {
  const m = text.trim().match(/^([-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(.*)$/);
  assert(m, 'Unexpected rendered numeric text');
  const defaults = {s: {ns: 1e-9, 'µs': 1e-6, 'μs': 1e-6, us: 1e-6, ms: 1e-3, s: 1}, none: {'': 1}, percent: {'%': 1},
    bytes: Object.fromEntries(['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB'].map((k, i) => [k, 1024 ** i]))};
  const scales = metric.display_scales || defaults[metric.unit];
  assert(scales && Object.hasOwn(scales, m[2]) && Number.isFinite(scales[m[2]]) && scales[m[2]] > 0, 'Declare exact displayed-unit conversion');
  const value = Number(m[1].replaceAll(',', '')) * scales[m[2]];
  assert(Number.isFinite(value)); return value;
}
function addRows(collected, rows, expectedCases, columns = ['case', 'Baseline', 'Candidate']) {
  let added = 0;
  for (const row of rows) {
    assert(row.cells.length === columns.length, 'Unexpected table columns');
    const key = row.cells[0];
    assert(expectedCases.includes(key), 'Unexpected rendered case');
    if (collected.has(key)) assert.deepEqual(collected.get(key), row.cells, 'Conflicting rendered case');
    else added++;
    collected.set(key, row.cells); row.case = key;
  }
  return added;
}
function visibleRowRect(el) {
  // Grafana's dashboard rows use display:contents; measure their actual cells.
  const cells = [...el.querySelectorAll('[role=gridcell],[role=cell]')];
  if (!cells.length) return null;
  const rects = [];
  for (const cell of cells) {
    const r = cell.getBoundingClientRect(); let left = 0, top = 0, right = innerWidth, bottom = innerHeight;
    for (let p = cell.parentElement; p; p = p.parentElement) {
      const s = getComputedStyle(p), b = p.getBoundingClientRect();
      if (['auto', 'scroll', 'hidden', 'clip'].includes(s.overflowY)) {top = Math.max(top, b.top); bottom = Math.min(bottom, b.bottom);}
      if (['auto', 'scroll', 'hidden', 'clip'].includes(s.overflowX)) {left = Math.max(left, b.left); right = Math.min(right, b.right);}
    }
    if (!(r.height > 0 && r.width > 0 && r.top >= top && r.bottom <= bottom && r.left >= left && r.right <= right)) return null;
    rects.push(r);
  }
  const x = Math.min(...rects.map(r => r.left)), y = Math.min(...rects.map(r => r.top));
  return {x, y, width: Math.max(...rects.map(r => r.right)) - x, height: Math.max(...rects.map(r => r.bottom)) - y};
}
async function rows(locator, visible = false) {
  const result = [], all = locator.getByRole('row');
  for (let i = 0; i < await all.count(); i++) {
    const row = all.nth(i);
    const rect = await row.evaluate(visibleRowRect);
    if (visible && !rect) continue;
    const cells = row.locator('[role=gridcell],[role=cell]');
    if (await cells.count()) result.push({cells: await cells.allInnerTexts(), rect});
  }
  return result;
}
function compactClip(dialog, visibleRows, viewport) {
  assert(visibleRows.length && visibleRows.every(r => r.rect), 'No fully visible rows');
  const x = Math.max(0, Math.floor(dialog.x)), y = Math.max(0, Math.floor(dialog.y));
  const right = Math.min(viewport.width, Math.ceil(dialog.x + dialog.width));
  const bottom = Math.min(viewport.height, Math.max(y + 360, ...visibleRows.map(r => Math.ceil(r.rect.y + r.rect.height + 16))));
  const clip = {x, y, width: right - x, height: bottom - y};
  assert(clip.width >= 640 && clip.height >= 360 && visibleRows.every(r => r.rect.x >= x && r.rect.y >= y && r.rect.x + r.rect.width <= right && r.rect.y + r.rect.height <= bottom), 'Compact clip would hide data');
  return clip;
}
async function captureComparison(session, config, q, capture) {
  const {page, context, origin} = session;
  assert(q.profile === 'small' || q.profile === 'large');
  assert(Number.isInteger(q.generation) && q.generation > 0 && Number.isFinite(q.from_ms) && Number.isFinite(q.to_ms) && q.from_ms < q.to_ms);
  assert(/^[a-zA-Z0-9._-]+$/.test(q.prefix) && Array.isArray(q.metrics));
  if (!q.metrics.length) clipCheck(q.overview_clip, page.viewportSize());
  const expected = structuredClone(q.dashboard);
  assert(expected && /^[a-zA-Z0-9_-]+$/.test(expected.uid) && q.folder_uid);
  const response = await context.request.get(origin + '/api/dashboards/uid/' + expected.uid, {maxRedirects: 0});
  assert(response.ok(), 'Dashboard API unavailable'); const live = await response.json();
  assert.equal(live.dashboard.uid, expected.uid); assert.equal(live.meta.folderUid, q.folder_uid);
  for (const x of expected.templating.list) {
    const y = live.dashboard.templating.list.find(v => v.name === x.name);
    if (!Object.hasOwn(x, 'options') && JSON.stringify(y?.options) === '[]') x.options = [];
  }
  for (const k of ['title', 'tags', 'panels', 'templating', 'annotations', 'links', 'time', 'timezone', 'refresh']) if (Object.hasOwn(expected, k)) assert.deepEqual(live.dashboard[k], expected[k], 'Effective dashboard differs');
  assert.equal(live.dashboard.templating.list.find(x => x.name === 'profile').query, 'small,large');
  const query = new URLSearchParams({'var-workspace': q.workspace, 'var-profile': q.profile, 'var-gpu_node': '$__all', 'var-gpu': '$__all', from: q.from_ms, to: q.to_ms});
  await page.goto(origin + '/d/' + expected.uid + '?' + query, {waitUntil: 'domcontentloaded'});
  await page.getByText('Selected comparison generation', {exact: true}).waitFor({timeout: 30000});
  async function panel(title) {
    const loc = page.locator('section').filter({has: page.getByText(title, {exact: true})});
    if (await loc.count()) {await loc.scrollIntoViewIfNeeded(); return loc;}
    await page.mouse.move(1400, 840); await page.mouse.wheel(0, -10000);
    for (let i = 0; i < 20; i++) {
      if (await loc.count()) {await loc.scrollIntoViewIfNeeded(); return loc;}
      await page.mouse.wheel(0, 450); await page.waitForTimeout(200);
    }
    throw Error('Panel did not render');
  }
  async function control(title, check) {
    const loc = await panel(title);
    for (let i = 0; i < 30; i++) {
      const values = (await rows(loc)).map(r => r.cells);
      if (check(values)) return values;
      await page.waitForTimeout(500);
    }
    throw Error('Selected comparison control differs');
  }
  const evidence = {schema: 'run-labs-grafana-observation/v1', lab: q.lab, profile: q.profile, dashboard_uid: expected.uid,
    generation: q.generation, api_definition_verified: true, live_dashboard_sha256: sha(JSON.stringify(live.dashboard)),
    time_range: {from_ms: q.from_ms, to_ms: q.to_ms}, controls: {}, screenshots: [], panels: [], visual_review_pending: true};
  evidence.controls.generation = await control('Selected comparison generation', r => r.length === 1 && Number(r[0].at(-1)) === q.generation);
  for (const [title, key] of [['Correctness of selected results', 'correctness'], ['Slurm allocation', 'jobs']]) {
    evidence.controls[key] = await control(title, r => r.length === 2 && ['baseline', 'candidate'].every(slot => {
      const row = r.find(x => x.includes(slot)); return row && Number(row.at(-1).replaceAll(',', '')) === (key === 'correctness' ? 1 : q.results[slot].job);
    }));
  }
  for (const metric of q.metrics) {
    const loc = await panel(metric.title); await loc.hover();
    await loc.locator('button[title="Menu"]').click(); await page.getByText('Inspect', {exact: true}).hover(); await page.getByText('Data', {exact: true}).click();
    const dialog = page.getByRole('dialog'); await dialog.waitFor(); await page.getByText('Data options', {exact: true}).click();
    const transform = dialog.locator('input[type=checkbox]').first();
    if (!await transform.isChecked()) await dialog.locator('label[for="' + await transform.getAttribute('id') + '"]').click();
    assert(await transform.isChecked() && await dialog.locator('#formatted-data-toggle').isChecked());
    const table = dialog.getByRole('table');
    await table.getByRole('columnheader').first().waitFor();
    const columns = await table.getByRole('columnheader').allInnerTexts();
    const slots = ['baseline', 'candidate'].filter(slot => metric.expected.some(row => Object.hasOwn(row.values, slot)));
    assert.equal(columns[0], 'case');
    assert(columns.every(c => ['case', 'Baseline', 'Candidate'].includes(c)) && new Set(columns).size === columns.length);
    assert(slots.every(slot => columns.includes(slot[0].toUpperCase() + slot.slice(1))), 'Required comparison column missing');
    const expectedCases = metric.expected.map(x => x.display_case);
    const collected = new Map(), captures = [];
    for (let index = 0; index < 40; index++) {
      const visible = await rows(table, true), added = addRows(collected, visible, expectedCases, columns);
      if (added) {
        const clip = compactClip(await dialog.boundingBox(), visible, page.viewportSize());
        const filename = path.join(config.output_root, q.prefix + '-' + metric.name.replaceAll('_', '-') + '-page' + (index + 1) + '.png');
        const shot = await capture(page, config, filename, clip);
        captures.push({...shot, cases: visible.map(r => metric.expected.find(e => e.display_case === r.case).case), visible_rows: visible.map(r => r.cells), clip});
      }
      if (collected.size === expectedCases.length) break;
      const b = await table.boundingBox(); assert(b);
      await page.mouse.move(Math.min(b.x + b.width - 25, 1870), Math.min(b.y + b.height - 30, 1000));
      await page.mouse.wheel(0, 400); await page.waitForTimeout(250);
    }
    assert.equal(collected.size, expectedCases.length, 'Not every metric case rendered');
    const checks = [];
    for (const entry of metric.expected) for (const [slot, value] of Object.entries(entry.values)) {
      const column = columns.indexOf(slot[0].toUpperCase() + slot.slice(1));
      const observed = number(collected.get(entry.display_case)[column], metric);
      assert(Math.abs(observed - value) <= Math.max(Math.abs(value) * 0.005, 1e-12), 'Rendered metric mismatch');
      checks.push({stage: q.results[slot].stage, result_index: q.results[slot].result_index, metric: metric.name, case: entry.case, unit: metric.unit, observed_base_units: observed});
    }
    // Each image carries only the checks for rows actually visible in its crop.
    for (const c of captures) c.numeric_checks = checks.filter(v => c.cases.includes(v.case));
    evidence.panels.push({metric: metric.name, numeric_checks: checks, captures});
    await page.keyboard.press('Escape'); await dialog.waitFor({state: 'hidden'});
  }
  if (!q.metrics.length) {
    const generation = await panel('Selected comparison generation');
    const correctness = page.locator('section').filter({has: page.getByText('Correctness of selected results', {exact: true})});
    // Scrolling remounts virtualized panels. Earlier controls do not prove that
    // the values are still rendered, or contained in the final screenshot.
    let visible;
    for (let i = 0; i < 30; i++) {
      const g = await rows(generation, true), c = await rows(correctness, true);
      const clip = q.overview_clip;
      if (g.length === 1 && Number(g[0].cells.at(-1)) === q.generation &&
          c.length === 2 && ['baseline', 'candidate'].every(slot => c.some(r => r.cells.includes(slot) && Number(r.cells.at(-1)) === 1)) &&
          [...g, ...c].every(({rect: r}) => r.x >= clip.x && r.y >= clip.y && r.x + r.width <= clip.x + clip.width && r.y + r.height <= clip.y + clip.height)) {
        visible = {generation: g, correctness: c}; break;
      }
      await page.waitForTimeout(500);
    }
    assert(visible, 'Qualification controls must be visible inside the overview clip');
    evidence.screenshots.push({...await capture(page, config, path.join(config.output_root, q.prefix + '-qualification.png'), q.overview_clip), numeric_checks: [], visible_controls: visible, clip: q.overview_clip});
  }
  return evidence;
}
async function run(session, config, q, capture) {
  const queries = [];
  const observe = response => {
    const u = new URL(response.url());
    if (u.origin !== session.origin || u.pathname !== '/api/ds/query') return;
    queries.push((async () => {
      assert(response.ok(), 'Grafana query request failed');
      const data = await response.json();
      assert(Object.values(data.results || {}).every(value => !value.error && (!value.status || value.status < 400)), 'Grafana query result failed');
    })());
    // Attach rejection immediately; propagate it through Promise.all below.
    queries.at(-1).catch(() => {});
  };
  session.page.on('response', observe);
  try {
    const evidence = await captureComparison(session, config, q, capture);
    await Promise.all(queries);
    saveJson(config.output_root, q.output, evidence);
    return evidence;
  } finally {session.page.off('response', observe);}
}
module.exports = {run, number, addRows, compactClip, visibleRowRect};
