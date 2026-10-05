// ui_drive debug-write-fail scenario for Simple Firearm Logbook. The debug
// log switch is turned on, then the fixture locks the new log file, so a
// later write fails and the log turns itself off. The warning must show in
// the banner and the switch must go back to off. Turning the switch on again
// starts a new, unlocked log, and the switch must stay on.

export default async function debugWriteFail(helpers) {
  const { evaluate, waitFor, click, check, screenshot } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().set_debug === 'function'", 10000);

  await click(".dbg-toggle .dbg-track");
  await waitFor("document.getElementById('dbgToggle').checked", 3000);

  // The fixture locks the file shortly after it appears, so keep writing
  // until a write fails.
  const failed = "document.getElementById('toast').style.display === 'block'" +
    " && document.getElementById('toast').textContent.includes('write failed')";
  let shown = false;
  for (let i = 0; i < 40 && !shown; i++) {
    await evaluate("api().log('ui check write ' + Date.now()).then(() => 'ok')");
    shown = await evaluate(failed);
    if (!shown) await new Promise(r => setTimeout(r, 200));
  }
  const text = await evaluate("document.getElementById('toast').textContent");
  check("the write-failed warning shows in the banner", shown, text);

  let off = true;
  await waitFor("!document.getElementById('dbgToggle').checked", 3000).catch(() => { off = false; });
  check("the debug switch went off when logging stopped", off, "switch still on");
  await screenshot("write-fail", 0);

  // Wait for the banner to clear so a new warning would be noticed.
  await waitFor("document.getElementById('toast').style.display === 'none'", 8000);
  await click(".dbg-toggle .dbg-track");
  await new Promise(r => setTimeout(r, 1000));
  const on = await evaluate("document.getElementById('dbgToggle').checked");
  const banner = await evaluate("document.getElementById('toast').style.display");
  check("turning it on again starts a new log and stays on", on && banner === "none", `on=${on} banner=${banner}`);
}
