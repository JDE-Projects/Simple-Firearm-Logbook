// ui_drive double-submit scenario for Simple Firearm Logbook. Registers a slow
// collect hook on each saving dialog through window.SFL, so every save stays
// in flight for a moment (Rename document and Export all have no hook, so a
// slow stand-in replaces their api call instead), then clicks the confirm
// button twice in a row. Each
// dialog must run its save once, keep its buttons off while the save runs,
// and switch them back on afterwards. Uses the CSV import fixture for the
// import dialog's Open dialog.
//
// Does not cover: restore (see double_submit_restore.js).

export default async function doubleSubmit(helpers) {
  const { click, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const firearms = async () => ((await evaluate("api().list_firearms()")).firearms || []);
  const isOpen = id => `document.getElementById('${id}').classList.contains('open')`;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture supplied the CSV file", fixture && fixture.ready === 3, JSON.stringify(fixture));

  // Each slow hook counts its collect calls and holds the save for 600 ms.
  await evaluate(`(() => {
    window.doubleSubmitDrive = {};
    window.slowHook = name => window.SFL.onDialog(name, {
      collect() {
        window.doubleSubmitDrive[name] = (window.doubleSubmitDrive[name] || 0) + 1;
        return new Promise(resolve => setTimeout(() => resolve({ok: true}), 600));
      },
    });
    return true;
  })()`);
  const collects = name => evaluate(`window.doubleSubmitDrive['${name}'] || 0`);

  // Two clicks in the same tick, the fastest double click there can be, then
  // a look at the dialog's buttons while the first save is still running.
  const doubleClickConfirm = (scrimId, onclick) => evaluate(`(() => {
    const confirm = document.querySelector("#${scrimId} button[onclick='${onclick}']");
    confirm.click();
    confirm.click();
    return Array.from(document.querySelectorAll('#${scrimId} button')).every(b => b.disabled);
  })()`);
  const buttonsBackOn = scrimId => evaluate(
    `Array.from(document.querySelectorAll('#${scrimId} button')).every(b => !b.disabled)`);

  // a) add firearm
  const before = (await firearms()).length;
  await evaluate("slowHook('firearmForm')");
  await click("button[onclick='openAddFirearmModal()']");
  await waitFor(isOpen("firearmModalScrim"), 5000);
  await evaluate(`(() => {
    document.getElementById('ff-make').value = 'Double';
    document.getElementById('ff-model').value = 'Click';
    document.getElementById('ff-serial').value = 'DBL-001';
    return true;
  })()`);
  const addOff = await doubleClickConfirm("firearmModalScrim", "saveFirearm()");
  check("add firearm: buttons are off while saving", addOff, String(addOff));
  await waitFor(`!${isOpen("firearmModalScrim")}`, 10000);
  await waitFor("document.getElementById('dispositionPanel') !== null", 10000);
  const added = (await firearms()).filter(f => f.serial_number === "DBL-001");
  check("add firearm: a double click saves one firearm",
    added.length === 1 && (await firearms()).length === before + 1, JSON.stringify(added));
  check("add firearm: the save ran once", (await collects("firearmForm")) === 1, String(await collects("firearmForm")));
  check("add firearm: buttons are back on afterwards", await buttonsBackOn("firearmModalScrim"), "");

  // b) record disposition
  await evaluate("slowHook('disposition')");
  await click("button[onclick='openDispositionModal()']");
  await waitFor(isOpen("dispositionModalScrim"), 5000);
  await evaluate("(() => { const s = document.getElementById('dp-status'); s.value = 'Sold'; onDispositionStatusChange(); return true; })()");
  const dispOff = await doubleClickConfirm("dispositionModalScrim", "saveDisposition()");
  check("disposition: buttons are off while saving", dispOff, String(dispOff));
  await waitFor(`!${isOpen("dispositionModalScrim")}`, 10000);
  check("disposition: the save ran once", (await collects("disposition")) === 1, String(await collects("disposition")));
  check("disposition: buttons are back on afterwards", await buttonsBackOn("dispositionModalScrim"), "");

  // c) mark as owned
  await evaluate("slowHook('markOwned')");
  await waitFor("document.querySelector(\"button[onclick='openMarkOwnedModal()']\") !== null", 10000);
  await click("button[onclick='openMarkOwnedModal()']");
  await waitFor(isOpen("markOwnedModalScrim"), 5000);
  const ownedOff = await doubleClickConfirm("markOwnedModalScrim", "confirmMarkOwned()");
  check("mark as owned: buttons are off while saving", ownedOff, String(ownedOff));
  await waitFor(`!${isOpen("markOwnedModalScrim")}`, 10000);
  check("mark as owned: the save ran once", (await collects("markOwned")) === 1, String(await collects("markOwned")));
  check("mark as owned: buttons are back on afterwards", await buttonsBackOn("markOwnedModalScrim"), "");

  // Rename and Export all have no dialog hooks, so a stand-in for their api
  // call counts each call and holds it for 600 ms instead. The stand-in for
  // export_full never opens the Save dialog.
  await evaluate(`(() => {
    window.slowApi = (method, result) => {
      window.doubleSubmitDrive[method] = 0;
      window.pywebview.api[method] = () => {
        window.doubleSubmitDrive[method] += 1;
        return new Promise(resolve => setTimeout(() => resolve(result()), 600));
      };
    };
    return true;
  })()`);

  // d) rename document, against one listed document put in place for the check
  await evaluate(`(() => {
    currentAttachments = [{id: 9001, label: 'receipt', seq: 1, size_bytes: 10, missing: false}];
    renderDocuments();
    slowApi('rename_attachment', () => ({
      ok: true, attachments: [{...currentAttachments[0], label: 'renamed receipt'}],
    }));
    openRenameAttachmentModal(9001);
    document.getElementById('ren-att-label').value = 'renamed receipt';
    return true;
  })()`);
  await waitFor(isOpen("renameAttachmentModalScrim"), 5000);
  const renameOff = await doubleClickConfirm("renameAttachmentModalScrim", "saveRenameAttachment()");
  check("rename document: buttons are off while saving", renameOff, String(renameOff));
  await waitFor(`!${isOpen("renameAttachmentModalScrim")}`, 10000);
  check("rename document: the rename ran once",
    (await collects("rename_attachment")) === 1, String(await collects("rename_attachment")));
  check("rename document: buttons are back on afterwards", await buttonsBackOn("renameAttachmentModalScrim"), "");

  // e) Export all
  await evaluate("slowApi('export_full', () => ({ok: true, path: 'C:\\\\stand-in\\\\export.zip'}))");
  await evaluate("(() => { openExportFullModal(); return true; })()");
  await waitFor(isOpen("exportFullModalScrim"), 5000);
  const exportOff = await doubleClickConfirm("exportFullModalScrim", "runExportFull()");
  check("export all: buttons are off while exporting", exportOff, String(exportOff));
  await waitFor("document.getElementById('export-full-note').style.display === 'block'", 10000);
  check("export all: the export ran once",
    (await collects("export_full")) === 1, String(await collects("export_full")));
  check("export all: buttons are back on afterwards", await buttonsBackOn("exportFullModalScrim"), "");
  await click("#exportFullOkBtn");
  await waitFor(`!${isOpen("exportFullModalScrim")}`, 5000);

  // f) delete firearm, with "Export backup first" off so no Save dialog opens
  await evaluate("slowHook('deleteFirearm')");
  await click("button[onclick='openDeleteFirearmModal()']");
  await waitFor(isOpen("deleteFirearmModalScrim"), 5000);
  await evaluate("(() => { document.getElementById('delExportFirst').checked = false; return true; })()");
  const delOff = await doubleClickConfirm("deleteFirearmModalScrim", "confirmDeleteFirearm()");
  check("delete firearm: buttons are off while deleting", delOff, String(delOff));
  await waitFor(`!${isOpen("deleteFirearmModalScrim")}`, 10000);
  const delErr = await evaluate("document.getElementById('del-firearm-err').textContent");
  check("delete firearm: the delete ran once with no error",
    (await collects("deleteFirearm")) === 1 && delErr === "", `${await collects("deleteFirearm")} | ${delErr}`);
  check("delete firearm: the firearm is gone", (await firearms()).length === before, String((await firearms()).length));
  check("delete firearm: buttons are back on afterwards", await buttonsBackOn("deleteFirearmModalScrim"), "");

  // g) CSV import
  await evaluate("slowHook('csvImport')");
  await click("button[onclick='openImportModal()']");
  await waitFor(isOpen("importModalScrim"), 5000);
  await click("#importPickBtn");
  await waitFor("document.getElementById('importMapStep').style.display === ''", 30000);
  await click("#importNextBtn");
  await waitFor("document.getElementById('importPreviewStep').style.display === ''", 10000);
  const importOff = await evaluate(`(() => {
    const confirm = document.getElementById('importCommitBtn');
    confirm.click();
    confirm.click();
    const cancel = document.getElementById('importCancelBtn');
    return confirm.disabled && cancel.disabled;
  })()`);
  check("import: Import selected and Cancel are off while importing", importOff, String(importOff));
  await waitFor("document.getElementById('import-done-note').style.display === 'block'", 20000);
  const doneText = await evaluate("document.getElementById('import-done-note').textContent");
  check("import: a double click imports the rows once",
    doneText.includes("Imported 3 firearm(s).") && (await firearms()).length === before + 3,
    `${doneText} | ${(await firearms()).length}`);
  check("import: the import ran once", (await collects("csvImport")) === 1, String(await collects("csvImport")));
  const okOn = await evaluate("!document.getElementById('importOkBtn').disabled");
  check("import: the OK button works afterwards", okOn, String(okOn));
  await screenshot("double-submit-import-done");
  await click("#importOkBtn");

  const extensionProblem = await evaluate(isOpen("extErrModalScrim"));
  check("no Extension problem dialog appeared", !extensionProblem, String(extensionProblem));
}
