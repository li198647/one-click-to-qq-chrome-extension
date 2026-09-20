/* 生成一个能在普通网页里打开的弹窗预览页（tools/preview-popup.html）。
 *
 * 目的：改完弹窗想看排版，但又不想每次都去 chrome://extensions 点重载、
 * 复制内容、再点图标。这里直接拿**真实的** popup.html + popup.js，
 * 只把 chrome.* 接口打桩喂假数据 —— 所以样式和渲染逻辑跟真身是同一份，
 * 不会出现"预览好看、实际不一样"的情况。
 *
 * 用法（PowerShell）： node tools\make-preview.js
 * 生成物 tools/preview-popup.html 已在 .gitignore 里，属于本地开发产物。
 */

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const ext = path.join(root, 'extension');

const now = Date.now();

/* 一份"看起来像真实使用"的假数据：4 条候选、第 1 条深色选中。 */
const mock = {
  preview: {
    ok: true,
    max: 4,
    note: '',
    stats: { '网页复制': 7, '触发快照': 5 },
    list: [
      { source: '选中文字', text: '无需API Key，27 个平台一键同步，支持网页转小程序', preview: '无需API Key，27 个平台一键…', totalChars: 27, truncated: true },
      { source: '剪贴板历史', text: '2700+ Star 的开源项目，社区维护活跃', preview: '2700+ Star 的开源项目，社区…', totalChars: 22, truncated: true },
      { source: '剪贴板历史', text: '10+ 平台', preview: '10+ 平台', totalChars: 8, truncated: false },
      { source: '剪贴板历史', text: '支持网页转小程序', preview: '支持网页转小程序', totalChars: 8, truncated: false }
    ]
  },
  health: { ok: true, data: { bot_ready: true, has_openid: true, send_ok: 13, send_fail: 0 } },
  last: { ok: true, last: { ok: true, at: now - 120000, source: '剪贴板' } }
};

const stub = `
window.chrome = {
  runtime: {
    getManifest: function () { return { version: '0.1.5' }; },
    sendMessage: function (msg) {
      var table = ${JSON.stringify(mock)};
      if (msg.type === 'get-preview') return Promise.resolve(table.preview);
      if (msg.type === 'get-health') return Promise.resolve(table.health);
      if (msg.type === 'get-last') return Promise.resolve(table.last);
      return Promise.resolve({ ok: true });
    }
  }
};
/* 让弹窗那条"在弹窗里读剪贴板"的路径也走通，否则底部会多出一条
   "关掉弹窗再点一次"的提示，看起来像有问题。 */
try {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { readText: function () { return Promise.resolve('无需API Key，27 个平台一键同步，支持网页转小程序'); } }
  });
} catch (e) { /* 打桩失败也不影响看排版 */ }
`;

let html = fs.readFileSync(path.join(ext, 'popup.html'), 'utf8');
const popupJs = fs.readFileSync(path.join(ext, 'popup.js'), 'utf8');

/* 先注入桩，再放真实的 popup.js —— 顺序不能反。 */
html = html.replace(
  '<script src="popup.js"></script>',
  '<script>' + stub + '</script>\n  <script>' + popupJs + '</script>'
);

/* 预览页要给弹窗留出同样的宽度，外面套一层浅灰底便于看边界。 */
html = html.replace(
  '</head>',
  '  <style>html{background:#EDEDEA}body{margin:24px auto!important;box-shadow:0 1px 4px rgba(0,0,0,.12);border-radius:10px;overflow:hidden}</style>\n</head>'
);
if (!/<title>/.test(html)) {
  html = html.replace('</head>', '  <title>弹窗预览（打桩版）</title>\n</head>');
}

const out = path.join(__dirname, 'preview-popup.html');
fs.writeFileSync(out, html, 'utf8');
console.log('preview written to ' + out);
