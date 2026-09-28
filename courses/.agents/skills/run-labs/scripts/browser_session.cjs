// Campaign-owned headless browser sessions; private IPC, never a CDP endpoint.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const revision = () => sha(JSON.stringify(['browser_session.cjs', 'grafana_capture.cjs', 'capture_browser.cjs'].map(name => [name, sha(fs.readFileSync(path.join(__dirname, name)))])));
function privatePath(filename, directory = false) {
  assert(path.isAbsolute(filename), 'Absolute private path required');
  for (let p = filename;; p = path.dirname(p)) {
    assert(!fs.lstatSync(p).isSymbolicLink(), 'Symlink not allowed');
    if (p === path.dirname(p)) break;
  }
  const s = fs.lstatSync(filename);
  assert(s.uid === process.getuid() && !(s.mode & 0o077), 'Owner-only path required');
  assert(directory ? s.isDirectory() : s.isFile(), 'Unexpected path type');
}
function readPrivate(filename) {
  privatePath(filename); assert(fs.statSync(filename).size <= 4 * 1024 * 1024, 'Request too large');
  return JSON.parse(fs.readFileSync(filename, 'utf8'));
}
function loopback(value) {
  const u = new URL(value);
  assert(['http:', 'https:'].includes(u.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname) && !u.username && !u.password, 'Only credential-free loopback URLs allowed');
  return u;
}
function outputPath(root, filename) {
  privatePath(root, true);
  assert(path.isAbsolute(filename) && path.dirname(filename) === root && !fs.existsSync(filename), 'Output must be a new direct child of private output_root');
  assert(/^[a-zA-Z0-9][a-zA-Z0-9._-]*\.(png|json)$/.test(path.basename(filename)), 'Unsafe output name');
  return filename;
}
function saveJson(root, filename, value) {
  fs.writeFileSync(outputPath(root, filename), JSON.stringify(value, null, 2) + '\n', {flag: 'wx', mode: 0o600});
}
function clipCheck(c, v) {
  assert(c && ['x', 'y', 'width', 'height'].every(k => Number.isFinite(c[k])) && c.x >= 0 && c.y >= 0 && c.width >= 640 && c.height >= 360 && c.x + c.width <= v.width && c.y + c.height <= v.height, 'Invalid readable clip');
}
async function capture(page, config, filename, clip) {
  outputPath(config.output_root, filename); clipCheck(clip, page.viewportSize());
  assert(!new URL(page.url()).pathname.includes('/login') && await page.locator('input[type=password]').count() === 0, 'Authentication screen cannot be captured');
  const bytes = await page.screenshot({clip, animations: 'disabled'});
  fs.writeFileSync(filename, bytes, {flag: 'wx', mode: 0o600});
  return {path: filename, sha256: sha(bytes)};
}
function credentials(spec) {
  if (!spec) return undefined;
  assert(Array.isArray(spec.argv) && spec.argv.length && path.isAbsolute(spec.argv[0]), 'Explicit credential reader required');
  let value;
  try { value = JSON.parse(execFileSync(spec.argv[0], spec.argv.slice(1), {encoding: 'utf8', timeout: 60000, maxBuffer: 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe']})); }
  catch { throw Error('Existing credential reader failed'); }
  const get = keys => keys.split('.').reduce((v, k) => v[k], value);
  const decode = v => spec.base64 ? Buffer.from(v, 'base64').toString() : v;
  const result = {username: decode(get(spec.username)), password: decode(get(spec.password))};
  assert(Object.values(result).every(v => typeof v === 'string' && v), 'Missing existing credential');
  return result;
}
async function actions(page, values) {
  assert(Array.isArray(values) && values.length <= 120, 'Bounded action batch required');
  for (const a of values) {
    if (a.type === 'click') {
      const v = page.viewportSize();
      assert(Number.isFinite(a.x) && Number.isFinite(a.y) && a.x >= 0 && a.y >= 0 && a.x < v.width && a.y < v.height, 'Click outside viewport');
      assert(!a.button || ['left', 'right'].includes(a.button), 'Invalid mouse button');
      await page.mouse.move(a.x, a.y);
      await page.mouse.click(a.x, a.y, {button: a.button || 'left', clickCount: a.double ? 2 : 1, delay: 80});
    } else if (a.type === 'key') {
      assert(typeof a.key === 'string' && a.key.length <= 80, 'Invalid key'); await page.keyboard.press(a.key);
    } else if (a.type === 'text') {
      assert(typeof a.text === 'string' && a.text.length <= 2048, 'Invalid text'); await page.keyboard.type(a.text, {delay: 5});
    } else if (a.type === 'wait') {
      assert(Number.isInteger(a.ms) && a.ms >= 0 && a.ms <= 10000, 'Bounded render wait required'); await page.waitForTimeout(a.ms);
    } else if (a.type === 'locator') {
      assert(typeof a.selector === 'string' && a.selector.length <= 512, 'Invalid locator'); await page.locator(a.selector).click();
    } else throw Error('Unknown browser action');
  }
}
class Sessions {
  constructor(config, chromium, resolveCredentials = credentials) {
    this.config = config; this.chromium = chromium; this.resolveCredentials = resolveCredentials;
    this.sessions = new Map(); this.browser = null;
  }
  async get(kind) {
    assert(['systems', 'compute', 'grafana'].includes(kind), 'Unknown service');
    const service = this.config.services[kind]; assert(service, 'Service not prepared');
    const url = loopback(service.url);
    if (this.sessions.has(kind)) {
      const old = this.sessions.get(kind);
      assert(!old.page.isClosed() && !new URL(old.page.url()).pathname.includes('/login'), 'Session expired; inspect before bounded recovery');
      return old;
    }
    if (!this.browser) {
      assert(!this.config.channel || ['chrome', 'chromium'].includes(this.config.channel), 'Unsupported prepared browser');
      this.browser = await this.chromium.launch({headless: true, ...(this.config.channel ? {channel: this.config.channel} : {})});
    }
    const auth = this.resolveCredentials(service.credentials);
    const context = await this.browser.newContext({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1,
      ...(kind !== 'grafana' && auth ? {httpCredentials: {...auth, origin: url.origin}} : {})});
    try {
      await context.route('**/*', route => new URL(route.request().url()).origin === url.origin ? route.continue() : route.abort());
      const page = await context.newPage(); page.setDefaultTimeout(15000);
      await page.goto(url.href, {waitUntil: 'domcontentloaded', timeout: 45000});
      if (kind === 'grafana' && new URL(page.url()).pathname.includes('/login')) {
        assert(auth, 'Grafana needs existing credentials');
        await page.locator('input[name=user]').fill(auth.username); await page.locator('input[name=password]').fill(auth.password);
        await page.getByRole('button', {name: 'Log in', exact: true}).click();
        await page.waitForURL(u => !u.pathname.includes('/login'), {timeout: 20000});
      }
      if (kind !== 'grafana') await page.waitForFunction(() => [...document.querySelectorAll('video')].some(v => v.videoWidth > 0 && v.readyState >= 2), {}, {timeout: 30000});
      const session = {context, page, origin: url.origin, report: null}; this.sessions.set(kind, session); return session;
    } catch (error) { await context.close(); throw error; }
  }
  async run(request) {
    if (request.operation === 'status') return {services: [...this.sessions.keys()], persistent: true};
    const s = await this.get(request.service);
    if (request.operation === 'grafana') {
      assert(request.service === 'grafana', 'Grafana needs its isolated context');
      return require('./grafana_capture.cjs').run(s, this.config, request, capture);
    }
    assert(['systems', 'compute'].includes(request.service) && ['open-report', 'inspect-report', 'close-report'].includes(request.operation), 'Unknown native operation');
    const r = request.report;
    assert(r && path.posix.isAbsolute(r.viewer_path) && /^[a-f0-9]{64}$/.test(r.sha256) && Number.isInteger(r.job) && r.job > 0 && typeof r.producer === 'string', 'Exact native report identity required');
    const identity = JSON.stringify(r);
    outputPath(this.config.output_root, request.output); clipCheck(request.clip, s.page.viewportSize());
    if (request.operation === 'open-report') {
      assert(!s.report, 'Close the prior owned report before opening another');
      assert(request.actions.some(a => a.type === 'text' && a.text === r.viewer_path), 'Open must type the exact viewer path');
      s.report = identity; // Reserve before input; uncertain navigation never auto-replays.
    } else assert(s.report === identity, 'Native report identity changed');
    await actions(s.page, request.actions);
    const shot = await capture(s.page, this.config, request.output, request.clip);
    if (request.operation === 'close-report') s.report = null;
    return {...shot, report: r, visual_review_pending: true};
  }
  async close() { if (this.browser) await this.browser.close(); this.sessions.clear(); }
}
async function serve(configFile) {
  const config = readPrivate(configFile);
  privatePath(config.output_root, true); privatePath(path.dirname(config.socket), true);
  assert(Buffer.byteLength(config.socket) < 100 && !fs.existsSync(config.socket), 'New short private socket path required');
  assert(path.isAbsolute(config.playwright_module), 'Prepared Playwright module path required');
  const sessions = new Sessions(config, require(config.playwright_module).chromium);
  const identity = {schema: 'run-labs-browser-session/v1', config_sha256: sha(fs.readFileSync(configFile)), runner_sha256: revision(), pid: process.pid};
  let busy = false;
  const server = net.createServer({allowHalfOpen: true}, socket => {
    socket.setEncoding('utf8'); socket.setTimeout(300000, () => socket.destroy());
    let data = '', received = false; socket.on('error', () => {});
    socket.on('data', async chunk => {
      if (received) return;
      data += chunk; if (Buffer.byteLength(data) > 4 * 1024 * 1024) return socket.destroy();
      if (!data.includes('\n')) return;
      received = true;
      if (busy) return socket.end(JSON.stringify({ok: false, error: 'Worker busy; no action taken'}) + '\n');
      busy = true;
      try {
        const r = JSON.parse(data); assert(r.config_sha256 === identity.config_sha256 && r.runner_sha256 === identity.runner_sha256, 'Worker configuration or revision differs');
        if (r.operation === 'stop') {await sessions.close(); socket.end(JSON.stringify({ok: true, stopped: true, identity}) + '\n'); server.close();}
        else socket.end(JSON.stringify({ok: true, identity, value: await sessions.run(r)}) + '\n');
      } catch {
        // Vendor errors can contain private URLs, typed text or credentials.
        socket.end(JSON.stringify({ok: false, error: 'Browser gate failed; inspect current state and private request. No automatic retry.'}) + '\n');
      } finally {busy = false;}
    });
  });
  await new Promise((resolve, reject) => {server.once('error', reject); server.listen(config.socket, resolve);});
  fs.chmodSync(config.socket, 0o600); process.stdout.write(JSON.stringify({ready: true, identity}) + '\n');
  const stop = async () => {await sessions.close(); server.close();};
  process.once('SIGTERM', stop); process.once('SIGINT', stop);
}
async function request(configFile, requestFile) {
  const config = readPrivate(configFile), value = readPrivate(requestFile);
  privatePath(path.dirname(config.socket), true);
  const st = fs.lstatSync(config.socket);
  assert(st.isSocket() && st.uid === process.getuid() && !(st.mode & 0o077), 'Private worker socket required');
  value.config_sha256 = sha(fs.readFileSync(configFile));
  value.runner_sha256 = revision();
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(config.socket); let output = '';
    socket.setEncoding('utf8'); socket.setTimeout(300000, () => socket.destroy(Error('Worker response timed out; reconcile before retry')));
    socket.once('connect', () => socket.end(JSON.stringify(value) + '\n'));
    socket.on('data', chunk => {output += chunk; if (output.length > 8 * 1024 * 1024) socket.destroy(Error('Response too large'));});
    socket.once('error', reject); socket.once('end', () => {try {resolve(JSON.parse(output));} catch {reject(Error('Lost worker response; reconcile before retry'));}});
  });
}
module.exports = {Sessions, actions, clipCheck, loopback, outputPath, capture, saveJson, sha, readPrivate, serve, request};
