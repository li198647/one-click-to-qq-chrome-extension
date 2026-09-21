# -*- coding: utf-8 -*-
"""量尺：弹窗状态行塞得下多少字。（改弹窗版式前后各跑一次）

为什么需要它：弹窗只有 320px 宽，状态行的内容宽是 272px。状态文案 + 那个
「启动本地桥」按钮加起来一旦超了，这一行就会悄悄掉成两行 —— 肉眼看得见，
但"还差几像素"看不出来。这里用**真实排版引擎**（Playwright 的 Chromium）
打开打桩预览页（真实 popup.html + popup.js）量，不靠估。

⚠️ 一个很容易量错的地方：`#statusText` 是 `flex: 1`，它**会自己撑满**
剩余空间。所以直接量它的 getBoundingClientRect().width 得到的是"分到多少"，
不是"文字本身要多宽" —— 无论文案多长它都显示"刚好占满"，什么问题都发现
不了。正确做法是另起一个 `white-space: nowrap` 的隐身元素，把同样的字体
样式套上去，量它 —— 就是下面 MEASURE 里干的事。

v1.0.4 实测结论（改版式后请对照这个数）：
    一切正常，可以发送            —      131/272
    连不上本地桥                  有按钮  176/272   余 96px
    桥在跑，但机器人没连上           —      157/272
    机器人在线，但还没拿到 openid    —      231/272   余 41px（最紧的"无按钮"那条）
    正在启动本地桥… + 启动中…30s    有按钮  205/272   余 67px
    正在连接机器人… + 启动中…30s    有按钮  205/272   余 67px
    → 全部单行。带按钮的行高度 36px（不带按钮 34px，只多 2px）。

跑法：
    python tools/measure-popup-row.py
前置：先跑 node tools/make-preview.js 生成预览页。
"""

import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "preview-popup-nobridge.html")
CHROME = r"C:\Users\Administrator\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe"

# 所有状态文案 —— 全部按 popup.js 里的原话抄，一个标点都不改
TEXTS = [
    ("一切正常，可以发送", False, "启动本地桥"),
    ("连不上本地桥", True, "启动本地桥"),
    ("桥在跑，但机器人没连上", False, "启动本地桥"),
    ("机器人在线，但还没拿到你的 openid", False, "启动本地桥"),
    # 启动过程中的两条。秒数是两位数（最大 30）时最宽，这里就按最宽算
    ("正在启动本地桥…", True, "启动中…30s"),
    ("正在连接机器人…", True, "启动中…30s"),
]

MEASURE = """
(args) => {
  const el = document.getElementById('statusText');
  const btn = document.getElementById('bridgeBtn');
  el.textContent = args.text;
  btn.style.display = args.showBtn ? '' : 'none';
  btn.disabled = false;
  btn.textContent = args.btnText || '启动本地桥';
  const row = document.querySelector('.row');
  const cs = getComputedStyle(row);
  const innerW = row.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);

  // ⚠️ #statusText 是 flex:1，会自己撑满剩余空间 —— 直接量它的宽度
  //    得到的是"分到多少"，不是"文字本身要多宽"，什么问题都发现不了。
  //    所以这里另起一个 nowrap 的影分身，量文字的自然宽度。
  const ghost = document.createElement('span');
  const scs = getComputedStyle(el);
  ghost.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;' +
    'font-family:' + scs.fontFamily + ';font-size:' + scs.fontSize +
    ';font-weight:' + scs.fontWeight + ';letter-spacing:' + scs.letterSpacing;
  ghost.textContent = args.text;
  document.body.appendChild(ghost);
  const natural = ghost.getBoundingClientRect().width;
  ghost.remove();

  const dotW = document.getElementById('dot').getBoundingClientRect().width;
  const btnW = args.showBtn ? btn.getBoundingClientRect().width : 0;
  const gap = parseFloat(cs.columnGap) || 0;
  const need = dotW + natural + btnW + gap * (args.showBtn ? 2 : 1);

  return {
    innerW: Math.round(innerW),
    natural: Math.round(natural),
    dotW: Math.round(dotW),
    btnW: Math.round(btnW),
    btnH: Math.round(btn.getBoundingClientRect().height),
    need: Math.round(need),
    slack: Math.round(innerW - need),
    rowH: Math.round(row.getBoundingClientRect().height),
    txtH: Math.round(el.getBoundingClientRect().height),
    wrapped: el.getBoundingClientRect().height > 22 || el.scrollWidth > el.clientWidth + 1,
  };
}
"""


def main():
    if not os.path.isfile(CHROME):
        print("找不到 Chromium:", CHROME)
        return 2
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 420, "height": 900})
        pg.goto("file:///" + PAGE.replace("\\", "/"))
        pg.wait_for_timeout(900)

        print("%-30s %5s %6s %5s %6s %6s %6s %5s" % (
            "状态文案", "按钮", "文字宽", "按钮宽", "需要", "可用", "余量", "行高"))
        bad = []
        for text, show, btnText in TEXTS:
            r = pg.evaluate(MEASURE, {"text": text, "showBtn": show, "btnText": btnText})
            print("%-30s %5s %6d %5d %6d %6d %6d %5d  %s" % (
                text[:28], ("有" if show else "—"), r["natural"], r["btnW"],
                r["need"], r["innerW"], r["slack"], r["rowH"],
                ("换行了!" if r["wrapped"] else "") + (" 挤不下!" if r["slack"] < 0 else "")))
            if r["wrapped"] or r["slack"] < 0:
                bad.append(text)

        # 布局总览
        r = pg.evaluate(MEASURE, {"text": "连不上本地桥", "showBtn": True,
                                  "btnText": "启动本地桥"})
        print("\n状态行内容宽 %d / 圆点 %d / 按钮 %d×%d / 余量 %d px" % (
            r["innerW"], r["dotW"], r["btnW"], r["btnH"], r["slack"]))

        pg.screenshot(path=os.path.join(HERE, "_rowshot_nobridge.png"))
        b.close()

    print("\n结论：" + ("全部单行显示，没有溢出" if not bad else ("有问题 -> " + " / ".join(bad))))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
