/* 生成能在普通网页里打开的弹窗预览页（tools/preview-popup.html）。
 *
 * 目的：改完弹窗想看排版，但又不想每次都去 chrome://extensions 点重载、
 * 复制内容、再点图标。这里直接拿**真实的** popup.html + popup.js，
 * 只把 chrome.* 接口打桩喂假数据 —— 所以样式和渲染逻辑跟真身是同一份，
 * 不会出现"预览好看、实际不一样"的情况。
 *
 * 一次生成五种状态并排的索引页（正常 / 长文本 / 出错 / 连不上桥 / 正在启动），
 * 目的是能一眼看全"改完版式之后各种情况下长什么样"。
 * v1.0.4 加的后两个是那个「启动本地桥」按钮的两种出现时机。
 *
 * 用法（bash 或 PowerShell 都行）： node tools/make-preview.js
 * 生成物 tools/preview-*.html 已在 .gitignore 里，属于本地开发产物。
 */

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const ext = path.join(root, 'extension');

/* 版本号从真实 manifest 读，别硬编码 —— 否则预览页会谎报版本。 */
const mf = JSON.parse(fs.readFileSync(path.join(ext, 'manifest.json'), 'utf8'));
const now = Date.now();

const HEALTH = { ok: true, data: { bot_ready: true, has_openid: true, send_ok: 14, send_fail: 0 } };
const NO_BRIDGE = { ok: false, error: 'TypeError: Failed to fetch' };
const LAST = { ok: true, last: { ok: true, at: now - 120000, source: '剪贴板' } };

const LIST_OK = [
  { source: '选中文字', text: 'qq好友', preview: 'qq好友', totalChars: 4, truncated: false },
  { source: '剪贴板历史', text: '将要发送 · 来自选中文字', preview: '将要发送 · 来自选中文字', totalChars: 14, truncated: false },
  { source: '剪贴板历史', text: '2700+ Star', preview: '2700+ Star', totalChars: 11, truncated: false },
  { source: '剪贴板历史', text: 'Markdown', preview: 'Markdown', totalChars: 8, truncated: false }
];

const STATES = [
  {
    key: 'ok',
    label: '① 正常 · 短文本',
    note: '和最常用的那种情况一样。状态行里没有按钮 —— v1.0.4 的按钮只在该出现时出现',
    clip: 'qq好友',
    height: 620,
    preview: {
      ok: true, max: 4, note: '',
      stats: { '切回页面': 1, '网页复制': 4, '页面剪贴板': 1 },
      list: LIST_OK
    }
  },
  {
    key: 'long',
    label: '② 长文本 · 只显示开头',
    note: '框里 20 字 + …（「共 N 字」那行已去掉）',
    clip: 'https://item.taobao.com/item.htm?id=123456789012345678',
    height: 620,
    preview: {
      ok: true, max: 4, note: '',
      stats: { '网页复制': 7, '触发快照': 5 },
      list: [
        { source: '剪贴板', text: 'https://item.taobao.com/item.htm?id=123456789012345678', preview: 'https://item.taobao.com/item…', totalChars: 650, truncated: true },
        { source: '剪贴板历史', text: '商品已下架，客服说换这个链接', preview: '商品已下架，客服说换这个链…', totalChars: 14, truncated: true },
        { source: '剪贴板历史', text: '483920', preview: '483920', totalChars: 6, truncated: false },
        { source: '剪贴板历史', text: 'https://www.zhihu.com/question/123456', preview: 'https://www.zhihu.com/que…', totalChars: 36, truncated: true }
      ]
    }
  },
  {
    key: 'err',
    label: '③ 出错 · 原因并入红标题',
    note: '「共 N 字」整行去掉后，原因改写在标题里，排查信息没丢',
    clip: '',
    height: 420,
    preview: {
      ok: false,
      restricted: true,
      reason: '这个页面发不了',
      detail: 'chrome:// 开头的页面、扩展商店、新标签页，Chrome 不允许扩展读取选中文字和标题。',
      list: [], note: '', stats: {}
    }
  },
  {
    key: 'nobridge',
    label: '④ 连不上桥 · 按钮出现',
    note: 'v1.0.4 新增。桥没在跑时，状态行右边才多出这个按钮；点它就由宿主把桥拉起来',
    clip: 'qq好友',
    height: 620,
    health: NO_BRIDGE,
    preview: {
      ok: true, max: 4, note: '',
      stats: { '网页复制': 2 },
      list: LIST_OK
    }
  },
  {
    key: 'starting',
    label: '⑤ 正在启动 · 按钮禁用',
    note: '点下去之后的真实过程：按钮变「正在启动…」并禁用，下面按 1.5 秒一轮去问桥起来没有',
    clip: 'qq好友',
    height: 620,
    health: NO_BRIDGE,
    launch: { ok: true, data: { ok: true, action: 'launching', pid: 12345, listening: false } },
    /* 加载完自动点一下 —— 走的是**真实**的点击处理函数，不是把字面值改上去 */
    afterLoad: "setTimeout(function(){ var b=document.getElementById('bridgeBtn'); if(b) b.click(); }, 400);",
    preview: {
      ok: true, max: 4, note: '',
      stats: { '网页复制': 2 },
      list: LIST_OK
    }
  }
];

/* 把 chrome.* 打桩成喂假数据的版本。先注入桩，再放真实的 popup.js。 */
function buildStub(st) {
  const table = {
    preview: st.preview,
    health: st.health || HEALTH,
    last: LAST,
    launch: st.launch || { ok: false, reason: '（预览页没有打桩）', detail: '' }
  };
  return `
window.chrome = {
  runtime: {
    getManifest: function () { return { version: ${JSON.stringify(mf.version)} }; },
    sendMessage: function (msg) {
      var table = ${JSON.stringify(table)};
      if (msg.type === 'get-preview') return Promise.resolve(table.preview);
      if (msg.type === 'get-health') return Promise.resolve(table.health);
      if (msg.type === 'get-last') return Promise.resolve(table.last);
      if (msg.type === 'launch-bridge') return Promise.resolve(table.launch);
      return Promise.resolve({ ok: true });
    }
  }
};
/* 让"在弹窗里读剪贴板"这条路径也走通，否则底部会多出一条
   "关掉弹窗再点一次"的提示，看起来像有问题。 */
try {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { readText: function () { return Promise.resolve(${JSON.stringify(st.clip)}); } }
  });
} catch (e) { /* 打桩失败也不影响看排版 */ }
`;
}

const popupHtml = fs.readFileSync(path.join(ext, 'popup.html'), 'utf8');
const popupJs = fs.readFileSync(path.join(ext, 'popup.js'), 'utf8');

for (const st of STATES) {
  let html = popupHtml.replace(
    '<script src="popup.js"></script>',
    '<script>' + buildStub(st) + '</script>\n  <script>' + popupJs + '</script>\n  ' +
    (st.afterLoad ? '<script>' + st.afterLoad + '</script>' : '')
  );
  /* 预览页给弹窗留出同样的宽度，外面套一层浅灰底便于看边界。 */
  html = html.replace(
    '</head>',
    '  <style>html{background:#EDEDEA}body{margin:24px auto!important;border:0.5px solid #D8D6CE;border-radius:10px;overflow:hidden}</style>\n' +
    '  <title>弹窗预览 · ' + st.label + '</title>\n</head>'
  );
  fs.writeFileSync(path.join(__dirname, 'preview-popup-' + st.key + '.html'), html, 'utf8');
}

/* 索引页：三种状态并排，一眼看全。
   用 srcdoc 把子页内联进来，而不是 src 指向旁边的文件 —— 这样索引页是
   **自包含**的：放进预览面板 / 拷到别的目录 / 单独发出去都能渲染，
   不会因为相对路径取不到子页而变成三个空白框。 */
function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}
const cols = STATES.map((st) => {
  const inner = fs.readFileSync(path.join(__dirname, 'preview-popup-' + st.key + '.html'), 'utf8');
  return [
    '  <div class="col">',
    '    <h2>' + st.label + '</h2>',
    '    <p class="note">' + st.note + '</p>',
    '    <iframe scrolling="no" style="height:' + st.height + 'px" srcdoc="' + esc(inner) + '"></iframe>',
    '  </div>'
  ].join('\n');
}).join('\n');

const index = [
  '<!DOCTYPE html>',
  '<html lang="zh-CN">',
  '<head>',
  '<meta charset="utf-8">',
  '<title>弹窗预览 · v' + mf.version + '</title>',
  '<style>',
  'html,body{margin:0;padding:0;background:#EDEDEA;font-family:system-ui,"Microsoft YaHei",sans-serif}',
  'h1{font-size:15px;font-weight:500;margin:22px 24px 4px;color:#2C2C2A}',
  'p.tip{margin:0 24px 18px;font-size:13px;color:#5F5E5A}',
  '.wrap{display:flex;gap:20px;align-items:flex-start;padding:0 24px 32px;flex-wrap:wrap}',
  '.col{flex:0 0 auto}',
  '.col h2{font-size:13px;font-weight:500;margin:0 0 3px;color:#2C2C2A}',
  '.col .note{font-size:12px;color:#8A8880;margin:0 0 8px;line-height:1.4;max-width:348px}',
  'iframe{width:348px;border:0.5px solid #D8D6CE;border-radius:10px;background:#fff;display:block}',
  '</style>',
  '</head>',
  '<body>',
  '<h1>弹窗预览 · v' + mf.version + '</h1>',
  '<p class="tip">用真实的 popup.html + popup.js 渲染，只把 chrome 接口打桩喂假数据 —— 和装上去看到的是同一份。④⑤ 是 v1.0.4 那个「启动本地桥」按钮的两种时机。</p>',
  '<div class="wrap">',
  cols,
  '</div>',
  '</body>',
  '</html>'
].join('\n');

fs.writeFileSync(path.join(__dirname, 'preview-popup.html'), index, 'utf8');
console.log('preview written:');
console.log('  tools/preview-popup.html  (索引，五种状态并排)');
STATES.forEach((st) => console.log('  tools/preview-popup-' + st.key + '.html'));
