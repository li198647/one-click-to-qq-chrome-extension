/* 弹窗：预览「本次将要发送的内容」，显示桥的状态，并提供
   "我不知道快捷键"时的兜底入口。

   预览和发送都由后台的 resolveContent() 取值，所以弹窗里显示的
   就是真正会发出去的那一份。

   ⚠️ 剪贴板为什么在弹窗里读（而不是在后台）：
   Chrome 规定 navigator.clipboard.readText() 只能在「当前有焦点的
   document」里调用，否则报 Document is not focused。点开图标时，
   弹窗正是唯一持有焦点的那个 document —— 所以由它读最合适。
   （之前放在隐藏的 offscreen 文档里读，必然失败。）
   弹窗读到的结果随消息传给后台，后台再和「选中文字 / 页面信息」
   一起走同一条取值链。 */

const dot = document.getElementById('dot');
const statusText = document.getElementById('statusText');
const detail = document.getElementById('detail');
const sendBtn = document.getElementById('sendBtn');
const lastLine = document.getElementById('lastLine');

const pv = document.getElementById('pv');
const pvHead = document.getElementById('pvHead');
const pvBody = document.getElementById('pvBody');
const pvMeta = document.getElementById('pvMeta');

function setStatus(kind, text) {
  dot.className = 'dot' + (kind ? ' ' + kind : '');
  statusText.textContent = text;
}

function setPreview(kind, head, body, meta) {
  pv.className = 'pv' + (kind ? ' ' + kind : '');
  pvHead.textContent = head || '';
  pvBody.textContent = body || '';
  pvBody.style.display = body ? 'block' : 'none';
  pvMeta.textContent = meta || '';
  pvMeta.style.display = meta ? 'block' : 'none';
}

function showDetail(text, isError) {
  detail.textContent = text;
  detail.className = 'show' + (isError ? ' err' : '');
}

function hideDetail() {
  detail.textContent = '';
  detail.className = '';
}

function fmtTime(ms) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

/* ---------------------------------------------- 在弹窗里读剪贴板 */

/* 把 Chrome 抛的长错误裁成一句能看的短话。 */
function briefError(e) {
  let s = String((e && e.message) ? e.message : e).trim();
  s = s
    .replace(/^NotAllowedError:\s*/, '')
    .replace(/^Failed to execute 'readText' on 'Clipboard':\s*/, '')
    .replace(/^Failed to execute 'read' on 'Clipboard':\s*/, '');
  return s.slice(0, 160);
}

/* 返回 { clipOk, clipText, clipError, clipKinds, where }，字段名和
   background.js 里 probePage() 的完全一致，便于统一处理。 */
async function readClipboardHere() {
  const out = {
    clipOk: false,
    clipText: '',
    clipError: '',
    clipKinds: [],
    where: '弹窗'
  };

  if (!navigator.clipboard || !navigator.clipboard.readText) {
    out.clipError = '这个浏览器环境不支持读取剪贴板';
    return out;
  }

  /* 弹窗刚弹出来的那一瞬间可能还没拿到焦点，这种情况值得等几十毫秒
     再试；剪贴板偶尔被别的程序短暂占用也一样。两类都可能自愈，
     所以一律重试，三次都不行才放弃（权限被硬拦的除外，重试也白搭）。 */
  for (let i = 0; i < 3; i++) {
    try {
      out.clipText = String((await navigator.clipboard.readText()) || '').trim();
      out.clipOk = true;
      out.clipError = '';
      break;
    } catch (e) {
      out.clipError = briefError(e);
      if (i < 2) {
        await new Promise((r) => setTimeout(r, 80));
        continue;
      }
      break;
    }
  }

  if (!out.clipOk) return out;

  /* 只在"读到了、但一个字都没有"时才多花一次调用，分辨剪贴板里到底
     装的是图片、文件、还是真的空。 */
  if (!out.clipText) {
    try {
      if (navigator.clipboard.read) {
        const items = await navigator.clipboard.read();
        const kinds = [];
        for (const it of (items || [])) {
          for (const t of (it.types || [])) {
            if (kinds.indexOf(t) === -1) kinds.push(t);
          }
        }
        out.clipKinds = kinds;
      }
    } catch (e) { /* 分辨不出来就退回到"剪贴板里没有文字"这句 */ }
  }

  return out;
}

/* ---------------------------------------------- 预览 */

let lastPreviewOk = false;

async function refreshPreview() {
  lastPreviewOk = false;
  setPreview('', '正在读取剪贴板…', '', '');
  sendBtn.disabled = true;

  /* 先在这里读剪贴板（弹窗此刻持有焦点），再连结果一起交给后台。 */
  const clip = await readClipboardHere();

  let r = null;
  try {
    r = await chrome.runtime.sendMessage({ type: 'get-preview', clipboard: clip });
  } catch (e) {
    r = { ok: false, reason: briefError(e), detail: '' };
  }

  if (!r || !r.ok) {
    const restricted = !!(r && r.restricted);
    setPreview(
      'err',
      (r && r.reason) || '拿不到要发送的内容',
      (r && r.detail) || '',
      restricted ? '这个页面发不了，按钮已停用。' : '按钮已停用。'
    );
    sendBtn.disabled = true;
    return;
  }

  lastPreviewOk = true;

  const metaLines = [];
  if (r.truncated) metaLines.push('共 ' + r.totalChars.toLocaleString('zh-CN') + ' 字，只显示开头');
  if (r.note) metaLines.push(r.note);
  /* 弹窗自己没读到剪贴板、并且最终也没用上剪贴板内容时，给一条自助提示。 */
  if (!clip.clipOk && r.source !== '剪贴板') {
    metaLines.push('（关掉这个弹窗、再点一次图标可以重试读取剪贴板）');
  }

  setPreview(
    r.note ? 'warn' : '',
    '将要发送 · 来自' + r.source,
    r.preview,
    metaLines.join('\n')
  );
  sendBtn.disabled = false;
}

/* ---------------------------------------------- 桥状态 */

async function refreshHealth() {
  setStatus('', '正在检查本地桥…');
  const r = await chrome.runtime.sendMessage({ type: 'get-health' });

  if (!r || !r.ok) {
    setStatus('bad', '连不上本地桥');
    showDetail(
      '桥程序没在运行。双击 bridge\\start_bridge.bat 启动它，然后重开这个弹窗。',
      true
    );
    return;
  }

  const d = r.data || {};
  if (!d.bot_ready) {
    setStatus('bad', '桥在跑，但机器人没连上');
    showDetail((d.last_error ? '最近错误：' + d.last_error + '\n' : '') +
      '检查 bridge\\config.json 里的 appid / secret，以及网络是否通畅。', true);
    return;
  }
  if (!d.has_openid) {
    setStatus('bad', '机器人在线，但还没拿到你的 openid');
    showDetail('去手机 QQ 里给机器人发一句话，桥就会记住你的身份。', true);
    return;
  }

  setStatus('ok', '一切正常，可以发送');
  const sent = (d.send_ok || 0) + ' 成功 / ' + (d.send_fail || 0) + ' 失败';
  hideDetail();
  showDetail('机器人已上线　·　累计 ' + sent, false);
}

/* ---------------------------------------------- 上次发送（一行） */

async function refreshLast() {
  lastLine.textContent = '';
  const r = await chrome.runtime.sendMessage({ type: 'get-last' });
  if (!r || !r.ok || !r.last) return;

  const L = r.last;
  if (L.ok) {
    lastLine.textContent = '上次：' + fmtTime(L.at) + ' 发送成功（' + L.source + '）';
  } else {
    lastLine.textContent = '上次：' + fmtTime(L.at) + ' 发送失败';
  }
}

/* ---------------------------------------------- 发送 */

sendBtn.addEventListener('click', async () => {
  sendBtn.disabled = true;
  sendBtn.textContent = '发送中…';

  /* 发送前重新读一次剪贴板，保证发的是"此刻"那份内容。 */
  const clip = await readClipboardHere();

  let res = null;
  try {
    res = await chrome.runtime.sendMessage({ type: 'do-send', clipboard: clip });
  } catch (e) {
    res = { ok: false, reason: briefError(e) };
  }

  sendBtn.textContent = '立即发送';

  /* 先刷新桥状态，再显示本次结果 —— 否则失败原因会被桥状态覆盖掉。 */
  await refreshHealth();
  await refreshLast();

  if (!res || !res.ok) {
    showDetail(
      '没发出去：' + ((res && res.reason) || '未知原因') +
      ((res && res.detail) ? '\n' + res.detail : ''),
      true
    );
  } else {
    showDetail('已发送 · 来源：' + res.source, false);
  }

  await refreshPreview();
});

/* ---------------------------------------------- 启动 */

(async function init() {
  await refreshPreview();
  await refreshHealth();
  await refreshLast();
})();
