// 로컬 미리보기 서버(의존성 없음).
//   node tools/serve.mjs [포트]          web/을 서빙하고, index.html은 Pages 빌드와 같은 문서 뼈대로 감싼다
//   node tools/serve.mjs --dist [포트]   빌드 결과 dist/를 그대로 서빙한다(스모크 테스트용)
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { wrapPage } from './page-shell.mjs';

const args = process.argv.slice(2);
const distMode = args.includes('--dist');
const root = resolve(fileURLToPath(new URL(distMode ? '../dist/' : '../web/', import.meta.url)));
const port = Number(args.find((a) => /^\d+$/.test(a)) || process.env.PORT || 8420);
const types = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.glb': 'model/gltf-binary',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.json': 'application/json',
};

createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    let rel = decodeURIComponent(url.pathname);
    if (rel.endsWith('/')) rel += 'index.html';
    const file = normalize(join(root, rel));
    if (!file.startsWith(root + sep)) throw Object.assign(new Error('forbidden'), { code: 'EACCES' });
    let data = await readFile(file);
    if (!distMode && file.endsWith(`${sep}index.html`)) {
      data = Buffer.from(wrapPage(data.toString('utf8'), { head: '<link rel="icon" href="favicon.svg" type="image/svg+xml">' }));
    }
    res.writeHead(200, { 'content-type': types[extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
    res.end(data);
  } catch (err) {
    const code = err.code === 'ENOENT' ? 404 : err.code === 'EACCES' ? 403 : 500;
    res.writeHead(code, { 'content-type': 'text/plain; charset=utf-8' });
    res.end(`${code}`);
  }
}).listen(port, '127.0.0.1', () => console.log(`Super Asurada 01 Garage (${distMode ? 'dist' : 'web'}) → http://localhost:${port}`));
