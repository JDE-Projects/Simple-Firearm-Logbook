// ui_drive launch-restore scenario. The fixture plants a valid logbook,
// makes a checked automatic-launch backup, then damages the live database.
// The fixture dismisses the native restore message, so this checks the
// restored record in the real app and confirms an edit persists after reload.

export default async function launchRestore(helpers) {
  const { click, type, evaluate, waitFor, check, screenshot, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  const list = await evaluate("api().list_firearms()");
  const firearm = (list.firearms || []).find(item => `${item.make} ${item.model}` === fixture.firearm);
  check("the firearm from the automatic backup is listed", !!firearm, JSON.stringify(list));
  if (!firearm) return;

  const row = `tr[onclick="openDetail(${firearm.id})"]`;
  await waitFor(`document.querySelector('${row}') !== null`, 10000);
  await click(row);
  await waitFor("document.getElementById('detailWrap').style.display !== 'none'", 10000);
  const title = await evaluate("document.getElementById('detailTitle').textContent");
  check("the restored firearm opens", title === fixture.firearm, title);

  await click("button[onclick='openEditFirearmModal()']");
  await waitFor("document.getElementById('firearmModalScrim').classList.contains('open')", 5000);
  await type("#ff-model", "19 restored");
  await click("#firearmModalScrim .btn.primary");
  await waitFor("!document.getElementById('firearmModalScrim').classList.contains('open')", 10000);

  await click("button[onclick='showListView()']");
  await waitFor(`document.querySelector('${row}') !== null`, 10000);
  await click(row);
  await waitFor("document.getElementById('detailTitle').textContent === 'Glock 19 restored'", 10000);
  const reloaded = await evaluate(`api().get_firearm(${firearm.id})`);
  check("the saved value remains after reloading the firearm",
    reloaded.ok === true && reloaded.firearm.model === "19 restored", JSON.stringify(reloaded));
  await screenshot("launch-restore");
}
