// ui_drive debug-start-fail scenario for Simple Firearm Logbook. The fixture
// blocks every debug log name the app could pick, so turning the debug log
// switch on fails. The warning must show in the banner and the switch must
// go back to off.

export default async function debugStartFail(helpers) {
  const { evaluate, waitFor, click, check, screenshot, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().set_debug === 'function'", 10000);
  check("the fixture placed the blocking junctions", fixture && fixture.junctions > 0, JSON.stringify(fixture));

  await click(".dbg-toggle .dbg-track");
  const failed = "document.getElementById('toast').style.display === 'block'" +
    " && document.getElementById('toast').textContent.includes('could not create')";
  let shown = true;
  await waitFor(failed, 5000).catch(() => { shown = false; });
  const text = await evaluate("document.getElementById('toast').textContent");
  check("the could-not-create warning shows in the banner", shown, text);

  let off = true;
  await waitFor("!document.getElementById('dbgToggle').checked", 3000).catch(() => { off = false; });
  check("the debug switch is back off", off, "switch still on");
  await screenshot("start-fail", 0);
}
