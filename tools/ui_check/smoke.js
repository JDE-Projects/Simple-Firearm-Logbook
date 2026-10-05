// ui_drive smoke scenario for Simple Firearm Logbook (see
// build-tools/ui_drive/README.md for the helpers and how this is launched).
// The fixture plants one firearm with two photos and two documents, holding
// one of each open so its file cannot be removed. Drives the real window
// through opening the firearm, deleting each photo and document (a warning
// must appear only when the file stays on disk), adding a firearm with a
// missing field and then correctly, the link opener refusing another site,
// and a theme round trip confirmed through the real Python bridge.
//
// Does not cover: file pickers (adding photos or documents by hand,
// import, export, backup, restore), the update check, or the debug log.
// Never opens the system browser.

export default async function smoke(helpers) {
  const { click, type, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const toastText = () => evaluate("document.getElementById('toast').style.display === 'block' ? document.getElementById('toast').textContent : ''");
  const hideToast = () => evaluate("clearTimeout(toastTimer); document.getElementById('toast').style.display = 'none'; true");

  // a) the page-to-Python link answers, with the real version.
  await waitFor("typeof api === 'function' && api() && typeof api().get_config === 'function'", 10000);
  const cfg = await evaluate("api().get_config()");
  check("get_config answers over the real bridge", !!cfg && cfg.ok === true, JSON.stringify(cfg));
  await waitFor("document.getElementById('verLabel').textContent.length > 0", 10000);
  const verLabel = await evaluate("document.getElementById('verLabel').textContent");
  check("version label matches the version get_config returned",
    verLabel === "v" + cfg.version && /^v\d+\.\d+\.\d+$/.test(verLabel), `${verLabel} vs v${cfg.version}`);

  // b) the planted firearm is listed and opens.
  const rowSel = `tr[onclick="openDetail(${fixture.firearm_id})"]`;
  await waitFor(`document.querySelector('${rowSel}') !== null`, 10000);
  await click(rowSel);
  await waitFor("document.getElementById('detailWrap').style.display !== 'none'", 10000);
  const title = await evaluate("document.getElementById('detailTitle').textContent");
  check("the planted firearm opens in the detail view", title.includes("Glock") && title.includes("19"), title);
  await waitFor("document.querySelectorAll('.photo-thumb').length === 2", 10000);
  await waitFor("document.querySelectorAll('.document-row').length === 2", 10000);
  await screenshot("detail");

  // c) deleting a photo whose file can be removed: no warning.
  await hideToast();
  await click(`#thumb-${fixture.photo_free} .photo-del`);
  await waitFor("document.getElementById('deletePhotoModalScrim').classList.contains('open')", 5000);
  await click("#deletePhotoModalScrim .btn.danger");
  await waitFor(`document.getElementById('thumb-${fixture.photo_free}') === null`, 10000);
  check("a removable photo deletes with no warning", (await toastText()) === "", await toastText());

  // d) deleting a photo whose file is held open: record goes, warning shows.
  await click(`#thumb-${fixture.photo_locked} .photo-del`);
  await waitFor("document.getElementById('deletePhotoModalScrim').classList.contains('open')", 5000);
  await click("#deletePhotoModalScrim .btn.danger");
  await waitFor(`document.getElementById('thumb-${fixture.photo_locked}') === null`, 10000);
  const photoWarned = await waitFor("document.getElementById('toast').style.display === 'block'", 5000)
    .then(() => true).catch(() => false);
  const photoWarning = await toastText();
  check("a photo whose file stays on disk shows a plain-language warning",
    photoWarned && photoWarning.includes("could not be removed from disk") && !/errno|exception|traceback/i.test(photoWarning),
    photoWarning);
  await screenshot("photo-warning", 200);
  await hideToast();

  // e) the same two cases for documents.
  await click(`button[onclick="openDeleteAttachmentModal(${fixture.doc_free})"]`);
  await waitFor("document.getElementById('deleteAttachmentModalScrim').classList.contains('open')", 5000);
  await click("#deleteAttachmentModalScrim .btn.danger");
  await waitFor("document.querySelectorAll('.document-row').length === 1", 10000);
  check("a removable document deletes with no warning", (await toastText()) === "", await toastText());

  await click(`button[onclick="openDeleteAttachmentModal(${fixture.doc_locked})"]`);
  await waitFor("document.getElementById('deleteAttachmentModalScrim').classList.contains('open')", 5000);
  await click("#deleteAttachmentModalScrim .btn.danger");
  await waitFor("document.querySelectorAll('.document-row').length === 0", 10000);
  const docWarned = await waitFor("document.getElementById('toast').style.display === 'block'", 5000)
    .then(() => true).catch(() => false);
  const docWarning = await toastText();
  check("a document whose file stays on disk shows a plain-language warning",
    docWarned && docWarning.includes("could not be removed from disk") && !/errno|exception|traceback/i.test(docWarning),
    docWarning);
  await screenshot("document-warning", 200);
  await hideToast();

  // f) the link opener refuses any site but jde-projects.com. Only the
  // refused case is called, so no browser opens.
  const refused = await evaluate("api().open_url('https://example.com')");
  check("open_url refuses another site", !!refused && refused.ok === false, JSON.stringify(refused));

  // g) error path, then core workflow: add a firearm.
  const listBefore = await evaluate("api().list_firearms()");
  await click("button[onclick='showListView()']");
  await click("button[onclick='openAddFirearmModal()']");
  await waitFor("document.getElementById('firearmModalScrim').classList.contains('open')", 5000);
  await type("#ff-make", "Ruger");
  await type("#ff-model", "");
  await click("#firearmModalScrim .btn.primary");
  const errShown = await waitFor("document.getElementById('ff-err').textContent.trim().length > 0", 5000)
    .then(() => true).catch(() => false);
  const errText = await evaluate("document.getElementById('ff-err').textContent");
  check("a missing model is refused with a plain-language message",
    errShown && !/errno|exception|traceback/i.test(errText), errText);
  await type("#ff-model", "10/22");
  await click("#firearmModalScrim .btn.primary");
  await waitFor("!document.getElementById('firearmModalScrim').classList.contains('open')", 10000);
  const newTitle = await evaluate("document.getElementById('detailTitle').textContent");
  check("the new firearm saves and opens", newTitle.includes("Ruger") && newTitle.includes("10/22"), newTitle);
  const listAfter = await evaluate("api().list_firearms()");
  const count = r => (r && (r.firearms || r.items || [])).length;
  check("one firearm was added", count(listAfter) === count(listBefore) + 1,
    `${count(listBefore)} -> ${count(listAfter)}`);

  // h) theme round trip, screenshot of each, confirmed saved through Python.
  for (let i = 0; i < 2; i++) {
    const wasLight = await evaluate("document.body.classList.contains('light')");
    await click("#theme-btn");
    await waitFor(`document.body.classList.contains('light') === ${!wasLight}`, 5000);
    await screenshot(wasLight ? "theme-dark" : "theme-light");
    const saved = await evaluate("api().get_config()");
    check(`theme toggle ${i + 1} was saved through Python`,
      saved.theme === (wasLight ? "dark" : "light"), JSON.stringify(saved.theme));
  }
}
