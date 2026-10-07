// ui_drive CSV import scenario. Drives the real CSV wizard and native Open
// dialog twice: once without an extension, then with the public csvImport
// dialog hooks registered directly through window.SFL.

export default async function csvImport(helpers) {
  const { click, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const count = result => (result && (result.firearms || result.items || [])).length;
  const firearms = () => evaluate("api().list_firearms()");
  const importModalOpen = () => "document.getElementById('importModalScrim').classList.contains('open')";
  const previewVisible = () => "document.getElementById('importPreviewStep').style.display === ''";
  const doneVisible = () => "document.getElementById('import-done-note').style.display === 'block'";

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture supplied the expected CSV rows",
    fixture && fixture.ready === 3 && fixture.errors === 1, JSON.stringify(fixture));
  const before = count(await firearms());
  check("the logbook starts empty", before === 0, String(before));

  // Part A: the normal wizard has no csvImport extension registered.
  await click("button[onclick='openImportModal()']");
  await waitFor(importModalOpen(), 5000);
  await click("#importPickBtn");
  await waitFor("document.getElementById('importMapStep').style.display === ''", 30000);
  await click("#importNextBtn");
  await waitFor(previewVisible(), 10000);
  const summaryA = await evaluate("document.getElementById('importSummary').textContent");
  check("the preview reports three ready rows and one error",
    summaryA.includes("3 ready") && summaryA.includes("1 with errors"), summaryA);
  const emptySlot = await evaluate("(() => { const slot = document.getElementById('csvImportSlot'); return slot.children.length === 0 && getComputedStyle(slot).display === 'none'; })()");
  check("the empty csvImport slot is hidden", emptySlot, String(emptySlot));
  await screenshot("csv-import-preview");

  await click("#importCommitBtn");
  await waitFor(doneVisible(), 20000);
  const doneA = await evaluate("document.getElementById('import-done-note').textContent");
  const afterA = count(await firearms());
  check("the first import completes with three firearms", doneA.includes("Imported 3 firearm(s)."), doneA);
  check("the first import adds exactly three firearms", afterA === before + 3, `${before} -> ${afterA}`);
  await screenshot("csv-import-done");
  await click("#importOkBtn");
  await waitFor(`!${importModalOpen()}`, 5000);

  // Part B: one public dialog-hook registration, controlled and observed by
  // window-level state so both blocked and successful commits are covered.
  await evaluate(`(() => {
    window.csvImportDrive = {collectCalls: 0, calls: []};
    window.SFL.onDialog('csvImport', {
      open(context) {
        window.csvImportDrive.calls.push({hook: 'open', records: context.records.length});
        const marker = document.createElement('div');
        marker.id = 'csv-import-marker';
        marker.textContent = 'csvImport extension marker';
        window.SFL.slots.csvImport.replaceChildren(marker);
      },
      collect(context) {
        window.csvImportDrive.collectCalls += 1;
        window.csvImportDrive.calls.push({hook: 'collect', records: context.records.length});
        if (window.csvImportDrive.collectCalls === 1) {
          return {ok: false, error: 'csvImport extension blocked this import.'};
        }
        return {ok: true, data: {marker: true}};
      },
      done(result, context) {
        window.csvImportDrive.calls.push({hook: 'done', records: context.records.length, result});
      },
    });
    return true;
  })()`);

  await click("button[onclick='openImportModal()']");
  await waitFor(importModalOpen(), 5000);
  await click("#importPickBtn");
  await waitFor("document.getElementById('importMapStep').style.display === ''", 30000);
  await click("#importNextBtn");
  await waitFor(previewVisible(), 10000);
  const openCall = await evaluate("window.csvImportDrive.calls.filter(call => call.hook === 'open')");
  check("the csvImport open hook sees the three importable records",
    openCall.length === 1 && openCall[0].records === 3, JSON.stringify(openCall));
  const filledSlot = await evaluate("(() => { const slot = document.getElementById('csvImportSlot'); return slot.textContent.includes('csvImport extension marker') && getComputedStyle(slot).display !== 'none'; })()");
  check("the csvImport slot shows the extension marker", filledSlot, String(filledSlot));
  await screenshot("csv-import-extension-preview");

  await click("#importPreviewBody input[type='checkbox']");
  await click("#importCommitBtn");
  const blockedError = await waitFor("document.getElementById('import-err').textContent.includes('csvImport extension blocked this import.')", 5000)
    .then(() => true).catch(() => false);
  const afterBlocked = count(await firearms());
  const doneAfterBlocked = await evaluate("window.csvImportDrive.calls.filter(call => call.hook === 'done')");
  check("the extension blocks the first selected import", blockedError,
    await evaluate("document.getElementById('import-err').textContent"));
  check("the blocked import does not change the firearm count", afterBlocked === afterA,
    `${afterA} -> ${afterBlocked}`);
  check("the blocked import does not call done", doneAfterBlocked.length === 0, JSON.stringify(doneAfterBlocked));
  await screenshot("csv-import-extension-blocked");

  await click("#importCommitBtn");
  await waitFor(doneVisible(), 20000);
  const doneB = await evaluate("document.getElementById('import-done-note').textContent");
  const afterB = count(await firearms());
  const hookCalls = await evaluate("window.csvImportDrive.calls");
  const collectCalls = hookCalls.filter(call => call.hook === "collect");
  const doneCalls = hookCalls.filter(call => call.hook === "done");
  check("the successful collect hook receives the two checked records",
    collectCalls.length === 2 && collectCalls[1].records === 2, JSON.stringify(collectCalls));
  check("the second import completes with two firearms", doneB.includes("Imported 2 firearm(s)."), doneB);
  check("the second import adds exactly two firearms", afterB === afterA + 2, `${afterA} -> ${afterB}`);
  check("done receives the successful result once",
    doneCalls.length === 1 && doneCalls[0].records === 2 && doneCalls[0].result && doneCalls[0].result.ok === true,
    JSON.stringify(doneCalls));
  const extensionProblem = await evaluate("document.getElementById('extErrModalScrim').classList.contains('open')");
  check("no Extension problem dialog appeared", !extensionProblem, String(extensionProblem));
}
