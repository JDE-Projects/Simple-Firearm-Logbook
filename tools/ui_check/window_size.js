// ui_drive window-size scenario for Simple Firearm Logbook. The fixture saves
// a window size smaller than the default. On launch the app must reopen at
// that saved size, not at its default size.

export default async function windowSize(helpers) {
  const { evaluate, waitFor, check, fixture } = helpers;

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture saved a smaller window", fixture.saved === true, JSON.stringify(fixture));

  // Restore runs after the window shows, then refits about 300 ms later.
  await new Promise(resolve => setTimeout(resolve, 3000));
  const size = await evaluate(
    "JSON.stringify({outerW: window.outerWidth, outerH: window.outerHeight," +
    " innerW: window.innerWidth, innerH: window.innerHeight, dpr: window.devicePixelRatio})"
  );
  const s = JSON.parse(size);
  const savedW = fixture.window.width / s.dpr;
  const savedH = fixture.window.height / s.dpr;
  check(`the window reopens at the saved size: ${size}, saved ${Math.round(savedW)} x ${Math.round(savedH)}`,
    Math.abs(s.outerW - savedW) <= 40 && Math.abs(s.outerH - savedH) <= 80, size);
}
