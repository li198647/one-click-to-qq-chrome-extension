/* ============================================================
   v1.0.3 · Permissions-Policy 剪贴板守卫 · 实测

   背景：reverso.net 上 content.js 的轮询读剪贴板时，扩展错误页刷出
     "Permissions policy violation: The Clipboard API has been blocked
      because of a permissions policy applied to the current document."
   那条报错是**浏览器自己打印**的，不是 Promise 的 rejection，try/catch
   接不住 —— 所以只能"先问策略、再决定叫不叫"。

   这个脚本**不另抄一份实现**，而是把 imageutil.js 里 clipReadAllowed
   那段源码原样切出来（按花括号配平）用 new Function 跑。
   抄一遍只能证明"抄对了"，取原文才能证明"代码是对的"。

   判定标准不是"函数返回了啥"，而是**假剪贴板有没有被调用**：
   被策略拦下时 readText 必须 0 次 —— 那才是浏览器不报错的真正原因。
   ============================================================ */

const fs = require('fs');
const path = require('path');

const BASE = path.resolve(__dirname, '..');
const SRC = fs.readFileSync(path.join(BASE, 'extension', 'imageutil.js'), 'utf8');

/* ---------- 从真实源码里切出 clipReadAllowed（配平花括号） ---------- */
function cutFunction(src, name) {
  const start = src.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('源码里找不到函数：' + name);
  let i = src.indexOf('{', start);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, j + 1);
    }
  }
  throw new Error('花括号没配平：' + name);
}

const body = cutFunction(SRC, 'clipReadAllowed');
const makeFn = new Function('document', body + '\nreturn clipReadAllowed;');

/* ---------- 假文档 ---------- */
const DOC_BLOCKED = { permissionsPolicy: { allowsFeature: () => false } };
const DOC_ALLOWED = { permissionsPolicy: { allowsFeature: () => true } };
const DOC_OLD_NAME = { featurePolicy: { allowsFeature: () => false } };  // 旧名
const DOC_SAYS_UNDEF = { permissionsPolicy: { allowsFeature: () => undefined } };
const DOC_NO_API = {};                                                   // 没有策略对象
const DOC_THROWS = {
  get permissionsPolicy() { throw new Error('boom'); }
};

/* ---------- 断言 ---------- */
let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  [OK]   ' + name); }
  else { fail++; console.log('  [FAIL] ' + name + (extra === undefined ? '' : '  → ' + extra)); }
}

/* 模拟 content.js 的 snapshot()：先问策略，允许才读。
   返回 { verdict, calls }，calls = 假剪贴板被读了几次。 */
async function attempt(doc) {
  const allowed = makeFn(doc)();
  const box = { calls: 0 };
  const nav = {
    clipboard: {
      readText: async () => { box.calls++; return '剪贴板内容'; }
    }
  };
  if (!allowed) return { verdict: '跳过', calls: 0 };
  await nav.clipboard.readText();
  return { verdict: '读过', calls: box.calls };
}

(async function () {
  console.log('=== ① 被站点策略拦下的页面（reverso.net 这类） ===');
  const blocked = await attempt(DOC_BLOCKED);
  ok('判定为"不许读"', makeFn(DOC_BLOCKED)() === false, String(makeFn(DOC_BLOCKED)()));
  ok('**剪贴板一次都没被读**（这才是浏览器不报错的原因）', blocked.calls === 0, blocked.calls);
  ok('走的是"跳过"分支', blocked.verdict === '跳过', blocked.verdict);

  console.log('');
  console.log('=== ② 正常情况下不能被误伤 ===');
  const allowedRes = await attempt(DOC_ALLOWED);
  ok('允许的页面仍判定为可读', makeFn(DOC_ALLOWED)() === true);
  ok('该读的时候真读了（1 次）', allowedRes.calls === 1, allowedRes.calls);

  console.log('');
  console.log('=== ③ 问不出来的情况一律放行（宁可多试，不可误伤） ===');
  ok('只有旧名 document.featurePolicy 时也能识别拦截', makeFn(DOC_OLD_NAME)() === false);
  ok('allowsFeature 返回 undefined（老 Chrome）→ 放行', makeFn(DOC_SAYS_UNDEF)() === true);
  ok('文档上压根没有策略对象 → 放行', makeFn(DOC_NO_API)() === true);
  ok('取策略时抛异常 → 放行（不把整个轮询拖死）', makeFn(DOC_THROWS)() === true);
  ok('没有 document（service worker 里）→ 放行', makeFn(undefined)() === true);

  console.log('');
  console.log('=== ④ 守卫会不会被悄悄摘掉（这几条是防退化的） ===');
  const CONTENT = fs.readFileSync(path.join(BASE, 'extension', 'content.js'), 'utf8');
  const BG = fs.readFileSync(path.join(BASE, 'extension', 'background.js'), 'utf8');
  const POPUP = fs.readFileSync(path.join(BASE, 'extension', 'popup.js'), 'utf8');

  /* 导出丢了会怎样：两个调用点都写着 `typeof qqImg.clipReadAllowed` 才用，
     导出一没，就静默退回"一律允许" —— 报错原样回来，而没有任何症状。
     所以要显式钉住这一条。 */
  ok('imageutil.js 的导出里有 clipReadAllowed（丢了会静默失效）',
    /clipReadAllowed:\s*clipReadAllowed/.test(SRC));

  /* 顺序是这整件事的命门：守卫必须**排在**调用之前。
     写在后面等于没写 —— 浏览器那条报错在调用瞬间就已经打出来了。

     ⚠️ 匹配串必须带上 `await ` 前缀。只写 `navigator.clipboard.readText`
     会撞上两处假目标：background.js 文件头注释里提到过它、content.js 的
     `canRead()` 里还有一句 `!navigator.clipboard.readText` 的能力探测。
     这两处都不真的读剪贴板，却会让 indexOf 得出相反结论（已踩过）。 */
  const CALL = 'await navigator.clipboard.readText()';
  ok('content.js：守位于真正的读取调用之前',
    CONTENT.indexOf('qqImg.clipReadAllowed') < CONTENT.indexOf(CALL),
    'guard@' + CONTENT.indexOf('qqImg.clipReadAllowed') + ' read@' + CONTENT.indexOf(CALL));
  ok('background.js：守位于真正的读取调用之前',
    BG.indexOf('!qqImg.clipReadAllowed()') < BG.indexOf(CALL),
    'guard@' + BG.indexOf('!qqImg.clipReadAllowed()') + ' read@' + BG.indexOf(CALL));

  /* 弹窗必须**保持**不设这道岗 —— 它不受站点策略管，而它偏偏是唯一
     可靠能读到剪贴板的那条路，误拦的代价比不拦大得多。
     （注释里提到过这个名字，所以先把注释剥掉再查。） */
  const POPUP_CODE = POPUP.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  ok('popup.js 没有真的调用它（只在注释里说明为什么不用）',
    POPUP_CODE.indexOf('clipReadAllowed') === -1
      && POPUP.indexOf('navigator.clipboard.readText') > -1);

  console.log('');
  console.log('=== ⑤ 万一探测没拦住：拒一次就永久闭嘴（别每 2 秒刷一条红字） ===');
  const looksLikePolicyBlock = new Function(
    cutFunction(CONTENT, 'looksLikePolicyBlock') + '\nreturn looksLikePolicyBlock;'
  )();

  const e1 = new Error('Disabled in this document by Feature Policy.');
  e1.name = 'NotAllowedError';
  ok('「Disabled in this document by Feature Policy」→ 认定为永久拦截',
    looksLikePolicyBlock(e1) === true);

  const e2 = new Error('The Clipboard API has been blocked because of a '
    + 'permissions policy applied to the current document.');
  e2.name = 'NotAllowedError';
  ok('「blocked because of a permissions policy」→ 认定为永久拦截',
    looksLikePolicyBlock(e2) === true);

  const e3 = new Error('Document is not focused.');
  e3.name = 'NotAllowedError';
  ok('**「Document is not focused」→ 不认定为永久拦截**（偶发，下次就好）',
    looksLikePolicyBlock(e3) === false,
    '这里误判的代价：整页的自动快照永久停摆');

  ok('空值 / 没见过的异常 → 不认定为永久拦截',
    looksLikePolicyBlock(null) === false && looksLikePolicyBlock({}) === false);
  ok('两处 catch 都记这个标记（首读 + 复核读）',
    (CONTENT.match(/policyBlocked = true/g) || []).length >= 2);
  ok('canRead 开头就看这个标记（闭嘴之后不再尝试）',
    /if \(policyBlocked\) return false;/.test(CONTENT));

  console.log('');
  console.log('----------------------------------------');
  console.log('通过 ' + pass + ' 项，失败 ' + fail + ' 项');
  process.exit(fail ? 1 : 0);
})();
