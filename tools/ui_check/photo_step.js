// ui_drive photo-step scenario for Simple Firearm Logbook. Opens fixture
// photos in the real lightbox and verifies stepping, wrapping, missing-photo
// handling, zoom, control visibility and placement, the viewport layout,
// clicks that close the view, the old photo staying on screen while the next
// one loads, and the arrow and Escape keys.
//
// Does not cover: visual styling beyond overlap and viewport bounds, keys
// pressed while a dialog is open over the photo view, held-down key repeat,
// drag scrolling at actual size, or every supported image format.

export default async function photoStep(helpers) {
  const { click, press, evaluate, waitFor, check, screenshot, fixture } = helpers;
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
    // Each thumbnail shows "Loading..." until its image arrives (or "Missing"
    // if its file is gone), so wait for every one to finish before anything
    // clicks one.
    await waitFor(`document.querySelectorAll('.photo-thumb').length === ${photoCount}
      && ![...document.querySelectorAll('.photo-thumb .ph-loading')].some(e => e.textContent === 'Loading...')`, 10000);
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
  const placement = `JSON.stringify((() => {
    const box = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width}; };
    return {photo:box('#lightboxImg'), stage:box('#lightboxStage'), controls:box('.lightbox-controls')};
  })())`;
  const smallPlacementText = await evaluate(placement);
  const smallPlacement = JSON.parse(smallPlacementText);
  const centered = p => Math.abs((p.photo.left + p.photo.right) - (p.stage.left + p.stage.right)) <= 2
    && Math.abs((p.photo.top + p.photo.bottom) - (p.stage.top + p.stage.bottom)) <= 2;
  check("small photo 1 shows at its own size, centered in the photo area",
    Math.abs(smallPlacement.photo.width - fixture.widths[0]) <= 1 && centered(smallPlacement), smallPlacementText);

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
  const largePlacementText = await evaluate(placement);
  const largePlacement = JSON.parse(largePlacementText);
  const sameSpot = ["left", "top", "right", "bottom"].every(side => Math.abs(largePlacement.controls[side] - smallPlacement.controls[side]) <= 1);
  check("the controls sit in the same place for small photo 1 and large photo 3, and photo 3 is centered",
    sameSpot && centered(largePlacement), JSON.stringify({small:smallPlacement.controls, large:largePlacement.controls, photo:largePlacement.photo, stage:largePlacement.stage}));

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

  // 13. A click on the empty space around a fit-size photo closes the view.
  await evaluate("document.getElementById('lightboxStage').dispatchEvent(new MouseEvent('click', {bubbles:true}))");
  await waitFor(`!(${lightboxOpen})`, 5000);
  const stageClosed = !(await evaluate(lightboxOpen));
  check("a click on the empty space around the photo closes the lightbox", stageClosed, String(stageClosed));
  await showPhoto(1);

  // 14. A direct backdrop click closes the lightbox.
  await evaluate("document.getElementById('lightboxScrim').dispatchEvent(new MouseEvent('click', {bubbles:true}))");
  await waitFor(`!(${lightboxOpen})`, 5000);
  const backdropClosed = !(await evaluate(lightboxOpen));
  check("a click targeted at the scrim closes the lightbox", backdropClosed, String(backdropClosed));

  // 15. Right and Left arrows step at fit size.
  await showPhoto(1);
  await press("ArrowRight");
  await waitFor(photoReady(fixture.widths[2]), 10000);
  const keyNext = JSON.stringify({count: await count(), width: await width()});
  check("Right arrow at fit size shows photo 3", JSON.parse(keyNext).count === "3 of 5" && JSON.parse(keyNext).width === fixture.widths[2], keyNext);
  await press("ArrowLeft");
  await waitFor(photoReady(fixture.widths[1]), 10000);
  const keyPrevious = JSON.stringify({count: await count(), width: await width()});
  check("Left arrow at fit size shows photo 2", JSON.parse(keyPrevious).count === "2 of 5" && JSON.parse(keyPrevious).width === fixture.widths[1], keyPrevious);

  // 16. The arrows wrap at both ends like the buttons.
  await press("ArrowLeft");
  await waitFor(photoReady(fixture.widths[0]), 10000);
  await press("ArrowLeft");
  await waitFor(photoReady(fixture.widths[4]), 10000);
  const keyWrapBack = JSON.stringify({count: await count(), width: await width()});
  check("Left arrow from photo 1 wraps to photo 5", JSON.parse(keyWrapBack).count === "5 of 5" && JSON.parse(keyWrapBack).width === fixture.widths[4], keyWrapBack);
  await press("ArrowRight");
  await waitFor(photoReady(fixture.widths[0]), 10000);
  const keyWrapForward = JSON.stringify({count: await count(), width: await width()});
  check("Right arrow from photo 5 wraps to photo 1", JSON.parse(keyWrapForward).count === "1 of 5" && JSON.parse(keyWrapForward).width === fixture.widths[0], keyWrapForward);

  // 17. While the next photo loads, the current photo and its label stay on
  // screen. A stand-in holds each photo request for 1.5 seconds.
  await evaluate(`(() => {
    const realGet = window.pywebview.api.get_photo_data;
    window.photoStepRealGet = realGet;
    window.pywebview.api.get_photo_data = (...args) =>
      new Promise(resolve => setTimeout(() => resolve(realGet(...args)), 1500));
    return true;
  })()`);
  await press("ArrowRight");
  const whileLoadingText = await evaluate(`JSON.stringify({displayed:${displayed('lightboxImg')}, width:${image}.naturalWidth, count:document.getElementById('lightboxCount').textContent})`);
  const whileLoading = JSON.parse(whileLoadingText);
  check("while photo 2 loads, photo 1 and its 1 of 5 label stay on screen",
    whileLoading.displayed && whileLoading.width === fixture.widths[0] && whileLoading.count === "1 of 5", whileLoadingText);
  await waitFor(photoReady(fixture.widths[1]), 10000);
  const swappedText = JSON.stringify({count: await count(), width: await width()});
  check("once loaded, photo 2 and its 2 of 5 label replace photo 1 together",
    JSON.parse(swappedText).count === "2 of 5" && JSON.parse(swappedText).width === fixture.widths[1], swappedText);
  await evaluate("(window.pywebview.api.get_photo_data = window.photoStepRealGet, true)");

  // 18. At actual size the arrows scroll the photo instead of stepping. The
  // view starts centered, so a nonzero scroll position shows the centering
  // has run and the photo has room to scroll on at least one axis.
  await press("ArrowRight");
  await waitFor(photoReady(fixture.widths[2]), 10000);
  await waitFor(displayed('lightboxZoom'), 10000);
  await click("#lightboxZoom");
  const stage = "document.getElementById('lightboxStage')";
  await waitFor(`document.getElementById('lightboxScrim').classList.contains('zoomed') && (${stage}.scrollLeft > 0 || ${stage}.scrollTop > 0)`, 5000);
  // The centering runs on the next animation frame. Let two frames pass so it
  // cannot land after the reset below and undo it.
  await evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))");
  await evaluate(`${stage}.scrollTo(0, 0)`);
  await waitFor(`${stage}.scrollLeft === 0 && ${stage}.scrollTop === 0`, 5000);
  const scrollState = () => evaluate(`JSON.stringify({left:${stage}.scrollLeft, top:${stage}.scrollTop,
    maxLeft:${stage}.scrollWidth - ${stage}.clientWidth, maxTop:${stage}.scrollHeight - ${stage}.clientHeight,
    zoomed:document.getElementById('lightboxScrim').classList.contains('zoomed'), count:document.getElementById('lightboxCount').textContent, width:${image}.naturalWidth})`);
  await press("ArrowRight");
  await press("ArrowDown");
  const scrolledText = await scrollState();
  const scrolled = JSON.parse(scrolledText);
  const near = (a, b) => Math.abs(a - b) <= 1;
  check("Right and Down arrows at actual size scroll the photo by up to 80 pixels without stepping",
    scrolled.zoomed && scrolled.count === "3 of 5" && scrolled.width === fixture.widths[2]
      && near(scrolled.left, Math.min(80, scrolled.maxLeft)) && near(scrolled.top, Math.min(80, scrolled.maxTop)), scrolledText);
  await press("ArrowLeft");
  await press("ArrowUp");
  const returnedText = await scrollState();
  const returned = JSON.parse(returnedText);
  check("Left and Up arrows at actual size scroll back without stepping",
    returned.zoomed && returned.count === "3 of 5" && returned.left === 0 && returned.top === 0, returnedText);

  // 19. Escape at actual size returns to fit; a second Escape closes.
  await press("Escape");
  await waitFor("!document.getElementById('lightboxScrim').classList.contains('zoomed')", 5000);
  const escapeZoomed = await evaluate(`JSON.stringify({open:${lightboxOpen}, zoomed:document.getElementById('lightboxScrim').classList.contains('zoomed'), count:document.getElementById('lightboxCount').textContent})`);
  check("Escape at actual size returns to fit and keeps photo 3 open",
    JSON.parse(escapeZoomed).open && !JSON.parse(escapeZoomed).zoomed && JSON.parse(escapeZoomed).count === "3 of 5", escapeZoomed);
  await press("Escape");
  await waitFor(`!(${lightboxOpen})`, 5000);
  const escapeClosed = !(await evaluate(lightboxOpen));
  check("a second Escape at fit size closes the photo view", escapeClosed, String(escapeClosed));

  // 20. One photo hides all stepping controls and the count.
  await openFirearm(fixture.firearm_b_id, 1);
  await click(".photo-thumb img");
  await waitFor(photoReady(640), 10000);
  const onePhoto = await evaluate(`JSON.stringify({previous:${displayed('lightboxPrevious')}, next:${displayed('lightboxNext')}, count:${displayed('lightboxCount')}, width:${image}.naturalWidth})`);
  const solo = JSON.parse(onePhoto);
  check("one-photo lightbox hides Previous, Next, and the count", !solo.previous && !solo.next && !solo.count, onePhoto);

  // 21. With one photo the arrows do not reload the photo.
  const requestBefore = await evaluate("lightboxRequest");
  await press("ArrowRight");
  await press("ArrowLeft");
  const soloKeys = await evaluate(`JSON.stringify({request:lightboxRequest, open:${lightboxOpen}, width:${image}.naturalWidth})`);
  const soloAfter = JSON.parse(soloKeys);
  check("arrows with one photo leave the view open and request no photo",
    soloAfter.request === requestBefore && soloAfter.open && soloAfter.width === 640, JSON.stringify({requestBefore, ...soloAfter}));

  // 22. Escape closes the view at fit size.
  await press("Escape");
  await waitFor(`!(${lightboxOpen})`, 5000);
  const escapeFit = !(await evaluate(lightboxOpen));
  check("Escape at fit size closes the photo view", escapeFit, String(escapeFit));
}
