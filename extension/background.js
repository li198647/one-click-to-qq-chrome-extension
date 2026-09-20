/* ============================================================
   一键发到 QQ · 后台脚本（MV3 service worker）
   职责：接收快捷键 / 弹窗的发送指令，抓内容，POST 给本地桥。
   ============================================================ */

const BRIDGE = 'http://127.0.0.1:18761';
const BRIDGE_SEND = BRIDGE + '/send';
const BRIDGE_HEALTH = BRIDGE + '/health';

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

/* ---------------------------------------------- 读剪贴板（offscreen） */

let offscreenPending = null;

async function ensureOffscreen() {
  try {
    const has = await chrome.offscreen.hasDocument();
    if (has) return true;
  } catch (e) { /* 忽略，继续尝试创建 */ }

  if (!offscreenPending) {
    offscreenPending = chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['CLIPBOARD'],
      justification: '读取剪贴板里的文字，用于发送到手机 QQ'
    }).catch(() => { }).then(() => {
      offscreenPending = null;
    });
  }
  try { await offscreenPending; } catch (e) { /* 忽略 */ }
  return true;
}

async function readClipboard() {
  try {
    await ensureOffscreen();
    const r = await chrome.runtime.sendMessage({
      target: 'offscreen',
      type: 'read-clipboard'
    });
    if (r && r.ok && r.text) return String(r.text).trim();
  } catch (e) { /* 读不到就当没有 */ }
  return '';
}

/* ---------------------------------------------- 抓页面信息 */

async function grabPageInfo(tabId) {
  try {
    const r = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: () => ({
        selection: String(window.getSelection ? window.getSelection().toString() : '').trim(),
        title: document.title || '',
        url: location.href || ''
      })
    });
    if (r && r[0] && r[0].result) return r[0].result;
  } catch (e) {
    return { restricted: true, selection: '', title: '', url: '' };
  }
  return { restricted: true, selection: '', title: '', url: '' };
}

/* ---------------------------------------------- 统一失败出口 */

async function fail(reason, detail) {
  await setBadge('ERR', '#E24B4A', 0);
  await notify('没发出去', reason + (detail ? '\n' + detail : ''));
  lastResult = { ok: false, at: Date.now(), reason: reason, detail: detail || '' };
  return lastResult;
}

/* ---------------------------------------------- 主流程 */

async function runSend() {
  let tab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    tab = tabs && tabs[0];
  } catch (e) { /* 忽略 */ }

  if (!tab || tab.id == null) {
    return await fail('找不到当前标签页。', '请先点开一个网页再按快捷键。');
  }

  const info = await grabPageInfo(tab.id);

  if (info.restricted) {
    return await fail(
      '这个页面 Chrome 不允许扩展读取。',
      'chrome:// 开头的设置页、扩展商店、新标签页都属于这类。换个普通网页再试。'
    );
  }

  const clip = await readClipboard();

  let content = '';
  let source = '';
  if (info.selection) {
    content = info.selection;
    source = '选中文字';
  } else if (clip) {
    content = clip;
    source = '剪贴板';
  } else {
    const parts = [];
    if (info.title) parts.push(info.title);
    if (info.url) parts.push(info.url);
    content = parts.join('\n');
    source = '页面标题+链接';
  }

  content = (content || '').trim();
  if (!content) {
    return await fail('没拿到任何内容。', '既没有选中文字，剪贴板也是空的。');
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
    runSend();
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.target === 'offscreen') return;

  if (msg.type === 'do-send') {
    runSend().then(sendResponse).catch((e) => sendResponse({ ok: false, reason: String(e) }));
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
