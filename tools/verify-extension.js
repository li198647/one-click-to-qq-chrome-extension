/* 扩展自检脚本（每次改完 extension/ 都跑一遍）
   用法： node tools\verify-extension.js
   结果写到 tools\verify-extension.txt（该文件被 .gitignore 忽略）

   在 PowerShell 里跑、不要用 bash（本机 git-bash 的 PATH 是坏的）。
   注意：读 JSON 一定要用 node —— PowerShell 的 ConvertFrom-Json 会把
   UTF-8 中文读成乱码，然后谎报 JSON 语法错误。 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const ext = path.join(root, 'extension');
const out = [];
const bad = [];

function check(name, ok, extra) {
  out.push((ok ? '[OK]   ' : '[FAIL] ') + name + (extra ? ' — ' + extra : ''));
  if (!ok) bad.push(name);
}

/* ---------- 1. 四个 JS 的语法 ---------- */
for (const f of ['background.js', 'content.js', 'popup.js', 'imageutil.js']) {
  const p = path.join(ext, f);
  try {
    new vm.Script(fs.readFileSync(p, 'utf8'), { filename: p });
    check('语法 ' + f, true);
  } catch (e) {
    check('语法 ' + f, false, e.message);
  }
}

/* ---------- 2. manifest ---------- */
let m = null;
try {
  m = JSON.parse(fs.readFileSync(path.join(ext, 'manifest.json'), 'utf8'));
  check('manifest JSON', true, 'version=' + m.version + ' name=' + m.name);
} catch (e) {
  check('manifest JSON', false, e.message);
}

if (m) {
  const cs = m.content_scripts && m.content_scripts[0];
  check('content_scripts 存在', !!cs);
  if (cs) {
    check('注入所有 iframe (all_frames)', cs.all_frames === true, String(cs.all_frames));
    check('run_at=document_start', cs.run_at === 'document_start', String(cs.run_at));
    check('注入了 content.js', (cs.js || []).indexOf('content.js') >= 0);
  }
  check('背景脚本存在', !!(m.background && m.background.service_worker));
  check('弹窗存在', !!(m.action && m.action.default_popup));

  const refs = [];
  if (m.background) refs.push(m.background.service_worker);
  if (m.action) {
    refs.push(m.action.default_popup);
    Object.keys(m.action.default_icon || {}).forEach((k) => refs.push(m.action.default_icon[k]));
  }
  Object.keys(m.icons || {}).forEach((k) => refs.push(m.icons[k]));
  (m.content_scripts || []).forEach((c) => (c.js || []).forEach((j) => refs.push(j)));

  const miss = refs.filter((r) => r && !fs.existsSync(path.join(ext, r)));
  check('manifest 引用的文件都存在', miss.length === 0, miss.join(',') || ('共 ' + refs.length + ' 个'));

  const di = m.action && m.action.default_icon ? m.action.default_icon : {};
  check('工具栏图标含 32px（高分屏要用）', !!di['32'], Object.keys(di).join(','));
}

/* ---------- 2b. 图标：四个尺寸都要在，且像素尺寸对得上 ---------- */
/* PNG 头里第 16~24 字节就是宽高（IHDR），不用装图像库也能读。 */
function pngSize(p) {
  const b = fs.readFileSync(p);
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
}
const iconBad = [];
for (const n of [16, 32, 48, 128]) {
  const p = path.join(ext, 'icons', 'icon' + n + '.png');
  if (!fs.existsSync(p)) { iconBad.push(n + ':缺失'); continue; }
  const wh = pngSize(p);
  if (wh[0] !== n || wh[1] !== n) iconBad.push(n + ':' + wh.join('x'));
}
check('图标 16/32/48/128 齐全且尺寸正确', iconBad.length === 0, iconBad.join(',') || '（4 个）');

/* ---------- 3. popup.html 与 popup.js 的 id 对齐 ---------- */
const html = fs.readFileSync(path.join(ext, 'popup.html'), 'utf8');
const popup = fs.readFileSync(path.join(ext, 'popup.js'), 'utf8');
const bg = fs.readFileSync(path.join(ext, 'background.js'), 'utf8');
const content = fs.readFileSync(path.join(ext, 'content.js'), 'utf8');

const ids = [...html.matchAll(/id="([\w-]+)"/g)].map((x) => x[1]);
const used = [...popup.matchAll(/getElementById\('([\w-]+)'\)/g)].map((x) => x[1]);
const noId = used.filter((i) => !ids.indexOf(i) >= 0 && ids.indexOf(i) < 0);
check('popup.js 用到的 id 都在 html 里', noId.length === 0, noId.join(',') || ('html ' + ids.length + ' 个 / js ' + used.length + ' 个'));

/* ---------- 3b. 版式：「将要发送」和「候选」必须是两块 ----------
   木木的要求原话："要发送的文字单独一个矩形框线框住，不要和候选放在一起"。
   做法是 .sendbox（含 .sbText 线框）与 .candsList 两个并列容器，
   候选行不再出现在发送框里面。下面按 HTML 结构断言，别只看类名有没有定义。 */
const sbStart = html.indexOf('id="sendBox"');
const sbEnd = html.indexOf('<!-- 候选');
const sbRegion = (sbStart >= 0 && sbEnd > sbStart) ? html.slice(sbStart, sbEnd) : '';
check('popup: 找到「将要发送」区块', sbRegion.length > 0);
check('popup: 发送的文字套了实线矩形', /\.sbText\s*\{[^}]*border:\s*1px solid/.test(html) && /id="sbText"/.test(sbRegion));
check('popup: 候选不在发送框里面（两块分开）',
  sbRegion.indexOf('candsList') === -1 && /id="candsList"/.test(html));
check('popup: 候选行有独立行样式（不是直接铺在背景上）', /\.cand\s*\{[^}]*border:/.test(html));
check('popup: 点候选会同步刷新上面那个框', /sbText\.textContent = c\.preview/.test(popup));

/* ---------- 3c. 版式（v0.1.6，1.0.0 起沿用）：发送按钮搬进标题行、缩成 Windows 小按钮 ----------
   木木的要求原话："将'立即发送'这个大按钮，缩小到 windows 对话框 yes/no
   那样的大小。并把改好的按钮放在'一键发到 qq'那一行的最右端"；
   另外「共 4 字」那一行要整行去掉。三件事都断言在这里。 */
const trStart = html.indexOf('class="titleRow"');
const trEnd = html.indexOf('</div>', trStart);
const titleRow = (trStart >= 0 && trEnd > trStart) ? html.slice(trStart, trEnd) : '';
check('popup: 发送按钮在标题行里', titleRow.indexOf('id="sendBtn"') >= 0 && /<h1>/.test(titleRow));
check('popup: 按钮是 Windows 小尺寸（min-width 88 + height 26）',
  /button\s*\{[^}]*min-width:\s*88px/.test(html) && /button\s*\{[^}]*height:\s*26px/.test(html));
check('popup: 通栏大按钮的旧样式已去掉（不再是 width:100%）', !/button\s*\{[^}]*width:\s*100%/.test(html));
check('popup: 「共 N 字」整行已彻底去掉', html.indexOf('id="sbMeta"') === -1 && popup.indexOf('sbMeta') === -1);
check('popup: 发送区标题字号 11px', /\.sbHead\s*\{[^}]*font-size:\s*11px/.test(html));
check('popup: 出错原因并入标题（且 pre-line 让换行生效）',
  /white-space:\s*pre-line/.test(html) && /sbHead\.textContent = \(\(r && r\.reason\)/.test(popup));

/* ---------- 3d. 图片功能（v1.0.1） ----------
   木木的要求原话："加入一键传递图片功能，在发 qq 的弹出菜单里，用特别的
   标记表明传过去的是图片，不是文字"。
  这里断言的是"这套结构确实在"，行为正确性由真机测试保证
   （tools/test_send_image.py 打真接口跑通过：上传 200 + 发送 200 + 手机收到）。 */

const img = fs.readFileSync(path.join(ext, 'imageutil.js'), 'utf8');

check('manifest 声明了 contextMenus（右键菜单要用）',
  !!m && Array.isArray(m.permissions) && m.permissions.indexOf('contextMenus') >= 0,
  m ? (m.permissions || []).join(',') : '');

check('imageutil.js 注入到网页，且在 content.js 之前', (() => {
  const cs = (m && m.content_scripts && m.content_scripts[0]) || {};
  const js = cs.js || [];
  const a = js.indexOf('imageutil.js');
  const b = js.indexOf('content.js');
  return a >= 0 && b >= 0 && a < b;
})());

check('popup.html 先加载 imageutil.js 再加载 popup.js',
  html.indexOf('imageutil.js') >= 0 && html.indexOf('imageutil.js') < html.indexOf('popup.js'));

check('background 加载 imageutil.js（service worker 里没有 FileReader）',
  /importScripts\('imageutil\.js'\)/.test(bg));

check('background 补注入时也带上 imageutil.js',
  /files:\s*\['imageutil\.js',\s*'content\.js'\]/.test(bg));

/* 图片必须"不进历史"：storage.session 只有 10MB，而 base64 要膨胀 4/3，
   真按字面把图也存进去，连文字历史都会一起写不进去。 */
check('图片不进候选历史（历史只收文字）',
  /async function pushHistory\(text, how\)/.test(bg) &&
  /if \(!t\) return st\.items;/.test(bg) &&
  /const t = String\(text == null \? '' : text\)\.trim\(\);/.test(bg));

check('候选项带 kind 与 key 字段',
  /list\.push\(\{ kind: 'image', image: clip\.image/.test(bg) &&
  /x\.key = \(x\.kind === 'image'\)/.test(bg));

check('发送按 key 精确匹配（图片没有"原文"可比对）',
  /r\.list\.find\(\(x\) => x\.key === want\)/.test(bg) &&
  !/pickText/.test(bg) && !/pickText/.test(popup));

check('发图片走独立通路 sendImageRun',
  /async function sendImageRun/.test(bg) && /chosen\.kind === 'image'/.test(bg));

check('右键菜单只对图片出现，且由扩展后台自己下载',
  /contexts:\s*\['image'\]/.test(bg) &&
  /chrome\.contextMenus\.onClicked\.addListener/.test(bg) &&
  /credentials:\s*'omit'/.test(bg));

check('取不到图时明确报错（不静默换别的内容发）',
  /这张图片取不到/.test(bg) && /复制图片/.test(bg));

check('剪贴板同时有图和文字时仍发文字（老行为不破）',
  /else if \(clip\.text\)/.test(bg) &&
  bg.indexOf('else if (clip.text)') < bg.indexOf('else if (clip.image)'));

/* 那个"特别标记"：候选行的小徽标 + 发送框的真缩略图 */
check('popup: 候选行有图片徽标（内联 SVG，不引外部图片文件）',
  /\.candIcon\s*\{/.test(html) && /function imgBadge/.test(popup) && /createElementNS/.test(popup));

check('popup: 发送框里有真缩略图',
  /id="sbThumbWrap"/.test(html) && /id="sbThumb"/.test(html) &&
  /sbThumb\.src = c\.image\.dataUrl/.test(popup));

check('popup: 图片那一行写「图片 · 长×宽 · 体积」',
  /\['图片'\]/.test(popup) && /parts\.push\(x\.width \+ '×' \+ x\.height\)/.test(popup));

check('popup: 换选时显式释放缩略图的 dataURL',
  /function clearThumb/.test(popup) && /sbThumb\.removeAttribute\('src'\)/.test(popup));

check('background: 注入网页的读图代码有存在性保护', /typeof qqImg !== 'undefined'/.test(bg));

check('imageutil: 用 IIFE 挂 globalThis（避免重复注入撞 const 声明）',
  /\(function \(g\) \{/.test(img) && /g\.qqImg = \{/.test(img));

check('imageutil: 不依赖 FileReader / DOM canvas（service worker 里没有）',
  !/new FileReader/.test(img) && !/createElement\('canvas'\)/.test(img));

check('imageutil: base64 分块编码（避免 apply 实参超限）',
  /CHUNK = 0x8000/.test(img) && /String\.fromCharCode\.apply/.test(img));

/* ---------- 4. 关键实现是否还在 ---------- */
const feat = [
  ['background: 历史写入串行化 (editStore + storeChain)', /let storeChain = Promise\.resolve\(\);/.test(bg) && /function editStore/.test(bg)],
  ['background: 历史键存 {items, stats}', /HISTORY_KEY = 'clipHistory'/.test(bg) && /stats/.test(bg)],
  ['background: 兼容旧版纯数组', /Array\.isArray\(v\)/.test(bg)],
  ['background: 来源统计按"新顶上来的那条"计数', /st\.items\[0\] !== t/.test(bg)],
  ['background: 补注入已开标签页', /function injectIntoAllTabs/.test(bg) && /onInstalled\.addListener/.test(bg)],
  ['background: 补注入用 allFrames', /allFrames: true/.test(bg)],
  ['background: 预览返回 max 与 stats', /max: HISTORY_MAX/.test(bg) && /stats: \(await readStore\(\)\)\.stats/.test(bg)],
  ['content: teardown 式重复注入保护（不是标志位 return）', /__qqSendSnifferTeardown/.test(content) && !/__qqSendSnifferReady/.test(content)],
  ['content: copy/cut 拿不到文字时延后补读剪贴板', /later\(function \(\) \{ snapshot\(how\); \}, 300\)/.test(content)],
  ['content: 同一份内容不重复上报', /if \(t === lastSent\) return;/.test(content)],
  ['content: 有焦点才读剪贴板', /function focusedHere/.test(content) && /if \(busy \|\| !canRead\(\) \|\| !focusedHere\(\)\) return;/.test(content)],
  ['content: 两读一致才上报', /SETTLE_MS/.test(content) && /if \(a !== b\) return;/.test(content)],
  ['popup: 底部诊断行', /id="diag"/.test(html) && /renderDiag/.test(popup)],
  ['popup: 显示版本号', /getManifest\(\)\.version/.test(popup)],
  ['popup: 候选没凑满时写明原因', /cands\.length < max/.test(popup)]
];
for (const [name, ok] of feat) check(name, ok);

/* ---------- 5. 扩展目录里不能有下划线开头的文件 ---------- */
function walk(d, acc) {
  for (const n of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, n.name);
    if (n.isDirectory()) walk(p, acc);
    else if (n.name.startsWith('_')) acc.push(p);
  }
  return acc;
}
const us = walk(ext, []);
check('扩展目录无下划线开头文件', us.length === 0, us.join(',') || '（干净）');

/* ---------- 6. 敏感文件仍被 gitignore ---------- */
let ig = '';
try { ig = fs.readFileSync(path.join(root, '.gitignore'), 'utf8'); } catch (e) { /* 忽略 */ }
check('.gitignore 排除 config.json', /config\.json/.test(ig));
check('.gitignore 排除 state.json', /state\.json/.test(ig));

/* ---------- 7. 桥端关键实现（只做存在性断言） ----------
   桥的**真实行为**由 tools/test_send_image.py 打真接口验证过（上传 200 +
   发送 200 + 手机确实收到图），这里只挡"哪天改代码把某一段顺手删了"
   这种事故。跨文件做文本断言有点越界，但比"删了没人发现"好。 */
let bridge = '';
try { bridge = fs.readFileSync(path.join(root, 'bridge', 'qq_bridge.py'), 'utf8'); } catch (e) { /* 忽略 */ }

check('桥: /send 认 image 字段', /data\.get\("image"\)/.test(bridge) && /async def do_send_image/.test(bridge));
check('桥: 走 file_data 直传（botpy 的 post_c2c_file 不收 file_data）',
  /"file_data": b64/.test(bridge) && /"file_type": 1/.test(bridge));
check('桥: 两步发送（先上传拿 file_info，再 msg_type=7）',
  /"msg_type": 7/.test(bridge) && /"file_info": file_info/.test(bridge));
check('桥: 超 20MB 缩尺寸，且只降分辨率不换格式',
  /SOFT_LIMIT = 20 \* 1024 \* 1024/.test(bridge) &&
  /SHRINK_STEPS/.test(bridge) && /Image\.LANCZOS/.test(bridge));
check('桥: 原格式被拒时转 PNG 重试一次', /def to_png/.test(bridge) && /转成 PNG 重试一次/.test(bridge));
check('桥: 请求体上限已放开（aiohttp 默认 1MB 会把图片挡成 413）',
  /client_max_size=256 \* 1024 \* 1024/.test(bridge));
check('桥: 自己取 access_token 并缓存（不碰 botpy 私有结构）',
  /async def get_access_token/.test(bridge) && /_TOKEN\["expire_at"\]/.test(bridge));
check('桥: 版本号 1.0.1', /^VERSION = "1\.0\.1"$/m.test(bridge));

/* ---------- 输出 ---------- */
out.push('');
out.push(bad.length ? ('❌ 有 ' + bad.length + ' 项没过：' + bad.join(' / ')) : '✅ 全部通过');
fs.writeFileSync(path.join(root, 'tools', 'verify-extension.txt'), out.join('\n'), 'utf8');
console.log(out.join('\n'));
