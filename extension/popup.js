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
   一起走同一条取值链。

   ⚠️ 1.0.1 起剪贴板里可能是一张图片：
   读文字失败（一个字都没有）时，顺手把图片也取出来一起交给后台。
   刻意**只在没有文字时**才去取图 —— 剪贴板里同时有图和文字时（比如
   从网页复制"图 + 说明"），仍然按老规矩发文字，不破坏已在用的行为。
   图片的候选行会带一个小徽标，发送框里则画一张真缩略图。

   ⚠️ 1.0.4 起状态行会多出一个「启动本地桥」按钮：
   重启电脑后桥不会自己跑起来，从前得去磁盘里翻 start_bridge.bat。
   现在只在"连不上本地桥"时出现，点一下由宿主程序把桥拉起来。
   实现在下面 waitBridgeUp / waitBotReady 那一段。 */

const dot = document.getElementById('dot');
const statusText = document.getElementById('statusText');
const bridgeBtn = document.getElementById('bridgeBtn');
const detail = document.getElementById('detail');
const sendBtn = document.getElementById('sendBtn');
const lastLine = document.getElementById('lastLine');
const clearLink = document.getElementById('clearLink');

const sendBox = document.getElementById('sendBox');
const sbHead = document.getElementById('sbHead');
const sbText = document.getElementById('sbText');
const sbThumbWrap = document.getElementById('sbThumbWrap');
const sbThumb = document.getElementById('sbThumb');
const sbThumbMeta = document.getElementById('sbThumbMeta');
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

/* ---------------------------------------------- 「启动本地桥」按钮（v1.0.4）

   只有"连不上本地桥"这一种状态下它才有意义 —— 桥在跑但机器人没连上、
   缺 openid、正在检查，这三种情况下点了都没用，所以平时整条不出现。

   ⚠️ 启动过程**不许静默**：点下去如果失败，原因原样摊在下面那行红字里；
   成功也要等桥真的应答了（轮询 /health）才敢说"起来了"。 */

const BRIDGE_POLL_MS = 1500;    // 两次探问之间的间隔
const BRIDGE_UP_MS = 30000;     // 第一段：等桥的 HTTP 服务应答
const BRIDGE_BOT_MS = 20000;    // 第二段：服务起来了，再等机器人登录

/* launching 期间 refreshHealth() 不许动这个按钮 —— 否则每轮健康检查都会
   把"正在启动…"刷回"启动本地桥"，看起来像自己抖。 */
let launching = false;

function showBridgeBtn(on) {
  bridgeBtn.style.display = on ? '' : 'none';
}

function sleep(ms) {
  return new Promise(function (r) { setTimeout(r, ms); });
}

function elapsedSec(t0) {
  return Math.round((Date.now() - t0) / 1000);
}

async function pollHealthOnce() {
  try {
    return await chrome.runtime.sendMessage({ type: 'get-health' });
  } catch (e) {
    return null;
  }
}

/* 第一段：等桥的 HTTP 服务应答（最长 BRIDGE_UP_MS）。返回是否等到了。

   ⚠️ 秒数刻意写在**按钮**上，不写在状态行里 —— 状态行只有 272px，塞了
   按钮之后余量只剩不到 50px（实测）。写成「正在启动本地桥…已等 12 秒」
   就只有 10px 余量了，字体一换就会掉成两行。 */
async function waitBridgeUp(t0) {
  while (Date.now() - t0 < BRIDGE_UP_MS) {
    await sleep(BRIDGE_POLL_MS);
    const h = await pollHealthOnce();
    if (h && h.ok) return true;
    bridgeBtn.textContent = '启动中…' + elapsedSec(t0) + 's';
  }
  return false;
}

/* 第二段：服务已经起来了，机器人登录还要几秒。这一段是锦上添花 ——
   超时不报错，真实状态交给 refreshHealth() 照实显示。 */
async function waitBotReady() {
  const t0 = Date.now();
  setStatus('', '正在连接机器人…');
  while (Date.now() - t0 < BRIDGE_BOT_MS) {
    const h = await pollHealthOnce();
    if (h && h.ok && h.data && h.data.bot_ready && h.data.has_openid) return true;
    await sleep(BRIDGE_POLL_MS);
  }
  return false;
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

function renderDiag(stats, storeWarn) {
  const parts = [];
  Object.keys(stats || {}).forEach(function (k) {
    const n = Number(stats[k]) || 0;
    if (n > 0) parts.push((SRC_LABEL[k] || k) + ' ×' + n);
  });

  const ver = 'v' + chrome.runtime.getManifest().version;
  diag.textContent = (parts.length ? '记录来源：' + parts.join(' · ') : '记录来源：还没有')
    + '　·　' + ver;

  /* 历史被挡下或写入降级时补一行红字。正常情况下是空串，这行就不出现。
     这一行的存在本身就是防线②的目的：把原来被空 catch 吞掉的事说出来。 */
  if (storeWarn) {
    const w = document.createElement('span');
    w.className = 'diagWarn';
    w.textContent = '注意 · ' + storeWarn;
    diag.appendChild(w);
  }
}

/* ---------------------------------------------- 图片那一行的说明文字 */

/* 「图片 · 1000×750 · 245 KB」。宽高读不出来时只显示体积 ——
   尺寸那格宁可留空，也不能编一个数出来。

   体积这里自己算，刻意不用 imageutil.js 里的同名函数：渲染这一层不该
   依赖另一个文件在不在，万一漏打包，宁可少一个数字也不要整个弹窗白屏。 */
function humanSize(n) {
  const b = Number(n) || 0;
  if (b < 1024) return b + ' B';
  if (b < 1024 * 1024) return Math.round(b / 1024) + ' KB';
  return (Math.round(b / 1024 / 102.4) / 10) + ' MB';
}

function imgMetaLine(im) {
  const x = im || {};
  const parts = ['图片'];
  if (x.width && x.height) parts.push(x.width + '×' + x.height);
  parts.push(humanSize(x.bytes));
  return parts.join(' · ');
}

/* 候选行前面那个小徽标（内联 SVG，不引外部图片文件）。
   用 currentColor，颜色跟着 CSS 走。 */
const SVGNS = 'http://www.w3.org/2000/svg';

function imgBadge() {
  const s = document.createElementNS(SVGNS, 'svg');
  s.setAttribute('viewBox', '0 0 16 16');
  s.setAttribute('width', '14');
  s.setAttribute('height', '14');
  s.setAttribute('class', 'candIcon');

  const r = document.createElementNS(SVGNS, 'rect');
  r.setAttribute('x', '1.6'); r.setAttribute('y', '2.6');
  r.setAttribute('width', '12.8'); r.setAttribute('height', '10.8');
  r.setAttribute('rx', '1.8');
  r.setAttribute('fill', 'none');
  r.setAttribute('stroke', 'currentColor');
  r.setAttribute('stroke-width', '1.3');

  const p = document.createElementNS(SVGNS, 'path');
  p.setAttribute('d', 'M3.2 11.2 L6.2 7.6 L8.3 9.9 L10.1 8.6 L12.8 11.2 Z');
  p.setAttribute('fill', 'currentColor');

  const c = document.createElementNS(SVGNS, 'circle');
  c.setAttribute('cx', '5.7'); c.setAttribute('cy', '5.7');
  c.setAttribute('r', '1.1');
  c.setAttribute('fill', 'currentColor');

  s.appendChild(r);
  s.appendChild(p);
  s.appendChild(c);
  return s;
}

/* 把缩略图收起来，并**断开对 dataURL 的引用**。
   一张 4MB 的图转成 dataURL 是 5MB 以上的字符串，挂在 <img> 上不会
   自己释放；显式清掉 src 才能让它被回收。 */
function clearThumb() {
  sbThumbWrap.style.display = 'none';
  sbThumb.removeAttribute('src');
  sbThumbMeta.textContent = '';
}

/* ---------------------------------------------- 渲染

   两块，视觉上刻意分开：
     ① 「将要发送」—— 已经定下的那一份（文字框 / 缩略图）。
     ② 「候选」—— 一个纯列表，用来改选。这是"可以挑的"。
   选中那条在两处同时高亮，所以点列表里的第 3 行，上面框里的字会跟着换。 */

function renderAll() {
  const c = cands[sel];

  /* ① 将要发送。两种形态互斥，不会同时出现：
     文字 = 线框框住的一段字；图片 = 真缩略图 + 尺寸体积。
     原来那行「共 N 字」已去掉（木木要求：不要这个提示）；
     内容超过 20 字时 preview 末尾本来就带 "…"，看得出被截断。 */
  if (c) {
    sbHead.textContent = '将要发送 · 来自' + c.source;

    if (c.kind === 'image' && c.image) {
      sbText.style.display = 'none';
      sbText.textContent = '';
      sbText.removeAttribute('title');
      sbThumbWrap.style.display = 'block';
      sbThumb.src = c.image.dataUrl;
      sbThumbMeta.textContent = imgMetaLine(c.image) +
        (c.image.name ? '　·　' + c.image.name : '');
    } else {
      clearThumb();
      sbText.style.display = 'block';
      sbText.textContent = c.preview;
      /* 悬停看全文 —— 框里只显示前 20 字，光看开头认不出是哪一条。 */
      sbText.title = c.text;
    }
  }

  /* ② 候选列表。图片项前面加一个小徽标，把"没有文字可预览"那一格换成
     「图片 · 1000×750 · 245 KB」—— 既看得出是图，又看得出是哪张。 */
  candsList.textContent = '';
  cands.forEach(function (it, i) {
    const el = document.createElement('div');
    el.className = 'cand' + (i === sel ? ' sel' : '');
    el.dataset.i = String(i);

    const no = document.createElement('span');
    no.className = 'candNo';
    no.textContent = String(i + 1);

    el.appendChild(no);

    const tx = document.createElement('span');
    tx.className = 'candTxt';

    if (it.kind === 'image' && it.image) {
      const line = imgMetaLine(it.image);
      el.title = line + (it.image.name ? '　' + it.image.name : '');
      el.appendChild(imgBadge());
      tx.textContent = line;
    } else {
      el.title = it.text;
      tx.textContent = it.preview;
    }

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
    clipImage: null,
    where: '弹窗'
  };

  /* ⚠️ 这里刻意**不**做 Permissions-Policy 判断（`qqImg.clipReadAllowed`）。
     受站点策略管的是 content script 和注入到页面里的探针 —— 它们是"住在
     别人家"的文档；弹窗是扩展自己的页面（chrome-extension://），策略由我们
     自己的 manifest 决定，不受当前网站影响。在这一处多拦一道只会多一个
     "误判成读不了"的风险，而它偏偏是唯一可靠能读到剪贴板的那条路。 */
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

  /* 只在"读到了、但一个字都没有"时才多花调用，做两件事：
     ① 分辨剪贴板里到底装的是图片、文件、还是真的空
     ② v1.0.1：如果是图片，把它取出来 —— 这就是"一键传图"的入口。
     刻意等文字优先：剪贴板里同时有图和文字时仍然发文字，
     不破坏已经在用的行为。 */
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
        /* 取图会解码一次图片（拿宽高），有点耗时 —— 只在真的没有文字
           时才做，正常发文字那一路一点开销都没增加。
           qqImg 来自 imageutil.js（popup.html 里排在 popup.js 之前）。
           万一它不在，就当"剪贴板里没有图"，文字那套照旧能用。 */
        if (typeof qqImg !== 'undefined' && qqImg && qqImg.pickImageFromClipItems) {
          out.clipImage = await qqImg.pickImageFromClipItems(items);
        }
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
  clearThumb();
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
    clearThumb();
    sendBtn.disabled = true;
    renderDiag((r && r.stats) || {}, (r && r.storeWarn) || '');
    return;
  }

  cands = Array.isArray(r.list) ? r.list : [];
  noteText = r.note || '';
  renderDiag(r.stats || {}, r.storeWarn || '');

  if (!cands.length) {
    sendBox.className = 'sendbox err';
    sbHead.textContent = '拿不到要发送的内容\n候选列表是空的。按钮已停用。';
    sbText.style.display = 'none';
    clearThumb();
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
    /* 启动中就不要把这个按钮抢回来 —— 让"正在启动…"留在那儿。 */
    if (!launching) showBridgeBtn(true);
    showDetail(
      '桥程序没在运行。点右边的「启动本地桥」把它拉起来；' +
      '不行就双击 bridge\\start_bridge.bat。',
      true
    );
    return;
  }

  /* 走到这里说明桥的 HTTP 服务活着 —— 那个按钮已经没有用了。 */
  showBridgeBtn(false);

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

/* 点「启动本地桥」：喊宿主去拉桥，然后自己盯着它起没起来。

   注意这里**没有**超时兜底就宣布成功的分支 —— 只有轮询到桥真的应答了
   才说"好了"；等不到就把话说明白。宁可告诉你"没起来"，也不假装成功。 */
bridgeBtn.addEventListener('click', async function () {
  if (launching) return;
  launching = true;
  bridgeBtn.disabled = true;
  bridgeBtn.textContent = '正在启动…';
  setStatus('', '正在启动本地桥…');
  hideDetail();

  let r = null;
  try {
    r = await chrome.runtime.sendMessage({ type: 'launch-bridge' });
  } catch (e) {
    r = { ok: false, reason: briefError(e), detail: '' };
  }

  if (!r || !r.ok) {
    launching = false;
    bridgeBtn.disabled = false;
    bridgeBtn.textContent = '启动本地桥';
    setStatus('bad', '连不上本地桥');
    showDetail(
      '没能启动本地桥：' + ((r && r.reason) || '未知原因') +
      ((r && r.detail) ? '\n' + r.detail : ''),
      true
    );
    return;
  }

  /* 宿主那边可能回报"已经在跑了"（例如你重启前它就活着）。
     那种情况不用等，直接看状态就行。 */
  const already = !!(r.data && r.data.action === 'already-running');

  const t0 = Date.now();
  const up = already ? true : await waitBridgeUp(t0);
  if (up) await waitBotReady();

  launching = false;
  bridgeBtn.disabled = false;
  bridgeBtn.textContent = '启动本地桥';
  await refreshHealth();
  await refreshLast();

  if (!up) {
    showDetail(
      '等了 ' + Math.round(BRIDGE_UP_MS / 1000) + ' 秒，桥还是没应答。\n' +
      '可以双击 bridge\\start_bridge.bat，看那个黑窗口里报了什么。',
      true
    );
  }
});

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
  /* 记下"你此刻看到并选中的那一条"的钥匙（'t:文字' 或 'i:0'）。
     发送时按钥匙精确匹配，匹配不到就直接发这份原文 ——
     保证看到什么就发什么。 */
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
      pickKey: pick ? pick.key : ''
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
