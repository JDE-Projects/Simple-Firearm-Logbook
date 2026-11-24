// ui_drive theme-save-fail scenario for Simple Firearm Logbook. The fixture
// holds the settings file open so saving it fails. Clicking the theme button
// must show an error and leave the theme as it was, so the screen never shows
// a theme that will be lost at the next launch.

export default async function themeSaveFail(helpers) {
  const { evaluate, waitFor, click, check, screenshot } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().save_theme === 'function'", 10000);
  const before = await evaluate("document.body.classList.contains('light')");
  check("the app starts in the dark theme", before === false, `light=${before}`);

  await click("#theme-btn");
  let shown = true;
  await waitFor(
    "document.getElementById('toast').style.display === 'block'" +
    " && document.getElementById('toast').textContent.includes('Could not save the theme')",
    5000,
  ).catch(() => { shown = false; });
  const text = await evaluate("document.getElementById('toast').textContent");
  check("a failed theme save shows an error", shown, text);

  const after = await evaluate("document.body.classList.contains('light')");
  check("the theme stays as it was when the save fails", after === false, `light=${after}`);
  await screenshot("theme-save-fail", 0);
}
