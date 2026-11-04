// GitHub Pages용 정적 사이트를 dist/에 만든다.
//   SITE_URL=https://<계정>.github.io/<저장소> node tools/build-pages.mjs
// SITE_URL은 공유 미리보기(og:image)에 절대 주소가 필요해서 받는다. 없으면 상대 경로로 둔다.
import { copyFile, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { wrapPage } from './page-shell.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const dist = `${root}dist/`;
const site = (process.env.SITE_URL || '').replace(/\/+$/, '');

const title = 'Super Asurada 01 Garage';
const desc = '『신세기 GPX 사이버 포뮬러』 슈퍼 아스라다 01을 브라우저에서 돌려 보는 팬메이드 3D 뷰어. 파츠를 눌러 설명을 보고 캐노피·부스트·에어로/랠리 모드를 조작해 보세요.';
const abs = (path) => (site ? `${site}/${path}` : path);

const head = [
  `<meta name="description" content="${desc}">`,
  '<meta name="theme-color" content="#06080c">',
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
const fragment = await readFile(`${root}web/index.html`, 'utf8');
await writeFile(`${dist}index.html`, wrapPage(fragment, { head }));
for (const [from, to] of [
  ['web/app.js', 'app.js'],
  ['web/asurada.gltf.json', 'asurada.gltf.json'],
  ['web/favicon.svg', 'favicon.svg'],
  ['assets/og.png', 'og.png'],
]) {
  await copyFile(`${root}${from}`, `${dist}${to}`);
}
console.log(`dist/ 생성 완료${site ? ` (SITE_URL=${site})` : ' (SITE_URL 없음: og:image가 상대 경로)'}`);
