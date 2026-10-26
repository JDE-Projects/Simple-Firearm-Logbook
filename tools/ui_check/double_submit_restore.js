// ui_drive double-submit scenario for the restore dialog. Uses the restore
// fixture. Registers a slow restore collect hook so the restore stays in
// flight for a moment, chooses "No, restore now", then checks the restore
// dialog's buttons are off (so "Restore this backup" can't start a second
// restore and Cancel can't delete the unpacked backup mid-restore), that the
// restore ran once, and that the OK button works afterwards.
//
// Does not cover: "Yes, back up first" (a Save dialog).

export default async function doubleSubmitRestore(helpers) {
  const { click, evaluate, waitFor, check, screenshot, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture wrote the backup", fixture.backup_ok === true, JSON.stringify(fixture));

  await evaluate(`(() => {
    window.restoreCollects = 0;
    window.SFL.onDialog('restore', {
      collect() {
        window.restoreCollects += 1;
        return new Promise(resolve => setTimeout(() => resolve({ok: true}), 600));
      },
    });
    return true;
  })()`);

  await click("button[onclick='openRestoreModal()']");
  await waitFor("document.getElementById('restoreModalScrim').classList.contains('open')", 5000);
  await click("#restorePickBtn");
  await waitFor("document.getElementById('restorePreviewStep').style.display === ''", 30000);
  await click("#restoreProceedBtn");
  await waitFor("document.getElementById('restoreBackupFirstModalScrim').classList.contains('open')", 5000);

  // Choose "No, restore now", then try "Restore this backup" again at once.
  const during = await evaluate(`(() => {
    document.querySelector("button[onclick=\\"restoreBackupFirstChoice('no')\\"]").click();
    const proceed = document.getElementById('restoreProceedBtn');
    const cancel = document.getElementById('restoreCancelBtn');
    proceed.click();
    return {
      proceedOff: proceed.disabled,
      cancelOff: cancel.disabled,
      promptReopened: document.getElementById('restoreBackupFirstModalScrim').classList.contains('open'),
    };
  })()`);
  check("restore: Restore this backup and Cancel are off while restoring",
    during.proceedOff && during.cancelOff, JSON.stringify(during));
  check("restore: a second click doesn't reopen the back-up-first prompt", !during.promptReopened, JSON.stringify(during));

  const done = await waitFor("document.getElementById('restore-done-note').style.display === 'block'", 20000)
    .then(() => true).catch(() => false);
  check("restore: the restore completes", done,
    await evaluate("document.getElementById('restore-err').textContent"));
  const collects = await evaluate("window.restoreCollects");
  check("restore: the restore ran once", collects === 1, String(collects));
  const okOn = await evaluate("!document.getElementById('restoreOkBtn').disabled");
  check("restore: the OK button works afterwards", okOn, String(okOn));
  await screenshot("double-submit-restore-done");
  await click("#restoreOkBtn");
}
