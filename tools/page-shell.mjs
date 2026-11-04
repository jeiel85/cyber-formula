// web/index.html은 claude.ai 아티팩트 형식(문서 뼈대 없이 본문만)이다.
// 로컬 미리보기와 GitHub Pages 빌드는 이 함수로 같은 뼈대를 씌운다.
// 첫 </style>까지(제목·메타·폰트·스타일)는 <head>로, 나머지는 <body>로 보낸다.
export function wrapPage(fragment, { head = '' } = {}) {
  const cut = fragment.indexOf('</style>');
  const headPart = cut >= 0 ? fragment.slice(0, cut + '</style>'.length) : '';
  const bodyPart = cut >= 0 ? fragment.slice(cut + '</style>'.length) : fragment;
  return `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>
${head}
${headPart.trim()}
</head>
<body>
${bodyPart.trim()}
</body>
</html>
`;
}
