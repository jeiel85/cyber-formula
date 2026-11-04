// 스모크 테스트: 헤드리스 Chrome을 DevTools 프로토콜(CDP)로 조종해 뷰어를 띄우고 주요 기능을 눌러 본다.
// 의존성 없이 Node 내장 fetch/WebSocket만 쓴다(Node 22 이상).
//
//   node tools/smoke.mjs --url http://127.0.0.1:8420/ [--og assets/og.png] [--out shots/]
//                        [--chrome <경로>] [--angle d3d11|swiftshader] [--timeout 120000]
//
// 확인: 로딩 완료(오류 없음), 첫 카메라 이동 종료, 모드·캐노피·엔진 커버·에어 브레이크·부스트 상태 전환,
// 파츠 마커 → 설명 카드, 카드 닫기, 콘솔 오류·예외·리소스 로드 실패가 없을 것.
// --og를 주면 첫 화면을 1200×630으로 찍어 공유 미리보기 이미지로 저장한다(?quality=high로 띄우길 권장).
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const argv = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 ? argv[i + 1] : fallback;
};
const url = opt('url');
if (!url) {
  console.error('사용법: node tools/smoke.mjs --url <주소> [--og <png>] [--out <폴더>]');
  process.exit(2);
}
const ogPath = opt('og');
const outDir = opt('out');
const timeout = Number(opt('timeout', 120000));
const isWin = process.platform === 'win32';
const chrome = opt('chrome', process.env.CHROME_PATH || (isWin ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome'));
const angle = opt('angle', isWin ? 'd3d11' : 'swiftshader');
const [W, H] = ogPath ? [1200, 630] : [1280, 800];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const profile = mkdtempSync(join(tmpdir(), 'asurada-smoke-'));
const port = 9300 + Math.floor(Math.random() * 500);
const flags = ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  `--use-angle=${angle}`, '--hide-scrollbars', '--mute-audio', `--window-size=${W},${H}`];
if (angle === 'swiftshader') flags.push('--enable-unsafe-swiftshader');
else flags.push('--ignore-gpu-blocklist');
if (!isWin && process.env.CI) flags.push('--no-sandbox', '--disable-dev-shm-usage'); // CI 러너의 샌드박스 제한 회피
const proc = spawn(chrome, [...flags, 'about:blank'], { stdio: 'ignore' });

const failures = [];
const problems = []; // 콘솔 오류·예외·로드 실패
let ws;
let seq = 0;
const pending = new Map();
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});
const evaluate = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(`평가 실패: ${expression} → ${r.exceptionDetails.text}`);
  return r.result.value;
};
async function waitFor(expression, ms, label) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (await evaluate(expression)) return true;
    await sleep(250);
  }
  failures.push(`${label} (${ms / 1000}초 안에 만족하지 않음: ${expression})`);
  return false;
}
async function shot(name) {
  if (!outDir) return;
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(outDir, `${name}.png`), Buffer.from(data, 'base64'));
}
const click = (selector) => evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return false; el.click(); return true; })()`);

try {
  let target;
  for (let i = 0; i < 80 && !target; i++) {
    await sleep(250);
    try {
      target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === 'page');
    } catch { /* Chrome이 아직 뜨는 중 */ }
  }
  if (!target) throw new Error(`Chrome DevTools에 연결하지 못했습니다(${chrome}).`);
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.reject(new Error(`${m.error.message}`)) : p.resolve(m.result);
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') problems.push(`예외: ${m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text}`);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      problems.push(`console.error: ${m.params.args.map((a) => a.value ?? a.description).join(' ')}`);
    }
    // 웹폰트는 대체 글꼴로 동작하므로 외부 폰트 로드 실패는 실패로 세지 않는다.
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error' && !/fonts\.(googleapis|gstatic)\.com/.test(m.params.entry.url || '')) {
      problems.push(`로그: ${m.params.entry.text} ${m.params.entry.url || ''}`);
    }
  };
  await send('Runtime.enable');
  await send('Log.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url });

  // 1) 로딩
  const loaded = await waitFor(`(() => { const l = document.getElementById('loader'); return l && (l.classList.contains('done') || l.classList.contains('error')); })()`, timeout, '모델 로딩');
  if (loaded && await evaluate(`document.getElementById('loader').classList.contains('error')`)) {
    failures.push(`로딩 오류: ${await evaluate(`document.getElementById('loader-text').textContent`)}`);
  }
  if (loaded) await waitFor(`document.getElementById('stage').dataset.ready === 'true'`, timeout, '첫 카메라 이동 종료');
  await sleep(800);
  if (ogPath && failures.length === 0) {
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(ogPath, Buffer.from(data, 'base64'));
    console.log(`공유 미리보기 저장: ${ogPath}`);
  }
  if (outDir) mkdirSync(outDir, { recursive: true });
  await shot('01-hero');

  // 2) 조작
  if (failures.length === 0) {
    for (const [sel, label] of [['#sw-canopy', '캐노피'], ['#sw-engine', '엔진 커버'], ['#sw-airbrake', '에어 브레이크'], ['#sw-airflow', '기류 표시']]) {
      await click(sel);
      await waitFor(`document.querySelector('${sel}').getAttribute('aria-checked') === 'true'`, 3000, `${label} 켜기`);
    }
    for (const mode of ['aero', 'rally', 'circuit']) {
      await click(`[data-mode=${mode}]`);
      await waitFor(`document.querySelector('[data-mode=${mode}]').getAttribute('aria-pressed') === 'true'`, 3000, `${mode} 모드`);
    }
    await sleep(1500);
    await shot('02-open-aero');
    await click('#boost'); // 열린 캐노피·엔진 커버를 닫고 점화해야 한다
    await waitFor(`document.getElementById('boost').dataset.state === 'on'`, 8000, '부스트 점화');
    await waitFor(`document.getElementById('sw-canopy').getAttribute('aria-checked') === 'false' && document.getElementById('sw-engine').getAttribute('aria-checked') === 'false'`, 3000, '부스트 전 캐노피·엔진 커버 닫힘');
    await click('[data-view=rear]');
    await sleep(2500);
    await shot('03-boost-rear');
    await click('#boost');
    await waitFor(`['cooling', 'idle'].includes(document.getElementById('boost').dataset.state)`, 3000, '부스트 끄기');

    const label = await evaluate(`(() => { const m = document.querySelector('.marker[data-label="테일 핀 & 리어 윙"]') || document.querySelector('.marker'); m.click(); return m.dataset.label; })()`);
    await waitFor(`!document.getElementById('card').hidden && document.getElementById('card-title').textContent === ${JSON.stringify(label)}`, 3000, '마커 → 설명 카드');
    await sleep(1500);
    await shot('04-card');
    await click('#card-next');
    await waitFor(`document.getElementById('card-pos').textContent.startsWith(String(${JSON.stringify(label)} === '테일 핀 & 리어 윙' ? 14 : 2))`, 3000, '다음 파츠');
    await evaluate(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })), true`);
    await waitFor(`document.getElementById('card').hidden`, 3000, 'Esc로 카드 닫기');
  }
  failures.push(...problems);
} catch (err) {
  failures.push(`실행 실패: ${err.message}`);
} finally {
  try { ws?.close(); } catch { /* 이미 닫힘 */ }
  proc.kill();
  await sleep(300);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* Chrome이 아직 잡고 있으면 남겨 둔다 */ }
}

if (failures.length) {
  console.error(`스모크 테스트 실패 (${failures.length}건)`);
  for (const f of failures) console.error(` - ${f}`);
  process.exit(1);
}
console.log('스모크 테스트 통과');
