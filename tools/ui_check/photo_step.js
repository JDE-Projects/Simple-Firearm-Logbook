// ui_drive photo-step scenario for Simple Firearm Logbook. Opens fixture
// photos in the real lightbox and verifies stepping, wrapping, missing-photo
// handling, zoom, control visibility, and the viewport layout.
//
// Does not cover: visual styling beyond overlap and viewport bounds, keyboard
// navigation, drag scrolling at actual size, or every supported image format.

export default async function photoStep(helpers) {
  const { click, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const image = "document.getElementById('lightboxImg')";
  const displayed = id => `getComputedStyle(document.getElementById('${id}')).display !== 'none'`;
  const count = () => evaluate("document.getElementById('lightboxCount').textContent");
  const width = () => evaluate(`${image}.naturalWidth`);
  const lightboxOpen = "document.getElementById('lightboxScrim').classList.contains('open')";
  const photoReady = expectedWidth =>
    `${lightboxOpen} && ${image}.naturalWidth === ${expectedWidth} && ${displayed('lightboxImg')}`;
  const thumbnail = id => `#thumb-${id} img`;
  const row = id => `tr[onclick="openDetail(${id})"]`;

  async function openFirearm(id, photoCount) {
    if (await evaluate("document.getElementById('detailWrap').style.display !== 'none'")) {
      await click("button[onclick='showListView()']");
      await waitFor("document.getElementById('listWrap').style.display !== 'none'", 5000);
    }
    const selector = row(id);
    await waitFor(`document.querySelector('${selector}') !== null`, 10000);
    await click(selector);
    await waitFor(`document.querySelectorAll('.photo-thumb').length === ${photoCount}`, 10000);
  }

  async function showPhoto(index) {
    await click(thumbnail(fixture.firearm_a_photo_ids[index]));
    await waitFor(photoReady(fixture.widths[index]), 10000);
  }

  await waitFor("typeof api === 'function' && api() && typeof api().get_firearm === 'function'", 10000);
  await openFirearm(fixture.firearm_a_id, 5);

  // 1. Open the second photo from its thumbnail.
  await showPhoto(1);
  const secondOpen = await evaluate(`JSON.stringify({open:${lightboxOpen}, count:document.getElementById('lightboxCount').textContent, width:${image}.naturalWidth})`);
  check("photo 2 opens with the 2 of 5 label and its natural width",
    JSON.parse(secondOpen).open && JSON.parse(secondOpen).count === "2 of 5" && JSON.parse(secondOpen).width === fixture.widths[1], secondOpen);

  // 2. Step forward to the large photo in fit mode.
  await click("#lightboxNext");
  await waitFor(photoReady(fixture.widths[2]), 10000);
  const thirdPhoto = JSON.stringify({count: await count(), width: await width()});
  check("Next shows photo 3 with the 3 of 5 label", JSON.parse(thirdPhoto).count === "3 of 5" && JSON.parse(thirdPhoto).width === fixture.widths[2], thirdPhoto);
  await screenshot("photo-step-fit");
  check("the fit-mode multiple-photo screenshot was taken during item 2", true, "photo-step-fit");

  // 3. Step back twice to the first photo.
  await click("#lightboxPrevious");
  await waitFor(photoReady(fixture.widths[1]), 10000);
  await click("#lightboxPrevious");
  await waitFor(photoReady(fixture.widths[0]), 10000);
  const firstPhoto = JSON.stringify({count: await count(), width: await width()});
  check("Previous twice shows photo 1 with the 1 of 5 label", JSON.parse(firstPhoto).count === "1 of 5" && JSON.parse(firstPhoto).width === fixture.widths[0], firstPhoto);

  // 4. Wrap backward from the first photo.
  await click("#lightboxPrevious");
  await waitFor(photoReady(fixture.widths[4]), 10000);
  const wrappedPrevious = JSON.stringify({count: await count(), width: await width()});
  check("Previous from photo 1 wraps to photo 5", JSON.parse(wrappedPrevious).count === "5 of 5" && JSON.parse(wrappedPrevious).width === fixture.widths[4], wrappedPrevious);

  // 5. Wrap forward from the last photo.
  await click("#lightboxNext");
  await waitFor(photoReady(fixture.widths[0]), 10000);
  const wrappedNext = JSON.stringify({count: await count(), width: await width()});
  check("Next from photo 5 wraps to photo 1", JSON.parse(wrappedNext).count === "1 of 5" && JSON.parse(wrappedNext).width === fixture.widths[0], wrappedNext);

  // 6. The fourth stored row exists but its file is missing.
  await click("#lightboxNext");
  await waitFor(photoReady(fixture.widths[1]), 10000);
  await click("#lightboxNext");
  await waitFor(photoReady(fixture.widths[2]), 10000);
  await click("#lightboxNext");
  await waitFor(`${displayed('lightboxError')} && /missing/i.test(document.getElementById('lightboxError').textContent)`, 10000);
  const missingPhoto = await evaluate(`JSON.stringify({errorDisplayed:${displayed('lightboxError')}, error:document.getElementById('lightboxError').textContent, imageDisplayed:${displayed('lightboxImg')}, count:document.getElementById('lightboxCount').textContent})`);
  const missing = JSON.parse(missingPhoto);
  check("missing photo 4 shows a missing error, hides the image, and keeps the 4 of 5 label",
    missing.errorDisplayed && /missing/i.test(missing.error) && !missing.imageDisplayed && missing.count === "4 of 5", missingPhoto);

  // 7. Stepping continues after the missing photo.
  await click("#lightboxNext");
  await waitFor(photoReady(fixture.widths[4]), 10000);
  const afterMissing = await evaluate(`JSON.stringify({count:document.getElementById('lightboxCount').textContent, width:${image}.naturalWidth, errorDisplayed:${displayed('lightboxError')}})`);
  const recovered = JSON.parse(afterMissing);
  check("Next from missing photo 4 shows photo 5 and clears the error",
    recovered.count === "5 of 5" && recovered.width === fixture.widths[4] && !recovered.errorDisplayed, afterMissing);

  // 8. The small photo needs no actual-size control.
  await click("#lightboxNext");
  await waitFor(photoReady(fixture.widths[0]), 10000);
  const smallZoom = await evaluate(`JSON.stringify({displayed:${displayed('lightboxZoom')}, width:${image}.naturalWidth})`);
  check("the Actual size button is hidden for small photo 1", !JSON.parse(smallZoom).displayed, smallZoom);

  // 9. The large photo's fit layout stays within the window without overlap.
  await click("#lightboxNext");
  await waitFor(photoReady(fixture.widths[1]), 10000);
  await click("#lightboxNext");
  await waitFor(photoReady(fixture.widths[2]), 10000);
  await waitFor(displayed('lightboxZoom'), 10000);
  const fitLayout = await evaluate(`JSON.stringify((() => {
    const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right, bottom:r.bottom}; };
    const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    const inside = r => r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
    const photo = rect('#lightboxImg'); const controls = rect('.lightbox-controls'); const close = rect('.lightbox-close');
    return {zoomDisplayed:${displayed('lightboxZoom')}, photo, controls, close, innerWidth, innerHeight,
      overlaps:[overlaps(photo, controls), overlaps(photo, close), overlaps(controls, close)], inside:[inside(photo), inside(controls), inside(close)]};
  })())`);
  const layout = JSON.parse(fitLayout);
  check("large photo fit layout shows Actual size without overlap and stays inside the window",
    layout.zoomDisplayed && !layout.overlaps.some(Boolean) && layout.inside.every(Boolean), fitLayout);

  // 10. Actual size uses image pixels adjusted for the device pixel ratio.
  await click("#lightboxZoom");
  await waitFor("document.getElementById('lightboxScrim').classList.contains('zoomed')", 5000);
  const actualSize = await evaluate(`JSON.stringify({zoomed:document.getElementById('lightboxScrim').classList.contains('zoomed'), renderedWidth:${image}.getBoundingClientRect().width, naturalWidth:${image}.naturalWidth, devicePixelRatio:window.devicePixelRatio, buttonText:document.getElementById('lightboxZoom').textContent.trim()})`);
  const actual = JSON.parse(actualSize);
  check("Actual size zooms photo 3 to natural width divided by device pixel ratio and changes to Fit",
    actual.zoomed && Math.abs(actual.renderedWidth - actual.naturalWidth / actual.devicePixelRatio) <= 1 && actual.buttonText === "Fit", actualSize);

  // 11. Stepping exits actual size.
  await screenshot("photo-step-zoomed");
  await click("#lightboxPrevious");
  await waitFor(photoReady(fixture.widths[1]), 10000);
  const stepExitsZoom = await evaluate("document.getElementById('lightboxScrim').classList.contains('zoomed')");
  check("stepping to another photo exits actual-size zoom", !stepExitsZoom, String(stepExitsZoom));

  // 12. A photo click in fit mode does not close the lightbox.
  await click("#lightboxImg");
  await waitFor(lightboxOpen, 5000);
  const photoClickOpen = await evaluate(lightboxOpen);
  check("clicking the fit-mode photo leaves the lightbox open", photoClickOpen, String(photoClickOpen));

  // 13. A direct backdrop click closes the lightbox.
  await evaluate("document.getElementById('lightboxScrim').dispatchEvent(new MouseEvent('click', {bubbles:true}))");
  await waitFor(`!(${lightboxOpen})`, 5000);
  const backdropClosed = !(await evaluate(lightboxOpen));
  check("a click targeted at the scrim closes the lightbox", backdropClosed, String(backdropClosed));

  // 14. One photo hides all stepping controls and the count.
  await openFirearm(fixture.firearm_b_id, 1);
  await click(".photo-thumb img");
  await waitFor(photoReady(640), 10000);
  const onePhoto = await evaluate(`JSON.stringify({previous:${displayed('lightboxPrevious')}, next:${displayed('lightboxNext')}, count:${displayed('lightboxCount')}, width:${image}.naturalWidth})`);
  const solo = JSON.parse(onePhoto);
  check("one-photo lightbox hides Previous, Next, and the count", !solo.previous && !solo.next && !solo.count, onePhoto);
  await click(".lightbox-close");
  await waitFor(`!(${lightboxOpen})`, 5000);
}
