// ui_drive window-taskbar scenario for Simple Firearm Logbook. The fixture
// saves a window position, then moves the window elsewhere, minimizes it, and
// restores it. Restoring from the taskbar must leave the window where the
// person moved it, not jump it back to the saved position.

export default async function windowTaskbar(helpers) {
  const { evaluate, waitFor, check, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture saved a window position", fixture.saved === true, JSON.stringify(fixture));

  // screenX/screenY are in page pixels; the fixture's spots are physical.
  const dpr = await evaluate("window.devicePixelRatio");
  const movedX = fixture.moved.x / dpr;
  const movedY = fixture.moved.y / dpr;
  const nearMoved = `Math.abs(window.screenX - ${movedX}) <= 20 && Math.abs(window.screenY - ${movedY}) <= 20`;

  const moved = await waitFor(nearMoved, 15000).then(() => true, () => false);
  check("the fixture moved the window", moved,
    await evaluate("JSON.stringify({x: window.screenX, y: window.screenY})"));

  // The fixture minimizes 1 s after the move and restores 1.5 s later. Wait
  // well past the restore, then confirm the window stayed where it was moved.
  await new Promise(resolve => setTimeout(resolve, 6000));
  const where = await evaluate("JSON.stringify({x: window.screenX, y: window.screenY})");
  const p = JSON.parse(where);
  check(`the window stays put after a restore from the taskbar: ${where}, moved to ` +
    `${Math.round(movedX)}, ${Math.round(movedY)}`,
    Math.abs(p.x - movedX) <= 20 && Math.abs(p.y - movedY) <= 20, where);
}
