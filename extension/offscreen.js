/* offscreen 文档：service worker 里没有 DOM，读剪贴板要靠它。 */

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.target !== 'offscreen') return;

  if (msg.type === 'read-clipboard') {
    navigator.clipboard.readText()
      .then((text) => sendResponse({ ok: true, text: text || '' }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
});
