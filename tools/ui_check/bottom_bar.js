// ui_drive bottom-bar scenario for Simple Firearm Logbook. The fixture opens
// the window at its minimum size. With an update notice showing, the notice
// must sit on the window's true center, the bar must stay one row with
// nothing cut off, and the left side must hold only the debug log switch.

export default async function bottomBar(helpers) {
  const { evaluate, waitFor, check, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture saved the minimum window size", fixture.saved === true, JSON.stringify(fixture));

  // The startup update check can rewrite the notice as it finishes, so let
  // that settle, then set the notice and measure in one step.
  await new Promise((r) => setTimeout(r, 1500));

  const m = JSON.parse(await evaluate(
    "(() => { $('updateNotice').style.opacity = '1';" +
    " $('updateNotice').innerHTML = buildUpdateNotice('10.10.10');" +
    " const bar = document.querySelector('.bottom-bar').getBoundingClientRect();" +
    " const n = $('updateNotice').getBoundingClientRect();" +
    " const parts = ['.bar-left', '.bar-center', '.bar-right'].map(s => document.querySelector('.bottom-bar ' + s).getBoundingClientRect());" +
    " const btn = $('updateBtn').getBoundingClientRect();" +
    " const left = [...document.querySelector('.bottom-bar .bar-left').children].map(c => c.className || c.id);" +
    " return JSON.stringify({ winW: document.documentElement.clientWidth, outerW: window.outerWidth," +
    " barH: bar.height, barTop: bar.top, barBottom: bar.bottom, barRight: bar.right," +
    " noticeCenter: (n.left + n.right) / 2, noticeW: n.width, left: left," +
    " partsTop: parts.map(p => p.top), partsBottom: parts.map(p => p.bottom)," +
    " btnRight: btn.right, btnH: btn.height }); })()"
  ));
  const detail = JSON.stringify(m);

  check(`the window opened at the minimum width: ${m.outerW}`, m.outerW <= 1010, detail);
  check("the update notice is showing", m.noticeW > 0, detail);
  check(`the notice is centered in the window (off by ${(m.noticeCenter - m.winW / 2).toFixed(1)}px)`,
    Math.abs(m.noticeCenter - m.winW / 2) <= 1, detail);
  check("the bar keeps its 44px height", Math.abs(m.barH - 44) < 1, detail);
  check("all three parts sit inside the bar on one row",
    m.partsTop.every((t) => t >= m.barTop) && m.partsBottom.every((b) => b <= m.barBottom), detail);
  check("the update button is not cut off", m.btnRight <= m.barRight && m.btnH < 40, detail);
  check("the left side holds only the debug log switch",
    m.left.length === 1 && m.left[0] === "dbg-toggle", detail);
}
