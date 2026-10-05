// ui_drive debug-log scenario for Simple Firearm Logbook. The fixture places
// five older debug logs and holds the oldest open. Launch pruning cannot
// delete that one, so its warning must reach the in-app banner once the page
// loads. Then the debug log switch is turned on and a line is written. The
// files left in the copied app folder are checked by hand after the run.

export default async function debugLog(helpers) {
  const { evaluate, waitFor, click, check, screenshot, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().set_debug === 'function'", 10000);
  check("the fixture placed the old logs", fixture && fixture.logs.length === 5, JSON.stringify(fixture));

  const bannerShows = "document.getElementById('toast').style.display === 'block'" +
    " && document.getElementById('toast').textContent.includes('could not delete old log')";
  let shown = true;
  await waitFor(bannerShows, 8000).catch(() => { shown = false; });
  const text = await evaluate("document.getElementById('toast').textContent");
  check("the launch prune warning shows in the banner", shown, text);
  check("the warning names the locked log", text.includes(fixture.locked), text);
  await screenshot("warning-banner", 0);

  await click(".dbg-toggle .dbg-track");
  await waitFor("document.getElementById('dbgToggle').checked", 3000);
  const result = await evaluate("api().log('ui check C:\\\\Users\\\\' + 'TestUser\\\\record.pdf').then(() => 'ok')");
  check("a debug line was written", result === "ok", String(result));
}
