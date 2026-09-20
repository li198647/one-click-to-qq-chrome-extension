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

/* ---------- 1. 三个 JS 的语法 ---------- */
for (const f of ['background.js', 'content.js', 'popup.js']) {
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

/* ---------- 输出 ---------- */
out.push('');
out.push(bad.length ? ('❌ 有 ' + bad.length + ' 项没过：' + bad.join(' / ')) : '✅ 全部通过');
fs.writeFileSync(path.join(root, 'tools', 'verify-extension.txt'), out.join('\n'), 'utf8');
console.log(out.join('\n'));
