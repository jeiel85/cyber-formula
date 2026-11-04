// 로컬 미리보기 서버. web/ 폴더를 서빙하고, index.html은 GitHub Pages 빌드와 같은
// 문서 뼈대(tools/page-shell.mjs)로 감싸서 보낸다.
//   node tools/serve.mjs [포트]   → http://localhost:8420
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { wrapPage } from './page-shell.mjs';

const root = resolve(fileURLToPath(new URL('../web/', import.meta.url)));
const port = Number(process.argv[2] || process.env.PORT || 8420);
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
    if (file.endsWith(`${sep}index.html`)) data = Buffer.from(wrapPage(data.toString('utf8'), { head: '<link rel="icon" href="favicon.svg" type="image/svg+xml">' }));
    res.writeHead(200, { 'content-type': types[extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
    res.end(data);
  } catch (err) {
    const code = err.code === 'ENOENT' ? 404 : err.code === 'EACCES' ? 403 : 500;
    res.writeHead(code, { 'content-type': 'text/plain; charset=utf-8' });
    res.end(`${code}`);
  }
}).listen(port, '127.0.0.1', () => console.log(`Super Asurada 01 Garage → http://localhost:${port}`));
