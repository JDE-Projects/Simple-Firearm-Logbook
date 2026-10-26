// ui_drive photo-drop scenario for Simple Firearm Logbook. Opens a fixture
// firearm, drops accepted JPEG and PNG files plus an ICO disguised as JPEG on
// the real add-photo zone, and verifies the gallery, bridge, and warning.
// Does not cover the native photo picker, image editing, or every supported
// image format.

export default async function photoDrop(helpers) {
  const { click, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const warning = "1 image was damaged and wasn't added.";
  const toastText = () => evaluate("document.getElementById('toast').style.display === 'block' ? document.getElementById('toast').textContent : ''");
  const dropFiles = files => evaluate(`(async () => {
    const dropZone = document.getElementById('photoDropZone');
    const transfer = new DataTransfer();
    for (const file of ${JSON.stringify(files)}) {
      const bytes = Uint8Array.from(atob(file.data), character => character.charCodeAt(0));
      transfer.items.add(new File([bytes], file.name, {type: file.type}));
    }
    dropZone.dispatchEvent(new DragEvent('drop', {
      bubbles: true,
      cancelable: true,
      dataTransfer: transfer,
    }));
    return true;
  })()`);
  const accepted = [
    {name: "front.jpg", type: "image/jpeg", data: fixture.front_jpeg},
    {name: "side.png", type: "image/png", data: fixture.side_png},
  ];
  const disguised = [{name: "disguised.jpg", type: "image/jpeg", data: fixture.disguised_ico}];

  await waitFor("typeof api === 'function' && api() && typeof api().get_firearm === 'function'", 10000);
  check("the fixture supplied all three image files",
    [fixture.front_jpeg, fixture.side_png, fixture.disguised_ico].every(data => typeof data === "string" && data.length > 0),
    JSON.stringify({front: !!fixture.front_jpeg, side: !!fixture.side_png, disguised: !!fixture.disguised_ico}));

  const rowSel = `tr[onclick="openDetail(${fixture.firearm_id})"]`;
  await waitFor(`document.querySelector('${rowSel}') !== null`, 10000);
  await click(rowSel);
  await waitFor("document.getElementById('detailWrap').style.display !== 'none'", 10000);
  check("the fixture firearm opens in the detail view",
    (await evaluate("document.getElementById('detailTitle').textContent")).includes("Glock"),
    await evaluate("document.getElementById('detailTitle').textContent"));

  await click("button[onclick='openAddPhotoModal()']");
  await waitFor("document.getElementById('addPhotoModalScrim').classList.contains('open')", 5000);

  await dropFiles([...accepted, ...disguised]);
  await waitFor("document.querySelectorAll('.photo-thumb').length === 2", 10000);
  check("the accepted drop shows exactly two photo thumbnails",
    await evaluate("document.querySelectorAll('.photo-thumb').length") === 2,
    await evaluate("document.querySelectorAll('.photo-thumb').length"));

  const firstBridge = await evaluate(`api().get_firearm(${fixture.firearm_id})`);
  check("the bridge stores exactly two photos after the first drop",
    !!firstBridge && firstBridge.ok === true && firstBridge.photos.length === 2,
    JSON.stringify(firstBridge));

  await waitFor("document.getElementById('toast').style.display === 'block'", 5000);
  const firstWarning = await toastText();
  check("the refused disguised file shows a plain damaged-image warning",
    firstWarning === warning && !/errno|exception|traceback/i.test(firstWarning), firstWarning);
  await screenshot("detail-after-first-drop", 0);

  // Clear the first warning so the second check can only pass on a new one.
  await evaluate("clearTimeout(toastTimer); document.getElementById('toast').style.display = 'none'; true");
  await click("button[onclick='openAddPhotoModal()']");
  await waitFor("document.getElementById('addPhotoModalScrim').classList.contains('open')", 5000);
  await dropFiles(disguised);
  await waitFor("document.getElementById('toast').style.display === 'block'", 10000);
  check("a second disguised-only drop adds no thumbnails",
    await evaluate("document.querySelectorAll('.photo-thumb').length") === 2,
    await evaluate("document.querySelectorAll('.photo-thumb').length"));

  const secondBridge = await evaluate(`api().get_firearm(${fixture.firearm_id})`);
  check("the bridge still stores exactly two photos after the second drop",
    !!secondBridge && secondBridge.ok === true && secondBridge.photos.length === 2,
    JSON.stringify(secondBridge));
  const secondWarning = await toastText();
  check("a second disguised-only drop repeats the plain damaged-image warning",
    secondWarning === warning && !/errno|exception|traceback/i.test(secondWarning), secondWarning);
}
