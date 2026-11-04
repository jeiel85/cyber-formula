// GitHub Pages용 정적 사이트를 dist/에 만든다.
//   npm ci && SITE_URL=https://<계정>.github.io/<저장소> node tools/build-pages.mjs
// - three.js를 node_modules에서 dist/vendor/로 복사하고 import map을 그쪽으로 돌린다(CDN이 막혀도 뜨도록).
//   app.js가 쓰는 애드온과 그 의존 파일만 따라가며 복사한다.
// - 모델은 meshopt 압축 GLB(web/asurada.glb)를 쓰도록 <meta name="asurada-model">을 넣는다.
// - SITE_URL은 공유 미리보기(og:image)에 절대 주소가 필요해서 받는다. 없으면 상대 경로로 둔다.
import { copyFile, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { wrapPage } from './page-shell.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const dist = join(root, 'dist');
const site = (process.env.SITE_URL || '').replace(/\/+$/, '');
const threeDir = join(root, 'node_modules', 'three');

const title = 'Super Asurada 01 Garage';
const desc = '『신세기 GPX 사이버 포뮬러』 슈퍼 아스라다 01을 브라우저에서 돌려 보는 팬메이드 3D 뷰어. 파츠를 눌러 설명을 보고 캐노피·엔진 커버·부스트·에어로/랠리 모드를 조작해 보세요.';
const abs = (path) => (site ? `${site}/${path}` : path);

// ---------------------------------------------------------------- three.js 자체 호스팅

if (!existsSync(join(threeDir, 'package.json'))) {
  throw new Error('node_modules/three가 없습니다. 먼저 `npm ci`를 실행하세요.');
}
const threeVersion = JSON.parse(await readFile(join(threeDir, 'package.json'), 'utf8')).version;
const CDN = `https://cdn.jsdelivr.net/npm/three@${threeVersion}/`;
let fragment = await readFile(join(root, 'web', 'index.html'), 'utf8');
if (!fragment.includes(CDN)) {
  // import map과 설치된 버전이 어긋나면 로컬·아티팩트(CDN)와 Pages(자체 호스팅)가 다른 three를 쓰게 된다.
  throw new Error(`web/index.html의 import map이 three@${threeVersion}을 가리키지 않습니다. package.json과 맞추세요.`);
}

const IMPORT_RE = /(?:import|export)\s+(?:[^'"]*?\sfrom\s+)?['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
const specifiers = (src) => [...src.matchAll(IMPORT_RE)].map((m) => m[1] || m[2]);

function resolveSpecifier(spec, fromFile) {
  if (spec === 'three') return join(threeDir, 'build', 'three.module.js');
  if (spec.startsWith('three/addons/')) return join(threeDir, 'examples', 'jsm', spec.slice('three/addons/'.length));
  if (spec.startsWith('.') && fromFile) return resolve(dirname(fromFile), spec);
  return null; // 외부 URL 등은 따라가지 않는다
}

const vendorFiles = new Set();
const queue = specifiers(await readFile(join(root, 'web', 'app.js'), 'utf8'))
  .map((s) => resolveSpecifier(s, null)).filter(Boolean);
while (queue.length) {
  const file = queue.pop();
  if (vendorFiles.has(file)) continue;
  if (!file.startsWith(threeDir + sep)) throw new Error(`three 패키지 밖의 파일을 가져옵니다: ${file}`);
  vendorFiles.add(file);
  for (const spec of specifiers(await readFile(file, 'utf8'))) {
    const next = resolveSpecifier(spec, file);
    if (next) queue.push(next);
  }
}

// ---------------------------------------------------------------- 페이지

fragment = fragment.replaceAll(CDN, './vendor/three/');
const head = [
  `<meta name="description" content="${desc}">`,
  '<meta name="theme-color" content="#06080c">',
  '<meta name="asurada-model" content="asurada.glb">',
  '<link rel="icon" href="favicon.svg" type="image/svg+xml">',
  '<meta property="og:type" content="website">',
  `<meta property="og:title" content="${title}">`,
  `<meta property="og:description" content="${desc}">`,
  site ? `<meta property="og:url" content="${site}/">` : '',
  `<meta property="og:image" content="${abs('og.png')}">`,
  '<meta property="og:image:width" content="1200">',
  '<meta property="og:image:height" content="630">',
  '<meta property="og:locale" content="ko_KR">',
  '<meta name="twitter:card" content="summary_large_image">',
].filter(Boolean).join('\n');

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await writeFile(join(dist, 'index.html'), wrapPage(fragment, { head }));
for (const [from, to] of [
  ['web/app.js', 'app.js'],
  ['web/asurada.glb', 'asurada.glb'],
  ['web/favicon.svg', 'favicon.svg'],
  ['assets/og.png', 'og.png'],
]) {
  await copyFile(join(root, from), join(dist, to));
}
for (const file of vendorFiles) {
  const to = join(dist, 'vendor', 'three', relative(threeDir, file));
  await mkdir(dirname(to), { recursive: true });
  await copyFile(file, to);
}
console.log(`dist/ 생성 완료 · three@${threeVersion} 파일 ${vendorFiles.size}개 자체 호스팅`
  + (site ? ` · SITE_URL=${site}` : ' · SITE_URL 없음: og:image가 상대 경로'));
