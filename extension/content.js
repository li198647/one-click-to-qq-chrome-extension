/* ============================================================
   网页内的复制监听 + 剪贴板快照（content script）

   为什么需要它：
   Chrome 不提供任何「剪贴板变了」的通知接口 —— 扩展只能在"能看见
   剪贴板"的瞬间读到它。想自动攒出一份历史，就只能在网页里守着
   每一个这样的瞬间：

     ① copy / cut 事件（最准，事件自带内容）
     ② 切回页面 / 标签页时的剪贴板快照（兜住"在别处复制完回来"）
     ③ 页面有焦点时的低频巡检（兜住网页自带的"复制"按钮）
     ④ iframe 里也注入一份（编辑器类页面的正文常常在 iframe 里）

   ⚠️ 0.1.4 修的两个坑（都是"连着复制 4 条、只留下最后 1 条"的成因）：

   【坑一】0.1.3 用"设个标志位就 return"来防止重复注入，是错的。
   扩展一重载，旧那份脚本的监听器**还挂在页面上**，但它的
   chrome.runtime 上下文已经失效 —— 它照旧能听到 copy 事件，却再也
   发不出消息，于是复制被静默吞掉；而新注入的脚本看到旧标志位就直接
   return，结果整页的监听**一直是死的**，非得手动刷新页面才好。
   → 正确做法：新实例先把旧实例装的东西全部拆掉（teardown），再重新装。

   【坑二】个别页面会在 copy 事件里清空 clipboardData，这时一个字的
   文字都取不到。0.1.3 是直接放弃（静默丢一条），现在改成稍等片刻
   直接去读一次真剪贴板。注意**必须延后**：copy 事件触发时剪贴板还
   没被写入，当场读只会读到上一份内容。

   这个脚本只做三件事：听事件、读剪贴板、把文字交给后台去重入列。
   它不读界面、不改页面、不联网。
   ============================================================ */

(function () {
  /* ---------------------------------------------- 重复注入：先拆旧的

     旧实例如果留下了 teardown 函数，就先把它的监听器和定时器全清掉。
     隔着一层 typeof 判断是因为 0.1.3 的老实例没有这个函数 —— 那种
     情况下我们只是多装一份监听器（老的那份发不出消息，等于哑的），
     刷新一次页面就彻底干净了。 */
  try {
    if (typeof window.__qqSendSnifferTeardown === 'function') {
      window.__qqSendSnifferTeardown();
    }
  } catch (e) { /* 忽略 */ }

  var disposers = [];

  function on(target, type, handler, opts) {
    target.addEventListener(type, handler, opts);
    disposers.push(function () { target.removeEventListener(type, handler, opts); });
  }

  function every(ms, fn) {
    var id = setInterval(fn, ms);
    disposers.push(function () { clearInterval(id); });
  }

  function later(fn, ms) {
    var id = setTimeout(fn, ms);
    disposers.push(function () { clearTimeout(id); });
  }

  window.__qqSendSnifferTeardown = function () {
    for (var i = 0; i < disposers.length; i++) {
      try { disposers[i](); } catch (e) { /* 忽略 */ }
    }
    disposers = [];
  };

  /* ---------------------------------------------- 配置 */

  /* 页面有焦点时巡检剪贴板的间隔（毫秒）。设成 0 可关掉这一路，
     只保留 copy/cut 事件和「切回页面」快照。 */
  var POLL_MS = 2000;

  /* 两次快照之间的最小间隔，避免点来点去时反复读剪贴板 */
  var MIN_GAP_MS = 1200;

  /* 连读两次、隔这么久再比对。中间被覆盖了就说明有更新的内容，
     交给覆盖它的那条路径去上报。 */
  var SETTLE_MS = 250;

  /* ---------------------------------------------- 上报 */

  /* 上一份送出去的内容。巡检会反复看到同一份剪贴板，靠它挡掉重复
     上报（后台也会去重，但没必要一直发消息）。 */
  var lastSent = '';

  function report(text, how) {
    var t = String(text == null ? '' : text).trim();
    if (!t) return;
    if (t === lastSent) return;
    lastSent = t;
    try {
      chrome.runtime.sendMessage(
        { type: 'clip-captured', text: t, how: how },
        /* 传个空回调来消费掉 lastError，否则扩展刚重载过时控制台会刷红字。 */
        function () { void chrome.runtime.lastError; }
      );
    } catch (e) {
      /* 扩展被重载 / 卸载的瞬间可能抛，忽略即可 */
    }
  }

  /* ---------------------------------------------- ① copy / cut 事件 */

  /* 优先用事件自带的 clipboardData —— 有些页面会在这里做改写；
     拿不到就退回当前选中的文字。 */
  function grabText(e) {
    var t = '';
    try {
      if (e && e.clipboardData) t = e.clipboardData.getData('text/plain') || '';
    } catch (err) { /* 忽略 */ }
    if (!t) {
      try {
        var sel = window.getSelection ? window.getSelection() : null;
        t = sel ? sel.toString() : '';
      } catch (err) { /* 忽略 */ }
    }
    return t;
  }

  function handleCopyCut(how) {
    return function (e) {
      var t = grabText(e);
      if (t) {
        report(t, how);
        return;
      }
      /* 一个字都没取到 —— 别静默丢掉，稍后直接读一次真剪贴板。
         必须延后：此刻剪贴板里还是上一份内容。 */
      later(function () { snapshot(how); }, 300);
    };
  }

  on(document, 'copy', handleCopyCut('网页复制'), true);
  on(document, 'cut', handleCopyCut('网页剪切'), true);

  /* ---------------------------------------------- ②③ 剪贴板快照

     只在「这个文档真的持有焦点」时才读：Chrome 规定剪贴板只能被
     有焦点的 document 读取（0.1.1 就是在这里踩的坑，见 README）。
     没焦点硬读必然报 NotAllowedError，所以先判断再读。

     读两次一致才上报 —— 否则可能把"刚刚被下一条覆盖掉的旧内容"
     当成最新的一条塞进历史，顺序就乱了。 */

  var busy = false;
  var lastAttempt = 0;

  function focusedHere() {
    try { return !!document.hasFocus(); } catch (e) { return false; }
  }

  function visibleHere() {
    try { return document.visibilityState !== 'hidden'; } catch (e) { return true; }
  }

  function canRead() {
    try {
      return !!(navigator.clipboard && navigator.clipboard.readText);
    } catch (e) { return false; }
  }

  function snapshot(how) {
    if (busy || !canRead() || !focusedHere()) return;

    var now = Date.now();
    if (now - lastAttempt < MIN_GAP_MS) return;
    lastAttempt = now;
    busy = true;

    (async function () {
      try {
        var a = String((await navigator.clipboard.readText()) || '').trim();
        if (!a) return;

        await new Promise(function (r) { setTimeout(r, SETTLE_MS); });

        if (!focusedHere()) return;
        var b = '';
        try {
          b = String((await navigator.clipboard.readText()) || '').trim();
        } catch (e) { return; }
        if (a !== b) return;

        report(a, how);
      } catch (e) {
        /* 读不到就静默等下一次时机，不打扰你 */
      } finally {
        busy = false;
      }
    })();
  }

  /* ② 切回页面 / 切回标签页 —— 你刚从别的程序复制完回来，就靠这一下 */
  on(window, 'focus', function () { snapshot('切回页面'); }, true);

  on(document, 'visibilitychange', function () {
    if (visibleHere()) snapshot('切回标签页');
  }, true);

  /* ③ 低频巡检 —— 兜住网页自带的「复制」按钮（writeText 不触发事件）。
     只在这个框架可见且有焦点时读，不可见就直接跳过。 */
  if (POLL_MS > 0) {
    every(POLL_MS, function () {
      if (!visibleHere() || !focusedHere()) return;
      snapshot('页面剪贴板');
    });
  }
})();
