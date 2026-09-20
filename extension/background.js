/* ============================================================
   一键发到 QQ · 后台脚本（MV3 service worker）
   职责：接收快捷键 / 弹窗的发送指令，抓内容，POST 给本地桥。
   同时给弹窗提供「本次将要发送的内容」预览 —— 预览和发送走的是
   同一个 resolveContent()，所以弹窗里看到什么，发出去就是什么。

   ⚠️ 剪贴板的关键约束（0.1.1 修正）：
   Chrome 规定 navigator.clipboard.readText() 只能在「当前有焦点的
   document」里调用，否则会抛：
       NotAllowedError: Document is not focused.
   而 offscreen 文档是隐藏的、永远拿不到焦点 —— 放进去读必然失败。
   所以读取必须由「当时真正持有焦点的那个上下文」来做：
     · 点图标 → 弹窗有焦点 → 由 popup.js 读，结果随消息传进来
     · 按快捷键 → 页面还有焦点 → 由这里注入页面去读
   两边都没读到就明确报错，不静默降级成别的内容。
   ============================================================ */

const BRIDGE = 'http://127.0.0.1:18761';
const BRIDGE_SEND = BRIDGE + '/send';
const BRIDGE_HEALTH = BRIDGE + '/health';

/* 弹窗预览最多显示多少个字符。20 个字足以让链接露出域名。 */
const PREVIEW_LIMIT = 20;

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

/* ---------------------------------------------- 解析本次要发送的内容

   预览和真实发送共用这一个函数 —— 这是"所见即所发"的唯一保证。
   取值链：① 选中文字 → ② 剪贴板 → ③ 页面标题+URL

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

  let content = '';
  let source = '';
  let note = '';

  if (!restricted && probe.selection) {
    /* 页面上还有选中时它优先于剪贴板 —— 预览会照实标出"来自选中文字"，
       这样即使和你以为的剪贴板不一致，你也能在弹窗里看见。 */
    content = String(probe.selection).trim();
    source = '选中文字';
  } else if (clip.text) {
    content = clip.text;
    source = '剪贴板';
  } else if (!restricted && (probe.title || probe.url)) {
    const parts = [];
    if (probe.title) parts.push(probe.title);
    if (probe.url) parts.push(probe.url);
    content = parts.join('\n');
    source = '页面标题+链接';

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

  content = (content || '').trim();
  if (!content) {
    return {
      ok: false,
      reason: '没拿到任何内容。',
      detail: '既没有选中文字，剪贴板也是空的。'
    };
  }

  return { ok: true, content: content, source: source, note: note };
}

/* ---------------------------------------------- 统一失败出口 */

async function fail(reason, detail) {
  await setBadge('ERR', '#E24B4A', 0);
  await notify('没发出去', reason + (detail ? '\n' + detail : ''));
  lastResult = { ok: false, at: Date.now(), reason: reason, detail: detail || '' };
  return lastResult;
}

/* ---------------------------------------------- 主流程 */

async function runSend(providedClip) {
  const r = await resolveContent(providedClip);
  if (!r.ok) {
    return await fail(r.reason, r.detail);
  }

  const content = r.content;
  const source = r.source;

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
      preview: '',
      source: '',
      totalChars: 0,
      truncated: false,
      note: ''
    };
  }

  const p = makePreview(r.content);
  return {
    ok: true,
    restricted: false,
    source: r.source,
    preview: p.preview,
    totalChars: p.totalChars,
    truncated: p.truncated,
    note: r.note || ''
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

/* ---------------------------------------------- 事件入口 */

chrome.commands.onCommand.addListener((command) => {
  if (command === 'send-to-qq') {
    /* 按快捷键时页面还有焦点，剪贴板由 probePage 在页面里读。 */
    runSend();
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return;

  if (msg.type === 'do-send') {
    /* msg.clipboard 是弹窗自己读到的结果（弹窗才持有焦点）。 */
    runSend(msg.clipboard).then(sendResponse).catch((e) => sendResponse({ ok: false, reason: String(e) }));
    return true;
  }
  if (msg.type === 'get-preview') {
    getPreview(msg.clipboard).then(sendResponse).catch((e) => {
      sendResponse({ ok: false, reason: String(e), detail: '', restricted: false });
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
});
