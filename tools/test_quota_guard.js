/* 配额防线实测（防线① 入口限长 / 防线② 写失败逐级退让）

   不抄代码：直接从 background.js 里把「存储 + 历史」那一段切出来，配一个
   会按字节上限抛错的假 chrome.storage.session 跑。抄一遍只能证明"抄对了"，
   切原文才能证明"代码是对的"。

   常量也是从文件里现取的 —— 免得测试里写 512KB、代码里改成 256KB 还显示通过。
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const SRC = fs.readFileSync(path.join(ROOT, 'extension', 'background.js'), 'utf8');

const START = 'const isText = (x) =>';
const END = '/* ---------------------------------------------- 在页面里读取';
const i = SRC.indexOf(START);
const j = SRC.indexOf(END);
if (i < 0 || j < 0 || j < i) {
  console.log('FAIL 切不出代码段 i=' + i + ' j=' + j);
  process.exit(1);
}
const body = SRC.slice(i, j);

const consts = ['HISTORY_MAX', 'HISTORY_KEY', 'STORE_ITEM_MAX'].map((n) => {
  const m = new RegExp('^const ' + n + ' = [^\\n]*;$', 'm').exec(SRC);
  if (!m) { console.log('FAIL 取不到常量 ' + n); process.exit(1); }
  return m[0];
});

/* 假存储：超上限就抛错，行为和 Chrome 一致（整次写入作废，不是丢这一条）
   注意 get 要返回 { 键: 值 }，而 set 收到的是 { 键: 值 }、桶里只留"值" ——
   第一次写这个假存储时因为套了两层，让"三条都进了历史"假失败了一轮。 */
function makeFakeChrome(limitBytes) {
  let val = null;
  let limit = limitBytes;
  let writes = 0;
  return {
    api: {
      storage: {
        session: {
          async get(k) { return val === null ? {} : { [k]: JSON.parse(val) }; },
          async set(obj) {
            writes++;
            const s = JSON.stringify(obj);
            if (Buffer.byteLength(s, 'utf8') > limit) {
              throw new Error('QUOTA_BYTES quota exceeded');
            }
            val = JSON.stringify(obj[Object.keys(obj)[0]]);
          }
        }
      }
    },
    setLimit(n) { limit = n; },
    raw() { return val === null ? null : JSON.parse(val); },
    writes() { return writes; }
  };
}

async function boot(limitBytes) {
  const env = makeFakeChrome(limitBytes);
  const ctx = { chrome: env.api, TextEncoder, JSON, Buffer, console };
  vm.createContext(ctx);
  vm.runInContext(
    consts.join('\n') + '\n' + body + '\n' +
    'globalThis.__api = { pushHistory, readStore, writeStore, storeNotice, byteLen,' +
    ' STORE_ITEM_MAX, HISTORY_MAX };',
    ctx
  );
  return { env, api: ctx.__api };
}

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('[OK]   ' + name); }
  else { fail++; console.log('[FAIL] ' + name + (extra ? '  → ' + extra : '')); }
}

async function main() {
  const MB = 1024 * 1024;

  console.log('--- 0. 常量取值（从文件现取） ---');
  const A = await boot(10 * MB);
  ok('单条上限 = 512KB', A.api.STORE_ITEM_MAX === 512 * 1024, A.api.STORE_ITEM_MAX);
  ok('候选条数上限 = 4', A.api.HISTORY_MAX === 4, A.api.HISTORY_MAX);
  ok('中文按 UTF-8 三字节算（不是 .length）', A.api.byteLen('中') === 3, A.api.byteLen('中'));

  console.log('');
  console.log('--- 1. 正常文字：不受影响、不弹提示 ---');
  await A.api.pushHistory('第一条', '网页复制');
  await A.api.pushHistory('第二条', '网页复制');
  const three = await A.api.pushHistory('第三条', '网页复制');
  ok('三条都进了历史', three.length === 3, three.length);
  ok('顺序是最新在前', three[0] === '第三条' && three[2] === '第一条');
  ok('正常时没有提示', A.api.storeNotice() === '', A.api.storeNotice());

  console.log('');
  console.log('--- 2. 防线①：单条超大被挡在历史外 ---');
  const big = 'A'.repeat(1 * MB);
  const afterBig = await A.api.pushHistory(big, '网页复制');
  ok('超大内容没进历史', afterBig.indexOf(big) === -1);
  ok('已攒的三条还在（没被连坐）', afterBig.length === 3, afterBig.length);
  ok('提示说清了原因', /太大/.test(A.api.storeNotice()) && /没进候选历史/.test(A.api.storeNotice()),
    A.api.storeNotice());
  const afterSmall = await A.api.pushHistory('第四条', '网页复制');
  ok('之后正常内容照常入列', afterSmall.length === 4 && afterSmall[0] === '第四条', afterSmall.length);
  ok('提示自动消失（不是永久挂着）', A.api.storeNotice() === '', A.api.storeNotice());

  console.log('');
  console.log('--- 3. 防线②：真撞配额时逐级退让，而不是整次失败 ---');
  const B = await boot(1 * MB);
  const chunk = (n) => String(n).repeat(400 * 1024);
  for (const n of [1, 2, 3]) await B.api.pushHistory(chunk(n), '网页复制');
  const cut = await B.api.pushHistory(chunk(4), '网页复制');
  ok('自动砍短后还能写进去（不是整次作废）', cut.length === 2, cut.length);
  ok('返回的候选数与真正存下来的条数一致',
    B.env.raw().items.length === cut.length,
    'stored=' + B.env.raw().items.length + ' returned=' + cut.length);
  ok('提示说清留了几条', /只留了最近 2 条/.test(B.api.storeNotice()), B.api.storeNotice());
  ok('留下的确实是最新的两条', cut[0] === chunk(4) && cut[1] === chunk(3));

  console.log('');
  console.log('--- 4. 极端：连一条都写不进去时不能卡死 ---');
  const C = await boot(60);
  await C.api.pushHistory('很大的一条'.repeat(100), '网页复制');
  ok('没崩、返回空列表', Array.isArray(await C.api.pushHistory('又来一条', '网页复制')));
  ok('提示说清是配额满', /配额已满/.test(C.api.storeNotice()), C.api.storeNotice());
  C.env.setLimit(10 * MB);
  const back = await C.api.pushHistory('恢复正常', '网页复制');
  ok('配额恢复后能重新写入（没有卡死）', back.length === 1 && back[0] === '恢复正常',
    JSON.stringify(back));
  ok('提示随之消失', C.api.storeNotice() === '', C.api.storeNotice());

  console.log('');
  console.log('--- 5. 台阶不会重复试同样的大小 ---');
  const D = await boot(10 * MB);
  const before = D.env.writes();
  await D.api.pushHistory('只有一条', '网页复制');
  ok('1 条内容只写 1 次（不会把 4/2/1 都试一遍）', D.env.writes() - before === 1,
    D.env.writes() - before);

  console.log('');
  console.log('================================');
  console.log('通过 ' + pass + ' 项，失败 ' + fail + ' 项');
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.log('崩溃: ' + (e && e.stack || e)); process.exit(1); });
