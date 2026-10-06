// ui_drive missing-document scenario for Simple Firearm Logbook. The fixture
// plants one firearm with a document named "Bill of sale & receipt.txt" and
// deletes its stored file. Opens the firearm, clicks the document's Open
// button, and confirms the error banner shows the name exactly as stored,
// with no HTML codes such as &amp;.

export default async function missingDocument(helpers) {
  const { click, evaluate, waitFor, check, screenshot, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().get_firearm === 'function'", 10000);
  check("the fixture planted a document with an ampersand in its name",
    typeof fixture.label === "string" && fixture.label.includes("&"), JSON.stringify(fixture));

  const opened = await evaluate(`openDetail(${fixture.firearm_id})`);
  check("the firearm's detail view opens", opened === true, String(opened));
  const listed = await evaluate("document.getElementById('docPanelTotal').textContent");
  check("the document panel reports the file as missing", listed.includes("1 missing"), listed);

  await click(`button[onclick='openAttachment(${fixture.attachment_id})']`);
  await waitFor("document.getElementById('toast').style.display === 'block'", 5000);
  const toast = await evaluate("document.getElementById('toast').textContent");
  const expected = `That file is missing. Use Delete to remove "${fixture.label}" from the list.`;
  check("the missing-file message shows the document name as stored", toast === expected, toast);
  check("the missing-file message contains no HTML codes", !/&(amp|quot|lt|gt);/.test(toast), toast);
  await screenshot("missing-document");
}
