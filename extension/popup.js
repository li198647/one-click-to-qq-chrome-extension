/* 弹窗：显示桥的状态，并提供"我不知道快捷键"时的兜底入口。 */

const dot = document.getElementById('dot');
const statusText = document.getElementById('statusText');
const detail = document.getElementById('detail');
const sendBtn = document.getElementById('sendBtn');

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

async function refreshLast() {
  const r = await chrome.runtime.sendMessage({ type: 'get-last' });
  if (r && r.ok && r.last) {
    const L = r.last;
    if (L.ok) {
      showDetail(
        '上次发送成功（' + fmtTime(L.at) + '，来源：' + L.source + '）\n' + L.content.slice(0, 120),
        false
      );
    } else {
      showDetail('上次发送失败（' + fmtTime(L.at) + '）：' + L.reason, true);
    }
  }
}

sendBtn.addEventListener('click', async () => {
  sendBtn.disabled = true;
  sendBtn.textContent = '发送中…';
  try {
    const r = await chrome.runtime.sendMessage({ type: 'do-send' });
    if (r && r.ok) {
      showDetail('已发送（来源：' + r.source + '）\n' + String(r.content).slice(0, 120), false);
    } else {
      showDetail('没发出去：' + ((r && r.reason) || '未知原因'), true);
    }
  } catch (e) {
    showDetail('没发出去：' + String(e), true);
  }
  sendBtn.disabled = false;
  sendBtn.textContent = '立即发送';
  await refreshHealth();
});

(async function init() {
  await refreshHealth();
  await refreshLast();
})();
