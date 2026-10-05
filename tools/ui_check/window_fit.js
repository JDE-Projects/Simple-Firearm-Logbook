// ui_drive window-fit scenario for Simple Firearm Logbook. The fixture saves
// a window size far larger than the screen. On launch the app must shrink the
// window to fit the screen (minus the taskbar) instead of opening it as saved.

export default async function windowFit(helpers) {
  const { evaluate, waitFor, check, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture saved an oversized window", fixture.saved === true, JSON.stringify(fixture));

  // The fit runs after the window shows, then refits once about 300 ms later.
  const fits = "window.outerWidth <= screen.availWidth && window.outerHeight <= screen.availHeight";
  await waitFor(fits, 5000).catch(() => {});
  const size = await evaluate(
    "JSON.stringify({w: window.outerWidth, h: window.outerHeight," +
    " availW: screen.availWidth, availH: screen.availHeight})"
  );
  const s = JSON.parse(size);

  check(`the window fits the screen: ${size}`, s.w <= s.availW && s.h <= s.availH, size);
  // A skipped restore leaves the default 1150 x 760 window, which would also
  // fit, so confirm the saved size was applied and then cut to the screen.
  check("the window was sized from the saved position, not the default",
    s.w >= s.availW - 40 && s.h >= s.availH - 80, size);
}
