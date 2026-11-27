// ui_drive scenario: keyboard-focus ring. Tabs through the main window to
// confirm the toggles are reachable and the log view is skipped, then, in
// both themes, moves keyboard focus onto a button, toggles (on and off),
// a dialog checkbox (checked), and a dialog radio button, and checks each
// shows a 2px solid ring in the theme's accent color. For a toggle the ring
// must be on the visible switch, since its real checkbox is hidden. Uses the
// focus-ring fixture, whose 40 firearms make the log view scroll (a
// scrolling area is a Tab stop unless the page opts it out).

export default async function focusRing(helpers) {
  const { press, evaluate, waitFor, check, screenshot } = helpers;
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));

  await waitFor("typeof api === 'function' && api() && typeof api().get_config === 'function'", 10000);
  await waitFor("document.querySelector('.toolbar .btn') !== null", 10000);

  // Tab order: a toggle is reachable, the log view is not.
  const describe = "(() => { const a = document.activeElement; return a.id ? '#' + a.id : a.className ? '.' + String(a.className).split(' ')[0] : a.tagName; })()";
  const order = [];
  for (let i = 0; i < 25; i++) {
    await press("Tab");
    order.push(await evaluate(describe));
  }
  check("Tab skips the log view", !order.includes(".table-wrap"), order.join(" "));
  check("Tab reaches the filter toggles", order.includes("#allFilterToggle"), order.join(" "));

  // Focus moved by script after a Tab press keeps the keyboard-focus look.
  const ring = async (focusSel, ringSel) => {
    await evaluate(`document.querySelector(${JSON.stringify(focusSel)}).focus(); true`);
    await pause(250); // buttons fade the ring in
    return evaluate(`(() => {
      const f = document.querySelector(${JSON.stringify(focusSel)});
      const el = document.querySelector(${JSON.stringify(ringSel)});
      const cs = getComputedStyle(el);
      const probe = document.createElement('span');
      probe.style.color = 'var(--accent)';
      document.body.appendChild(probe);
      const accent = getComputedStyle(probe).color;
      probe.remove();
      return { focused: document.activeElement === f, visible: f.matches(':focus-visible'),
               style: cs.outlineStyle, width: cs.outlineWidth, color: cs.outlineColor, accent };
    })()`);
  };
  // Display scaling rounds the ring to whole screen pixels, so 2px can
  // measure 1.6px (125% scaling); anything from 1.5px counts.
  const ok =(r) => r.focused && r.visible && r.style === "solid" && parseFloat(r.width) >= 1.5 && r.color === r.accent;

  for (const theme of ["dark", "light"]) {
    const isLight = await evaluate("document.body.classList.contains('light')");
    if ((theme === "light") !== isLight) {
      await evaluate("toggleTheme(); true");
      await waitFor(`document.body.classList.contains('light') === ${theme === "light"}`, 5000);
    }
    await press("Tab");

    for (const [name, focusSel, ringSel] of [
      ["button", ".toolbar .btn", ".toolbar .btn"],
      ["toggle (on)", "#allFilterToggle", "#allFilterToggle + .sd-track"],
      ["toggle (off)", "#showDisposedToggle", "#showDisposedToggle + .sd-track"],
      ["debug toggle", "#dbgToggle", "#dbgToggle + .dbg-track"],
    ]) {
      const r = await ring(focusSel, ringSel);
      check(`${theme}: ${name} shows the accent ring`, ok(r), JSON.stringify(r));
      await screenshot(`${theme}-${name.replace(/[^a-z]+/g, "-").replace(/-$/, "")}`, 50);
    }

    for (const [name, open, sel, close, prep] of [
      ["dialog checkbox (checked)", "openAddFirearmModal()", "#ff-trust", "closeFirearmModal()", "document.getElementById('ff-trust').checked = true;"],
      ["dialog radio", "openExportFullModal()", "#pd-primary", "closeExportFullModal()", ""],
    ]) {
      await evaluate(`${open}; true`);
      await pause(400); // the add dialog moves focus to Make on open
      await evaluate(`${prep} document.querySelector('${sel}').scrollIntoView({block:'center'}); true`);
      await press("Tab");
      const r = await ring(sel, sel);
      check(`${theme}: ${name} shows the accent ring`, ok(r), JSON.stringify(r));
      await screenshot(`${theme}-${name.replace(/[^a-z]+/g, "-").replace(/-$/, "")}`, 50);
      await evaluate(`${close}; true`);
      await pause(300);
    }
  }
}
