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

   ⚠️ 1.0.1 新增「一键传图片」，入口有两个：
     · 剪贴板里的图片 —— 复制了图（截图工具 / 网页右键"复制图片"）后
       点图标或按 Ctrl+B，弹窗那套流程原样复用，只是内容从文字换成图
     · 右键网页图片 → 「发送此图片到 QQ」

   两处关键决定，都是被硬限制逼出来的，不是随手选的：

   【决定一】图片不进程候选列表（历史）。
     chrome.storage.session 的配额是 10MB，而 base64 要膨胀 4/3 ——
     4 张 2MB 的截图 = 原始 8MB = base64 后 10.7MB，直接撑爆。配额一爆
     不是"图片存不进去"这么客气，而是**整个历史写入失败**，连你攒的
     文字候选一起搞挂。所以图片只作为"本次要发的那一条"出现。

   【决定二】右键图片时由扩展后台自己下载，而不是把网址交给腾讯去下。
     实测：把 127.0.0.1 的地址丢给腾讯文档里的 url 参数，回 400
     「上传URL错误」—— 证明是**腾讯的服务器**去下载这个地址，家里没有
     公网地址，那条路彻底堵死。
     另外要提前知道：Chrome 规定扩展后台发起的跨站请求**不带目标站的
     cookie**，所以"需要登录才看得到"的图片右键发是发不了的，会明确
     报错并提示改用「复制图片」，不静默换内容。

   图片的「超 20MB 缩尺寸」和「格式被拒就转 PNG」都放在桥那边用
   Pillow 做，扩展这边只负责读图、转 base64、读出宽高。

   ⚠️ 1.0.4 新增「启动本地桥」按钮（见下面 launchBridge 那一段）：
   重启电脑后桥不会自己跑起来，以前得去磁盘里翻 start_bridge.bat。
   现在弹窗上那个按钮会经 Native Messaging 让宿主把桥拉起来。
   ============================================================ */

/* imageutil.js 必须在这里同步加载：service worker 里没有 FileReader，
   图片转 base64 和读宽高都靠它。三个环境（后台 / 弹窗 / 网页）共用
   同一份文件，所以那边写得很保守，见那个文件顶部的说明。 */
importScripts('imageutil.js');

const BRIDGE = 'http://127.0.0.1:18761';
const BRIDGE_SEND = BRIDGE + '/send';
const BRIDGE_HEALTH = BRIDGE + '/health';

/* 列表里每一项最多显示多少个字符。20 个字足以让链接露出域名。 */
const PREVIEW_LIMIT = 20;

/* 候选列表最多几项（= 保留最近几次内容）。 */
const HISTORY_MAX = 4;

/* 单条能进历史的最大体积（UTF-8 字节）。

   chrome.storage.session 的配额是 10,485,760 字节，而 set 是覆盖整个键 ——
   超限不是"这一条存不进去"，而是**这一次写入整体作废**（官方原文：
   updates that would cause this limit to be exceeded fail immediately）。
   所以 4 条 × 512KB = 2MB，只占配额 20%，留 80% 余量。

   被挡下的内容照样能发出去 —— 当前要发的那条来自剪贴板/页面，不经过历史，
   代价只是"回头改选不到它"。图片的规则和这里是一致的。 */
const STORE_ITEM_MAX = 512 * 1024;

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

/* UTF-8 字节数。中文一个字 3 字节 —— 拿 .length 当字节数会低估三倍，
   而配额是按字节算的。TextEncoder 在 service worker 里有。 */
function byteLen(s) {
  const t = String(s == null ? '' : s);
  try {
    return new TextEncoder().encode(t).length;
  } catch (e) {
    return t.length * 3;   // 兜底：按最坏情况估，宁可高估
  }
}

/* 历史出状况时留在这里的两句话，随消息带回弹窗显示。
   刻意不落进 storage —— 那正是写不进去的地方。 */
let storeWarn = '';   // 写入降级/失败
let storeSkip = '';   // 上一次被"太大"挡在历史外的说明

function storeNotice() {
  return [storeSkip, storeWarn].filter(Boolean).join(' ');
}

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

/* 写历史。撞配额时**逐级退让**，而不是静默失败。

   chrome.storage 没有事务，也没有"只写这一条" —— 每次 set 都是覆盖整个键。
   所以超限的后果是"这次写入整体作废"，不是"这条太大存不进"。反过来说，
   少写一点是真的能救回来的，不是碰运气。

   退让阶梯：4 条 → 2 条 → 1 条 → 清空。退到哪一级就把原因记下来，
   由 storeNotice() 带回弹窗 —— 从前这里是个空 catch，配额一爆你就只会
   看到"候选列表怎么一直是这几条"，而且查不出为什么。 */
async function writeStore(st) {
  const items = (st.items || []).slice(0, HISTORY_MAX);
  const stats = st.stats || {};

  /* 阶梯去重：items 本来就少于 4 条时，别重复试同样的大小 */
  const sizes = [];
  [items.length, 2, 1, 0].forEach((n) => {
    if (n <= items.length && sizes.indexOf(n) === -1) sizes.push(n);
  });

  for (let i = 0; i < sizes.length; i++) {
    const n = sizes[i];
    const cut = { items: items.slice(0, n), stats: n ? stats : {} };
    try {
      await chrome.storage.session.set({ [HISTORY_KEY]: cut });
      /* 全量写成功就把上次那句话清掉，免得旧提示一直挂着 */
      if (n === sizes[0]) storeWarn = '';
      else if (n === 0) storeWarn = '历史写不进去了（浏览器存储配额已满），这次没留下候选。';
      else storeWarn = '历史太长，只留了最近 ' + n + ' 条。';
      return cut;
    } catch (e) { /* 降到下一级再试 */ }
  }

  storeWarn = '历史写不进去了（浏览器存储配额已满），这次没留下候选。';
  return { items: [], stats: {} };
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
    const wrote = await writeStore(st);
    /* writeStore 可能因为配额被迫砍短。那种情况下以"真正写进去的"为准 ——
       否则这一轮会多报一条候选：点得动、但下次刷新就没了。 */
    if (Array.isArray(out) && Array.isArray(wrote.items) && wrote.items.length < out.length) {
      return wrote.items;
    }
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

  /* 太大就不进历史（防线①）。留一句说明 —— 否则你会以为"复制没生效"。

     真实撞线场景不是"4 张 2MB 截图"（图片压根不进历史），而是一条超大文本：
     比如从地址栏复制一个 data:image/png;base64,… 地址，那串是纯文本、几 MB，
     正好从后门绕过"图片不进历史"那道墙。一条超 10MB 就能让整次写入作废。 */
  if (t && byteLen(t) > STORE_ITEM_MAX) {
    storeSkip = '这次的内容太大（' + Math.round(byteLen(t) / 1024) +
      ' KB），没进候选历史；发送不受影响。';
    /* 只读也走同一条串行链，拿到的是排到队后的状态，不会读到写一半的数据 */
    return await editStore((st) => st.items);
  }

  storeSkip = '';
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
          selAt: 0, imgCopyAt: 0,
          clipOk: false, clipText: '', clipError: '', clipKinds: [], clipImage: null,
          where: '页面'
        };

        try {
          out.selection = String(
            window.getSelection ? window.getSelection().toString() : ''
          ).trim();
        } catch (e) { /* 忽略 */ }

        /* v1.0.2：两个时刻由 content.js 记录（同一个 isolated world）。
           拿不到（受限页面、页面还没注入 content.js）就是 0。 */
        try { out.selAt = Number(window.__qqSendSelAt) || 0; } catch (e) { /* 忽略 */ }
        try { out.imgCopyAt = Number(window.__qqSendImgCopyAt) || 0; } catch (e) { /* 忽略 */ }

        out.title = document.title || '';
        out.url = location.href || '';

        let focused = false;
        try { focused = !!document.hasFocus(); } catch (e) { /* 忽略 */ }
        if (!focused) {
          out.clipError = '页面当前没有焦点';
          return out;
        }

        /* v1.0.3：站点用 Permissions-Policy 关掉了 clipboard-read 就先别叫
           （reverso.net 就是）。这条报错由**浏览器自己打印**，不是 Promise
           的 rejection，catch 不住 —— 会在扩展的错误页里刷一条红字。
           判断用的是 content.js 那同一份（imageutil.js 提供）。 */
        if (typeof qqImg !== 'undefined' && qqImg && qqImg.clipReadAllowed
            && !qqImg.clipReadAllowed()) {
          out.clipError = '这个页面禁止读取剪贴板';
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

            /* 1.0.1：剪贴板里没有文字、但有图片时，把图片本体也取出来。
               qqImg 由 content_scripts 注入的 imageutil.js 提供 ——
               executeScript 注入的代码和 content script 在**同一个
               isolated world**，所以这个全局变量取得到。页面受限
               （chrome:// 之类）时它不存在，跳过即可，不算错误。 */
            if (typeof qqImg !== 'undefined' && qqImg && qqImg.pickImageFromClipItems) {
              out.clipImage = await qqImg.pickImageFromClipItems(items);
            }
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
   成一份形状，再挑出可用的那一份。

   ⚠️ 刻意**不深拷贝** image.dataUrl —— 一张几 MB 的图，转成 dataURL
   已经是 4/3 的字符串了，再拷一份纯属浪费内存和 CPU。它全程只被读，
   没人在原地改它。 */

function normalizeClip(x) {
  const o = { ok: false, text: '', error: '', kinds: [], image: null, where: '' };
  if (!x) return o;
  o.ok = !!x.clipOk;
  o.text = String(x.clipText || '').trim();
  o.error = String(x.clipError || '');
  if (Array.isArray(x.clipKinds)) o.kinds = x.clipKinds;
  if (x.clipImage && x.clipImage.dataUrl) o.image = x.clipImage;
  o.where = String(x.where || '');
  return o;
}

/* 挑一份最该用的剪贴板结果。优先级：
     ① 真正读到文字的    ② 真正读到图片的
     ③ 读成功但两者都没有 ④ 一份留了错误原因的（好让弹窗说清为什么）
   ①优先于②是既定规则：剪贴板里同时有图和文字时（比如从网页复制
   "图 + 说明文字"），仍然按老规矩发文字，不破坏已在用的行为。 */
function pickClip(a, b) {
  if (a && a.ok && a.text) return a;
  if (b && b.ok && b.text) return b;
  if (a && a.ok && a.image) return a;
  if (b && b.ok && b.image) return b;
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

/* 剪贴板读到了、但既没有文字也没有图片（或图片没取到）时，说清里面
   到底是什么。1.0.1 加了图片这条取值链之后，原来那句"剪贴板里是一张
   图片，没有文字"就变成会误导人的话 —— 图片本该已经被接住了。 */
function describeClipFallback(clip) {
  const kinds = (clip && clip.kinds) || [];
  let looksImage = false;
  for (const t of kinds) {
    if (String(t).indexOf('image/') === 0) { looksImage = true; break; }
  }
  if (looksImage) {
    return '剪贴板里是图片，但这次没能取到它（可能格式或权限问题），' +
      '所以改发页面信息。';
  }
  const d = describeKinds(kinds);
  return d ? (d + '所以这次改发页面信息。') : '剪贴板里没有文字，所以这次改发页面信息。';
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

/* 给列表里的一项补上展示用的字段。

   图片项也要一起发给弹窗（带 dataUrl）—— 弹窗要用它画那张缩略图。
   代价是几 MB 的字符串多走一趟消息，换来的是弹窗完全不用猜"我手里
   这份图是不是列表里那一条"，少一处隐式耦合。 */
function decorate(item) {
  if (item.kind === 'image') {
    const im = item.image || {};
    return {
      kind: 'image',
      key: item.key,
      source: item.source,
      text: '',
      preview: '',
      totalChars: 0,
      truncated: false,
      image: {
        dataUrl: im.dataUrl || '',
        mime: im.mime || '',
        width: im.width || 0,
        height: im.height || 0,
        bytes: im.bytes || 0,
        name: im.name || ''
      }
    };
  }
  const p = makePreview(item.text);
  return {
    kind: 'text',
    key: item.key,
    text: item.text,
    source: item.source,
    preview: p.preview,
    totalChars: p.totalChars,
    truncated: p.truncated,
    image: null
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

  const selText = (!restricted && probe.selection) ? String(probe.selection).trim() : '';
  const clipImage = clip.image || null;

  /* ---------------------------------------------- v1.0.2：谁当"将要发送"

     剪贴板里是图片、页面上又留着选中文字时，判据是**谁更新**，而不是
     "谁存在"。因为页面上的选中状态是个持续状态：你右键复制完那段文字
     后，高亮不会自己消失，于是它会一直把之后复制的图片挤掉 —— 这正是
     木木报的「再复制一次图片，弹窗里没有缩略图」。

     两个时刻都由 content.js 在页面里记（selectionchange / copy 事件）：
       · 选中文字比"复制图片"更晚 → 你刚选了字，发选中文字
       · 否则（含"压根没观察到复制图片这个动作"）→ 发图片
     后一种兜底是故意的：剪贴板里冒出图片，本身就说明你刚复制了一张图。 */
  const selAt = Number(probe.selAt) || 0;
  const imgCopyAt = Number(probe.imgCopyAt) || 0;
  const selIsNewer = !!(selAt > 0 && imgCopyAt > 0 && selAt > imgCopyAt);
  const imageWins = !!clipImage && !selIsNewer;

  if (imageWins) {
    /* 图片只作为"本次要发的那一条"出现，不落进历史（理由见文件头：
       storage.session 只有 10MB，而 base64 要膨胀 4/3）。 */
    list.push({ kind: 'image', image: clipImage, source: '剪贴板图片' });
  }

  if (selText) {
    list.push({ kind: 'text', text: selText, source: '选中文字' });
  }

  /* 图片没赢的时候（选中文字更晚）它仍然要进候选 —— 否则看起来就像
     "扩展又读不到图了"。两条都在列表里，点一下就能换。 */
  if (!list.length && clipImage) {
    list.push({ kind: 'image', image: clipImage, source: '剪贴板图片' });
  }

  if (!list.length && clip.text) {
    list.push({ kind: 'text', text: clip.text, source: '剪贴板' });
  }

  if (clipImage && selText) {
    note = '剪贴板里是一张图片，页面上还留着一处选中文字 —— 两条都列在候选里，' +
      '现在默认发' + (imageWins ? '图片' : '选中文字') + '，点另一条就能改。';
  }

  if (!list.length && !restricted && (probe.title || probe.url)) {
    const parts = [];
    if (probe.title) parts.push(probe.title);
    if (probe.url) parts.push(probe.url);
    list.push({ kind: 'text', text: parts.join('\n'), source: '页面标题+链接' });

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
      note = describeClipFallback(clip);
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

  const first = list.length ? list[0] : null;
  if (!first) {
    return {
      ok: false,
      reason: '没拿到任何内容。',
      detail: '既没有选中文字，剪贴板也是空的。'
    };
  }

  /* 补上更旧的候选。跳过和第一项重复的那一条，最多凑满 HISTORY_MAX 项。
     历史里只有文字，所以这里补进来的一定都是文字项。 */
  const firstText = (first.kind === 'text') ? first.text : '';
  for (let i = 0; i < history.length && list.length < HISTORY_MAX; i++) {
    const h = history[i];
    if (h === firstText) continue;
    if (list.some((x) => x.kind === 'text' && x.text === h)) continue;
    list.push({ kind: 'text', text: h, source: '剪贴板历史' });
  }

  /* 每一项配一把稳定的钥匙，弹窗改选时用它精确指认"我要发这一条"。
     文字用内容本身当钥匙（和旧版一致）；图片用「体积 + 宽高」——1.0.1
     是按位置算的（'i:0'），但 v1.0.2 起图片可能排在选中文字后面，
     位置不再稳定，照位置算会在"预览"和"发送"两次解析之间错位。 */
  list.forEach((x) => {
    if (x.kind === 'image') {
      const im = x.image || {};
      x.key = 'i:' + (im.bytes || 0) + '_' + (im.width || 0) + 'x' + (im.height || 0);
    } else {
      x.key = 't:' + x.text;
    }
  });

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

/* pickKey：弹窗里当前选中那一项的钥匙（在 resolveContent 结尾配好）。
   传了它就以它为准 —— 精确匹配候选列表；匹配不到但看得出是文字时，
   就直接发这份原文。这样即使列表在两次请求之间发生了变化，"你看到的
   那条"也一定是"发出去的那条"，不会悄悄换成别的。
   按快捷键时没有界面，不传，发 list[0]（= 最新那条）。 */
async function runSend(providedClip, pickKey) {
  const r = await resolveContent(providedClip);
  if (!r.ok) {
    return await fail(r.reason, r.detail);
  }

  const want = String(pickKey == null ? '' : pickKey).trim();
  let chosen = r.list[0];

  if (want) {
    const hit = r.list.find((x) => x.key === want);
    if (hit) {
      chosen = hit;
    } else if (want.indexOf('t:') === 0) {
      /* 列表变了，但"你看到的那条文字"仍然发得出去 —— 照原文发。 */
      chosen = { kind: 'text', text: want.slice(2), source: '候选列表' };
    }
    /* 钥匙指向图片、而列表里已经没有它了：退回 list[0]，
       发送结果里会照实写清楚实际发出去的是哪一条。 */
  }

  if (chosen.kind === 'image') {
    return await sendImageRun(chosen.image, chosen.source);
  }
  return await sendTextRun(chosen.text, chosen.source);
}

/* 发一段文字。 */
async function sendTextRun(content, source) {
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
      '最常见的原因：桥程序没在运行。回到弹窗点「启动本地桥」，' +
      '或双击 bridge\\start_bridge.bat 启动它。'
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
    kind: 'text',
    at: Date.now(),
    source: source,
    content: content,
    msg_id: payload.msg_id || '',
    has_url: !!payload.has_url
  };
  return lastResult;
}

/* ---------------------------------------------- 发图片（1.0.1）

   两条来源（剪贴板里的图 / 右键网页图片）最后都汇到这里：把 dataURL
   交给本地的桥，由它去上传和发送。扩展这一侧刻意不做任何图像处理 ——
   缩尺寸、格式兜底都在桥那边用 Pillow 做，免得两边行为不一致。 */

async function sendImageRun(image, source) {
  if (!image || !image.dataUrl) {
    return await fail('没拿到图片数据。', '请重新复制一次图片再试。');
  }

  const meta = (image.width && image.height)
    ? (image.width + '×' + image.height + ' · ' + qqImg.humanSize(image.bytes))
    : qqImg.humanSize(image.bytes);

  let payload = null;
  try {
    const res = await fetch(BRIDGE_SEND, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image: { data_url: image.dataUrl, name: image.name || '', mime: image.mime || '' }
      })
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
      '最常见的原因：桥程序没在运行。回到弹窗点「启动本地桥」，' +
      '或双击 bridge\\start_bridge.bat 启动它。'
    );
  }

  if (!payload || payload.ok !== true) {
    const reason = (payload && payload.message) ? payload.message : '桥程序报告图片发送失败。';
    const detail = (payload && payload.error) ? 'code=' + payload.error : '';
    return await fail(reason, detail);
  }

  await setBadge('OK', '#1D9E75', 2500);
  lastResult = {
    ok: true,
    kind: 'image',
    at: Date.now(),
    source: source,
    content: '[图片] ' + meta,
    msg_id: payload.msg_id || '',
    has_url: false
  };

  /* 桥替我们改动了图片时必须说一声（超过 20MB 被缩了尺寸、或原格式被
     拒改成了 PNG）。悄悄改掉画质、把动图变成静图，都不行。 */
  if (payload.warning) {
    await notify(
      '图片已发出（和原图有出入）',
      payload.warning_message || '图片发出去了，但和原图不完全一样。'
    );
  }
  return lastResult;
}

/* ---------------------------------------------- 右键网页图片（1.0.1）

   ⚠️ 取字节这一步有个绕不过去的边界：Chrome 规定扩展后台发起的跨站
   请求**不会带上目标站的 cookie**。所以"需要登录才看得到"的图片
   （网盘、后台系统、私有仓库里的图）在这里必然下载失败 —— 这不是能
   修的 bug，是浏览器的安全边界。碰到就明确报错、让人改用「复制图片」，
   绝不静默换成别的内容发出去。

   blob: 开头的地址同样拿不到 —— 那个地址只在页面自己的上下文里有效。 */

const MENU_IMAGE_ID = 'qq-send-image';
const IMAGE_FETCH_TIMEOUT_MS = 20000;

/* 幂等注册。菜单已存在时 create 会报一个"id 重复"的错，吞掉即可。 */
function ensureImageMenu() {
  try {
    chrome.contextMenus.create({
      id: MENU_IMAGE_ID,
      title: '发送此图片到 QQ',
      contexts: ['image']
    }, () => { void chrome.runtime.lastError; });
  } catch (e) { /* 忽略 */ }
}

/* 重载扩展时先清后建 —— 这样改了菜单文案也能立刻生效。 */
function rebuildImageMenu() {
  try {
    chrome.contextMenus.removeAll(() => {
      void chrome.runtime.lastError;
      ensureImageMenu();
    });
  } catch (e) { /* 忽略 */ }
}

async function fetchImageBlob(url) {
  const ctl = new AbortController();
  const timer = setTimeout(() => {
    try { ctl.abort(); } catch (e) { /* 忽略 */ }
  }, IMAGE_FETCH_TIMEOUT_MS);

  try {
    const res = await fetch(url, {
      credentials: 'omit',
      cache: 'no-store',
      signal: ctl.signal
    });
    if (!res.ok) return { ok: false, why: 'http', status: res.status };
    const blob = await res.blob();
    if (!blob || !blob.size) return { ok: false, why: 'empty' };
    return { ok: true, blob: blob };
  } catch (e) {
    const m = String((e && e.message) ? e.message : e);
    return { ok: false, why: /abort/i.test(m) ? 'timeout' : 'network', error: m };
  } finally {
    clearTimeout(timer);
  }
}

function nameFromUrl(url, mime) {
  let base = '';
  try {
    const u = new URL(url);
    base = String(u.pathname || '').split('/').pop() || '';
    try { base = decodeURIComponent(base); } catch (e) { /* 保留原样 */ }
  } catch (e) { /* 忽略 */ }
  base = base.replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 60);
  if (!base) base = 'image';
  if (base.indexOf('.') === -1) base += '.' + qqImg.extFor(mime);
  return base;
}

async function sendRightClickImage(url) {
  await setBadge('…', '#5F5E5A', 0);

  const f = await fetchImageBlob(url);
  if (!f.ok) {
    const why = (f.why === 'timeout')
      ? ('下载超时（' + Math.round(IMAGE_FETCH_TIMEOUT_MS / 1000) + ' 秒没下完）。')
      : (f.why === 'http'
        ? ('服务器返回 HTTP ' + f.status + '。')
        : (f.why === 'empty' ? '下载下来是空的。' : '下载失败。'));
    return await fail(
      '这张图片取不到。',
      why + '\n需要登录才能看到的图片，Chrome 不允许扩展后台带着你的' +
      '登录状态去下载。请改成在图上右键「复制图片」，再用弹窗发送。'
    );
  }

  if (!qqImg.isImageMime(f.blob.type)) {
    return await fail(
      '这个地址拿到的不是图片。',
      '服务器返回的类型是 ' + (f.blob.type || '未知') +
      '。它可能其实是个下载链接或网页，不是能直接发的图。'
    );
  }

  try {
    const image = await qqImg.prepareImage(f.blob, nameFromUrl(url, f.blob.type));
    return await sendImageRun(image, '网页图片');
  } catch (e) {
    return await fail('处理这张图片时出错了。', String((e && e.message) ? e.message : e));
  }
}

chrome.contextMenus.onClicked.addListener((info) => {
  if (!info || info.menuItemId !== MENU_IMAGE_ID) return;
  const url = info.srcUrl || '';
  if (!url) return;
  sendRightClickImage(url);
});

/* service worker 冷启动时的兜底。放在文件最末尾是因为上面那个
   MENU_IMAGE_ID 是 const —— 提前调用会撞上"暂时性死区"直接抛错。
   菜单已存在时这个 create 只会被忽略（id 重复的报错被吞掉），
   既不会重复添加，也不会覆盖。 */
ensureImageMenu();

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
      stats: {},
      storeWarn: storeNotice()
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
    stats: (await readStore()).stats,
    /* 历史被挡下/降级时的一句话。正常情况下是空串，弹窗不显示。 */
    storeWarn: storeNotice()
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

/* ---------------------------------------------- 启动本地桥（v1.0.4）

   浏览器**不允许**扩展直接运行本地程序 —— 没有这种 API。Chrome 官方
   给的唯一通道叫 Native Messaging：扩展只能跟一个"宿主程序"说话，由
   宿主去启动别的东西。所以链路是：

     点「启动本地桥」
       → chrome.runtime.sendNativeMessage('com.mumu.qq_bridge', {cmd:'start'})
         → Chrome 按注册表找到 bridge\qq_host.bat 并把它拉起来
           → 它执行 qq_native_host.py
             → 那个脚本先看 18761 端口在不在，不在才拉起 start_bridge.bat
               （最小化到任务栏）

   「宿主在哪」这份登记信息由**桥自己**在启动时写好（它一启动就知道自己
   的绝对路径），所以正常情况下这里一次都不会失败。真失败了也不许静默 ——
   下面把浏览器的英文原话翻成人话，并说清下一步该双击哪个文件。 */

const NATIVE_HOST = 'com.mumu.qq_bridge';

/* 浏览器的报错只有那么几句固定的英文，逐句对上号。
   兜底一律**带上原文** —— 万一将来冒出没见过的一句，原样带出来才好排查，
   绝不能自己编一句"启动失败"把线索吃掉（这是本项目最贵的反模式）。 */
function explainNativeError(e) {
  const raw = String((e && e.message) ? e.message : e).trim();

  if (/is not registered/i.test(raw)) {
    return {
      reason: '本地桥的启动入口还没登记。',
      detail: '双击 bridge\\重新登记.bat 登记一次即可。\n' +
        '（桥每次启动都会自己登记，所以看到这句通常说明 bridge 文件夹' +
        '被搬过位置，或登记项被清理软件删掉了。）'
    };
  }
  if (/forbidden/i.test(raw)) {
    return {
      reason: '浏览器不允许这个扩展使用该启动入口。',
      detail: '宿主清单里的 allowed_origins 与当前扩展 ID 对不上，\n' +
        '多半是扩展被重新安装过（换了 ID）。告诉 WorkBuddy 重新生成宿主清单。'
    };
  }
  if (/not found|no such native/i.test(raw)) {
    return {
      reason: '找不到宿主程序文件。',
      detail: '登记信息指向的 bridge\\qq_host.bat 不在了。\n' +
        '把 bridge 文件夹放回原位置，或双击 bridge\\重新登记.bat。'
    };
  }
  return { reason: '没能启动本地桥。', detail: raw };
}

async function launchBridge() {
  try {
    const r = await chrome.runtime.sendNativeMessage(NATIVE_HOST, { cmd: 'start' });
    return { ok: true, data: r || {} };
  } catch (e) {
    const x = explainNativeError(e);
    return { ok: false, reason: x.reason, detail: x.detail };
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
        /* 顺序有意义：imageutil.js 先落地（弹窗注入的那段读图片的代码
           要用它），content.js 后跑。 */
        files: ['imageutil.js', 'content.js']
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
  rebuildImageMenu();
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
       msg.pickKey 是弹窗里当前选中那一项的钥匙（'t:文字' 或 'i:0'）——
       改成钥匙而不是原文，是因为 1.0.1 起候选项可能是图片，图片没有
       "原文"可比对。 */
    runSend(msg.clipboard, msg.pickKey)
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, reason: String(e) }));
    return true;
  }
  if (msg.type === 'get-preview') {
    getPreview(msg.clipboard).then(sendResponse).catch((e) => {
      sendResponse({ ok: false, reason: String(e), detail: '', restricted: false, list: [], note: '', max: HISTORY_MAX, stats: {}, storeWarn: storeNotice() });
    });
    return true;
  }
  if (msg.type === 'get-health') {
    getHealth().then(sendResponse).catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
  if (msg.type === 'launch-bridge') {
    /* 弹窗上那个「启动本地桥」按钮。宿主自己会判断"桥已经在跑了吗"，
       所以这个操作是幂等的 —— 连点几下也不会起出第二个桥。 */
    launchBridge()
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, reason: String(e), detail: '' }));
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
