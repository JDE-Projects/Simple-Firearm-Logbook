// ui_drive restore-close scenario for Simple Firearm Logbook. Uses the
// restore fixture: picks the backup through the real Open dialog and leaves
// the preview open, so the driver closes the app mid-preview. The scenario
// reports the temp folder the backup was unpacked into; the person running
// the check confirms that folder is gone once the app has closed, since a
// scenario cannot look after the app exits.

export default async function restoreClose(helpers) {
  const { click, evaluate, waitFor, check, screenshot, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture wrote the backup", fixture.backup_ok === true, JSON.stringify(fixture));

  await click("button[onclick='openRestoreModal()']");
  await waitFor("document.getElementById('restoreModalScrim').classList.contains('open')", 5000);
  await click("#restorePickBtn");
  const previewShown = await waitFor("document.getElementById('restorePreviewStep').style.display === ''", 30000)
    .then(() => true).catch(() => false);
  check("choosing the backup opens its preview", previewShown,
    await evaluate("document.getElementById('restore-err').textContent"));

  const staging = await evaluate("RESTORE.staging");
  check(`the backup was unpacked for the preview: ${staging}`,
    typeof staging === "string" && /sfl_restore_/.test(staging), String(staging));
  await screenshot("restore-preview-left-open");
}
