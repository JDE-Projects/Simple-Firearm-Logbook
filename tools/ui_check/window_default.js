// ui_drive window-default scenario for Simple Firearm Logbook. No window
// position is saved (a first launch). On a screen that can hold the default
// 1280 x 820 window, the app must leave its size alone and keep it on screen.

export default async function windowDefault(helpers) {
  const { evaluate, waitFor, check } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  // Placement runs after the window shows, then refits about 300 ms later.
  await new Promise(resolve => setTimeout(resolve, 3000));
  const size = await evaluate(
    "JSON.stringify({x: window.screenX, y: window.screenY, w: window.outerWidth, h: window.outerHeight," +
    " innerW: window.innerWidth, innerH: window.innerHeight," +
    " availX: screen.availLeft, availY: screen.availTop, availW: screen.availWidth, availH: screen.availHeight})"
  );
  const s = JSON.parse(size);
  const roomy = s.availW >= 1340 && s.availH >= 900;
  check(`the screen can hold the default window: ${size}`, roomy, size);
  check(`the page keeps the default 1280 x 820 size: ${size}`,
    Math.abs(s.innerW - 1280) <= 2 && Math.abs(s.innerH - 820) <= 2, size);
  check(`the title bar is on screen: ${size}`, s.y >= s.availY && s.x >= s.availX - 10, size);
}
