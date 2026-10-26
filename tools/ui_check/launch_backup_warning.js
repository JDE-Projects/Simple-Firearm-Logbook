// ui_drive launch-backup-warning scenario. A plain file blocks creation of
// the backup folder. The launch warning must show while the valid logbook
// still opens and lists its firearm.

export default async function launchBackupWarning(helpers) {
  const { evaluate, waitFor, check, screenshot } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  const warning = "document.getElementById('toast').style.display === 'block'" +
    " && document.getElementById('toast').textContent.includes(\"Today's backup of your logbook couldn't be saved\")";
  let shown = true;
  await waitFor(warning, 5000).catch(() => { shown = false; });
  const text = await evaluate("document.getElementById('toast').textContent");
  check("the launch backup warning is shown", shown, text);

  await waitFor("document.body.textContent.includes('Glock') && document.body.textContent.includes('19')", 5000);
  const firearmListed = await evaluate("document.body.textContent.includes('Glock') && document.body.textContent.includes('19')");
  check("the valid firearm is listed", firearmListed, "Glock 19 missing");
  await screenshot("launch-backup-warning", 0);
}
