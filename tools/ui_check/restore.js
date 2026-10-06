// ui_drive restore scenario for Simple Firearm Logbook. The fixture plants a
// live logbook with two firearms and a backup zip holding only the first,
// and fills in the Windows Open dialog with the backup's path when it
// appears. Drives the real restore: pick the file, check the preview,
// restore without a backup first, and confirm the logbook now matches the
// backup.
//
// Does not cover: "Yes, back up first" (a Save dialog) or a failed restore.

export default async function restore(helpers) {
  const { click, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const makes = async () => {
    const r = await evaluate("api().list_firearms()");
    return (r.firearms || []).map(f => `${f.make} ${f.model}`).sort();
  };

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture wrote the backup", fixture.backup_ok === true, JSON.stringify(fixture));
  const before = await makes();
  check("the live logbook starts with both firearms",
    before.length === 2 && before.includes("Glock 19"), JSON.stringify(before));

  // a) pick the backup through the real Open dialog.
  await click("button[onclick='openRestoreModal()']");
  await waitFor("document.getElementById('restoreModalScrim').classList.contains('open')", 5000);
  await click("#restorePickBtn");
  const previewShown = await waitFor("document.getElementById('restorePreviewStep').style.display === ''", 30000)
    .then(() => true).catch(() => false);
  check("choosing the backup opens its preview", previewShown,
    await evaluate("document.getElementById('restore-err').textContent"));
  const summary = await evaluate("document.getElementById('restoreSummary').textContent");
  check("the preview counts the backup's one firearm", summary.includes("1 firearm(s)"), summary);
  check("the preview shows the backup date as YYYY-MM-DD HH:MM",
    /Backup made \d{4}-\d{2}-\d{2} \d{2}:\d{2} with/.test(summary), summary);
  await screenshot("restore-preview");

  // b) restore without a backup first.
  await click("#restoreProceedBtn");
  await waitFor("document.getElementById('restoreBackupFirstModalScrim').classList.contains('open')", 5000);
  await click("button[onclick=\"restoreBackupFirstChoice('no')\"]");
  const done = await waitFor("document.getElementById('restore-done-note').style.display === 'block'", 20000)
    .then(() => true).catch(() => false);
  const doneText = await evaluate("document.getElementById('restore-done-note').textContent || document.getElementById('restore-err').textContent");
  check("the restore completes", done, doneText);
  await screenshot("restore-done");
  await click("#restoreOkBtn");

  // c) the logbook now matches the backup.
  const after = await makes();
  check("the logbook matches the backup after restoring",
    after.length === 1 && after[0] === "Smith & Wesson 686", JSON.stringify(after));
}
