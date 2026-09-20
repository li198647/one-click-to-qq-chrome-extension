/* 弹窗：上面一块「将要发送」（文字本体被线框框住），下面一块「候选」
   （纯列表，可点选改发哪一条），再下面是发送按钮和桥的状态。

   两块刻意分开：上面是"已经定下的那一份"，下面是"可以挑的东西"。
   候选列表由后台的 resolveContent() 生成 —— 和真正发送走的是同一条
   取值链，所以框里显示什么，发出去就是什么。

   默认选中第 1 项（最新那条）。点别的行可以改选，上面的框和按钮文案
   都会跟着变。

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
const clearLink = document.getElementById('clearLink');

const sendBox = document.getElementById('sendBox');
const sbHead = document.getElementById('sbHead');
const sbText = document.getElementById('sbText');
const candsHead = document.getElementById('candsHead');
const candsTitleText = document.getElementById('candsTitleText');
const candsList = document.getElementById('candsList');
const candsNote = document.getElementById('candsNote');
const diag = document.getElementById('diag');

/* 后台返回的候选列表；sel 是当前选中的下标（默认 0 = 最新那条）。 */
let cands = [];
let sel = 0;
let noteText = '';
let lastPreviewOk = false;

function setStatus(kind, text) {
  dot.className = 'dot' + (kind ? ' ' + kind : '');
  statusText.textContent = text;
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

/* ---------------------------------------------- 底部诊断小字

   把"每一路来源各记下了几条"摊开写出来。这不是装饰：如果连着复制
   4 次、这里却写着「网页复制 ×0」，就能立刻断定网页里那套监听没生效，
   而不用去猜"是不是历史功能坏了"。顺便把版本号也带上 —— 重载扩展后
   靠它确认加载的确实是新版。 */

const SRC_LABEL = {
  '网页复制': '网页复制',
  '网页剪切': '网页剪切',
  '切回页面': '切回页面',
  '切回标签页': '切回标签页',
  '页面剪贴板': '定时巡检',
  '触发快照': '打开弹窗',
  '剪贴板': '其它'
};

function renderDiag(stats) {
  const parts = [];
  Object.keys(stats || {}).forEach(function (k) {
    const n = Number(stats[k]) || 0;
    if (n > 0) parts.push((SRC_LABEL[k] || k) + ' ×' + n);
  });

  const ver = 'v' + chrome.runtime.getManifest().version;
  diag.textContent = (parts.length ? '记录来源：' + parts.join(' · ') : '记录来源：还没有')
    + '　·　' + ver;
}

/* ---------------------------------------------- 渲染

   两块，视觉上刻意分开：
     ① 「将要发送」—— 文字本体单独套一个实线矩形。这是"已经定下的"。
     ② 「候选」—— 一个纯列表，用来改选。这是"可以挑的"。
   选中那条在两处同时高亮，所以点列表里的第 3 行，上面框里的字会跟着换。 */

function renderAll() {
  const c = cands[sel];

  /* ① 将要发送 —— 只有标题和文字本体两块。
     原来那行「共 N 字」已去掉（木木要求：不要这个提示）；
     内容超过 20 字时 preview 末尾本来就带 "…"，看得出被截断。 */
  if (c) {
    sbHead.textContent = '将要发送 · 来自' + c.source;
    sbText.style.display = 'block';
    sbText.textContent = c.preview;
    /* 悬停看全文 —— 框里只显示前 20 字，光看开头认不出是哪一条。 */
    sbText.title = c.text;
  }

  /* ② 候选列表 */
  candsList.textContent = '';
  cands.forEach(function (it, i) {
    const el = document.createElement('div');
    el.className = 'cand' + (i === sel ? ' sel' : '');
    el.dataset.i = String(i);
    el.title = it.text;

    const no = document.createElement('span');
    no.className = 'candNo';
    no.textContent = String(i + 1);

    const tx = document.createElement('span');
    tx.className = 'candTxt';
    tx.textContent = it.preview;

    el.appendChild(no);
    el.appendChild(tx);
    candsList.appendChild(el);
  });

  candsHead.style.display = cands.length ? 'flex' : 'none';
  candsTitleText.textContent = '候选 · 最近 ' + cands.length + ' 条';

  candsNote.textContent = noteText;
  candsNote.className = 'candsNote' + (noteText ? ' show' : '');

  /* 按钮文案跟着选择走，免得选中了第 3 条、按钮却还写"立即发送"。 */
  sendBtn.textContent = sel === 0 ? '立即发送' : ('发送第 ' + (sel + 1) + ' 条');
}

candsList.addEventListener('click', function (e) {
  const el = e.target && e.target.closest ? e.target.closest('.cand') : null;
  if (!el) return;
  const i = parseInt(el.dataset.i, 10);
  if (isNaN(i) || i === sel || !cands[i]) return;
  sel = i;
  renderAll();
});

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

/* ---------------------------------------------- 刷新候选列表 */

async function refreshPreview() {
  lastPreviewOk = false;
  cands = [];
  sel = 0;
  noteText = '';

  sendBox.className = 'sendbox';
  sbHead.textContent = '正在读取剪贴板…';
  sbText.textContent = '';
  sbText.title = '';
  candsHead.style.display = 'none';
  candsList.textContent = '';
  candsNote.textContent = '';
  candsNote.className = 'candsNote';
  sendBtn.disabled = true;
  sendBtn.textContent = '立即发送';

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
    sendBox.className = 'sendbox err';
    /* 出错原因并入标题：原来那行「共 N 字」已按要求去掉，如果这里
       再不写原因，出错时就只剩一个红标题，排查时无从下手。
       （.sbHead 带 white-space: pre-line，所以下面的换行会生效。） */
    sbHead.textContent = ((r && r.reason) || '拿不到要发送的内容') +
      ((r && r.detail) ? '\n' + r.detail : '') +
      (restricted ? '\n这个页面发不了，按钮已停用。' : '\n按钮已停用。');
    /* 没内容可发时把那个线框收起来 —— 留一个空框只会让人以为漏了东西。 */
    sbText.style.display = 'none';
    sendBtn.disabled = true;
    renderDiag((r && r.stats) || {});
    return;
  }

  cands = Array.isArray(r.list) ? r.list : [];
  noteText = r.note || '';
  renderDiag(r.stats || {});

  if (!cands.length) {
    sendBox.className = 'sendbox err';
    sbHead.textContent = '拿不到要发送的内容\n候选列表是空的。按钮已停用。';
    sbText.style.display = 'none';
    sendBtn.disabled = true;
    return;
  }

  /* 弹窗自己没读到剪贴板、并且最终也没用上剪贴板内容时，给一条自助提示。 */
  if (!clip.clipOk && cands[0].source !== '剪贴板') {
    noteText = (noteText ? noteText + '\n' : '') +
      '（关掉这个弹窗、再点一次图标可以重试读取剪贴板）';
  }

  /* 候选没凑满时说明原因 —— 否则看起来像"功能坏了"。
     扩展只在少数几个瞬间读得到剪贴板，漏掉的补不回来。 */
  const max = r.max || 4;
  if (cands.length < max) {
    noteText = (noteText ? noteText + '\n' : '') +
      '候选 ' + cands.length + '/' + max + ' 条。扩展只在「网页里复制」' +
      '「切回页面」「打开这个弹窗 / 按 Ctrl+B」这几个瞬间读得到剪贴板。';
  }

  lastPreviewOk = true;
  /* 候选数量不足这一类说明只出现在候选区，不再把上面那个"将要发送"框
     染成黄色 —— 框里的内容本身是没问题的。 */
  sendBox.className = 'sendbox';
  renderAll();
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

/* ---------------------------------------------- 清空历史 */

clearLink.addEventListener('click', async function () {
  clearLink.textContent = '已清空';
  try {
    await chrome.runtime.sendMessage({ type: 'clear-history' });
  } catch (e) { /* 忽略 */ }
  await refreshPreview();
  setTimeout(function () { clearLink.textContent = '清空历史'; }, 1200);
});

/* ---------------------------------------------- 发送 */

sendBtn.addEventListener('click', async () => {
  /* 记下"你此刻看到并选中的那一条原文"。发送时按原文精确匹配，
     匹配不到就直接发它 —— 保证看到什么就发什么。 */
  const pick = cands[sel];

  sendBtn.disabled = true;
  sendBtn.textContent = '发送中…';

  /* 发送前重新读一次剪贴板，保证发的是"此刻"那份内容。 */
  const clip = await readClipboardHere();

  let res = null;
  try {
    res = await chrome.runtime.sendMessage({
      type: 'do-send',
      clipboard: clip,
      pickText: pick ? pick.text : ''
    });
  } catch (e) {
    res = { ok: false, reason: briefError(e) };
  }

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
