/* ============================================================
   一键发到 QQ · 后台脚本（MV3 service worker）
   职责：接收快捷键 / 弹窗 / 网页监听的指令，抓内容，POST 给本地桥。
   同时维护一份「最近 4 条内容」的候选列表，供弹窗展示与改选。

   预览和发送走同一个 resolveContent()，所以弹窗里看到什么，
   发出去就是什么。

   ⚠️ 剪贴板的关键约束（0.1.1 修正）：
   Chrome 规定 navigator.clipboard.readText() 只能在「当前有焦点的
   document」里调用，否则会抛：
       NotAllowedError: Document is not focused.
   而 offscreen 文档是隐藏的、永远拿不到焦点 —— 放进去读必然失败。
   所以读取必须由「当时真正持有焦点的那个上下文」来做：
     · 点图标 → 弹窗有焦点 → 由 popup.js 读，结果随消息传进来
     · 按快捷键 → 页面还有焦点 → 由这里注入页面去读
   两边都没读到就明确报错，不静默降级成别的内容。

   ⚠️ 历史的来源（0.1.2 起，0.1.3 补全）：
   Chrome 没有任何「剪贴板变了」的通知接口，扩展只能在"能看见剪贴板"
   的瞬间读到它。所以历史靠四路一起攒（前三路在 content.js）：
     · 网页里的 copy / cut 事件
     · 切回页面 / 标签页时的剪贴板快照（兜住在别处复制后回来）
     · 页面有焦点时的低频巡检（兜住网页自带的"复制"按钮）
     · 每次被触发时对剪贴板拍一张快照（点图标 / 按 Ctrl+B）
   历史存在 chrome.storage.session —— 只在内存里，关掉浏览器即清空，
   绝不把可能含密码/验证码的剪贴板内容写到磁盘上。

   ⚠️ 0.1.3 补的第二个坑：manifest 里的 content_scripts 只对「之后加载
   的页面」生效，扩展一重载，已经开着的标签页里就没有监听器了 ——
   在那些页面上复制，扩展完全听不见（木木报的"只攒到 2 条"就是这个）。
   所以下面会在安装/更新/重载时主动给现有标签页补注入一次。

   ⚠️ 0.1.4 补的第三个坑：连着复制 4 条、只留下最后 1 条。
   两个原因，一个在这个文件、一个在 content.js：
     · 这里：历史是"读-改-写"，而 chrome.storage 没有事务。4 个消息
       几乎同时到达时（service worker 刚被唤醒时最容易），4 次写入
       互相覆盖，只剩最后一个。→ 所有改动串进一条 Promise 链（editStore）
     · content.js：0.1.3 用标志位防重复注入是错的，重载扩展后旧实例的
       监听器还在但发不出消息，新实例又被标志位挡住 → 整页监听是死的。
   另外这里顺便记录了每一路来源各记下几条（stats），弹窗底部会显示，
   出问题时能立刻看出是哪一路没在工作。
   ============================================================ */

const BRIDGE = 'http://127.0.0.1:18761';
const BRIDGE_SEND = BRIDGE + '/send';
const BRIDGE_HEALTH = BRIDGE + '/health';

/* 列表里每一项最多显示多少个字符。20 个字足以让链接露出域名。 */
const PREVIEW_LIMIT = 20;

/* 候选列表最多几项（= 保留最近几次内容）。 */
const HISTORY_MAX = 4;

/* 历史存放位置：chrome.storage.session = 内存，关浏览器即清。
   不用 storage.local（会明文落盘）、更不用 storage.sync（会上传云端）。
   存的是一个对象 { items, stats }，不是纯数组。 */
const HISTORY_KEY = 'clipHistory';

let badgeTimer = null;
let lastResult = null;

/* ---------------------------------------------- 图标角标 */

async function setBadge(text, color, clearAfterMs) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color: color || '#5F5E5A' });
    await chrome.action.setBadgeText({ text: text || '' });
  } catch (e) { /* 忽略 */ }

  if (badgeTimer) {
    clearTimeout(badgeTimer);
    badgeTimer = null;
  }
  if (text && clearAfterMs) {
    badgeTimer = setTimeout(() => {
      try { chrome.action.setBadgeText({ text: '' }); } catch (e) { /* 忽略 */ }
      badgeTimer = null;
    }, clearAfterMs);
  }
}

/* ---------------------------------------------- 系统通知 */

async function notify(title, message) {
  try {
    await chrome.notifications.create('', {
      type: 'basic',
      iconUrl: 'icons/icon128.png',
      title: title,
      message: message,
      priority: 2
    });
  } catch (e) { /* 忽略 */ }
}

/* ---------------------------------------------- 历史（内存，关浏览器即清）

   一个键里同时存两样东西：
     items —— 最近 4 条内容
     stats —— 每一路来源各"真正记下了"多少条

   记 stats 是为了能一眼看出是不是哪一路没在工作：如果连着复制 4 次、
   弹窗底部却写着「网页复制 ×0」，那就说明网页里那套监听根本没生效，
   而不是"历史坏了"这种无从下手的感觉。 */

const isText = (x) => typeof x === 'string' && !!x;

async function readStore() {
  try {
    const o = await chrome.storage.session.get(HISTORY_KEY);
    const v = o && o[HISTORY_KEY];
    /* 兼容 0.1.3 及更早存的纯数组 */
    if (Array.isArray(v)) return { items: v.filter(isText), stats: {} };
    if (v && typeof v === 'object') {
      return {
        items: Array.isArray(v.items) ? v.items.filter(isText) : [],
        stats: (v.stats && typeof v.stats === 'object' && !Array.isArray(v.stats)) ? v.stats : {}
      };
    }
  } catch (e) { /* 忽略 */ }
  return { items: [], stats: {} };
}

async function writeStore(st) {
  const clean = {
    items: (st.items || []).slice(0, HISTORY_MAX),
    stats: st.stats || {}
  };
  try {
    await chrome.storage.session.set({ [HISTORY_KEY]: clean });
  } catch (e) { /* 忽略 */ }
  return clean;
}

/* ⚠️ 所有"读-改-写"必须排队串行执行。

   chrome.storage 没有事务、没有原子操作。连着复制 4 条时，4 个消息
   几乎同时到达（service worker 刚从休眠中被唤醒时尤其容易），4 个
   pushHistory 会各自读到同一份旧列表、各自写回自己那一份 —— 最后写入
   的把前面三个全盖掉。表现就是"连着复制 4 次，只留下最后 1 条"。

   把每次改动串进同一条 Promise 链，一次只跑一个，问题就没了。 */
let storeChain = Promise.resolve();

function editStore(fn) {
  const run = storeChain.then(async () => {
    const st = await readStore();
    const out = await fn(st);
    await writeStore(st);
    return out;
  });
  /* 某一次失败不能把后面排队的全掐断 */
  storeChain = run.then(() => undefined, () => undefined);
  return run.catch(() => []);
}

/* 入列。相同内容不重复留两份 —— 只把它移到最前。
   去重是必需的：网页复制时记一条，紧接着又被触发快照一次，
   不去重就会立刻出现两条一模一样的内容。

   how：这一条是谁送来的（网页复制 / 切回页面 / 页面剪贴板 / 触发快照）。
   只在"确实顶上来了新的一条"时才计数，避免同一份内容被反复巡检刷数。 */
async function pushHistory(text, how) {
  const t = String(text == null ? '' : text).trim();
  const src = String(how || '').trim() || '剪贴板';

  return await editStore((st) => {
    if (!t) return st.items;
    if (st.items[0] !== t) {
      st.stats[src] = (Number(st.stats[src]) || 0) + 1;
    }
    st.items = [t].concat(st.items.filter((x) => x !== t));
    return st.items;
  });
}

/* ---------------------------------------------- 在页面里读取

   注入到页面里跑，所以能同时拿到「选中文字 / 标题 / 链接 / 剪贴板」。
   剪贴板这一段只有当页面自己还是活跃标签（比如你刚按下快捷键）时
   才读得到；点图标打开弹窗时页面会失去焦点，那种情况交给 popup.js。

   注意：这个函数会被序列化后注入，必须是自包含的，不能引用外部变量。
   内部把异常全部吞掉，保证它永不抛错 —— 否则 executeScript 失败会被
   误判成"页面受限"。 */

async function probePage(tabId) {
  try {
    const r = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: async () => {
        const out = {
          selection: '', title: '', url: '',
          clipOk: false, clipText: '', clipError: '', clipKinds: [],
          where: '页面'
        };

        try {
          out.selection = String(
            window.getSelection ? window.getSelection().toString() : ''
          ).trim();
        } catch (e) { /* 忽略 */ }

        out.title = document.title || '';
        out.url = location.href || '';

        let focused = false;
        try { focused = !!document.hasFocus(); } catch (e) { /* 忽略 */ }
        if (!focused) {
          out.clipError = '页面当前没有焦点';
          return out;
        }

        try {
          out.clipText = String((await navigator.clipboard.readText()) || '').trim();
          out.clipOk = true;
        } catch (e) {
          out.clipError = String((e && e.message) ? e.message : e);
          return out;
        }

        if (!out.clipText) {
          try {
            const items = await navigator.clipboard.read();
            const kinds = [];
            for (const it of (items || [])) {
              for (const t of (it.types || [])) {
                if (kinds.indexOf(t) === -1) kinds.push(t);
              }
            }
            out.clipKinds = kinds;
          } catch (e) { /* 分辨不出来就退回到"没有文字"这句 */ }
        }
        return out;
      }
    });
    if (r && r[0] && r[0].result) return r[0].result;
  } catch (e) {
    return { restricted: true, selection: '', title: '', url: '' };
  }
  return { restricted: true, selection: '', title: '', url: '' };
}

/* ---------------------------------------------- 剪贴板结果归一化

   弹窗（popup.js）和页面（probePage）回传的是同样的字段，这里统一
   成一份形状，再挑出可用的那一份。 */

function normalizeClip(x) {
  const o = { ok: false, text: '', error: '', kinds: [], where: '' };
  if (!x) return o;
  o.ok = !!x.clipOk;
  o.text = String(x.clipText || '').trim();
  o.error = String(x.clipError || '');
  if (Array.isArray(x.clipKinds)) o.kinds = x.clipKinds;
  o.where = String(x.where || '');
  return o;
}

/* 优先用真正读到文字的那一份；都读不到时挑一份留下原因，
   好让弹窗能把"为什么读不到"写清楚。 */
function pickClip(a, b) {
  if (a && a.ok && a.text) return a;
  if (b && b.ok && b.text) return b;
  if (a && a.ok) return a;
  if (b && b.ok) return b;
  return (a && a.error) ? a : (b || a);
}

/* "页面当前没有焦点"是预期内的噪音（点图标时页面必然失去焦点），
   不该当成错误原因摆给木木看。 */
function cleanErr(s) {
  const t = String(s || '').trim();
  if (!t || t === '页面当前没有焦点') return '';
  return t;
}

/* 把剪贴板里的类型翻译成一句人话。分辨不出就返回空串。 */
function describeKinds(kinds) {
  if (!kinds || !kinds.length) return '';
  for (const t of kinds) {
    if (String(t).indexOf('image/') === 0) return '剪贴板里是一张图片，没有文字。';
  }
  return '剪贴板里有内容，但不是文字（' + kinds.join('、') + '）。';
}

/* ---------------------------------------------- 预览用的字符切分 */

/* 按"人眼看到的一个字"来切，而不是按 UTF-16 码元 ——
   否则 emoji 会被劈成半个，显示成乱码方块。 */
let graphemeSeg = null;
try {
  if (typeof Intl !== 'undefined' && Intl.Segmenter) {
    graphemeSeg = new Intl.Segmenter('zh', { granularity: 'grapheme' });
  }
} catch (e) { graphemeSeg = null; }

function toChars(s) {
  const str = String(s || '');
  if (graphemeSeg) {
    const out = [];
    for (const seg of graphemeSeg.segment(str)) out.push(seg.segment);
    return out;
  }
  return Array.from(str);
}

/* 先归一化空白（否则剪贴板开头的换行会占掉整个预览），再切。 */
function makePreview(text) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  const chars = toChars(normalized);
  const truncated = chars.length > PREVIEW_LIMIT;
  return {
    preview: truncated ? chars.slice(0, PREVIEW_LIMIT).join('') + '…' : chars.join(''),
    totalChars: chars.length,
    truncated: truncated
  };
}

/* 给列表里的一项补上展示用的字段。 */
function decorate(item) {
  const p = makePreview(item.text);
  return {
    text: item.text,
    source: item.source,
    preview: p.preview,
    totalChars: p.totalChars,
    truncated: p.truncated
  };
}

/* ---------------------------------------------- 解析候选列表

   预览和真实发送共用这一个函数 —— 这是"所见即所发"的唯一保证。

   list[0] 永远是「本次将要发送的那一份」，取值优先级：
     ① 选中文字 → ② 剪贴板 → ③ 页面标题+URL
   list[1..] 是历史里更旧的内容（去重后，最多凑满 HISTORY_MAX 项）。

   providedClip：点图标那条路上，弹窗自己读到的剪贴板结果。
                 按快捷键时为 undefined，改用注入页面读到的结果。 */

async function resolveContent(providedClip) {
  let tab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    tab = tabs && tabs[0];
  } catch (e) { /* 忽略 */ }

  if (!tab || tab.id == null) {
    return {
      ok: false,
      reason: '找不到当前标签页。',
      detail: '请先点开一个网页再来。'
    };
  }

  const probe = await probePage(tab.id);
  const restricted = !!probe.restricted;

  const fromPopup = normalizeClip(providedClip);
  const fromPage = normalizeClip(probe);
  const clip = pickClip(fromPopup, fromPage);

  /* 「触发时快照」那一路：把此刻观察到的剪贴板收进历史（自动去重）。
     网页复制监听漏掉的情况 —— 网页自带复制按钮、在别的程序里复制 ——
     全靠它兜住。 */
  const history = await pushHistory(clip.text, '触发快照');

  const list = [];
  let note = '';

  if (!restricted && probe.selection) {
    /* 页面上还有选中时它优先于剪贴板 —— 弹窗会照实标出"来自选中文字"，
       这样即使和你以为的不一样，你也能在列表里看见。 */
    list.push({ text: String(probe.selection).trim(), source: '选中文字' });
  } else if (clip.text) {
    list.push({ text: clip.text, source: '剪贴板' });
  } else if (!restricted && (probe.title || probe.url)) {
    const parts = [];
    if (probe.title) parts.push(probe.title);
    if (probe.url) parts.push(probe.url);
    list.push({ text: parts.join('\n'), source: '页面标题+链接' });

    if (!clip.ok) {
      const diag = [];
      const e1 = cleanErr(fromPopup.error);
      const e2 = cleanErr(fromPage.error);
      if (e1) diag.push('弹窗：' + e1);
      if (e2) diag.push('页面：' + e2);
      note = '读不到剪贴板' +
        (diag.length ? '（' + diag.join('；') + '）' : '') +
        '，所以这次改发页面信息。';
    } else {
      note = describeKinds(clip.kinds) || '剪贴板里没有文字，所以这次改发页面信息。';
    }
  } else if (restricted) {
    /* 页面受限时拿不到选中文字和标题，但剪贴板若有文字上面就已经用掉了。
       走到这里说明两个来源都没有。 */
    return {
      ok: false,
      restricted: true,
      reason: '这个页面 Chrome 不允许扩展读取。',
      detail: 'chrome:// 开头的设置页、扩展商店、新标签页都属于这类。' +
        '这次剪贴板里也没有文字，所以没东西可发。'
    };
  }

  const first = list.length ? list[0].text : '';
  if (!first) {
    return {
      ok: false,
      reason: '没拿到任何内容。',
      detail: '既没有选中文字，剪贴板也是空的。'
    };
  }

  /* 补上更旧的候选。跳过和第一项重复的那一条，最多凑满 HISTORY_MAX 项。 */
  for (let i = 0; i < history.length && list.length < HISTORY_MAX; i++) {
    const h = history[i];
    if (h === first) continue;
    if (list.some((x) => x.text === h)) continue;
    list.push({ text: h, source: '剪贴板历史' });
  }

  return { ok: true, list: list, note: note, restricted: false };
}

/* ---------------------------------------------- 统一失败出口 */

async function fail(reason, detail) {
  await setBadge('ERR', '#E24B4A', 0);
  await notify('没发出去', reason + (detail ? '\n' + detail : ''));
  lastResult = { ok: false, at: Date.now(), reason: reason, detail: detail || '' };
  return lastResult;
}

/* ---------------------------------------------- 主流程 */

/* pickText：弹窗里当前被选中的那条原文。
   传了它就以它为准 —— 精确匹配候选列表，匹配不到就直接发这份原文。
   这样即使列表在两次请求之间发生了变化，"你看到的那条"也一定是
   "发出去的那条"，不会悄悄换成别的。
   按快捷键时没有界面，不传，发 list[0]（= 最新那条）。 */
async function runSend(providedClip, pickText) {
  const r = await resolveContent(providedClip);
  if (!r.ok) {
    return await fail(r.reason, r.detail);
  }

  const want = String(pickText == null ? '' : pickText).trim();
  let content = '';
  let source = '';

  if (want) {
    const hit = r.list.find((x) => x.text === want);
    if (hit) {
      content = hit.text;
      source = hit.source;
    } else {
      content = want;
      source = '候选列表';
    }
  } else {
    content = r.list[0].text;
    source = r.list[0].source;
  }

  let payload = null;
  try {
    const res = await fetch(BRIDGE_SEND, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content })
    });
    payload = await res.json().catch(() => null);
    if (!res.ok) {
      return await fail(
        '本地桥返回了错误状态 HTTP ' + res.status + '。',
        payload ? JSON.stringify(payload).slice(0, 300) : ''
      );
    }
  } catch (e) {
    return await fail(
      '连不上本地桥（127.0.0.1:18761）。',
      '最常见的原因：桥程序没在运行。双击 bridge\\start_bridge.bat 启动它。'
    );
  }

  if (!payload || payload.ok !== true) {
    const reason = (payload && payload.message) ? payload.message : '桥程序报告发送失败。';
    const detail = (payload && payload.error) ? 'code=' + payload.error : '';
    return await fail(reason, detail);
  }

  await setBadge('OK', '#1D9E75', 2500);
  lastResult = {
    ok: true,
    at: Date.now(),
    source: source,
    content: content,
    msg_id: payload.msg_id || '',
    has_url: !!payload.has_url
  };
  return lastResult;
}

/* ---------------------------------------------- 给弹窗的预览 */

async function getPreview(providedClip) {
  const r = await resolveContent(providedClip);

  if (!r.ok) {
    return {
      ok: false,
      restricted: !!r.restricted,
      reason: r.reason,
      detail: r.detail || '',
      list: [],
      note: '',
      max: HISTORY_MAX,
      stats: {}
    };
  }

  return {
    ok: true,
    restricted: false,
    list: r.list.map(decorate),
    note: r.note || '',
    /* 上限，供弹窗在"没凑满"时说明原因 */
    max: HISTORY_MAX,
    /* 每一路来源各记下了几条 —— 弹窗底部会用一行小字显示 */
    stats: (await readStore()).stats
  };
}

/* ---------------------------------------------- 桥健康检查 */

async function getHealth() {
  try {
    const res = await fetch(BRIDGE_HEALTH, { cache: 'no-store' });
    const data = await res.json();
    return { ok: true, data: data };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/* ---------------------------------------------- 给已打开的标签页补装监听

   manifest 里的 content_scripts 只对「之后加载的页面」生效。扩展一
   重载（或刚装、刚升级），此刻已经开着的标签页里就没有 content.js ——
   你在那些页面上怎么复制，扩展都听不见。表现就是：候选列表永远只有
   弹窗自己读到的那一条。

   所以这里在扩展被安装 / 更新 / 重载，以及 service worker 冷启动时，
   主动给现有标签页补注入一次。content.js 自己带重复注入保护
   （__qqSendSnifferReady），多注入几次等于空操作。

   注意：要注入 iframe（allFrames），因为公众号 / 头条 / 知乎这类
   编辑器页面的正文常常在一个 iframe 里，复制就发生在那个 iframe 内。 */

const CS_KEY = 'csInjectedFor';

async function injectIntoAllTabs() {
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({});
  } catch (e) {
    return;
  }

  for (const t of (tabs || [])) {
    if (!t || t.id == null) continue;
    /* chrome:// 页面、扩展商店、被冻结或已丢弃的标签页都注入不了，
       逐个 try 住，一个失败不影响其余的。 */
    try {
      await chrome.scripting.executeScript({
        target: { tabId: t.id, allFrames: true },
        files: ['content.js']
      });
    } catch (e) { /* 跳过这个标签页 */ }
  }
}

/* 一个浏览器会话里，每个版本只在冷启动时扫一次，避免 service worker
   每次被唤醒都把几十个标签页过一遍。 */
async function ensureContentScripts() {
  const v = chrome.runtime.getManifest().version;
  try {
    const o = await chrome.storage.session.get(CS_KEY);
    if (o && o[CS_KEY] === v) return;
  } catch (e) { /* 读不到就照做 */ }

  await injectIntoAllTabs();

  try { await chrome.storage.session.set({ [CS_KEY]: v }); } catch (e) { /* 忽略 */ }
}

/* 安装 / 升级 / 在 chrome://extensions 里点「重新加载」都会走到这里，
   此时强制补装一次 —— 不等版本号，保证"一点重载就立刻生效"。 */
chrome.runtime.onInstalled.addListener(() => {
  injectIntoAllTabs();
});

/* service worker 冷启动（浏览器刚打开、或闲置被回收后又被唤醒）。 */
ensureContentScripts();

/* ---------------------------------------------- 事件入口 */

chrome.commands.onCommand.addListener((command) => {
  if (command === 'send-to-qq') {
    /* 按快捷键时页面还有焦点，剪贴板由 probePage 在页面里读。
       没有界面可挑，所以永远发最新那条（list[0]）。 */
    runSend();
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return;

  if (msg.type === 'do-send') {
    /* msg.clipboard 是弹窗自己读到的结果（弹窗才持有焦点）；
       msg.pickText 是弹窗里当前选中的那条原文。 */
    runSend(msg.clipboard, msg.pickText)
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, reason: String(e) }));
    return true;
  }
  if (msg.type === 'get-preview') {
    getPreview(msg.clipboard).then(sendResponse).catch((e) => {
      sendResponse({ ok: false, reason: String(e), detail: '', restricted: false, list: [], note: '', max: HISTORY_MAX, stats: {} });
    });
    return true;
  }
  if (msg.type === 'get-health') {
    getHealth().then(sendResponse).catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
  if (msg.type === 'get-last') {
    sendResponse({ ok: true, last: lastResult });
    return true;
  }
  if (msg.type === 'clip-captured') {
    /* 来自 content.js：网页里刚发生一次复制/剪切/剪贴板快照。
       msg.how 说明是哪一路（网页复制 / 网页剪切 / 切回页面 /
       切回标签页 / 页面剪贴板），用于统计"哪一路没在工作"。 */
    pushHistory(msg.text, msg.how)
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (msg.type === 'clear-history') {
    /* 计数也一起归零 —— "清空"就该是干干净净的，不留半个数字让人猜。 */
    editStore((st) => { st.items = []; st.stats = {}; return []; })
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }
});
