/* ============================================================
   图片工具（v1.0.1 新增）—— service worker / 弹窗 / 网页 三处共用

   ⚠️ 它是"一份代码被三个环境加载"，下面的写法全是被环境逼出来的：

   ① service worker 里**没有** FileReader、没有 DOM 的 canvas / Image。
      所以「binary → base64」只能自己分块 btoa，拿宽高只能用
      createImageBitmap。这几个（Blob.arrayBuffer / btoa /
      createImageBitmap）恰好是三个环境唯一都有的交集。

   ② content script 会在扩展重载时被反复注入（见 background.js 的
      injectIntoAllTabs）。顶层写 const / let 的话，第二次注入会抛
      "Identifier 'x' has already been declared"，整个脚本当场停摆。
      所以全部包进 IIFE，只往 globalThis 挂一个 qqImg。

   ③ 这个文件同时出现在三处，任何一处不能用的语法都会拖垮另外两处，
      所以刻意保持保守写法（不写 import / export / 可选链）。

   它只做三件事：认格式、blob 转 dataURL、读出宽高。
   「超 20MB 缩尺寸」和「格式被拒就转 PNG 重试」都放在桥那边用 Pillow
   做 —— 那边一行的事，扩展这边不重复实现，也就不会两边行为不一致。
   ============================================================ */

(function (g) {
  'use strict';

  /* QQ 富媒体接口认的图片格式。刻意用白名单：剪贴板里也可能是 PDF、
     Excel、甚至可执行文件，那些绝不能被当成图片发出去。 */
  var MIME_RE = /^image\/(png|jpeg|jpg|gif|webp|bmp)$/i;

  var EXT = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/jpg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'image/bmp': 'bmp'
  };

  function isImageMime(mime) {
    return MIME_RE.test(String(mime || ''));
  }

  function extFor(mime) {
    return EXT[String(mime || '').toLowerCase()] || 'png';
  }

  /* ArrayBuffer → base64。
     分块是必需的：把几百万个字节一次性塞进 String.fromCharCode.apply
     会直接抛 RangeError（实参个数超上限）。0x8000 = 32768 是各浏览器
     都稳的块大小。 */
  function bufToBase64(buf) {
    var bytes = new Uint8Array(buf);
    var CHUNK = 0x8000;
    var parts = [];
    for (var i = 0; i < bytes.length; i += CHUNK) {
      parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK)));
    }
    return btoa(parts.join(''));
  }

  /* base64 长度 → 原始字节数。不真解码，够填"245 KB"那一格就行。 */
  function base64Bytes(b64) {
    var s = String(b64 || '');
    if (!s) return 0;
    var pad = 0;
    if (s.charAt(s.length - 1) === '=') pad++;
    if (s.charAt(s.length - 2) === '=') pad++;
    return Math.floor(s.length * 3 / 4) - pad;
  }

  /* 拆 dataURL。返回 null 表示这不是一个合法的 dataURL。 */
  function parseDataUrl(u) {
    var m = /^data:([^;,]*)(;base64)?,([\s\S]*)$/.exec(String(u || ''));
    if (!m) return null;
    var b64 = m[3] || '';
    return {
      mime: String(m[1] || '').toLowerCase(),
      base64: b64,
      bytes: base64Bytes(b64)
    };
  }

  function humanSize(n) {
    var b = Number(n) || 0;
    if (b < 1024) return b + ' B';
    if (b < 1024 * 1024) return Math.round(b / 1024) + ' KB';
    return (Math.round(b / 1024 / 102.4) / 10) + ' MB';
  }

  /* 把一张图打包成"候选列表里的一项"。
     宽高读不出来（图损坏、格式太怪）不算失败 —— 那格留空即可，
     不能因为读不到尺寸就把整张图丢掉，那样连发都发不出去了。 */
  function prepareImage(blob, name) {
    return (async function () {
      var buf = await blob.arrayBuffer();
      var mime = String(blob.type || '').toLowerCase();
      /* 有些来源不给 type。随便填一个会让后面的判断走错，但桥那边用
         Pillow 重新探测真实格式，所以这里给个占位值是安全的。 */
      if (!isImageMime(mime)) mime = 'image/png';

      var width = 0;
      var height = 0;
      try {
        if (typeof createImageBitmap === 'function') {
          var bmp = await createImageBitmap(new Blob([buf], { type: mime }));
          width = bmp.width || 0;
          height = bmp.height || 0;
          if (bmp.close) bmp.close();
        }
      } catch (e) { /* 解不开就留空 */ }

      return {
        kind: 'image',
        dataUrl: 'data:' + mime + ';base64,' + bufToBase64(buf),
        mime: mime,
        width: width,
        height: height,
        bytes: buf.byteLength,
        name: String(name || ('image.' + extFor(mime)))
      };
    })();
  }

  /* 从 navigator.clipboard.read() 的返回值里挑出第一张图片。
     不是图片、或取不到，一律返回 null（调用方据此走"剪贴板里没图"的
     正常分支，不报错）。 */
  function pickImageFromClipItems(items) {
    return (async function () {
      var list = items || [];
      for (var i = 0; i < list.length; i++) {
        var it = list[i];
        var types = (it && it.types) ? it.types : [];
        for (var j = 0; j < types.length; j++) {
          if (!isImageMime(types[j])) continue;
          try {
            var blob = await it.getType(types[j]);
            if (!blob) continue;
            return await prepareImage(blob, 'clipboard.' + extFor(types[j]));
          } catch (e) { /* 这一种取不到就试下一种 */ }
        }
      }
      return null;
    })();
  }

  g.qqImg = {
    isImageMime: isImageMime,
    extFor: extFor,
    bufToBase64: bufToBase64,
    base64Bytes: base64Bytes,
    parseDataUrl: parseDataUrl,
    humanSize: humanSize,
    prepareImage: prepareImage,
    pickImageFromClipItems: pickImageFromClipItems
  };
})(typeof self !== 'undefined' ? self : this);
